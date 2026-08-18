import { Router } from "express";
import http from "http";

export interface FlaskProxyOptions {
  // Internal localhost port the target Flask process listens on.
  port: number;
  // Exact route whitelist to proxy (paths are relative to the Express mount).
  routes: string[];
}

// Build a Router that forwards a fixed whitelist of paths to a Flask process on
// localhost:<port>, relaying the raw request body + original content-type so
// webhook payloads (text/plain, application/json, …) arrive intact. Used twice:
//   • /api  -> the LIVE trading bot          (port 8000)
//   • /api2 -> the ANALYSIS-ONLY bot         (port 8001)
export function createFlaskProxy({ port, routes }: FlaskProxyOptions): Router {
  const router = Router();

  function proxyToFlask(req: any, res: any) {
    const flaskPath = req.path === "/" ? "/" : req.path;
    const query = Object.keys(req.query).length
      ? "?" + new URLSearchParams(req.query as Record<string, string>).toString()
      : "";

    // req.body is a Buffer (see express.raw in app.ts) holding the exact bytes the
    // client sent. Forward it verbatim with its original content-type so Flask
    // receives webhook payloads intact regardless of how TradingView labels them
    // (text/plain, application/json, …). Flask parses with get_json(force=True)
    // and falls back to the raw text, so the content-type only needs to be honest.
    const bodyBuf: Buffer = Buffer.isBuffer(req.body) ? req.body : Buffer.alloc(0);

    const headers: Record<string, string> = {};
    if (bodyBuf.length > 0) {
      const incomingCt = req.headers["content-type"];
      headers["content-type"] =
        (Array.isArray(incomingCt) ? incomingCt[0] : incomingCt) ||
        "application/json";
      headers["content-length"] = bodyBuf.length.toString();
    }

    const options: http.RequestOptions = {
      hostname: "localhost",
      port,
      path: flaskPath + query,
      method: req.method,
      headers,
    };

    const proxyReq = http.request(options, (proxyRes) => {
      res.status(proxyRes.statusCode ?? 200);
      const ct = proxyRes.headers["content-type"];
      if (ct) res.set("content-type", ct);

      // ── SSE / streaming response handling ───────────────────────────────
      // When Flask returns text/event-stream we must:
      //   1. Kill any upstream proxy buffering (Replit's nginx layer respects
      //      X-Accel-Buffering: no; generic CDNs respect this too).
      //   2. Flush headers to the browser immediately so EventSource sees the
      //      200 + content-type and arms its reconnect logic from second 0.
      //   3. Disable the Node socket read-timeout (default is 0 = no timeout,
      //      but express / http module can set one; make it explicit).
      //   4. Tear down the upstream Flask connection when the browser tab closes
      //      so Flask's generator loop exits cleanly.
      const isSse = typeof ct === "string" && ct.includes("text/event-stream");
      if (isSse) {
        res.set("Cache-Control", "no-cache");
        res.set("Connection",    "keep-alive");
        // Disable nginx / Replit proxy response buffering for this stream.
        res.set("X-Accel-Buffering", "no");
        // Flush status + headers to the browser right away — without this,
        // Express may hold the response until the first chunk arrives (which
        // for a silent market can be 15 s), causing EventSource to time out
        // before it ever sees the 200.
        res.flushHeaders();
        // Remove any socket read-timeout on the upstream connection.
        proxyReq.setTimeout(0);
        proxyRes.socket?.setTimeout(0);
        // When the browser closes the tab / navigates away, destroy the
        // upstream pipe so Flask's generator exits instead of leaking forever.
        const onClose = () => { proxyRes.destroy(); };
        res.on("close", onClose);
        proxyRes.on("end", () => res.off("close", onClose));
      }

      // Forward Flask's caching directives. The /dashboard route serves inline JS
      // that changes on every deploy and must be served no-store, otherwise the
      // browser can run a stale cached dashboard and appear "frozen" on toggles.
      for (const h of ["cache-control", "pragma", "expires"]) {
        if (isSse) continue; // SSE headers already set above — don't overwrite
        const v = proxyRes.headers[h];
        if (v) res.set(h, Array.isArray(v) ? v.join(", ") : v);
      }
      proxyRes.pipe(res);
    });

    proxyReq.on("error", () => {
      if (!res.headersSent) {
        res.status(502).json({ error: "Webhook server unreachable" });
      }
    });

    if (bodyBuf.length > 0) {
      proxyReq.write(bodyBuf);
    }
    proxyReq.end();
  }

  router.all(routes, proxyToFlask);
  return router;
}

