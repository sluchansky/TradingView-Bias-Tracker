/**
 * Route-level tests for journal-attachments.ts
 *
 * Uses supertest to make real HTTP requests against an in-process Express app
 * with mocked pool and GCS bucket.  No real database or GCS calls are made.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import express from "express";
import request from "supertest";
import { createJournalAttachmentsRouter, MAX_PER_TRADE, MAX_SIZE_BYTES } from "../journal-attachments.js";
import type { AttachmentRouterDeps } from "../journal-attachments.js";
import type { Bucket, File } from "@google-cloud/storage";

// Local mock types — mirrors the structural interfaces in journal-attachments.ts
type MockPool   = AttachmentRouterDeps["pool"];
type MockBucket = AttachmentRouterDeps["getBucket"] extends () => infer B ? B : never;

// ── Mock factories ─────────────────────────────────────────────────────────────

interface MockPoolClient {
  query:   ReturnType<typeof vi.fn>;
  release: ReturnType<typeof vi.fn>;
}

function makePoolClient(overrides: Partial<MockPoolClient> = {}): MockPoolClient {
  return {
    query:   vi.fn(),
    release: vi.fn(),
    ...overrides,
  };
}

function makePool(client: ReturnType<typeof makePoolClient>): MockPool {
  return {
    connect: vi.fn().mockResolvedValue(client),
  } as unknown as MockPool;
}

function makeFile(existsResult = true): File {
  const readable = {
    on: vi.fn().mockReturnThis(),
    pipe: vi.fn().mockReturnThis(),
  };
  return {
    save:             vi.fn().mockResolvedValue(undefined),
    exists:           vi.fn().mockResolvedValue([existsResult]),
    createReadStream: vi.fn().mockReturnValue(readable),
    delete:           vi.fn().mockResolvedValue(undefined),
  } as unknown as File;
}

function makeBucket(file = makeFile()): MockBucket {
  return { file: vi.fn().mockReturnValue(file) } as unknown as MockBucket;
}

function makeApp(pool: MockPool, bucket: MockBucket) {
  const app = express();
  // Parse raw bytes for upload
  app.use((req, _res, next) => {
    if (req.method === "POST") {
      const chunks: Buffer[] = [];
      req.on("data", (chunk: Buffer) => chunks.push(chunk));
      req.on("end", () => { req.body = Buffer.concat(chunks); next(); });
    } else {
      next();
    }
  });
  app.use(createJournalAttachmentsRouter({ pool, getBucket: () => bucket }));
  return app;
}

// Small valid PNG bytes (1×1 pixel, RFC 2083)
const PNG_BYTES = Buffer.from(
  "89504e470d0a1a0a0000000d49484452000000010000000108020000009001" +
  "2e0000000c4944415408d7636060600000000200018e85c86e0000000049454e44ae426082",
  "hex"
);

// ── Tests ──────────────────────────────────────────────────────────────────────

// ── Upload flow helpers ────────────────────────────────────────────────────────
// The POST route now calls pool.connect() TWICE:
//   1. preClient — existence check (SELECT id FROM …) + count check
//   2. insertClient — atomic INSERT … SELECT … WHERE count < MAX
// Both clients share the same mock in makePool; the mock's connect() resolves
// to whichever client is injected, in call order.

function makeTwoClientPool(preClient: MockPoolClient, insertClient: MockPoolClient): MockPool {
  return {
    connect: vi.fn()
      .mockResolvedValueOnce(preClient)
      .mockResolvedValueOnce(insertClient),
  } as unknown as MockPool;
}

// Pre-flight client that finds the trade and reports a given count
function makePreClient(count: number | "throw"): MockPoolClient {
  if (count === "throw") {
    return makePoolClient({ query: vi.fn().mockRejectedValue(new Error("db error")), release: vi.fn() });
  }
  return makePoolClient({
    query: vi.fn()
      .mockResolvedValueOnce({ rows: [{ id: 1 }] })      // trade existence → found
      .mockResolvedValueOnce({ rows: [{ count: String(count) }] }),  // count
    release: vi.fn(),
  });
}

// Insert client that runs the advisory-lock transaction and returns a successful row.
// Transaction sequence: BEGIN → pg_advisory_xact_lock → COUNT → INSERT → COMMIT
function makeInsertClient(
  freshCount: number,
  id = 99,
  createdAt = new Date("2026-07-31T12:00:00Z"),
): MockPoolClient {
  return makePoolClient({
    query: vi.fn()
      .mockResolvedValueOnce({ rows: [] })                          // BEGIN
      .mockResolvedValueOnce({ rows: [] })                          // advisory lock
      .mockResolvedValueOnce({ rows: [{ count: String(freshCount) }] }) // COUNT under lock
      .mockResolvedValueOnce({ rows: [{ id, created_at: createdAt }] }) // INSERT
      .mockResolvedValueOnce({ rows: [] }),                         // COMMIT
    release: vi.fn(),
  });
}

// Insert client that sees count >= MAX under lock → ROLLBACK → 400
function makeInsertClientCapHit(): MockPoolClient {
  return makePoolClient({
    query: vi.fn()
      .mockResolvedValueOnce({ rows: [] })                                 // BEGIN
      .mockResolvedValueOnce({ rows: [] })                                 // advisory lock
      .mockResolvedValueOnce({ rows: [{ count: String(MAX_PER_TRADE) }] }) // COUNT → cap
      .mockResolvedValueOnce({ rows: [] }),                                 // ROLLBACK
    release: vi.fn(),
  });
}

describe("POST /journal/trade/:source/:id/attachment", () => {
  let file: File;
  let bucket: MockBucket;

  beforeEach(() => {
    file   = makeFile();
    bucket = makeBucket(file);
  });

  it("rejects unknown source with 400 — no DB calls", async () => {
    // Input validation fires before pool.connect(); no client needed
    const pool = { connect: vi.fn() } as unknown as MockPool;
    const app  = makeApp(pool, bucket);
    const res  = await request(app)
      .post("/journal/trade/unknown/1/attachment?stage=review_markup")
      .set("Content-Type", "image/png").send(PNG_BYTES);
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/unknown source/);
    expect(pool.connect).not.toHaveBeenCalled();
  });

  it("rejects invalid stage with 400 — no DB calls", async () => {
    const pool = { connect: vi.fn() } as unknown as MockPool;
    const app  = makeApp(pool, bucket);
    const res  = await request(app)
      .post("/journal/trade/system/1/attachment?stage=bad_stage")
      .set("Content-Type", "image/png").send(PNG_BYTES);
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/invalid stage/);
    expect(pool.connect).not.toHaveBeenCalled();
  });

  it("rejects non-image MIME with 400 — no DB calls", async () => {
    const pool = { connect: vi.fn() } as unknown as MockPool;
    const app  = makeApp(pool, bucket);
    const res  = await request(app)
      .post("/journal/trade/system/1/attachment?stage=review_markup")
      .set("Content-Type", "application/pdf")
      .send(Buffer.from("PDF content"));
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/invalid file type/);
    expect(pool.connect).not.toHaveBeenCalled();
  });

  it("rejects body exceeding 5 MB with 413 — no DB calls", async () => {
    const pool = { connect: vi.fn() } as unknown as MockPool;
    const app  = makeApp(pool, bucket);
    const res  = await request(app)
      .post("/journal/trade/system/1/attachment?stage=review_markup")
      .set("Content-Type", "image/png")
      .send(Buffer.alloc(MAX_SIZE_BYTES + 1, 0xff));
    expect(res.status).toBe(413);
    expect(pool.connect).not.toHaveBeenCalled();
  });

  // Placeholder insert client for cases where it's never actually called
  const unusedInsertClient = () => makePoolClient({ query: vi.fn(), release: vi.fn() });

  it("returns 404 for nonexistent system trade — no GCS upload", async () => {
    const preClient = makePoolClient({
      query: vi.fn().mockResolvedValueOnce({ rows: [] }), // trade not found
      release: vi.fn(),
    });
    const pool = makeTwoClientPool(preClient, unusedInsertClient());
    const app  = makeApp(pool, bucket);
    const res  = await request(app)
      .post("/journal/trade/system/999/attachment?stage=review_markup")
      .set("Content-Type", "image/png").send(PNG_BYTES);
    expect(res.status).toBe(404);
    expect(res.body.error).toMatch(/not found in system/);
    expect(file.save).not.toHaveBeenCalled();
  });

  it("returns 404 for nonexistent tradzella trade — no GCS upload", async () => {
    const preClient = makePoolClient({
      query: vi.fn().mockResolvedValueOnce({ rows: [] }), // trade not found
      release: vi.fn(),
    });
    const pool = makeTwoClientPool(preClient, unusedInsertClient());
    const app  = makeApp(pool, bucket);
    const res  = await request(app)
      .post("/journal/trade/tradzella/777/attachment?stage=before_entry")
      .set("Content-Type", "image/jpeg").send(PNG_BYTES);
    expect(res.status).toBe(404);
    expect(res.body.error).toMatch(/not found in tradzella/);
    expect(file.save).not.toHaveBeenCalled();
  });

  it("enforces 10-per-trade limit at pre-flight — returns 400, no GCS upload", async () => {
    const preClient = makePoolClient({
      query: vi.fn()
        .mockResolvedValueOnce({ rows: [{ id: 42 }] })                        // trade exists
        .mockResolvedValueOnce({ rows: [{ count: String(MAX_PER_TRADE) }] }), // cap reached
      release: vi.fn(),
    });
    const pool = makeTwoClientPool(preClient, unusedInsertClient());
    const app  = makeApp(pool, bucket);
    const res  = await request(app)
      .post("/journal/trade/system/42/attachment?stage=review_markup")
      .set("Content-Type", "image/png").send(PNG_BYTES);
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/max 10 attachments/);
    expect(file.save).not.toHaveBeenCalled();
  });

  it("returns 500 when pre-flight DB throws", async () => {
    const preClient = makePreClient("throw");
    const pool = makeTwoClientPool(preClient, unusedInsertClient());
    const app  = makeApp(pool, bucket);
    const res  = await request(app)
      .post("/journal/trade/system/1/attachment?stage=review_markup")
      .set("Content-Type", "image/png").send(PNG_BYTES);
    expect(res.status).toBe(500);
    expect(file.save).not.toHaveBeenCalled();
  });

  it("returns 500 when GCS save throws — advisory-lock transaction never started", async () => {
    const failFile   = { ...makeFile(), save: vi.fn().mockRejectedValue(new Error("GCS unavailable")) } as unknown as File;
    const failBucket = makeBucket(failFile);
    const preClient  = makePreClient(0);
    const insertClient = unusedInsertClient();
    const pool = makeTwoClientPool(preClient, insertClient);
    const app  = makeApp(pool, failBucket);
    const res  = await request(app)
      .post("/journal/trade/system/1/attachment?stage=review_markup")
      .set("Content-Type", "image/png").send(PNG_BYTES);
    expect(res.status).toBe(500);
    expect(insertClient.query).not.toHaveBeenCalled(); // no transaction attempted
  });

  it("returns the attachment on successful upload — full advisory-lock transaction", async () => {
    const mockDate     = new Date("2026-07-31T12:00:00Z");
    const preClient    = makePreClient(2);
    const insertClient = makeInsertClient(/* freshCount */ 2, /* id */ 99, mockDate);
    const pool = makeTwoClientPool(preClient, insertClient);
    const app  = makeApp(pool, bucket);
    const res  = await request(app)
      .post("/journal/trade/system/1/attachment?stage=at_entry&filename=chart.png")
      .set("Content-Type", "image/png").send(PNG_BYTES);
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.attachment.id).toBe(99);
    expect(res.body.attachment.stage).toBe("at_entry");
    expect(res.body.attachment.serve_url).toBe("/api/journal/attachment/99");
    expect(file.save).toHaveBeenCalledOnce();
    // Verify the advisory lock was acquired: BEGIN + lock + count + INSERT + COMMIT = 5 calls
    expect(insertClient.query).toHaveBeenCalledTimes(5);
    const lockCall = (insertClient.query as ReturnType<typeof vi.fn>).mock.calls[1];
    expect(lockCall[0]).toMatch(/pg_advisory_xact_lock/);
  });

  it("advisory lock — under-cap fresh count under lock is re-checked after GCS upload", async () => {
    // Cap-hit discovered under the advisory lock (not the pre-flight check)
    const preClient    = makePreClient(9);   // pre-flight: 9 < 10 → OK
    const insertClient = makeInsertClientCapHit(); // locked count = 10 → ROLLBACK
    const pool = makeTwoClientPool(preClient, insertClient);
    const app  = makeApp(pool, bucket);
    const res  = await request(app)
      .post("/journal/trade/system/1/attachment?stage=review_markup")
      .set("Content-Type", "image/png").send(PNG_BYTES);
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/max 10 attachments/);
    // GCS upload happened before the lock; best-effort GCS delete is attempted
    expect(file.save).toHaveBeenCalledOnce();
    // Transaction: BEGIN + lock + count (=10) + ROLLBACK = 4 calls; no INSERT
    expect(insertClient.query).toHaveBeenCalledTimes(4);
  });

  it("advisory lock serializes slot contention: first upload succeeds, second sees full cap", async () => {
    // Scenario: two uploads arrive when there are 9 existing attachments.
    // Pre-flight check passes for both (count=9 < 10) — this is intentionally
    // optimistic and is expected to race.  The advisory lock serializes the
    // actual INSERT: whichever request acquires the lock first succeeds; the
    // second then sees count=10 under the lock and is rolled back.
    //
    // The test submits the requests sequentially (deterministic mock ordering)
    // to prove the invariant: even when both pre-flights see count=9, the
    // locked fresh count ensures only one insert commits.
    const preA    = makePreClient(9);
    const insertA = makeInsertClient(/* freshCount */ 9, /* id */ 50, new Date());
    const preB    = makePreClient(9);           // also sees count=9 at pre-flight
    const insertB = makeInsertClientCapHit();   // locked count=10 → ROLLBACK

    // Sequential mock order per request: (pre, insert) × 2
    const pool = {
      connect: vi.fn()
        .mockResolvedValueOnce(preA)
        .mockResolvedValueOnce(insertA)
        .mockResolvedValueOnce(preB)
        .mockResolvedValueOnce(insertB),
    } as unknown as MockPool;

    const app = makeApp(pool, bucket);

    // Request A: lock acquired first → fresh count 9 → INSERT → COMMIT → 200
    const resA = await request(app)
      .post("/journal/trade/system/1/attachment?stage=review_markup")
      .set("Content-Type", "image/png").send(PNG_BYTES);
    expect(resA.status).toBe(200);

    // Request B: lock acquired after A commits → fresh count 10 → ROLLBACK → 400
    const resB = await request(app)
      .post("/journal/trade/system/1/attachment?stage=review_markup")
      .set("Content-Type", "image/png").send(PNG_BYTES);
    expect(resB.status).toBe(400);
    expect(resB.body.error).toMatch(/max 10 attachments/);

    // Both requests uploaded to GCS; B's object was then deleted (best-effort)
    expect(file.save).toHaveBeenCalledTimes(2);
    // insertB ran the advisory lock transaction: BEGIN + lock + count + ROLLBACK = 4 calls
    expect(insertB.query).toHaveBeenCalledTimes(4);
    const insertBCalls = (insertB.query as ReturnType<typeof vi.fn>).mock.calls;
    expect(insertBCalls[1][0]).toMatch(/pg_advisory_xact_lock/);
    expect(insertBCalls[3][0]).toBe("ROLLBACK");
  });
});

