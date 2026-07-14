export type DashboardTicker = "MNQ" | "MGC" | "MES" | "MYM";

export type ConnectionState =
  | "idle"
  | "loading"
  | "connected"
  | "warming"
  | "stale"
  | "error";

export type PricePoint = {
  time: number;
  price: number;
};

export type TradePlan = {
  trade_plan?: boolean;
  direction?: string | null;
  entry_zone?: string | null;
  stop_loss?: string | number | null;
  target1?: string | number | null;
  target2?: string | number | null;
  rr?: string | number | null;
  invalidation?: string | null;
};

export type DashboardStatus = {
  status?: string;
  verdict?: string;
  active_ticker?: string;
  trading_mode?: string;
  execution_mode?: string;
  execution_live?: boolean;
  market_open?: boolean;
  market_status?: string;
  market_reason?: string;
  next_open?: string;
  current_price?: number;
  display_price?: string;
  last_valid_price?: number;
  last_valid_time?: string;
  vwap_value?: number;
  vwap_status?: string;
  nearest_supply?: number;
  nearest_demand?: number;
  bias?: string;
  current_atr?: number;
  edge_score?: number;
  edge_grade?: string;
  edge_breakdown?: Record<string, unknown>;
  strict_direction?: string;
  strict_reason?: string;
  strict_missing?: unknown[];
  stage_next_step?: string;
  stage_invalidation?: string;
  recommendation?: string;
  action?: string;
  warning?: string | null;
  market_structure?: string;
  structure_class?: string;
  structure_detail?: string;
  risk_zone?: string;
  risk_detail?: string;
  session_window?: string;
  session_preferred?: boolean;
  session_day_type?: Record<string, unknown> | string;
  market_narrative?: Record<string, unknown>;
  news_filter?: Record<string, unknown>;
  alert_diagnostics?: Record<string, unknown>;
  gate_debug?: Record<string, unknown>;
  confluences?: Record<string, unknown>;
  trade_plan?: TradePlan;
  main_brain?: Record<string, unknown>;
  main_brain_voice?: Record<string, unknown> | string;
  analyst?: Record<string, unknown>;
  analyst_report?: Record<string, unknown>;
  trade_memory?: Record<string, unknown>;
  main_brain_learning_stats?: Record<string, unknown>;
  session_quality?: Record<string, unknown>;
  equity_curve_today?: Record<string, unknown>;
  data_feed?: Record<string, unknown>;
  brain_state?: Record<string, unknown>;
  _demo?: boolean;
};

export type DashboardMessage = {
  id: number;
  role: "user" | "assistant";
  text: string;
};

export const DASHBOARD_TICKERS: DashboardTicker[] = ["MNQ", "MGC", "MES", "MYM"];

export function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function asNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function asStringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map(asString).filter((item): item is string => item !== null)
    : [];
}

export function formatValue(value: unknown, decimals = 2): string {
  const number = asNumber(value);
  return number === null
    ? "Unavailable"
    : number.toLocaleString("en-US", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      });
}