// ── Route whitelists ─────────────────────────────────────────────────────────
// LIVE trading bot (artifacts/tradingview-webhook). The full current route set.
export const BOT1_ROUTES = [
  "/",
  "/ping",
  "/webhook",
  "/enter",
  "/traderspost",
  // Manual pre-READY "TAKE THIS TRADE" preview entry (owner-only money path; NOT in
  // dashboard-auth OPEN_PATHS). Flag-gated (USER_APPROVED_PREVIEW_ENABLED); routes
  // through the SAME audited execution gateway as /traderspost.
  "/take-preview",
  // Manual Desk market order (owner-only money path; NOT in dashboard-auth
  // OPEN_PATHS). Flag-gated (MANUAL_ORDER_ENABLED); fires a discretionary market
  // order regardless of setup state through the SAME audited execution gateway.
  "/manual-order",
  // Owner-only notification test: fires a real Discord push (phone) + returns a
  // diagnostic. Pure notification test — never touches the gate or any broker path.
  "/notify-test",
  "/breakeven",
  "/close",
  // Quick Exit: owner-only broker flatten (action='exit', non-reversing) + local-
  // tracking clear. Sends a real order on live modes; paper/manual_only only clears
  // tracking. NOT in dashboard-auth OPEN_PATHS.
  "/quick-exit",
  // Stop-managing: owner-only local-tracking flush. Clears a stale tracked /
  // managed / monitored position so the bot stops showing a trade the user
  // already closed elsewhere. TRACKING-ONLY — never sends a broker order (NOT in
  // dashboard-auth OPEN_PATHS).
  "/stop-managing",
  "/trade",
  "/clear",
  "/clear-fired-keys",
  "/price",
  "/alerts",
  "/diagnostics",
  "/diagnostics-live",
  "/decision-trace",
  // Auto-Trade Settings page + its JSON API (owner-only runtime per-asset safety
  // controls; NOT in dashboard-auth OPEN_PATHS so Basic Auth + CSRF apply).
  "/auto-trade-settings",
  "/safety-settings",
  "/eval-metrics",
  "/status",
  "/mode",
  "/alerts/mute",
  "/auto-trade",
  // MICRO SCALP MODE toggle + ghost ledger (owner-only, DISPLAY/GHOST ONLY —
  // never places orders; NOT in dashboard-auth OPEN_PATHS).
  "/micro-scalp",
  // Live SWING strategy library selector (owner-only money-path control; NOT in
  // dashboard-auth OPEN_PATHS). DEMOTE-ONLY filter — only narrows which already-READY
  // SWING setups are taken; never creates a trade or loosens the gate.
  "/swing-strategy",
  "/swing-analysis",
  // Volatility Intelligence snapshot (DISPLAY-ONLY, OBSERVE-ONLY; NOT in
  // dashboard-auth OPEN_PATHS). Returns VIX regime/direction/risk-tone block.
  // Never touches gate, scoring, sizing, or execution.
  "/volatility-intelligence",
  // FVG / IFVG Scanner (owner-only; SHADOW/DISPLAY-ONLY; NOT in dashboard-auth
  // OPEN_PATHS). Runs the all-day FVG lifecycle engine on the Databento bar
  // stream; surfaces zones, lifecycle states, and ranking scores for the Main
  // Brain scanner panel and chart overlays. Never touches gate, edge score,
  // sizing, or execution — purely observational.
  "/fvg/zones",
  "/fvg/summary",
  "/fvg/sequences",
  "/advisor",
  "/pro-review",
  "/trade-debate",
  "/learning",
  "/learning-score",
  "/entry-quality",
  "/review-idea",
  "/failure-analysis",
  "/dpv2-scorecard",
  // AI assistant chat (owner-only; DISPLAY/READ-ONLY; NOT in dashboard-auth
  // OPEN_PATHS). Answers questions about the live setup + general trading.
  "/assistant",
  "/journal",
  "/journal/",
  // Phase 7K: Journal unified trade review (owner-only; DISPLAY/READ-ONLY except
  // import/rollback which mutate ONLY private journal tables — never the gate,
  // scoring, learning formulas, or broker path).
  "/journal/trades",
  "/journal/import/preview",
  "/journal/import/confirm",
  "/journal/import/rollback",
  "/journal/import/batches",
  "/journal/trade/:source/:id",
  "/journal/trade/:source/:id/notes",
  // Phase 7N: Review workflow — PATCH updates review fields, POST marks excluded.
  // All write to journal_reviews only (never execution-truth tables).
  "/journal/trade/:source/:id/review",
  "/journal/trade/:source/:id/exclude",
  "/journal/review-queue",
  // Phase 7N Batch B: full queue (5-bucket view) + calendar summary.
  // Both are read-only display routes — no money-path or gate involvement.
  "/journal/review-queue-full",
  "/journal/calendar-summary",
  // Phase 7N Batch C: per-trade learning eligibility + review analytics.
  // Both are pure SELECT aggregations — never touch learning weights or gate.
  "/journal/learning-eligibility",
  "/journal/review-analytics",
  // Phase 7O: Journal Coaching Dashboard (display-only analytics, SELECT only).
  "/journal/coaching",
  // Phase 7O.2: Intraday 30-min block coaching analytics (display-only, SELECT only).
  "/journal/coaching/intraday",
  // Phase 7O.3: Rating × Mistake/Emotion correlation analytics (display-only, SELECT only).
  "/journal/coaching/correlations",
  // Phase 7K-A.2: Native Journal read API (DISPLAY/READ-ONLY; owner-only; NOT in
  // dashboard-auth OPEN_PATHS). Reads only from native_journal table — never
  // touches the gate, scoring, learning formulas, or any broker path.
  "/journal/native-trades",
  "/journal/native-trades/:id",
  // Phase 7K-C: Native journal review workflow (owner-only; PATCH review fields,
  // upload/delete screenshot attachments — never modifies planned context or execution).
  "/journal/native-trades/:id/review",
  // POST /screenshots and DELETE /screenshots/:attachment_id are Express-native
  // (nj-screenshots.ts handles GCS upload with server-generated keys); only
  // the metadata-query path (/journal/native-counts) stays Flask-proxied.
  "/journal/native-counts",
  // Phase 7K-C: Native journal review queue (owner-only; aggregation of CLOSED
  // trades pending review — display-only SELECT, never touches any money path).
  "/journal/native-review-queue",
  "/journal/analytics",
  "/journal/playbook",
  "/journal/learning",
  // Phase 7M: Directional Balance audit panel (owner-only; DISPLAY/READ-ONLY; NOT in
  // dashboard-auth OPEN_PATHS). Aggregates Long vs Short signal counts from in-memory
  // EVAL_METRICS + ALERT_HISTORY + strategy_trades DB. Never modifies any state.
  "/directional-balance",
  "/eod",
  "/weekly",
  "/why",
  "/why/:ticker",
  "/dashboard",
  // Backtesting engine (owner-only; NOT in dashboard-auth OPEN_PATHS). The raw
  // body limit for CSV uploads is raised in app.ts.
  "/backtest/upload",
  "/backtest/datasets",
  "/backtest/datasets/:id",
  "/backtest/run",
  "/backtest/optimize",
  "/backtest/runs/:id",
  "/backtest/export",
  "/backtest/coverage",
  "/backtest/baselines",
  "/backtest/baselines/generate",
  "/backtest/baselines/:baseline_id",
  "/backtest/baselines/:baseline_id/trades",
  "/backtest/baselines/:baseline_id/breakdowns",
  // TradeZella journal import + review (owner-only; NOT in dashboard-auth
  // OPEN_PATHS). The raw body limit for the CSV upload is raised in app.ts.
  "/tradezella/upload",
  "/tradezella/analysis",
  "/tradezella/trades",
  "/tradezella/reset",
  "/tradezella/reseed-reviews",
  "/tradezella/rematch",
  "/tradezella/review-queue",
  "/trade-snapshots",
  // Manual Trade Manager (ADVISORY / DISPLAY-ONLY; owner-only; NOT in dashboard-auth
  // OPEN_PATHS). Never sends a broker order — monitors a manually-entered position.
  "/manual-trade",
  "/manual-trade/close",
  // Prop Firm Protection (owner-only; NOT in dashboard-auth OPEN_PATHS). Toggle +
  // account/rules CRUD + decision log. The gateway guard is the money-path layer.
  "/prop-protection",
  "/prop-accounts",
  "/prop-decisions",
  // LIVE 2-contract runner arming (owner-only; NOT in dashboard-auth OPEN_PATHS).
  // In-memory armed flag over the existing fail-closed gateway; resets on restart.
  "/live-runner",
  // AUTO EARLY-EXIT arming (owner-only; NOT in dashboard-auth OPEN_PATHS).
  // In-memory armed flag; when armed the watcher flattens a bot position whose
  // thesis is confirmed-invalid via a NON-REVERSING exit. Resets OFF on restart.
  "/auto-exit",
  // Scalping Strategy Research Engine (owner-only; RESEARCH/DISPLAY-ONLY; NOT in
  // dashboard-auth OPEN_PATHS). GET = cached research view; POST = trigger recompute.
  // Walled off from the live money path — new strategies never auto-trade live.
  "/scalp-research",
  // AutoSearch — Karpathy-style hypothesis training loop (owner-only;
  // RESEARCH/DISPLAY-ONLY; NOT in dashboard-auth OPEN_PATHS). Generate hypotheses
  // from trade history, score historically, ghost-validate forward, manually promote
  // validated insights to the Main Brain. Never trades or modifies the gate.
  "/autosearch",
  "/autosearch/generate",
  "/autosearch/rescore",
  "/autosearch/add",
  "/autosearch/promote/:hyp_key",
  "/autosearch/reject/:hyp_key",
  // BOT TRAINING MODE proof metrics (owner-only; DISPLAY/READ-ONLY; NOT in
  // dashboard-auth OPEN_PATHS). Never sends or mutates — staged-controller state +
  // paper-graded performance of recorded suggestions.
  "/training/status",
  "/training/metrics",
  // BOT TRAINING MODE stage control (owner-only; the ONE mutating training route;
  // NOT in dashboard-auth OPEN_PATHS). POST sets bot_training_state.stage (1-4):
  // Stage 4 lets live orders reach the broker; 1-3 are suggest-only. Owner-only via
  // Basic Auth + CSRF here; it only sets the value the existing gate already reads.
  "/training/stage",
  // TRADING ACADEMY / AI Trading Library (owner-only; LEARNING-ONLY; NOT in
  // dashboard-auth OPEN_PATHS). Knowledge sources + AI extraction + strategy
  // playbook + validation lifecycle + honest backtest links + Q&A. Walled off
  // from the live money path — nothing here can place or modify a trade.
  "/academy/sources",
  "/academy/sources/:id",
  "/academy/sources/:id/extract",
  "/academy/sources/:id/status",
  "/academy/strategies",
  "/academy/strategies/:id",
  "/academy/strategies/:id/status",
  "/academy/strategies/:id/backtest",
  "/academy/rules",
  "/academy/rules/:id",
  "/academy/rules/:id/status",
  "/academy/metrics",
  "/academy/ask",
  // Persistent Market Thesis (Phase 2 + Phase 3) — owner-only; DISPLAY-ONLY; NOT in
  // dashboard-auth OPEN_PATHS. Returns thesis snapshots, history, and Phase-3
  // shadow-validation stats / stale-setup markers.
  "/thesis",
  "/thesis/stats",
  "/thesis/stale",
  "/thesis/:instrument",
  "/thesis/:instrument/history",
  // Databento live feed — display-only market data endpoints.
  // /databento-bars: 1-minute OHLCV bars for the dashboard live chart.
  // /databento-status: connection health + per-instrument telemetry.
  // /main-brain/chart: unified chart endpoint — OHLCV + VWAP + structure events
  //   + active-trade overlay, with optional timeframe aggregation (1m/5m/15m).
  // /main-brain/tick-stream: SSE stream of individual trade ticks — one JSON
  //   event per trade, used by the dashboard real-time chart (EventSource).
  //   Added to OPEN_PATHS in dashboard-auth.ts because EventSource cannot
  //   send Authorization headers; the route returns only live price ticks
  //   (no account data, no credentials).
  // All return {"ok":false,"enabled":false} when DATABENTO_ENABLED=0 so the
  // UI can distinguish "feed off" from "route missing".
  "/databento-bars",
  "/databento-status",
  "/main-brain/chart",
  // SSE stream — EventSource; Flask validates the short-lived ?token= param
  "/main-brain/tick-stream",
  // SSE token issuance — auth-protected at Express edge; issues a 45s token
  // that the browser uses once to authenticate the EventSource connection.
  "/main-brain/tick-stream-token",
  // SSE diagnostics — subscriber counts, queue depths, drop counters, limits
  "/main-brain/tick-stream/diagnostics",
  // Right Brain status — training log, PF, mode, last per-instrument eval.
  // DISPLAY/READ-ONLY; owner-only; NOT in dashboard-auth OPEN_PATHS.
  "/right-brain",
  // Phase 5E: Source attribution session analytics report.
  // RESEARCH/DISPLAY-ONLY; owner-only; NOT in dashboard-auth OPEN_PATHS.
  // Returns a read-only analytics report from the in-memory ring buffer —
  // source distribution, duplicate evidence stats, component correlation,
  // evidence age, and three key research findings. Never modifies any state.
  "/source-analytics",
  // Phase 5F: Decision quality & signal calibration analytics.
  // RESEARCH/DISPLAY-ONLY; owner-only; NOT in dashboard-auth OPEN_PATHS.
  // Reads from decision_snapshots DB table — component win rates, duplicate
  // evidence impact, evidence age by outcome, and recommendations. Never
  // modifies scoring, gate, learning, or any production store.
  "/decision-quality",
  // Phase 6: Strategy scan coverage diagnostics.
  // DIAGNOSTICS/DISPLAY-ONLY; owner-only; NOT in dashboard-auth OPEN_PATHS.
  // Returns STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER (last-scan snapshot per ticker).
  // Never triggers strategy evaluation or mutates any scoring/production state.
  "/strategy-scan-diagnostics",
  // Phase 1B shadow validation: Left Brain Market Intelligence.
  // VWAP source-authority diagnostics per instrument (GET only, read-only).
  "/lb-thesis",
  "/lb-thesis-obs",
  "/lb-vwap-authority",
  // Full Left Brain MI shadow report: timing, classification distribution,
  // flip-rate stats, output validation, VWAP authority. Display-only.
  "/lb-shadow-report",
  // Main Brain read-only aggregation (Phase 7B — owner-only; DISPLAY/READ-ONLY;
  // NOT in dashboard-auth OPEN_PATHS). Assembles the versioned Main Brain
  // payload from canonical V1 interfaces. Never mutates trading state.
  "/main-brain",
  // ── Execution Arm / Disarm Control ──────────────────────────────────────────
  // Owner-only; NOT in dashboard-auth OPEN_PATHS (Basic Auth + CSRF apply).
  // /execution/state   — GET  current arm state (sanitized, no secrets)
  // /execution/enable  — POST enable the execution software switch
  // /execution/disable — POST disable the execution software switch + disarm
  // /execution/arm     — POST arm the system (requires exact confirmation phrase)
  // /execution/disarm  — POST disarm immediately (blocks new entries)
  // /execution/kill-switch — POST safety-lock (requires separate reset)
  // /execution/reset-safety-lock — POST reset the kill-switch lock
  // /execution/audit-log — GET recent arm-state-change audit records
  "/execution/state",
  "/execution/enable",
  "/execution/disable",
  "/execution/arm",
  "/execution/disarm",
  "/execution/kill-switch",
  "/execution/reset-safety-lock",
  "/execution/audit-log",
  "/execution/set-mode",
  // Profitability Engine Phase 1 — ghost observation ledger (RESEARCH/DISPLAY-ONLY;
  // owner-only; NOT in dashboard-auth OPEN_PATHS). Aggregated stats + raw
  // observation list. Never touches gate, scoring, sizing, or execution.
  "/profitability/summary",
  "/profitability/observations",
  // Phase 8A: Edge Ledger diagnostics — signal-vs-management accounting
  // (RESEARCH/DISPLAY-ONLY; owner-only; NOT in dashboard-auth OPEN_PATHS).
  // Shows per-strategy signal outcome vs managed outcome comparison.
  // Never touches gate, learning weights, scoring, or execution.
  "/edge-ledger/diagnostics",
  // Phase 8B: Operations Readiness — research engine health snapshot + event feed
  // (DISPLAY-ONLY; owner-only; NOT in dashboard-auth OPEN_PATHS).
  // Never touches gate, scoring, sizing, learning, or execution.
  "/research-health",
  "/research-events",
  // Research Operations panel — lightweight aggregated status for GRE/FVG/SCALP/IT
  // engines: observation counts, evidence-state breakdown, READY_FOR_REVIEW queue.
  // DISPLAY-ONLY; owner-only; NOT in OPEN_PATHS. Never touches gate or execution.
  "/research-ops",
  // Phase 8C: Gate Effectiveness Audit endpoints (DISPLAY/MEASUREMENT-ONLY; owner-only).
  // Never touches gate, scoring, sizing, learning, arm state, or execution.
  "/gate-effectiveness",
  "/gate-effectiveness/validate-wiring",
  "/gate-effectiveness/missed-winners",
  "/gate-effectiveness/saved-losses",
  // Phase 8C unified pipeline — mode-separated reports + deduplicated opportunities.
  "/gate-effectiveness/mode-report",
  "/gate-effectiveness/mode-comparison",
  "/gate-effectiveness/opportunities",
  // Phase 8C settlement-health: watcher diagnostic state + outcome_status counts.
  // Backfill: one-time POST to settle stale EXPIRED/PENDING observations.
  // Both DISPLAY/RESEARCH ONLY — never touches gate, execution, or risk.
  "/gate-effectiveness/settlement-health",
  "/gate-effectiveness/backfill",
  // Phase 8C MODE→STRATEGY→GATE→OUTCOME funnel analytics.
  "/gate-effectiveness/strategy-report",
  // Phase 10 SCALP Feedback Loop research endpoints (DISPLAY/RESEARCH ONLY;
  // owner-only; NOT in dashboard-auth OPEN_PATHS).
  // scalp-feedback-health: pipeline health snapshot (vwap coverage, strategy
  // identity coverage, ghost_observations webhook count, outcome breakdown).
  // shadow-cohorts: per-cohort win-rate analytics for BLOCKED SCALP records.
  // Neither endpoint touches gate, scoring, sizing, learning, or execution.
  "/gate-effectiveness/scalp-feedback-health",
  "/gate-effectiveness/shadow-cohorts",
  // Visual Brain V1 — MNQ 1-minute stateful market observer (SHADOW/DISPLAY-ONLY;
  // owner-only; NOT in dashboard-auth OPEN_PATHS). Captures MNQ chart screenshots,
  // sends to vision LLM, persists structured market-state observations. NEVER
  // touches gate, scoring, sizing, learning, arm state, or execution.
  "/visual-brain/status",
  "/visual-brain/history",
  "/visual-brain/cost",
  "/visual-brain/all-status",
  // Phase 8B.1: Multi-Timeframe Trend Alignment endpoint (DISPLAY-ONLY).
  // Returns 4H/15M trend states from Databento 1m bar resampling.
  "/market/trend-alignment",
  // Canonical Market State Engine (shadow/DISPLAY-ONLY; owner-only; NOT in
  // dashboard-auth OPEN_PATHS).  Returns per-instrument Databento-computed
  // VWAP/ATR/structure/CVD/RVOL/trend snapshot with source-comparison metadata.
  // All six selectors default to "legacy" — no promotion is possible this phase.
  "/canonical-market-state",
  // Phase 2 Ghost Research Engine — RESEARCH/DISPLAY-ONLY; owner-only; NOT in
  // dashboard-auth OPEN_PATHS. Observes OrbEngine BREAKOUT_DETECTED transitions,
  // creates up to 10 ghost experiment variants per opportunity, and tracks
  // outcomes on real Databento bars. NEVER touches gate, scoring, sizing,
  // learning weights, or execution. All READY_FOR_REVIEW findings require
  // deliberate human operator action before any live change.
  "/ghost-research/health",
  "/ghost-research/candidates",
  "/ghost-research/experiments",
  "/ghost-research/candidate/:experiment_id",
  "/ghost-research/opportunity/:opportunity_id",
  "/ghost-research/baseline-vs-variant",
  "/ghost-research/ready-for-review",
  // Phase 3 Canonical Decision Contract — SHADOW/AUDIT-ONLY; owner-only; NOT in
  // dashboard-auth OPEN_PATHS. Per-instrument canonical state machine records +
  // transition history + parity-mismatch flags. NEVER touches gate, scoring,
  // sizing, learning, or execution. Shadow mode only until explicitly promoted.
  "/decision-state",
  // Structure-event deduplication counters — DISPLAY/AUDIT-ONLY; owner-only; NOT in
  // dashboard-auth OPEN_PATHS.  Reports since-restart cross-source dedup statistics
  // (TV events received, Databento events produced, matched/deduped, fallbacks,
  // conflicts). NEVER touches gate, scoring, sizing, learning, or execution.
  "/structure-dedup-metrics",
];

