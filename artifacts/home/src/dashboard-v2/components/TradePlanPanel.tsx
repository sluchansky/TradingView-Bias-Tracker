import type { DashboardStatus } from "../types";
import { formatValue } from "../types";
import { DashboardPanel, DataRow, Unavailable } from "./Panel";

export function TradePlanPanel({ data }: { data: DashboardStatus | null }) {
  const plan = data?.trade_plan;
  const available = plan?.trade_plan === true;

  return (
    <DashboardPanel title="Trade plan & risk">
      {available ? (
        <div className="dv2-data-list">
          <DataRow label="Direction" value={plan.direction ?? "Unavailable"} />
          <DataRow label="Entry zone" value={plan.entry_zone ?? "Unavailable"} />
          <DataRow label="Stop" value={formatValue(plan.stop_loss)} tone="bear" />
          <DataRow label="Target 1" value={formatValue(plan.target1)} tone="bull" />
          <DataRow label="Target 2" value={formatValue(plan.target2)} tone="bull" />
          <DataRow label="Risk / reward" value={plan.rr ?? "Unavailable"} />
        </div>
      ) : (
        <Unavailable>
          {data?.strict_reason || plan?.invalidation || "No active trade plan. Waiting for confirmation."}
        </Unavailable>
      )}
    </DashboardPanel>
  );
}
