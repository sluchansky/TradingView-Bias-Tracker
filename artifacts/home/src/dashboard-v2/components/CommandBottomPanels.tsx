import type { DashboardStatus } from "../types";
import { asNumber, asRecord, asString, formatValue } from "../types";
import { DashboardPanel, DataRow, Unavailable } from "./Panel";

export function ActiveAlertsPanel({ data }: { data: DashboardStatus | null }) {
  const root = asRecord(data);
  const alertLevel = asString(root.alert_level);
  const setupStage = asString(root.setup_stage);
  const activeTicker = asString(root.active_ticker);

  return (
    <DashboardPanel title="Active alerts" className="dv2-bottom-card">
      {!data ? (
        <Unavailable />
      ) : alertLevel ? (
        <div className="dv2-data-list">
          <DataRow label="Current level" value={alertLevel} tone="caution" />
          <DataRow label="Instrument" value={activeTicker ?? "Unavailable"} />
          <DataRow label="Setup" value={setupStage ?? "Unavailable"} />
        </div>
      ) : (
        <Unavailable>No active alert.</Unavailable>
      )}
    </DashboardPanel>
  );
}

export function PositionsPanel({ data }: { data: DashboardStatus | null }) {
  const root = asRecord(data);
  const management = asRecord(root.active_trade_mgmt);
  const managedPositions = Array.isArray(management.positions)
    ? management.positions.map(asRecord).filter((position) => Object.keys(position).length > 0)
    : [];
  const brainState = asRecord(data?.brain_state);
  const execution = asRecord(brainState.execution);
  const brainPosition = asRecord(execution.open_position);
  const position = managedPositions[0] ?? brainPosition;
  const active = managedPositions.length > 0 || Object.keys(brainPosition).length > 0;
  const currentR = asNumber(position.current_r ?? position.unrealized_r);

  return (
    <DashboardPanel title="Positions" className="dv2-bottom-card">
      {!data ? (
        <Unavailable />
      ) : active ? (
        <div className="dv2-data-list">
          {managedPositions.length > 1 && <DataRow label="Open positions" value={managedPositions.length} />}
          <DataRow label="Symbol" value={asString(position.symbol) ?? data.active_ticker ?? "Unavailable"} />
          <DataRow label="Direction" value={asString(position.direction) ?? "Unavailable"} />
          <DataRow label="Entry" value={formatValue(position.entry_price ?? position.entry)} />
          <DataRow
            label="Current R"
            value={currentR === null ? "Unavailable" : `${currentR.toFixed(2)}R`}
          />
        </div>
      ) : (
        <Unavailable>No active position.</Unavailable>
      )}
    </DashboardPanel>
  );
}
