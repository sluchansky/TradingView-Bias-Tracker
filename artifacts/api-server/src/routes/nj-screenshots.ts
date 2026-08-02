/**
 * NJ Screenshot routes — Express-native (GCS + DB).
 * Handles upload, serve, and delete of native-journal trade screenshots.
 *
 * Routes (relative to /api Express mount):
 *   POST   /journal/native-trades/:trade_id/screenshots/upload
 *     — validates image, uploads to GCS with server-generated key,
 *       then registers metadata in Flask's JSONB column.
 *   GET    /journal/native-screenshot/:attachment_id
 *     — looks up storage_key in DB (no Flask round-trip), serves from GCS.
 *   DELETE /journal/native-trades/:trade_id/screenshots/:attachment_id
 *     — looks up key in DB, calls Flask to remove JSONB entry, then GCS delete.
 *
 * Clients never supply a storage_key; the server always generates one.
 */

import { Router } from "express";
import { randomUUID } from "crypto";
import type { Bucket } from "@google-cloud/storage";

// ── Constants (exported for tests) ─────────────────────────────────────────
export const NJ_MAX_SIZE_BYTES       = 5 * 1024 * 1024;   // 5 MB
export const NJ_MAX_PER_TRADE        = 10;
export const NJ_SCREENSHOT_KEY_PREFIX = "nj/attachments";
export const NJ_VALID_MIME_TYPES = new Set([
  "image/jpeg", "image/png", "image/gif", "image/webp",
]);
export const NJ_EXT_MAP: Record<string, string> = {
  "image/jpeg": "jpg", "image/png": "png",
  "image/gif": "gif", "image/webp": "webp",
};
export const NJ_VALID_CATEGORIES = new Set([
  "PRE_ENTRY", "ENTRY", "MANAGEMENT", "EXIT", "REVIEW", "OTHER",
]);

// ── Dependency types ────────────────────────────────────────────────────────
interface DbRow { [col: string]: unknown }
interface DbPoolClient {
  query(text: string, values?: unknown[]): Promise<{ rows: DbRow[] }>;
  release(): void;
}
interface DbPool {
  connect(): Promise<DbPoolClient>;
}

export interface NJScreenshotsRouterDeps {
  pool: DbPool;
  getBucket: () => Bucket;
  /** Base URL for internal Flask calls, e.g. "http://127.0.0.1:8000" */
  flaskBase: string;
}

