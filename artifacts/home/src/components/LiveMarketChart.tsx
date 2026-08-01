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

function StatusStrip({ data }: { data: ChartResponse | null }) {
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
}

export const LiveMarketChart: React.FC<LiveMarketChartProps> = ({
  ticker,
  onInstrumentChange,
  authHeader,
}) => {
  const [collapsed,     setCollapsed]     = useState(false);
  const [instrument,    setInstrument]    = useState(ticker || "MGC");
  const [timeframe,     setTimeframe]     = useState("1m");
  const [data,          setData]          = useState<ChartResponse | null>(null);
  const [showVwap,      setShowVwap]      = useState(true);
  const [showTrade,     setShowTrade]     = useState(true);
  const [showStructure, setShowStructure] = useState(true);

  // Sync instrument with parent ticker
  useEffect(() => {
    if (ticker && ticker !== instrument) setInstrument(ticker);
  }, [ticker]); // eslint-disable-line react-hooks/exhaustive-deps

  const containerRef = useRef<HTMLDivElement>(null);
  const wrapRef      = useRef<HTMLDivElement>(null);

  // Stable refs to chart / series (created once per mount cycle)
  const chartRef       = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const vwapSeriesRef  = useRef<ISeriesApi<"Line"> | null>(null);
  const volSeriesRef   = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markersApiRef  = useRef<ISeriesMarkersPluginApi<Time> | null>(null);

  // Price lines for active trade levels
  type PriceLine = ReturnType<ISeriesApi<"Candlestick">["createPriceLine"]>;
  const tradeLinesRef  = useRef<PriceLine[]>([]);

  const lastBarsRef    = useRef<ChartBar[]>([]);
  const inFlightRef    = useRef(false);
  const [fullscreen,   setFullscreen] = useState(false);

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

  // ── Create / destroy chart ────────────────────────────────────────────────
  useEffect(() => {
    if (collapsed || !containerRef.current) return;

    const chart = createChart(containerRef.current, {
      width:  containerRef.current.clientWidth,
      height: CHART_H,
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

    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.resize(containerRef.current.clientWidth, CHART_H);
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current        = null;
      candleSeriesRef.current = null;
      vwapSeriesRef.current   = null;
      volSeriesRef.current    = null;
      markersApiRef.current   = null;
      tradeLinesRef.current   = [];
      lastBarsRef.current     = [];
    };
  }, [collapsed]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Apply data to chart ───────────────────────────────────────────────────
  useEffect(() => {
    if (!data || !candleSeriesRef.current || !chartRef.current) return;

    const bars    = data.bars ?? [];
    const partial = data.partial_bar ?? null;
    const prev    = lastBarsRef.current;

    // Build full candle list
    const allCandles = [...bars, ...(partial ? [partial] : [])].map(barToCandle);
    if (allCandles.length === 0) return;

    const needsReset =
      prev.length === 0 ||
      bars.length < prev.length ||
      (bars.length > 0 && prev.length > 0 && bars[0].ts !== prev[0].ts);

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
    lastBarsRef.current = bars;

    // Volume histogram
    if (volSeriesRef.current) {
      const all = [...bars, ...(partial ? [partial] : [])];
      volSeriesRef.current.setData(
        all.map((b) => ({
          time:  b.ts as UTCTimestamp,
          value: b.volume ?? 0,
          color: b.close >= b.open ? `${T.green}60` : `${T.red}60`,
        })),
      );
    }

    // VWAP overlay — single horizontal line at current session value
    if (vwapSeriesRef.current) {
      if (showVwap && data.vwap?.value) {
        const v = data.vwap.value;
        const all = [...bars, ...(partial ? [partial] : [])];
        vwapSeriesRef.current.setData(
          all.map((b) => ({ time: b.ts as UTCTimestamp, value: v })),
        );
      } else {
        vwapSeriesRef.current.setData([]);
      }
    }

    // Structure event markers
    if (markersApiRef.current) {
      if (showStructure) {
        const markers = (data.structure_events ?? [])
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
    }

    // Active trade price lines
    if (candleSeriesRef.current) {
      for (const pl of tradeLinesRef.current) {
        try { candleSeriesRef.current.removePriceLine(pl); } catch { /* gone */ }
      }
      tradeLinesRef.current = [];

      if (showTrade && data.active_trade) {
        const at = data.active_trade;
        const addLine = (
          price: number | undefined,
          color: string,
          title: string,
        ) => {
          if (price == null || !isFinite(price)) return;
          const pl = candleSeriesRef.current!.createPriceLine({
            price, color, lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title,
          });
          tradeLinesRef.current.push(pl);
        };
        addLine(at.entry,   T.cyan,  "Entry");
        addLine(at.stop,    T.red,   "Stop");
        addLine(at.target1, T.green, "TP1");
        if (at.target2 != null) addLine(at.target2, "#86efac", "TP2");
      }
    }
  }, [data, showVwap, showStructure, showTrade]);

  // ── Instrument / timeframe change handlers ────────────────────────────────
  const handleInstrument = (inst: string) => {
    setInstrument(inst);
    lastBarsRef.current = [];
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
          <StatusStrip data={data} />

          {isDisabled ? (
            <div style={{
              height: CHART_H, display: "flex", alignItems: "center",
              justifyContent: "center", flexDirection: "column", gap: 8,
            }}>
              <span style={{ fontSize: 13, color: T.txtMuted }}>DATABENTO FEED DISABLED</span>
              <span style={{ fontSize: 10, color: T.txtMuted }}>
                Set{" "}
                <code style={{ fontFamily: T.mono, color: T.cyan }}>DATABENTO_ENABLED=1</code>
                {" "}to enable live data
              </span>
            </div>
          ) : (
            <div ref={containerRef} style={{ width: "100%", height: CHART_H }} />
          )}

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
