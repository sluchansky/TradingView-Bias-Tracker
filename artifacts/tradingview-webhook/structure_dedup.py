"""structure_dedup.py
Eliminates duplicate influence when the same structural or sweep event is
observed by both TradingView and Databento.

Canonical rule
──────────────
  Databento is the canonical source for all events it produces.

  When Databento fires an event:
    • The new Databento record gets  source="databento",  canonical=True.
    • Any existing TV entry in ALERT_HISTORY that matches the same logical event
      is retroactively marked  canonical=False, duplicate_of=<db_ts>.

  When TradingView fires an event:
    • The new TV record gets  source="tradingview".
    • If a matching Databento canonical already exists, the TV record gets
      canonical=False, duplicate_of=<db_ts>.
    • If no Databento match exists, TV gets  canonical=True  (fallback / legacy).

Downstream readers
──────────────────
  Every ALERT_HISTORY consumer that influences the gate or Edge Score should
  add::

      if a.get("canonical") is False:
          continue

  after the alert_type check.  Entries without a ``canonical`` field (legacy
  records from before this module was deployed) return ``None`` for
  ``a.get("canonical")`` → ``None is False`` evaluates to False → the entry
  is NOT skipped → full backward compatibility preserved.

Matching criteria (ALL must hold — same for structure and sweep)
──────────────────────────────────────────────────────────────────
  • same alert_type  (already normalised, instrument prefix included for sweeps)
  • same instrument
  • |ts_a − ts_b| ≤ DEDUP_TIME_SECS  (90 s ≈ 1.5 × 60-second bars)
  • |price_a − price_b| ≤ DEDUP_PRICE_TICKS × tick_size  (10 ticks)
    — price check skipped if either entry has no price (fail-open)

Fail-open everywhere: any exception leaves ``canonical`` unset (treated as
True by all downstream readers, preserving existing behaviour).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


# ── Structure-event taxonomy ────────────────────────────────────────────────────

#: The complete set of normalised structure alert_type strings produced by both
#: TradingView Pine scripts and DatabentoBrain.  No bare "BOS"/"CHOCH" strings
#: exist in production — the SUPPLY/DEMAND suffixes encode direction.
STRUCTURE_TYPES: frozenset = frozenset({
    "BOS DEMAND",   "BOS SUPPLY",
    "CHOCH DEMAND", "CHOCH SUPPLY",
    "HH", "HL",
    "LH", "LL",
})

# Directional tag (used for conflict detection only)
STRUCTURE_DIRECTION: Dict[str, str] = {
    "BOS DEMAND":   "BULLISH",  "BOS SUPPLY":   "BEARISH",
    "CHOCH DEMAND": "BULLISH",  "CHOCH SUPPLY": "BEARISH",
    "HH":           "BULLISH",  "HL":           "BULLISH",
    "LH":           "BEARISH",  "LL":           "BEARISH",
}

# Logical family (BOS, CHOCH, or SWING pivot)
STRUCTURE_FAMILY: Dict[str, str] = {
    "BOS DEMAND":   "BOS",    "BOS SUPPLY":   "BOS",
    "CHOCH DEMAND": "CHOCH",  "CHOCH SUPPLY": "CHOCH",
    "HH":           "SWING",  "HL":           "SWING",
    "LH":           "SWING",  "LL":           "SWING",
}


# ── Sweep-event taxonomy ─────────────────────────────────────────────────────────

_LIVE_INSTRUMENTS: Tuple[str, ...] = ("MGC", "MNQ", "MES", "MYM")

#: Prefixed sweep alert_type strings produced by both TradingView Pine scripts
#: and DatabentoBrain._detect_sweep().  Format: "{inst} BULLISH SWEEP" or
#: "{inst} BEARISH SWEEP".  Instrument is embedded in the type so consumers
#: use ticker_scoped=True when calling _latest_ts / _has.
SWEEP_TYPES: frozenset = frozenset(
    f"{inst} {d} SWEEP"
    for inst in _LIVE_INSTRUMENTS
    for d in ("BULLISH", "BEARISH")
)

SWEEP_DIRECTION: Dict[str, str] = {
    **{f"{inst} BULLISH SWEEP": "BULLISH" for inst in _LIVE_INSTRUMENTS},
    **{f"{inst} BEARISH SWEEP": "BEARISH" for inst in _LIVE_INSTRUMENTS},
}

SWEEP_FAMILY: Dict[str, str] = {t: "SWEEP" for t in SWEEP_TYPES}


# ── Combined maps (used by conflict detector for both families) ─────────────────

_ALL_DIRECTION: Dict[str, str] = {**STRUCTURE_DIRECTION, **SWEEP_DIRECTION}
_ALL_FAMILY:    Dict[str, str]  = {**STRUCTURE_FAMILY,   **SWEEP_FAMILY}

#: Every alert_type handled by this module (structure + sweep).
ALL_DEDUP_TYPES: frozenset = STRUCTURE_TYPES | SWEEP_TYPES


# ── Shared tolerances ────────────────────────────────────────────────────────────

# Per-instrument minimum tick size for price tolerance calculations
INSTRUMENT_TICK_SIZE: Dict[str, float] = {
    "MGC": 0.10, "GC":  0.10,
    "MNQ": 0.25, "NQ":  0.25,
    "MES": 0.25, "ES":  0.25,
    "MYM": 1.00, "YM":  1.00,
}
_DEFAULT_TICK_SIZE = 0.25

#: Seconds within which two events from different sources are considered to
#: represent the same logical bar-close event.
#: 90 s ≈ 1.5 × 60-second bar cadence — wide enough to absorb any latency
#: difference between TV webhook delivery and Databento bar-close callback
#: without accidentally merging events from genuinely different bars.
#: Same value is appropriate for sweeps: Databento fires on bar close, TV Pine
#: also fires within seconds of the same bar close.
DEDUP_TIME_SECS: int = 90

#: Price tolerance expressed in ticks.  10 ticks covers normal spread between
#: TV's alert price (current price at Pine alert time) and Databento's bar-close
#: price.  Both sources use bar-close price for sweeps, so 10 ticks is generous.
DEDUP_PRICE_TICKS: int = 10

#: Counter keys shared by both the structure and sweep metric dicts.
_METRIC_KEYS: Tuple[str, ...] = (
    "tv_events_received",
    "databento_events_produced",
    "matched_events",
    "tv_fallback_events",
    "unmatched_databento_events",
    "deduped_events",       # duplicates suppressed from live consumers
    "conflict_events",
)


# ── Internal helpers ────────────────────────────────────────────────────────────

def _parse_ts(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string to an aware datetime, or None."""
    if not ts_str:
        return None
    try:
        ts = datetime.fromisoformat(ts_str)
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _price_tolerance(inst: str) -> float:
    """Absolute price tolerance for instrument *inst* (tick_size × DEDUP_PRICE_TICKS)."""
    return INSTRUMENT_TICK_SIZE.get(inst, _DEFAULT_TICK_SIZE) * DEDUP_PRICE_TICKS


