import { readFileSync } from "node:fs";
import http from "node:http";
import { resolve } from "node:path";
import express from "express";
import request from "supertest";
import { describe, expect, it } from "vitest";
import {
  BOT1_ROUTES,
  DEFAULT_FLASK_PORT,
  resolveFlaskPort,
} from "./flask-proxy";
import { createLiveBotRouter } from "./index";

const root = resolve(import.meta.dirname, "../../../..");

describe("local dashboard topology", () => {
  it("keeps the chart and tick-stream routes in the live proxy whitelist", () => {
    expect(BOT1_ROUTES).toContain("/main-brain/chart");
    expect(BOT1_ROUTES).toContain("/main-brain/tick-stream");
    expect(BOT1_ROUTES).toContain("/main-brain/tick-stream-token");
  });

  it("uses the hosted Flask port by default and validates local overrides", () => {
    expect(DEFAULT_FLASK_PORT).toBe(8000);
    expect(resolveFlaskPort(undefined)).toBe(8000);
    expect(resolveFlaskPort("8123")).toBe(8123);
    expect(() => resolveFlaskPort("0")).toThrow(/FLASK_PORT/);
    expect(() => resolveFlaskPort("not-a-port")).toThrow(/FLASK_PORT/);
  });

  it("forwards an authenticated chart request to the configured Flask port", async () => {
    let receivedPath = "";
    const upstream = http.createServer((req, res) => {
      receivedPath = req.url ?? "";
      const payload = JSON.stringify({
        ok: false,
        enabled: false,
        reason: "Databento feed intentionally disabled",
        bars: [],
        partial_bar: null,
        connection: {
          status: "DISCONNECTED",
          connected: false,
          reconnects: 0,
          last_ts: null,
          error: null,
        },
      });
      res.writeHead(200, {
        "content-type": "application/json",
        "content-length": Buffer.byteLength(payload),
      });
      res.end(payload);
    });
    await new Promise<void>((done) => upstream.listen(0, "127.0.0.1", done));
    const address = upstream.address();
    if (!address || typeof address === "string") throw new Error("fixture did not bind a TCP port");

    const previousPassword = process.env.DASHBOARD_PASSWORD;
    process.env.DASHBOARD_PASSWORD = "topology-test-password";
    try {
      const app = express();
      app.use(createLiveBotRouter(address.port));
      const response = await request(app)
        .get("/main-brain/chart?instrument=MNQ&timeframe=1m&limit=5")
        .auth("admin", "topology-test-password");

      expect(response.status).toBe(200);
      expect(response.body).toMatchObject({
        ok: false,
        enabled: false,
        reason: "Databento feed intentionally disabled",
      });
      expect(receivedPath).toBe("/main-brain/chart?instrument=MNQ&timeframe=1m&limit=5");
    } finally {
      if (previousPassword == null) delete process.env.DASHBOARD_PASSWORD;
      else process.env.DASHBOARD_PASSWORD = previousPassword;
      await new Promise<void>((done, reject) =>
        upstream.close((error) => (error ? reject(error) : done())),
      );
    }
  });

  it("keeps the Windows launcher, Express target, and Vite proxy on one port contract", () => {
    const launcher = readFileSync(
      resolve(root, "scripts/windows/Start-TradingDashboard.ps1"),
      "utf8",
    );
    const index = readFileSync(
      resolve(root, "artifacts/api-server/src/routes/index.ts"),
      "utf8",
    );
    const vite = readFileSync(
      resolve(root, "artifacts/home/vite.config.ts"),
      "utf8",
    );
    expect(launcher).toContain('SetEnvironmentVariable("FLASK_PORT", "$FlaskPort"');
    expect(launcher).toContain('"LOCAL_API_PROXY_TARGET", "http://127.0.0.1:$ApiPort"');
    expect(index).toContain("resolveFlaskPort(process.env.FLASK_PORT)");
    expect(index).toContain("createLiveBotRouter");
    expect(vite).toContain("LOCAL_API_PROXY_TARGET");
    expect(vite).toContain('proxy:');
  });

  it("documents the coordinated startup instead of directing Windows users to Vite alone", () => {
    const docs = readFileSync(resolve(root, "WINDOWS_HOSTING.md"), "utf8");
    expect(docs).toContain("Start-TradingDashboard.ps1");
    expect(docs).toContain("FLASK_PORT");
    expect(docs).toContain("DATABENTO FEED DISABLED");
  });
});