import http from "node:http";
import express from "express";
import request from "supertest";
import { describe, expect, it } from "vitest";
import { createLiveBotRouter } from "./index";

const DASHBOARD_PASSWORD = "operator-controls-proxy-test-password";

const actions = [
  {
    name: "execution",
    method: "POST" as const,
    path: "/api/traderspost",
    body: { ticker: "MGC", contracts: 1 },
    upstreamPath: "/traderspost",
  },
  {
    name: "journal",
    method: "PATCH" as const,
    path: "/api/journal/trade/native/42/review",
    body: { overall_quality: 1 },
    upstreamPath: "/journal/trade/native/42/review",
  },
  {
    name: "research repair",
    method: "POST" as const,
    path: "/api/paper-sim/reprocess",
    body: { ledger: "scalp", id: 42, fetch_verified_history: true },
    upstreamPath: "/paper-sim/reprocess",
  },
];

function sendAction(
  app: express.Express,
  action: (typeof actions)[number],
  authenticated = false,
) {
  const test = action.method === "PATCH"
    ? request(app).patch(action.path)
    : request(app).post(action.path);

  test.set("Host", "dashboard.test");
  if (authenticated) {
    test
      .auth("admin", DASHBOARD_PASSWORD)
      .set("Origin", "http://dashboard.test");
  }
  return test.send(action.body);
}

describe("operator controls through the dashboard proxy", () => {
  it("rejects anonymous actions and forwards authenticated actions without touching production", async () => {
    const received: Array<{ method: string; path: string; body: unknown }> = [];
    const upstream = http.createServer(async (req, res) => {
      const chunks: Buffer[] = [];
      for await (const chunk of req) chunks.push(Buffer.from(chunk));
      const rawBody = Buffer.concat(chunks).toString("utf8");
      received.push({
        method: req.method ?? "",
        path: req.url ?? "",
        body: rawBody ? JSON.parse(rawBody) : null,
      });
      const payload = JSON.stringify({ ok: true, test_fixture: true });
      res.writeHead(200, {
        "content-type": "application/json",
        "content-length": Buffer.byteLength(payload),
      });
      res.end(payload);
    });
    await new Promise<void>((resolve, reject) => {
      upstream.once("error", reject);
      upstream.listen(0, "127.0.0.1", resolve);
    });
    const address = upstream.address();
    if (!address || typeof address === "string") {
      await new Promise<void>((resolve) => upstream.close(() => resolve()));
      throw new Error("operator-controls fixture did not bind a TCP port");
    }

    const previousPassword = process.env.DASHBOARD_PASSWORD;
    process.env.DASHBOARD_PASSWORD = DASHBOARD_PASSWORD;
    const app = express();
    // Match the production app: the proxy forwards the raw request body to Flask.
    app.use(express.raw({ type: () => true, limit: "1mb" }));
    app.use("/api", createLiveBotRouter(address.port));

    try {
      for (const action of actions) {
        const response = await sendAction(app, action);
        expect(response.status, `${action.name} anonymous status`).toBe(401);
        expect(response.body).toEqual({ error: "Authentication required" });
      }
      expect(received).toHaveLength(0);

      for (const action of actions) {
        const response = await sendAction(app, action, true);
        expect(response.status, `${action.name} authenticated status`).toBe(200);
        expect(response.body).toEqual({ ok: true, test_fixture: true });
      }

      expect(received).toEqual(
        actions.map((action) => ({
          method: action.method,
          path: action.upstreamPath,
          body: action.body,
        })),
      );
    } finally {
      if (previousPassword == null) delete process.env.DASHBOARD_PASSWORD;
      else process.env.DASHBOARD_PASSWORD = previousPassword;
      await new Promise<void>((resolve, reject) =>
        upstream.close((error) => (error ? reject(error) : resolve())),
      );
    }
  });
});