/**
 * mainBrainNormalizer.ts — Phase 7C.1/7C.2
 *
 * Pure functions that map the live /main-brain payload schema to the shape
 * expected by each panel on the operator console.
 *
 * Rules:
 *  - No React imports — pure TypeScript, testable with tsx or any Node runner.
 *  - No trading values computed; no defaults invented.
 *  - Null / undefined fields with no canonical source are preserved as-is.
 *  - The original raw object is NEVER mutated.
 *
 * Exported for use by MainBrain.tsx and by test_phase7c3_normalizer.ts.
 */

// ── Helpers ──────────────────────────────────────────────────────────────────

export function safeStr(v: unknown, fallback = '—'): string {
  if (v == null || v === '' || v === 'null') return fallback;
  return String(v);
}

export function safeNum(v: unknown): number | null {
  const n = Number(v);
  return (v != null && !isNaN(n) && isFinite(n)) ? n : null;
}

/** Extract a boolean from a bare boolean, absent value (fail-open → true),
 *  or an availability envelope `{available: bool}`. */
export function extractAvail(v: unknown): boolean {
  if (v == null) return true;           // absent → fail-open
  if (typeof v === 'boolean') return v;
  if (typeof v === 'object') return (v as Record<string, unknown>).available !== false;
  return v !== false;
}

export function mapStrategyResult(r: string): string {
  if (r === 'ready')     return 'READY';
  if (r === 'skipped')   return 'SKIP';
  if (r === 'no_signal') return 'NO SIGNAL';
  return r.toUpperCase() || '—';
}

// ── Normalizer ───────────────────────────────────────────────────────────────

/**
 * Maps the live /main-brain payload schema to the shape expected by each panel.
 * Read-only: no trading values computed, no defaults invented.
 * Null/undefined fields with no canonical source are preserved as-is.
 *
 * IMPORTANT: This function must be kept in sync with its usage in MainBrain.tsx.
 * Any schema change that requires updating this function also requires updating
 * the corresponding panel consumer in MainBrain.tsx.
 */
