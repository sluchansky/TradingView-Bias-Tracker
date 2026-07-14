import type { DashboardStatus } from "../types";
import { asNumber, asRecord, asString, formatValue } from "../types";
import { DashboardPanel, DataRow, Unavailable } from "./Panel";

export function MarketStatusPanel({ data }: { data: DashboardStatus | null }) {
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
    <DashboardPanel title="Market status" eyebrow="Flow · levels · structure" className="dv2-market-status">
      <div className="dv2-market-status-section">
        <h3>Order flow</h3>
        {asString(marketRead.order_flow) && (
          <p className="dv2-panel-summary">{asString(marketRead.order_flow)}</p>
        )}
        <div className="dv2-market-status-grid">
          <DataRow
            label="CVD"
            value={cvd ?? "Unavailable"}
            tone={/bull|pos/i.test(cvd ?? "") ? "bull" : /bear|neg/i.test(cvd ?? "") ? "bear" : "info"}
          />
          <DataRow label="RVOL" value={rvol === null ? "Unavailable" : `${rvol.toFixed(2)}×`} />
          <DataRow label="Volume" value={volume ?? "Unavailable"} />
        </div>
      </div>

      <div className="dv2-market-status-section">
        <h3>Levels</h3>
        <div className="dv2-market-status-grid">
          <DataRow label="Supply" value={formatValue(data?.nearest_supply)} tone="bear" />
          <DataRow label="VWAP" value={formatValue(data?.vwap_value)} tone="info" />
          <DataRow label="Demand" value={formatValue(data?.nearest_demand)} tone="bull" />
          <DataRow
            label="Last valid"
            value={data ? (data.last_valid_time ?? "Current snapshot") : "Unavailable"}
          />
        </div>
      </div>

      <div className="dv2-market-status-section">
        <h3>Structure</h3>
        {data?.market_structure ? (
          <>
            <div className="dv2-structure-label">{data.market_structure}</div>
            <p className="dv2-panel-summary">
              {data.structure_detail || "Structure detail is unavailable."}
            </p>
            <div className="dv2-market-status-grid">
              <DataRow label="Class" value={data.structure_class ?? "Unavailable"} />
              <DataRow
                label="Character"
                value={asString(marketRead.market_character) ?? "Unavailable"}
              />
              <DataRow
                label="Risk zone"
                value={data.risk_zone ?? "Unavailable"}
                tone={/risk|over|supply/i.test(data.risk_zone ?? "") ? "caution" : "info"}
              />
            </div>
          </>
        ) : (
          <Unavailable>Waiting for market structure data.</Unavailable>
        )}
      </div>
    </DashboardPanel>
  );
}