describe("GET /journal/trade/:source/:id/attachments", () => {
  it("returns attachment list for a trade", async () => {
    const row = { id: 5, source: "system", trade_id: 1, stage: "review_markup",
                  filename: "x.png", mime_type: "image/png", size_bytes: 100,
                  created_at: new Date("2026-07-01T00:00:00Z") };
    const client = makePoolClient({ query: vi.fn().mockResolvedValue({ rows: [row] }), release: vi.fn() });
    const pool   = makePool(client);
    const bucket = makeBucket();
    const app    = makeApp(pool, bucket);
    const res = await request(app).get("/journal/trade/system/1/attachments");
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.attachments).toHaveLength(1);
    expect(res.body.attachments[0].serve_url).toBe("/api/journal/attachment/5");
  });

  it("returns empty list when no attachments exist", async () => {
    const client = makePoolClient({ query: vi.fn().mockResolvedValue({ rows: [] }), release: vi.fn() });
    const pool   = makePool(client);
    const app    = makeApp(pool, makeBucket());
    const res = await request(app).get("/journal/trade/tradzella/999/attachments");
    expect(res.status).toBe(200);
    expect(res.body.attachments).toHaveLength(0);
  });

  it("returns 400 for invalid source", async () => {
    const client = makePoolClient({ query: vi.fn(), release: vi.fn() });
    const pool   = makePool(client);
    const app    = makeApp(pool, makeBucket());
    const res = await request(app).get("/journal/trade/badSource/1/attachments");
    expect(res.status).toBe(400);
  });

  it("returns 500 when DB query throws", async () => {
    const client = makePoolClient({
      query:   vi.fn().mockRejectedValue(new Error("db down")),
      release: vi.fn(),
    });
    const pool = makePool(client);
    const app  = makeApp(pool, makeBucket());
    const res  = await request(app).get("/journal/trade/system/1/attachments");
    expect(res.status).toBe(500);
    expect(res.body.ok).toBe(false);
  });
});

