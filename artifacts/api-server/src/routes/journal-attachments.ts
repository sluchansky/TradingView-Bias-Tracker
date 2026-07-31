/**
 * Journal Attachment routes — owner-auth-gated via dashboardAuth applied upstream.
 *
 * Storage: Google Cloud Storage (App Storage).  Metadata: PostgreSQL journal_attachments table.
 *
 * Routes (all relative to the /api Express mount):
 *   POST   /journal/trade/:source/:id/attachment  — validate + upload to GCS + save metadata
 *   GET    /journal/trade/:source/:id/attachments — list metadata (DB only)
 *   GET    /journal/attachment/:id               — serve image from GCS (auth-gated)
 *   DELETE /journal/attachment/:id               — delete from GCS + DB
 *
 * The router is created via a factory function to allow dependency injection in tests.
 */

import { Router } from "express";
import { randomUUID } from "crypto";
import type { Bucket } from "@google-cloud/storage";

// Minimal structural interfaces — no direct 'pg' dependency needed.
// The real pool from @workspace/db satisfies these at runtime.
interface DbRow { [col: string]: unknown }
interface DbPoolClient {
  query(text: string, values?: unknown[]): Promise<{ rows: DbRow[] }>;
  release(): void;
}
interface DbPool {
  connect(): Promise<DbPoolClient>;
}

// ── Constants (exported for tests) ────────────────────────────────────────────
export const VALID_STAGES  = new Set(["before_entry","at_entry","during","after_exit","review_markup"]);
export const VALID_SOURCES = new Set(["system","tradzella"]);
export const MAX_SIZE_BYTES = 5 * 1024 * 1024;   // 5 MB hard limit
export const MAX_PER_TRADE  = 10;
export const VALID_MIME_TYPES = new Set(["image/jpeg","image/png","image/gif","image/webp"]);
export const EXT_MAP: Record<string, string> = {
  "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp",
};

// ── Helpers ───────────────────────────────────────────────────────────────────
export function safeFilename(name: string): string {
  return name.replace(/[^a-zA-Z0-9._-]/g, "_").replace(/\.{2,}/g, "_").slice(0, 200);
}

export function getPrivateDir(): string {
  return (process.env.PRIVATE_OBJECT_DIR || "objects/uploads").replace(/\/$/, "");
}

// ── Factory (accepts injectable deps for testing) ─────────────────────────────
export interface AttachmentRouterDeps {
  pool: DbPool;
  getBucket: () => Bucket;
}

