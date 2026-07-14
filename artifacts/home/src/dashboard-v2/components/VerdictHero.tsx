import { resolveAuthoritativeVerdict } from "../composeAIBriefing";
import type { DashboardStatus } from "../types";
import { asNumber, asRecord, asString } from "../types";

export function VerdictHero({
  data,
  loading,
  live,
}: {
  data: DashboardStatus | null;
  loading: boolean;
  live: boolean;
}) {
  const root = asRecord(data);
  const brain = asRecord(data?.main_brain);
  const resolved = resolveAuthoritativeVerdict(data);
  const edge = live ? asNumber(brain.edge_score) ?? asNumber(data?.edge_score) : null;
  const grade = live ? asString(brain.edge_grade) ?? asString(data?.edge_grade) : null;
  const tone = !live
    ? "info"
    : resolved.label === "INVALIDATED"
      ? "bear"
      : resolved.ready
        ? resolved.direction === "Short" ? "bear" : "bull"
        : "caution";
  const label = live ? resolved.label : loading ? "CONNECTING" : "WAIT";
  const classification = live ? (
    asString(root.setup_stage)
    ?? asString(root.trade_quality_label)
    ?? asString(data?.edge_grade)
  ) : null;

  return (
    <section className={`dv2-verdict dv2-verdict-${tone}`}>
      <div className="dv2-verdict-copy">
        <span className="dv2-eyebrow">{live ? "Live verdict" : "Status"}</span>
        <h1>{label}</h1>
        <span className="dv2-verdict-state">
          {classification ?? (loading ? "Loading setup classification" : live
            ? "Setup classification unavailable"
            : "Live status unavailable")}
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
