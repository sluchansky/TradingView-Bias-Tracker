import type { DashboardStatus } from "../types";
import { asRecord, asString, formatValue } from "../types";
import { DashboardPanel, DataRow } from "./Panel";

export function MarketContextPanel({ data }: { data: DashboardStatus | null }) {
  const feed = asRecord(data?.data_feed);

  return (
    <DashboardPanel title="Market context" className="dv2-decision-card">
      <div className="dv2-data-list">
        <DataRow
          label="Bias"
          value={data?.bias ?? "Unavailable"}
          tone={/bull/i.test(data?.bias ?? "") ? "bull" : /bear/i.test(data?.bias ?? "") ? "bear" : "info"}
        />
        <DataRow label="ATR" value={formatValue(data?.current_atr)} />
        <DataRow
          label="Last valid"
          value={data ? (data.last_valid_time ?? "Current snapshot") : "Unavailable"}
        />
        <DataRow
          label="Feed"
          value={asString(feed.freshness_label) ?? asString(feed.overall_freshness) ?? "Unavailable"}
        />
      </div>
    </DashboardPanel>
  );
}
