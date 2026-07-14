import type { DashboardStatus } from "./types";
import type {
  AITimelineCategory,
  AITimelineEvent,
  AITimelineSnapshot,
  AITimelineTone,
  TimelineInput,
} from "./aiTimelineTypes";

export const TIMELINE_LIMIT = 100;
export const EDGE_TIMELINE_THRESHOLDS = [20, 28, 50] as const;
export const EDGE_MEANINGFUL_DELTA = 12;

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

export function normalizeTimelineState(value: string | null): string {
  return (value ?? "unknown").trim().toLowerCase().replace(/\s+/g, "-");
}

function connectionState(value: TimelineInput["connection"]): AITimelineSnapshot["connection"] {
  if (value === "connected") return "connected";
  if (value === "error") return "disconnected";
  if (value === "stale") return "stale";
  return "connecting";
}

function verdictState(root: Record<string, unknown>, brain: Record<string, unknown>): string | null {
  const raw = (text(root.verdict) ?? text(brain.status))?.toUpperCase();
  if (!raw) return null;
  if (/\b(?:NOT READY|WAIT|NO TRADE|INVALIDATED)\b/.test(raw)) {
    return raw.includes("INVALID") ? "INVALIDATED" : "WAIT";
  }
  if (/\b(?:READY|STRONG TRADE|POSSIBLE TRADE)\b/.test(raw)) {
    const direction = text(root.strict_direction) ?? text(brain.favored_direction) ?? raw;
    if (/short|bear/i.test(direction)) return "READY SHORT";
    if (/long|bull/i.test(direction)) return "READY LONG";
    return "READY";
  }
  return raw;
}

function positionState(root: Record<string, unknown>, brainState: Record<string, unknown>): string {
  const management = record(root.active_trade_mgmt);
  const positions = Array.isArray(management.positions)
    ? management.positions.map(record).filter((position) => Object.keys(position).length > 0)
    : [];
  const execution = record(brainState.execution);
  const openPosition = positions[0] ?? record(execution.open_position);
  if (!Object.keys(openPosition).length) return "flat";
  const direction = text(openPosition.direction) ?? "open";
  const symbol = text(openPosition.symbol) ?? text(root.active_ticker) ?? "";
  return `open:${direction}:${symbol}`;
}

export function createTimelineSnapshot({ ticker, connection, data }: TimelineInput): AITimelineSnapshot {
  const root = record(data as DashboardStatus | null);
  const brain = record(root.main_brain);
  const brainState = record(root.brain_state);
  const marketRead = record(brainState.market_read);
  const diagnostics = record(root.alert_diagnostics);
  const signals = record(brain.signals);
  const liquidityFocus = record(brain.liquidity_focus);
  const safety = record(brainState.safety);
  const news = record(root.news_filter);
  const nextEvent = record(news.next_event);
  const price = number(root.current_price);
  const vwap = number(root.vwap_value);
  const volatility = record(root.volatility);

  return {
    instrument: ticker,
    connection: connectionState(connection),
    verdict: verdictState(root, brain),
    edge: number(brain.edge_score) ?? number(root.edge_score),
    structure: text(root.market_structure) ?? text(marketRead.structure),
    liquidity: text(liquidityFocus.state) ?? text(marketRead.liquidity_state),
    orderFlow: text(diagnostics.cvd_state) ?? text(diagnostics.cvd) ?? text(signals.cvd),
    vwapRelation: price !== null && vwap !== null
      ? price === vwap ? "at" : price > vwap ? "above" : "below"
      : null,
    volatility: text(marketRead.volatility) ?? text(volatility.regime),
    risk: text(safety.risk_status) ?? text(root.risk_zone),
    position: positionState(root, brainState),
    news: text(nextEvent.title) ?? text(nextEvent.name),
  };
}

function event(
  snapshot: AITimelineSnapshot,
  now: Date,
  category: AITimelineCategory,
  state: string,
  message: string,
  tone: AITimelineTone,
): AITimelineEvent {
  const normalizedState = normalizeTimelineState(state);
  const timestamp = now.toISOString();
  const key = `${category}:${snapshot.instrument}:${normalizedState}`;
  return {
    id: `${timestamp}:${key}`,
    key,
    timestamp,
    category,
    message,
    tone,
    instrument: snapshot.instrument,
    state,
  };
}

function crossedThreshold(previous: number, current: number): number | null {
  const crossed = EDGE_TIMELINE_THRESHOLDS.filter((threshold) =>
    (previous < threshold && current >= threshold)
    || (previous >= threshold && current < threshold)
  );
  return crossed.length ? crossed[crossed.length - 1] : null;
}

