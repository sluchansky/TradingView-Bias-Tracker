import express, { type Express } from "express";
import pinoHttp from "pino-http";
import router, { api2Router } from "./routes";
import { createViewOnlyRouter } from "./routes/view-only";
import { logger } from "./lib/logger";

const app: Express = express();
app.disable("x-powered-by");

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
// The dashboard, API proxy, and webhook receiver are all same-origin. Do not
// grant arbitrary websites browser access to protected responses via permissive
// CORS headers. TradingView sends server-to-server webhooks and needs no CORS.
app.use((_req, res, next) => {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("Referrer-Policy", "same-origin");
  res.setHeader("Permissions-Policy", "camera=(), geolocation=(), microphone=()");
  if (process.env.NODE_ENV === "production") {
    res.setHeader("Strict-Transport-Security", "max-age=31536000; includeSubDomains");
  }
  next();
});
// Capture the raw request body for EVERY content type (as a Buffer) so the
// Flask proxy can forward it verbatim. TradingView posts webhook alerts as
// text/plain, which express.json() silently ignores — leaving req.body empty
// and the alert dropped before it ever reaches Flask. express.raw with a
// catch-all type buffers the bytes without attempting (and failing) to parse
// non-JSON payloads. Flask does all parsing (get_json(force=True) + raw-text
// fallback), so the proxy only needs to relay the original bytes + content-type.
//
// Large bodies are needed by exactly TWO authenticated endpoints — the backtest
// CSV upload (e.g. a year of 1-minute bars) and the TradeZella journal CSV
// upload. Scope the big limit to those single paths so the many other (and the
// open) /api endpoints don't buffer multi-MB payloads; a global 64mb cap was an
// unauthenticated memory/availability surface. Body-parser marks req._body once
// consumed, so the tight global parser below is a no-op on the upload paths (no
// double read). Webhook payloads remain tiny.
app.use(
  [
    "/api/backtest/upload",
    "/api/tradezella/upload",
    "/api2/backtest/upload",
    // Journal CSV import — broker exports can exceed the 1 MB global limit.
    // Preview parses the raw CSV server-side and issues a tamper-proof token;
    // the body never reaches Flask's confirm route, so scoping here is safe.
    "/api/journal/import/preview",
  ],
  express.raw({ type: () => true, limit: "32mb" }),
);
// Journal screenshot uploads — up to 5 MB per image, owner-only paths.
// Dynamic paths cannot be listed literally, so we match with a predicate.
// Covers both the legacy trade attachment route and the new NJ screenshot upload route.
app.use(
  (req, _res, next) => {
    if (
      req.method === "POST" &&
      (
        /^\/api\/journal\/trade\/[^/]+\/\d+\/attachment$/.test(req.path) ||
        /^\/api\/journal\/native-trades\/[^/]+\/screenshots\/upload$/.test(req.path)
      )
    ) {
      express.raw({ type: () => true, limit: "5mb" })(req, _res, next);
    } else {
      next();
    }
  },
);
app.use(express.raw({ type: () => true, limit: "1mb" }));

app.use("/api", router);
app.use("/api2", api2Router);

// Watch-only, expiring, password-protected shareable dashboard link. Self-contained
// auth (signed link token + view session cookie); deliberately NOT behind
// dashboardAuth. The only reachable data path is GET /view/api/status; everything
// else under /view/api is 403 fail-closed.
app.use("/view", createViewOnlyRouter());

export default app;
