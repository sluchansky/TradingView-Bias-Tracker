import type { DashboardStatus } from "../types";
import { asRecord } from "../types";
import { DashboardPanel, Unavailable } from "./Panel";

const EVIDENCE_KEYS: Array<[string, string]> = [
  ["structure_confirmed", "Structure"],
  ["zone_valid", "Zone"],
  ["vwap_confirmed", "VWAP"],
  ["cvd_confirmed", "CVD"],
  ["volume_confirmed", "Volume"],
  ["liquidity_sweep", "Sweep"],
];

export function EvidenceSnapshotPanel({ data }: { data: DashboardStatus | null }) {
  const gate = asRecord(data?.gate_debug);
  const confluences = asRecord(data?.confluences);
  const items = EVIDENCE_KEYS.map(([key, label]) => {
    const raw = gate[key] ?? confluences[key] ?? (
      key === "zone_valid" ? confluences.zone_confirmed : undefined
    );
    return { label, value: typeof raw === "boolean" ? raw : null };
  });
  const hasEvidence = items.some((item) => item.value !== null);

  return (
    <DashboardPanel title="Evidence snapshot" eyebrow="Current gate inputs">
      {hasEvidence ? (
        <div className="dv2-evidence-grid">
          {items.map((item) => (
            <div
              key={item.label}
              className={item.value === true ? "is-pass" : item.value === false ? "is-fail" : "is-unknown"}
            >
              <i>{item.value === true ? "✓" : item.value === false ? "×" : "·"}</i>
              <span>{item.label}</span>
            </div>
          ))}
        </div>
      ) : (
        <Unavailable>Evidence will populate when gate diagnostics are available.</Unavailable>
      )}
    </DashboardPanel>
  );
}
