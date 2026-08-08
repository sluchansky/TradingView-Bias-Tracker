/**
 * LiveMarketChart — Databento-powered live candlestick chart for Main Brain.
 *
 * Uses lightweight-charts v5 (MIT, purpose-built for OHLCV data).
 * Polls /api/main-brain/chart every 5 seconds with an in-flight guard.
 * Stops polling when collapsed to preserve resources.
 *
 * Placement: below the Market State Strip, above the panel grid.
 * Default state: EXPANDED.
 *
 * Strict scope: display-only. No writes to backend state.
 */
import React, {
  useEffect,
  useRef,
  useState,
  useCallback,
} from "react";
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  createSeriesMarkers,
  CrosshairMode,
  LineStyle,
  IChartApi,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  Time,
  UTCTimestamp,
} from "lightweight-charts";

// ── Types ─────────────────────────────────────────────────────────────────────

interface ChartBar {
  ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
  complete?: boolean;
}

interface ChartVwap {
  value: number;
  ts: string | null;
  source: string;
}

interface StructureEvent {
  ts: number;
  type: string;
  price: number | null;
  source: string;
}

interface ActiveTrade {
  direction?: string;
  entry?: number;
  stop?: number;
  target1?: number;
  target2?: number;
  opened_at?: string;
}

interface LeftBrainMeta {
  last_updated_at?: string;
  direction?: string;
}

interface ChartConnection {
  status: string;
  connected: boolean;
  reconnects: number;
  last_ts: string | null;
  error: string | null;
}

interface FvgZoneOverlay {
  fvg_id:       string;
  direction:    "BULLISH" | "BEARISH";
  lower:        number;
  upper:        number;
  status:       string;
  is_ifvg?:     boolean;
  rank_score?:  number;
}

interface FvgSequenceOverlay {
  fvg_id:        string;
  direction:     "BULLISH" | "BEARISH";
  setup_family:  string;
  current_state: string;
  zone_lower:    number;
  zone_upper:    number;
  is_primary?:   boolean;
  entry_window_label?: string;
}

interface ChartResponse {
  ok: boolean;
  enabled: boolean;
  reason?: string;
  instrument?: string;
  native_contract?: string;
  timeframe?: string;
  generated_at?: string;
  connection?: ChartConnection;
  bars?: ChartBar[];
  bar_count_1m?: number;
  partial_bar?: ChartBar | null;
  vwap?: ChartVwap | null;
  structure_events?: StructureEvent[];
  active_trade?: ActiveTrade | null;
  left_brain?: LeftBrainMeta | null;
  fvg_zones?:      FvgZoneOverlay[];
  fvg_sequences?:  FvgSequenceOverlay[];
  telemetry?: Record<string, unknown>;
}

// ── Theme ─────────────────────────────────────────────────────────────────────