def _is_databento(entry: Dict) -> bool:
    """True when *entry* was produced by DatabentoBrain."""
    src = entry.get("source") or entry.get("instrument_source") or ""
    return src == "databento"


def _events_match(a: Dict, b: Dict) -> bool:
    """True when *a* and *b* represent the same logical event.

    Requires identical alert_type AND instrument, time within DEDUP_TIME_SECS,
    and price within DEDUP_PRICE_TICKS ticks (price check is skipped when
    either entry has no price, to preserve fail-open behaviour).
    Works for both structure and sweep events.
    """
    if a is b:
        return False
    if a.get("alert_type") != b.get("alert_type"):
        return False
    if a.get("instrument") != b.get("instrument"):
        return False
    ts_a = _parse_ts(a.get("timestamp"))
    ts_b = _parse_ts(b.get("timestamp"))
    if ts_a is None or ts_b is None:
        return False
    if abs((ts_a - ts_b).total_seconds()) > DEDUP_TIME_SECS:
        return False
    p_a = a.get("price")
    p_b = b.get("price")
    if p_a is not None and p_b is not None:
        tol = _price_tolerance(a.get("instrument") or b.get("instrument") or "")
        if abs(float(p_a) - float(p_b)) > tol:
            return False
    return True


def _has_conflict(entry: Dict, history: List[Dict]) -> bool:
    """Return True when a Databento event of the same family but opposite
    direction exists for the same instrument within the dedup window.

    Works for both STRUCTURE and SWEEP families.
    """
    a_type    = entry.get("alert_type", "")
    family    = _ALL_FAMILY.get(a_type)
    direction = _ALL_DIRECTION.get(a_type)
    if not family or not direction:
        return False

    inst = entry.get("instrument", "")
    ts   = _parse_ts(entry.get("timestamp"))
    if ts is None:
        return False

    # Collect all types with same family but opposite direction
    opposite_types = frozenset(
        t for t, f in _ALL_FAMILY.items()
        if f == family and _ALL_DIRECTION.get(t) != direction
    )

    for h in history:
        if h.get("alert_type") not in opposite_types:
            continue
        if h.get("instrument") != inst:
            continue
        if not _is_databento(h):
            continue
        h_ts = _parse_ts(h.get("timestamp"))
        if h_ts and abs((ts - h_ts).total_seconds()) <= DEDUP_TIME_SECS:
            return True
    return False


