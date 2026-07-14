import type { DashboardStatus } from "../types";
import { asNumber, asRecord, asString } from "../types";
import { DashboardPanel, DataRow } from "./Panel";

export function OrderFlowPanel({ data }: { data: DashboardStatus | null }) {
  const diagnostics = asRecord(data?.alert_diagnostics);
  const brain = asRecord(data?.main_brain);
  const signals = asRecord(brain.signals);
  const brainState = asRecord(data?.brain_state);
  const marketRead = asRecord(brainState.market_read);
  const cvd = (
    asString(diagnostics.cvd_state)
    ?? asString(diagnostics.cvd)
    ?? asString(signals.cvd)
  );
  const rvol = asNumber(diagnostics.rvol_value);
  const volume = asString(diagnostics.volume);

  return (
    <DashboardPanel title="Order flow">
      {asString(marketRead.order_flow) && (
        <p className="dv2-panel-summary">{asString(marketRead.order_flow)}</p>
      )}
      <div className="dv2-data-list">
        <DataRow
          label="CVD"
          value={cvd ?? "Unavailable"}
          tone={/bull|pos/i.test(cvd ?? "") ? "bull" : /bear|neg/i.test(cvd ?? "") ? "bear" : "info"}
        />
        <DataRow label="Relative volume" value={rvol === null ? "Unavailable" : `${rvol.toFixed(2)}×`} />
        <DataRow label="Volume state" value={volume ?? "Unavailable"} />
      </div>
    </DashboardPanel>
  );
}
