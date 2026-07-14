import type { DashboardStatus } from "../types";
import { asRecord, asString } from "../types";
import { DashboardPanel, Unavailable } from "./Panel";

export function MarketHistoryPanel({ data }: { data: DashboardStatus | null }) {
  const brainState = asRecord(data?.brain_state);
  const events = Array.isArray(brainState.recent_events)
    ? brainState.recent_events
      .map(asRecord)
      .filter((event) => asString(event.detail) || asString(event.kind))
      .slice(0, 8)
    : [];

  return (
    <DashboardPanel title="Market history">
      {events.length ? (
        <ol className="dv2-history-list">
          {events.map((event, index) => (
            <li key={`${asString(event.ts) ?? "event"}-${index}`}>
              <time>{asString(event.ts) ?? "—"}</time>
              <div>
                <strong>{asString(event.kind)?.replace(/_/g, " ") ?? "Market update"}</strong>
                <span>{asString(event.detail) ?? "No detail available."}</span>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <Unavailable>No market history is available yet.</Unavailable>
      )}
    </DashboardPanel>
  );
}
