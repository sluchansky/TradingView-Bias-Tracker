import express, { type Express } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import router from "./routes";
import { logger } from "./lib/logger";

const app: Express = express();

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
app.use(cors());
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
  ["/api/backtest/upload", "/api/tradezella/upload"],
  express.raw({ type: () => true, limit: "32mb" }),
);
app.use(express.raw({ type: () => true, limit: "1mb" }));

app.use("/api", router);

export default app;
