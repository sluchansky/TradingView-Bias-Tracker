import type { DashboardStatus } from "../types";
import { formatValue } from "../types";
import { DashboardPanel, DataRow } from "./Panel";

export function LevelsPanel({ data }: { data: DashboardStatus | null }) {
  return (
    <DashboardPanel title="Levels to watch">
      <div className="dv2-data-list">
        <DataRow label="Supply" value={formatValue(data?.nearest_supply)} tone="bear" />
        <DataRow label="VWAP" value={formatValue(data?.vwap_value)} tone="info" />
        <DataRow label="Demand" value={formatValue(data?.nearest_demand)} tone="bull" />
        <DataRow
          label="Last valid"
          value={data?.last_valid_time ?? "Current snapshot"}
        />
      </div>
    </DashboardPanel>
  );
}
