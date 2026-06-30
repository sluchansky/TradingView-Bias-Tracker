import { Router } from "express";
import http from "http";

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
    port: 8000,
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

router.all(
  [
    "/",
    "/ping",
    "/webhook",
    "/enter",
    "/traderspost",
    "/breakeven",
    "/close",
    "/trade",
    "/clear",
    "/price",
    "/alerts",
    "/diagnostics",
    "/diagnostics-live",
    "/eval-metrics",
    "/status",
    "/mode",
    "/alerts/mute",
    "/auto-trade",
    "/advisor",
    "/pro-review",
    "/trade-debate",
    "/learning",
    "/learning-score",
    "/entry-quality",
    "/review-idea",
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
    // Scalping Strategy Research Engine (owner-only; RESEARCH/DISPLAY-ONLY; NOT in
    // dashboard-auth OPEN_PATHS). GET = cached research view; POST = trigger recompute.
    // Walled off from the live money path — new strategies never auto-trade live.
    "/scalp-research",
    // BOT TRAINING MODE proof metrics (owner-only; DISPLAY/READ-ONLY; NOT in
    // dashboard-auth OPEN_PATHS). Never sends or mutates — staged-controller state +
    // paper-graded performance of recorded suggestions.
    "/training/status",
    "/training/metrics",
  ],
  proxyToFlask,
);

export default router;
