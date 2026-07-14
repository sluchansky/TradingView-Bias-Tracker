import type { DashboardStatus } from "../types";
import { asNumber, asRecord, asString } from "../types";
import { DashboardPanel, DataRow, Unavailable } from "./Panel";

export function IntelligenceRail({ data }: { data: DashboardStatus | null }) {
  const brain = asRecord(data?.main_brain);
  const brainState = asRecord(data?.brain_state);
  const marketRead = asRecord(brainState.market_read);
  const safety = asRecord(brainState.safety);
  const liquidity = asRecord(brain.liquidity_focus);
  const diagnostics = asRecord(data?.alert_diagnostics);
  const signals = asRecord(brain.signals);
  const vetoSources = Array.isArray(safety.veto_source)
    ? safety.veto_source.map(asString).filter((value): value is string => value !== null).join(", ")
    : asString(safety.veto_source);
  const cvd = (
    asString(diagnostics.cvd_state)
    ?? asString(diagnostics.cvd)
    ?? asString(signals.cvd)
  );
  const rvol = asNumber(diagnostics.rvol_value);

  return (
    <>
      <DashboardPanel title="Structure" className="dv2-intel-card">
        {data?.market_structure ? (
          <>
            <strong className="dv2-intel-headline">{data.market_structure}</strong>
            <p className="dv2-panel-summary">{data.structure_detail || "Detail unavailable."}</p>
            <DataRow label="Class" value={data.structure_class ?? "Unavailable"} />
          </>
        ) : <Unavailable>Structure unavailable.</Unavailable>}
      </DashboardPanel>

      <DashboardPanel title="Liquidity" className="dv2-intel-card">
        <div className="dv2-data-list">
          <DataRow
            label="State"
            value={asString(liquidity.state) ?? asString(marketRead.liquidity_state) ?? "Unavailable"}
          />
          <DataRow label="Target" value={asString(liquidity.target) ?? "Unavailable"} />
          <DataRow label="Character" value={asString(marketRead.market_character) ?? "Unavailable"} />
        </div>
      </DashboardPanel>

      <DashboardPanel title="Order flow" className="dv2-intel-card">
        {asString(marketRead.order_flow) && (
          <p className="dv2-panel-summary">{asString(marketRead.order_flow)}</p>
        )}
        <div className="dv2-data-list">
          <DataRow
            label="CVD"
            value={cvd ?? "Unavailable"}
            tone={/bull|pos/i.test(cvd ?? "") ? "bull" : /bear|neg/i.test(cvd ?? "") ? "bear" : "info"}
          />
          <DataRow label="RVOL" value={rvol === null ? "Unavailable" : `${rvol.toFixed(2)}×`} />
          <DataRow label="Volume" value={asString(diagnostics.volume) ?? "Unavailable"} />
        </div>
      </DashboardPanel>

      <DashboardPanel title="Risk status" className="dv2-intel-card">
        <div className="dv2-data-list">
          <DataRow
            label="Status"
            value={asString(safety.risk_status) ?? data?.risk_zone ?? "Unavailable"}
            tone={/high|risk|over/i.test(asString(safety.risk_status) ?? data?.risk_zone ?? "") ? "caution" : "info"}
          />
          <DataRow label="Prop" value={asString(safety.prop_status) ?? "Unavailable"} />
          <DataRow label="Veto" value={vetoSources || (data ? "None reported" : "Unavailable")} />
          <DataRow label="R:R" value={data?.trade_plan?.rr ?? "Unavailable"} />
        </div>
      </DashboardPanel>
    </>
  );
}