// ── Router factory ──────────────────────────────────────────────────────────
export function createNJScreenshotsRouter({
  pool, getBucket, flaskBase,
}: NJScreenshotsRouterDeps): Router {
  const router = Router();

  // ── POST /journal/native-trades/:trade_id/screenshots/upload ─────────────
  router.post("/journal/native-trades/:trade_id/screenshots/upload", async (req, res) => {
    const { trade_id } = req.params;
    if (!trade_id || !/^[0-9a-f-]{36}$/i.test(trade_id)) {
      res.status(400).json({ ok: false, error: "invalid trade_id" }); return;
    }

    // Validate category
    const category = String(req.query.category || "OTHER").toUpperCase();
    if (!NJ_VALID_CATEGORIES.has(category)) {
      res.status(400).json({ ok: false, error: `invalid category '${category}'` }); return;
    }
    const caption = String(req.query.caption || "").slice(0, 200);

    // Validate image payload
    const body: Buffer = Buffer.isBuffer(req.body) ? req.body : Buffer.alloc(0);
    if (body.length === 0) {
      res.status(400).json({ ok: false, error: "empty body — send raw image bytes" }); return;
    }
    if (body.length > NJ_MAX_SIZE_BYTES) {
      res.status(413).json({ ok: false, error: "image too large — max 5 MB" }); return;
    }

    const ctRaw = (Array.isArray(req.headers["content-type"])
      ? req.headers["content-type"][0] : (req.headers["content-type"] || "")) as string;
    const mimeType = ctRaw.split(";")[0].trim().toLowerCase();
    if (!NJ_VALID_MIME_TYPES.has(mimeType)) {
      res.status(400).json({ ok: false, error: `unsupported type '${mimeType}'` }); return;
    }

    // Pre-flight: verify trade exists + count existing screenshots
    const preClient = await pool.connect();
    try {
      const r = await preClient.query(
        `SELECT internal_trade_id,
                jsonb_array_length(COALESCE(screenshots, '[]'::jsonb)) AS sc_count
         FROM native_journal WHERE id = $1::uuid`,
        [trade_id]
      );
      if (r.rows.length === 0) {
        res.status(404).json({ ok: false, error: "trade not found" }); return;
      }
      if (Number(r.rows[0].sc_count) >= NJ_MAX_PER_TRADE) {
        res.status(400).json({ ok: false, error: "max_attachments_reached" }); return;
      }
    } finally {
      preClient.release();
    }

    // Server-generated GCS key — client never supplies this
    const ext = NJ_EXT_MAP[mimeType] ?? "jpg";
    const attachmentId = randomUUID();
    const storageKey   = `${NJ_SCREENSHOT_KEY_PREFIX}/${attachmentId}.${ext}`;

    // Upload to GCS
    const file = getBucket().file(storageKey);
    try {
      await file.save(body, { contentType: mimeType, resumable: false });
    } catch (err: unknown) {
      console.error("[nj-screenshot] GCS upload:", (err as Error)?.message);
      res.status(500).json({ ok: false, error: "upload failed" }); return;
    }

    // Register metadata in Flask (server-to-server — key is server-generated)
    try {
      const flaskResp = await fetch(
        `${flaskBase}/journal/native-trades/${trade_id}/screenshots`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            attachment_id: attachmentId,
            category,
            caption: caption || null,
            storage_key: storageKey,
            mime_type: mimeType,
            file_size: body.length,
          }),
        }
      );
      const fd = (await flaskResp.json()) as { ok: boolean; error?: string };
      if (!fd.ok) {
        try { await file.delete({ ignoreNotFound: true }); } catch { /* best-effort */ }
        res.status(500).json({ ok: false, error: fd.error || "metadata failed" }); return;
      }
    } catch (err: unknown) {
      console.error("[nj-screenshot] Flask metadata:", (err as Error)?.message);
      try { await file.delete({ ignoreNotFound: true }); } catch { /* best-effort */ }
      res.status(500).json({ ok: false, error: "metadata failed" }); return;
    }

    res.json({
      ok: true,
      attachment_id: attachmentId,
      storage_key:   storageKey,
      mime_type:     mimeType,
      file_size:     body.length,
      category,
      caption:       caption || null,
      uploaded_at:   new Date().toISOString(),
      serve_url:     `/api/journal/native-screenshot/${attachmentId}`,
    });
  });

  // ── GET /journal/native-screenshot/:attachment_id ─────────────────────────
  router.get("/journal/native-screenshot/:attachment_id", async (req, res) => {
    const { attachment_id } = req.params;
    if (!attachment_id || !/^[0-9a-f-]{36}$/i.test(attachment_id)) {
      res.status(400).json({ ok: false, error: "invalid attachment_id" }); return;
    }

    // Resolve storage_key directly from DB — no Flask round-trip
    const client = await pool.connect();
    let storageKey: string;
    let mimeType: string;
    try {
      const r = await client.query(
        `SELECT s->>'storage_key' AS storage_key,
                s->>'mime_type'   AS mime_type
         FROM native_journal,
              jsonb_array_elements(COALESCE(screenshots, '[]'::jsonb)) AS s
         WHERE s->>'attachment_id' = $1
         LIMIT 1`,
        [attachment_id]
      );
      if (r.rows.length === 0) {
        res.status(404).json({ ok: false, error: "screenshot not found" }); return;
      }
      storageKey = r.rows[0].storage_key as string;
      mimeType   = (r.rows[0].mime_type as string) || "image/jpeg";
    } catch (err: unknown) {
      console.error("[nj-screenshot] key lookup:", (err as Error)?.message);
      res.status(500).json({ ok: false, error: "lookup failed" }); return;
    } finally {
      client.release();
    }

    // Stream from GCS
    try {
      const file = getBucket().file(storageKey);
      const [exists] = await file.exists();
      if (!exists) {
        res.status(404).json({ ok: false, error: "file not found in storage" }); return;
      }
      res.setHeader("Content-Type", mimeType);
      res.setHeader("Cache-Control", "private, max-age=3600");
      file.createReadStream()
        .on("error", (_err: Error) => {
          if (!res.headersSent) res.status(500).json({ ok: false, error: "stream error" });
        })
        .pipe(res);
    } catch (err: unknown) {
      console.error("[nj-screenshot] serve:", (err as Error)?.message);
      if (!res.headersSent) res.status(500).json({ ok: false, error: "serve failed" });
    }
  });

  // ── DELETE /journal/native-trades/:trade_id/screenshots/:attachment_id ────
  router.delete(
    "/journal/native-trades/:trade_id/screenshots/:attachment_id",
    async (req, res) => {
      const { trade_id, attachment_id } = req.params;
      if (!trade_id || !attachment_id) {
        res.status(400).json({ ok: false, error: "missing params" }); return;
      }

      // Get storage_key before removing metadata
      const client = await pool.connect();
      let storageKey: string | null = null;
      try {
        const r = await client.query(
          `SELECT s->>'storage_key' AS storage_key
           FROM native_journal,
                jsonb_array_elements(COALESCE(screenshots, '[]'::jsonb)) AS s
           WHERE id = $1::uuid AND s->>'attachment_id' = $2
           LIMIT 1`,
          [trade_id, attachment_id]
        );
        if (r.rows.length > 0) {
          storageKey = r.rows[0].storage_key as string | null;
        }
      } catch (err: unknown) {
        console.error("[nj-screenshot] delete key lookup:", (err as Error)?.message);
      } finally {
        client.release();
      }

      // Call Flask to remove JSONB entry
      try {
        const flaskResp = await fetch(
          `${flaskBase}/journal/native-trades/${trade_id}/screenshots/${attachment_id}`,
          { method: "DELETE" }
        );
        if (!flaskResp.ok) {
          const fd = (await flaskResp.json()) as { error?: string };
          res.status(flaskResp.status).json({ ok: false, error: fd.error || "delete failed" });
          return;
        }
      } catch (err: unknown) {
        console.error("[nj-screenshot] Flask delete:", (err as Error)?.message);
        res.status(500).json({ ok: false, error: "metadata delete failed" }); return;
      }

      // Best-effort GCS delete after metadata is removed
      if (storageKey) {
        try {
          await getBucket().file(storageKey).delete({ ignoreNotFound: true });
        } catch (err: unknown) {
          console.warn("[nj-screenshot] GCS delete warn:", (err as Error)?.message);
        }
      }

      res.json({ ok: true });
    }
  );

  return router;
}

// ── Default export ────────────────────────────────────────────────────────────
import { pool } from "@workspace/db";
import { objectStorageClient } from "../lib/objectStorage.js";

function getRealBucket(): import("@google-cloud/storage").Bucket {
  const id = process.env.DEFAULT_OBJECT_STORAGE_BUCKET_ID;
  if (!id) throw new Error("DEFAULT_OBJECT_STORAGE_BUCKET_ID not set");
  return objectStorageClient.bucket(id);
}

export default createNJScreenshotsRouter({
  pool,
  getBucket: getRealBucket,
  flaskBase: `http://127.0.0.1:${process.env.FLASK_PORT ?? "8000"}`,
});