const T = {
  bg:       "#09090b",
  surface:  "#111113",
  border:   "#27272a",
  txt:      "#f4f4f5",
  txtMuted: "#71717a",
  cyan:     "#06b6d4",
  green:    "#22c55e",
  red:      "#ef4444",
  amber:    "#f59e0b",
  purple:   "#a78bfa",
  mono:     "'JetBrains Mono', 'Fira Code', monospace",
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function ageStr(isoStr: string | null | undefined): string {
  if (!isoStr) return "—";
  try {
    const diffMs = Date.now() - new Date(isoStr).getTime();
    const s = Math.floor(diffMs / 1000);
    if (s < 60)   return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    return `${Math.floor(s / 3600)}h ago`;
  } catch {
    return "—";
  }
}

function statusColor(status: string): string {
  switch (status) {
    case "LIVE":          return T.green;
    case "DELAYED":       return T.amber;
    case "STALE":         return "#f97316";
    case "RECONNECTING":  return T.cyan;
    case "DISCONNECTED":  return T.red;
    case "NO DATA":       return T.txtMuted;
    case "MARKET CLOSED": return T.purple;
    default:              return T.txtMuted;
  }
}

function barToCandle(b: ChartBar) {
  return {
    time:  b.ts as UTCTimestamp,
    open:  b.open,
    high:  b.high,
    low:   b.low,
    close: b.close,
  };
}

// ── Status strip ──────────────────────────────────────────────────────────────

function StatusStrip({
  data,
  sseConnected,
  sseAuthFailed,
}: {
  data: ChartResponse | null;
  sseConnected: boolean;
  sseAuthFailed: boolean;
}) {
  if (!data) {
    return (
      <div style={{ padding: "4px 10px", fontSize: 10, color: T.txtMuted, fontFamily: T.mono }}>
        Loading…
      </div>
    );
  }

  const conn    = data.connection;
  const status  = conn?.status ?? (data.enabled === false ? "DISCONNECTED" : "NO DATA");
  const bars    = data.bars ?? [];
  const partial = data.partial_bar;
  const lb      = data.left_brain;

  return (
    <div style={{
      display: "flex", flexWrap: "wrap", gap: "6px 16px",
      padding: "4px 10px",
      fontSize: 10, fontFamily: T.mono, color: T.txtMuted,
      borderBottom: `1px solid ${T.border}`,
    }}>
      <span>
        <span style={{ color: statusColor(status), fontWeight: 700 }}>{status}</span>
        {(conn?.reconnects ?? 0) > 0 && (
          <span style={{ color: T.amber }}> ×{conn!.reconnects}</span>
        )}
      </span>
      <span style={{
        color:      sseAuthFailed ? T.amber : (sseConnected ? T.green : T.txtMuted),
        fontWeight: (sseConnected || sseAuthFailed) ? 700 : 400,
      }}>
        {sseAuthFailed ? "⚠ AUTH REQUIRED" : (sseConnected ? "● TICK LIVE" : "○ TICK OFF")}
      </span>
      {data.instrument && (
        <span>{data.instrument}
          {data.native_contract && (
            <span style={{ color: T.cyan }}> [{data.native_contract}]</span>
          )}
        </span>
      )}
      {data.timeframe && <span style={{ color: T.purple }}>{data.timeframe}</span>}
      <span>Event: <span style={{ color: T.txt }}>{ageStr(conn?.last_ts)}</span></span>
      {bars.length > 0 && (
        <span>Bar: <span style={{ color: T.txt }}>
          {new Date(bars[bars.length - 1].ts * 1000).toLocaleTimeString([], {
            hour: "2-digit", minute: "2-digit",
          })}
        </span></span>
      )}
      <span>Bars: <span style={{ color: T.txt }}>{data.bar_count_1m ?? bars.length}</span></span>
      <span>Partial: <span style={{ color: partial ? T.amber : T.txtMuted }}>
        {partial ? "YES" : "none"}
      </span></span>
      {lb?.last_updated_at && (
        <span>LB: <span style={{ color: T.cyan }}>{ageStr(lb.last_updated_at)}</span>
          {lb.direction && (
            <span style={{
              color: lb.direction === "BULLISH" ? T.green
                : lb.direction === "BEARISH" ? T.red : T.txtMuted,
            }}> {lb.direction}</span>
          )}
        </span>
      )}
      {conn?.error && (
        <span style={{ color: T.red }}>Err: {String(conn.error).slice(0, 40)}</span>
      )}
    </div>
  );
}

// ── Overlay toggle ────────────────────────────────────────────────────────────

function OverlayToggle({ label, active, onClick }: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button onClick={onClick} style={{
      fontSize: 10, fontFamily: T.mono,
      padding: "2px 8px", borderRadius: 3,
      border: `1px solid ${active ? T.cyan : T.border}`,
      background: active ? `${T.cyan}20` : "transparent",
      color: active ? T.cyan : T.txtMuted,
      cursor: "pointer", userSelect: "none",
    }}>
      {label}
    </button>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

const POLL_MS     = 5_000;
const CHART_H     = 380;
const INSTRUMENTS = ["MGC", "MNQ", "MES", "MYM"] as const;
const TIMEFRAMES  = ["1m", "5m", "15m"] as const;

export interface LiveMarketChartProps {
  ticker: string;
  onInstrumentChange?: (inst: string) => void;
  authHeader?: string;
  /** When true the chart renders at 450 px instead of the default 380 px.
   *  Used by the Trade Desk section where the chart is the centrepiece. */
  tall?: boolean;
}

export const LiveMarketChart: React.FC<LiveMarketChartProps> = ({
  ticker,
  onInstrumentChange,
  authHeader,
  tall = false,
}) => {
  const [collapsed,     setCollapsed]     = useState(false);
  const [instrument,    setInstrument]    = useState(ticker || "MGC");
  const [timeframe,     setTimeframe]     = useState("1m");
  const [data,          setData]          = useState<ChartResponse | null>(null);
  const [showVwap,      setShowVwap]      = useState(true);
  const [showTrade,     setShowTrade]     = useState(true);
  const [showStructure, setShowStructure] = useState(true);
  const [showFvg,       setShowFvg]       = useState(false);
  const [sseConnected,  setSseConnected]  = useState(false);

  // Derived chart height — tall mode used by Trade Desk section
  const chartH = tall ? 450 : CHART_H;

  // Sync instrument with parent ticker
  useEffect(() => {
    if (ticker && ticker !== instrument) setInstrument(ticker);
  }, [ticker]); // eslint-disable-line react-hooks/exhaustive-deps

  const containerRef = useRef<HTMLDivElement>(null);
  const wrapRef      = useRef<HTMLDivElement>(null);

  // Stable refs to chart / series (created once per mount cycle)
  const chartRef        = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const vwapSeriesRef   = useRef<ISeriesApi<"Line"> | null>(null);
  const volSeriesRef    = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markersApiRef   = useRef<ISeriesMarkersPluginApi<Time> | null>(null);

  // Price lines for active trade levels and FVG zone overlay
  type PriceLine = ReturnType<ISeriesApi<"Candlestick">["createPriceLine"]>;
  const tradeLinesRef   = useRef<PriceLine[]>([]);
  const fvgLinesRef     = useRef<PriceLine[]>([]);

  const lastBarsRef     = useRef<ChartBar[]>([]);
  const inFlightRef     = useRef(false);
  const [fullscreen,    setFullscreen] = useState(false);

  // Tick-level partial bar — updated on every SSE tick so the current bar
  // moves in real time.  Reset on instrument change and synced from the poll
  // response so we always have a correct open/high/low baseline.
  const partialBarRef   = useRef<{ts:number,open:number,high:number,low:number,close:number,volume:number} | null>(null);

  // Tracks the last display context so the chart can be immediately populated
  // after it is recreated (e.g. when chartH or collapsed changes).
  const latestDisplayRef = useRef<{
    data: ChartResponse | null;
    showVwap: boolean;
    showStructure: boolean;
    showTrade: boolean;
    showFvg: boolean;
  }>({ data: null, showVwap: true, showStructure: true, showTrade: true, showFvg: false });
  // True while an SSE connection is alive — suppresses the poll-driven partial
  // bar update (SSE has already rendered it tick-by-tick).
  const sseActiveRef    = useRef(false);
  // Generation counter — incremented on every instrument/collapsed change.
  // Stale events from a closing stream are ignored when gen !== generationRef.current.
  const generationRef   = useRef(0);
  // Auth-failure latch — set when the token endpoint returns 401/403;
  // prevents infinite reconnect on authentication failure.
  const [sseAuthFailed, setSseAuthFailed] = useState(false);

  // ── Fetch ─────────────────────────────────────────────────────────────────
  const fetchData = useCallback(async () => {
    if (inFlightRef.current || collapsed) return;
    inFlightRef.current = true;
    try {
      const limit = timeframe === "1m" ? 300 : timeframe === "5m" ? 120 : 60;
      const url   = `/api/main-brain/chart?instrument=${instrument}&timeframe=${timeframe}&limit=${limit}`;
      const headers: Record<string, string> = {};
      if (authHeader) headers["Authorization"] = authHeader;
      const res  = await fetch(url, { headers });
      if (!res.ok) return;
      const json = (await res.json()) as ChartResponse;
      setData(json);
    } catch {
      // Silent — show stale data
    } finally {
      inFlightRef.current = false;
    }
  }, [instrument, timeframe, collapsed, authHeader]);

  // Trigger fetch on instrument/timeframe change
  useEffect(() => { void fetchData(); }, [fetchData]);

  // Polling loop — stops when collapsed
  useEffect(() => {
    if (collapsed) return;
    const id = setInterval(() => { void fetchData(); }, POLL_MS);
    return () => clearInterval(id);
  }, [fetchData, collapsed]);

  // ── SSE tick subscription ─────────────────────────────────────────────────
  // Secure token-based EventSource flow:
  //   1. POST /api/main-brain/tick-stream-token (authenticated with authHeader)
  //      → short-lived 45-second token.
  //   2. Open EventSource /api/main-brain/tick-stream?inst=…&token=<tok>.
  //      Flask validates the token; anonymous connections are rejected with 401.
  //   3. Handle typed SSE events: "tick", "heartbeat", "status".
  //   4. On disconnect: close, acquire a fresh token, reconnect with backoff.
  //   5. Stop reconnecting on 401/403 from the token endpoint (show AUTH REQUIRED).
  //
  // generationRef guards against stale ticks from a closing stream arriving after
  // an instrument switch — each effect run increments the generation counter and
  // tick handlers check it before mutating chart state.
  useEffect(() => {
    if (collapsed) return;

    // Capture this effect's generation; stale events from previous runs are ignored.
    const gen = ++generationRef.current;

    let es: EventSource | null = null;
    let timer:    ReturnType<typeof setTimeout> | null = null;
    let watchdog: ReturnType<typeof setTimeout> | null = null;
    // Initial delay 5 s — long enough to avoid the eviction death-spiral where
    // a rapid reconnect immediately displaces its predecessor, which fires
    // onerror, which reconnects again in a tight loop.  Cap at 15 s.
    let delay = 5_000;
    const MAX_DELAY = 15_000;
    // Watchdog: if no tick OR heartbeat arrives within 25 s (server sends
    // heartbeats every 15 s), the proxy has silently dropped the connection.
    // Proactively close and reconnect — onerror alone is unreliable for proxied
    // long-lived SSE connections.
    const WATCHDOG_MS = 25_000;
    let stopped = false;

    const clearWatchdog = () => { if (watchdog) { clearTimeout(watchdog); watchdog = null; } };
    const resetWatchdog = () => {
      clearWatchdog();
      if (stopped || gen !== generationRef.current) return;
      watchdog = setTimeout(() => {
        if (stopped || gen !== generationRef.current) return;
        // Stale connection — close cleanly and reconnect immediately (reset delay;
        // this is not a repeated failure, just a silent proxy drop).
        es?.close(); es = null;
        sseActiveRef.current = false;
        setSseConnected(false);
        delay = 2_000;
        void connect();
      }, WATCHDOG_MS);
    };

    const connect = async () => {
      if (stopped || gen !== generationRef.current) return;

      // Step 1 — obtain a fresh short-lived token via authenticated POST
      let token: string;
      try {
        const headers: Record<string, string> = { 'Content-Type': 'application/json' };
        if (authHeader) headers['Authorization'] = authHeader;
        const resp = await fetch(
          `/api/main-brain/tick-stream-token?inst=${instrument}`,
          { method: 'POST', headers },
        );
        if (resp.status === 401 || resp.status === 403) {
          // Auth failure — stop retrying; show AUTH REQUIRED in the status strip
          if (!stopped && gen === generationRef.current) {
            setSseAuthFailed(true);
            sseActiveRef.current = false;
            setSseConnected(false);
          }
          return;
        }
        if (!resp.ok) throw new Error(`token-fetch ${resp.status}`);
        const body = await resp.json() as { token: string };
        token = body.token;
      } catch {
        // Network / parse error — retry with backoff
        if (!stopped && gen === generationRef.current) {
          timer = setTimeout(() => { delay = Math.min(delay * 2, MAX_DELAY); void connect(); }, delay);
        }
        return;
      }

      if (stopped || gen !== generationRef.current) return;

      // Step 2 — open EventSource with token in query string
      es = new EventSource(
        `/api/main-brain/tick-stream?inst=${instrument}&token=${encodeURIComponent(token)}`,
      );

      es.onopen = () => {
        if (gen !== generationRef.current) { es?.close(); return; }
        sseActiveRef.current = true;
        setSseConnected(true);
        setSseAuthFailed(false);
        delay = 2_000;
        resetWatchdog(); // arm the watchdog once the stream is open
      };

      // Typed "tick" events — use server-authoritative partial_bar when present
      es.addEventListener('tick', (rawEv: Event) => {
        if (gen !== generationRef.current) return;
        resetWatchdog(); // live tick = connection confirmed
        try {
          const ev = rawEv as MessageEvent;
          const tick = JSON.parse(ev.data) as {
            ts_s: number;
            price: number;
            size: number;
            side: string;
            partial_bar?: {
              open: number; high: number; low: number; close: number;
              volume: number; complete: boolean;
            };
          };
          const barTs = Math.floor(tick.ts_s / 60) * 60;

          if (tick.partial_bar) {
            // Use server-authoritative snapshot — already aggregated by the backend
            const pb = tick.partial_bar;
            partialBarRef.current = {
              ts: barTs, open: pb.open, high: pb.high,
              low: pb.low, close: pb.close, volume: pb.volume ?? 0,
            };
          } else {
            // Fallback: build client-side aggregate (no partial_bar field)
            const pb = partialBarRef.current;
            if (!pb || pb.ts !== barTs) {
              partialBarRef.current = {
                ts: barTs, open: tick.price, high: tick.price,
                low: tick.price, close: tick.price, volume: tick.size ?? 0,
              };
            } else {
              if (tick.price > pb.high) pb.high = tick.price;
              if (tick.price < pb.low)  pb.low  = tick.price;
              pb.close  = tick.price;
              pb.volume = (pb.volume ?? 0) + (tick.size ?? 0);
            }
          }

          // Push the updated bar to the chart (replaces current-minute candle)
          const b = partialBarRef.current!;
          candleSeriesRef.current?.update({
            time: b.ts as UTCTimestamp,
            open: b.open, high: b.high, low: b.low, close: b.close,
          });
          volSeriesRef.current?.update({
            time:  b.ts as UTCTimestamp,
            value: b.volume ?? 0,
            color: b.close >= b.open ? `${T.green}60` : `${T.red}60`,
          });
        } catch { /* ignore parse errors */ }
      });

      // "status" event — feed-level connection confirmation
      es.addEventListener('status', () => { resetWatchdog(); });

      // "heartbeat" event — keep-alive ping from server every 15 s.
      // Reset the watchdog so we only force-reconnect when genuinely silent.
      es.addEventListener('heartbeat', () => { resetWatchdog(); });

      es.onerror = () => {
        clearWatchdog();
        sseActiveRef.current = false;
        setSseConnected(false);
        es?.close();
        es = null;
        if (!stopped && gen === generationRef.current) {
          timer = setTimeout(() => {
            delay = Math.min(delay * 2, MAX_DELAY);
            void connect(); // fresh token on each reconnect attempt
          }, delay);
        }
      };
    };

    void connect();

    return () => {
      stopped = true;
      generationRef.current++; // invalidate any in-flight tick events immediately
      sseActiveRef.current = false;
      setSseConnected(false);
      es?.close();
      if (timer)    clearTimeout(timer);
      if (watchdog) clearTimeout(watchdog);
    };
  }, [instrument, collapsed, authHeader]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Apply data to chart ───────────────────────────────────────────────────
  // Declared BEFORE the createChart effect so it can be called immediately
  // after building a fresh chart — prevents "blank chart until next poll"
  // when chartH or collapsed changes and the chart is recreated.
  const applyDataToChart = useCallback(() => {
    const { data: d, showVwap: sv, showStructure: ss, showTrade: st } = latestDisplayRef.current;
    if (!d || !candleSeriesRef.current || !chartRef.current) return;

    const bars    = d.bars ?? [];
    const partial = d.partial_bar ?? null;
    const prev    = lastBarsRef.current;

    // Sync partialBarRef from poll data so SSE ticks always have the correct
    // open/high/low baseline, even on first connect or after an instrument switch.
    if (partial && (!partialBarRef.current || partialBarRef.current.ts !== partial.ts)) {
      partialBarRef.current = {
        ts:     partial.ts,
        open:   partial.open,
        high:   partial.high,
        low:    partial.low,
        close:  partial.close,
        volume: partial.volume ?? 0,
      };
    }

    // When SSE is live, skip the poll-driven partial bar update — the SSE
    // handler is already keeping it current tick-by-tick.
    const pollPartial = sseActiveRef.current ? null : partial;

    // Build full candle list
    const allCandles = [...bars, ...(pollPartial ? [pollPartial] : [])].map(barToCandle);
    if (allCandles.length === 0) return;

    const needsReset =
      prev.length === 0 ||
      bars.length < prev.length ||
      (bars.length > 0 && prev.length > 0 && bars[0].ts !== prev[0].ts);

    try {
      if (needsReset) {
        candleSeriesRef.current.setData(allCandles);
        if (prev.length === 0) chartRef.current.timeScale().scrollToRealTime();
      } else {
        // Incremental: update last 2 candles (latest complete + partial)
        const from = Math.max(0, allCandles.length - 2);
        for (let i = from; i < allCandles.length; i++) {
          candleSeriesRef.current.update(allCandles[i]);
        }
      }
    } catch { /* chart may have been destroyed mid-render — ignore */ }
    lastBarsRef.current = bars;

    // Volume histogram
    if (volSeriesRef.current) {
      const all = [...bars, ...(partial ? [partial] : [])];
      try {
        volSeriesRef.current.setData(
          all.map((b) => ({
            time:  b.ts as UTCTimestamp,
            value: b.volume ?? 0,
            color: b.close >= b.open ? `${T.green}60` : `${T.red}60`,
          })),
        );
      } catch { /* ignore */ }
    }

    // VWAP overlay — single horizontal line at current session value
    if (vwapSeriesRef.current) {
      try {
        if (sv && d.vwap?.value) {
          const v = d.vwap.value;
          const all = [...bars, ...(partial ? [partial] : [])];
          vwapSeriesRef.current.setData(
            all.map((b) => ({ time: b.ts as UTCTimestamp, value: v })),
          );
        } else {
          vwapSeriesRef.current.setData([]);
        }
      } catch { /* ignore */ }
    }

    // Structure event markers
    if (markersApiRef.current) {
      try {
        if (ss) {
          const markers = (d.structure_events ?? [])
            .filter(ev => ev.price != null)
            .map(ev => {
              const isBull  = /demand|BOS|CHOCH|BULLISH|HH|HL/i.test(ev.type);
              const isSweep = /sweep/i.test(ev.type);
              const color   = isSweep ? T.purple : isBull ? T.green : T.red;
              const pos     = isBull ? "belowBar" : "aboveBar";
              const shape   = isBull ? "arrowUp"  : "arrowDown";
              return {
                time:     ev.ts as UTCTimestamp,
                position: pos  as "belowBar" | "aboveBar",
                color,
                shape:    shape as "arrowUp" | "arrowDown",
                text:     ev.type.replace(/^(MGC|MNQ|MES|MYM) /i, "").slice(0, 14),
                size:     1,
              };
            })
            .sort((a, b) => (a.time as number) - (b.time as number));
          markersApiRef.current.setMarkers(markers);
        } else {
          markersApiRef.current.setMarkers([]);
        }
      } catch { /* ignore */ }
    }

    // Active trade price lines
    if (candleSeriesRef.current) {
      for (const pl of tradeLinesRef.current) {
        try { candleSeriesRef.current.removePriceLine(pl); } catch { /* gone */ }
      }
      tradeLinesRef.current = [];

      if (st && d.active_trade) {
        const at = d.active_trade;
        const addLine = (
          price: number | undefined,
          color: string,
          title: string,
        ) => {
          if (price == null || !isFinite(price)) return;
          try {
            const pl = candleSeriesRef.current!.createPriceLine({
              price, color, lineWidth: 1,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: true,
              title,
            });
            tradeLinesRef.current.push(pl);
          } catch { /* ignore */ }
        };
        addLine(at.entry,   T.cyan,  "Entry");
        addLine(at.stop,    T.red,   "Stop");
        addLine(at.target1, T.green, "TP1");
        if (at.target2 != null) addLine(at.target2, "#86efac", "TP2");
      }
    }

    // ── FVG / IFVG zone price-band overlay ──────────────────────────────────
    // Shadow-only annotation — never affects gate or execution.
    // Draws dashed horizontal price lines for upper and lower bounds of each
    // active FVG/IFVG zone.  Only rendered when the FVG toggle is ON.
    if (candleSeriesRef.current) {
      // Always clear previous FVG lines first
      for (const pl of fvgLinesRef.current) {
        try { candleSeriesRef.current.removePriceLine(pl); } catch { /* gone */ }
      }
      fvgLinesRef.current = [];

      const { showFvg: sf } = latestDisplayRef.current;
      if (sf) {
        const zones     = d.fvg_zones     ?? [];
        const seqZones  = d.fvg_sequences ?? [];

        // Build a set of fvg_ids that have a sequence in SHADOW_READY
        const shadowReady = new Set(
          seqZones.filter(s => s.current_state === "SHADOW_READY").map(s => s.fvg_id),
        );

        const addFvgLine = (
          price: number,
          color: string,
          title: string,
          style: number,
        ) => {
          if (!isFinite(price)) return;
          try {
            const pl = candleSeriesRef.current!.createPriceLine({
              price,
              color,
              lineWidth: 1,
              lineStyle: style,
              axisLabelVisible: false,
              title,
            });
            fvgLinesRef.current.push(pl);
          } catch { /* ignore */ }
        };

        for (const z of zones) {
          const isBull   = z.direction === "BULLISH";
          const isIfvg   = !!z.is_ifvg;
          const isReady  = shadowReady.has(z.fvg_id);
          // Color: shadow-ready → green/red vivid; IFVG → pink; normal → indigo/rose
          const baseColor = isReady
            ? (isBull ? "#22c55e" : "#ef4444")
            : isIfvg
            ? "#ec4899"
            : (isBull ? "#6366f1" : "#f43f5e");
          // Opacity suffix in hex: ready=cc (~80%), others=66 (~40%)
          const opacity   = isReady ? "cc" : "66";
          const color     = `${baseColor}${opacity}`;
          // Line style: ready → solid (0), ifvg → large-dashed (2), normal → dashed (1)
          const style = isReady ? 0 : isIfvg ? 2 : 1;
          const prefix = isReady ? "★" : isIfvg ? "⟳" : "";
          addFvgLine(z.upper, color, `${prefix}${z.fvg_id.slice(-4)}U`, style);
          addFvgLine(z.lower, color, `${prefix}${z.fvg_id.slice(-4)}L`, style);
        }
      }
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Create / destroy chart ────────────────────────────────────────────────
  useEffect(() => {
    if (collapsed || !containerRef.current) return;

    const chart = createChart(containerRef.current, {
      width:  containerRef.current.clientWidth,
      height: chartH,
      layout: {
        background: { color: T.surface },
        textColor:  T.txtMuted,
        fontSize:   10,
      },
      grid: {
        vertLines: { color: "#1c1c1e" },
        horzLines: { color: "#1c1c1e" },
      },
      crosshair:        { mode: CrosshairMode.Normal },
      rightPriceScale:  { borderColor: T.border },
      timeScale: {
        borderColor:    T.border,
        timeVisible:    true,
        secondsVisible: false,
      },
    });

    const candle = chart.addSeries(CandlestickSeries, {
      upColor:         T.green,
      downColor:       T.red,
      borderUpColor:   T.green,
      borderDownColor: T.red,
      wickUpColor:     T.green,
      wickDownColor:   T.red,
    });

    const vwapLine = chart.addSeries(LineSeries, {
      color:    T.cyan,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title:    "VWAP",
      priceLineVisible: false,
    });

    const vol = chart.addSeries(HistogramSeries, {
      color:       `${T.cyan}40`,
      priceFormat: { type: "volume" } as const,
      priceScaleId: "volume",
    });
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    });

    // Attach markers plugin to the candlestick series
    const markersApi = createSeriesMarkers(candle);

    chartRef.current        = chart;
    candleSeriesRef.current = candle;
    vwapSeriesRef.current   = vwapLine;
    volSeriesRef.current    = vol;
    markersApiRef.current   = markersApi;

    // Apply any data we already have so the chart is never blank after
    // recreation (e.g. navigating to Trade Desk changes chartH, which
    // destroys and recreates the chart — without this the canvas stays
    // empty until the next 5-second poll).
    applyDataToChart();

    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.resize(containerRef.current.clientWidth, chartH);
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      try { chart.remove(); } catch { /* container may already be detached */ }
      chartRef.current        = null;
      candleSeriesRef.current = null;
      vwapSeriesRef.current   = null;
      volSeriesRef.current    = null;
      markersApiRef.current   = null;
      tradeLinesRef.current   = [];
      fvgLinesRef.current     = [];
      lastBarsRef.current     = [];
    };
  }, [collapsed, chartH, applyDataToChart]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Sync display state + re-apply data on change ──────────────────────────
  useEffect(() => {
    // Keep the ref current so applyDataToChart (called from createChart effect)
    // always has the latest values without needing to be re-created.
    latestDisplayRef.current = { data, showVwap, showStructure, showTrade, showFvg };
    applyDataToChart();
  }, [data, showVwap, showStructure, showTrade, showFvg, applyDataToChart]);

  // Clear stale chart data whenever the instrument changes so applyDataToChart
  // never renders the previous instrument's bars on top of the new feed.
  // Without this, applyDataToChart fires with old data + empty lastBarsRef →
  // repopulates lastBarsRef with wrong-instrument bars → when fresh data
  // arrives the needsReset check sees matching first-bar timestamps and only
  // does an incremental update, leaving the majority of old bars on screen.
  useEffect(() => {
    setData(null);
    lastBarsRef.current   = [];
    partialBarRef.current = null;
  }, [instrument]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Instrument / timeframe change handlers ────────────────────────────────
  const handleInstrument = (inst: string) => {
    setInstrument(inst);
    if (onInstrumentChange) onInstrumentChange(inst);
  };

  const handleTimeframe = (tf: string) => {
    setTimeframe(tf);
    lastBarsRef.current = [];
  };

  const jumpToLive = () => chartRef.current?.timeScale().scrollToRealTime();

  const toggleFullscreen = () => {
    if (!wrapRef.current) return;
    if (!fullscreen) {
      wrapRef.current.requestFullscreen?.().catch(() => {});
    } else {
      document.exitFullscreen?.();
    }
    setFullscreen(f => !f);
  };

  // ── Derived display state ─────────────────────────────────────────────────
  const conn       = data?.connection;
  const status     = conn?.status ?? (data?.enabled === false ? "DISCONNECTED" : "—");
  const isDisabled = data?.enabled === false;
  const at         = data?.active_trade;

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div ref={wrapRef} style={{
      background: T.surface,
      border: `1px solid ${T.border}`,
      borderRadius: 8,
      overflow: "hidden",
      marginBottom: 12,
    }}>
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "6px 12px",
        borderBottom: collapsed ? "none" : `1px solid ${T.border}`,
      }}>
        <span
          onClick={() => setCollapsed(c => !c)}
          style={{
            fontSize: 10, fontWeight: 700, letterSpacing: "0.1em",
            color: T.cyan, cursor: "pointer", userSelect: "none",
          }}
        >
          {collapsed ? "▶" : "▼"} DATABENTO LIVE MARKET
        </span>

        {!collapsed && (
          <span style={{
            fontSize: 9, fontWeight: 700, fontFamily: T.mono,
            color: statusColor(status),
            padding: "1px 6px", borderRadius: 3,
            border:     `1px solid ${statusColor(status)}40`,
            background: `${statusColor(status)}10`,
          }}>
            {status}
          </span>
        )}

        <div style={{ flex: 1 }} />

        {!collapsed && (
          <>
            {/* Instrument selector */}
            <div style={{ display: "flex", gap: 3 }}>
              {INSTRUMENTS.map(inst => (
                <button key={inst} onClick={() => handleInstrument(inst)} style={{
                  fontSize: 10, padding: "2px 7px", borderRadius: 3, cursor: "pointer",
                  border:     `1px solid ${instrument === inst ? T.cyan : T.border}`,
                  background: instrument === inst ? `${T.cyan}20` : "transparent",
                  color:      instrument === inst ? T.cyan : T.txtMuted,
                  fontWeight: instrument === inst ? 700 : 400,
                }}>
                  {inst}
                </button>
              ))}
            </div>

            {/* Timeframe selector */}
            <div style={{ display: "flex", gap: 3 }}>
              {TIMEFRAMES.map(tf => (
                <button key={tf} onClick={() => handleTimeframe(tf)} style={{
                  fontSize: 10, padding: "2px 7px", borderRadius: 3, cursor: "pointer",
                  border:     `1px solid ${timeframe === tf ? T.purple : T.border}`,
                  background: timeframe === tf ? `${T.purple}20` : "transparent",
                  color:      timeframe === tf ? T.purple : T.txtMuted,
                  fontWeight: timeframe === tf ? 700 : 400,
                }}>
                  {tf}
                </button>
              ))}
            </div>

            {/* Overlay toggles */}
            <div style={{ display: "flex", gap: 4 }}>
              <OverlayToggle label="VWAP"      active={showVwap}      onClick={() => setShowVwap(v => !v)} />
              <OverlayToggle label="TRADE"     active={showTrade}     onClick={() => setShowTrade(v => !v)} />
              <OverlayToggle label="STRUCTURE" active={showStructure} onClick={() => setShowStructure(v => !v)} />
              <OverlayToggle label="FVG"       active={showFvg}       onClick={() => setShowFvg(v => !v)} />
            </div>

            {/* Actions */}
            <button onClick={jumpToLive} title="Jump to latest bar" style={{
              fontSize: 9, padding: "2px 6px", borderRadius: 3, cursor: "pointer",
              border: `1px solid ${T.border}`, background: "transparent", color: T.txtMuted,
            }}>⟶ LIVE</button>

            <button onClick={toggleFullscreen} title="Toggle fullscreen" style={{
              fontSize: 10, padding: "2px 6px", borderRadius: 3, cursor: "pointer",
              border: `1px solid ${T.border}`, background: "transparent", color: T.txtMuted,
            }}>⛶</button>
          </>
        )}
      </div>

      {/* Body */}
      {!collapsed && (
        <>
          <StatusStrip data={data} sseConnected={sseConnected} sseAuthFailed={sseAuthFailed} />

          {/* The chart container is always rendered so containerRef stays
              stable. Swapping it in/out (via isDisabled) was unmounting the
              DOM node, orphaning the lightweight-charts instance and causing
              a permanent blank canvas when the feed re-enabled.
              The disabled overlay is positioned on top instead. */}
          <div style={{ position: "relative" }}>
            <div
              ref={containerRef}
              style={{
                width: "100%",
                height: chartH,
                // Hide the canvas visually when disabled, but keep it in the
                // layout so ResizeObserver keeps firing and clientWidth stays
                // valid for chart.resize() calls.
                visibility: isDisabled ? "hidden" : "visible",
                pointerEvents: isDisabled ? "none" : "auto",
              }}
            />
            {isDisabled && (
              <div style={{
                position: "absolute", inset: 0,
                display: "flex", alignItems: "center",
                justifyContent: "center", flexDirection: "column", gap: 8,
              }}>
                <span style={{ fontSize: 13, color: T.txtMuted }}>DATABENTO FEED DISABLED</span>
                <span style={{ fontSize: 10, color: T.txtMuted }}>
                  Set{" "}
                  <code style={{ fontFamily: T.mono, color: T.cyan }}>DATABENTO_ENABLED=1</code>
                  {" "}to enable live data
                </span>
              </div>
            )}
          </div>

          {/* Active trade footer */}
          {!isDisabled && at && showTrade && (
            <div style={{
              display: "flex", gap: 12, padding: "4px 12px",
              borderTop: `1px solid ${T.border}`,
              fontSize: 10, fontFamily: T.mono,
            }}>
              <span style={{ color: T.txtMuted }}>Active trade:</span>
              <span style={{ color: T.txt }}>{(at.direction ?? "").toUpperCase()}</span>
              {at.entry   != null && <span>Entry <span style={{ color: T.cyan  }}>{at.entry}</span></span>}
              {at.stop    != null && <span>Stop <span style={{ color: T.red   }}>{at.stop}</span></span>}
              {at.target1 != null && <span>TP1 <span style={{ color: T.green }}>{at.target1}</span></span>}
              {at.target2 != null && <span>TP2 <span style={{ color: T.green }}>{at.target2}</span></span>}
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default LiveMarketChart;
