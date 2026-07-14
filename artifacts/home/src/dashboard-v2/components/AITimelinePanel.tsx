import { memo, useLayoutEffect, useRef } from "react";
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

export const AITimelinePanel = memo(function AITimelinePanel({
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
  const anchorRef = useRef<{ id: string; offset: number; distanceFromBottom: number } | null>(null);

  const captureAnchor = () => {
    const viewport = viewportRef.current;
    if (!viewport || wasAtTopRef.current) {
      anchorRef.current = null;
      return;
    }
    const viewportTop = viewport.getBoundingClientRect().top;
    const items = Array.from(viewport.querySelectorAll<HTMLElement>("[data-event-id]"));
    const firstVisible = items.find((item) => item.getBoundingClientRect().bottom >= viewportTop);
    anchorRef.current = firstVisible
      ? {
          id: firstVisible.dataset.eventId ?? "",
          offset: firstVisible.getBoundingClientRect().top - viewportTop,
          distanceFromBottom: viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop,
        }
      : null;
  };

  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const anchor = anchorRef.current;
    if (!wasAtTopRef.current && anchor?.id) {
      const item = Array.from(
        viewport.querySelectorAll<HTMLElement>("[data-event-id]"),
      ).find((candidate) => candidate.dataset.eventId === anchor.id);
      if (item) {
        const viewportTop = viewport.getBoundingClientRect().top;
        const currentOffset = item.getBoundingClientRect().top - viewportTop;
        viewport.scrollTop += currentOffset - anchor.offset;
      } else {
        viewport.scrollTop = Math.max(
          0,
          viewport.scrollHeight - viewport.clientHeight - anchor.distanceFromBottom,
        );
      }
    }
    captureAnchor();
  }, [events]);

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
          captureAnchor();
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
              <li key={item.id} data-event-id={item.id} className={`is-${item.tone}`}>
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
});
