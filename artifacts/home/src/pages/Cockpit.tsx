import { useState, useEffect, useCallback, useRef } from "react";

// ── Types ──────────────────────────────────────────────────────────────────────
type TradePlan = {
  trade_plan?: boolean;
  direction?: string;
  entry_zone?: string;
  stop_loss?: string | number;
  target1?: string | number;
  rr?: string;
  rr_num?: number;
};

type ActiveTradeMgmt = {
  active?: boolean;
  status?: string;
  direction?: string;
  entry_price?: number;
  symbol?: string;
  unrealized_r?: number;
  current_r?: number;
};

type TimelineEvent = {
  event?: string;
  category?: string;
  time?: string;
  ts?: string;
  icon?: string;
};

type BrainContract = {
  decision: {
    verdict: string;
    is_ready: boolean;
    direction: string | null;
    next_action: string | null;
  };
  score: {
    value: number;
    max: number;
    grade: string | null;
  };
  reasons: {
    top: string[];
  };
  trade_plan: TradePlan | null;
  freshness?: Record<string, unknown>;
};

type StatusData = {
  verdict: string;
  edge_score: number;
  edge_grade?: string;
  strict_reason?: string;
  strict_direction?: string;
  stage_next_step?: string;
  stage_invalidation?: string;
  current_price?: number;
  display_price?: string;
  trading_mode?: string;
  execution_mode?: string;
  active_ticker?: string;
  trade_plan?: TradePlan;
  market_events_timeline?: TimelineEvent[];
  gate_debug?: Record<string, unknown>;
  alert_diagnostics?: Record<string, unknown>;
  vwap_value?: number;
  vwap_status?: string;
  active_trade_mgmt?: ActiveTradeMgmt;
  prop_firm?: {
    enabled?: boolean;
    db_ready?: boolean;
    headline?: string;
    account?: { name?: string; firm?: string } | null;
    phase2?: string[];
  };
  trade_memory?: { summary_text?: string };
  analyst?: { memory_review?: unknown };
  confidence_governor?: { summary?: unknown };
  main_brain_voice?: string;
  status?: string;
  bias?: string;
  biasDirection?: string | null;
  conviction_tier?: string | null;
  alert_level?: string | null;
  bullish_score?: number;
  bearish_score?: number;
  current_atr?: number;
  confluences?: {
    bos?: boolean;
    choch?: boolean;
    vwap?: boolean;
    vwap_value?: number;
    vwap_status?: string;
    direction?: string;
    cvd_confirmed?: boolean;
    volume_confirmed?: boolean;
    zone_confirmed?: boolean;
    liquidity_sweep?: boolean;
    structure_confirmed?: boolean;
  };
  data_feed?: {
    overall_freshness?: string;
    provider?: string;
    instruments?: Record<string, { freshness?: string; auto_age_secs?: number }>;
  };
  brain?: BrainContract;
};

type InstSnap = { state: string; edge: number; mode: string };

const INSTRUMENTS = ["MNQ", "MGC", "MES", "MYM"];

// ── Design tokens (unchanged) ──────────────────────────────────────────────────
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

// ── Helper components ──────────────────────────────────────────────────────────
function Label({ children }: { children: string }) {
  return (
    <span style={{
      fontSize: "10px", fontWeight: 700, letterSpacing: "1.5px",
      textTransform: "uppercase" as const, color: C.textFaint,
    }}>
      {children}
    </span>
  );
}
function Divider() {
  return <div style={{ height: "1px", background: C.border }} />;
}
function safeStr(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}
function timelineIcon(cat?: string) {
  return cat === "trade" ? "✅" : cat === "setup" ? "📍" : cat === "data" ? "🔵" : "◎";
}
function timelineColor(cat?: string) {
  return cat === "trade" ? "#4ade80" : cat === "setup" ? C.indigo : C.textDim;
}
function fmtTime(ev: TimelineEvent) {
  if (ev.time) return ev.time;
  if (ev.ts) {
    try {
      return new Date(ev.ts).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
    } catch { /* ignore */ }
  }
  return "";
}

function playChihuahuaBark(): void {
  try {
    type WinAudio = Window & { webkitAudioContext?: typeof AudioContext };
    const AudioCtx = window.AudioContext ?? (window as WinAudio).webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const bark = (t: number) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "sawtooth";
      o.frequency.setValueAtTime(1100, t);
      o.frequency.exponentialRampToValueAtTime(680, t + 0.09);
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(0.22, t + 0.008);
      g.gain.exponentialRampToValueAtTime(0.001, t + 0.13);
      o.connect(g); g.connect(ctx.destination); o.start(t); o.stop(t + 0.14);

      const o2 = ctx.createOscillator(), g2 = ctx.createGain();
      o2.type = "square";
      o2.frequency.setValueAtTime(2200, t);
      o2.frequency.exponentialRampToValueAtTime(1400, t + 0.07);
      g2.gain.setValueAtTime(0, t);
      g2.gain.linearRampToValueAtTime(0.07, t + 0.005);
      g2.gain.exponentialRampToValueAtTime(0.001, t + 0.10);
      o2.connect(g2); g2.connect(ctx.destination); o2.start(t); o2.stop(t + 0.11);
    };
    const now = ctx.currentTime;
    bark(now); bark(now + 0.2); bark(now + 0.4);
    setTimeout(() => ctx.close(), 1000);
  } catch { /* unsupported */ }
}