export function normalizeMainBrainPayload(raw: Record<string, unknown>): Record<string, unknown> {
  // ── market: flatten session object, rename selected_instrument → instrument ──
  const mkt  = (raw.market ?? {}) as Record<string, unknown>;
  const sess = (mkt.session ?? {}) as Record<string, unknown>;
  const market: Record<string, unknown> = {
    ...mkt,
    session_status: safeStr(sess.status, 'UNKNOWN'),
    instrument:     safeStr(mkt.selected_instrument, ''),
  };

  // ── market_state: extract regime string from possible {regime, reason} object ─
  const ms     = (raw.market_state ?? {}) as Record<string, unknown>;
  const regRaw = ms.regime;
  const market_state: Record<string, unknown> = {
    ...ms,
    regime: (typeof regRaw === 'object' && regRaw !== null)
      ? safeStr((regRaw as Record<string, unknown>).regime, '')
      : regRaw,
  };

  // ── left_brain: flatten thesis sub-object ────────────────────────────────────
  const lb     = (raw.left_brain ?? {}) as Record<string, unknown>;
  const thesis = (lb.thesis ?? {}) as Record<string, unknown>;
  const left_brain: Record<string, unknown> = {
    ...lb,
    direction:    thesis.direction    ?? null,
    confidence:   thesis.confidence   ?? null,
    narrative:    thesis.narrative    ?? null,
    generated_at: thesis.generated_at ?? null,
    status:       thesis.status       ?? null,
    momentum:     thesis.strength     ?? null,  // strength → momentum label
    age_seconds:  thesis.age_seconds  ?? null,
  };

  // ── verdict: add edge_grade alias + pass transparency fields ─────────────────
  const vrd = (raw.verdict ?? {}) as Record<string, unknown>;
  const verdict: Record<string, unknown> = {
    ...vrd,
    edge_grade:           vrd.grade,
    edge_components:      Array.isArray(vrd.edge_components)      ? vrd.edge_components      : [],
    score_breakdown:      Array.isArray(vrd.score_breakdown)      ? vrd.score_breakdown      : [],
    failed_confirmations: Array.isArray(vrd.failed_confirmations) ? vrd.failed_confirmations : [],
    risks:                Array.isArray(vrd.risks)                ? vrd.risks                : [],
  };

  // ── strategy_scanner: rename fields, build trade_plan ───────────────────────
  const sc     = (raw.strategy_scanner ?? {}) as Record<string, unknown>;
  const ranked = Array.isArray(sc.ranked_strategies)
    ? (sc.ranked_strategies as Record<string, unknown>[]) : [];
  const normalizedStrategies: Record<string, unknown>[] = ranked.map(s => {
    const eligible   = s.eligible !== false;
    const skipReason = safeStr(s.skip_reason, '');
    return {
      ...s,
      key:             s.strategy_key,
      name:            s.label,
      readiness:       mapStrategyResult(safeStr(s.result, '')),
      mode_compatible: eligible ? null
        : (skipReason.toLowerCase().includes('mode') ? false : null),
    };
  });
  const selKey   = sc.selected as string | null | undefined;
  const selStrat = normalizedStrategies.find(s => s.strategy_key === selKey);
  const targets  = Array.isArray(sc.targets) ? sc.targets as unknown[] : [];
  const strategy_scanner: Record<string, unknown> = {
    ...sc,
    selected_strategy: selKey,
    strategies:        normalizedStrategies,
    trade_plan: {
      entry:     sc.entry     ?? null,
      stop:      sc.stop      ?? null,
      target_1:  targets[0]   ?? null,
      target_2:  targets[1]   ?? null,
      target_3:  targets[2]   ?? null,
      rr:        sc.risk_reward ?? null,
      direction: selStrat?.direction ?? null,
      setup:     selStrat ? safeStr(selStrat.label as unknown, '') : null,
      status:    selKey ? 'READY' : null,
    },
  };

  // ── active_trades: wrap bare array in {available, trades} ───────────────────
  const atRaw     = raw.active_trades;
  const tradesArr = Array.isArray(atRaw) ? (atRaw as Record<string, unknown>[]) : [];
  const active_trades: Record<string, unknown> = {
    available: true,
    trades: tradesArr.map(t => ({ ...t, quantity: t.contracts ?? t.quantity })),
  };

  // ── alerts: wrap bare array; normalize field names ───────────────────────────
  const alertsRaw = raw.alerts;
  const alertsArr = Array.isArray(alertsRaw) ? (alertsRaw as Record<string, unknown>[]) : [];
  const alerts: Record<string, unknown> = {
    available: true,
    items: alertsArr.map(a => ({
      ...a,
      timestamp:  a.ts,
      instrument: safeStr(a.ticker, '').replace(/1!$/, ''),
      message:    a.alert_type,
      severity:   a.verdict ?? a.alert_type,
    })),
  };

  // ── journal: flatten summary stats, rename recent_trades/field aliases ────────
  const jnl        = (raw.journal ?? {}) as Record<string, unknown>;
  const jnlSummary = (jnl.summary ?? {}) as Record<string, unknown>;
  const recentList = Array.isArray(jnl.recent_trades)
    ? (jnl.recent_trades as Record<string, unknown>[]) : [];
  const rawWr = safeNum(jnlSummary.win_rate);
  const journal: Record<string, unknown> = {
    available:      jnl.available !== false,
    today_count:    jnlSummary.total_trades ?? null,
    today_win_rate: rawWr != null ? rawWr * 100 : null,
    today_avg_r:    jnlSummary.avg_r ?? null,
    recent_closed:  recentList.map(t => ({
      ...t,
      instrument: t.symbol ?? t.instrument,
      setup:      t.strategy ?? t.setup,
    })),
  };

  // ── system_status: already has canonical aliases added by backend ────────────
  //    (db_ready, databento_ready, broker_ready, learning_ready now in payload)
  const system_status = (raw.system_status ?? {}) as Record<string, unknown>;

  // ── performance: add trade_count alias ──────────────────────────────────────
  const perf = (raw.performance ?? {}) as Record<string, unknown>;
  const performance: Record<string, unknown> = { ...perf, trade_count: perf.sample ?? null };

  // ── availability: extract {available: bool} objects → plain booleans ─────────
  const rawAvail = (raw.availability ?? {}) as Record<string, unknown>;
  const availability: Record<string, unknown> = {
    ...rawAvail,
    left_brain:        extractAvail(rawAvail.left_brain),
    strategy_scanner:  extractAvail(rawAvail.strategy_scanner),
    coach:             extractAvail(rawAvail.coach),
    journal:           extractAvail(rawAvail.journal),
    decision_timeline: extractAvail(rawAvail.timeline),   // payload key is 'timeline'
    alerts:            extractAvail(rawAvail.alerts),
    active_trades:     extractAvail(rawAvail.active_trades),
    execution_gateway: extractAvail(rawAvail.execution_gateway),
    performance:       extractAvail(rawAvail.performance),
    market:            extractAvail(rawAvail.market),
    market_state:      extractAvail(rawAvail.market_state),
    system_status:     extractAvail(rawAvail.system_status),
  };

  // ── decision_timeline: normalize event field names ───────────────────────────
  const tl      = (raw.decision_timeline ?? {}) as Record<string, unknown>;
  const tlEvts  = Array.isArray(tl.events) ? (tl.events as Record<string, unknown>[]) : [];
  const decision_timeline: Record<string, unknown> = {
    ...tl,
    available: true,
    events: tlEvts.map(e => ({
      ...e,
      timestamp:   e.ts ?? e.timestamp,
      event_label: e.label ?? e.event_type,
      source:      e.event_type,
    })),
  };

  // ── main_brain: voice lives at raw.voice (added by backend Phase 7C.1) ───────
  // raw.voice may be a string or a {narration, headline} dict from compute_main_brain_voice
  const rawVoice = raw.voice;
  const voiceStr: string | null =
    rawVoice == null              ? null
    : typeof rawVoice === 'string'  ? rawVoice
    : typeof rawVoice === 'object'
      ? (safeStr((rawVoice as Record<string, unknown>).narration, '')
          || safeStr((rawVoice as Record<string, unknown>).headline, '')
          || null)
      : null;
  const main_brain: Record<string, unknown> = { voice: voiceStr };

  return {
    ...raw,
    market,
    market_state,
    left_brain,
    verdict,
    strategy_scanner,
    active_trades,
    alerts,
    journal,
    system_status,
    performance,
    availability,
    decision_timeline,
    main_brain,
  };
}
