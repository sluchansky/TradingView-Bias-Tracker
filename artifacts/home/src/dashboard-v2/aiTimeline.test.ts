import test from "node:test";
import assert from "node:assert/strict";
import {
  TIMELINE_LIMIT,
  composeTimelineEvents,
  createTimelineSnapshot,
  mergeTimelineEvents,
} from "./composeTimelineEvents.ts";
import {
  persistTimeline,
  restoreTimeline,
  timelineSessionDate,
  timelineStorageKey,
  type TimelineStorage,
} from "./aiTimelineStorage.ts";
import type { AITimelineEvent } from "./aiTimelineTypes";

const NOW = new Date("2026-07-14T15:30:00.000Z");

function snapshot(data: unknown, connection: "connected" | "error" | "loading" = "connected") {
  return createTimelineSnapshot({ ticker: "MNQ", connection, data });
}

function timelineEvent(index: number): AITimelineEvent {
  return {
    id: `event-${index}`,
    key: `Edge:MNQ:state-${index}`,
    timestamp: new Date(NOW.getTime() - index * 1000).toISOString(),
    category: "Edge",
    message: `Edge event ${index}.`,
    tone: "purple",
    instrument: "MNQ",
    state: `state-${index}`,
  };
}

test("creates one honest disconnected-service event", () => {
  const before = snapshot({ verdict: "WAIT" });
  const after = snapshot({ verdict: "WAIT" }, "error");
  const events = composeTimelineEvents(before, after, NOW);

  assert.equal(events.length, 1);
  assert.equal(events[0].category, "System");
  assert.match(events[0].message, /Trading service disconnected/i);
});

test("creates a verdict-change event", () => {
  const before = snapshot({ verdict: "WAIT" });
  const after = snapshot({ verdict: "READY", strict_direction: "Long" });
  const events = composeTimelineEvents(before, after, NOW);
  const verdict = events.find((item) => item.category === "Verdict");

  assert.ok(verdict);
  assert.match(verdict.message, /WAIT to READY LONG/i);
  assert.equal(verdict.tone, "green");
});

test("creates a structure-change event", () => {
  const before = snapshot({ verdict: "WAIT", market_structure: "Range Structure" });
  const after = snapshot({ verdict: "WAIT", market_structure: "Bullish Structure" });
  const events = composeTimelineEvents(before, after, NOW);
  const structure = events.find((item) => item.category === "Structure");

  assert.ok(structure);
  assert.match(structure.message, /Range Structure to Bullish Structure/i);
});

test("creates edge event only for a meaningful threshold crossing", () => {
  const before = snapshot({ verdict: "WAIT", edge_score: 19 });
  const smallMove = snapshot({ verdict: "WAIT", edge_score: 19.5 });
  const crossing = snapshot({ verdict: "WAIT", edge_score: 20 });

  assert.equal(
    composeTimelineEvents(before, smallMove, NOW).filter((item) => item.category === "Edge").length,
    0,
  );
  const edge = composeTimelineEvents(before, crossing, NOW)
    .find((item) => item.category === "Edge");
  assert.ok(edge);
  assert.match(edge.message, /crossing 20/i);
});

test("suppresses duplicate continuing events", () => {
  const before = snapshot({ verdict: "WAIT" });
  const after = snapshot({ verdict: "READY", strict_direction: "Long" });
  const incoming = composeTimelineEvents(before, after, NOW);
  const once = mergeTimelineEvents([], incoming, NOW);
  const repeated = mergeTimelineEvents(
    once,
    incoming,
    new Date(NOW.getTime() + 60_000),
  );

  assert.equal(once.length, 1);
  assert.equal(repeated.length, 1);
});

test("keeps only the newest 100 events", () => {
  const oversized = Array.from({ length: 125 }, (_, index) => timelineEvent(index));
  const limited = mergeTimelineEvents(oversized, [], NOW);

  assert.equal(limited.length, TIMELINE_LIMIT);
  assert.equal(limited[0].id, "event-0");
  assert.equal(limited[99].id, "event-99");
});

test("returns no initial event while waiting for first status data", () => {
  const current = snapshot(null, "loading");
  assert.deepEqual(composeTimelineEvents(null, current, NOW), []);
});

test("restores the current instrument and session timeline from storage", () => {
  const values = new Map<string, string>();
  const storage: TimelineStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value); },
  };
  const date = timelineSessionDate(NOW);
  const mnqKey = timelineStorageKey("MNQ", date);
  const mgcKey = timelineStorageKey("MGC", date);
  const expected = [timelineEvent(1), timelineEvent(2)];

  persistTimeline(storage, mnqKey, expected);

  assert.deepEqual(restoreTimeline(storage, mnqKey), expected);
  assert.deepEqual(restoreTimeline(storage, mgcKey), []);
  assert.match(mnqKey, /MNQ$/);
});