export function createJournalAttachmentsRouter({ pool, getBucket }: AttachmentRouterDeps): Router {
  const router = Router();

  // ── POST /journal/trade/:source/:id/attachment ──────────────────────────────
  router.post("/journal/trade/:source/:id/attachment", async (req, res) => {
    const { source, id } = req.params;
    const tradeId = parseInt(id, 10);

    if (!VALID_SOURCES.has(source)) {
      res.status(400).json({ ok: false, error: `unknown source '${source}'` }); return;
    }
    if (isNaN(tradeId) || tradeId <= 0) {
      res.status(400).json({ ok: false, error: "invalid trade_id" }); return;
    }

    const stage = String(req.query.stage || "review_markup").toLowerCase();
    if (!VALID_STAGES.has(stage)) {
      res.status(400).json({ ok: false, error: `invalid stage '${stage}' — use before_entry|at_entry|during|after_exit|review_markup` }); return;
    }

    const body: Buffer = Buffer.isBuffer(req.body) ? req.body : Buffer.alloc(0);
    if (body.length === 0) {
      res.status(400).json({ ok: false, error: "empty body — send raw image bytes" }); return;
    }
    if (body.length > MAX_SIZE_BYTES) {
      res.status(413).json({ ok: false,
        error: `image too large (${(body.length / 1048576).toFixed(1)} MB) — max 5 MB` }); return;
    }

    // Strict MIME check — must be declared AND be a supported image type
    const ctRaw = (Array.isArray(req.headers["content-type"])
      ? req.headers["content-type"][0] : (req.headers["content-type"] || "")) as string;
    const mimeType = ctRaw.split(";")[0].trim().toLowerCase();
    if (!VALID_MIME_TYPES.has(mimeType)) {
      res.status(400).json({ ok: false,
        error: `invalid file type '${mimeType}' — only image/jpeg, image/png, image/gif, image/webp accepted` }); return;
    }

    const ext = EXT_MAP[mimeType] ?? "jpg";
    const filename = safeFilename(String(req.query.filename || `screenshot.${ext}`));

    // ── Pre-flight DB checks (before GCS upload) ───────────────────────────────
    // 1. Verify the trade actually exists; prevents orphaned objects for bad IDs.
    // 2. Pre-flight count check (soft cap; final enforcement is atomic in INSERT).
    // Both checks share one client, released before the GCS call.
    const tradeTable = source === "tradzella" ? "tradezella_trades" : "strategy_trades";
    {
      const preClient: DbPoolClient = await pool.connect();
      try {
        const existsRes = await preClient.query(
          `SELECT id FROM ${tradeTable} WHERE id=$1 LIMIT 1`,
          [tradeId]
        );
        if (existsRes.rows.length === 0) {
          res.status(404).json({ ok: false, error: `trade ${tradeId} not found in ${source}` }); return;
        }
        const countRes = await preClient.query(
          "SELECT COUNT(*) FROM journal_attachments WHERE source=$1 AND trade_id=$2",
          [source, tradeId]
        );
        if (parseInt(countRes.rows[0].count as string, 10) >= MAX_PER_TRADE) {
          res.status(400).json({ ok: false, error: `max ${MAX_PER_TRADE} attachments per trade` }); return;
        }
      } finally {
        preClient.release();
      }
    }

    // ── GCS upload ─────────────────────────────────────────────────────────────
    const objectKey = `${getPrivateDir()}/journal/${randomUUID()}.${ext}`;
    const file = getBucket().file(objectKey);
    try {
      await file.save(body, { contentType: mimeType, resumable: false });
    } catch (uploadErr: any) {
      console.error("[journal-attachment] GCS upload error:", uploadErr?.message);
      res.status(500).json({ ok: false, error: "upload failed" }); return;
    }

    // ── Serialized INSERT via transaction-scoped advisory lock ─────────────────
    // pg_advisory_xact_lock blocks all other transactions holding the same key
    // until this transaction commits or rolls back.  Key = hashtext(source) XOR
    // trade_id ensures a unique bigint without DDL.  Two concurrent uploads for
    // the same trade will queue here; the second will see the fresh count and
    // be rejected correctly.
    const insertClient: DbPoolClient = await pool.connect();
    try {
      await insertClient.query("BEGIN");

      // Acquire exclusive advisory lock scoped to this transaction.
      // hashtext(text) → int4; we cast to bigint for the single-arg overload.
      await insertClient.query(
        "SELECT pg_advisory_xact_lock(hashtext($1)::bigint)",
        [`${source}:${tradeId}`]
      );

      // Fresh count under lock — safe from concurrent modification.
      const lockedCount = await insertClient.query(
        "SELECT COUNT(*) FROM journal_attachments WHERE source=$1 AND trade_id=$2",
        [source, tradeId]
      );
      if (parseInt(lockedCount.rows[0].count as string, 10) >= MAX_PER_TRADE) {
        await insertClient.query("ROLLBACK");
        // Clean up the GCS object we already uploaded before the lock
        try { await file.delete({ ignoreNotFound: true }); } catch { /* best-effort */ }
        res.status(400).json({ ok: false, error: `max ${MAX_PER_TRADE} attachments per trade` }); return;
      }

      const insertRes = await insertClient.query(
        `INSERT INTO journal_attachments
           (source, trade_id, stage, filename, storage_key, mime_type, size_bytes)
         VALUES ($1,$2,$3,$4,$5,$6,$7)
         RETURNING id, created_at`,
        [source, tradeId, stage, filename, objectKey, mimeType, body.length]
      );
      await insertClient.query("COMMIT");

      const row = insertRes.rows[0];
      res.json({
        ok: true,
        attachment: {
          id: row.id, source, trade_id: tradeId, stage, filename,
          storage_key: objectKey, mime_type: mimeType, size_bytes: body.length,
          created_at: (row.created_at as Date | null)?.toISOString?.() ?? row.created_at,
          serve_url: `/api/journal/attachment/${row.id}`,
        },
      });
    } catch (err: any) {
      console.error("[journal-attachment] insert error:", err?.message);
      await insertClient.query("ROLLBACK").catch(() => { /* ignore on already-rolled-back */ });
      try { await file.delete({ ignoreNotFound: true }); } catch { /* best-effort cleanup */ }
      res.status(500).json({ ok: false, error: "upload failed" });
    } finally {
      insertClient.release();
    }
  });

  // ── GET /journal/trade/:source/:id/attachments ──────────────────────────────
  router.get("/journal/trade/:source/:id/attachments", async (req, res) => {
    const { source, id } = req.params;
    const tradeId = parseInt(id, 10);
    if (!VALID_SOURCES.has(source) || isNaN(tradeId) || tradeId <= 0) {
      res.status(400).json({ ok: false, error: "invalid source or trade id" }); return;
    }
    const client: DbPoolClient = await pool.connect();
    try {
      const result = await client.query(
        `SELECT id, source, trade_id, stage, filename, mime_type, size_bytes, created_at
         FROM journal_attachments
         WHERE source=$1 AND trade_id=$2
         ORDER BY created_at ASC`,
        [source, tradeId]
      );
      const attachments = result.rows.map((r: DbRow) => ({
        ...r,
        created_at: (r.created_at as Date | null)?.toISOString?.() ?? r.created_at,
        serve_url: `/api/journal/attachment/${r.id}`,
      }));
      res.json({ ok: true, attachments });
    } catch (err: any) {
      console.error("[journal-attachment] list error:", err?.message);
      res.status(500).json({ ok: false, error: "query failed" });
    } finally {
      client.release();
    }
  });

  // ── GET /journal/attachment/:id ─────────────────────────────────────────────
  // Streams the image from GCS. Auth-gated upstream by dashboardAuth.
  router.get("/journal/attachment/:id", async (req, res) => {
    const attachId = parseInt(req.params.id, 10);
    if (isNaN(attachId) || attachId <= 0) {
      res.status(400).json({ ok: false, error: "invalid id" }); return;
    }

    // Fetch metadata — release pool client before opening the GCS stream
    let storageKey: string, mimeType: string, filename: string;
    {
      const client: DbPoolClient = await pool.connect();
      try {
        const result = await client.query(
          "SELECT storage_key, mime_type, filename FROM journal_attachments WHERE id=$1",
          [attachId]
        );
        if (result.rows.length === 0) {
          res.status(404).json({ ok: false, error: "attachment not found" }); return;
        }
        const r0 = result.rows[0];
        storageKey = r0.storage_key as string;
        mimeType   = r0.mime_type as string;
        filename   = r0.filename as string;
      } finally {
        client.release();
      }
    }

    try {
      const file = getBucket().file(storageKey);
      const [exists] = await file.exists();
      if (!exists) { res.status(404).json({ ok: false, error: "file not found in storage" }); return; }

      res.setHeader("Content-Type", mimeType);
      res.setHeader("Content-Disposition", `inline; filename="${filename}"`);
      res.setHeader("Cache-Control", "private, max-age=3600");
      file.createReadStream()
        .on("error", (_err: Error) => {
          if (!res.headersSent) res.status(500).json({ ok: false, error: "stream error" });
        })
        .pipe(res);
    } catch (err: any) {
      console.error("[journal-attachment] serve error:", err?.message);
      if (!res.headersSent) res.status(500).json({ ok: false, error: "serve failed" });
    }
  });

  // ── DELETE /journal/attachment/:id ──────────────────────────────────────────
  // Deletes from GCS first, then DB. Idempotent on GCS (ignoreNotFound).
  router.delete("/journal/attachment/:id", async (req, res) => {
    const attachId = parseInt(req.params.id, 10);
    if (isNaN(attachId) || attachId <= 0) {
      res.status(400).json({ ok: false, error: "invalid id" }); return;
    }
    const client: DbPoolClient = await pool.connect();
    try {
      const result = await client.query(
        "SELECT id, storage_key FROM journal_attachments WHERE id=$1",
        [attachId]
      );
      if (result.rows.length === 0) {
        res.status(404).json({ ok: false, error: "attachment not found" }); return;
      }
      const storage_key = result.rows[0].storage_key as string;

      // GCS delete — fail-soft: log warning but proceed to remove DB row
      try {
        await getBucket().file(storage_key).delete({ ignoreNotFound: true });
      } catch (gcsErr: any) {
        console.warn("[journal-attachment] GCS delete warning:", gcsErr?.message);
      }

      await client.query("DELETE FROM journal_attachments WHERE id=$1", [attachId]);
      res.json({ ok: true, id: attachId });
    } catch (err: any) {
      console.error("[journal-attachment] delete error:", err?.message);
      res.status(500).json({ ok: false, error: "delete failed" });
    } finally {
      client.release();
    }
  });

  return router;
}

// ── Default export: router with real dependencies ─────────────────────────────
// Imported lazily to allow tests to mock before the module resolves.
import { pool } from "@workspace/db";
import { objectStorageClient } from "../lib/objectStorage.js";

function getRealBucket(): import("@google-cloud/storage").Bucket {
  const id = process.env.DEFAULT_OBJECT_STORAGE_BUCKET_ID;
  if (!id) throw new Error("DEFAULT_OBJECT_STORAGE_BUCKET_ID not set");
  return objectStorageClient.bucket(id);
}

export default createJournalAttachmentsRouter({ pool, getBucket: getRealBucket });
