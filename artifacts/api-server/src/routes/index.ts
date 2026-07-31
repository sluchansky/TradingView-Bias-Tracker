import { Router, type IRouter } from "express";
import healthRouter from "./health";
import { dashboardAuth } from "./dashboard-auth";
import { createFlaskProxy, BOT1_ROUTES, BOT2_ROUTES } from "./flask-proxy";
import { mintLinkToken, randomPassword } from "./view-tokens";
import journalAttachmentsRouter from "./journal-attachments";

// LIVE trading bot — mounted at /api (Flask on port 8000). Behavior unchanged.
const router: IRouter = Router();
router.use(healthRouter);
router.use(dashboardAuth);

// Mint a watch-only, expiring, password-protected dashboard link. ADMIN-ONLY:
// it sits AFTER dashboardAuth (so the owner's password + CSRF check are enforced)
// and BEFORE createFlaskProxy, and it is NOT in BOT1_ROUTES — so it is handled
// locally in Express and never proxied to Flask. POST keeps the chosen password
// out of URLs/history. Returns { url, path, password, expiresAt }.
router.post("/view-link", (req, res) => {
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

  const host = String(req.headers["x-forwarded-host"] || req.headers.host || "")
    .split(",")[0]
    .trim();
  const proto = String(req.headers["x-forwarded-proto"] || "https")
    .split(",")[0]
    .trim();
  const path = `/view?t=${encodeURIComponent(token)}`;
  const url = host ? `${proto}://${host}${path}` : path;
  res.json({ url, path, password, expiresAt: exp });
});

// Journal attachment routes — Express-native (GCS + DB).  Sit before the Flask
// proxy so they are handled locally.  dashboardAuth is already applied above.
router.use(journalAttachmentsRouter);

router.use(createFlaskProxy({ port: 8000, routes: BOT1_ROUTES }));

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
