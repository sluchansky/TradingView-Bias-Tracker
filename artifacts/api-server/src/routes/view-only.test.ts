import { readFileSync } from "node:fs";
import http from "node:http";
import { resolve } from "node:path";
import express from "express";
import request from "supertest";
import { describe, expect, it } from "vitest";
import { createLiveBotRouter } from "./index";
import {
  checkPublishedWatchHost,
  createViewOnlyRouter,
  WATCH_LINK_PROTOCOL,
} from "./view-only";
import { mintLinkToken, mintSessionCookie } from "./view-tokens";

const DASHBOARD_PASSWORD = "view-only-boundary-test-password";
const root = resolve(import.meta.dirname, "../../../..");

function configuredProxyPaths(): string[] {
  const manifest = readFileSync(
    resolve(root, "artifacts/api-server/.replit-artifact/artifact.toml"),
    "utf8",
  );
  const pathsLine = manifest.match(/^paths\s*=\s*\[([^\]]*)\]/m)?.[1] ?? "";
  return Array.from(pathsLine.matchAll(/"([^"]+)"/g), (match) => match[1]);
}

// Model the outer path router that decides which artifact receives a request.
// Keeping this in the test makes the /view manifest entry part of the exercised
// boundary instead of only asserting the Express mount in isolation.
function createConfiguredProxy(api: express.Express): express.Express {
  const paths = configuredProxyPaths();
  const proxy = express();
  proxy.use((req, res, next) => {
    const rawPath = String(req.url ?? "").split("?")[0];
    const forwarded = paths.some(
      (prefix) => rawPath === prefix || rawPath.startsWith(`${prefix}/`),
    );
    if (!forwarded) {
      res.status(404).send("path not configured");
      return;
    }
    api(req, res, next);
  });
  return proxy;
}

function basic(username: string, password: string): string {
  return `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}`;
}

