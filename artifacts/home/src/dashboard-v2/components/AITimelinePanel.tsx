import { useLayoutEffect, useRef } from "react";
import type { AITimelineEvent } from "../aiTimelineTypes";

function eventTime(timestamp: string): string {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      });
}

export function AITimelinePanel({
  events,
  newEventCount,
  onViewingNewestChange,
}: {
  events: AITimelineEvent[];
  newEventCount: number;
  onViewingNewestChange: (viewingNewest: boolean) => void;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const wasAtTopRef = useRef(true);
  const previousHeightRef = useRef(0);

  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const nextHeight = viewport.scrollHeight;
    if (!wasAtTopRef.current && previousHeightRef.current > 0) {
      viewport.scrollTop += nextHeight - previousHeightRef.current;
    }
    previousHeightRef.current = nextHeight;
  }, [events.length]);

  const showNewest = () => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    viewport.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
    wasAtTopRef.current = true;
    onViewingNewestChange(true);
  };

  return (
    <section className="dv2-ai-timeline" aria-label="AI reasoning timeline">
      {newEventCount > 0 && (
        <button className="dv2-timeline-new" type="button" onClick={showNewest}>
          {newEventCount} new event{newEventCount === 1 ? "" : "s"}
        </button>
      )}
      <div
        ref={viewportRef}
        className="dv2-timeline-viewport"
        onScroll={(event) => {
          const atTop = event.currentTarget.scrollTop <= 8;
          wasAtTopRef.current = atTop;
          onViewingNewestChange(atTop);
        }}
      >
        {events.length === 0 ? (
          <div className="dv2-timeline-empty">
            <strong>Waiting for the first market event.</strong>
            <span>The timeline will begin when live status data arrives.</span>
          </div>
        ) : (
          <ol className="dv2-timeline-list">
            {events.map((item) => (
              <li key={item.id} className={`is-${item.tone}`}>
                <i aria-hidden="true" />
                <time dateTime={item.timestamp}>{eventTime(item.timestamp)}</time>
                <strong>{item.category}</strong>
                <p>{item.message}</p>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
