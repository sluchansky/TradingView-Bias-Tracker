import type { DashboardStatus } from "../types";
import { asRecord, asString, formatValue } from "../types";
import { DashboardPanel, DataRow } from "./Panel";

export function MarketContextPanel({ data }: { data: DashboardStatus | null }) {
  const feed = asRecord(data?.data_feed);
  const brainState = asRecord(data?.brain_state);
  const marketRead = asRecord(brainState.market_read);
  const price = data?.market_open === false
    ? data.last_valid_price ?? data.current_price
    : data?.current_price;

  return (
    <DashboardPanel title="Market context">
      <div className="dv2-data-list">
        <DataRow label="Price" value={formatValue(price)} />
        <DataRow label="VWAP" value={formatValue(data?.vwap_value)} />
        <DataRow
          label="Bias"
          value={data?.bias ?? "Unavailable"}
          tone={/bull/i.test(data?.bias ?? "") ? "bull" : /bear/i.test(data?.bias ?? "") ? "bear" : "info"}
        />
        <DataRow label="ATR" value={formatValue(data?.current_atr)} />
        <DataRow
          label="Character"
          value={asString(marketRead.market_character) ?? "Unavailable"}
        />
        <DataRow
          label="Feed"
          value={asString(feed.freshness_label) ?? asString(feed.overall_freshness) ?? "Unavailable"}
        />
      </div>
    </DashboardPanel>
  );
}
