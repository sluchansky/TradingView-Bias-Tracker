import type { DashboardStatus } from "../types";
import { asNumber, asRecord, asString } from "../types";
import { DashboardPanel, Unavailable } from "./Panel";

export function MainReasonCard({ data }: { data: DashboardStatus | null }) {
  const brain = asRecord(data?.main_brain);
  const edge = asNumber(brain.edge_score) ?? asNumber(data?.edge_score);
  const price = asNumber(data?.current_price);
  const vwap = asNumber(data?.vwap_value);
  const structure = asString(data?.market_structure);
  const reason = (
    asString(data?.strict_reason)
    ?? asString(brain.summary)
  );
  const nextStep = asString(data?.stage_next_step);
  const recommendation = asString(data?.recommendation) ?? asString(data?.action);
  const vwapRelation = price !== null && vwap !== null
    ? price === vwap ? "at" : price > vwap ? "above" : "below"
    : null;
  const sameAsReason = (value: string | null) =>
    value !== null && reason !== null && value.trim().toLowerCase() === reason.trim().toLowerCase();
  const finalInstruction = nextStep && !sameAsReason(nextStep)
    ? `The next step is: ${nextStep}`
    : recommendation && !sameAsReason(recommendation)
      ? `The current recommendation is: ${recommendation}`
      : null;
  const briefing = [
    structure ? `I'm monitoring ${structure.toLowerCase()}.` : null,
    edge !== null ? `Current edge is ${Math.round(edge)}/110.` : null,
    vwapRelation ? `Price is ${vwapRelation} VWAP.` : null,
    reason ? `My current read: ${reason}` : null,
    finalInstruction,
  ].filter((line, index, lines): line is string =>
    line !== null && lines.indexOf(line) === index
  );

  return (
    <DashboardPanel title="AI briefing" className="dv2-decision-card dv2-reason-card">
      {briefing.length ? (
        <div className="dv2-briefing-copy">
          {briefing.map((line) => <p key={line}>{line}</p>)}
        </div>
      ) : <Unavailable>Live briefing unavailable.</Unavailable>}
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
