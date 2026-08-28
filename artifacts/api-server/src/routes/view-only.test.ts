import http from "node:http";
import express from "express";
import request from "supertest";
import { describe, expect, it } from "vitest";
import { createViewOnlyRouter } from "./view-only";
import { mintLinkToken, mintSessionCookie } from "./view-tokens";

const DASHBOARD_PASSWORD = "view-only-boundary-test-password";

const ownerActions = [
  {
    name: "execution",
    method: "POST" as const,
    path: "/view/api/traderspost",
    body: { ticker: "MGC", contracts: 1 },
  },
  {
    name: "journal review",
    method: "PATCH" as const,
    path: "/view/api/journal/trade/native/42/review",
    body: { overall_quality: 1 },
  },
  {
    name: "research repair",
    method: "POST" as const,
    path: "/view/api/paper-sim/reprocess",
    body: { ledger: "scalp", id: 42, fetch_verified_history: true },
  },
];

describe("watch-only session boundary", () => {
  it("reads status but rejects owner actions without reaching the upstream fixture", async () => {
    const received: Array<{ method: string; path: string }> = [];
    const upstream = http.createServer((req, res) => {
      received.push({ method: req.method ?? "", path: req.url ?? "" });
      const payload = req.url?.startsWith("/status")
        ? JSON.stringify({ ok: true, test_fixture: true, ticker: "MGC" })
        : JSON.stringify({ error: "unexpected upstream request" });
      res.writeHead(req.url?.startsWith("/status") ? 200 : 500, {
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
      throw new Error("view-only fixture did not bind a TCP port");
    }

    const previousPassword = process.env.DASHBOARD_PASSWORD;
    process.env.DASHBOARD_PASSWORD = DASHBOARD_PASSWORD;
    const app = express();
    app.use(express.raw({ type: () => true, limit: "1mb" }));
    app.use("/view", createViewOnlyRouter(address.port));
    const { exp } = mintLinkToken(3600, "viewer-password");
    const session = mintSessionCookie(exp);

    try {
      const status = await request(app)
        .get("/view/api/status?ticker=MGC")
        .set("Cookie", `vsess=${session}`);

      expect(status.status).toBe(200);
      expect(status.body).toEqual({ ok: true, test_fixture: true, ticker: "MGC" });

      for (const action of ownerActions) {
        const actionRequest = action.method === "PATCH"
          ? request(app).patch(action.path)
          : request(app).post(action.path);
        const response = await actionRequest
          .set("Cookie", `vsess=${session}`)
          .send(action.body);

        expect(response.status, `${action.name} status`).toBe(403);
        expect(response.body, `${action.name} body`).toEqual({
          error: "forbidden (view-only)",
        });
      }

      expect(received).toEqual([{ method: "GET", path: "/status?ticker=MGC" }]);
    } finally {
      if (previousPassword == null) delete process.env.DASHBOARD_PASSWORD;
      else process.env.DASHBOARD_PASSWORD = previousPassword;
      await new Promise<void>((resolve, reject) =>
        upstream.close((error) => (error ? reject(error) : resolve())),
      );
    }
  });
});