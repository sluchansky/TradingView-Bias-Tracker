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

function concise(value: string | null, maxLength = 220): string | null {
  if (!value) return null;
  const firstSentence = value.split(/(?<=[.!?])\s+/)[0].replace(/[.!?]+$/, "").trim();
  if (!firstSentence) return null;
  return firstSentence.length <= maxLength
    ? firstSentence
    : `${firstSentence.slice(0, maxLength - 1).trimEnd()}…`;
}

function sentence(value: string): string {
  const trimmed = value.trim();
  const capitalized = trimmed ? `${trimmed[0].toUpperCase()}${trimmed.slice(1)}` : trimmed;
  return /[.!?]$/.test(capitalized) ? capitalized : `${capitalized}.`;
}

function sameText(left: string | null, right: string | null): boolean {
  return left !== null && right !== null
    && left.toLowerCase().replace(/[.!?]$/, "") === right.toLowerCase().replace(/[.!?]$/, "");
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
  const rawStatus = (
    text(brain.status)
    ?? text(data.verdict)
    ?? "WAIT"
  ).toUpperCase();
  const direction = text(brain.favored_direction) ?? text(data.strict_direction);
  const ready = rawStatus.includes("READY");
  const verdict = ready
    ? (/short|bear/i.test(direction ?? rawStatus) ? "READY SHORT" : "READY LONG")
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
  const missing = Array.isArray(data.strict_missing)
    ? data.strict_missing
      .map(text)
      .filter((item): item is string => item !== null)
      .slice(0, 3)
      .map((item) => item.replace(/_/g, " "))
    : [];
  const nextStep = concise(text(data.stage_next_step));

  const status = ready
    ? "Setup ready"
    : missing.length || nextStep
      ? "Waiting for confirmation"
      : structure
        ? "Reviewing structure"
        : `Monitoring ${ticker}`;

  const sentences: string[] = [];
  const view = [
    bias ? `${bias.toLowerCase()} bias` : null,
    structure ? structure.toLowerCase() : null,
  ].filter((item): item is string => item !== null);
  sentences.push(view.length
    ? sentence(`On ${ticker}, I see ${view.join(" with ")}`)
    : `I’m monitoring ${ticker}.`);

  const context: string[] = [];
  if (price !== null && vwap !== null) {
    context.push(`price is ${price === vwap ? "at" : price > vwap ? "above" : "below"} VWAP`);
  }
  if (liquidity) context.push(`liquidity is ${liquidity}`);
  if (orderFlow) context.push(`order flow is ${orderFlow}`);
  if (volatility) context.push(`volatility is ${volatility}`);
  if (context.length) sentences.push(sentence(`${context.slice(0, 3).join("; ")}`));

  const edgeText = edge === null ? "" : ` at an edge score of ${Math.round(edge)}/110`;
  sentences.push(reason
    ? sentence(`The verdict is ${verdict}${edgeText}: ${reason}`)
    : sentence(`The verdict is ${verdict}${edgeText}`));

  if (missing.length && !sameText(missing.join(", "), reason)) {
    sentences.push(sentence(`I’m still waiting on ${missing.join(", ")}`));
  }
  if (nextStep && !sameText(nextStep, reason) && !missing.some((item) => sameText(item, nextStep))) {
    sentences.push(sentence(`Next, I’m watching for ${nextStep}`));
  }

  return {
    status,
    paragraph: sentences.slice(0, 5).join(" "),
  };
}
