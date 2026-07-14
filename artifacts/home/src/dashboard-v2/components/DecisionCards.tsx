import type { DashboardStatus } from "../types";
import { asNumber, asRecord, asString } from "../types";
import { DashboardPanel, Unavailable } from "./Panel";

export function MainReasonCard({ data }: { data: DashboardStatus | null }) {
  const brain = asRecord(data?.main_brain);
  const reason = (
    asString(data?.strict_reason)
    ?? asString(brain.summary)
    ?? asString(data?.recommendation)
  );
  const nextStep = (
    asString(data?.stage_next_step)
    ?? asString(data?.action)
    ?? asString(data?.recommendation)
  );

  return (
    <DashboardPanel title="Main reason" className="dv2-decision-card dv2-reason-card">
      {reason ? <p className="dv2-command-reason">{reason}</p> : <Unavailable />}
      <div className="dv2-next-step">
        <span>Next step</span>
        <strong>{nextStep ?? "Unavailable"}</strong>
      </div>
    </DashboardPanel>
  );
}

export function BullBearPowerCard({ data }: { data: DashboardStatus | null }) {
  const root = asRecord(data);
  const diagnostics = asRecord(data?.alert_diagnostics);
  const bullish = asNumber(root.long_score) ?? asNumber(diagnostics.long_score);
  const bearish = asNumber(root.short_score) ?? asNumber(diagnostics.short_score);
  const total = (bullish ?? 0) + (bearish ?? 0);
  const bullWidth = total > 0 ? ((bullish ?? 0) / total) * 100 : 0;
  const bearWidth = total > 0 ? ((bearish ?? 0) / total) * 100 : 0;

  return (
    <DashboardPanel title="Bull / Bear power" className="dv2-decision-card dv2-power-card">
      {bullish === null && bearish === null ? (
        <Unavailable>Power scores unavailable.</Unavailable>
      ) : (
        <div className="dv2-power">
          <div>
            <span className="dv2-bull">Bull</span>
            <strong className="dv2-bull">{Math.round(bullish ?? 0)}</strong>
          </div>
          <div className="dv2-power-track">
            <i className="is-bull" style={{ width: `${bullWidth}%` }} />
            <i className="is-bear" style={{ width: `${bearWidth}%` }} />
          </div>
          <div>
            <span className="dv2-bear">Bear</span>
            <strong className="dv2-bear">{Math.round(bearish ?? 0)}</strong>
          </div>
        </div>
      )}
    </DashboardPanel>
  );
}