async function listen(
  handler: (req: http.IncomingMessage, res: http.ServerResponse) => void,
): Promise<{ server: http.Server; origin: string; requests: http.IncomingMessage[] }> {
  const requests: http.IncomingMessage[] = [];
  const server = http.createServer((req, res) => {
    requests.push(req);
    handler(req, res);
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    throw new Error("probe fixture did not bind a TCP port");
  }
  return {
    server,
    origin: `http://127.0.0.1:${address.port}`,
    requests,
  };
}

async function close(server: http.Server): Promise<void> {
  await new Promise<void>((resolve, reject) =>
    server.close((error) => (error ? reject(error) : resolve())),
  );
}

function createOwnerApp(canonicalWatchOrigin: string): express.Express {
  const app = express();
  app.use(express.raw({ type: () => true, limit: "1mb" }));
  app.use("/api", createLiveBotRouter(1, canonicalWatchOrigin));
  return app;
}

const ownerActions = [
  {
    name: "execution GET",
    method: "GET" as const,
    path: "/view/api/traderspost",
    body: undefined,
  },
  {
    name: "execution POST",
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
  {
    name: "strategy delete",
    method: "DELETE" as const,
    path: "/view/api/strategy-trades/42",
    body: undefined,
  },
];

const obfuscatedOwnerActions = [
  {
    name: "encoded separator",
    method: "GET" as const,
    path: "/view/api%2Ftraderspost",
    body: undefined,
  },
  {
    name: "encoded separator lowercase",
    method: "POST" as const,
    path: "/view/api%2ftraderspost",
    body: { ticker: "MGC", contracts: 1 },
  },
  {
    name: "dot segment",
    method: "PATCH" as const,
    path: "/view/api/./journal/trade/native/42/review",
    body: { overall_quality: 1 },
  },
  {
    name: "parent dot segment",
    method: "DELETE" as const,
    path: "/view/api/journal/trade/native/42/../42",
    body: undefined,
  },
];

describe("watch-only session boundary", () => {
  it("uses an absolute deadline even when a publish slowly streams bytes", async () => {
    const fixture = await listen((_req, res) => {
      res.writeHead(200, {
        "content-type": "application/json",
        "x-watch-link-protocol": WATCH_LINK_PROTOCOL,
      });
      const timer = setInterval(() => res.write(" "), 10);
      res.once("close", () => clearInterval(timer));
    });
    const startedAt = Date.now();

    try {
      await expect(checkPublishedWatchHost(fixture.origin, 50)).resolves.toEqual({
        ok: false,
        reason: "unreachable",
      });
      expect(Date.now() - startedAt).toBeLessThan(500);
    } finally {
      await close(fixture.server);
    }
  });

  it("rejects a private request-derived publish host before probing it", async () => {
    const previousPassword = process.env.DASHBOARD_PASSWORD;
    const previousDeployment = process.env.REPLIT_DEPLOYMENT;
    process.env.DASHBOARD_PASSWORD = DASHBOARD_PASSWORD;
    process.env.REPLIT_DEPLOYMENT = "1";

    try {
      const response = await request(createOwnerApp(""))
        .post("/api/view-link")
        .set("Authorization", basic("admin", DASHBOARD_PASSWORD))
        .set("Origin", "https://127.0.0.1:65534")
        .set("Host", "127.0.0.1:65534")
        .set("X-Forwarded-Host", "127.0.0.1:65534")
        .set("X-Forwarded-Proto", "https")
        .send({});

      expect(response.status).toBe(503);
      expect(response.body.error).toBe("publish_unreachable");
      expect(response.body.url).toBeUndefined();
    } finally {
      if (previousPassword == null) delete process.env.DASHBOARD_PASSWORD;
      else process.env.DASHBOARD_PASSWORD = previousPassword;
      if (previousDeployment == null) delete process.env.REPLIT_DEPLOYMENT;
      else process.env.REPLIT_DEPLOYMENT = previousDeployment;
    }
  });

  it("checks the public watch page before minting a link", async () => {
    const fixture = await listen((req, res) => {
      expect(req.method).toBe("GET");
      expect(req.url).toMatch(/^\/view\/healthz\?__watch_link_probe=[^&]+$/);
      expect(req.headers.cookie).toBeUndefined();
      const body = JSON.stringify({ status: "ok", protocol: WATCH_LINK_PROTOCOL });
      res.writeHead(200, {
        "content-type": "application/json; charset=utf-8",
        "content-length": Buffer.byteLength(body),
        "x-watch-link-protocol": WATCH_LINK_PROTOCOL,
      });
      res.end(body);
    });
    const previousPassword = process.env.DASHBOARD_PASSWORD;
    process.env.DASHBOARD_PASSWORD = DASHBOARD_PASSWORD;

    try {
      const response = await request(createOwnerApp(fixture.origin))
        .post("/api/view-link")
        .set("Authorization", basic("admin", DASHBOARD_PASSWORD))
        .set("Origin", fixture.origin)
        .set("X-Forwarded-Host", fixture.origin.slice("http://".length))
        .set("X-Forwarded-Proto", "http")
        .send({});

      expect(response.status).toBe(200);
      expect(response.body.publish).toEqual({ ok: true, status: 200 });
      expect(response.body.url).toMatch(
        new RegExp(`^${fixture.origin}/view\\?t=`),
      );
      expect(response.body.password).toBeTruthy();
      expect(fixture.requests).toHaveLength(1);
    } finally {
      if (previousPassword == null) delete process.env.DASHBOARD_PASSWORD;
      else process.env.DASHBOARD_PASSWORD = previousPassword;
      await close(fixture.server);
    }
  });

  it("warns and does not mint when the published watch page is stale", async () => {
    const fixture = await listen((_req, res) => {
      const body = "<html><head><title>old publish</title></head></html>";
      res.writeHead(200, {
        "content-type": "text/html; charset=utf-8",
        "content-length": Buffer.byteLength(body),
      });
      res.end(body);
    });
    const previousPassword = process.env.DASHBOARD_PASSWORD;
    process.env.DASHBOARD_PASSWORD = DASHBOARD_PASSWORD;

    try {
      const response = await request(createOwnerApp(fixture.origin))
        .post("/api/view-link")
        .set("Authorization", basic("admin", DASHBOARD_PASSWORD))
        .set("Origin", fixture.origin)
        .set("X-Forwarded-Host", fixture.origin.slice("http://".length))
        .set("X-Forwarded-Proto", "http")
        .send({});

      expect(response.status).toBe(409);
      expect(response.body).toEqual({
        error: "publish_stale",
        message: "The published watch page is stale or missing the current read-only protection. No link was created.",
        publish: { ok: false, reason: "stale" },
      });
      expect(response.body.url).toBeUndefined();
      expect(response.body.password).toBeUndefined();
    } finally {
      if (previousPassword == null) delete process.env.DASHBOARD_PASSWORD;
      else process.env.DASHBOARD_PASSWORD = previousPassword;
      await close(fixture.server);
    }
  });

  it("warns and does not mint when the published host is unreachable", async () => {
    const fixture = await listen((_req, res) => res.end("unused"));
    const origin = fixture.origin;
    await close(fixture.server);
    const previousPassword = process.env.DASHBOARD_PASSWORD;
    process.env.DASHBOARD_PASSWORD = DASHBOARD_PASSWORD;

    try {
      const response = await request(createOwnerApp(origin))
        .post("/api/view-link")
        .set("Authorization", basic("admin", DASHBOARD_PASSWORD))
        .set("Origin", origin)
        .set("X-Forwarded-Host", origin.slice("http://".length))
        .set("X-Forwarded-Proto", "http")
        .send({});

      expect(response.status).toBe(503);
      expect(response.body).toEqual({
        error: "publish_unreachable",
        message: "The public watch page did not respond, so no link was created.",
        publish: { ok: false, reason: "unreachable" },
      });
      expect(response.body.url).toBeUndefined();
    } finally {
      if (previousPassword == null) delete process.env.DASHBOARD_PASSWORD;
      else process.env.DASHBOARD_PASSWORD = previousPassword;
    }
  });

  it("reads status but rejects owner actions and obfuscated paths without reaching the upstream fixture", async () => {
    const received: Array<{ method: string; path: string }> = [];
    const upstream = http.createServer((req, res) => {
      received.push({ method: req.method ?? "", path: req.url ?? "" });
      const isStatus = req.url?.startsWith("/status");
      const isDashboard = req.url === "/dashboard";
      const payload = isStatus
        ? JSON.stringify({ ok: true, test_fixture: true, ticker: "MGC" })
        : isDashboard
          ? "<html><head></head><body><script>const BASE = '/api';</script></body></html>"
          : JSON.stringify({ error: "unexpected upstream request" });
      res.writeHead(isStatus || isDashboard ? 200 : 500, {
        "content-type": isDashboard ? "text/html" : "application/json",
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
    const api = express();
    api.use(express.raw({ type: () => true, limit: "1mb" }));
    api.use("/view", createViewOnlyRouter(address.port));
    const app = createConfiguredProxy(api);
    const { exp } = mintLinkToken(3600, "viewer-password");
    const session = mintSessionCookie(exp);

    try {
      expect(configuredProxyPaths()).toContain("/view");

      const health = await request(app).get("/view/healthz");
      expect(health.status).toBe(200);
      expect(health.body).toEqual({
        status: "ok",
        protocol: WATCH_LINK_PROTOCOL,
      });
      expect(health.headers["x-watch-link-protocol"]).toBe(WATCH_LINK_PROTOCOL);

      const status = await request(app)
        .get("/view/api/status?ticker=MGC")
        .set("Cookie", `vsess=${session}`);

      expect(status.status).toBe(200);
      expect(status.body).toEqual({ ok: true, test_fixture: true, ticker: "MGC" });

      for (const action of [...ownerActions, ...obfuscatedOwnerActions]) {
        const actionRequest = action.method === "GET"
          ? request(app).get(action.path)
          : action.method === "PATCH"
            ? request(app).patch(action.path)
            : action.method === "DELETE"
              ? request(app).delete(action.path)
              : request(app).post(action.path);
        const preparedRequest = action.body === undefined
          ? actionRequest
          : actionRequest.send(action.body);
        const response = await preparedRequest.set("Cookie", `vsess=${session}`);

        expect(response.status, `${action.name} status`).toBe(403);
        expect(response.body, `${action.name} body`).toEqual({
          error: "forbidden (view-only)",
        });
      }

      expect(received).toEqual([
        { method: "GET", path: "/dashboard" },
        { method: "GET", path: "/status?ticker=MGC" },
      ]);
    } finally {
      if (previousPassword == null) delete process.env.DASHBOARD_PASSWORD;
      else process.env.DASHBOARD_PASSWORD = previousPassword;
      await new Promise<void>((resolve, reject) =>
        upstream.close((error) => (error ? reject(error) : resolve())),
      );
    }
  });
});