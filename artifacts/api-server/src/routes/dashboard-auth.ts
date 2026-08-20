import { type RequestHandler } from "express";
import { timingSafeEqual } from "node:crypto";

// Paths (relative to the /api mount) that MUST stay open for machines:
//   "/"        -> deployment healthcheck + service index JSON
//   "/ping"    -> uptime monitoring (e.g. UptimeRobot)
//   "/webhook" -> TradingView alert delivery
// Everything else (the dashboard page, its data reads, and all trade
// mutations) requires the dashboard password.
const OPEN_PATHS = new Set([
  "/",
  "/ping",
  "/webhook",
  "/vrm",
  // SSE tick stream — EventSource cannot send Authorization headers, so Express
  // cannot enforce Basic-auth on this route.  Security is provided at the Flask
  // layer via a short-lived cryptographic token: the browser first POSTs to
  // /main-brain/tick-stream-token (which IS auth-protected at this Express edge),
  // receives a 45-second token, and then opens EventSource with ?token=<tok>.
  // Flask rejects all tokenless/expired/wrong-instrument requests with 401 before
  // allocating any subscriber queue — anonymous clients are rejected at the gate.
  "/main-brain/tick-stream",
]);

// Methods that do not change state — no CSRF (origin) check needed.
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const AUTH_WINDOW_MS = 15 * 60 * 1000;
const AUTH_MAX_FAILURES = 8;
const AUTH_ATTEMPTS = new Map<string, { count: number; resetAt: number }>();

function safeEqual(a: string, b: string): boolean {
  const ab = Buffer.from(a);
  const bb = Buffer.from(b);
  if (ab.length !== bb.length) return false;
  return timingSafeEqual(ab, bb);
}

function authAttemptKey(req: { headers: Record<string, unknown>; socket?: { remoteAddress?: string } }): string {
  // Replit's proxy supplies the client chain in x-forwarded-for. Use only its
  // first address and cap its length so this in-memory protection cannot itself
  // become an unbounded-memory input.
  const forwarded = req.headers["x-forwarded-for"];
  const first = Array.isArray(forwarded) ? forwarded[0] : forwarded;
  const ip = String(first ?? req.socket?.remoteAddress ?? "unknown")
    .split(",")[0]
    .trim()
    .slice(0, 80);
  return ip || "unknown";
}

function authRateLimited(key: string, now = Date.now()): boolean {
  const entry = AUTH_ATTEMPTS.get(key);
  if (!entry) return false;
  if (now >= entry.resetAt) {
    AUTH_ATTEMPTS.delete(key);
    return false;
  }
  return entry.count >= AUTH_MAX_FAILURES;
}

function noteAuthFailure(key: string, now = Date.now()): void {
  const entry = AUTH_ATTEMPTS.get(key);
  if (!entry || now >= entry.resetAt) {
    AUTH_ATTEMPTS.set(key, { count: 1, resetAt: now + AUTH_WINDOW_MS });
  } else {
    entry.count += 1;
  }
  if (AUTH_ATTEMPTS.size > 10_000) {
    for (const [oldKey, oldEntry] of AUTH_ATTEMPTS) {
      if (now >= oldEntry.resetAt) AUTH_ATTEMPTS.delete(oldKey);
    }
  }
}

function clearAuthFailures(key: string): void {
  AUTH_ATTEMPTS.delete(key);
}

// The public host(s) the browser actually connected to. Replit's proxy sets
// BOTH x-forwarded-host (overwriting any client-supplied value — verified) and
// Host to the real public host (dev domain or custom domain), and a forged
// Host is rejected upstream. Both are therefore proxy-controlled and cannot be
// spoofed by a malicious page, so we accept a match against either — robust to
// whichever header carries the public host in dev vs. prod, with no hardcoding.
export function candidateHosts(req: { headers: Record<string, unknown> }): Set<string> {
  const out = new Set<string>();
  const add = (v: unknown) => {
    if (!v) return;
    for (const part of String(v).split(",")) {
      const h = part.trim().toLowerCase();
      if (h) out.add(h);
    }
  };
  add(req.headers["x-forwarded-host"]);
  add(req.headers.host);
  return out;
}

// CSRF defense: a state-changing request is only allowed when its Origin
// (or Referer) host matches the host the request was sent to. A malicious
// site forging a request from the owner's browser carries its own origin,
// which will not match — so it is rejected.
export function sameOrigin(req: { headers: Record<string, unknown> }): boolean {
  const hosts = candidateHosts(req);
  if (hosts.size === 0) return false;
  const src = (req.headers.origin ?? req.headers.referer) as string | undefined;
  if (!src) return false;
  try {
    return hosts.has(new URL(src).host.toLowerCase());
  } catch {
    return false;
  }
}

export const dashboardAuth: RequestHandler = (req, res, next) => {
  if (OPEN_PATHS.has(req.path)) {
    next();
    return;
  }

  const password = process.env.DASHBOARD_PASSWORD;
  const username = process.env.DASHBOARD_USERNAME || "admin";
  if (!password) {
    console.error(
      "[auth] DASHBOARD_PASSWORD is not set — refusing dashboard access",
    );
    res.status(503).json({ error: "Dashboard authentication is not configured" });
    return;
  }

  // 1) HTTP Basic Auth gate. Both the configured username and password are
  // checked using constant-time comparisons. The username defaults to "admin"
  // so existing operator clients remain compatible unless explicitly changed.
  const header = req.headers.authorization ?? "";
  // A browser on the login screen may poll protected data before a user has
  // supplied credentials. That is not a login attempt and must not exhaust the
  // brute-force budget; only malformed or invalid Basic credentials count.
  const hasCredentialAttempt = header.startsWith("Basic ");
  const attemptKey = authAttemptKey(req);
  if (hasCredentialAttempt && authRateLimited(attemptKey)) {
    res.set("Retry-After", String(Math.ceil(AUTH_WINDOW_MS / 1000)));
    res.status(429).json({ error: "Too many authentication attempts. Try again later." });
    return;
  }
  let authed = false;
  if (header.startsWith("Basic ")) {
    try {
      const decoded = Buffer.from(header.slice(6), "base64").toString("utf8");
      const sep = decoded.indexOf(":");
      const user = sep >= 0 ? decoded.slice(0, sep) : "";
      const pass = sep >= 0 ? decoded.slice(sep + 1) : "";
      authed = user.length > 0
        && pass.length > 0
        && safeEqual(user, username)
        && safeEqual(pass, password);
    } catch {
      authed = false;
    }
  }
  if (!authed) {
    if (hasCredentialAttempt) noteAuthFailure(attemptKey);
    res.set("WWW-Authenticate", 'Basic realm="AI Trading Partner", charset="UTF-8"');
    res.status(401).json({ error: "Authentication required" });
    return;
  }
  clearAuthFailures(attemptKey);

  // 2) CSRF gate for state-changing requests: must be same-origin. This stops
  //    a malicious site from using the owner's cached credentials to trigger
  //    mode changes or trades from another tab.
  if (!SAFE_METHODS.has(req.method.toUpperCase()) && !sameOrigin(req)) {
    res.status(403).json({ error: "Cross-origin request rejected" });
    return;
  }

  next();
};
