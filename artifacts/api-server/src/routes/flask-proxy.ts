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
      // Forward Flask's caching directives. The /dashboard route serves inline JS
      // that changes on every deploy and must be served no-store, otherwise the
      // browser can run a stale cached dashboard and appear "frozen" on toggles.
      for (const h of ["cache-control", "pragma", "expires"]) {
        const v = proxyRes.headers[h];
        if (v) res.set(h, Array.isArray(v) ? v.join(", ") : v);
      }
      proxyRes.pipe(res);
    });

    proxyReq.on("error", () => {
      res.status(502).json({ error: "Webhook server unreachable" });
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
  // Stop-managing: owner-only local-tracking flush. Clears a stale tracked /
  // managed / monitored position so the bot stops showing a trade the user
  // already closed elsewhere. TRACKING-ONLY — never sends a broker order (NOT in
  // dashboard-auth OPEN_PATHS).
  "/stop-managing",
  "/trade",
  "/clear",
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
  // TradeZella journal import + review (owner-only; NOT in dashboard-auth
  // OPEN_PATHS). The raw body limit for the CSV upload is raised in app.ts.
  "/tradezella/upload",
  "/tradezella/analysis",
  "/tradezella/trades",
  "/tradezella/reset",
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
  // Both return {"ok":false,"enabled":false} when DATABENTO_ENABLED=0 so the
  // UI can distinguish "feed off" from "route missing".
  "/databento-bars",
  "/databento-status",
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
  // Lord Piggington VRM model (static asset; no auth — fetched by Three.js in the dashboard).
  "/vrm",
];
