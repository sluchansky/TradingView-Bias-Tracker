import { useEffect, useMemo, useState } from "react";
import { AvatarSettingsPanel } from "@/components/avatar/AvatarSettingsPanel";
import type { AvatarState } from "@/components/avatar/avatarTypes";
import { useAvatarSelection } from "@/components/avatar/useAvatarSelection";
import { AvatarPanel } from "@/dashboard-v2/components/AvatarPanel";
import { ChartPanel } from "@/dashboard-v2/components/ChartPanel";
import { ActiveAlertsPanel, PositionsPanel } from "@/dashboard-v2/components/CommandBottomPanels";
import { DashboardV2Header } from "@/dashboard-v2/components/DashboardV2Header";
import { DashboardV2Login } from "@/dashboard-v2/components/DashboardV2Login";
import { BullBearPowerCard, MainReasonCard } from "@/dashboard-v2/components/DecisionCards";
import { IntelligenceRail } from "@/dashboard-v2/components/IntelligenceRail";
import { LevelsPanel } from "@/dashboard-v2/components/LevelsPanel";
import { MarketContextPanel } from "@/dashboard-v2/components/MarketContextPanel";
import { NewsSessionPanel } from "@/dashboard-v2/components/SessionPanels";
import { TalkToAvatarPanel } from "@/dashboard-v2/components/TalkToAvatarPanel";
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
  const [assistantThinking, setAssistantThinking] = useState(false);
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
  const operatorState = asString(brain.status) ?? asString(dashboard.data?.market_status);
  const nextStep = asString(dashboard.data?.stage_next_step);
  const readyToTrade = (operatorState ?? "").toUpperCase() === "READY";
  const operatorTone = dashboard.connection === "connected"
    ? "live"
    : dashboard.connection === "stale" || dashboard.connection === "warming"
      ? "caution"
      : dashboard.connection === "error"
        ? "error"
        : "idle";
  const connectedOperatorStatus = voice.speaking
    ? "Explaining setup"
    : assistantThinking || voice.voiceState === "processing" || voice.voiceState === "requesting"
      ? "Evaluating setup"
      : readyToTrade
        ? "Ready to trade"
        : nextStep && /\bBOS\b/i.test(nextStep)
          ? "Waiting for BOS"
          : nextStep && /liquidity/i.test(nextStep)
            ? "Reviewing liquidity"
            : nextStep && /VWAP/i.test(nextStep)
              ? "Watching VWAP"
              : nextStep && /order flow|CVD|volume/i.test(nextStep)
                ? "Evaluating order flow"
                : nextStep && /confirm/i.test(nextStep)
                  ? "Waiting for confirmation"
                  : `Monitoring ${dashboard.ticker}`;
  const operatorStatus = dashboard.connection === "connected"
    ? connectedOperatorStatus
    : dashboard.connection === "stale"
      ? `Data stale · ${dashboard.ticker}`
      : dashboard.connection === "error"
        ? `Connection error · ${dashboard.ticker}`
        : dashboard.connection === "loading" || dashboard.connection === "warming"
          ? `Connecting to ${dashboard.ticker}`
          : `Waiting to connect · ${dashboard.ticker}`;

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

      <main className="dv2-command-center">
        <aside className="dv2-command-left">
          <AvatarPanel
            avatarState={avatarState}
            speaking={voice.speaking}
            speechCtrlRef={voice.speechCtrlRef}
            voiceListeningRef={voice.voiceListeningRef}
            selection={avatarSelection}
            voiceState={voice.voiceState}
            operatorStatus={operatorStatus}
            operatorTone={operatorTone}
            aiThinking={assistantThinking || voice.voiceState === "processing" || voice.voiceState === "requesting"}
            dataUnavailable={!dashboard.data || dashboard.connection === "loading"
              || dashboard.connection === "warming" || dashboard.connection === "error"}
          />
          <TalkToAvatarPanel
            title="Speak to Lord Piggington"
            askAssistant={dashboard.askAssistant}
            speak={voice.speak}
            voiceState={voice.voiceState}
            transcript={voice.transcript}
            voiceError={voice.voiceError}
            startListening={voice.startListening}
            stopListening={voice.stopListening}
            markIdle={voice.markIdle}
            onThinkingChange={setAssistantThinking}
          />
          <section className="dv2-voice-output" aria-label="Voice output status">
            <span>Voice output</span>
            <strong>{voice.muted
              ? "Muted"
              : voice.speaking
                ? "Speaking"
                : voice.voiceState === "listening"
                  ? "Listening"
                  : "Ready"}</strong>
            <small>{voice.voiceError ?? "Browser speech and microphone status"}</small>
          </section>
        </aside>

        <section className="dv2-command-main">
          <div className="dv2-decision-row">
            <VerdictHero
              data={dashboard.data}
              loading={dashboard.connection === "loading" || dashboard.connection === "warming"}
            />
            <MainReasonCard data={dashboard.data} />
            <MarketContextPanel data={dashboard.data} />
            <BullBearPowerCard data={dashboard.data} />
          </div>
          <ChartPanel data={dashboard.data} points={dashboard.priceHistory} />
        </section>

        <aside className="dv2-command-right">
          <IntelligenceRail data={dashboard.data} />
        </aside>

        <section className="dv2-command-bottom">
          <LevelsPanel data={dashboard.data} />
          <NewsSessionPanel data={dashboard.data} />
          <ActiveAlertsPanel data={dashboard.data} />
          <PositionsPanel data={dashboard.data} />
        </section>
      </main>
    </div>
  );
}
