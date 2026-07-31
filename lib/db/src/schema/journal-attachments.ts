/**
 * journal_attachments — per-trade screenshot metadata for the Journal Review feature.
 *
 * Files live in GCS (App Storage); this table stores only the metadata and
 * object-storage key.  The actual CREATE TABLE is managed by drizzle-kit push.
 *
 * INSERT/SELECT only from application code — no DDL at runtime.
 */

import {
  pgTable,
  serial,
  text,
  integer,
  timestamp,
  unique,
  index,
} from "drizzle-orm/pg-core";
import { sql } from "drizzle-orm";
import { check } from "drizzle-orm/pg-core";

export const journalAttachments = pgTable(
  "journal_attachments",
  {
    id: serial("id").primaryKey(),

    /** Trade origin: 'system' (bot-generated) or 'tradzella' (imported). */
    source: text("source").notNull(),

    /** Foreign-key style reference to the trade id in the source table. */
    trade_id: integer("trade_id").notNull(),

    /**
     * When in the trade lifecycle the screenshot was captured.
     * Allowed: before_entry | at_entry | during | after_exit | review_markup
     */
    stage: text("stage").notNull().default("review_markup"),

    /** Original filename (sanitised before storage). */
    filename: text("filename").notNull(),

    /** Full GCS object key (bucket-relative path). UNIQUE — one slot per file. */
    storage_key: text("storage_key").notNull(),

    /** MIME type declared by the client and validated server-side. */
    mime_type: text("mime_type").notNull(),

    /** File size in bytes (max 5 MB = 5_242_880). */
    size_bytes: integer("size_bytes").notNull(),

    created_at: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [
    // Constraints — mirrors the CHECK constraints already applied to the live DB.
    check(
      "chk_attach_source",
      sql`${t.source} IN ('system', 'tradzella')`,
    ),
    check(
      "chk_attach_stage",
      sql`${t.stage} IN ('before_entry','at_entry','during','after_exit','review_markup')`,
    ),
    check(
      "chk_attach_size",
      sql`${t.size_bytes} > 0 AND ${t.size_bytes} <= 5242880`,
    ),
    // Storage key must be globally unique (one GCS object = one row).
    unique("journal_attachments_storage_key_key").on(t.storage_key),
    // Fast lookup by (source, trade_id) for list and count queries.
    index("idx_journal_attach_trade").on(t.source, t.trade_id, t.created_at),
  ],
);

export type JournalAttachment    = typeof journalAttachments.$inferSelect;
export type NewJournalAttachment = typeof journalAttachments.$inferInsert;
