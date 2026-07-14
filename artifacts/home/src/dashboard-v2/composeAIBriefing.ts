import type { ConnectionState, DashboardStatus, DashboardTicker } from "./types";

export type AIBriefing = {
  status: string;
  paragraph: string;
};

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function number(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function flattenPunctuation(value: string): string {
  let output = "";
  for (let index = 0; index < value.length; index += 1) {
    const character = value[index];
    if (character === "!" || character === "?") {
      output += ";";
      continue;
    }
    if (character !== ".") {
      output += character;
      continue;
    }

    const previous = value[index - 1] ?? "";
    const next = value[index + 1] ?? "";
    const token = value.slice(Math.max(0, value.lastIndexOf(" ", index - 1) + 1), index);
    const decimal = /\d/.test(previous) && /\d/.test(next);
    const abbreviation = /^[A-Za-z]$/.test(token)
      || /^(?:[A-Za-z]\.)+[A-Za-z]$/.test(token);
    output += decimal || abbreviation ? "." : ";";
  }
  return output
    .replace(/\s*;\s*/g, "; ")
    .replace(/(?:;\s*)+/g, "; ")
    .replace(/[;\s]+$/, "")
    .trim();
}

function concise(value: string | null, maxLength = 220): string | null {
  if (!value) return null;
  const flattened = flattenPunctuation(value);
  if (!flattened) return null;
  return flattened.length <= maxLength
    ? flattened
    : `${flattened.slice(0, maxLength - 1).trimEnd()}…`;
}

function conciseMissing(value: string | null): string | null {
  const flattened = concise(value, 100);
  return flattened?.split(";")[0].trim() || null;
}

function sentence(value: string): string {
  const trimmed = value.trim();
  const capitalized = trimmed ? `${trimmed[0].toUpperCase()}${trimmed.slice(1)}` : trimmed;
  return /[.!?]$/.test(capitalized) ? capitalized : `${capitalized}.`;
}

function sameText(left: string | null, right: string | null): boolean {
  return left !== null && right !== null
    && left.toLowerCase().replace(/[.!?;]$/, "") === right.toLowerCase().replace(/[.!?;]$/, "");
}

function naturalConfirmation(value: string, direction: string | null): string {
  const normalized = value.toLowerCase().replace(/_/g, " ");
  if (/conflicting structure|structure conflict/.test(normalized)) {
    return "resolution of conflicting structure";
  }
  if (/cvd conflict|delta conflict/.test(normalized)) {
    return "delta alignment";
  }
  if (/edge score|edge low/.test(normalized)) {
    return "a sufficient edge score";
  }
  if (/volatility block|too volatile|too quiet/.test(normalized)) {
    return "volatility to return inside the allowed range";
  }
  if (/location/.test(normalized)) {
    return "an acceptable entry location";
  }
  if (/session filter|session window/.test(normalized)) {
    return "the preferred session window";
  }
  if (/cooldown|duplicate/.test(normalized)) {
    return "the duplicate-signal cooldown to clear";
  }
  if (/data stale|fresh data/.test(normalized)) {
    return "fresh market data";
  }
  if (/\bbos\b|break of structure|structure/.test(normalized)) {
    return "a confirmed break of structure";
  }
  if (/vwap/.test(normalized)) {
    return direction === "Long"
      ? "a VWAP reclaim"
      : direction === "Short"
        ? "VWAP confirmation for the short"
        : "VWAP confirmation";
  }
  if (/liquidity|sweep/.test(normalized)) return "liquidity confirmation";
  if (/zone/.test(normalized)) return "zone confirmation";
  if (/cvd|delta/.test(normalized)) return "delta confirmation";
  if (/volume|rvol/.test(normalized)) return "volume confirmation";
  if (/location|entry quality/.test(normalized)) return "entry-location confirmation";
  return normalized;
}

function confidenceLanguage(edge: number | null): string | null {
  if (edge === null) return null;
  if (edge >= 80) return "strong";
  if (edge >= 55) return "moderate";
  if (edge >= 30) return "developing";
  return "limited";
}

function confirmationConcepts(value: string | null): string[] {
  if (!value) return [];
  const normalized = value.toLowerCase();
  return [
    /\bbos\b|break of structure|structure/.test(normalized) ? "structure" : null,
    /vwap/.test(normalized) ? "vwap" : null,
    /liquidity|sweep/.test(normalized) ? "liquidity" : null,
    /cvd|delta/.test(normalized) ? "delta" : null,
    /volume|rvol/.test(normalized) ? "volume" : null,
    /zone/.test(normalized) ? "zone" : null,
    /edge score|edge low/.test(normalized) ? "edge" : null,
    /location/.test(normalized) ? "location" : null,
    /volatility|too volatile|too quiet/.test(normalized) ? "volatility" : null,
    /session/.test(normalized) ? "session" : null,
    /cooldown|duplicate/.test(normalized) ? "cooldown" : null,
    /data stale|fresh data/.test(normalized) ? "data" : null,
  ].filter((concept): concept is string => concept !== null);
}

function sharesConfirmationConcept(left: string | null, right: string | null): boolean {
  const leftConcepts = confirmationConcepts(left);
  const rightConcepts = confirmationConcepts(right);
  return leftConcepts.some((concept) => rightConcepts.includes(concept));
}

function afterBecause(value: string): string {
  return /^I\b/.test(value)
    ? value
    : `${value[0].toLowerCase()}${value.slice(1)}`;
}

export function composeAIBriefing({
  data,
  ticker,
  connection,
}: {
  data: DashboardStatus | null;
  ticker: DashboardTicker;
  connection: ConnectionState;
}): AIBriefing {
  if (!data || connection !== "connected") {
    const serviceUnavailable = connection === "error";
    return {
      status: "Waiting for market data",
      paragraph: serviceUnavailable
        ? "The trading service is currently unavailable. I’ll resume analysis when fresh status data arrives."
        : "I’m waiting for fresh status data. No live market analysis is available yet.",
    };
  }

  const root = record(data);
  const brain = record(data.main_brain);
  const brainState = record(data.brain_state);
  const marketRead = record(brainState.market_read);
  const liquidityFocus = record(brain.liquidity_focus);
  const rawStatus = (text(data.verdict) ?? text(brain.status) ?? "WAIT").toUpperCase();
  const directionValue = text(data.strict_direction) ?? text(brain.favored_direction);
  const direction = /^(short|bearish)$/i.test(directionValue ?? "")
    ? "Short"
    : /^(long|bullish)$/i.test(directionValue ?? "")
      ? "Long"
      : /short|bear/i.test(rawStatus)
        ? "Short"
        : /long|bull/i.test(rawStatus)
          ? "Long"
          : null;
  const explicitlyNotReady = /\b(?:NOT READY|WAIT|NO TRADE|INVALIDATED)\b/i.test(rawStatus);
  const ready = !explicitlyNotReady
    && /\b(?:READY|STRONG TRADE|POSSIBLE TRADE)\b/i.test(rawStatus);
  const verdict = ready
    ? direction
      ? (/short|bear/i.test(direction) ? "READY SHORT" : "READY LONG")
      : "READY"
    : "WAIT";
  const edge = number(brain.edge_score) ?? number(data.edge_score);
  const bias = concise(text(data.bias));
  const structure = concise(text(data.market_structure) ?? text(marketRead.structure));
  const price = number(data.current_price);
  const vwap = number(data.vwap_value);
  const liquidity = concise(text(liquidityFocus.state) ?? text(marketRead.liquidity_state));
  const orderFlow = concise(text(marketRead.order_flow));
  const volatility = concise(text(marketRead.volatility)
    ?? text(record(root.volatility).regime));
  const reason = concise(text(data.strict_reason) ?? text(brain.wait_reason));
  const explicitMissing = Array.isArray(data.strict_missing)
    ? data.strict_missing
      .map((item) => conciseMissing(text(item)))
      .filter((item): item is string => item !== null)
      .slice(0, 3)
      .map((item) => item.replace(/_/g, " "))
    : [];
  const missing = explicitMissing
    .map((item) => naturalConfirmation(item, direction))
    .filter((item, index, items) => items.indexOf(item) === index)
    .slice(0, 3);
  const nextStep = concise(text(data.stage_next_step));
  const invalidation = concise(text(data.stage_invalidation));

  const missingAreConfirmations = missing.every((item) =>
    /confirmation|alignment|break of structure|reclaim/.test(item)
  );
  const nextConcepts = confirmationConcepts(nextStep);
  const nextIsConfirmation = nextConcepts.some((concept) =>
    ["structure", "vwap", "liquidity", "delta", "volume", "zone"].includes(concept)
  );
  const status = ready
    ? "Setup ready"
    : missing.length
      ? missingAreConfirmations ? "Waiting for confirmation" : "Waiting for conditions"
      : nextStep
        ? nextIsConfirmation ? "Waiting for confirmation" : "Waiting for conditions"
      : structure
        ? "Reviewing structure"
        : `Monitoring ${ticker}`;

  const sentences: string[] = [];
  const observation: string[] = [];
  if (bias) observation.push(`the bias is ${bias.toLowerCase()}`);
  if (structure) observation.push(`structure is ${structure.toLowerCase()}`);
  if (price !== null && vwap !== null) {
    observation.push(`price is ${price === vwap ? "at" : price > vwap ? "above" : "below"} VWAP`);
  }
  if (!observation.length && orderFlow) observation.push(`order flow is ${orderFlow}`);
  if (!observation.length && liquidity) observation.push(`liquidity is ${liquidity}`);
  if (!observation.length && volatility) observation.push(`volatility is ${volatility}`);
  sentences.push(observation.length
    ? sentence(`On ${ticker}, ${observation.slice(0, 3).join(", and ")}`)
    : `I’m monitoring ${ticker}, but the current status does not include a directional market read.`);

  const uncoveredMissing = missing.filter((item) => !sharesConfirmationConcept(item, reason));
  const missingPhrase = uncoveredMissing.join(", ");
  const verdictReason = reason && uncoveredMissing.length
    ? `${reason}; specifically, I still need ${missingPhrase}`
    : reason
      ? reason
      : uncoveredMissing.length
        ? `I still need ${missingPhrase}`
        : null;
  sentences.push(verdictReason
    ? sentence(`The verdict is ${verdict} because ${afterBecause(verdictReason)}`)
    : ready
      ? sentence(`The verdict is ${verdict} because the current trading status marks the setup as ready`)
      : `The verdict is WAIT; the current status does not include a specific decision reason.`);

  const confidence = confidenceLanguage(edge);
  if (confidence) {
    sentences.push(sentence(`The current edge supports ${confidence} confidence`));
  }

  const nextDistinct = nextStep
    && !sameText(nextStep, reason)
    && !sharesConfirmationConcept(nextStep, reason)
    && !missing.some((item) => sharesConfirmationConcept(nextStep, item))
    ? nextStep
    : null;
  if (nextDistinct || invalidation || missing.length) {
    const watchClause = nextDistinct
      ? `I’m watching for ${nextDistinct}`
      : "I’m holding the current view";
    const changeClause = invalidation
      ? `I would reconsider if ${invalidation}`
      : "resolving the outstanding condition would alter my current assessment";
    sentences.push(sentence(`${watchClause}; ${changeClause}`));
  } else if (sentences.length < 3) {
    sentences.push("I’ll continue monitoring fresh status updates before changing this assessment.");
  }

  return {
    status,
    paragraph: sentences.slice(0, 4).join(" "),
  };
}
