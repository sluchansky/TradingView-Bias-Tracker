import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ConnectionState,
  DashboardStatus,
  DashboardTicker,
  PricePoint,
} from "./types";

const POLL_INTERVAL_MS = 3_000;
const STALE_AFTER_MS = 12_000;
const MAX_PRICE_POINTS = 120;

function readStoredPassword(): string {
  try {
    return localStorage.getItem("brain_auth") ?? "";
  } catch {
    return "";
  }
}

function authHeaders(password: string): Record<string, string> {
  if (!password) return {};
  const bytes = new TextEncoder().encode(`admin:${password}`);
  let binary = "";
  bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
  return { Authorization: `Basic ${btoa(binary)}` };
}

export function useDashboardV2Data(initialTicker: DashboardTicker = "MNQ") {
  const [ticker, setTicker] = useState<DashboardTicker>(initialTicker);
  const [password, setPassword] = useState(readStoredPassword);
  const [authRequired, setAuthRequired] = useState(() => !readStoredPassword());
  const [data, setData] = useState<DashboardStatus | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [priceHistory, setPriceHistory] = useState<PricePoint[]>([]);
  const requestVersionRef = useRef(0);
  const tickerRef = useRef<DashboardTicker>(initialTicker);

  const headers = useMemo(() => authHeaders(password), [password]);

  const clearAuth = useCallback(() => {
    try {
      localStorage.removeItem("brain_auth");
    } catch {
      // Storage is optional.
    }
    setPassword("");
    setAuthRequired(true);
    setConnection("idle");
  }, []);

  const applyPayload = useCallback((payload: DashboardStatus) => {
    setData(payload);
    setConnection("connected");
    setError(null);
    const now = Date.now();
    setLastUpdated(now);

    const price = Number(payload.current_price);
    if (payload.market_open === true && Number.isFinite(price) && price > 0) {
      setPriceHistory((previous) => [
        ...previous,
        { time: now, price },
      ].slice(-MAX_PRICE_POINTS));
    }
  }, []);

  const fetchStatus = useCallback(async (
    selectedTicker: DashboardTicker,
    requestHeaders = headers,
  ): Promise<"ok" | "warming" | "unauthorized" | "error"> => {
    const requestVersion = ++requestVersionRef.current;
    try {
      const response = await fetch(
        `/api/status?ticker=${encodeURIComponent(selectedTicker)}`,
        { credentials: "include", headers: requestHeaders },
      );
      if (requestVersion !== requestVersionRef.current || selectedTicker !== tickerRef.current) {
        return "ok";
      }

      if (response.status === 401) {
        clearAuth();
        return "unauthorized";
      }
      if (response.status === 503) {
        setConnection("warming");
        setError("Analysis is warming up. Live data will appear automatically.");
        return "warming";
      }
      if (!response.ok) {
        setConnection("error");
        setError(`Status request failed (HTTP ${response.status}).`);
        return "error";
      }

      const payload = await response.json() as DashboardStatus;
      if (payload.status === "warming") {
        setConnection("warming");
        setError("Analysis is warming up. Live data will appear automatically.");
        return "warming";
      }
      applyPayload(payload);
      return "ok";
    } catch {
      setConnection("error");
      setError("Unable to reach the trading service.");
      return "error";
    }
  }, [applyPayload, clearAuth, headers]);

  const authenticate = useCallback(async (candidate: string): Promise<boolean> => {
    const trimmed = candidate.trim();
    if (!trimmed) return false;
    setConnection("loading");
    const candidateHeaders = authHeaders(trimmed);
    const result = await fetchStatus(ticker, candidateHeaders);
    if (result === "unauthorized" || result === "error") return false;

    try {
      localStorage.setItem("brain_auth", trimmed);
    } catch {
      // Storage is optional; the current session still works.
    }
    setPassword(trimmed);
    setAuthRequired(false);
    return true;
  }, [fetchStatus, ticker]);

  const refresh = useCallback(() => {
    void fetchStatus(ticker);
  }, [fetchStatus, ticker]);

  const selectTicker = useCallback((nextTicker: DashboardTicker) => {
    requestVersionRef.current += 1;
    tickerRef.current = nextTicker;
    setData(null);
    setPriceHistory([]);
    setError(null);
    setConnection("loading");
    setTicker(nextTicker);
  }, []);

  useEffect(() => {
    setData(null);
    setPriceHistory([]);
    setError(null);
    if (!password) return;

    let active = true;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      if (!active) return;
      setConnection((current) => current === "idle" ? "loading" : current);
      await fetchStatus(ticker);
      if (active) timeout = setTimeout(poll, POLL_INTERVAL_MS);
    };
    void poll();
    return () => {
      active = false;
      requestVersionRef.current += 1;
      if (timeout) clearTimeout(timeout);
    };
  }, [fetchStatus, password, ticker]);

  useEffect(() => {
    const interval = setInterval(() => {
      if (lastUpdated && Date.now() - lastUpdated > STALE_AFTER_MS) {
        setConnection((current) => current === "connected" ? "stale" : current);
      }
    }, 2_000);
    return () => clearInterval(interval);
  }, [lastUpdated]);

  const askAssistant = useCallback(async (question: string): Promise<string> => {
    const response = await fetch("/api/assistant", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...headers },
      body: JSON.stringify({ question: question.slice(0, 2_000), ticker }),
    });
    if (response.status === 401) {
      clearAuth();
      throw new Error("Your dashboard session expired.");
    }
    const payload = await response.json() as { ok?: boolean; answer?: string; error?: string };
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `Assistant request failed (${response.status}).`);
    }
    if (!payload.answer) throw new Error("The assistant did not return a response.");
    return payload.answer;
  }, [clearAuth, headers, ticker]);

  return {
    ticker,
    setTicker: selectTicker,
    data,
    connection,
    error,
    lastUpdated,
    priceHistory,
    authRequired,
    authenticate,
    clearAuth,
    refresh,
    askAssistant,
  };
}
