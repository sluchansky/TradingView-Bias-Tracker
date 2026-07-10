import { useState } from "react";

const MOCK = {
  ticker: "MNQ",
  mode: "SCALP",
  verdict: "WAIT",
  edge: 58,
  reason:
    "No confirmed structure on MNQ. Watching for a BOS above 21,340 to validate the demand setup that formed at the open.",
  nextRequirement: "BOS or CHOCH on the 5m timeframe above the prior high at 21,340",
  invalidation:
    "Price breaks below 21,280 — the demand zone would be considered consumed",
  learning:
    "73% win rate across 19 similar demand-zone SCALP setups this week. Avg win: +1.4R",
  actionLabel: "Enter manually when structure confirms",
  price: "21,318.50",
  change: "+12.25",
  instruments: [
    { name: "MNQ", state: "WAIT", mode: "SCALP", edge: 58 },
    { name: "MGC", state: "WAIT", mode: "SCALP", edge: 41 },
    { name: "MES", state: "READY", mode: "SWING", edge: 76 },
    { name: "MYM", state: "WAIT", mode: "SCALP", edge: 33 },
  ],
  account: {
    balance: "$48,240",
    dailyPnl: "+$380",
    riskUsed: 32,
    maxLoss: "$500",
    remaining: "$380 left",
    trades: 2,
    wins: 2,
  },
  activeTrade: {
    ticker: "MES",
    dir: "Long",
    entry: "5,012.00",
    unrealized: "+0.8R",
  },
  timeline: [
    { time: "09:42", icon: "📍", event: "Demand zone confirmed at 21,290", kind: "setup" },
    { time: "09:38", icon: "🔵", event: "VWAP updated: 21,305.20", kind: "data" },
    { time: "09:31", icon: "⚡", event: "Session open — market scan active", kind: "system" },
    { time: "09:28", icon: "✅", event: "MES LONG entered at 5,012", kind: "trade" },
    { time: "09:15", icon: "🗺️", event: "Pre-market structure mapped", kind: "system" },
    { time: "09:02", icon: "🔵", event: "VWAP seeded: 21,298.40", kind: "data" },
  ],
  diagnostics: [
    { label: "Edge score", value: "58 / 100", sub: "B grade · 8 pts below READY threshold" },
    { label: "BOS / CHOCH", value: "Not detected", sub: "Structure gate: FAIL — blocking trade" },
    { label: "VWAP", value: "21,305.20", sub: "Price below VWAP — bearish lean on 5m" },
    { label: "Zone", value: "Demand 21,290–21,300", sub: "Intact · not yet mitigated" },
    { label: "CVD", value: "Bearish", sub: "Net selling pressure — directional veto active" },
    { label: "Volume", value: "0.9× avg", sub: "Below 1.5× threshold — Volume component: 0 pts" },
  ],
  ticket: {
    entryZone: "21,318–21,325",
    stop: "21,280",
    target1: "21,380",
    rr: "1:1.6",
  },
};

const C = {
  bg: "#07070d",
  surface: "#0c0c18",
  surfaceHigh: "#111120",
  border: "rgba(255,255,255,0.045)",
  borderMid: "rgba(255,255,255,0.07)",
  accent: "#6366f1",
  accentMuted: "rgba(99,102,241,0.12)",
  accentBorder: "rgba(99,102,241,0.28)",
  textPrimary: "#eeeef8",
  textSecondary: "#7070a0",
  textDim: "#32324e",
  textFaint: "#1e1e32",
  green: "#22c55e",
  greenBg: "rgba(34,197,94,0.08)",
  greenBorder: "rgba(34,197,94,0.16)",
  orange: "#f97316",
  indigo: "#a5b4fc",
  teal: "#34d399",
};

function Label({ children }: { children: string }) {
  return (
    <span
      style={{
        fontSize: "10px",
        fontWeight: 700,
        letterSpacing: "1.5px",
        textTransform: "uppercase" as const,
        color: C.textFaint,
      }}
    >
      {children}
    </span>
  );
}

function Divider() {
  return (
    <div style={{ height: "1px", background: C.border, margin: "0" }} />
  );
}

