import { type RequestHandler } from "express";
import { timingSafeEqual } from "node:crypto";

// Paths (relative to the /api mount) that MUST stay open for machines:
//   "/"        -> deployment healthcheck + service index JSON
//   "/ping"    -> uptime monitoring (e.g. UptimeRobot)
//   "/webhook" -> TradingView alert delivery
// Everything else (the dashboard page, its data reads, and all trade
// mutations) requires the dashboard password.
const OPEN_PATHS = new Set(["/", "/ping", "/webhook"]);

function safeEqual(a: string, b: string): boolean {
  const ab = Buffer.from(a);
  const bb = Buffer.from(b);
  if (ab.length !== bb.length) return false;
  return timingSafeEqual(ab, bb);
}

export const dashboardAuth: RequestHandler = (req, res, next) => {
  if (OPEN_PATHS.has(req.path)) {
    next();
    return;
  }

  const password = process.env.DASHBOARD_PASSWORD;
  if (!password) {
    // Fail OPEN when no password is configured so the owner is never locked
    // out of their own dashboard by a missing secret — but make it loud.
    console.warn(
      "[auth] DASHBOARD_PASSWORD is not set — dashboard endpoints are UNLOCKED",
    );
    next();
    return;
  }

  const header = req.headers.authorization ?? "";
  if (header.startsWith("Basic ")) {
    const decoded = Buffer.from(header.slice(6), "base64").toString("utf8");
    const sep = decoded.indexOf(":");
    const pass = sep >= 0 ? decoded.slice(sep + 1) : "";
    if (pass.length > 0 && safeEqual(pass, password)) {
      next();
      return;
    }
  }

  res.set("WWW-Authenticate", 'Basic realm="AI Trading Partner", charset="UTF-8"');
  res.status(401).json({ error: "Authentication required" });
};