export function composeTimelineEvents(
  previous: AITimelineSnapshot | null,
  current: AITimelineSnapshot,
  now = new Date(),
): AITimelineEvent[] {
  if (!previous) {
    if (current.connection === "disconnected") {
      return [event(
        current,
        now,
        "System",
        "disconnected",
        "Trading service disconnected. Waiting to resume live analysis.",
        "red",
      )];
    }
    if (current.connection === "connected") {
      return [event(
        current,
        now,
        "Monitoring",
        "live",
        `Monitoring ${current.instrument} with live status data.`,
        "blue",
      )];
    }
    if (current.connection === "stale") {
      return [event(
        current,
        now,
        "System",
        "stale",
        "Live status is stale. Waiting for fresh market data before updating analysis.",
        "amber",
      )];
    }
    return [];
  }

  const events: AITimelineEvent[] = [];
  if (previous.connection !== current.connection) {
    if (current.connection === "disconnected") {
      events.push(event(
        current, now, "System", "disconnected",
        "Trading service disconnected. Waiting to resume live analysis.", "red",
      ));
    } else if (current.connection === "stale") {
      events.push(event(
        current, now, "System", "stale",
        "Live status became stale. Keeping the last snapshot while waiting for fresh data.", "amber",
      ));
    } else if (current.connection === "connected") {
      const fromOffline = previous.connection === "disconnected";
      events.push(event(
        current, now, "System", "connected",
        fromOffline
          ? `Trading service reconnected. Live analysis resumed for ${current.instrument}.`
          : `Fresh status data received. Monitoring ${current.instrument}.`,
        "gray",
      ));
    }
  }

  if (previous.instrument !== current.instrument) {
    events.push(event(
      current, now, "Monitoring", `instrument-${current.instrument}`,
      `Monitoring switched to ${current.instrument}.`, "blue",
    ));
  }

  if (previous.verdict !== current.verdict && current.verdict) {
    const ready = current.verdict.startsWith("READY");
    const invalid = current.verdict === "INVALIDATED";
    events.push(event(
      current, now, "Verdict", current.verdict,
      previous.verdict
        ? `Verdict changed from ${previous.verdict} to ${current.verdict}.`
        : `Current verdict is ${current.verdict}.`,
      invalid ? "red" : ready ? "green" : "amber",
    ));
  }

  if (previous.edge !== null && current.edge !== null && previous.edge !== current.edge) {
    const threshold = crossedThreshold(previous.edge, current.edge);
    const meaningful = Math.abs(current.edge - previous.edge) >= EDGE_MEANINGFUL_DELTA;
    if (threshold !== null || meaningful) {
      const direction = current.edge > previous.edge ? "rose" : "fell";
      const state = threshold !== null
        ? `${direction}-through-${threshold}`
        : `${direction}-band-${Math.floor(current.edge / EDGE_MEANINGFUL_DELTA)}`;
      const detail = threshold !== null ? `, crossing ${threshold}` : "";
      events.push(event(
        current, now, "Edge", state,
        `Edge ${direction} from ${Math.round(previous.edge)} to ${Math.round(current.edge)}${detail}.`,
        "purple",
      ));
    }
  }

  const changed = (
    category: AITimelineCategory,
    previousState: string | null,
    currentState: string | null,
    tone: AITimelineTone,
    label: string,
  ) => {
    if (previousState !== currentState && currentState) {
      events.push(event(
        current, now, category, currentState,
        previousState
          ? `${label} changed from ${previousState} to ${currentState}.`
          : `${label} is ${currentState}.`,
        tone,
      ));
    }
  };

  changed("Structure", previous.structure, current.structure, "cyan", "Structure");
  changed("Liquidity", previous.liquidity, current.liquidity, "cyan", "Liquidity");
  changed("Order Flow", previous.orderFlow, current.orderFlow, "cyan", "Order flow");
  changed("VWAP", previous.vwapRelation, current.vwapRelation, "cyan", "Price relative to VWAP");
  changed("Volatility", previous.volatility, current.volatility, "amber", "Volatility");
  changed(
    "Risk", previous.risk, current.risk,
    /high|invalid|block|risk/i.test(current.risk ?? "") ? "red" : "amber",
    "Risk state",
  );
  changed(
    "Trade", previous.position, current.position,
    current.position === "flat" ? "gray" : "green",
    "Position state",
  );
  changed("News", previous.news, current.news, "blue", "Next market event");

  return events;
}

export function composeInstrumentSelectionEvent(
  previousInstrument: AITimelineSnapshot["instrument"],
  currentInstrument: AITimelineSnapshot["instrument"],
  now = new Date(),
): AITimelineEvent | null {
  if (previousInstrument === currentInstrument) return null;
  const snapshot: AITimelineSnapshot = {
    instrument: currentInstrument,
    connection: "connecting",
    verdict: null,
    edge: null,
    structure: null,
    liquidity: null,
    orderFlow: null,
    vwapRelation: null,
    volatility: null,
    risk: null,
    position: null,
    news: null,
  };
  return event(
    snapshot,
    now,
    "Monitoring",
    `selected-from-${previousInstrument}`,
    `Monitoring switched from ${previousInstrument} to ${currentInstrument}.`,
    "blue",
  );
}

export function composeInstrumentDepartureEvent(
  previousInstrument: AITimelineSnapshot["instrument"],
  currentInstrument: AITimelineSnapshot["instrument"],
  now = new Date(),
): AITimelineEvent | null {
  if (previousInstrument === currentInstrument) return null;
  const snapshot: AITimelineSnapshot = {
    instrument: previousInstrument,
    connection: "connecting",
    verdict: null,
    edge: null,
    structure: null,
    liquidity: null,
    orderFlow: null,
    vwapRelation: null,
    volatility: null,
    risk: null,
    position: null,
    news: null,
  };
  return event(
    snapshot,
    now,
    "Monitoring",
    `left-for-${currentInstrument}`,
    `Monitoring moved from ${previousInstrument} to ${currentInstrument}.`,
    "gray",
  );
}

export function mergeTimelineEvents(
  existing: AITimelineEvent[],
  incoming: AITimelineEvent[],
  now = new Date(),
  limit = TIMELINE_LIMIT,
): AITimelineEvent[] {
  let merged = [...existing];
  for (const candidate of incoming) {
    const latestInCategory = merged.find((item) =>
      item.category === candidate.category && item.instrument === candidate.instrument
    );
    const sameStateContinues = latestInCategory?.key === candidate.key;
    if (sameStateContinues) continue;
    merged = [candidate, ...merged];
  }
  return merged.slice(0, limit);
}
