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
  assert.doesNotMatch(result.paragraph, /^Unavailable\.?$/i);
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
  assert.match(result.paragraph, /price is below VWAP/i);
  assert.match(result.paragraph, /structure confirmation is missing/i);
  assert.match(result.paragraph, /current edge supports developing confidence/i);
  assert.doesNotMatch(result.paragraph, /Wait for bullish BOS/i);
  assert.match(result.paragraph, /resolving the outstanding condition would alter my current assessment/i);
  assert.doesNotMatch(result.paragraph, /48\/110/);
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
  assert.match(result.paragraph, /current edge supports strong confidence/i);
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
  assert.match(result.paragraph, /current edge supports moderate confidence/i);
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

  assert.match(result.paragraph, /verdict is READY\b/i);
  assert.doesNotMatch(result.paragraph, /READY (LONG|SHORT)/i);
});

test("does not treat a non-directional label as LONG", () => {
  const result = connected({
    verdict: "READY",
    strict_direction: "Neither",
    strict_reason: "Direction is unresolved",
  });

  assert.match(result.paragraph, /verdict is READY\b/i);
  assert.doesNotMatch(result.paragraph, /READY (LONG|SHORT)/i);
});

test("sanitizes multi-sentence missing-confirmation text", () => {
  const result = connected({
    verdict: "WAIT",
    strict_missing: ["BOS missing.Second sentence!Third sentence"],
  });

  assert.match(result.paragraph, /confirmed break of structure/i);
  assert.doesNotMatch(result.paragraph, /Second sentence|Third sentence/i);
});

test("does not treat NOT READY as actionable", () => {
  const result = connected({
    verdict: "SETUP NOT READY",
    strict_direction: "Long",
    strict_reason: "One confirmation remains",
  });

  assert.match(result.paragraph, /verdict is WAIT/i);
  assert.doesNotMatch(result.paragraph, /READY LONG/i);
});

test("preserves common abbreviations while flattening source sentences", () => {
  const result = connected({
    verdict: "WAIT",
    stage_next_step: "Await the U.S. session.Confirm structure next.",
  });

  assert.match(result.paragraph, /U\.S\. session; Confirm structure next/i);
  assert.doesNotMatch(result.paragraph, /Await the U\.(?:\s|$)/);
});

test("caps live briefing output at four sentences", () => {
  const result = connected({
    verdict: "WAIT",
    edge_score: 61,
    bias: "Bullish",
    market_structure: "Range Structure",
    current_price: 100,
    vwap_value: 101,
    strict_reason: "Structure is incomplete",
    strict_missing: ["BOS", "VWAP confirmation"],
    stage_next_step: "Wait for a bullish BOS",
    stage_invalidation: "Price invalidates the current zone",
  });
  const sentences = result.paragraph.split(/(?<=[.!?])\s+/);

  assert.ok(sentences.length <= 4);
  assert.match(result.paragraph, /current edge supports moderate confidence/i);
  assert.match(result.paragraph, /I would reconsider if Price invalidates the current zone/i);
});

test("preserves conflict semantics in missing confirmations", () => {
  const result = connected({
    verdict: "WAIT",
    edge_score: 42,
    strict_missing: ["conflicting_structure", "cvd_conflict"],
  });

  assert.match(result.paragraph, /resolution of conflicting structure/i);
  assert.match(result.paragraph, /delta alignment/i);
  assert.doesNotMatch(result.paragraph, /confirmed break of structure|delta confirmation/i);
});

test("does not infer required blockers from optional gate diagnostics", () => {
  const result = connected({
    verdict: "WAIT",
    market_structure: "Range Structure",
    gate_debug: { zoneValid: false, cvd_ok: false },
  });

  assert.equal(result.status, "Reviewing structure");
  assert.doesNotMatch(result.paragraph, /zone confirmation|delta confirmation/i);
});

test("mentions each natural missing confirmation only once", () => {
  const result = connected({
    verdict: "WAIT",
    edge_score: 60,
    strict_reason: "Structure confirmation is missing",
    strict_missing: ["BOS"],
  });
  const matches = result.paragraph.match(/structure confirmation is missing/gi) ?? [];

  assert.equal(matches.length, 1);
  assert.doesNotMatch(result.paragraph, /confirmed break of structure/i);
  assert.match(result.paragraph, /current edge supports moderate confidence/i);
});

test("deduplicates non-confirmation blockers against the verdict reason", () => {
  const result = connected({
    verdict: "WAIT",
    edge_score: 35,
    strict_reason: "Edge score is below the required threshold",
    strict_missing: ["edge_score_low"],
  });
  const edgeReason = result.paragraph.match(/edge score is below the required threshold/gi) ?? [];

  assert.equal(edgeReason.length, 1);
  assert.doesNotMatch(result.paragraph, /sufficient edge score/i);
  assert.equal(result.status, "Waiting for conditions");
});

test("describes volatility and location blockers as conditions, not confirmations", () => {
  const result = connected({
    verdict: "WAIT",
    strict_missing: ["volatility_block", "location"],
  });

  assert.equal(result.status, "Waiting for conditions");
  assert.match(result.paragraph, /volatility to return inside the allowed range/i);
  assert.match(result.paragraph, /acceptable entry location/i);
  assert.doesNotMatch(result.paragraph, /volatility confirmation|location confirmation/i);
  assert.match(result.paragraph, /resolving the outstanding condition would alter my current assessment/i);
});

test("classifies next-step-only non-confirmation blockers as conditions", () => {
  const session = connected({
    verdict: "WAIT",
    stage_next_step: "Wait for the U.S. session window",
  });
  const volatility = connected({
    verdict: "WAIT",
    stage_next_step: "Wait for volatility to normalize",
  });

  assert.equal(session.status, "Waiting for conditions");
  assert.equal(volatility.status, "Waiting for conditions");
});

test("keeps first-person rationale capitalized and uses a clear condition referent", () => {
  const result = connected({
    verdict: "WAIT",
    strict_missing: ["edge_score_low"],
  });

  assert.match(result.paragraph, /because I still need a sufficient edge score/i);
  assert.match(result.paragraph, /resolving the outstanding condition would alter my current assessment/i);
  assert.doesNotMatch(result.paragraph, /because i still/);
  assert.doesNotMatch(result.paragraph, /a change there/i);
});

test("classifies conflict resolution as a condition", () => {
  const cvd = connected({
    verdict: "WAIT",
    strict_missing: ["cvd_conflict"],
  });
  const structure = connected({
    verdict: "WAIT",
    stage_next_step: "Resolve conflicting structure",
  });

  assert.equal(cvd.status, "Waiting for conditions");
  assert.equal(structure.status, "Waiting for conditions");
});

test("preserves leading acronyms in verdict reasoning", () => {
  const result = connected({
    verdict: "WAIT",
    strict_reason: "VWAP confirmation is missing",
  });

  assert.match(result.paragraph, /because VWAP confirmation is missing/i);
  assert.doesNotMatch(result.paragraph, /because vWAP/);
});

test("removes imperative prefixes from the watch sentence", () => {
  const result = connected({
    verdict: "WAIT",
    stage_next_step: "Wait for buyers to reclaim VWAP",
  });

  assert.match(result.paragraph, /I’m watching for buyers to reclaim VWAP/i);
  assert.doesNotMatch(result.paragraph, /watching for Wait for/i);
});
