import type { DashboardStatus } from "../types";
import { asNumber, asRecord, asString } from "../types";

export function VerdictHero({ data, loading }: { data: DashboardStatus | null; loading: boolean }) {
  const root = asRecord(data);
  const brain = asRecord(data?.main_brain);
  const status = (
    asString(brain.status)
    ?? asString(data?.verdict)
    ?? (loading ? "CONNECTING" : "UNAVAILABLE")
  ).toUpperCase();
  const direction = asString(brain.favored_direction) ?? asString(data?.strict_direction);
  const edge = asNumber(brain.edge_score) ?? asNumber(data?.edge_score);
  const grade = asString(brain.edge_grade) ?? asString(data?.edge_grade);
  const ready = status.includes("READY");
  const bearish = /short|bear/i.test(direction ?? status);
  const tone = ready ? (bearish ? "bear" : "bull") : status.includes("BUILD") ? "caution" : "info";
  const label = ready ? (bearish ? "READY SHORT" : "READY LONG") : loading ? "CONNECTING" : "WAIT";
  const classification = (
    asString(root.setup_stage)
    ?? asString(root.trade_quality_label)
    ?? asString(data?.edge_grade)
  );

  return (
    <section className={`dv2-verdict dv2-verdict-${tone}`}>
      <div className="dv2-verdict-copy">
        <span className="dv2-eyebrow">Live verdict</span>
        <h1>{label}</h1>
        <span className="dv2-verdict-state">
          {classification ?? (loading ? "Loading setup classification" : "Setup classification unavailable")}
        </span>
      </div>
      <div className="dv2-edge">
        <div className="dv2-edge-number">{edge === null ? "—" : Math.round(edge)}</div>
        <div>
          <strong>Edge score</strong>
          <span>{grade ? `${grade} grade` : "Grade unavailable"}</span>
        </div>
        <div className="dv2-edge-track" aria-label={`Edge score ${edge ?? "unavailable"}`}>
          <i style={{ width: `${(Math.max(0, Math.min(edge ?? 0, 110)) / 110) * 100}%` }} />
        </div>
      </div>
    </section>
  );
}
