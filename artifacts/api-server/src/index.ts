import app from "./app";
import { logger } from "./lib/logger";
import { runMigrations } from "./lib/migrate";

const rawPort = process.env["PORT"];

if (!rawPort) {
  throw new Error(
    "PORT environment variable is required but was not provided.",
  );
}

const port = Number(rawPort);

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

// Apply all SQL migration files in lib/db/migrations/ before accepting traffic.
// Every migration uses CREATE TABLE IF NOT EXISTS so this is idempotent on restarts.
// A failed migration exits the process so the platform restarts cleanly.
runMigrations()
  .then(() => {
    const server = app.listen(port, (err) => {
      if (err) {
        logger.error({ err }, "Error listening on port");
        process.exit(1);
      }

      logger.info({ port }, "Server listening");
    });

    // SSE / long-lived connections: disable the server-level keep-alive
    // timeout so the Replit reverse-proxy cannot silently close idle SSE
    // streams (the default Node.js keep-alive timeout of 5 s is shorter than
    // most upstream proxy idle-connection timers and kills SSE within seconds).
    // headersTimeout must also be 0 — it fires if no request headers arrive
    // before the timeout and can terminate a keep-alive connection prematurely.
    server.keepAliveTimeout = 0;
    server.headersTimeout   = 0;
  })
  .catch((err) => {
    logger.error({ err }, "Database migration failed — aborting startup");
    process.exit(1);
  });
