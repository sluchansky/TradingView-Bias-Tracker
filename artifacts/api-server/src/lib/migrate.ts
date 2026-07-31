/**
 * migrate.ts — applies all SQL migration files in lib/db/migrations/ in
 * alphabetical order before the Express server starts accepting requests.
 *
 * Every migration file uses CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT
 * EXISTS so the runner is safe to call on every startup (idempotent).  A
 * failed migration aborts the process so the platform restarts cleanly
 * rather than serving traffic against a mismatched schema.
 *
 * PATH RESOLUTION NOTE
 * --------------------
 * esbuild bundles every TypeScript source file into a single dist/index.mjs,
 * so import.meta.url always resolves to that one file.  The depth from that
 * file to the monorepo root differs from the depth from the TypeScript source
 * file (vitest runs source files directly).  Rather than hardcoding a depth
 * that breaks in one context, we walk up from the current file's directory
 * until we find lib/db/migrations — which is reliable in both bundled and
 * source layouts and in any deployment root.
 */
import { readdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { pool } from "@workspace/db";
import { logger } from "./logger";

/**
 * Walk up from startDir until a directory named lib/db/migrations is found as
 * a child.  Returns the absolute path to that directory.  Throws if not found
 * within 10 ancestor levels (i.e. the repo is missing the migrations folder).
 *
 * Exported for path-resolution unit tests.
 */
export function findMigrationsDir(startDir: string): string {
  let dir = startDir;
  for (let i = 0; i < 10; i++) {
    const candidate = join(dir, "lib", "db", "migrations");
    if (existsSync(candidate)) return candidate;
    const parent = dirname(dir);
    if (parent === dir) break; // reached filesystem root
    dir = parent;
  }
  throw new Error(`Cannot locate lib/db/migrations walking up from ${startDir}`);
}

// Exported so path-resolution tests can assert the value without importing pool.
export const MIGRATIONS_DIR: string = findMigrationsDir(
  dirname(fileURLToPath(import.meta.url)),
);

export async function runMigrations(): Promise<void> {
  let files: string[];
  try {
    const entries = await readdir(MIGRATIONS_DIR);
    files = entries.filter((f) => f.endsWith(".sql")).sort();
  } catch (err: any) {
    logger.error({ err, dir: MIGRATIONS_DIR }, "migrate: cannot read migrations directory");
    throw err;
  }

  if (files.length === 0) {
    logger.info("migrate: no SQL migration files found — skipping");
    return;
  }

  const client = await pool.connect();
  try {
    for (const file of files) {
      const sql = await readFile(join(MIGRATIONS_DIR, file), "utf-8");
      logger.info({ file }, "migrate: applying");
      await client.query(sql);
      logger.info({ file }, "migrate: applied");
    }
    logger.info({ count: files.length }, "migrate: all migrations applied");
  } finally {
    client.release();
  }
}
