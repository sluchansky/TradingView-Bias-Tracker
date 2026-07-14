import type { ConnectionState, DashboardTicker } from "./types";

export type AITimelineCategory =
  | "System"
  | "Monitoring"
  | "Verdict"
  | "Edge"
  | "Structure"
  | "Liquidity"
  | "Order Flow"
  | "VWAP"
  | "Volatility"
  | "Risk"
  | "Trade"
  | "News";

export type AITimelineTone =
  | "blue"
  | "purple"
  | "cyan"
  | "amber"
  | "green"
  | "red"
  | "gray";

export type AITimelineEvent = {
  id: string;
  key: string;
  timestamp: string;
  category: AITimelineCategory;
  message: string;
  tone: AITimelineTone;
  instrument: DashboardTicker;
  state: string;
};

export type AITimelineSnapshot = {
  instrument: DashboardTicker;
  connection: "connected" | "connecting" | "disconnected";
  verdict: string | null;
  edge: number | null;
  structure: string | null;
  liquidity: string | null;
  orderFlow: string | null;
  vwapRelation: "above" | "below" | "at" | null;
  volatility: string | null;
  risk: string | null;
  position: string | null;
  news: string | null;
};

export type TimelineInput = {
  ticker: DashboardTicker;
  connection: ConnectionState;
  data: unknown;
};
