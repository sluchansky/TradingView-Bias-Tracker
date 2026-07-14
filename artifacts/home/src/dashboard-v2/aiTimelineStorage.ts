import type { DashboardTicker } from "./types";
import type { AITimelineEvent } from "./aiTimelineTypes";
import { TIMELINE_LIMIT } from "./composeTimelineEvents.ts";

export type TimelineStorage = Pick<Storage, "getItem" | "setItem">;

const VALID_CATEGORIES = new Set([
  "System", "Monitoring", "Verdict", "Edge", "Structure", "Liquidity",
  "Order Flow", "VWAP", "Volatility", "Risk", "Trade", "News",
]);
const VALID_TONES = new Set(["blue", "purple", "cyan", "amber", "green", "red", "gray"]);
const VALID_INSTRUMENTS = new Set(["MNQ", "MGC", "MES", "MYM"]);

export function timelineSessionDate(date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export function timelineStorageKey(ticker: DashboardTicker, date: string): string {
  return `atp_ai_timeline_v1:${date}:${ticker}`;
}

function isTimelineEvent(value: unknown): value is AITimelineEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Partial<AITimelineEvent>;
  return typeof event.id === "string"
    && typeof event.key === "string"
    && typeof event.timestamp === "string"
    && typeof event.category === "string"
    && VALID_CATEGORIES.has(event.category)
    && typeof event.message === "string"
    && typeof event.tone === "string"
    && VALID_TONES.has(event.tone)
    && typeof event.instrument === "string"
    && VALID_INSTRUMENTS.has(event.instrument)
    && typeof event.state === "string"
    && Number.isFinite(new Date(event.timestamp).getTime());
}

export function restoreTimeline(storage: TimelineStorage, key: string): AITimelineEvent[] {
  try {
    const parsed = JSON.parse(storage.getItem(key) ?? "[]");
    return Array.isArray(parsed)
      ? parsed.filter(isTimelineEvent).slice(0, TIMELINE_LIMIT)
      : [];
  } catch {
    return [];
  }
}

export function persistTimeline(
  storage: TimelineStorage,
  key: string,
  events: AITimelineEvent[],
) {
  try {
    storage.setItem(key, JSON.stringify(events.slice(0, TIMELINE_LIMIT)));
  } catch {
    // Timeline remains available in memory if storage is unavailable.
  }
}
