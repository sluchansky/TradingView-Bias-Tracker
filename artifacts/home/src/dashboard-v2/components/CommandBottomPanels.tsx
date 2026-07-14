import type { DashboardStatus } from "../types";
import { asNumber, asRecord, asString, formatValue } from "../types";
import { DashboardPanel, DataRow, Unavailable } from "./Panel";

function formatAlertLabel(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function ActiveAlertsPanel({ data }: { data: DashboardStatus | null }) {
  const root = asRecord(data);
  const counts = asRecord(root.alert_counts);
  const alerts = Object.entries(counts)
    .map(([label, value]) => ({ label: formatAlertLabel(label), count: asNumber(value) }))
    .filter((item): item is { label: string; count: number } => item.count !== null)
    .sort((a, b) => b.count - a.count)
    .slice(0, 3);
  const alertLevel = asString(root.alert_level);
  const totalStored = asNumber(root.total_alerts_stored);

  return (
    <DashboardPanel title="Active alerts" className="dv2-bottom-card">
      {!data ? (
        <Unavailable />
      ) : alerts.length || alertLevel || totalStored !== null ? (
        <div className="dv2-data-list">
          {alertLevel && <DataRow label="Current level" value={alertLevel} tone="caution" />}
          {alerts.map((alert) => (
            <DataRow key={alert.label} label={alert.label} value={alert.count} />
          ))}
          {totalStored !== null && <DataRow label="Stored" value={Math.round(totalStored)} />}
        </div>
      ) : (
        <Unavailable>No active alert data.</Unavailable>
      )}
    </DashboardPanel>
  );
}

export function PositionsPanel({ data }: { data: DashboardStatus | null }) {
  const root = asRecord(data);
  const management = asRecord(root.active_trade_mgmt);
  const brainState = asRecord(data?.brain_state);
  const execution = asRecord(brainState.execution);
  const brainPosition = asRecord(execution.open_position);
  const active = management.active === true
    || /active|open|managing/i.test(asString(management.status) ?? "")
    || Object.keys(brainPosition).length > 0;
  const position = Object.keys(management).length ? management : brainPosition;

  return (
    <DashboardPanel title="Positions" className="dv2-bottom-card">
      {!data ? (
        <Unavailable />
      ) : active ? (
        <div className="dv2-data-list">
          <DataRow label="Symbol" value={asString(position.symbol) ?? data.active_ticker ?? "Unavailable"} />
          <DataRow label="Direction" value={asString(position.direction) ?? "Unavailable"} />
          <DataRow label="Entry" value={formatValue(position.entry_price ?? position.entry)} />
          <DataRow
            label="Current R"
            value={asNumber(position.current_r ?? position.unrealized_r) === null
              ? "Unavailable"
              : `${asNumber(position.current_r ?? position.unrealized_r)?.toFixed(2)}R`}
          />
        </div>
      ) : (
        <Unavailable>No active position.</Unavailable>
      )}
    </DashboardPanel>
  );
}
