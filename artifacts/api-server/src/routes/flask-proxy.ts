import { Router } from "express";
import http from "http";

const router = Router();

function proxyToFlask(req: any, res: any) {
  const flaskPath = req.path === "/" ? "/" : req.path;
  const query = Object.keys(req.query).length
    ? "?" + new URLSearchParams(req.query as Record<string, string>).toString()
    : "";

  const bodyStr =
    req.body && Object.keys(req.body).length > 0
      ? JSON.stringify(req.body)
      : "";

  const headers: Record<string, string> = {
    "content-type": "application/json",
  };
  if (bodyStr) {
    headers["content-length"] = Buffer.byteLength(bodyStr).toString();
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
    proxyRes.pipe(res);
  });

  proxyReq.on("error", () => {
    res.status(502).json({ error: "Webhook server unreachable" });
  });

  if (bodyStr) {
    proxyReq.write(bodyStr);
  }
  proxyReq.end();
}

router.all(
  [
    "/",
    "/ping",
    "/webhook",
    "/enter",
    "/breakeven",
    "/close",
    "/trade",
    "/clear",
    "/price",
    "/alerts",
    "/status",
    "/mode",
    "/journal",
    "/journal/",
    "/eod",
    "/broker/status",
    "/broker/toggle",
    "/broker/test",
    "/dashboard",
  ],
  proxyToFlask,
);

export default router;
