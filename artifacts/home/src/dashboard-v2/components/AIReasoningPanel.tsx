import type { DashboardStatus } from "../types";
import { asRecord, asString } from "../types";
import { Unavailable } from "./Panel";

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
    <section className="dv2-conversation-card" aria-labelledby="dv2-reasoning-title">
      <div className="dv2-conversation-speaker" aria-hidden="true">AI</div>
      <div>
        <span className="dv2-eyebrow">AI partner’s read</span>
        <h2 id="dv2-reasoning-title">Why I see it this way</h2>
        {reasoning ? (
          <p className="dv2-reasoning">{reasoning}</p>
        ) : (
          <Unavailable>AI reasoning is not available for this snapshot.</Unavailable>
        )}
      </div>
    </section>
  );
}