describe("GET /journal/attachment/:id", () => {
  it("returns 404 when attachment not found in DB", async () => {
    const client = makePoolClient({ query: vi.fn().mockResolvedValue({ rows: [] }), release: vi.fn() });
    const pool   = makePool(client);
    const app    = makeApp(pool, makeBucket());
    const res = await request(app).get("/journal/attachment/9999");
    expect(res.status).toBe(404);
    expect(res.body.ok).toBe(false);
  });

  it("returns 404 when object is missing from GCS", async () => {
    const client = makePoolClient({
      query: vi.fn().mockResolvedValue({ rows: [{ storage_key: "gcs/key", mime_type: "image/png", filename: "a.png" }] }),
      release: vi.fn(),
    });
    const pool    = makePool(client);
    const badFile = makeFile(false);  // exists() → false
    const bucket  = makeBucket(badFile);
    const app     = makeApp(pool, bucket);
    const res = await request(app).get("/journal/attachment/1");
    expect(res.status).toBe(404);
    expect(res.body.error).toMatch(/file not found in storage/);
  });
});

describe("DELETE /journal/attachment/:id", () => {
  it("returns 404 when attachment not in DB", async () => {
    const client = makePoolClient({ query: vi.fn().mockResolvedValue({ rows: [] }), release: vi.fn() });
    const pool   = makePool(client);
    const app    = makeApp(pool, makeBucket());
    const res = await request(app).delete("/journal/attachment/9999");
    expect(res.status).toBe(404);
  });

  it("deletes from GCS and DB, returns ok", async () => {
    const file   = makeFile();
    const bucket = makeBucket(file);
    const client = makePoolClient({
      query: vi.fn()
        .mockResolvedValueOnce({ rows: [{ id: 3, storage_key: "gcs/key/file.png" }] })
        .mockResolvedValueOnce({ rows: [] }), // DELETE
      release: vi.fn(),
    });
    const pool = makePool(client);
    const app  = makeApp(pool, bucket);
    const res  = await request(app).delete("/journal/attachment/3");
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.id).toBe(3);
    expect(file.delete).toHaveBeenCalledOnce();
  });

  it("still removes the DB row even when GCS delete throws", async () => {
    const gcsFailFile = {
      ...makeFile(),
      delete: vi.fn().mockRejectedValue(new Error("GCS 503")),
    } as unknown as File;
    const bucket = makeBucket(gcsFailFile);
    const client = makePoolClient({
      query: vi.fn()
        .mockResolvedValueOnce({ rows: [{ id: 7, storage_key: "k" }] })
        .mockResolvedValueOnce({ rows: [] }),
      release: vi.fn(),
    });
    const pool = makePool(client);
    const app  = makeApp(pool, bucket);
    const res  = await request(app).delete("/journal/attachment/7");
    // GCS delete is fail-soft — the DB row should still be removed
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
  });
});
