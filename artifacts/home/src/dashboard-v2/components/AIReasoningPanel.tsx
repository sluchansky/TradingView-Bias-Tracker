import type { DashboardStatus } from "../types";
import { asRecord, asString } from "../types";
import { DashboardPanel, Unavailable } from "./Panel";

export function AIReasoningPanel({ data }: { data: DashboardStatus | null }) {
  const voice = asRecord(data?.main_brain_voice);
  const brain = asRecord(data?.main_brain);
  const analyst = asRecord(data?.analyst_report);
  const reasoning = (
    asString(voice.narration)
    ?? asString(brain.summary)
    ?? asString(analyst.summary)
  );

  return (
    <DashboardPanel title="AI reasoning" eyebrow="Why this verdict">
      {reasoning ? (
        <p className="dv2-reasoning">{reasoning}</p>
      ) : (
        <Unavailable>AI reasoning is not available for this snapshot.</Unavailable>
      )}
    </DashboardPanel>
  );
}