export function CockpitV3() {
  const [activeTicker, setActiveTicker] = useState("MNQ");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [tradeOpen, setTradeOpen] = useState(false);

  const verdict = MOCK.verdict;
  const isReady = verdict.includes("READY");

  const verdictColor = isReady ? C.green : "#a5b4fc";
  const verdictGlow = isReady
    ? "0 0 100px rgba(34,197,94,0.12)"
    : "0 0 100px rgba(165,180,252,0.07)";

  return (
    <div
      style={{
        background: C.bg,
        color: C.textPrimary,
        fontFamily:
          "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        height: "100vh",
        display: "grid",
        gridTemplateColumns: "56px 1fr 228px",
        gridTemplateRows: "1fr 80px",
        overflow: "hidden",
        WebkitFontSmoothing: "antialiased",
      }}
    >
      {/* ═══════════════════════════════════════════════════
          LEFT NAV RAIL
      ═══════════════════════════════════════════════════ */}
      <nav
        style={{
          gridRow: "1 / -1",
          background: C.surface,
          borderRight: `1px solid ${C.border}`,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          paddingTop: "14px",
          paddingBottom: "14px",
          gap: "4px",
        }}
      >
        {/* Logo */}
        <div
          style={{
            width: "32px",
            height: "32px",
            background: "linear-gradient(135deg, #6366f1 0%, #818cf8 100%)",
            borderRadius: "9px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: "15px",
            marginBottom: "18px",
            boxShadow: "0 0 24px rgba(99,102,241,0.35)",
            flexShrink: 0,
          }}
        >
          🤖
        </div>

        {/* Instrument selector tiles */}
        {MOCK.instruments.map((inst) => {
          const active = inst.name === activeTicker;
          const dotColor =
            inst.state === "READY"
              ? C.green
              : inst.state === "WAIT"
              ? "#2a2a44"
              : "#f59e0b";
          return (
            <button
              key={inst.name}
              onClick={() => setActiveTicker(inst.name)}
              style={{
                width: "44px",
                background: active ? C.accentMuted : "transparent",
                border: `1px solid ${active ? C.accentBorder : "transparent"}`,
                borderRadius: "10px",
                padding: "7px 0 6px",
                cursor: "pointer",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: "5px",
                transition: "all 0.15s",
              }}
            >
              <span
                style={{
                  fontSize: "10px",
                  fontWeight: 700,
                  color: active ? C.indigo : C.textDim,
                  letterSpacing: "0.4px",
                }}
              >
                {inst.name}
              </span>
              <div
                style={{
                  width: "5px",
                  height: "5px",
                  borderRadius: "50%",
                  background: dotColor,
                  boxShadow:
                    inst.state === "READY"
                      ? "0 0 6px rgba(34,197,94,0.6)"
                      : "none",
                }}
              />
            </button>
          );
        })}

        <div style={{ flex: 1 }} />

        {/* Bottom icon buttons */}
        {[
          { emoji: "⚙️", label: "Diag", action: () => setDrawerOpen(true) },
          { emoji: "📊", label: "Learn", action: () => {} },
          { emoji: "🔬", label: "Rsrch", action: () => {} },
          { emoji: "🚀", label: "Exec", action: () => setTradeOpen(true) },
        ].map((item) => (
          <button
            key={item.label}
            onClick={item.action}
            style={{
              width: "44px",
              height: "42px",
              background: "transparent",
              border: "none",
              borderRadius: "10px",
              cursor: "pointer",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: "2px",
              transition: "background 0.15s",
            }}
          >
            <span style={{ fontSize: "15px", lineHeight: 1 }}>{item.emoji}</span>
            <span
              style={{ fontSize: "8px", color: C.textFaint, letterSpacing: "0.5px" }}
            >
              {item.label}
            </span>
          </button>
        ))}
      </nav>

      {/* ═══════════════════════════════════════════════════
          MAIN BRAIN WORKSPACE
      ═══════════════════════════════════════════════════ */}
      <main
        style={{
          padding: "36px 48px 28px",
          display: "flex",
          flexDirection: "column",
          overflowY: "auto",
        }}
      >
        {/* ── Top bar ── */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: "40px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span
              style={{
                fontSize: "12px",
                fontWeight: 600,
                color: C.textDim,
                letterSpacing: "2px",
                textTransform: "uppercase",
              }}
            >
              {activeTicker}
            </span>
            <div
              style={{
                background: C.accentMuted,
                border: `1px solid ${C.accentBorder}`,
                borderRadius: "20px",
                padding: "2px 9px",
                fontSize: "10px",
                color: C.accent,
                fontWeight: 700,
                letterSpacing: "1px",
              }}
            >
              SCALP
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: "8px" }}>
            <span
              style={{
                fontSize: "20px",
                fontWeight: 700,
                color: C.textPrimary,
                letterSpacing: "-0.5px",
              }}
            >
              {MOCK.price}
            </span>
            <span
              style={{ fontSize: "13px", color: C.green, fontWeight: 600 }}
            >
              {MOCK.change}
            </span>
          </div>
        </div>

        {/* ── VERDICT (the centrepiece) ── */}
        <div style={{ marginBottom: "24px" }}>
          <div
            style={{
              fontSize: "76px",
              fontWeight: 800,
              color: verdictColor,
              letterSpacing: "-4px",
              lineHeight: 0.95,
              textShadow: verdictGlow,
              marginBottom: "20px",
              userSelect: "none",
            }}
          >
            {verdict}
          </div>

          {/* Confidence bar */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "14px",
              marginBottom: "22px",
            }}
          >
            <div
              style={{
                flex: 1,
                height: "3px",
                background: "rgba(255,255,255,0.05)",
                borderRadius: "2px",
              }}
            >
              <div
                style={{
                  height: "100%",
                  width: `${MOCK.edge}%`,
                  background: `linear-gradient(90deg, ${C.accent}, ${verdictColor})`,
                  borderRadius: "2px",
                }}
              />
            </div>
            <span
              style={{
                fontSize: "12px",
                color: C.textDim,
                fontWeight: 600,
                fontVariantNumeric: "tabular-nums",
                whiteSpace: "nowrap",
              }}
            >
              {MOCK.edge} / 100
            </span>
          </div>

          {/* Plain-English reason */}
          <p
            style={{
              fontSize: "17px",
              fontWeight: 400,
              color: C.textSecondary,
              lineHeight: 1.65,
              maxWidth: "620px",
              margin: 0,
            }}
          >
            {MOCK.reason}
          </p>
        </div>

        {/* ── Three key rows ── */}
        <div style={{ display: "flex", flexDirection: "column" }}>
          {[
            {
              label: "Next requirement",
              value: MOCK.nextRequirement,
              accent: C.indigo,
              symbol: "→",
            },
            {
              label: "Invalidation",
              value: MOCK.invalidation,
              accent: C.orange,
              symbol: "✕",
            },
            {
              label: "Learning memory",
              value: MOCK.learning,
              accent: C.teal,
              symbol: "◎",
            },
          ].map((row, i) => (
            <div key={row.label}>
              <div
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: "16px",
                  padding: "15px 0",
                }}
              >
                <span
                  style={{
                    fontSize: "13px",
                    color: row.accent,
                    flexShrink: 0,
                    width: "16px",
                    textAlign: "center",
                    marginTop: "2px",
                    opacity: 0.8,
                  }}
                >
                  {row.symbol}
                </span>
                <div>
                  <Label>{row.label}</Label>
                  <div
                    style={{
                      fontSize: "14px",
                      color: C.textSecondary,
                      lineHeight: 1.55,
                      marginTop: "5px",
                    }}
                  >
                    {row.value}
                  </div>
                </div>
              </div>
              {i < 2 && <Divider />}
            </div>
          ))}
        </div>

        {/* ── Suggested Levels strip ── */}
        <div style={{ marginTop: "24px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "12px" }}>
            <Label>Suggested levels</Label>
            <div style={{
              fontSize: "10px", color: "#f59e0b", fontWeight: 600,
              background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.18)",
              borderRadius: "20px", padding: "1px 8px", letterSpacing: "0.5px",
            }}>
              FORMING · not yet READY
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: "10px" }}>
            {[
              { label: "Entry Zone", value: "21,318", sub: "– 21,325", color: "#e8e8f0", bg: "rgba(255,255,255,0.03)", accent: "rgba(255,255,255,0.08)" },
              { label: "Stop Loss", value: "21,280", sub: "–38 pts", color: "#f87171", bg: "rgba(239,68,68,0.05)", accent: "rgba(239,68,68,0.15)" },
              { label: "Target 1", value: "21,380", sub: "+62 pts", color: "#60a5fa", bg: "rgba(96,165,250,0.05)", accent: "rgba(96,165,250,0.15)" },
              { label: "R : R", value: "1 : 1.6", sub: "ATR-based", color: "#a5b4fc", bg: "rgba(165,180,252,0.05)", accent: "rgba(165,180,252,0.15)" },
            ].map((tile) => (
              <div key={tile.label} style={{
                padding: "14px 16px",
                background: tile.bg,
                border: `1px solid ${tile.accent}`,
                borderRadius: "12px",
                position: "relative" as const,
                overflow: "hidden",
              }}>
                <div style={{
                  position: "absolute", top: 0, left: 0, right: 0, height: "2px",
                  background: tile.accent,
                }} />
                <Label>{tile.label}</Label>
                <div style={{
                  fontSize: "20px", fontWeight: 800, color: tile.color,
                  letterSpacing: "-0.5px", marginTop: "7px", lineHeight: 1,
                }}>
                  {tile.value}
                </div>
                <div style={{ fontSize: "11px", color: C.textDim, marginTop: "4px" }}>
                  {tile.sub}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* ── Action row ── */}
        <div
          style={{
            marginTop: "auto",
            paddingTop: "20px",
            display: "flex",
            gap: "10px",
            alignItems: "center",
          }}
        >
          <button
            onClick={() => setTradeOpen(true)}
            style={{
              padding: "11px 26px",
              background: isReady ? C.green : C.accentMuted,
              border: `1px solid ${isReady ? "transparent" : C.accentBorder}`,
              borderRadius: "12px",
              cursor: "pointer",
              fontSize: "13px",
              fontWeight: 700,
              color: isReady ? "#030d06" : "#9090d0",
              letterSpacing: "0.3px",
              transition: "all 0.15s",
            }}
          >
            {isReady ? "Enter Trade" : "Open Trade Ticket"}
          </button>
          <button
            onClick={() => setDrawerOpen(true)}
            style={{
              padding: "11px 18px",
              background: "transparent",
              border: `1px solid ${C.border}`,
              borderRadius: "12px",
              cursor: "pointer",
              fontSize: "12px",
              color: C.textDim,
              fontWeight: 600,
              letterSpacing: "0.3px",
            }}
          >
            Diagnostics
          </button>
        </div>
      </main>

      {/* ═══════════════════════════════════════════════════
          RIGHT RISK RAIL
      ═══════════════════════════════════════════════════ */}
      <aside
        style={{
          background: C.surface,
          borderLeft: `1px solid ${C.border}`,
          padding: "24px 20px",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <Label>Account</Label>

        {/* Balance block */}
        <div style={{ marginTop: "14px", marginBottom: "20px" }}>
          <div
            style={{
              fontSize: "26px",
              fontWeight: 800,
              color: C.textPrimary,
              letterSpacing: "-0.8px",
            }}
          >
            {MOCK.account.balance}
          </div>
          <div
            style={{
              fontSize: "12px",
              color: C.green,
              fontWeight: 600,
              marginTop: "3px",
            }}
          >
            {MOCK.account.dailyPnl} today
          </div>
        </div>

        {/* Risk meter */}
        <div style={{ marginBottom: "18px" }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              marginBottom: "7px",
            }}
          >
            <span style={{ fontSize: "11px", color: C.textDim }}>
              Daily risk used
            </span>
            <span
              style={{ fontSize: "11px", color: C.textSecondary, fontWeight: 600 }}
            >
              {MOCK.account.riskUsed}%
            </span>
          </div>
          <div
            style={{
              height: "4px",
              background: "rgba(255,255,255,0.04)",
              borderRadius: "2px",
            }}
          >
            <div
              style={{
                height: "100%",
                width: `${MOCK.account.riskUsed}%`,
                background: `linear-gradient(90deg, ${C.green}, #a3e635)`,
                borderRadius: "2px",
              }}
            />
          </div>
        </div>

        <Divider />

        {/* Stats */}
        <div style={{ marginTop: "4px" }}>
          {[
            { label: "Max loss", value: MOCK.account.maxLoss },
            { label: "Remaining", value: MOCK.account.remaining },
            { label: "Trades today", value: String(MOCK.account.trades) },
            { label: "Wins", value: `${MOCK.account.wins} of ${MOCK.account.trades}` },
          ].map((stat) => (
            <div
              key={stat.label}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "10px 0",
                borderBottom: `1px solid ${C.border}`,
              }}
            >
              <span style={{ fontSize: "12px", color: C.textDim }}>
                {stat.label}
              </span>
              <span
                style={{
                  fontSize: "12px",
                  fontWeight: 600,
                  color: C.textSecondary,
                }}
              >
                {stat.value}
              </span>
            </div>
          ))}
        </div>

        <div style={{ flex: 1 }} />

        {/* Active trade indicator */}
        <div
          style={{
            padding: "12px 14px",
            background: C.greenBg,
            border: `1px solid ${C.greenBorder}`,
            borderRadius: "10px",
            marginBottom: "10px",
          }}
        >
          <div
            style={{
              fontSize: "10px",
              fontWeight: 700,
              color: "#166534",
              letterSpacing: "1.2px",
              marginBottom: "5px",
              textTransform: "uppercase",
            }}
          >
            Active · {MOCK.activeTrade.ticker}
          </div>
          <div style={{ fontSize: "12px", color: "#4ade80" }}>
            {MOCK.activeTrade.dir} @ {MOCK.activeTrade.entry}
          </div>
          <div
            style={{ fontSize: "11px", color: "#166534", marginTop: "2px" }}
          >
            {MOCK.activeTrade.unrealized} unrealized
          </div>
        </div>

        {/* Prop safety */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "7px",
          }}
        >
          <div
            style={{
              width: "6px",
              height: "6px",
              background: C.green,
              borderRadius: "50%",
              boxShadow: "0 0 6px rgba(34,197,94,0.5)",
            }}
          />
          <span style={{ fontSize: "11px", color: "#16a34a", fontWeight: 600 }}>
            Prop rules safe
          </span>
        </div>
      </aside>

      {/* ═══════════════════════════════════════════════════
          BOTTOM EVENT TIMELINE
      ═══════════════════════════════════════════════════ */}
      <footer
        style={{
          gridColumn: "1 / -1",
          background: "#090912",
          borderTop: `1px solid ${C.border}`,
          display: "flex",
          alignItems: "center",
          overflowX: "auto",
          overflowY: "hidden",
          paddingLeft: "8px",
        }}
      >
        {MOCK.timeline.map((ev, i) => (
          <div
            key={i}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "10px",
              padding: "0 24px",
              height: "100%",
              borderRight: `1px solid ${C.border}`,
              flexShrink: 0,
            }}
          >
            <span
              style={{
                fontSize: "10px",
                color: C.textFaint,
                fontWeight: 600,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {ev.time}
            </span>
            <span style={{ fontSize: "13px" }}>{ev.icon}</span>
            <span
              style={{
                fontSize: "12px",
                color:
                  ev.kind === "trade"
                    ? "#4ade80"
                    : ev.kind === "setup"
                    ? C.indigo
                    : C.textDim,
                whiteSpace: "nowrap",
              }}
            >
              {ev.event}
            </span>
          </div>
        ))}
      </footer>

      {/* ═══════════════════════════════════════════════════
          DIAGNOSTICS SLIDE-OVER DRAWER
      ═══════════════════════════════════════════════════ */}
      {drawerOpen && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 50,
            display: "flex",
          }}
        >
          <div
            onClick={() => setDrawerOpen(false)}
            style={{
              flex: 1,
              background: "rgba(0,0,0,0.55)",
              backdropFilter: "blur(6px)",
              WebkitBackdropFilter: "blur(6px)",
            }}
          />
          <div
            style={{
              width: "360px",
              background: C.surfaceHigh,
              borderLeft: `1px solid ${C.borderMid}`,
              display: "flex",
              flexDirection: "column",
              overflowY: "auto",
            }}
          >
            {/* Drawer header */}
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "22px 24px 18px",
                borderBottom: `1px solid ${C.border}`,
                position: "sticky",
                top: 0,
                background: C.surfaceHigh,
              }}
            >
              <span
                style={{
                  fontSize: "13px",
                  fontWeight: 700,
                  color: C.textPrimary,
                }}
              >
                Diagnostics
              </span>
              <button
                onClick={() => setDrawerOpen(false)}
                style={{
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  fontSize: "20px",
                  color: C.textDim,
                  lineHeight: 1,
                  padding: "0 2px",
                }}
              >
                ×
              </button>
            </div>

            {/* Gate items */}
            <div
              style={{
                padding: "16px 24px",
                display: "flex",
                flexDirection: "column",
                gap: "8px",
              }}
            >
              <Label>Gate breakdown — {activeTicker}</Label>
              <div style={{ height: "12px" }} />
              {MOCK.diagnostics.map((item) => {
                const isFail =
                  item.sub.toLowerCase().includes("fail") ||
                  item.sub.toLowerCase().includes("block") ||
                  item.sub.toLowerCase().includes("below");
                return (
                  <div
                    key={item.label}
                    style={{
                      padding: "13px 16px",
                      background: "rgba(255,255,255,0.02)",
                      border: `1px solid ${C.border}`,
                      borderRadius: "10px",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        marginBottom: "5px",
                      }}
                    >
                      <span style={{ fontSize: "12px", color: C.textDim }}>
                        {item.label}
                      </span>
                      <span
                        style={{
                          fontSize: "12px",
                          fontWeight: 600,
                          color: isFail ? "#f87171" : C.textSecondary,
                        }}
                      >
                        {item.value}
                      </span>
                    </div>
                    <span style={{ fontSize: "11px", color: C.textFaint }}>
                      {item.sub}
                    </span>
                  </div>
                );
              })}
            </div>

            {/* Drawer footer */}
            <div
              style={{
                padding: "16px 24px",
                marginTop: "auto",
                borderTop: `1px solid ${C.border}`,
              }}
            >
              <button
                onClick={() => setDrawerOpen(false)}
                style={{
                  width: "100%",
                  padding: "11px",
                  background: "transparent",
                  border: `1px solid ${C.border}`,
                  borderRadius: "10px",
                  cursor: "pointer",
                  fontSize: "12px",
                  color: C.textDim,
                  fontWeight: 600,
                }}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════
          TRADE TICKET BOTTOM SHEET
      ═══════════════════════════════════════════════════ */}
      {tradeOpen && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 50,
            display: "flex",
            alignItems: "flex-end",
          }}
        >
          <div
            onClick={() => setTradeOpen(false)}
            style={{
              position: "absolute",
              inset: 0,
              background: "rgba(0,0,0,0.55)",
              backdropFilter: "blur(6px)",
              WebkitBackdropFilter: "blur(6px)",
            }}
          />
          <div
            style={{
              position: "relative",
              width: "100%",
              background: C.surfaceHigh,
              borderTop: `1px solid ${C.borderMid}`,
              padding: "28px 40px",
              display: "flex",
              gap: "32px",
              alignItems: "flex-start",
            }}
          >
            <div style={{ flex: 1 }}>
              <div
                style={{
                  fontSize: "13px",
                  fontWeight: 700,
                  color: C.textPrimary,
                  marginBottom: "18px",
                  letterSpacing: "-0.2px",
                }}
              >
                Trade Ticket
                <span
                  style={{
                    fontSize: "12px",
                    color: C.textDim,
                    fontWeight: 400,
                    marginLeft: "10px",
                  }}
                >
                  {activeTicker} · Mock data — not connected
                </span>
              </div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(4, 1fr)",
                  gap: "10px",
                }}
              >
                {[
                  { label: "Entry Zone", value: MOCK.ticket.entryZone, color: C.textSecondary },
                  { label: "Stop Loss", value: MOCK.ticket.stop, color: "#f87171" },
                  { label: "Target 1", value: MOCK.ticket.target1, color: "#60a5fa" },
                  { label: "R:R", value: MOCK.ticket.rr, color: C.indigo },
                ].map((f) => (
                  <div
                    key={f.label}
                    style={{
                      padding: "14px 16px",
                      background: "rgba(255,255,255,0.02)",
                      border: `1px solid ${C.border}`,
                      borderRadius: "10px",
                    }}
                  >
                    <Label>{f.label}</Label>
                    <div
                      style={{
                        fontSize: "18px",
                        fontWeight: 700,
                        color: f.color,
                        marginTop: "6px",
                        letterSpacing: "-0.3px",
                      }}
                    >
                      {f.value}
                    </div>
                  </div>
                ))}
              </div>
              <div
                style={{
                  marginTop: "12px",
                  fontSize: "11px",
                  color: C.textFaint,
                }}
              >
                Setup is WAIT — manual override only. All safety layers apply on send.
              </div>
            </div>

            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "8px",
                paddingTop: "28px",
                flexShrink: 0,
              }}
            >
              <button
                style={{
                  padding: "12px 36px",
                  background: C.green,
                  border: "none",
                  borderRadius: "10px",
                  cursor: "pointer",
                  fontSize: "14px",
                  fontWeight: 800,
                  color: "#030d06",
                  letterSpacing: "0.2px",
                }}
              >
                Enter Long
              </button>
              <button
                onClick={() => setTradeOpen(false)}
                style={{
                  padding: "11px 36px",
                  background: "transparent",
                  border: `1px solid ${C.border}`,
                  borderRadius: "10px",
                  cursor: "pointer",
                  fontSize: "13px",
                  color: C.textDim,
                  fontWeight: 600,
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
