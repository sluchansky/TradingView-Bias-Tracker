import { useEffect, useState } from "react";
import type { ConnectionState, DashboardStatus, DashboardTicker } from "../types";
import { DASHBOARD_TICKERS } from "../types";

function useEasternClock() {
  const [time, setTime] = useState("");
  useEffect(() => {
    const tick = () => setTime(
      new Date().toLocaleTimeString("en-US", {
        timeZone: "America/New_York",
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
        hour12: true,
      }) + " ET",
    );
    tick();
    const interval = setInterval(tick, 1_000);
    return () => clearInterval(interval);
  }, []);
  return time;
}

export function DashboardV2Header({
  ticker,
  onTickerChange,
  data,
  connection,
  muted,
  settingsOpen,
  onToggleMuted,
  onToggleSettings,
}: {
  ticker: DashboardTicker;
  onTickerChange: (ticker: DashboardTicker) => void;
  data: DashboardStatus | null;
  connection: ConnectionState;
  muted: boolean;
  settingsOpen: boolean;
  onToggleMuted: () => void;
  onToggleSettings: () => void;
}) {
  const time = useEasternClock();
  const marketOpen = data?.market_open;
  const execution = String(data?.execution_mode ?? "");
  const demo = data?._demo === true || /paper|sim|demo/i.test(execution);

  return (
    <header className="dv2-header">
      <div className="dv2-brand">
        <span className="dv2-brand-mark">AI</span>
        <div>
          <strong>AI Trading Partner</strong>
          <span>Dashboard V2</span>
        </div>
      </div>

      <nav className="dv2-instruments" aria-label="Select instrument">
        {DASHBOARD_TICKERS.map((item) => (
          <button
            key={item}
            className={item === ticker ? "is-active" : ""}
            onClick={() => onTickerChange(item)}
            type="button"
          >
            {item}
          </button>
        ))}
      </nav>

      <div className="dv2-header-status">
        <span className={`dv2-connection is-${connection}`}>
          <i />
          {connection === "connected" ? "Connected" : connection}
        </span>
        <span className={marketOpen === true ? "dv2-bull" : marketOpen === false ? "dv2-caution" : undefined}>
          {marketOpen === true ? "Market open" : marketOpen === false ? "Market closed" : "Market unavailable"}
        </span>
        <span className={data ? (demo ? "dv2-caution" : "dv2-info") : undefined}>
          {data ? (demo ? "Demo / paper" : "Live data") : "Mode unavailable"}
        </span>
        <time>{time}</time>
        <button
          className="dv2-icon-button"
          type="button"
          onClick={onToggleMuted}
          aria-label={muted ? "Enable sound" : "Mute sound"}
          title={muted ? "Enable sound" : "Mute sound"}
        >
          {muted ? "Sound off" : "Sound on"}
        </button>
        <button
          className="dv2-icon-button"
          type="button"
          onClick={onToggleSettings}
          aria-label="Open dashboard settings"
          aria-expanded={settingsOpen}
          aria-controls="dashboard-v2-settings"
        >
          Settings
        </button>
      </div>
    </header>
  );
}