function buildLegacyBrainFallback(d: StatusData): BrainContract {
  console.warn("[Cockpit] data.brain absent — using legacy whole-contract fallback");
  const top: string[] = [];
  if (d.strict_reason) top.push(d.strict_reason);
  return {
    decision: {
      verdict: d.verdict,
      is_ready: d.verdict.includes("READY"),
      direction: d.strict_direction ?? null,
      next_action: d.stage_next_step ?? null,
    },
    score: {
      value: d.edge_score,
      max: 110,
      grade: d.edge_grade ?? null,
    },
    reasons: { top },
    trade_plan: d.trade_plan ?? null,
  };
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function Cockpit() {
  const [activeTicker, setActiveTicker] = useState("MNQ");
  const [drawerOpen, setDrawerOpen]     = useState(false);
  const [tradeOpen, setTradeOpen]       = useState(false);
  const [data, setData]                 = useState<StatusData | null>(null);
  const [instSnaps, setInstSnaps]       = useState<Record<string, InstSnap>>({});
  const [staleSnaps, setStaleSnaps]     = useState<Record<string, boolean>>({});
  const [fetchError, setFetchError]     = useState<string | null>(null);
  const [pwd, setPwd]                   = useState<string>(() => { try { return sessionStorage.getItem("cockpit_pwd") ?? ""; } catch { return ""; } });
  const [showLogin, setShowLogin]       = useState<boolean>(() => { try { return !sessionStorage.getItem("cockpit_pwd"); } catch { return true; } });
  const [loginInput, setLoginInput]     = useState("");
  const [loginErr, setLoginErr]         = useState(false);
  const [isMobile, setIsMobile]         = useState(() => typeof window !== "undefined" && window.innerWidth < 768);
  const [barkMuted, setBarkMuted]       = useState<boolean>(() => {
    try { return localStorage.getItem("cockpit_bark_muted") === "1"; } catch { return false; }
  });

  const activeRef   = useRef(activeTicker);
  activeRef.current = activeTicker;
  const pwdRef      = useRef(pwd);
  pwdRef.current    = pwd;
  const loginAttempted  = useRef(false);
  const prevVerdictRef  = useRef<string>("");
  const barkMutedRef    = useRef(barkMuted);
  barkMutedRef.current  = barkMuted;

  // ── applyData: shared by active poll + background snap ──────────────────────
  const applyData = useCallback((json: StatusData, ticker: string) => {
    if (ticker === activeRef.current) {
      const newVerdict = json.verdict ?? "";
      if (newVerdict.includes("READY") && !prevVerdictRef.current.includes("READY") && !barkMutedRef.current) {
        playChihuahuaBark();
      }
      prevVerdictRef.current = newVerdict;
      setData(json);
      setFetchError(null);
    }
    // Successful response clears stale flag for that instrument
    setStaleSnaps(prev => ({ ...prev, [ticker]: false }));
    const snapBrain = json.brain;
    setInstSnaps(prev => ({
      ...prev,
      [ticker]: {
        state: snapBrain
          ? (snapBrain.decision.is_ready ? "READY" : "WAIT")
          : ((json.verdict ?? "").includes("READY") ? "READY" : "WAIT"),
        edge:  snapBrain ? snapBrain.score.value : (json.edge_score ?? 0),
        mode:  json.trading_mode ?? "SCALP",
      },
    }));
  }, []);

  // ── Active instrument 3s poll ────────────────────────────────────────────────
  const fetchStatus = useCallback(async (ticker: string) => {
    try {
      const headers: Record<string, string> = {};
      const p = pwdRef.current;
      if (p) headers["Authorization"] = `Basic ${btoa(":" + p)}`;
      const res = await fetch(`/api/status?ticker=${ticker}`, { credentials: "include", headers });
      if (res.status === 401) {
        const hadPwd = !!pwdRef.current;
        setPwd("");
        try { sessionStorage.removeItem("cockpit_pwd"); } catch { /* ignore */ }
        if (hadPwd && loginAttempted.current) setLoginErr(true);
        loginAttempted.current = false;
        setShowLogin(true);
        return;
      }
      if (res.status === 503) return;
      if (!res.ok) { setFetchError(`HTTP ${res.status}`); return; }
      const json: StatusData = await res.json();
      if (json.status === "warming") return;
      applyData(json, ticker);
    } catch {
      setFetchError("Network error");
    }
  }, [applyData]);

  // ── Non-active instrument 30s background snap ────────────────────────────────
  // Guards: ticker !== activeRef; failed fetch marks the snap stale; cannot
  // overwrite active instrument data (applyData guards on activeRef.current).
  const fetchBackgroundSnap = useCallback(async (ticker: string) => {
    try {
      const headers: Record<string, string> = {};
      const p = pwdRef.current;
      if (p) headers["Authorization"] = `Basic ${btoa(":" + p)}`;
      const res = await fetch(`/api/status?ticker=${ticker}`, { credentials: "include", headers });
      if (!res.ok) {
        setStaleSnaps(prev => ({ ...prev, [ticker]: true }));
        return;
      }
      const json: StatusData = await res.json();
      if (json.status === "warming") return;
      applyData(json, ticker);
    } catch {
      setStaleSnaps(prev => ({ ...prev, [ticker]: true }));
    }
  }, [applyData]);

  const handleLogin = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    setLoginErr(false);
    const p = loginInput.trim();
    if (!p) return;
    setPwd(p);
    pwdRef.current = p;
    loginAttempted.current = true;
    try { sessionStorage.setItem("cockpit_pwd", p); } catch { /* ignore */ }
    setShowLogin(false);
    setLoginInput("");
    INSTRUMENTS.forEach(inst => fetchStatus(inst));
  }, [loginInput, fetchStatus]);

  // Initial fetch for all instruments
  useEffect(() => { INSTRUMENTS.forEach(inst => fetchStatus(inst)); }, [fetchStatus]);

  // Active instrument: 3s poll
  useEffect(() => {
    fetchStatus(activeTicker);
    const id = setInterval(() => fetchStatus(activeTicker), 3000);
    return () => clearInterval(id);
  }, [activeTicker, fetchStatus]);

  // Non-active instruments: 30s background refresh
  useEffect(() => {
    const id = setInterval(() => {
      INSTRUMENTS.forEach(inst => {
        if (inst !== activeRef.current) fetchBackgroundSnap(inst);
      });
    }, 30000);
    return () => clearInterval(id);
  }, [fetchBackgroundSnap]);

  // Responsive: track viewport width
  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // ── Brain Contract (Phase 4B — single whole-contract accessor) ───────────────
  const brain    = data != null ? (data.brain ?? buildLegacyBrainFallback(data)) : null;
  const verdict  = brain?.decision.verdict ?? "—";
  const isReady  = brain?.decision.is_ready ?? false;
  const edge     = brain?.score.value ?? 0;
  const edgeMax  = brain?.score.max ?? 110;
  const mode     = data?.trading_mode ?? "—";

  const displayPrice = data?.display_price
    ?? (data?.current_price
        ? data.current_price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        : "—");

  // Brain-owned operator fields
  const reason       = safeStr(brain?.reasons.top?.[0]) ?? (data ? "" : "Connecting to bot...");
  const nextReq      = safeStr(brain?.decision.next_action) ?? (data ? "No requirement pending." : "—");
  const invalidation = safeStr(data?.stage_invalidation) ?? (data ? "No invalidation defined." : "—");

  // Learning — diagnostic/history sources (Section 5 only, not the Brain reason)
  const learningText = safeStr(data?.trade_memory?.summary_text)
    ?? safeStr(data?.analyst?.memory_review)
    ?? safeStr(data?.confidence_governor?.summary)
    ?? (data ? "No matching setups in learning memory yet." : "—");

  // Trade plan — from brain contract
  const tp           = brain?.trade_plan ?? null;
  const hasPlan      = !!tp?.trade_plan && !!tp?.entry_zone;
  const entryZoneStr = tp?.entry_zone ?? "—";
  const stopLossStr  = tp?.stop_loss  != null ? Number(tp.stop_loss).toFixed(2)  : "—";
  const target1Str   = tp?.target1    != null ? Number(tp.target1).toFixed(2)    : "—";
  const rrStr        = tp?.rr ?? "—";

  const { midEntry } = (() => {
    if (!hasPlan || !tp?.entry_zone) return { midEntry: null };
    const z = tp.entry_zone!;
    if (z.includes("–")) {
      const [lo, hi] = z.split("–").map(s => parseFloat(s.replace(/,/g, "")));
      return { midEntry: (lo + hi) / 2 };
    }
    return { midEntry: parseFloat(z.replace(/,/g, "")) };
  })();
  const stopDelta = midEntry != null && stopLossStr !== "—"
    ? `${(Number(stopLossStr) - midEntry).toFixed(0)} pts` : "";
  const t1Delta   = midEntry != null && target1Str !== "—"
    ? `+${(Number(target1Str) - midEntry).toFixed(0)} pts` : "";

  const levelsBadgeText   = isReady && hasPlan ? "ACTIVE PLAN"           : "FORMING · not yet READY";
  const levelsBadgeColor  = isReady && hasPlan ? C.green                 : "#f59e0b";
  const levelsBadgeBg     = isReady && hasPlan ? "rgba(34,197,94,0.08)"  : "rgba(245,158,11,0.08)";
  const levelsBadgeBorder = isReady && hasPlan ? "rgba(34,197,94,0.18)" : "rgba(245,158,11,0.18)";

  // Section 3 — Risk & Execution (flat /status sources retained)
  const atm            = data?.active_trade_mgmt;
  const hasActiveTrade = (atm?.active === true) || atm?.status === "active";
  const atDir          = atm?.direction ?? "Long";
  const atEntry        = atm?.entry_price != null ? atm.entry_price.toFixed(2) : "—";
  const atSymbol       = atm?.symbol ?? activeTicker;
  const atR            = atm?.current_r ?? atm?.unrealized_r;
  const atUnrealized   = atR != null ? `${atR > 0 ? "+" : ""}${atR.toFixed(2)}R` : "—";

  const propEnabled    = data?.prop_firm?.enabled ?? false;
  const propHasAccount = data?.prop_firm?.account != null;
  const propSafe       = !propEnabled || propHasAccount;

  // Section 2 — Market Structure (flat /status sources retained)
  const bias      = safeStr(data?.bias) ?? "—";
  const vwapVal   = data?.confluences?.vwap_value ?? data?.vwap_value;
  const price     = data?.current_price;
  const aboveVwap = vwapVal != null && price != null ? price > vwapVal : null;
  const vwapCtx   = vwapVal != null
    ? `${vwapVal.toFixed(2)} · ${aboveVwap === true ? "price above" : aboveVwap === false ? "price below" : ""}`
    : "—";
  const atrStr    = data?.current_atr != null ? `${data.current_atr.toFixed(2)} pts` : "—";

  // Section 3 — execution context (flat /status)
  const freshness = safeStr(data?.data_feed?.overall_freshness) ?? "—";
  const execMode  = safeStr(data?.execution_mode)?.replace(/_/g, " ") ?? "—";

  // Seven gate indicators (Section 2)
  const gateItems = [
    { label: "BOS",    ok: data?.confluences?.bos },
    { label: "CHOCH",  ok: data?.confluences?.choch },
    { label: "VWAP",   ok: data?.confluences?.vwap },
    { label: "CVD",    ok: data?.confluences?.cvd_confirmed },
    { label: "Volume", ok: data?.confluences?.volume_confirmed },
    { label: "Zone",   ok: data?.confluences?.zone_confirmed },
    { label: "Sweep",  ok: data?.confluences?.liquidity_sweep },
  ];

  // Section 5 — Learning & History
  const timeline: TimelineEvent[] = Array.isArray(data?.market_events_timeline)
    ? data!.market_events_timeline!.slice(0, 12) : [];

  // Section 4 — Diagnostics drawer (raw/diagnostic fields, behind control)
  const gateDebug = (data?.gate_debug && typeof data.gate_debug === "object") ? data.gate_debug : null;
  const diagItems = (() => {
    if (!data) return [];
    const items: { label: string; value: string; sub: string }[] = [];
    items.push({ label: "Edge score", value: `${edge} / ${edgeMax}`, sub: brain?.score.grade ? `${brain.score.grade} grade` : "" });
    items.push({ label: "Direction (raw)", value: data.strict_direction ?? "—", sub: "" });
    items.push({ label: "Bull score", value: `${data.bullish_score ?? 0}`, sub: "directional confidence" });
    items.push({ label: "Bear score", value: `${data.bearish_score ?? 0}`, sub: "directional confidence" });
    items.push({ label: "Feed",        value: freshness, sub: data.data_feed?.provider ?? "" });
    if (data.vwap_value) {
      const dir = (data.current_price ?? 0) > data.vwap_value ? "above" : "below";
      items.push({ label: "VWAP", value: data.vwap_value.toFixed(2), sub: `Price ${dir} VWAP` });
    }
    if (gateDebug) {
      const keyLabels: Record<string, string> = {
        zone: "Zone", vwap: "VWAP gate", structure: "BOS / CHOCH",
        cvd: "CVD", volume: "Volume", volatility: "Volatility", entry_quality: "Entry quality",
      };
      for (const [k, v] of Object.entries(gateDebug)) {
        if (k === "blocked_by" || k === "reason") continue;
        items.push({
          label: keyLabels[k] ?? k.replace(/_/g, " "),
          value: v === true ? "PASS" : v === false ? "FAIL" : String(v),
          sub: "",
        });
      }
    }
    return items;
  })();

  // Nav rail instrument snapshots (with stale indicator)
  const instruments = INSTRUMENTS.map(name => ({
    name,
    state: instSnaps[name]?.state ?? "—",
    edge:  instSnaps[name]?.edge  ?? 0,
    mode:  instSnaps[name]?.mode  ?? "—",
    stale: staleSnaps[name] ?? false,
  }));

  const verdictColor = isReady ? C.green : "#a5b4fc";
  const verdictGlow  = isReady ? "0 0 100px rgba(34,197,94,0.12)" : "0 0 100px rgba(165,180,252,0.07)";

  // ── Render ─────────────────────────────────────────────────────────────────────
  return (
    <div id="cockpit-root" style={{
      background: C.bg, color: C.textPrimary,
      fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      WebkitFontSmoothing: "antialiased",
      // Desktop: fixed 3-column × 2-row grid; Mobile: 2-column × 3-row, scrollable
      ...(isMobile ? {
        display: "grid",
        gridTemplateColumns: "56px 1fr",
        gridTemplateRows: "auto auto 92px",
        minHeight: "100vh",
      } : {
        display: "grid",
        gridTemplateColumns: "56px 1fr 228px",
        gridTemplateRows: "1fr 92px",
        height: "100vh",
        overflow: "hidden",
      }),
    }}>

      {/* ── NAV RAIL ──────────────────────────────────────────────────────────── */}
      <nav id="cockpit-nav" style={{
        gridRow: "1 / -1", gridColumn: 1,
        background: C.surface, borderRight: `1px solid ${C.border}`,
        display: "flex", flexDirection: "column", alignItems: "center",
        paddingTop: "14px", paddingBottom: "14px", gap: "4px",
        ...(isMobile ? { position: "sticky", top: 0, zIndex: 10 } : {}),
      }}>
        <div style={{
          width: "32px", height: "32px",
          background: "linear-gradient(135deg, #6366f1 0%, #818cf8 100%)",
          borderRadius: "9px", display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: "15px", marginBottom: "18px", boxShadow: "0 0 24px rgba(99,102,241,0.35)", flexShrink: 0,
        }}>🤖</div>

        {instruments.map((inst) => {
          const active = inst.name === activeTicker;
          // Purple dot = stale snapshot; green = READY; dim = WAIT
          const dotColor = inst.stale
            ? "#7c3aed"
            : inst.state === "READY" ? C.green
            : inst.state === "WAIT"  ? "#2a2a44"
            : "#1e1e32";
          return (
            <button key={inst.name} onClick={() => setActiveTicker(inst.name)} style={{
              width: "44px", background: active ? C.accentMuted : "transparent",
              border: `1px solid ${active ? C.accentBorder : "transparent"}`,
              borderRadius: "10px", padding: "7px 0 6px", cursor: "pointer",
              display: "flex", flexDirection: "column", alignItems: "center", gap: "5px",
            }}>
              <span style={{ fontSize: "10px", fontWeight: 700, color: active ? C.indigo : C.textDim, letterSpacing: "0.4px" }}>
                {inst.name}
              </span>
              <div style={{
                width: "5px", height: "5px", borderRadius: "50%", background: dotColor,
                boxShadow: inst.state === "READY" && !inst.stale ? "0 0 6px rgba(34,197,94,0.6)" : "none",
              }} />
            </button>
          );
        })}

        <div style={{ flex: 1 }} />
        {[
          { emoji: "⚙️", label: "Diag",  action: () => setDrawerOpen(true) },
          { emoji: "🚀", label: "Exec",  action: () => setTradeOpen(true) },
        ].map(item => (
          <button key={item.label} onClick={item.action} style={{
            width: "44px", height: "42px", background: "transparent", border: "none",
            borderRadius: "10px", cursor: "pointer",
            display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: "2px",
          }}>
            <span style={{ fontSize: "15px", lineHeight: 1 }}>{item.emoji}</span>
            <span style={{ fontSize: "8px", color: C.textFaint, letterSpacing: "0.5px" }}>{item.label}</span>
          </button>
        ))}
        {/* Page navigation — Brain / Dashboard / Engine */}
        <div style={{ width: "100%", borderTop: `1px solid ${C.border}`, paddingTop: 6, display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
          {([
            { href: "/",            emoji: "🧠", label: "Brain"  },
            { href: "/dashboard",   emoji: "📊", label: "Dash"   },
          ] as const).map(({ href, emoji, label }) => (
            <a key={label} href={href} title={label} style={{
              width: "44px", height: "38px", background: "transparent", border: "none",
              borderRadius: "10px", cursor: "pointer", textDecoration: "none",
              display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: "2px",
            }}
              onMouseEnter={e => (e.currentTarget as HTMLAnchorElement).style.background = "rgba(255,255,255,0.05)"}
              onMouseLeave={e => (e.currentTarget as HTMLAnchorElement).style.background = "transparent"}
            >
              <span style={{ fontSize: "14px", lineHeight: 1 }}>{emoji}</span>
              <span style={{ fontSize: "7px", color: C.textFaint, letterSpacing: "0.5px" }}>{label}</span>
            </a>
          ))}
          <a href="/api/dashboard" target="_blank" rel="noreferrer" title="Engineering dashboard" style={{
            width: "44px", height: "38px", background: "transparent", border: "none",
            borderRadius: "10px", cursor: "pointer", textDecoration: "none",
            display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: "2px",
          }}
            onMouseEnter={e => (e.currentTarget as HTMLAnchorElement).style.background = "rgba(255,255,255,0.05)"}
            onMouseLeave={e => (e.currentTarget as HTMLAnchorElement).style.background = "transparent"}
          >
            <span style={{ fontSize: "13px", lineHeight: 1 }}>⚙</span>
            <span style={{ fontSize: "7px", color: C.textFaint, letterSpacing: "0.5px" }}>Engine</span>
          </a>
        </div>
      </nav>

      {/* ── SECTION 1: BRAIN DECISION ──────────────────────────────────────────── */}
      <main id="cockpit-brain-decision" style={{
        gridColumn: 2, gridRow: 1,
        padding: isMobile ? "20px 16px" : "36px 48px 28px",
        display: "flex", flexDirection: "column",
        overflowY: isMobile ? "visible" : "auto",
      }}>

        {/* Top bar: instrument · mode · price */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "40px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span style={{ fontSize: "12px", fontWeight: 600, color: C.textDim, letterSpacing: "2px", textTransform: "uppercase" }}>
              {activeTicker}
            </span>
            <div style={{
              background: C.accentMuted, border: `1px solid ${C.accentBorder}`,
              borderRadius: "20px", padding: "2px 9px",
              fontSize: "10px", color: C.accent, fontWeight: 700, letterSpacing: "1px",
            }}>{mode || "—"}</div>
            {fetchError && (
              <span style={{ fontSize: "10px", color: "#f87171", letterSpacing: "0.5px" }}>⚠ {fetchError}</span>
            )}
          </div>
          <span style={{ fontSize: "20px", fontWeight: 700, color: C.textPrimary, letterSpacing: "-0.5px" }}>
            {displayPrice}
          </span>
        </div>

        {/* Primary verdict + score + grade + direction */}
        <div style={{ marginBottom: "24px" }}>
          {/* Verdict headline — single primary display */}
          <div id="primary-verdict" style={{
            fontSize: "76px", fontWeight: 800, color: verdictColor,
            letterSpacing: "-4px", lineHeight: 0.95, textShadow: verdictGlow,
            marginBottom: "20px", userSelect: "none",
          }}>{verdict}</div>

          {/* Score bar + score label */}
          <div style={{ display: "flex", alignItems: "center", gap: "14px", marginBottom: "10px" }}>
            <div style={{ flex: 1, height: "3px", background: "rgba(255,255,255,0.05)", borderRadius: "2px" }}>
              <div id="primary-score-bar" style={{
                height: "100%",
                width: `${Math.max(0, Math.min(100, edgeMax > 0 ? (edge / edgeMax) * 100 : 0))}%`,
                background: `linear-gradient(90deg, ${C.accent}, ${verdictColor})`,
                borderRadius: "2px", transition: "width 0.6s ease",
              }} />
            </div>
            <span id="primary-score-label" style={{
              fontSize: "12px", color: C.textDim, fontWeight: 600,
              fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap",
            }}>
              {edge} / {edgeMax}
            </span>
          </div>

          {/* Grade + direction — brain-owned, single primary display */}
          <div style={{ display: "flex", gap: "6px", marginBottom: "18px", flexWrap: "wrap" }}>
            {brain?.score.grade && (
              <div id="primary-grade" style={{
                background: "rgba(165,180,252,0.08)", border: "1px solid rgba(165,180,252,0.18)",
                borderRadius: "8px", padding: "3px 10px",
                fontSize: "11px", color: C.indigo, fontWeight: 700, letterSpacing: "0.5px",
              }}>Grade {brain.score.grade}</div>
            )}
            {brain != null && (
              <div id="primary-direction" style={{
                background: "rgba(52,211,153,0.08)", border: "1px solid rgba(52,211,153,0.18)",
                borderRadius: "8px", padding: "3px 10px",
                fontSize: "11px", color: C.teal, fontWeight: 700, letterSpacing: "0.5px",
              }}>{brain?.decision.direction ?? "—"}</div>
            )}
          </div>

          {/* Primary reason (Brain reasons.top[0] only) */}
          <p style={{ fontSize: "17px", fontWeight: 400, color: C.textSecondary, lineHeight: 1.65, maxWidth: "620px", margin: 0 }}>
            {reason}
          </p>
        </div>

        {/* Two key rows: Next action + Invalidation */}
        <div id="cockpit-key-rows" style={{ display: "flex", flexDirection: "column" }}>
          {[
            { label: "Next action",  value: nextReq,      accent: C.indigo, symbol: "→" },
            { label: "Invalidation", value: invalidation, accent: C.orange, symbol: "✕" },
          ].map((row, i) => (
            <div key={row.label}>
              <div style={{ display: "flex", alignItems: "flex-start", gap: "16px", padding: "15px 0" }}>
                <span style={{ fontSize: "13px", color: row.accent, flexShrink: 0, width: "16px", textAlign: "center", marginTop: "2px", opacity: 0.8 }}>
                  {row.symbol}
                </span>
                <div>
                  <Label>{row.label}</Label>
                  <div style={{ fontSize: "14px", color: C.textSecondary, lineHeight: 1.55, marginTop: "5px" }}>
                    {row.value}
                  </div>
                </div>
              </div>
              {i < 1 && <Divider />}
            </div>
          ))}
        </div>

        {/* Suggested levels — from brain.trade_plan */}
        <div style={{ marginTop: "24px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "12px" }}>
            <Label>Suggested levels</Label>
            <div style={{
              fontSize: "10px", color: levelsBadgeColor, fontWeight: 600,
              background: levelsBadgeBg, border: `1px solid ${levelsBadgeBorder}`,
              borderRadius: "20px", padding: "1px 8px", letterSpacing: "0.5px",
            }}>{levelsBadgeText}</div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr 1fr" : "1fr 1fr 1fr 1fr", gap: "10px" }}>
            {[
              { label: "Entry Zone", value: entryZoneStr, sub: entryZoneStr.includes("–") ? "zone range" : "", color: "#e8e8f0", bg: "rgba(255,255,255,0.03)", accent: "rgba(255,255,255,0.08)" },
              { label: "Stop Loss",  value: stopLossStr,  sub: stopDelta,  color: "#f87171", bg: "rgba(239,68,68,0.05)",    accent: "rgba(239,68,68,0.15)" },
              { label: "Target 1",  value: target1Str,   sub: t1Delta,    color: "#60a5fa", bg: "rgba(96,165,250,0.05)",   accent: "rgba(96,165,250,0.15)" },
              { label: "R : R",     value: rrStr,        sub: "ATR-based", color: "#a5b4fc", bg: "rgba(165,180,252,0.05)", accent: "rgba(165,180,252,0.15)" },
            ].map(tile => (
              <div key={tile.label} style={{
                padding: "14px 16px", background: tile.bg, border: `1px solid ${tile.accent}`,
                borderRadius: "12px", position: "relative" as const, overflow: "hidden",
              }}>
                <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: "2px", background: tile.accent }} />
                <Label>{tile.label}</Label>
                <div style={{ fontSize: "20px", fontWeight: 800, color: tile.color, letterSpacing: "-0.5px", marginTop: "7px", lineHeight: 1 }}>
                  {tile.value}
                </div>
                {tile.sub && <div style={{ fontSize: "11px", color: C.textDim, marginTop: "4px" }}>{tile.sub}</div>}
              </div>
            ))}
          </div>
        </div>

        {/* Primary action buttons */}
        <div style={{ marginTop: "auto", paddingTop: "20px", display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
          <button id="enter-trade-btn" onClick={() => { if (hasPlan) setTradeOpen(true); }} style={{
            padding: "11px 26px", background: isReady && hasPlan ? C.green : C.accentMuted,
            border: `1px solid ${isReady && hasPlan ? "transparent" : C.accentBorder}`,
            borderRadius: "12px", cursor: hasPlan ? "pointer" : "default", fontSize: "13px", fontWeight: 700,
            color: isReady && hasPlan ? "#030d06" : "#9090d0", letterSpacing: "0.3px",
          }}>
            {isReady && hasPlan ? "Enter Trade" : !hasPlan ? "No actionable trade plan" : "Open Trade Ticket"}
          </button>
          <button onClick={() => setDrawerOpen(true)} style={{
            padding: "11px 18px", background: "transparent", border: `1px solid ${C.border}`,
            borderRadius: "12px", cursor: "pointer", fontSize: "12px", color: C.textDim, fontWeight: 600,
          }}>
            Diagnostics
          </button>
        </div>
      </main>

      {/* ── SECTIONS 2 + 3: MARKET STRUCTURE & RISK/EXECUTION ─────────────────── */}
      <aside id="cockpit-market-risk" style={{
        gridColumn: isMobile ? 2 : 3,
        gridRow: isMobile ? 2 : 1,
        background: C.surface,
        borderLeft: isMobile ? "none" : `1px solid ${C.border}`,
        borderTop: isMobile ? `1px solid ${C.border}` : "none",
        padding: "20px 20px",
        display: "flex", flexDirection: "column",
        overflowY: isMobile ? "visible" : "auto",
      }}>

        {/* ─ SECTION 2: Market Structure ─────────────────────────────────────── */}
        <div id="cockpit-market-structure" style={{ marginBottom: "2px" }}>
          <Label>Market structure</Label>
        </div>

        {/* Seven gate indicators */}
        <div id="gate-checklist" style={{ marginTop: "8px", marginBottom: "10px", display: "flex", flexWrap: "wrap", gap: "5px" }}>
          {gateItems.map(g => (
            <div key={g.label} style={{
              display: "flex", alignItems: "center", gap: "3px",
              fontSize: "10px", fontWeight: 700, letterSpacing: "0.3px",
              padding: "3px 7px", borderRadius: "6px",
              background: g.ok === true ? "rgba(34,197,94,0.08)" : g.ok === false ? "rgba(239,68,68,0.06)" : "rgba(255,255,255,0.02)",
              border: `1px solid ${g.ok === true ? "rgba(34,197,94,0.2)" : g.ok === false ? "rgba(239,68,68,0.15)" : C.border}`,
              color: g.ok === true ? C.green : g.ok === false ? "#f87171" : C.textDim,
            }}>
              <span>{g.ok === true ? "✓" : g.ok === false ? "✗" : "·"}</span>
              <span>{g.label}</span>
            </div>
          ))}
        </div>

        {/* Structural context: VWAP, Bias, ATR */}
        <div style={{ marginBottom: "14px" }}>
          {[
            { label: "VWAP", value: vwapCtx },
            { label: "Bias", value: bias },
            { label: "ATR",  value: atrStr },
          ].map(stat => (
            <div key={stat.label} style={{
              display: "flex", justifyContent: "space-between", alignItems: "center",
              padding: "6px 0", borderBottom: `1px solid ${C.border}`,
            }}>
              <span style={{ fontSize: "11px", color: C.textDim }}>{stat.label}</span>
              <span style={{
                fontSize: "11px", fontWeight: 600, color: C.textSecondary,
                textAlign: "right", maxWidth: "120px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const,
              }}>{stat.value}</span>
            </div>
          ))}
        </div>

        <Divider />

        {/* ─ SECTION 3: Risk & Execution ─────────────────────────────────────── */}
        <div id="cockpit-risk-execution" style={{ marginTop: "12px", marginBottom: "8px" }}>
          <Label>Risk & execution</Label>
        </div>

        {/* Execution context rows */}
        <div style={{ marginBottom: "10px" }}>
          {[
            { label: "Exec mode", value: execMode },
            { label: "Mode",      value: mode },
          ].map(stat => (
            <div key={stat.label} style={{
              display: "flex", justifyContent: "space-between", alignItems: "center",
              padding: "6px 0", borderBottom: `1px solid ${C.border}`,
            }}>
              <span style={{ fontSize: "11px", color: C.textDim }}>{stat.label}</span>
              <span style={{
                fontSize: "11px", fontWeight: 600, color: C.textSecondary,
                textAlign: "right", maxWidth: "120px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const,
              }}>{stat.value}</span>
            </div>
          ))}
        </div>

        <div style={{ flex: 1 }} />

        {/* Active trade block */}
        {hasActiveTrade ? (
          <div id="active-trade-block" style={{
            padding: "12px 14px", background: C.greenBg, border: `1px solid ${C.greenBorder}`,
            borderRadius: "10px", marginBottom: "10px",
          }}>
            <div style={{ fontSize: "10px", fontWeight: 700, color: "#166534", letterSpacing: "1.2px", marginBottom: "5px", textTransform: "uppercase" }}>
              Active · {atSymbol}
            </div>
            <div style={{ fontSize: "12px", color: "#4ade80" }}>{atDir} @ {atEntry}</div>
            <div style={{ fontSize: "11px", color: "#166534", marginTop: "2px" }}>{atUnrealized} unrealized</div>
          </div>
        ) : data ? (
          <div style={{ padding: "10px 14px", background: "rgba(255,255,255,0.02)", border: `1px solid ${C.border}`, borderRadius: "10px", marginBottom: "10px" }}>
            <span style={{ fontSize: "11px", color: C.textFaint }}>No active trade</span>
          </div>
        ) : null}

        {/* Prop-account protection dot */}
        <div id="prop-protection" style={{ display: "flex", alignItems: "center", gap: "7px" }}>
          <div style={{
            width: "6px", height: "6px", borderRadius: "50%",
            background: !propEnabled ? "#4b5563" : propSafe ? C.green : "#f97316",
            boxShadow: propEnabled && propSafe ? "0 0 6px rgba(34,197,94,0.5)" : "none",
          }} />
          <span style={{ fontSize: "11px", fontWeight: 600, color: !propEnabled ? C.textDim : propSafe ? "#16a34a" : "#f97316" }}>
            {!data ? "—" :
             !propEnabled ? "Prop rules off" :
             !propHasAccount ? "No account set" :
             safeStr(data?.prop_firm?.account?.name)
               ? `${data!.prop_firm!.account!.name} · ON`
               : "Protection ON"}
          </span>
        </div>
      </aside>

      {/* ── SECTION 5: LEARNING & HISTORY ──────────────────────────────────────── */}
      <footer id="cockpit-learning" style={{
        gridColumn: isMobile ? 2 : "2 / -1",
        gridRow: isMobile ? 3 : 2,
        background: "#090912", borderTop: `1px solid ${C.border}`,
        display: "flex", flexDirection: "column",
        overflow: "hidden",
      }}>
        {/* Learning text row (trade memory / analyst memory / governor — Section 5 only) */}
        <div style={{
          display: "flex", alignItems: "center", gap: "8px",
          padding: "5px 12px 5px 16px", borderBottom: `1px solid ${C.border}`,
          flexShrink: 0,
        }}>
          <span style={{
            fontSize: "9px", fontWeight: 700, letterSpacing: "1.5px",
            textTransform: "uppercase", color: C.teal, flexShrink: 0,
          }}>Learning</span>
          <span style={{
            fontSize: "11px", color: C.textDim,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1,
          }}>
            {learningText}
          </span>
        </div>

        {/* Timeline events + bark mute */}
        <div style={{ display: "flex", alignItems: "center", flex: 1, overflowX: "auto", overflowY: "hidden", paddingLeft: "8px" }}>
          <button
            onClick={() => setBarkMuted(m => {
              const next = !m;
              try { localStorage.setItem("cockpit_bark_muted", next ? "1" : "0"); } catch { /* ignore */ }
              return next;
            })}
            title={barkMuted ? "Unmute chihuahua bark on READY" : "Mute chihuahua bark on READY"}
            style={{
              flexShrink: 0, background: "none", border: `1px solid ${C.border}`,
              borderRadius: "6px", padding: "2px 9px", cursor: "pointer",
              fontSize: "11px", color: barkMuted ? C.textFaint : C.textDim,
              marginRight: "8px", whiteSpace: "nowrap",
            }}
          >
            {barkMuted ? "🔇" : "🐕"} {barkMuted ? "bark: off" : "bark: on"}
          </button>

          {timeline.length === 0 ? (
            <span style={{ fontSize: "12px", color: C.textFaint, padding: "0 24px" }}>
              {data ? "No timeline events yet." : "Loading timeline..."}
            </span>
          ) : timeline.map((ev, i) => (
            <div key={i} style={{
              display: "flex", alignItems: "center", gap: "10px",
              padding: "0 24px", height: "100%", borderRight: `1px solid ${C.border}`, flexShrink: 0,
            }}>
              <span style={{ fontSize: "10px", color: C.textFaint, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
                {fmtTime(ev)}
              </span>
              <span style={{ fontSize: "13px" }}>{ev.icon ?? timelineIcon(ev.category)}</span>
              <span style={{ fontSize: "12px", color: timelineColor(ev.category), whiteSpace: "nowrap" }}>
                {safeStr(ev.event) ?? ""}
              </span>
            </div>
          ))}
        </div>
      </footer>

      {/* ── SECTION 4: DIAGNOSTICS DRAWER (collapsed by default) ──────────────── */}
      {drawerOpen && (
        <div id="cockpit-diagnostics" style={{ position: "fixed", inset: 0, zIndex: 50, display: "flex" }}>
          <div onClick={() => setDrawerOpen(false)} style={{
            flex: 1, background: "rgba(0,0,0,0.55)",
            backdropFilter: "blur(6px)", WebkitBackdropFilter: "blur(6px)",
          }} />
          <div style={{
            width: "360px", background: C.surfaceHigh,
            borderLeft: `1px solid ${C.borderMid}`,
            display: "flex", flexDirection: "column", overflowY: "auto",
          }}>
            <div style={{
              display: "flex", justifyContent: "space-between", alignItems: "center",
              padding: "22px 24px 18px", borderBottom: `1px solid ${C.border}`,
              position: "sticky", top: 0, background: C.surfaceHigh,
            }}>
              <span style={{ fontSize: "13px", fontWeight: 700, color: C.textPrimary }}>Diagnostics</span>
              <button onClick={() => setDrawerOpen(false)} style={{
                background: "transparent", border: "none", cursor: "pointer",
                fontSize: "20px", color: C.textDim, lineHeight: 1, padding: "0 2px",
              }}>×</button>
            </div>
            <div style={{ padding: "16px 24px", display: "flex", flexDirection: "column", gap: "8px" }}>
              <Label>{`Gate breakdown — ${activeTicker}`}</Label>
              <div style={{ height: "12px" }} />
              {diagItems.length === 0 ? (
                <div style={{ fontSize: "12px", color: C.textFaint }}>
                  {data ? "No gate data." : "Loading..."}
                </div>
              ) : diagItems.map(item => {
                const isFail = item.value === "FAIL" || item.value === "false";
                return (
                  <div key={item.label} style={{
                    padding: "13px 16px", background: "rgba(255,255,255,0.02)",
                    border: `1px solid ${C.border}`, borderRadius: "10px",
                  }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: item.sub ? "5px" : 0 }}>
                      <span style={{ fontSize: "12px", color: C.textDim }}>{item.label}</span>
                      <span style={{ fontSize: "12px", fontWeight: 600, color: isFail ? "#f87171" : C.textSecondary }}>{item.value}</span>
                    </div>
                    {item.sub && <span style={{ fontSize: "11px", color: C.textFaint }}>{item.sub}</span>}
                  </div>
                );
              })}
            </div>
            <div style={{ padding: "16px 24px", marginTop: "auto", borderTop: `1px solid ${C.border}` }}>
              <button onClick={() => setDrawerOpen(false)} style={{
                width: "100%", padding: "11px", background: "transparent",
                border: `1px solid ${C.border}`, borderRadius: "10px",
                cursor: "pointer", fontSize: "12px", color: C.textDim, fontWeight: 600,
              }}>Close</button>
            </div>
          </div>
        </div>
      )}

      {/* ── TRADE TICKET ───────────────────────────────────────────────────────── */}
      {tradeOpen && (
        <div style={{ position: "fixed", inset: 0, zIndex: 50, display: "flex", alignItems: "flex-end" }}>
          <div onClick={() => setTradeOpen(false)} style={{
            position: "absolute", inset: 0, background: "rgba(0,0,0,0.55)",
            backdropFilter: "blur(6px)", WebkitBackdropFilter: "blur(6px)",
          }} />
          <div style={{
            position: "relative", width: "100%", background: C.surfaceHigh,
            borderTop: `1px solid ${C.borderMid}`, padding: "28px 40px",
            display: "flex", gap: "32px", alignItems: "flex-start", flexWrap: "wrap",
          }}>
            <div style={{ flex: 1, minWidth: "240px" }}>
              <div style={{ fontSize: "13px", fontWeight: 700, color: C.textPrimary, marginBottom: "18px" }}>
                Trade Ticket
                <span style={{ fontSize: "12px", color: C.textDim, fontWeight: 400, marginLeft: "10px" }}>
                  {activeTicker} · {isReady ? "READY" : "WAIT — manual override only"}
                </span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "10px" }}>
                {[
                  { label: "Entry Zone", value: entryZoneStr, color: C.textSecondary },
                  { label: "Stop Loss",  value: stopLossStr,  color: "#f87171" },
                  { label: "Target 1",  value: target1Str,   color: "#60a5fa" },
                  { label: "R:R",        value: rrStr,         color: C.indigo },
                ].map(f => (
                  <div key={f.label} style={{
                    padding: "14px 16px", background: "rgba(255,255,255,0.02)",
                    border: `1px solid ${C.border}`, borderRadius: "10px",
                  }}>
                    <Label>{f.label}</Label>
                    <div style={{ fontSize: "18px", fontWeight: 700, color: f.color, marginTop: "6px", letterSpacing: "-0.3px" }}>
                      {f.value}
                    </div>
                  </div>
                ))}
              </div>
              <div style={{ marginTop: "12px", fontSize: "11px", color: C.textFaint }}>
                {hasPlan
                  ? `${tp?.direction ?? ""} setup · levels are bot-suggested`
                  : "No active plan — levels populate when a setup forms"}
              </div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "8px", paddingTop: "28px", flexShrink: 0 }}>
              <button onClick={() => setTradeOpen(false)} style={{
                padding: "12px 36px",
                background: isReady && hasPlan ? C.green : "rgba(99,102,241,0.15)",
                border: isReady && hasPlan ? "none" : `1px solid ${C.accentBorder}`,
                borderRadius: "10px", cursor: "pointer", fontSize: "14px", fontWeight: 800,
                color: isReady && hasPlan ? "#030d06" : "#9090d0",
              }}>
                {isReady && hasPlan ? `Enter ${tp?.direction ?? "Trade"}` : !hasPlan ? "No Trade Plan" : "Watching..."}
              </button>
              <button onClick={() => setTradeOpen(false)} style={{
                padding: "11px 36px", background: "transparent",
                border: `1px solid ${C.border}`, borderRadius: "10px",
                cursor: "pointer", fontSize: "13px", color: C.textDim, fontWeight: 600,
              }}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {/* ── LOGIN OVERLAY ───────────────────────────────────────────────────────── */}
      {showLogin && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 100,
          display: "flex", alignItems: "center", justifyContent: "center",
          background: "rgba(7,7,13,0.96)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)",
        }}>
          <div style={{
            background: C.surface, border: `1px solid ${C.borderMid}`,
            borderRadius: "20px", padding: "40px 36px", width: "320px",
            display: "flex", flexDirection: "column", gap: "0px",
          }}>
            <div style={{ fontSize: "26px", fontWeight: 800, color: C.textPrimary, letterSpacing: "-0.5px", marginBottom: "6px" }}>
              🤖 AI Cockpit
            </div>
            <div style={{ fontSize: "13px", color: C.textDim, marginBottom: "28px" }}>
              Enter your dashboard password to connect.
            </div>
            <form onSubmit={handleLogin} style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              <input
                type="password"
                autoFocus
                placeholder="Dashboard password"
                value={loginInput}
                onChange={e => { setLoginInput(e.target.value); setLoginErr(false); }}
                style={{
                  padding: "13px 16px", background: "rgba(255,255,255,0.04)",
                  border: `1px solid ${loginErr ? "#f87171" : C.borderMid}`,
                  borderRadius: "12px", color: C.textPrimary, fontSize: "15px", outline: "none",
                  fontFamily: "inherit", width: "100%", boxSizing: "border-box" as const,
                }}
              />
              {loginErr && <div style={{ fontSize: "12px", color: "#f87171" }}>Incorrect password — try again.</div>}
              <button type="submit" style={{
                padding: "13px", background: "linear-gradient(135deg, #6366f1, #818cf8)",
                border: "none", borderRadius: "12px", cursor: "pointer",
                fontSize: "14px", fontWeight: 700, color: "#fff", letterSpacing: "0.3px", marginTop: "4px",
              }}>Connect</button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