# ── Dedup engine ────────────────────────────────────────────────────────────────

class StructureDedup:
    """Thread-safe event deduplication engine for structure and sweep alerts.

    Tracks separate counters for STRUCTURE events (BOS/CHOCH/HH/HL/LH/LL) and
    SWEEP events ({inst} BULLISH/BEARISH SWEEP) so mismatches can be diagnosed
    per subsystem.

    Singleton usage::

        from structure_dedup import STRUCTURE_DEDUP
        STRUCTURE_DEDUP.on_tv_event(record, list(ALERT_HISTORY))
        STRUCTURE_DEDUP.on_databento_event(record, list(self._ah))
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        #: Per-family monotonically-increasing counters since last server restart.
        self._structure_metrics: Dict[str, int] = {k: 0 for k in _METRIC_KEYS}
        self._sweep_metrics:     Dict[str, int] = {k: 0 for k in _METRIC_KEYS}

    # ── Public routing ──────────────────────────────────────────────────────────

    def on_databento_event(self, new_record: Dict, history: List[Dict]) -> None:
        """Tag *new_record* canonical and retroactively demote matching TV entries.

        Must be called AFTER *new_record* has been appended to ALERT_HISTORY
        (so it is visible to future readers) but *history* is the snapshot taken
        BEFORE the append — so *new_record* is not in *history*.

        Routes automatically to structure or sweep metrics based on alert_type.
        No-op for unrecognised types (preserves existing behaviour).

        Args:
            new_record: The Databento event dict, just appended.
            history:    A list() snapshot of ALERT_HISTORY taken before the append.
        """
        new_record.setdefault("source",    "databento")
        new_record["canonical"] = True

        a_type = new_record.get("alert_type", "")
        if a_type in STRUCTURE_TYPES:
            self._on_databento(new_record, history, self._structure_metrics)
        elif a_type in SWEEP_TYPES:
            self._on_databento(new_record, history, self._sweep_metrics)
        # else: unrecognised type — canonical=True already set, no counter bump

    def on_tv_event(self, new_record: Dict, history: List[Dict]) -> None:
        """Tag *new_record* with source and canonical status before it is appended.

        If a matching canonical Databento event already exists in *history*, the
        TV record is marked shadow (canonical=False).  Otherwise it is canonical
        (TV fallback).  Routes automatically to structure or sweep metrics.

        Args:
            new_record: The TV event dict, NOT YET appended to ALERT_HISTORY.
            history:    A list() snapshot of ALERT_HISTORY (does not contain new_record).
        """
        new_record["source"] = "tradingview"

        a_type = new_record.get("alert_type", "")
        if a_type in STRUCTURE_TYPES:
            self._on_tv(new_record, history, self._structure_metrics)
        elif a_type in SWEEP_TYPES:
            self._on_tv(new_record, history, self._sweep_metrics)
        else:
            # Non-structure, non-sweep TV alert — canonical by default
            new_record["canonical"] = True

    # ── Core shared logic (structure and sweep identical) ───────────────────────

    def _on_databento(
        self,
        new_record: Dict,
        history:    List[Dict],
        m:          Dict[str, int],
    ) -> None:
        with self._lock:
            m["databento_events_produced"] += 1

        db_ts_str = new_record.get("timestamp", "")
        demoted   = 0
        for h in history:
            if h is new_record:
                continue
            if h.get("canonical") is False:
                continue   # already a shadow — don't double-demote
            if _is_databento(h):
                continue   # never retroactively demote another Databento event
            if not _events_match(new_record, h):
                continue
            # h is a TV (or untagged legacy) event matching this Databento event
            h["canonical"]      = False
            h["duplicate_of"]   = db_ts_str
            h["matched_source"] = "databento"
            demoted += 1

        with self._lock:
            m["matched_events"]            += demoted
            m["deduped_events"]            += demoted
            if demoted == 0:
                m["unmatched_databento_events"] += 1

    def _on_tv(
        self,
        new_record: Dict,
        history:    List[Dict],
        m:          Dict[str, int],
    ) -> None:
        with self._lock:
            m["tv_events_received"] += 1

        # Scan for an existing canonical Databento event that matches
        db_match: Optional[Dict] = None
        for h in reversed(history):   # newest → oldest: find most recent match
            if not _is_databento(h):
                continue
            if h.get("canonical") is False:
                continue
            if _events_match(new_record, h):
                db_match = h
                break

        if db_match is not None:
            new_record["canonical"]      = False
            new_record["duplicate_of"]   = db_match.get("timestamp", "")
            new_record["matched_source"] = "databento"
            with self._lock:
                m["matched_events"] += 1
                m["deduped_events"] += 1
        else:
            new_record["canonical"] = True
            with self._lock:
                m["tv_fallback_events"] += 1
            # Flag cross-source conflict: same family, opposite direction from Databento
            if _has_conflict(new_record, history):
                new_record["conflict"] = True
                with self._lock:
                    m["conflict_events"] += 1

    # ── Metrics ─────────────────────────────────────────────────────────────────

    def get_metrics(self) -> Dict:
        """Return a point-in-time snapshot of all counters, namespaced by family.

        Returns::

            {
                "structure": { "tv_events_received": N, ... },
                "sweep":     { "tv_events_received": N, ... },
            }

        The ``deduped_events`` counter in each family equals the number of
        duplicates suppressed from live gate/Edge-Score consumers.
        """
        with self._lock:
            return {
                "structure": dict(self._structure_metrics),
                "sweep":     dict(self._sweep_metrics),
            }

    # ── Legacy compat shim (tests that read flat dict) ──────────────────────────

    @property
    def metrics(self) -> Dict[str, int]:
        """Aggregate of structure + sweep counters as a flat dict.
        Preserved so any code that used the old flat `.metrics` attribute
        continues to work (values are sums across both families).
        """
        with self._lock:
            return {
                k: self._structure_metrics[k] + self._sweep_metrics[k]
                for k in _METRIC_KEYS
            }


# ── Module-level singleton ──────────────────────────────────────────────────────
#: Import this in app.py and databento_brain.py.
STRUCTURE_DEDUP: StructureDedup = StructureDedup()