// ANALYSIS-ONLY bot (artifacts/analysis-bot), seeded from the June-21 snapshot.
// ONLY the routes that snapshot actually serves — a strict subset of BOT1_ROUTES.
// The newer live-bot endpoints (auto-trade, advisor, tradezella, prop-protection,
// academy, …) simply don't exist in this build, so they are intentionally absent.
export const BOT2_ROUTES = [
  "/",
  "/ping",
  "/webhook",
  "/mode",
  "/alerts",
  "/diagnostics",
  "/diagnostics-live",
  "/eval-metrics",
  "/status",
  "/price",
  "/clear",
  "/clear-fired-keys",
  "/enter",
  "/traderspost",
  "/breakeven",
  "/close",
  "/trade",
  "/journal",
  "/journal/",
  "/eod",
  "/weekly",
  "/why",
  "/why/:ticker",
  "/dashboard",
  "/backtest/upload",
  "/backtest/datasets",
  "/backtest/datasets/:id",
  "/backtest/run",
  "/backtest/optimize",
  "/backtest/runs/:id",
  "/backtest/export",
  "/backtest/coverage",
  "/backtest/baselines",
  "/backtest/baselines/generate",
  "/backtest/baselines/:baseline_id",
  "/backtest/baselines/:baseline_id/trades",
  "/backtest/baselines/:baseline_id/breakdowns",
  // Lord Piggington VRM model (static asset; no auth — fetched by Three.js in the dashboard).
  "/vrm",
];
