import test from "node:test";
import assert from "node:assert/strict";
import { composeAIBriefing } from "./composeAIBriefing.ts";
import type { DashboardStatus } from "./types";

function connected(data: DashboardStatus) {
  return composeAIBriefing({ data, ticker: "MNQ", connection: "connected" });
}

test("returns an honest unavailable-data briefing", () => {
  const result = composeAIBriefing({ data: null, ticker: "MNQ", connection: "error" });

  assert.equal(result.status, "Waiting for market data");
  assert.match(result.paragraph, /trading service is currently unavailable/i);
  assert.match(result.paragraph, /fresh status data/i);
});

test("explains WAIT with missing confirmation and next step", () => {
  const result = connected({
    verdict: "WAIT",
    edge_score: 48,
    bias: "Bullish",
    market_structure: "Range Structure",
    current_price: 100,
    vwap_value: 101,
    strict_reason: "Structure confirmation is missing",
    strict_missing: ["BOS"],
    stage_next_step: "Wait for bullish BOS",
    main_brain: { status: "WATCHING" },
  });

  assert.equal(result.status, "Waiting for confirmation");
  assert.match(result.paragraph, /verdict is WAIT/i);
  assert.match(result.paragraph, /48\/110/);
  assert.match(result.paragraph, /price is below VWAP/i);
  assert.match(result.paragraph, /still waiting on BOS/i);
  assert.match(result.paragraph, /Wait for bullish BOS/i);
});

test("composes a READY LONG briefing", () => {
  const result = connected({
    verdict: "READY",
    edge_score: 82,
    bias: "Bullish",
    market_structure: "Bullish Structure",
    strict_reason: "Long setup conditions are aligned",
    main_brain: { status: "READY", favored_direction: "Long" },
  });

  assert.equal(result.status, "Setup ready");
  assert.match(result.paragraph, /verdict is READY LONG/i);
  assert.match(result.paragraph, /82\/110/);
  assert.doesNotMatch(result.paragraph, /READY SHORT/i);
});

test("composes a READY SHORT briefing", () => {
  const result = connected({
    verdict: "READY",
    edge_score: 77,
    bias: "Bearish",
    market_structure: "Bearish Structure",
    strict_reason: "Short setup conditions are aligned",
    main_brain: { status: "READY", favored_direction: "Short" },
  });

  assert.equal(result.status, "Setup ready");
  assert.match(result.paragraph, /verdict is READY SHORT/i);
  assert.match(result.paragraph, /77\/110/);
  assert.doesNotMatch(result.paragraph, /READY LONG/i);
});

test("does not let display brain status override an authoritative WAIT", () => {
  const result = connected({
    verdict: "WAIT",
    strict_reason: "Confirmation is still missing",
    main_brain: { status: "READY", favored_direction: "Long" },
  });

  assert.match(result.paragraph, /verdict is WAIT/i);
  assert.doesNotMatch(result.paragraph, /READY LONG/i);
});

test("does not invent a direction for an ambiguous READY verdict", () => {
  const result = connected({
    verdict: "READY",
    strict_reason: "Setup conditions are aligned",
  });

  assert.match(result.paragraph, /verdict is READY(?:[.:])/i);
  assert.doesNotMatch(result.paragraph, /READY (LONG|SHORT)/i);
});

test("does not treat a non-directional label as LONG", () => {
  const result = connected({
    verdict: "READY",
    strict_direction: "Neither",
    strict_reason: "Direction is unresolved",
  });

  assert.match(result.paragraph, /verdict is READY(?:[.:])/i);
  assert.doesNotMatch(result.paragraph, /READY (LONG|SHORT)/i);
});

test("sanitizes multi-sentence missing-confirmation text", () => {
  const result = connected({
    verdict: "WAIT",
    strict_missing: ["BOS missing.Second sentence!Third sentence"],
  });

  assert.match(result.paragraph, /still waiting on BOS missing/i);
  assert.doesNotMatch(result.paragraph, /Second sentence|Third sentence/i);
});
