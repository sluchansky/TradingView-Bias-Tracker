import type { DashboardStatus } from "../types";
import { asRecord, asString } from "../types";
import { DashboardPanel, DataRow } from "./Panel";

export function NewsSessionPanel({ data }: { data: DashboardStatus | null }) {
  const news = asRecord(data?.news_filter);
  const nextEvent = asRecord(news.next_event);
  const eventName = asString(nextEvent.title) ?? asString(nextEvent.name);
  const sessionLabel = data?.session_preferred === true
    ? "Preferred window"
    : data?.session_preferred === false
      ? "Outside preferred window"
      : "Unavailable";
  const nextOpen = !data
    ? "Unavailable"
    : data.market_open === false
      ? data.next_open ?? "Unavailable"
      : data.market_open === true
        ? "Market is open"
        : "Unavailable";

  return (
    <DashboardPanel title="News / events" className="dv2-bottom-card">
      <div className="dv2-data-list">
        <DataRow label="Window" value={data?.session_window ?? "Unavailable"} />
        <DataRow label="Session" value={sessionLabel} />
        <DataRow
          label="Next event"
          value={data ? eventName ?? asString(news.reason) ?? "No event available" : "Unavailable"}
        />
        <DataRow label="Next open" value={nextOpen} />
      </div>
    </DashboardPanel>
  );
}
