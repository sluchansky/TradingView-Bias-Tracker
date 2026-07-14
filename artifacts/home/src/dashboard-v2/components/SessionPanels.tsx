import type { DashboardStatus } from "../types";
import { asNumber, asRecord, asString } from "../types";
import { DashboardPanel, DataRow, Unavailable } from "./Panel";

export function ObjectivePanel({ data }: { data: DashboardStatus | null }) {
  const objective = data?.stage_next_step || data?.recommendation || data?.action;
  return (
    <DashboardPanel title="Today’s objective">
      {objective ? <p className="dv2-objective">{objective}</p> : <Unavailable />}
    </DashboardPanel>
  );
}

export function SessionPerformancePanel({ data }: { data: DashboardStatus | null }) {
  const quality = asRecord(data?.session_quality);
  const learning = asRecord(data?.main_brain_learning_stats);
  const equity = asRecord(data?.equity_curve_today);
  const wins = asNumber(equity.wins) ?? asNumber(quality.wins);
  const losses = asNumber(equity.losses) ?? asNumber(quality.losses);
  const expectancy = asNumber(learning.expectancy);

  return (
    <DashboardPanel title="Session performance">
      <div className="dv2-data-list">
        <DataRow label="Record" value={wins === null && losses === null ? "Unavailable" : `${wins ?? 0}W · ${losses ?? 0}L`} />
        <DataRow label="Expectancy" value={expectancy === null ? "Unavailable" : `${expectancy.toFixed(2)}R`} />
        <DataRow label="Quality" value={asString(quality.grade) ?? asString(quality.label) ?? "Unavailable"} />
      </div>
    </DashboardPanel>
  );
}

export function SessionMemoryPanel({ data }: { data: DashboardStatus | null }) {
  const memory = asRecord(data?.trade_memory);
  const learning = asRecord(data?.brain_state);
  const learningBlock = asRecord(learning.learning);
  const summary = asString(memory.summary_text);

  return (
    <DashboardPanel title="Session memory">
      {summary ? <p className="dv2-panel-summary">{summary}</p> : (
        <div className="dv2-data-list">
          <DataRow label="Similar samples" value={asNumber(learningBlock.similar_samples) ?? "Unavailable"} />
          <DataRow label="Win rate" value={asNumber(learningBlock.win_rate) === null ? "Unavailable" : `${asNumber(learningBlock.win_rate)}%`} />
          <DataRow label="Common failure" value={asString(learningBlock.common_failure) ?? "Unavailable"} />
        </div>
      )}
    </DashboardPanel>
  );
}

export function NewsSessionPanel({ data }: { data: DashboardStatus | null }) {
  const news = asRecord(data?.news_filter);
  const nextEvent = asRecord(news.next_event);
  const eventName = asString(nextEvent.title) ?? asString(nextEvent.name);

  return (
    <DashboardPanel title="News & session">
      <div className="dv2-data-list">
        <DataRow label="Window" value={data?.session_window ?? "Unavailable"} />
        <DataRow label="Session" value={data?.session_preferred ? "Preferred window" : "Outside preferred window"} />
        <DataRow label="Next event" value={eventName ?? asString(news.reason) ?? "No event available"} />
        <DataRow label="Next open" value={data?.market_open === false ? data.next_open ?? "Unavailable" : "Market is open"} />
      </div>
    </DashboardPanel>
  );
}
