import express, { type Express } from "express";
import { createFlaskProxy, BOT1_ROUTES } from "./routes/flask-proxy";
import { dashboardAuth } from "./routes/dashboard-auth";

const rawPort = process.env["PORT"];
const rawFlaskPort = process.env["FLASK_PORT"] ?? "8000";

if (!rawPort) {
  throw new Error("PORT environment variable is required but was not provided.");
}

const port = Number(rawPort);
if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}
const flaskPort = Number(rawFlaskPort);
if (!Number.isInteger(flaskPort) || flaskPort <= 0 || flaskPort > 65535) {
  throw new Error(`Invalid FLASK_PORT value: "${rawFlaskPort}"`);
}

/**
 * Windows-local dashboard bridge.
 *
 * This is deliberately narrower than the artifact API entry point: it uses the
 * established Express auth + Flask proxy for /api, but does not import database
 * routes, Object Storage routes, or the migration runner. It lets a local Vite
 * dashboard reach the exact Flask process that owns the Databento in-memory bar
 * cache without creating or mutating any database state.
 */
const app: Express = express();
app.disable("x-powered-by");
app.use((_req, res, next) => {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("Referrer-Policy", "same-origin");
  res.setHeader("Permissions-Policy", "camera=(), geolocation=(), microphone=()");
  next();
});

// Preserve Flask's webhook/body parsing contract for all supported proxy paths.
app.use(express.raw({ type: () => true, limit: "1mb" }));
app.use("/api", dashboardAuth);
app.use("/api", createFlaskProxy({ port: flaskPort, routes: BOT1_ROUTES }));

const server = app.listen(port, "127.0.0.1", (err) => {
  if (err) {
    console.error("[windows-local-proxy] failed to listen", err);
    process.exit(1);
  }
  console.info(`[windows-local-proxy] listening on http://127.0.0.1:${port}/api`);
});

// Match the established SSE behavior so current-candle EventSource updates are
// not closed by Node's default short keep-alive timeout.
server.keepAliveTimeout = 0;
server.headersTimeout = 0;