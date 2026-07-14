import type { DashboardStatus } from "../types";
import { DashboardPanel, DataRow, Unavailable } from "./Panel";

export function MarketStructurePanel({ data }: { data: DashboardStatus | null }) {
  return (
    <DashboardPanel title="Market structure">
      {data?.market_structure ? (
        <>
          <div className="dv2-structure-label">{data.market_structure}</div>
          <p className="dv2-panel-summary">
            {data.structure_detail || "Structure detail is unavailable."}
          </p>
          <div className="dv2-data-list">
            <DataRow label="Class" value={data.structure_class ?? "Unavailable"} />
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
    </DashboardPanel>
  );
}
