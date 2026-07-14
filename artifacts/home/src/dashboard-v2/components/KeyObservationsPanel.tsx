import type { DashboardStatus } from "../types";
import { asRecord, asString, asStringList } from "../types";
import { DashboardPanel, Unavailable } from "./Panel";

export function KeyObservationsPanel({ data }: { data: DashboardStatus | null }) {
  const brain = asRecord(data?.main_brain);
  const report = asRecord(data?.analyst_report);
  const observations = [
    ...asStringList(brain.market_brain),
    ...asStringList(brain.strategy_brain),
    ...asStringList(report.evidence_for),
  ].filter((item, index, all) => all.indexOf(item) === index).slice(0, 5);

  const currentAction = (
    asString(data?.recommendation)
    ?? asString(data?.action)
    ?? asString(data?.stage_next_step)
  );

  return (
    <DashboardPanel title="Key observations">
      {observations.length ? (
        <ul className="dv2-observations">
          {observations.map((observation) => <li key={observation}>{observation}</li>)}
        </ul>
      ) : (
        <Unavailable>No observations are available yet.</Unavailable>
      )}
      <div className="dv2-current-action">
        <span className="dv2-eyebrow">Current action</span>
        <strong>{currentAction ?? "Wait for the next confirmed condition."}</strong>
      </div>
    </DashboardPanel>
  );
}
