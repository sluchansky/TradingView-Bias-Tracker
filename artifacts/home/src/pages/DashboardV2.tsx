import { useEffect, useMemo, useState } from "react";
import { AvatarSettingsPanel } from "@/components/avatar/AvatarSettingsPanel";
import type { AvatarState } from "@/components/avatar/avatarTypes";
import { useAvatarSelection } from "@/components/avatar/useAvatarSelection";
import { AIReasoningPanel } from "@/dashboard-v2/components/AIReasoningPanel";
import { AvatarPanel } from "@/dashboard-v2/components/AvatarPanel";
import { ChartPanel } from "@/dashboard-v2/components/ChartPanel";
import { CollapsibleSection } from "@/dashboard-v2/components/CollapsibleSection";
import { DashboardV2Header } from "@/dashboard-v2/components/DashboardV2Header";
import { DashboardV2Login } from "@/dashboard-v2/components/DashboardV2Login";
import { EvidenceSnapshotPanel } from "@/dashboard-v2/components/EvidenceSnapshotPanel";
import { KeyObservationsPanel } from "@/dashboard-v2/components/KeyObservationsPanel";
import { MarketContextPanel } from "@/dashboard-v2/components/MarketContextPanel";
import { MarketHistoryPanel } from "@/dashboard-v2/components/MarketHistoryPanel";
import { MarketStatusPanel } from "@/dashboard-v2/components/MarketStatusPanel";
import { NewsSessionPanel, ObjectivePanel, SessionMemoryPanel, SessionPerformancePanel } from "@/dashboard-v2/components/SessionPanels";
import { TalkToAvatarPanel } from "@/dashboard-v2/components/TalkToAvatarPanel";
import { TradePlanPanel } from "@/dashboard-v2/components/TradePlanPanel";
import { VerdictHero } from "@/dashboard-v2/components/VerdictHero";
import { asNumber, asRecord, asString } from "@/dashboard-v2/types";
import { useDashboardV2Data } from "@/dashboard-v2/useDashboardV2Data";
import { useDashboardV2Voice } from "@/dashboard-v2/useDashboardV2Voice";
import "@/dashboard-v2/dashboard-v2.css";

export default function DashboardV2() {
  const dashboard = useDashboardV2Data();
  const voice = useDashboardV2Voice();
  const avatarSelection = useAvatarSelection();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const brain = asRecord(dashboard.data?.main_brain);

  useEffect(() => {
    if (!settingsOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSettingsOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [settingsOpen]);

  const avatarState = useMemo<AvatarState>(() => {
    if (!dashboard.data) return "WAIT";
    const status = (asString(brain.status) ?? asString(dashboard.data.verdict) ?? "").toUpperCase();
    const direction = asString(brain.favored_direction) ?? asString(dashboard.data.strict_direction) ?? "";
    const edge = asNumber(brain.edge_score) ?? asNumber(dashboard.data.edge_score) ?? 0;
    if (status.includes("READY")) return /short|bear/i.test(direction) ? "READY_SHORT" : "READY_LONG";
    if (status.includes("MANAGING")) return "ACTIVE";
    if (status.includes("BUILD") || edge >= 50) return "FORMING";
    if (edge >= 28) return "ANALYZING";
    if (edge < 20) return "NO_EDGE";
    return "WAIT";
  }, [brain, dashboard.data]);

  if (dashboard.authRequired) {
    return <DashboardV2Login authenticate={dashboard.authenticate} />;
  }

  return (
    <div className="dv2-root">
      <DashboardV2Header
        ticker={dashboard.ticker}
        onTickerChange={dashboard.setTicker}
        data={dashboard.data}
        connection={dashboard.connection}
        muted={voice.muted}
        settingsOpen={settingsOpen}
        onToggleMuted={() => voice.setMuted(!voice.muted)}
        onToggleSettings={() => setSettingsOpen((open) => !open)}
      />

      {settingsOpen && (
        <div
          id="dashboard-v2-settings"
          className="dv2-settings"
          role="region"
          aria-label="Dashboard settings"
        >
          <div>
            <strong>Dashboard V2 settings</strong>
            <span>
              Last update: {dashboard.lastUpdated
                ? new Date(dashboard.lastUpdated).toLocaleTimeString()
                : "Not connected"}
            </span>
          </div>
          <button type="button" onClick={dashboard.refresh}>Refresh now</button>
          <button type="button" onClick={dashboard.clearAuth}>Sign out</button>
          <a href="/">Open current dashboard</a>
          <a href="/api/dashboard">Open engineering dashboard</a>
          <AvatarSettingsPanel selection={avatarSelection} />
        </div>
      )}

      {(dashboard.error || dashboard.connection === "stale") && (
        <div className={`dv2-notice is-${dashboard.connection}`}>
          {dashboard.connection === "stale"
            ? "Live data is stale. The last valid snapshot remains visible."
            : dashboard.error}
        </div>
      )}

      <main className="dv2-dashboard-body">
        <section className="dv2-hero-experience">
          <AvatarPanel
            avatarState={avatarState}
            speaking={voice.speaking}
            speechCtrlRef={voice.speechCtrlRef}
            voiceListeningRef={voice.voiceListeningRef}
            selection={avatarSelection}
          />
          <div className="dv2-hero-intelligence">
            <VerdictHero
              data={dashboard.data}
              loading={dashboard.connection === "loading" || dashboard.connection === "warming"}
            />
            <AIReasoningPanel data={dashboard.data} />
          </div>
        </section>

        <ChartPanel data={dashboard.data} points={dashboard.priceHistory} />

        <section className="dv2-workspace dv2-workspace-secondary">
          <aside className="dv2-column dv2-left-column">
            <MarketContextPanel data={dashboard.data} />
            <ObjectivePanel data={dashboard.data} />
          </aside>

          <section className="dv2-column dv2-center-column">
            <KeyObservationsPanel data={dashboard.data} />
            <div className="dv2-session-grid">
              <SessionPerformancePanel data={dashboard.data} />
              <CollapsibleSection
                title="Session memory"
                summary="Learning context and similar setups"
              >
                <SessionMemoryPanel data={dashboard.data} />
              </CollapsibleSection>
            </div>
            <CollapsibleSection
              title="Market history"
              summary="Recent verdict, bias, structure, and blocker changes"
            >
              <MarketHistoryPanel data={dashboard.data} />
            </CollapsibleSection>
          </section>

          <aside className="dv2-column dv2-right-column">
            <MarketStatusPanel data={dashboard.data} />
            <TradePlanPanel data={dashboard.data} />
            <NewsSessionPanel data={dashboard.data} />
          </aside>
        </section>
      </main>

      <footer className="dv2-bottom">
        <TalkToAvatarPanel
          askAssistant={dashboard.askAssistant}
          speak={voice.speak}
          voiceState={voice.voiceState}
          transcript={voice.transcript}
          voiceError={voice.voiceError}
          startListening={voice.startListening}
          stopListening={voice.stopListening}
          markIdle={voice.markIdle}
        />
        <CollapsibleSection
          title="Evidence snapshot"
          summary="Gate inputs and confirmation detail"
        >
          <EvidenceSnapshotPanel data={dashboard.data} />
        </CollapsibleSection>
      </footer>
    </div>
  );
}
