import { type RequestHandler } from "express";
import { timingSafeEqual } from "node:crypto";

// Paths (relative to the /api mount) that MUST stay open for machines:
//   "/"        -> deployment healthcheck + service index JSON
//   "/ping"    -> uptime monitoring (e.g. UptimeRobot)
//   "/webhook" -> TradingView alert delivery
// Everything else (the dashboard page, its data reads, and all trade
// mutations) requires the dashboard password.
const OPEN_PATHS = new Set(["/", "/ping", "/webhook"]);

// Methods that do not change state — no CSRF (origin) check needed.
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

function safeEqual(a: string, b: string): boolean {
  const ab = Buffer.from(a);
  const bb = Buffer.from(b);
  if (ab.length !== bb.length) return false;
  return timingSafeEqual(ab, bb);
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
  if (!password) {
    // In development, fail OPEN so a missing secret never blocks local work.
    // Everywhere else (production/deployment) fail CLOSED — for a live trading
    // app an unconfigured password must lock the dashboard, not expose it.
    if (process.env.NODE_ENV === "development") {
      console.warn(
        "[auth] DASHBOARD_PASSWORD is not set — dashboard endpoints are UNLOCKED (development only)",
      );
      next();
      return;
    }
    console.error(
      "[auth] DASHBOARD_PASSWORD is not set — refusing dashboard access",
    );
    res.status(503).json({ error: "Dashboard authentication is not configured" });
    return;
  }

  // 1) Password gate (HTTP Basic Auth). Username is ignored; only the
  //    password is checked, with a timing-safe comparison.
  const header = req.headers.authorization ?? "";
  let authed = false;
  if (header.startsWith("Basic ")) {
    const decoded = Buffer.from(header.slice(6), "base64").toString("utf8");
    const sep = decoded.indexOf(":");
    const pass = sep >= 0 ? decoded.slice(sep + 1) : "";
    if (pass.length > 0 && safeEqual(pass, password)) authed = true;
  }
  if (!authed) {
    res.set("WWW-Authenticate", 'Basic realm="AI Trading Partner", charset="UTF-8"');
    res.status(401).json({ error: "Authentication required" });
    return;
  }

  // 2) CSRF gate for state-changing requests: must be same-origin. This stops
  //    a malicious site from using the owner's cached credentials to trigger
  //    mode changes or trades from another tab.
  if (!SAFE_METHODS.has(req.method.toUpperCase()) && !sameOrigin(req)) {
    res.status(403).json({ error: "Cross-origin request rejected" });
    return;
  }

  next();
};
