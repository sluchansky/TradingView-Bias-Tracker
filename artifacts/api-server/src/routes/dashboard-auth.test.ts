import { afterEach, describe, expect, it } from "vitest";
import { dashboardAuth } from "./dashboard-auth";

const originalPassword = process.env.DASHBOARD_PASSWORD;
const originalUsername = process.env.DASHBOARD_USERNAME;

afterEach(() => {
  if (originalPassword === undefined) delete process.env.DASHBOARD_PASSWORD;
  else process.env.DASHBOARD_PASSWORD = originalPassword;
  if (originalUsername === undefined) delete process.env.DASHBOARD_USERNAME;
  else process.env.DASHBOARD_USERNAME = originalUsername;
});

function basic(username: string, password: string): string {
  return `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}`;
}

function invoke(options: {
  path?: string;
  method?: string;
  authorization?: string;
  origin?: string;
} = {}) {
  const result = { next: false, status: 0, body: null as unknown, headers: {} as Record<string, string> };
  const res = {
    set(name: string, value: string) {
      result.headers[name] = value;
      return res;
    },
    status(code: number) {
      result.status = code;
      return res;
    },
    json(body: unknown) {
      result.body = body;
      return res;
    },
  };
  dashboardAuth(
    {
      path: options.path ?? "/status",
      method: options.method ?? "GET",
      headers: {
        host: "dashboard.test",
        authorization: options.authorization,
        origin: options.origin,
      },
      socket: { remoteAddress: "127.0.0.1" },
    } as any,
    res as any,
    () => { result.next = true; },
  );
  return result;
}

describe("dashboardAuth", () => {
  it("requires the configured username as well as the password", () => {
    process.env.DASHBOARD_USERNAME = "operator";
    process.env.DASHBOARD_PASSWORD = "correct-password";

    const result = invoke({ authorization: basic("admin", "correct-password") });

    expect(result.next).toBe(false);
    expect(result.status).toBe(401);
  });

  it("rejects a wrong password", () => {
    process.env.DASHBOARD_USERNAME = "operator";
    process.env.DASHBOARD_PASSWORD = "correct-password";

    const result = invoke({ authorization: basic("operator", "incorrect-password") });

    expect(result.next).toBe(false);
    expect(result.status).toBe(401);
  });

  it("fails closed when authentication is not configured", () => {
    delete process.env.DASHBOARD_PASSWORD;

    const result = invoke();

    expect(result.next).toBe(false);
    expect(result.status).toBe(503);
  });

  it("does not rate-limit a login screen before credentials are supplied", () => {
    process.env.DASHBOARD_USERNAME = "operator";
    process.env.DASHBOARD_PASSWORD = "correct-password";

    for (let i = 0; i < 12; i += 1) {
      const result = invoke();
      expect(result.next).toBe(false);
      expect(result.status).toBe(401);
    }
  });

  it("requires a same-origin request for protected writes", () => {
    process.env.DASHBOARD_USERNAME = "operator";
    process.env.DASHBOARD_PASSWORD = "correct-password";

    const result = invoke({
      method: "POST",
      authorization: basic("operator", "correct-password"),
    });

    expect(result.next).toBe(false);
    expect(result.status).toBe(403);
  });

  it("keeps the TradingView webhook route available to its sender", () => {
    delete process.env.DASHBOARD_PASSWORD;

    const result = invoke({ path: "/webhook", method: "POST" });

    expect(result.next).toBe(true);
    expect(result.status).toBe(0);
  });
});