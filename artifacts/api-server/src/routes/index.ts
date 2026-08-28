import { Router, type IRouter } from "express";
import { isIP } from "node:net";
import healthRouter from "./health";
import { dashboardAuth } from "./dashboard-auth";
import {
  createFlaskProxy,
  BOT1_ROUTES,
  BOT2_ROUTES,
  resolveFlaskPort,
} from "./flask-proxy";
import { checkPublishedWatchHost } from "./view-only";
import { mintLinkToken, randomPassword } from "./view-tokens";
import journalAttachmentsRouter from "./journal-attachments";
import njScreenshotsRouter from "./nj-screenshots";

// LIVE trading bot — mounted at /api (Flask on port 8000 by default).
// FLASK_PORT is intentionally explicit for the Windows dashboard launcher;
// hosted startup leaves it unset and retains the established topology.
export const LIVE_FLASK_PORT = resolveFlaskPort(process.env.FLASK_PORT);

function parsedOrigin(
  value: string,
  options: { allowHttp?: boolean; allowPrivate?: boolean } = {},
): string | null {
  try {
    const parsed = new URL(value);
    if (
      (parsed.protocol !== "https:" && !(options.allowHttp && parsed.protocol === "http:"))
      || parsed.username
      || parsed.password
      || parsed.pathname !== "/"
      || parsed.search
      || parsed.hash
      || !parsed.hostname
      || (
        !options.allowPrivate
        && (
          isIP(parsed.hostname) !== 0
          || parsed.hostname === "localhost"
          || parsed.hostname.endsWith(".localhost")
          || parsed.hostname.endsWith(".local")
        )
      )
    ) {
      return null;
    }
    return parsed.origin;
  } catch {
    return null;
  }
}

function publishedOrigin(
  req: { headers: Record<string, unknown> },
  canonicalOrigin?: string,
): string | null {
  if (canonicalOrigin) {
    return parsedOrigin(canonicalOrigin, { allowHttp: true, allowPrivate: true });
  }
  if (process.env.REPLIT_DEPLOYMENT !== "1") return null;

  const forwardedHostRaw = req.headers["x-forwarded-host"];
  const hostRaw = req.headers.host;
  const protoRaw = req.headers["x-forwarded-proto"];
  if (
    Array.isArray(forwardedHostRaw)
    || Array.isArray(hostRaw)
    || Array.isArray(protoRaw)
  ) {
    return null;
  }
  const forwardedHost = String(forwardedHostRaw ?? "").trim().toLowerCase();
  const host = String(hostRaw ?? "").trim().toLowerCase();
  const proto = String(protoRaw ?? "").trim().toLowerCase();
  if (
    !forwardedHost
    || forwardedHost.includes(",")
    || forwardedHost !== host
    || proto !== "https"
  ) {
    return null;
  }
  return parsedOrigin(`https://${forwardedHost}`);
}

export function createLiveBotRouter(
  liveFlaskPort = LIVE_FLASK_PORT,
  canonicalWatchOrigin?: string,
): IRouter {
  const router: IRouter = Router();
  router.use(healthRouter);
  router.use(dashboardAuth);

  // Lightweight browser-regression probe. Reaching this handler proves the
  // request crossed dashboardAuth; it deliberately performs no Flask, DB, or
  // trading work and returns no credential-derived data.
  router.get("/operator-console-auth-check", (_req, res) => {
    res.status(204).end();
  });

// Mint a watch-only, expiring, password-protected dashboard link. ADMIN-ONLY:
// it sits AFTER dashboardAuth (so the owner's password + CSRF check are enforced)
// and BEFORE createFlaskProxy, and it is NOT in BOT1_ROUTES — so it is handled
// locally in Express and never proxied to Flask. POST keeps the chosen password
// out of URLs/history. Returns { url, path, password, expiresAt }.
  router.post("/view-link", async (req, res) => {
    const origin = publishedOrigin(req, canonicalWatchOrigin);
    if (!origin) {
      res.status(503).json({
        error: "publish_unreachable",
        message: "The public watch page could not be verified, so no link was created.",
        publish: { ok: false, reason: "unreachable" },
      });
      return;
    }

    const publish = await checkPublishedWatchHost(origin);
    if (!publish.ok) {
      const stale = publish.reason === "stale";
      res.status(stale ? 409 : 503).json({
        error: stale ? "publish_stale" : "publish_unreachable",
        message: stale
          ? "The published watch page is stale or missing the current read-only protection. No link was created."
          : "The public watch page did not respond, so no link was created.",
        publish,
      });
      return;
    }

    const buf: Buffer = Buffer.isBuffer(req.body) ? req.body : Buffer.alloc(0);
    let body: any = {};
    try {
      body = buf.length ? JSON.parse(buf.toString("utf8")) : {};
    } catch {
      res.status(400).json({ error: "invalid JSON" });
      return;
    }
    let ttl = Number(body.ttlSeconds);
    if (!Number.isFinite(ttl) || ttl <= 0) ttl = 24 * 3600;
    ttl = Math.min(ttl, 30 * 24 * 3600); // cap at 30 days
    const chosen = typeof body.password === "string" ? body.password : "";
    const password = chosen.length >= 4 ? chosen : randomPassword();
    const { token, exp } = mintLinkToken(ttl, password);

    const path = `/view?t=${encodeURIComponent(token)}`;
    const url = `${origin}${path}`;
    res.json({ url, path, password, expiresAt: exp, publish });
  });

// Journal attachment routes — Express-native (GCS + DB).  Sit before the Flask
// proxy so they are handled locally.  dashboardAuth is already applied above.
  router.use(journalAttachmentsRouter);

// NJ screenshot routes — upload/serve/delete backed by GCS.  Express generates
// server-side keys; Flask JSONB metadata is registered internally (never from client).
// Mounted before Flask proxy so Express intercepts DELETE and GET for screenshots.
  router.use(njScreenshotsRouter);

  router.use(createFlaskProxy({ port: liveFlaskPort, routes: BOT1_ROUTES }));
  return router;
}

const router = createLiveBotRouter();

// ANALYSIS-ONLY bot — mounted at /api2 (Flask on port 8001). Same dashboard
// password (dashboardAuth) and same open paths (/, /ping, /webhook); it proxies
// ONLY the June-21 snapshot's routes and never shares the live bot's port. The
// analysis bot itself (ANALYSIS_ONLY=1) cannot place orders or post to Discord
// and confines its DB access to the isolated `analysis_bot` schema.
const api2Router: IRouter = Router();
api2Router.use(dashboardAuth);
api2Router.use(createFlaskProxy({ port: 8001, routes: BOT2_ROUTES }));

export { api2Router };
export default router;
