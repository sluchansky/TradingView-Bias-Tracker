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
  ],
  proxyToFlask,
);

export default router;
