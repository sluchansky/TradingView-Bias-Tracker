import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ConnectionState, DashboardStatus, DashboardTicker } from "./types";
import type { AITimelineEvent, AITimelineSnapshot } from "./aiTimelineTypes";
import {
  composeInstrumentSelectionEvent,
  composeTimelineEvents,
  createTimelineSnapshot,
  mergeTimelineEvents,
} from "./composeTimelineEvents";
import {
  persistTimeline,
  restoreTimeline,
  timelineSessionDate,
  timelineStorageKey,
  type TimelineStorage,
} from "./aiTimelineStorage";

function browserStorage(): TimelineStorage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function useAITimeline({
  ticker,
  connection,
  data,
}: {
  ticker: DashboardTicker;
  connection: ConnectionState;
  data: DashboardStatus | null;
}) {
  const date = timelineSessionDate();
  const storageKey = useMemo(() => timelineStorageKey(ticker, date), [date, ticker]);
  const [events, setEvents] = useState<AITimelineEvent[]>([]);
  const [newEventCount, setNewEventCount] = useState(0);
  const eventsRef = useRef<AITimelineEvent[]>([]);
  const previousSnapshotRef = useRef<AITimelineSnapshot | null>(null);
  const previousTickerRef = useRef<DashboardTicker | null>(null);
  const viewingNewestRef = useRef(true);
  const activeScopeRef = useRef("");

  useEffect(() => {
    const storage = browserStorage();
    const restored = storage ? restoreTimeline(storage, storageKey) : [];
    const selectionEvent = previousTickerRef.current
      ? composeInstrumentSelectionEvent(previousTickerRef.current, ticker)
      : null;
    const scopedEvents = selectionEvent
      ? mergeTimelineEvents(restored, [selectionEvent])
      : restored;
    activeScopeRef.current = storageKey;
    previousSnapshotRef.current = null;
    previousTickerRef.current = ticker;
    viewingNewestRef.current = true;
    setNewEventCount(0);
    eventsRef.current = scopedEvents;
    setEvents(scopedEvents);
    if (storage && selectionEvent) persistTimeline(storage, storageKey, scopedEvents);
  }, [storageKey, ticker]);

  useEffect(() => {
    if (activeScopeRef.current !== storageKey) return;
    const current = createTimelineSnapshot({ ticker, connection, data });
    const incoming = composeTimelineEvents(previousSnapshotRef.current, current);
    previousSnapshotRef.current = current;
    if (!incoming.length) return;

    const existing = eventsRef.current;
    const merged = mergeTimelineEvents(existing, incoming);
    const existingIds = new Set(existing.map((item) => item.id));
    const added = merged.filter((item) => !existingIds.has(item.id)).length;
    if (!added) return;
    eventsRef.current = merged;
    setEvents(merged);
    if (!viewingNewestRef.current) {
      setNewEventCount((count) => count + added);
    }
    const storage = browserStorage();
    if (storage) persistTimeline(storage, storageKey, merged);
  }, [connection, data, storageKey, ticker]);

  const setViewingNewest = useCallback((viewingNewest: boolean) => {
    viewingNewestRef.current = viewingNewest;
    if (viewingNewest) setNewEventCount(0);
  }, []);

  return { events, newEventCount, setViewingNewest };
}
