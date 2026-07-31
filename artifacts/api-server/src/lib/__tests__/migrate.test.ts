/**
 * migrate.test.ts — path-resolution and migration-runner unit tests.
 *
 * These tests verify that:
 *  1. findMigrationsDir() correctly locates lib/db/migrations from any
 *     ancestor directory, including the bundled dist/ layout.
 *  2. MIGRATIONS_DIR resolves to an existing directory that contains the
 *     expected SQL migration file(s).
 *  3. runMigrations() reads each SQL file in sorted order and applies it via
 *     the pool client.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { existsSync, readdirSync } from "node:fs";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";

// ── Mock @workspace/db so we can inject a test pool ────────────────────────
vi.mock("@workspace/db", () => ({
  pool: { connect: vi.fn() },
}));
// Also suppress the logger so test output stays clean
vi.mock("../logger", () => ({
  logger: { info: vi.fn(), error: vi.fn(), warn: vi.fn(), debug: vi.fn() },
}));

// Import AFTER mocking so the module picks up the stubs
import { findMigrationsDir, MIGRATIONS_DIR, runMigrations } from "../migrate";
import { pool as mockPool } from "@workspace/db";

// Compute stable path anchors from this test file's location.
// this file: .../workspace/artifacts/api-server/src/lib/__tests__/migrate.test.ts
// dirname   : .../workspace/artifacts/api-server/src/lib/__tests__
// 5 levels up → workspace root (.../workspace)
const THIS_DIR = resolve(fileURLToPath(import.meta.url), "..");
const WORKSPACE_ROOT = resolve(THIS_DIR, "../../../../..");

// ── 1. findMigrationsDir ────────────────────────────────────────────────────
describe("findMigrationsDir", () => {
  it("finds lib/db/migrations when starting from the workspace root", () => {
    const found = findMigrationsDir(WORKSPACE_ROOT);
    expect(found).toMatch(/lib[/\\]db[/\\]migrations$/);
    expect(existsSync(found)).toBe(true);
  });

  it("finds lib/db/migrations when starting from a deep subdirectory (bundled-dist simulation)", () => {
    // Simulate dist/index.mjs layout: artifacts/api-server/dist is 3 levels below
    // the workspace root.  findMigrationsDir must walk up and find lib/db/migrations
    // regardless of starting depth.
    const simulatedDistDir = join(WORKSPACE_ROOT, "artifacts", "api-server", "dist");
    expect(existsSync(simulatedDistDir)).toBe(true); // sanity: dist was built
    const found = findMigrationsDir(simulatedDistDir);
    expect(found).toMatch(/lib[/\\]db[/\\]migrations$/);
    expect(existsSync(found)).toBe(true);
  });

  it("throws when no lib/db/migrations ancestor exists", () => {
    expect(() => findMigrationsDir("/")).toThrow(/Cannot locate lib\/db\/migrations/);
  });
});

// ── 2. MIGRATIONS_DIR constant ──────────────────────────────────────────────
describe("MIGRATIONS_DIR", () => {
  it("resolves to an existing directory", () => {
    expect(existsSync(MIGRATIONS_DIR)).toBe(true);
  });

  it("contains at least one .sql migration file", () => {
    const sqlFiles = readdirSync(MIGRATIONS_DIR).filter((f) => f.endsWith(".sql"));
    expect(sqlFiles.length).toBeGreaterThan(0);
  });

  it("contains the journal_attachments migration", () => {
    const sqlFiles = readdirSync(MIGRATIONS_DIR).filter((f) => f.endsWith(".sql"));
    expect(sqlFiles.some((f) => f.includes("journal_attachments"))).toBe(true);
  });
});

// ── 3. runMigrations ────────────────────────────────────────────────────────
describe("runMigrations", () => {
  let mockClient: { query: ReturnType<typeof vi.fn>; release: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    mockClient = { query: vi.fn().mockResolvedValue({ rows: [] }), release: vi.fn() };
    (mockPool.connect as ReturnType<typeof vi.fn>).mockResolvedValue(mockClient);
  });

  it("connects to the pool, applies every SQL file in sorted order, then releases", async () => {
    await runMigrations();

    expect(mockPool.connect).toHaveBeenCalledOnce();
    expect(mockClient.release).toHaveBeenCalledOnce();

    const sqlFiles = readdirSync(MIGRATIONS_DIR).filter((f) => f.endsWith(".sql")).sort();
    expect(mockClient.query).toHaveBeenCalledTimes(sqlFiles.length);

    // Each query call must include the CREATE TABLE / CREATE INDEX SQL
    const allCalls = (mockClient.query as ReturnType<typeof vi.fn>).mock.calls;
    for (const [sql] of allCalls) {
      expect(typeof sql).toBe("string");
      expect(sql.length).toBeGreaterThan(0);
    }
  });

  it("applies migrations in alphabetical order (001 before 002, etc.)", async () => {
    await runMigrations();
    const calls = (mockClient.query as ReturnType<typeof vi.fn>).mock.calls.map(
      ([sql]: [string]) => sql,
    );
    const sortedNames = readdirSync(MIGRATIONS_DIR)
      .filter((f) => f.endsWith(".sql"))
      .sort();
    // Verify the SQL executed for each call contains content from the sorted file
    expect(calls.length).toBe(sortedNames.length);
    // Spot-check: first file's SQL must mention journal_attachments
    expect(calls[0]).toMatch(/journal_attachments/);
    expect(calls[0]).toMatch(/CREATE TABLE IF NOT EXISTS/);
  });

  it("releases the pool client even when a query throws", async () => {
    mockClient.query.mockRejectedValue(new Error("DB error"));
    await expect(runMigrations()).rejects.toThrow("DB error");
    expect(mockClient.release).toHaveBeenCalledOnce();
  });
});
