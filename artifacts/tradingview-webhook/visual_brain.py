"""Visual Brain V1 — Multi-Instrument Stateful Market Observer.

Captures the live chart (or a locally-generated candlestick
chart from Databento bars) once per 60 seconds, sends the image plus native
multi-timeframe context and a compact text history to a vision-capable LLM,
receives a strict JSON market state, and persists every observation to
visual_brain_observations.

SHADOW / OBSERVATION ONLY.
- Does NOT place trades.
- Does NOT modify SCALP / INTRADAY_TREND / SWING decision logic.
- Does NOT alter gate thresholds, edge scores, or execution paths.
- All writes FAIL-OPEN — a bug here cannot affect the money path.
- One model call maximum per 300 seconds; screenshot compressed to ≤800px wide.

Flag: VISUAL_BRAIN_ENABLED=true  (default OFF → byte-identical).
Instruments: VISUAL_BRAIN_SYMBOL=MNQ,MGC  (comma-separated; default MNQ and MGC).
"""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
VISUAL_BRAIN_ENABLED   = os.getenv("VISUAL_BRAIN_ENABLED", "false").lower() in ("true", "1", "yes")
VISUAL_BRAIN_SYMBOL    = os.getenv("VISUAL_BRAIN_SYMBOL", "MNQ,MGC")
# Parsed list — used by all multi-instrument logic; VISUAL_BRAIN_SYMBOL kept for compat
_VB_SYMBOLS: list = [s.strip().upper() for s in VISUAL_BRAIN_SYMBOL.split(",") if s.strip()]
VISUAL_BRAIN_INTERVAL  = max(60, int(os.getenv("VISUAL_BRAIN_INTERVAL_SECONDS", "300")))
_VB_MODEL              = "gpt-5.4"               # vision-capable; matches ASSISTANT_MODEL used throughout app.py
_VB_MAX_TOKENS         = 1200
_VB_IMAGE_MAX_PX       = 800                      # max width before JPEG downscale
_VB_HISTORY_LIMIT      = 10                       # last N state transitions passed to model
_VB_SCREENSHOT_TIMEOUT = 30                       # seconds per Playwright capture
_CHART_BARS_LOOKBACK   = 60                       # bars sent to chart renderer
_VB_OPENAI_TIMEOUT_SECONDS = 20
VISUAL_BRAIN_BENCHMARK_ENABLED = os.getenv(
    "VISUAL_BRAIN_BENCHMARK_ENABLED", "false"
).lower() in ("true", "1", "yes", "on")
VISUAL_BRAIN_BENCHMARK_CANDIDATE_ENABLED = os.getenv(
    "VISUAL_BRAIN_BENCHMARK_CANDIDATE_ENABLED", "false"
).lower() in ("true", "1", "yes", "on")
VISUAL_BRAIN_BENCHMARK_CANDIDATE_MODEL = os.getenv(
    "VISUAL_BRAIN_BENCHMARK_CANDIDATE_MODEL", "gpt-4o-mini"
)
VISUAL_BRAIN_BENCHMARK_CANDIDATE_INPUT_COST_PER_MILLION = max(
    0.0,
    float(os.getenv(
        "VISUAL_BRAIN_BENCHMARK_CANDIDATE_INPUT_COST_PER_MILLION", "0.15"
    )),
)
VISUAL_BRAIN_BENCHMARK_CANDIDATE_OUTPUT_COST_PER_MILLION = max(
    0.0,
    float(os.getenv(
        "VISUAL_BRAIN_BENCHMARK_CANDIDATE_OUTPUT_COST_PER_MILLION", "0.60"
    )),
)
VISUAL_BRAIN_BENCHMARK_MAX_STALENESS_SECONDS = max(
    60,
    int(os.getenv("VISUAL_BRAIN_BENCHMARK_MAX_STALENESS_SECONDS", "1800")),
)
VISUAL_BRAIN_BENCHMARK_IMAGE_HAMMING_MAX = max(
    0,
    int(os.getenv("VISUAL_BRAIN_BENCHMARK_IMAGE_HAMMING_MAX", "3")),
)
_BENCHMARK_HISTORY_LIMIT = 200

# ── Module-level state ────────────────────────────────────────────────────────
VB_DB_READY          = False
_VB_LOCK             = threading.Lock()
# Per-instrument last observation cache (replaces single _LAST_OBSERVATION).
_LAST_OBSERVATION_BY_INST: dict = {}   # instrument → most recent parsed observation dict

# ── Injected dependencies (set by check_vb_db_ready() and start()) ────────────
# These are injected at boot time by app.py so that visual_brain.py never needs
# to `import app` itself.  When app.py runs as __main__, doing `import app`
# from a sub-module loads a SECOND copy of app.py under the name `app` with
# empty globals — breaking DB connections, price stores, and bar history.
#
# _db_conn_fn  : callable() → psycopg2 connection | None
# _price_store : dict reference {instrument: {"value": float}} (AUTO_PRICE_BY_TICKER)
# _vwap_store  : dict reference {instrument: {"value": float, ...}} (VWAP_BY_TICKER)
# _bars_fn     : callable(instrument: str) → list[dict]  (live Databento bars)
_db_conn_fn  : Optional[Callable] = None
_price_store : Optional[dict]     = None
_vwap_store  : Optional[dict]     = None
_bars_fn     : Optional[Callable] = None
_research_observation_fn: Optional[Callable] = None
_research_outcome_fn: Optional[Callable] = None
_benchmark_state_fn: Optional[Callable] = None

# ── Cost tracking (resets at midnight ET) ────────────────────────────────────
_COST_LOCK           = threading.Lock()
_vb_calls_today      = 0
_vb_cost_today       = 0.0          # estimated USD
_vb_cost_reset_day   : Optional[str] = None   # "YYYY-MM-DD" in ET

# ── Estimated token costs for gpt-5.4 (per 1M tokens, USD) ──────────────────
# Vision input ≈ $0.15/1M; output ≈ $0.60/1M
_COST_PER_INPUT_TOK  = 0.00000015
_COST_PER_OUTPUT_TOK = 0.00000060

# ── Shadow benchmark state ──────────────────────────────────────────────────
# This state is deliberately in-memory.  It is never read by the evaluator,
# persistence, alerts, Market Student, coordinator, execution, risk, or broker
# paths.  A future local Windows VLM can register a runner in the same namespace
# without changing the canonical OpenAI observer.
_BENCHMARK_LOCK = threading.RLock()
_BENCHMARK_LAST_BY_INST: dict = {}
_BENCHMARK_RECENT: deque = deque(maxlen=_BENCHMARK_HISTORY_LIMIT)
_BENCHMARK_CANDIDATE_RUNNERS: dict[str, Callable] = {}
_BENCHMARK_PENDING_CANDIDATES: dict = {}
_BENCHMARK_ACTIVE_CANDIDATES: set[str] = set()
_BENCHMARK_OPEN_CYCLES: set[str] = set()
_BENCHMARK_COUNTERS: dict = {}
_BENCHMARK_LOCAL = threading.local()
_BENCHMARK_MAX_CANDIDATE_RUNNERS = 8
_BENCHMARK_MAX_INSTRUMENTS = 16
_BENCHMARK_MAX_PENDING_CANDIDATES = 50
_BENCHMARK_MAX_ACTIVE_CANDIDATES = 4


def _benchmark_empty_counters() -> dict:
    return {
        "cycles": 0,
        "baseline_api_calls": 0,
        "baseline_successes": 0,
        "baseline_failures": 0,
        "baseline_retries": 0,
        "baseline_schema_failures": 0,
        "baseline_input_tokens": 0,
        "baseline_output_tokens": 0,
        "baseline_cost_usd": 0.0,
        "candidate_calls": 0,
        "candidate_scheduled": 0,
        "candidate_skipped_busy": 0,
        "candidate_start_failures": 0,
        "candidate_late_or_evicted_results": 0,
        "candidate_successes": 0,
        "candidate_failures": 0,
        "candidate_schema_failures": 0,
        "candidate_input_tokens": 0,
        "candidate_output_tokens": 0,
        "candidate_retries": 0,
        "candidate_cost_usd": 0.0,
        "candidate_errors": {},
        "projected": {
            "no_new_bar_image": {"would_call": 0, "would_skip": 0, "avoided_calls": 0},
            "deterministic_events": {"would_call": 0, "would_skip": 0, "avoided_calls": 0},
            "max_staleness_heartbeat": {"would_call": 0, "would_skip": 0, "avoided_calls": 0},
        },
        "trigger_reasons": {},
        "heartbeat_usage": 0,
        "image_suppression": {"exact": 0, "near_identical": 0, "no_new_bar": 0},
        "input_errors": 0,
        "last_cycle_at": None,
        "last_baseline_success_at": None,
        "max_observed_staleness_seconds": None,
    }


def _benchmark_reset_state() -> None:
    """Reset only in-memory benchmark state; intended for isolated tests."""
    global _BENCHMARK_COUNTERS
    with _BENCHMARK_LOCK:
        _BENCHMARK_LAST_BY_INST.clear()
        _BENCHMARK_RECENT.clear()
        _BENCHMARK_PENDING_CANDIDATES.clear()
        _BENCHMARK_ACTIVE_CANDIDATES.clear()
        _BENCHMARK_OPEN_CYCLES.clear()
        _BENCHMARK_COUNTERS = _benchmark_empty_counters()


_BENCHMARK_COUNTERS = _benchmark_empty_counters()


def register_benchmark_candidate(name: str, runner: Callable) -> None:
    """Register an optional shadow candidate runner.

    `runner(payload)` receives an immutable-by-convention payload containing the
    exact screenshot bytes and deterministic context used by the baseline cycle.
    Its return value is telemetry only.  This interface intentionally also fits a
    local Windows VLM adapter; registering a runner never enables it.
    """
    candidate_name = str(name or "").strip()
    if not candidate_name or not callable(runner):
        raise ValueError("candidate name and callable runner are required")
    with _BENCHMARK_LOCK:
        if (
            candidate_name not in _BENCHMARK_CANDIDATE_RUNNERS
            and len(_BENCHMARK_CANDIDATE_RUNNERS) >= _BENCHMARK_MAX_CANDIDATE_RUNNERS
        ):
            raise RuntimeError("benchmark candidate registry is full")
        _BENCHMARK_CANDIDATE_RUNNERS[candidate_name] = runner


def unregister_benchmark_candidate(name: str) -> None:
    """Remove a previously registered shadow candidate runner."""
    with _BENCHMARK_LOCK:
        _BENCHMARK_CANDIDATE_RUNNERS.pop(str(name or "").strip(), None)


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        return repr(value)


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _safe_copy(value: Any) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _completed_bar_fingerprint(bars: list[dict]) -> Optional[str]:
    """Return an identity for the latest completed native bar."""
    if not bars:
        return None
    last = bars[-1] or {}
    epoch = _bar_epoch(last)
    if epoch is not None:
        return f"{epoch:.6f}"
    fields = {
        key: last.get(key)
        for key in ("open", "high", "low", "close", "volume")
    }
    return _stable_hash(fields) if any(v is not None for v in fields.values()) else None


def _image_fingerprint(image_bytes: Optional[bytes]) -> dict:
    """Return exact and small perceptual fingerprints without persisting pixels."""
    if not image_bytes:
        return {"sha256": None, "ahash": None}
    result = {"sha256": hashlib.sha256(image_bytes).hexdigest(), "ahash": None}
    try:
        from PIL import Image  # noqa: PLC0415
        image = Image.open(io.BytesIO(image_bytes)).convert("L")
        image.thumbnail((16, 16))
        pixels = list(image.getdata())
        if pixels:
            mean = sum(pixels) / len(pixels)
            bits = "".join("1" if px >= mean else "0" for px in pixels)
            result["ahash"] = f"{int(bits, 2):0{len(bits) // 4}x}"
    except Exception:
        # Exact hashing remains useful if optional image decoding is unavailable.
        pass
    return result


def _hamming_distance(left: Optional[str], right: Optional[str]) -> Optional[int]:
    if not left or not right:
        return None
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except (TypeError, ValueError):
        return None


def _drop_volatile_fields(value: Any) -> Any:
    """Normalize deterministic state while ignoring transport timestamps."""
    if isinstance(value, dict):
        return {
            key: _drop_volatile_fields(item)
            for key, item in sorted(value.items())
            if key not in {"ts", "timestamp", "updated_at", "observed_at"}
        }
    if isinstance(value, (list, tuple)):
        return [_drop_volatile_fields(item) for item in value]
    if isinstance(value, float):
        return round(value, 4)
    return value


def _benchmark_semantic_snapshot(snapshot: Optional[dict]) -> dict:
    """Project only meaningful deterministic event families for comparison."""
    if not isinstance(snapshot, dict):
        return {}
    families = {
        "structure": (
            "structure", "structure_event", "bos", "choch", "structure_state",
        ),
        "vwap_levels": (
            "vwap", "levels", "session_levels", "nearest_supply", "nearest_demand",
        ),
        "thesis_ready_blockers": (
            "thesis", "ready", "verdict", "blockers", "setup_state",
        ),
        "volatility": ("volatility", "atr", "volatility_state"),
        "volume": ("volume", "rvol", "volume_spike", "cvd"),
        "session": ("session", "session_state"),
        "recovery": ("recovery", "recovery_events", "data_health"),
    }
    projected = {}
    for family, keys in families.items():
        values = {key: snapshot[key] for key in keys if key in snapshot}
        if values:
            projected[family] = _drop_volatile_fields(values)
    return projected


def _benchmark_session_label(snapshot: Optional[dict]) -> str:
    """Return a bounded session label without exposing the native snapshot."""
    if not isinstance(snapshot, dict):
        return "unknown"
    for key in ("session", "session_state", "market_session"):
        value = snapshot.get(key)
        if isinstance(value, dict):
            value = (
                value.get("label")
                or value.get("name")
                or value.get("session")
                or value.get("state")
            )
        if value is not None and str(value).strip():
            return str(value).strip().upper()[:64]
    return "unknown"


def _benchmark_event_reasons(previous_semantic: dict, current_semantic: dict) -> list[str]:
    labels = {
        "structure": "structure/BOS/CHOCH",
        "vwap_levels": "VWAP/level",
        "thesis_ready_blockers": "thesis/READY/blocker",
        "volatility": "volatility",
        "volume": "volume",
        "session": "session",
        "recovery": "recovery",
    }
    reasons = []
    for family, current in current_semantic.items():
        previous = previous_semantic.get(family)
        if previous != current:
            prefix = "available" if previous is None else "changed"
            reasons.append(f"{labels.get(family, family)} {prefix}")
    return reasons


def compute_benchmark_trigger_policies(
    previous_state: Optional[dict],
    *,
    completed_bar_fingerprint: Optional[str],
    image_fingerprint: Optional[dict],
    deterministic_snapshot: Optional[dict],
    now_epoch: Optional[float] = None,
    max_staleness_seconds: Optional[int] = None,
) -> dict:
    """Pure telemetry calculation for the three proposed call policies.

    The result is intentionally detached from `_vb_tick()`'s decision to call
    the canonical evaluator.  It answers what each policy *would* have done.
    """
    previous = previous_state if isinstance(previous_state, dict) else {}
    image = image_fingerprint if isinstance(image_fingerprint, dict) else {}
    previous_image = previous.get("image_fingerprint") or {}
    previous_bar = previous.get("completed_bar_fingerprint")
    new_bar = bool(completed_bar_fingerprint and completed_bar_fingerprint != previous_bar)
    exact_duplicate = bool(
        image.get("sha256")
        and image.get("sha256") == previous_image.get("sha256")
    )
    distance = _hamming_distance(image.get("ahash"), previous_image.get("ahash"))
    near_duplicate = bool(
        not exact_duplicate
        and distance is not None
        and distance <= VISUAL_BRAIN_BENCHMARK_IMAGE_HAMMING_MAX
    )
    policy_one_reasons = []
    if not completed_bar_fingerprint:
        policy_one_reasons.append("no_new_completed_bar")
    elif not new_bar:
        policy_one_reasons.append("no_new_completed_bar")
    if exact_duplicate:
        policy_one_reasons.append("exact_image_duplicate")
    elif near_duplicate:
        policy_one_reasons.append("near_identical_image")
    policy_one_call = bool(
        completed_bar_fingerprint
        and new_bar
        and not exact_duplicate
        and not near_duplicate
    )
    if not previous and completed_bar_fingerprint and image.get("sha256"):
        policy_one_call = True
        policy_one_reasons = ["initial_observation"]

    current_semantic = _benchmark_semantic_snapshot(deterministic_snapshot)
    previous_semantic = previous.get("semantic_snapshot") or {}
    event_reasons = _benchmark_event_reasons(previous_semantic, current_semantic)
    event_signature = _stable_hash({
        "semantic": current_semantic,
        "reasons": event_reasons,
    }) if event_reasons else None
    coalesced = bool(
        event_signature and event_signature == previous.get("last_event_signature")
    )
    policy_two_call = bool(event_reasons and not coalesced)
    if coalesced:
        event_reasons = [*event_reasons, "coalesced_duplicate_event"]

    now = float(now_epoch if now_epoch is not None else time.time())
    last_baseline_at = previous.get("last_baseline_at")
    try:
        staleness = max(0.0, now - float(last_baseline_at)) if last_baseline_at is not None else None
    except (TypeError, ValueError):
        staleness = None
    threshold = max(
        60,
        int(
            max_staleness_seconds
            if max_staleness_seconds is not None
            else VISUAL_BRAIN_BENCHMARK_MAX_STALENESS_SECONDS
        ),
    )
    policy_three_call = last_baseline_at is None or (
        staleness is not None and staleness >= threshold
    )

    return {
        "no_new_bar_image": {
            "would_call": policy_one_call,
            "would_skip": not policy_one_call,
            "reasons": policy_one_reasons,
            "new_completed_bar": new_bar,
            "exact_image_duplicate": exact_duplicate,
            "near_identical_image": near_duplicate,
            "image_hamming_distance": distance,
        },
        "deterministic_events": {
            "would_call": policy_two_call,
            "would_skip": not policy_two_call,
            "reasons": event_reasons,
            "event_signature": event_signature,
            "coalesced": coalesced,
            "semantic_fingerprint": _stable_hash(current_semantic),
        },
        "max_staleness_heartbeat": {
            "would_call": policy_three_call,
            "would_skip": not policy_three_call,
            "reasons": (
                ["first_cycle"] if last_baseline_at is None
                else ["max_staleness_reached"] if policy_three_call else ["within_staleness_budget"]
            ),
            "staleness_seconds": round(staleness, 3) if staleness is not None else None,
            "max_staleness_seconds": threshold,
            "heartbeat_used": policy_three_call,
        },
    }


def _benchmark_begin_cycle(instrument: str) -> Optional[dict]:
    if not VISUAL_BRAIN_BENCHMARK_ENABLED:
        return None
    with _BENCHMARK_LOCK:
        previous = _safe_copy(_BENCHMARK_LAST_BY_INST.get(instrument) or {})
    return {
        "instrument": instrument,
        "started_at": time.time(),
        "cycle_id": f"{instrument}:{time.time_ns()}",
        "previous": previous,
        "baseline_attempts": [],
        "baseline_failures": [],
        "candidate": None,
        "prepared": False,
        "market_context": {},
        "deterministic_snapshot": {},
        "session": "unknown",
        "recent_history": [],
        "previous_state": None,
        "screenshot_bytes": None,
    }


def _benchmark_prepare_cycle(
    cycle: Optional[dict],
    *,
    bars: list[dict],
    screenshot_bytes: Optional[bytes],
    market_context: dict,
    previous_state: Optional[dict],
    recent_history: list[dict],
) -> None:
    if cycle is None:
        return
    try:
        deterministic_snapshot = {}
        if _benchmark_state_fn is not None:
            deterministic_snapshot = _benchmark_state_fn(cycle["instrument"]) or {}
            if not isinstance(deterministic_snapshot, dict):
                deterministic_snapshot = {}
        image_fp = _image_fingerprint(screenshot_bytes)
        bar_fp = _completed_bar_fingerprint(bars)
        cycle.update({
            "prepared": True,
            "completed_bar_fingerprint": bar_fp,
            "image_fingerprint": image_fp,
            "market_context": _safe_copy(market_context or {}),
            "deterministic_snapshot": _safe_copy(deterministic_snapshot),
            "session": _benchmark_session_label(deterministic_snapshot),
            "previous_state": _safe_copy(previous_state),
            "recent_history": _safe_copy(recent_history),
            "screenshot_bytes": bytes(screenshot_bytes) if screenshot_bytes else None,
        })
        cycle["policies"] = compute_benchmark_trigger_policies(
            cycle["previous"],
            completed_bar_fingerprint=bar_fp,
            image_fingerprint=image_fp,
            deterministic_snapshot=deterministic_snapshot,
            now_epoch=cycle["started_at"],
        )
        cycle["context_fingerprint"] = _stable_hash(cycle["market_context"])
    except Exception as exc:
        cycle["input_error"] = type(exc).__name__
        cycle["policies"] = {}


def _benchmark_current_cycle() -> Optional[dict]:
    if not VISUAL_BRAIN_BENCHMARK_ENABLED:
        return None
    return getattr(_BENCHMARK_LOCAL, "cycle", None)


def _benchmark_record_baseline_attempt(
    attempt: int,
    input_tokens: int,
    output_tokens: int,
) -> Optional[dict]:
    cycle = _benchmark_current_cycle()
    if cycle is None:
        return None
    record = {
        "attempt": attempt,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "estimated_cost_usd": round(
            int(input_tokens or 0) * _COST_PER_INPUT_TOK
            + int(output_tokens or 0) * _COST_PER_OUTPUT_TOK,
            8,
        ),
        "model": _VB_MODEL,
        "ok": False,
        "error_category": None,
    }
    cycle["baseline_attempts"].append(record)
    return record


def _benchmark_record_baseline_failure(exc: Exception) -> None:
    cycle = _benchmark_current_cycle()
    if cycle is None:
        return
    text = str(exc).lower()
    category = "schema" if (
        "schema" in text or "json" in text or isinstance(exc, ValueError)
    ) else "transport" if any(
        token in text for token in ("timeout", "connection", "api")
    ) else "unknown"
    cycle["baseline_failures"].append({
        "error_type": type(exc).__name__,
        "error_category": category,
    })


def _build_analysis_messages(
    screenshot_bytes: bytes,
    previous_state: Optional[dict],
    recent_history: list[dict],
    instrument: str,
    market_context: Optional[dict],
    now_utc: str,
) -> list[dict]:
    """Build the canonical prompt payload shared by baseline and candidates."""
    history_text = _build_history_text(recent_history)
    prev_summary = ""
    if previous_state:
        prev_summary = (
            f"Previous observation: bias={previous_state.get('bias')} "
            f"state={previous_state.get('market_state')} "
            f"event={previous_state.get('last_event')} "
            f"action={previous_state.get('action')} "
            f"conf={previous_state.get('confidence')}%\n"
            f"Previous summary: {previous_state.get('summary', '')}"
        )
    b64_img = base64.b64encode(screenshot_bytes).decode()
    context_text = json.dumps(market_context or {}, separators=(",", ":"), sort_keys=True)
    user_content = [
        {
            "type": "text",
            "text": (
                f"Instrument: {instrument}\n"
                f"Current UTC time: {now_utc}\n\n"
                f"--- PREVIOUS STATE ---\n{prev_summary or 'First observation.'}\n\n"
                f"--- STATE HISTORY (newest last) ---\n{history_text}\n\n"
                f"--- NATIVE MULTI-TIMEFRAME CONTEXT ---\n{context_text}\n\n"
                "Analyze the native context and attached chart image. Respond with ONLY the JSON object."
            ),
        },
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{b64_img}",
                "detail": "auto",
            },
        },
    ]
    return [
        {"role": "system", "content": _get_system_prompt(instrument)},
        {"role": "user", "content": user_content},
    ]


def _run_openai_benchmark_candidate(payload: dict) -> dict:
    """Run an explicitly enabled cheaper multimodal candidate in isolation."""
    api_key = os.getenv("AI_INTEGRATIONS_OPENAI_API_KEY", "")
    base_url = os.getenv("AI_INTEGRATIONS_OPENAI_BASE_URL", "https://api.openai.com/v1")
    if not api_key:
        raise RuntimeError("AI_INTEGRATIONS_OPENAI_API_KEY not set")
    from openai import OpenAI  # noqa: PLC0415
    http_client = httpx.Client(
        trust_env=False,
        timeout=_VB_OPENAI_TIMEOUT_SECONDS,
        verify=True,
    )
    client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
    try:
        messages = _build_analysis_messages(
            payload["screenshot_bytes"],
            payload["previous_state"],
            payload["recent_history"],
            payload["instrument"],
            payload["market_context"],
            payload["prompt_timestamp"],
        )
        response = client.chat.completions.create(
            model=VISUAL_BRAIN_BENCHMARK_CANDIDATE_MODEL,
            max_completion_tokens=_VB_MAX_TOKENS,
            messages=messages,
        )
        usage = response.usage
        content = (response.choices[0].message.content or "").strip()
        parsed = None
        schema_valid = False
        try:
            parsed = json.loads(content)
            schema_valid, _ = _validate_observation(parsed)
        except Exception:
            schema_valid = False
        return {
            "model": VISUAL_BRAIN_BENCHMARK_CANDIDATE_MODEL,
            "input_tokens": int(usage.prompt_tokens if usage else 0),
            "output_tokens": int(usage.completion_tokens if usage else 0),
            "schema_valid": schema_valid,
            "response_received": bool(content),
            "output_type": type(parsed).__name__ if parsed is not None else None,
        }
    finally:
        http_client.close()


def _normalize_candidate_result(candidate_name: str, result: Any) -> dict:
    if not isinstance(result, dict):
        result = {"response_received": True, "output_type": type(result).__name__}
    candidate = {
        "name": candidate_name,
        "model": result.get("model", candidate_name),
        "input_tokens": int(result.get("input_tokens") or 0),
        "output_tokens": int(result.get("output_tokens") or 0),
        "retry_count": max(0, int(result.get("retry_count") or 0)),
        "schema_valid": bool(result.get("schema_valid", False)),
        "response_received": bool(result.get("response_received", True)),
        "error_type": None,
    }
    if not candidate["schema_valid"]:
        candidate["error_type"] = "schema"
    candidate["estimated_cost_usd"] = round(
        (
            candidate["input_tokens"]
            * VISUAL_BRAIN_BENCHMARK_CANDIDATE_INPUT_COST_PER_MILLION
            + candidate["output_tokens"]
            * VISUAL_BRAIN_BENCHMARK_CANDIDATE_OUTPUT_COST_PER_MILLION
        ) / 1_000_000.0,
        8,
    )
    return candidate


def _aggregate_candidate_result_locked(candidate: dict) -> None:
    counters = _BENCHMARK_COUNTERS
    counters["candidate_calls"] += 1
    candidate_ok = not candidate.get("error_type") and candidate.get("response_received")
    counters["candidate_successes"] += int(candidate_ok)
    counters["candidate_failures"] += int(not candidate_ok)
    counters["candidate_schema_failures"] += int(candidate.get("error_type") == "schema")
    counters["candidate_input_tokens"] += int(candidate.get("input_tokens") or 0)
    counters["candidate_output_tokens"] += int(candidate.get("output_tokens") or 0)
    counters["candidate_retries"] += int(candidate.get("retry_count") or 0)
    counters["candidate_cost_usd"] += float(candidate.get("estimated_cost_usd") or 0)
    if candidate.get("error_type"):
        errors = counters["candidate_errors"]
        key = str(candidate["error_type"])
        if key not in errors and len(errors) >= 16:
            key = "other"
        errors[key] = errors.get(key, 0) + 1


def _apply_candidate_result_locked(record: dict, candidate: dict) -> None:
    """Attach one completed candidate result while `_BENCHMARK_LOCK` is held."""
    record["candidate"] = _safe_copy(candidate)
    _aggregate_candidate_result_locked(candidate)


def _record_async_candidate_result(cycle_id: str, candidate: dict) -> None:
    with _BENCHMARK_LOCK:
        for record in reversed(_BENCHMARK_RECENT):
            if record.get("cycle_id") == cycle_id:
                _apply_candidate_result_locked(record, candidate)
                return
        if cycle_id not in _BENCHMARK_OPEN_CYCLES:
            _aggregate_candidate_result_locked(candidate)
            _BENCHMARK_COUNTERS["candidate_late_or_evicted_results"] += 1
            return
        if len(_BENCHMARK_PENDING_CANDIDATES) >= _BENCHMARK_MAX_PENDING_CANDIDATES:
            _BENCHMARK_PENDING_CANDIDATES.pop(
                next(iter(_BENCHMARK_PENDING_CANDIDATES)), None
            )
        _BENCHMARK_PENDING_CANDIDATES[cycle_id] = _safe_copy(candidate)


def _paired_candidate_worker(
    cycle_id: str,
    instrument: str,
    candidate_name: str,
    runner: Optional[Callable],
    payload: dict,
) -> None:
    """Run outside the canonical worker so a hung candidate cannot block cadence."""
    try:
        try:
            raw_result = (
                runner(payload) if runner
                else _run_openai_benchmark_candidate(payload)
            )
            candidate = _normalize_candidate_result(candidate_name, raw_result)
        except Exception as exc:
            candidate = {
                "name": candidate_name,
                "model": candidate_name,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "schema_valid": False,
                "response_received": False,
                "error_type": type(exc).__name__,
            }
        _record_async_candidate_result(cycle_id, candidate)
    finally:
        with _BENCHMARK_LOCK:
            _BENCHMARK_ACTIVE_CANDIDATES.discard(instrument)


def _run_paired_benchmark_candidate(cycle: Optional[dict]) -> None:
    """Schedule best-effort candidate measurement; baseline never waits for it."""
    if cycle is None or not VISUAL_BRAIN_BENCHMARK_CANDIDATE_ENABLED:
        return
    if not cycle.get("screenshot_bytes"):
        return
    candidate_name = os.getenv("VISUAL_BRAIN_BENCHMARK_CANDIDATE", "openai-cheap")
    with _BENCHMARK_LOCK:
        runner = _BENCHMARK_CANDIDATE_RUNNERS.get(candidate_name)
        busy = (
            cycle["instrument"] in _BENCHMARK_ACTIVE_CANDIDATES
            or len(_BENCHMARK_ACTIVE_CANDIDATES) >= _BENCHMARK_MAX_ACTIVE_CANDIDATES
        )
        if not busy:
            _BENCHMARK_ACTIVE_CANDIDATES.add(cycle["instrument"])
            _BENCHMARK_OPEN_CYCLES.add(cycle["cycle_id"])
    if busy:
        cycle["candidate"] = {
            "enabled": True,
            "scheduled": False,
            "skipped": "busy",
            "name": candidate_name,
        }
        return
    payload = {
        "instrument": cycle["instrument"],
        "screenshot_bytes": bytes(cycle["screenshot_bytes"]),
        "market_context": _safe_copy(cycle.get("market_context") or {}),
        "previous_state": _safe_copy(cycle.get("previous_state")),
        "recent_history": _safe_copy(cycle.get("recent_history") or []),
        "prompt_timestamp": cycle.get("prompt_timestamp") or datetime.now(timezone.utc).isoformat(),
        "context_fingerprint": cycle.get("context_fingerprint"),
        "image_fingerprint": _safe_copy(cycle.get("image_fingerprint") or {}),
    }
    cycle["candidate"] = {
        "enabled": True,
        "scheduled": True,
        "name": candidate_name,
        "model": (
            VISUAL_BRAIN_BENCHMARK_CANDIDATE_MODEL
            if runner is None else candidate_name
        ),
    }
    worker = threading.Thread(
        target=_paired_candidate_worker,
        args=(cycle["cycle_id"], cycle["instrument"], candidate_name, runner, payload),
        daemon=True,
    )
    try:
        worker.start()
    except Exception:
        with _BENCHMARK_LOCK:
            _BENCHMARK_ACTIVE_CANDIDATES.discard(cycle["instrument"])
            _BENCHMARK_OPEN_CYCLES.discard(cycle["cycle_id"])
        cycle["candidate"] = {
            "enabled": True,
            "scheduled": False,
            "skipped": "start_failure",
            "name": candidate_name,
        }


def _benchmark_finish_cycle(cycle: Optional[dict]) -> None:
    """Aggregate one cycle into bounded read-only in-memory telemetry."""
    if cycle is None:
        return
    try:
        attempts = cycle.get("baseline_attempts") or []
        baseline_calls = len(attempts)
        baseline_success = any(bool(item.get("ok")) for item in attempts)
        baseline_input = sum(int(item.get("input_tokens") or 0) for item in attempts)
        baseline_output = sum(int(item.get("output_tokens") or 0) for item in attempts)
        baseline_cost = sum(float(item.get("estimated_cost_usd") or 0) for item in attempts)
        baseline_failures = cycle.get("baseline_failures") or []
        schema_failures = sum(
            1 for item in baseline_failures if item.get("error_category") == "schema"
        )
        candidate = cycle.get("candidate") or {}
        policies = cycle.get("policies") or {}
        record = {
            "cycle_id": cycle["cycle_id"],
            "instrument": cycle["instrument"],
            "session": cycle.get("session", "unknown"),
            "timestamp": datetime.fromtimestamp(
                cycle["started_at"], timezone.utc
            ).isoformat(),
            "completed_bar_fingerprint": cycle.get("completed_bar_fingerprint"),
            "image_fingerprint": _safe_copy(cycle.get("image_fingerprint") or {}),
            "context_fingerprint": cycle.get("context_fingerprint"),
            "deterministic_fingerprint": (
                (policies.get("deterministic_events") or {}).get("semantic_fingerprint")
            ),
            "policies": _safe_copy(policies),
            "baseline": {
                "model": _VB_MODEL,
                "api_calls": baseline_calls,
                "success": baseline_success,
                "input_tokens": baseline_input,
                "output_tokens": baseline_output,
                "estimated_cost_usd": round(baseline_cost, 8),
                "retry_count": max(0, baseline_calls - 1),
                "failures": _safe_copy(baseline_failures),
            },
            "candidate": _safe_copy(candidate) if candidate else {
                "enabled": False,
            },
        }
        with _BENCHMARK_LOCK:
            counters = _BENCHMARK_COUNTERS
            counters["cycles"] += 1
            counters["baseline_api_calls"] += baseline_calls
            counters["baseline_successes"] += int(baseline_success)
            counters["baseline_failures"] += len(baseline_failures)
            counters["baseline_retries"] += max(0, baseline_calls - 1)
            counters["baseline_schema_failures"] += schema_failures
            counters["baseline_input_tokens"] += baseline_input
            counters["baseline_output_tokens"] += baseline_output
            counters["baseline_cost_usd"] += baseline_cost
            if candidate.get("scheduled"):
                counters["candidate_scheduled"] += 1
            elif candidate.get("skipped") == "busy":
                counters["candidate_skipped_busy"] += 1
            elif candidate.get("skipped") == "start_failure":
                counters["candidate_start_failures"] += 1
            if cycle.get("input_error"):
                counters["input_errors"] += 1
            for policy_name, policy in policies.items():
                if not isinstance(policy, dict):
                    continue
                called = bool(policy.get("would_call"))
                counters["projected"][policy_name]["would_call"] += int(called)
                counters["projected"][policy_name]["would_skip"] += int(not called)
                counters["projected"][policy_name]["avoided_calls"] += (
                    baseline_calls if not called else 0
                )
                for reason in policy.get("reasons") or []:
                    reasons = counters["trigger_reasons"]
                    reasons[reason] = reasons.get(reason, 0) + 1
            image_policy = policies.get("no_new_bar_image") or {}
            if image_policy.get("exact_image_duplicate"):
                counters["image_suppression"]["exact"] += 1
            if image_policy.get("near_identical_image"):
                counters["image_suppression"]["near_identical"] += 1
            if "no_new_completed_bar" in (image_policy.get("reasons") or []):
                counters["image_suppression"]["no_new_bar"] += 1
            heartbeat = policies.get("max_staleness_heartbeat") or {}
            if heartbeat.get("heartbeat_used"):
                counters["heartbeat_usage"] += 1
                if heartbeat.get("staleness_seconds") is not None:
                    observed = float(heartbeat["staleness_seconds"])
                    prior_max = counters["max_observed_staleness_seconds"]
                    counters["max_observed_staleness_seconds"] = round(
                        max(observed, float(prior_max or 0)), 3
                    )
            counters["last_cycle_at"] = record["timestamp"]
            if baseline_success:
                counters["last_baseline_success_at"] = record["timestamp"]
            _BENCHMARK_RECENT.append(record)
            pending_candidate = _BENCHMARK_PENDING_CANDIDATES.pop(
                cycle["cycle_id"], None
            )
            if pending_candidate is not None:
                _apply_candidate_result_locked(record, pending_candidate)
            if (
                cycle["instrument"] not in _BENCHMARK_LAST_BY_INST
                and len(_BENCHMARK_LAST_BY_INST) >= _BENCHMARK_MAX_INSTRUMENTS
            ):
                _BENCHMARK_LAST_BY_INST.pop(next(iter(_BENCHMARK_LAST_BY_INST)), None)
            _BENCHMARK_LAST_BY_INST[cycle["instrument"]] = {
                "completed_bar_fingerprint": cycle.get("completed_bar_fingerprint"),
                "image_fingerprint": _safe_copy(cycle.get("image_fingerprint") or {}),
                "semantic_snapshot": _benchmark_semantic_snapshot(
                    cycle.get("deterministic_snapshot")
                ),
                "last_event_signature": (
                    (policies.get("deterministic_events") or {}).get("event_signature")
                ),
                "last_baseline_at": cycle["started_at"] if baseline_success else (
                    cycle["previous"].get("last_baseline_at")
                ),
            }
    except Exception as exc:
        logger.debug("[VISUAL_BRAIN] benchmark aggregation failed: %s", exc)
    finally:
        with _BENCHMARK_LOCK:
            _BENCHMARK_OPEN_CYCLES.discard(cycle["cycle_id"])


_BENCHMARK_MIN_REPRESENTATIVE_CYCLES = 30
_BENCHMARK_MIN_REPRESENTATIVE_INSTRUMENTS = 2
_BENCHMARK_MIN_REPRESENTATIVE_SESSIONS = 2
_BENCHMARK_MIN_CANDIDATE_PAIRS = 20


def _benchmark_rate(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(float(numerator) / float(denominator), 4)


def _benchmark_empty_rollup() -> dict:
    return {
        "cycles": 0,
        "instruments": [],
        "sessions": [],
        "baseline": {
            "api_calls": 0,
            "calls": 0,
            "completed_runs": 0,
            "successes": 0,
            "failures": 0,
            "retries": 0,
            "schema_valid_calls": 0,
            "schema_failures": 0,
            "schema_valid_rate": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0.0,
        },
        "candidate": {
            "paired_cycles": 0,
            "calls": 0,
            "instruments": [],
            "sessions": [],
            "successes": 0,
            "failures": 0,
            "retries": 0,
            "schema_valid_calls": 0,
            "schema_failures": 0,
            "schema_valid_rate": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0.0,
        },
        "paired_baseline": {
            "completed_runs": 0,
            "api_calls": 0,
            "successes": 0,
            "failures": 0,
            "retries": 0,
            "schema_valid_rate": None,
            "estimated_cost_usd": 0.0,
        },
        "projected": {},
        "heartbeat_usage": 0,
        "comparisons": {
            "cost_savings_usd": None,
            "cost_reduction_rate": None,
            "schema_validity_delta": None,
            "retry_delta_per_call": None,
        },
    }


def _benchmark_rollup(cycles: list[dict]) -> dict:
    """Summarize bounded records without recomputing or changing any state."""
    result = _benchmark_empty_rollup()
    instruments = set()
    sessions = set()
    candidate_instruments = set()
    candidate_sessions = set()
    policy_names = (
        "no_new_bar_image",
        "deterministic_events",
        "max_staleness_heartbeat",
    )
    result["projected"] = {
        name: {"would_call": 0, "would_skip": 0, "avoided_calls": 0}
        for name in policy_names
    }

    for record in cycles:
        if not isinstance(record, dict):
            continue
        result["cycles"] += 1
        instrument = str(record.get("instrument") or "unknown").upper()
        session = str(record.get("session") or "unknown").upper()
        instruments.add(instrument)
        sessions.add(session)

        baseline = record.get("baseline") or {}
        baseline_rollup = result["baseline"]
        calls = max(0, int(baseline.get("api_calls") or 0))
        successes = int(bool(baseline.get("success")))
        completed_runs = int(calls > 0)
        retries = max(0, int(baseline.get("retry_count") or max(0, calls - 1)))
        schema_failures = sum(
            1
            for failure in (baseline.get("failures") or [])
            if isinstance(failure, dict) and failure.get("error_category") == "schema"
        )
        baseline_rollup["api_calls"] += calls
        baseline_rollup["calls"] += calls
        baseline_rollup["completed_runs"] += completed_runs
        baseline_rollup["successes"] += successes
        baseline_rollup["failures"] += int(not successes and calls > 0)
        baseline_rollup["retries"] += retries
        baseline_rollup["schema_valid_calls"] += successes
        baseline_rollup["schema_failures"] += schema_failures
        baseline_rollup["input_tokens"] += int(baseline.get("input_tokens") or 0)
        baseline_rollup["output_tokens"] += int(baseline.get("output_tokens") or 0)
        baseline_rollup["estimated_cost_usd"] += float(
            baseline.get("estimated_cost_usd") or 0
        )

        candidate = record.get("candidate") or {}
        # A scheduled/skipped marker is not a paired observation.  Only count
        # normalized results, so late candidates do not inflate quality rates.
        candidate_observed = (
            "schema_valid" in candidate and "response_received" in candidate
        )
        if candidate_observed:
            candidate_rollup = result["candidate"]
            paired_baseline = result["paired_baseline"]
            candidate_instruments.add(instrument)
            candidate_sessions.add(session)
            candidate_rollup["paired_cycles"] += 1
            candidate_rollup["calls"] += 1
            candidate_ok = bool(
                candidate.get("schema_valid")
                and candidate.get("response_received")
                and not candidate.get("error_type")
            )
            candidate_rollup["successes"] += int(candidate_ok)
            candidate_rollup["failures"] += int(not candidate_ok)
            candidate_rollup["retries"] += max(
                0, int(candidate.get("retry_count") or 0)
            )
            candidate_rollup["schema_valid_calls"] += int(
                bool(candidate.get("schema_valid"))
            )
            candidate_rollup["schema_failures"] += int(
                not bool(candidate.get("schema_valid"))
            )
            candidate_rollup["input_tokens"] += int(
                candidate.get("input_tokens") or 0
            )
            candidate_rollup["output_tokens"] += int(
                candidate.get("output_tokens") or 0
            )
            candidate_rollup["estimated_cost_usd"] += float(
                candidate.get("estimated_cost_usd") or 0
            )
            paired_baseline["completed_runs"] += completed_runs
            paired_baseline["api_calls"] += calls
            paired_baseline["successes"] += successes
            paired_baseline["failures"] += int(not successes and calls > 0)
            paired_baseline["retries"] += retries
            paired_baseline["estimated_cost_usd"] += float(
                baseline.get("estimated_cost_usd") or 0
            )

        policies = record.get("policies") or {}
        for policy_name in policy_names:
            policy = policies.get(policy_name) or {}
            if not isinstance(policy, dict):
                continue
            would_call = bool(policy.get("would_call"))
            projected = result["projected"][policy_name]
            projected["would_call"] += int(would_call)
            projected["would_skip"] += int(not would_call)
            projected["avoided_calls"] += calls if not would_call else 0
            if (
                policy_name == "max_staleness_heartbeat"
                and policy.get("heartbeat_used")
            ):
                result["heartbeat_usage"] += 1

    result["instruments"] = sorted(instruments)
    result["sessions"] = sorted(sessions)
    result["candidate"]["instruments"] = sorted(candidate_instruments)
    result["candidate"]["sessions"] = sorted(candidate_sessions)
    result["baseline"]["schema_valid_rate"] = _benchmark_rate(
        result["baseline"]["successes"],
        result["baseline"]["completed_runs"],
    )
    result["candidate"]["schema_valid_rate"] = _benchmark_rate(
        result["candidate"]["schema_valid_calls"],
        result["candidate"]["paired_cycles"],
    )
    result["paired_baseline"]["schema_valid_rate"] = _benchmark_rate(
        result["paired_baseline"]["successes"],
        result["paired_baseline"]["completed_runs"],
    )
    for side in ("baseline", "candidate", "paired_baseline"):
        result[side]["estimated_cost_usd"] = round(
            float(result[side]["estimated_cost_usd"]), 8
        )

    baseline_cost = result["paired_baseline"]["estimated_cost_usd"]
    candidate_cost = result["candidate"]["estimated_cost_usd"]
    paired = result["candidate"]["paired_cycles"]
    if paired:
        result["comparisons"] = {
            "cost_savings_usd": round(baseline_cost - candidate_cost, 8),
            "cost_reduction_rate": (
                None
                if baseline_cost <= 0
                else round((baseline_cost - candidate_cost) / baseline_cost, 4)
            ),
            "schema_validity_delta": (
                None
                if result["paired_baseline"]["schema_valid_rate"] is None
                or result["candidate"]["schema_valid_rate"] is None
                else round(
                    result["candidate"]["schema_valid_rate"]
                    - result["paired_baseline"]["schema_valid_rate"],
                    4,
                )
            ),
            "retry_delta_per_call": round(
                (
                    result["candidate"]["retries"] / max(1, paired)
                    - result["paired_baseline"]["retries"] / max(1, paired)
                ),
                4,
            ),
        }
    return result


def _benchmark_advisory(
    cycles: list[dict],
    overall: dict,
    *,
    candidate_enabled: bool,
) -> dict:
    """Create a conservative report-only recommendation."""
    instruments = set(overall.get("instruments") or [])
    sessions = {
        session for session in (overall.get("sessions") or []) if session != "UNKNOWN"
    }
    evidence_gaps = []
    if len(cycles) < _BENCHMARK_MIN_REPRESENTATIVE_CYCLES:
        evidence_gaps.append(
            f"need at least {_BENCHMARK_MIN_REPRESENTATIVE_CYCLES} cycles"
        )
    if len(instruments) < _BENCHMARK_MIN_REPRESENTATIVE_INSTRUMENTS:
        evidence_gaps.append(
            f"need at least {_BENCHMARK_MIN_REPRESENTATIVE_INSTRUMENTS} instruments"
        )
    if len(sessions) < _BENCHMARK_MIN_REPRESENTATIVE_SESSIONS:
        evidence_gaps.append(
            f"need at least {_BENCHMARK_MIN_REPRESENTATIVE_SESSIONS} labeled sessions"
        )

    if not VISUAL_BRAIN_BENCHMARK_ENABLED:
        return {
            "decision": "NO_AUTOMATIC_ROLLOUT",
            "recommendation": "keep_current_cadence",
            "confidence": "none",
            "confidence_score": 0.0,
            "representative_sample": False,
            "evidence_gaps": ["benchmark is disabled"],
            "reason": "Read-only evidence collection is disabled.",
            "required_guard": "retain max_staleness_heartbeat for any future policy",
        }

    if candidate_enabled:
        candidate = overall["candidate"]
        candidate_instruments = set(candidate.get("instruments") or [])
        candidate_sessions = {
            item
            for item in (candidate.get("sessions") or [])
            if item != "UNKNOWN"
        }
        if candidate["paired_cycles"] < _BENCHMARK_MIN_CANDIDATE_PAIRS:
            evidence_gaps.append(
                f"need at least {_BENCHMARK_MIN_CANDIDATE_PAIRS} paired candidate results"
            )
        if len(candidate_instruments) < _BENCHMARK_MIN_REPRESENTATIVE_INSTRUMENTS:
            evidence_gaps.append(
                "paired candidate results must cover at least "
                f"{_BENCHMARK_MIN_REPRESENTATIVE_INSTRUMENTS} instruments"
            )
        if len(candidate_sessions) < _BENCHMARK_MIN_REPRESENTATIVE_SESSIONS:
            evidence_gaps.append(
                "paired candidate results must cover at least "
                f"{_BENCHMARK_MIN_REPRESENTATIVE_SESSIONS} labeled sessions"
            )

    representative = not evidence_gaps
    projected = overall.get("projected") or {}
    best_policy = None
    if projected:
        best_policy = max(
            projected,
            key=lambda name: projected[name].get("avoided_calls", 0),
        )

    if not representative:
        return {
            "decision": "NO_AUTOMATIC_ROLLOUT",
            "recommendation": "continue_read_only_collection",
            "confidence": "low",
            "confidence_score": 0.25,
            "representative_sample": False,
            "evidence_gaps": evidence_gaps,
            "reason": "The sample is not yet representative enough to change paid-call cadence.",
            "recommended_policy": best_policy,
            "required_guard": "retain max_staleness_heartbeat for any future policy",
        }

    candidate = overall["candidate"]
    candidate_rate = candidate.get("schema_valid_rate")
    cost_reduction = (overall.get("comparisons") or {}).get("cost_reduction_rate")
    if candidate_enabled and (
        candidate_rate is None
        or candidate_rate < 0.99
        or cost_reduction is None
        or cost_reduction <= 0
    ):
        return {
            "decision": "NO_AUTOMATIC_ROLLOUT",
            "recommendation": "keep_gpt_5_4_and_continue_shadow_comparison",
            "confidence": "medium",
            "confidence_score": 0.65,
            "representative_sample": True,
            "evidence_gaps": [
                "candidate must sustain >=99% schema validity and lower cost"
            ],
            "reason": "A cheaper candidate has not met the minimum quality and cost bar.",
            "recommended_policy": best_policy,
            "required_guard": "retain max_staleness_heartbeat for any future policy",
        }

    return {
        "decision": "NO_AUTOMATIC_ROLLOUT",
        "recommendation": "review_shadow_policy_before_any_staged_change",
        "confidence": "medium",
        "confidence_score": 0.75,
        "representative_sample": True,
        "evidence_gaps": [
            "semantic equivalence and downstream usefulness still require human review"
        ],
        "reason": (
            "The sample supports a human review of the highest-avoidance policy, "
            "but does not authorize changing production cadence."
        ),
        "recommended_policy": best_policy,
        "required_guard": "retain max_staleness_heartbeat for any future policy",
    }


def get_benchmark_report(
    limit: int = 50,
    instrument: Optional[str] = None,
    session: Optional[str] = None,
) -> dict:
    """Return bounded shadow benchmark telemetry; never recomputes market state."""
    limit = max(1, min(int(limit), _BENCHMARK_HISTORY_LIMIT))
    with _BENCHMARK_LOCK:
        recent = list(_BENCHMARK_RECENT)
        if instrument:
            recent = [
                item for item in recent
                if item.get("instrument") == instrument.upper()
            ]
        if session:
            recent = [
                item for item in recent
                if item.get("session") == session.upper()
            ]
        recent = recent[-limit:]
        counters = _safe_copy(_BENCHMARK_COUNTERS)
    counters["baseline_cost_usd"] = round(float(counters.get("baseline_cost_usd") or 0), 8)
    counters["candidate_cost_usd"] = round(float(counters.get("candidate_cost_usd") or 0), 8)
    overall = _benchmark_rollup(recent)
    by_instrument = {
        instrument_name: _benchmark_rollup(
            [item for item in recent if item.get("instrument") == instrument_name]
        )
        for instrument_name in overall["instruments"]
    }
    by_session = {
        session_name: _benchmark_rollup(
            [item for item in recent if item.get("session") == session_name]
        )
        for session_name in overall["sessions"]
    }
    candidate_enabled = (
        VISUAL_BRAIN_BENCHMARK_ENABLED
        and VISUAL_BRAIN_BENCHMARK_CANDIDATE_ENABLED
    )
    return {
        "ok": True,
        "enabled": VISUAL_BRAIN_BENCHMARK_ENABLED,
        "candidate_enabled": candidate_enabled,
        "candidate_model": VISUAL_BRAIN_BENCHMARK_CANDIDATE_MODEL,
        "candidate_pricing_per_million": {
            "input_usd": VISUAL_BRAIN_BENCHMARK_CANDIDATE_INPUT_COST_PER_MILLION,
            "output_usd": VISUAL_BRAIN_BENCHMARK_CANDIDATE_OUTPUT_COST_PER_MILLION,
        },
        "max_staleness_seconds": VISUAL_BRAIN_BENCHMARK_MAX_STALENESS_SECONDS,
        "image_hamming_max": VISUAL_BRAIN_BENCHMARK_IMAGE_HAMMING_MAX,
        "counters": counters,
        "sample": {
            "scope": "bounded_recent_cycles",
            "cycle_limit": limit,
            "cycles": overall["cycles"],
            "instruments": overall["instruments"],
            "sessions": overall["sessions"],
            "representative": (
                overall["cycles"] >= _BENCHMARK_MIN_REPRESENTATIVE_CYCLES
                and len(overall["instruments"])
                >= _BENCHMARK_MIN_REPRESENTATIVE_INSTRUMENTS
                and len(
                    [
                        item for item in overall["sessions"]
                        if item != "UNKNOWN"
                    ]
                ) >= _BENCHMARK_MIN_REPRESENTATIVE_SESSIONS
            ),
        },
        "rollup": overall,
        "by_instrument": by_instrument,
        "by_session": by_session,
        "advisory": _benchmark_advisory(
            recent, overall, candidate_enabled=candidate_enabled
        ),
        "recent_cycles": recent,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_conn():
    """Return a Postgres connection or None (fail-open).

    Uses _db_conn_fn injected at start()/check_vb_db_ready() time.
    Never imports app — see module docstring for why.
    """
    if _db_conn_fn is None:
        return None
    try:
        return _db_conn_fn()
    except Exception as exc:
        logger.debug("visual_brain _get_conn: %s", exc)
        return None


def check_vb_db_ready(db_conn_fn: Optional[Callable] = None) -> None:
    """Probe visual_brain_observations; set VB_DB_READY. FAIL-OPEN.

    Args:
        db_conn_fn: callable returning a psycopg2 connection (injected from
                    app.py's _learning_conn).  If provided, stores it as the
                    module-level _db_conn_fn for all subsequent DB calls.
    """
    global VB_DB_READY, _db_conn_fn
    if db_conn_fn is not None:
        _db_conn_fn = db_conn_fn
    conn = _get_conn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM visual_brain_observations LIMIT 1")
            cur.fetchone()
        VB_DB_READY = True
        logger.info("[VISUAL_BRAIN] visual_brain_observations table ready")
    except Exception as exc:
        logger.warning("[VISUAL_BRAIN] DB probe failed — table missing?: %s", exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Cost tracking
# ─────────────────────────────────────────────────────────────────────────────

def _et_date_str() -> str:
    """Today's date string in ET (UTC-4 approximate)."""
    from datetime import timezone, timedelta  # noqa: PLC0415
    et = datetime.now(timezone(timedelta(hours=-4)))
    return et.strftime("%Y-%m-%d")


def _record_cost(input_tokens: int, output_tokens: int) -> None:
    global _vb_calls_today, _vb_cost_today, _vb_cost_reset_day
    today = _et_date_str()
    with _COST_LOCK:
        if _vb_cost_reset_day != today:
            _vb_calls_today = 0
            _vb_cost_today  = 0.0
            _vb_cost_reset_day = today
        _vb_calls_today += 1
        _vb_cost_today  += (input_tokens * _COST_PER_INPUT_TOK
                            + output_tokens * _COST_PER_OUTPUT_TOK)


def get_cost_summary() -> dict:
    """Return today's cost counters (for /visual-brain/cost route)."""
    today = _et_date_str()
    with _COST_LOCK:
        if _vb_cost_reset_day != today:
            return {"calls_today": 0, "cost_today_usd": 0.0, "date": today}
        return {
            "calls_today":    _vb_calls_today,
            "cost_today_usd": round(_vb_cost_today, 6),
            "date":           today,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Screenshot capture
# ─────────────────────────────────────────────────────────────────────────────

# TradingView public embed URLs per instrument — 1-minute chart, no login required.
# Playwright fallback only; primary path uses matplotlib from Databento bars.
_TV_EMBED_URLS: dict = {
    "MNQ": (
        "https://www.tradingview.com/chart/?symbol=CME_MINI%3ANQ1%21&interval=1"
        "&theme=dark&hide_side_toolbar=1&hide_top_toolbar=1&allow_symbol_change=0"
    ),
    "MGC": (
        "https://www.tradingview.com/chart/?symbol=COMEX%3AMG1%21&interval=1"
        "&theme=dark&hide_side_toolbar=1&hide_top_toolbar=1&allow_symbol_change=0"
    ),
    "MES": (
        "https://www.tradingview.com/chart/?symbol=CME_MINI%3AES1%21&interval=1"
        "&theme=dark&hide_side_toolbar=1&hide_top_toolbar=1&allow_symbol_change=0"
    ),
    "MYM": (
        "https://www.tradingview.com/chart/?symbol=CBOT_MINI%3AYM1%21&interval=1"
        "&theme=dark&hide_side_toolbar=1&hide_top_toolbar=1&allow_symbol_change=0"
    ),
}


def _bar_epoch(bar: dict) -> Optional[float]:
    """Return a bar timestamp as Unix seconds, tolerating native bar shapes."""
    raw = bar.get("ts_event", bar.get("ts"))
    if raw is None:
        return None
    try:
        if isinstance(raw, datetime):
            dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        if isinstance(raw, str):
            text = raw.strip().replace("Z", "+00:00")
            return datetime.fromisoformat(text).timestamp()
        value = float(raw)
        # Databento can expose nanoseconds; other sources commonly use seconds.
        if value > 1e14:
            return value / 1e9
        if value > 1e11:
            return value / 1e3
        return value
    except (TypeError, ValueError, OverflowError):
        return None


def _resample_bars(bars: list[dict], bucket_seconds: int) -> list[dict]:
    """Resample native OHLCV bars into deterministic UTC buckets."""
    buckets: dict[int, dict] = {}
    for bar in bars:
        ts = _bar_epoch(bar)
        if ts is None:
            continue
        try:
            o = float(bar.get("open"))
            h = float(bar.get("high"))
            lo = float(bar.get("low"))
            c = float(bar.get("close"))
            v = float(bar.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        if not all(map(lambda n: n == n and abs(n) != float("inf"), (o, h, lo, c))):
            continue
        bucket = int(ts // bucket_seconds) * bucket_seconds
        current = buckets.get(bucket)
        if current is None:
            buckets[bucket] = {
                "ts": float(bucket), "open": o, "high": h, "low": lo,
                "close": c, "volume": v,
            }
        else:
            current["high"] = max(current["high"], h)
            current["low"] = min(current["low"], lo)
            current["close"] = c
            current["volume"] += v
    return [buckets[key] for key in sorted(buckets)]


def _timeframe_bias(tf_bars: list[dict]) -> tuple[str, float]:
    """Classify a resampled timeframe without inventing a bias from one bar."""
    closes = [float(b["close"]) for b in tf_bars if b.get("close") is not None]
    if len(closes) < 3:
        return "UNKNOWN", 0.0
    recent = closes[-min(8, len(closes)):]
    first, last = recent[0], recent[-1]
    avg_range = sum(
        max(float(b.get("high", 0)) - float(b.get("low", 0)), 0.0)
        for b in tf_bars[-len(recent):]
    ) / max(len(recent), 1)
    threshold = max(avg_range * 0.18, abs(last) * 0.00008)
    delta = last - first
    if abs(delta) <= threshold:
        return "NEUTRAL", min(abs(delta) / max(threshold, 1e-9), 1.0)
    return ("BULLISH" if delta > 0 else "BEARISH"), min(abs(delta) / max(threshold * 3, 1e-9), 1.0)


def _build_market_context(bars: list[dict], instrument: str) -> dict:
    """Build the native context supplied beside the screenshot to the model.

    The screenshot remains a 1-minute visual input, but the model also receives
    deterministic 5m/15m/1h/4h/1D summaries made from the same native bars.
    This is display-only context: it never feeds the trading engine.
    """
    context: dict = {
        "source": "Databento native bars; deterministic UTC resampling",
        "instrument": instrument,
        "bias": "UNKNOWN",
        "alignment": "UNKNOWN",
        "price": None,
        "vwap": None,
        "session_high": None,
        "session_low": None,
        "timeframes": {},
    }
    if not bars:
        return context

    valid = [b for b in bars if _bar_epoch(b) is not None]
    if not valid:
        return context
    valid.sort(key=lambda b: _bar_epoch(b) or 0)

    try:
        last = valid[-1]
        context["price"] = round(float(last.get("close")), 4)
    except (TypeError, ValueError):
        pass

    # Session levels use the current UTC trading date. They are descriptive
    # levels, not an execution signal.
    try:
        now = datetime.now(timezone.utc)
        session_start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        session_bars = [b for b in valid if (_bar_epoch(b) or 0) >= session_start]
        if session_bars:
            context["session_high"] = round(max(float(b["high"]) for b in session_bars), 4)
            context["session_low"] = round(min(float(b["low"]) for b in session_bars), 4)
    except (TypeError, ValueError):
        pass

    for label, seconds in (
        ("1m", 60), ("5m", 5 * 60), ("15m", 15 * 60),
        ("1h", 60 * 60), ("4h", 4 * 60 * 60), ("1D", 24 * 60 * 60),
    ):
        tf_bars = valid if label == "1m" else _resample_bars(valid, seconds)
        bias, confidence = _timeframe_bias(tf_bars)
        last_bar = tf_bars[-1] if tf_bars else {}
        tf = {
            "bars": len(tf_bars),
            "bias": bias,
            "confidence": round(confidence, 3),
            "last_close": round(float(last_bar["close"]), 4) if last_bar.get("close") is not None else None,
            "last_bar_utc": (
                datetime.fromtimestamp(float(last_bar["ts"]), timezone.utc).isoformat()
                if last_bar.get("ts") is not None else None
            ),
        }
        context["timeframes"][label] = tf

    decided = [
        context["timeframes"][label]["bias"]
        for label in ("15m", "1h", "4h", "1D")
        if context["timeframes"].get(label, {}).get("bias") in {"BULLISH", "BEARISH", "NEUTRAL"}
    ]
    if decided:
        bulls = decided.count("BULLISH")
        bears = decided.count("BEARISH")
        context["bias"] = "BULLISH" if bulls > bears else "BEARISH" if bears > bulls else "NEUTRAL"
        if bulls == len(decided) or bears == len(decided):
            context["alignment"] = "ALIGNED"
        elif bulls == 0 or bears == 0:
            context["alignment"] = "NEUTRAL"
        else:
            context["alignment"] = "MIXED"

    if _vwap_store is not None:
        try:
            live_vwap = (_vwap_store.get(instrument) or {}).get("value")
            if live_vwap is not None:
                context["vwap"] = round(float(live_vwap), 4)
        except (TypeError, ValueError):
            pass
    return context


def _compress_image(raw_png: bytes, max_px: int = _VB_IMAGE_MAX_PX) -> bytes:
    """Resize + JPEG-compress screenshot bytes; returns JPEG bytes."""
    from PIL import Image  # noqa: PLC0415
    img = Image.open(io.BytesIO(raw_png)).convert("RGB")
    w, h = img.size
    if w > max_px:
        ratio = max_px / w
        img = img.resize((max_px, int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


def _generate_chart_from_bars(bars: list, instrument: str) -> bytes:
    """Render a matplotlib OHLCV candlestick chart from Databento bars.
    Returns JPEG bytes.

    Memory safety: fig is ALWAYS closed in finally, even if an exception fires
    mid-render.  Without try/finally a failed render leaks the figure object,
    causing slow OOM over hours (4 instruments × every 60s = ~240 figs/hour).
    plt.close("all") at the top also reclaims any figures that leaked before
    this fix was applied.
    """
    import gc  # noqa: PLC0415
    import matplotlib  # noqa: PLC0415
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415
    import matplotlib.patches as mpatches  # noqa: PLC0415

    if not bars:
        raise ValueError("no bars for chart generation")

    # Safety: purge any orphaned figures from previous failed renders.
    plt.close("all")

    recent = bars[-_CHART_BARS_LOOKBACK:]
    times  = list(range(len(recent)))

    # Reject flat/dead charts before touching the model — overnight sessions for
    # MGC/MES/MYM produce bars with identical OHLC values; sending those wastes
    # an API call and always returns UNCLEAR/0%.
    closes = [b.get("close", 0) for b in recent if b.get("close")]
    if closes and max(closes) - min(closes) < 0.01:
        raise ValueError(f"chart is flat (range {max(closes)-min(closes):.4f}) — skipping dead-market bars")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), gridspec_kw={"height_ratios": [3, 1]})
    try:
        fig.patch.set_facecolor("#050c1a")
        for ax in (ax1, ax2):
            ax.set_facecolor("#0b1628")
            ax.tick_params(colors="white")
            ax.spines[:].set_color("#1e3a5f")

        # ── Candlesticks ──
        for i, b in enumerate(recent):
            o, h, lo, c = b.get("open", 0), b.get("high", 0), b.get("low", 0), b.get("close", 0)
            col = "#22c55e" if c >= o else "#ef4444"
            ax1.plot([i, i], [lo, h], color=col, linewidth=0.8, zorder=1)
            ax1.add_patch(mpatches.FancyBboxPatch(
                (i - 0.3, min(o, c)), 0.6, abs(c - o) or 0.5,
                boxstyle="square,pad=0", linewidth=0, facecolor=col, zorder=2
            ))

        # ── VWAP line — anchored to the current CME session open, not bar[0] ──
        # The hist-seed loads 3+ days of bars for MTF Trend.  Computing VWAP from
        # bar[0] produces a multi-day average that sits far from today's price;
        # the model sees the VWAP floating 100-200 pts from the candles and calls
        # the chart UNCLEAR on every cycle.
        #
        # Fix: filter to bars on or after the current session open before
        # accumulating.  CME equity micros (MNQ/MES/MYM) open at 23:00 UTC
        # (18:00 ET); COMEX gold (MGC) opens at 22:00 UTC (17:00 ET).
        session_open_hour_utc = 22 if instrument == "MGC" else 23
        _now_utc = datetime.now(timezone.utc)
        _candidate = _now_utc.replace(
            hour=session_open_hour_utc, minute=0, second=0, microsecond=0
        )
        if _candidate > _now_utc:          # open hasn't happened yet today → use yesterday's
            _candidate -= timedelta(days=1)
        _session_open_unix = _candidate.timestamp()
        _session_bars = [b for b in bars if b.get("ts", 0) >= _session_open_unix]
        # Fall back to all bars only if session has just opened and few bars exist
        _vwap_source = _session_bars if len(_session_bars) >= 10 else bars

        cum_pv = 0.0; cum_v = 0.0; all_vwap = []
        for b in _vwap_source:
            tp = (b.get("high", 0) + b.get("low", 0) + b.get("close", 0)) / 3.0
            v  = b.get("volume", 1) or 1
            cum_pv += tp * v; cum_v += v
            all_vwap.append(cum_pv / cum_v if cum_v else tp)
        vwap_vals = all_vwap[-len(recent):]      # trim to visible bars
        # A session can begin inside the visible lookback. In that case the VWAP
        # series is intentionally shorter than the candles, so align it to the
        # right instead of passing mismatched x/y arrays to matplotlib.
        vwap_times = times[-len(vwap_vals):] if vwap_vals else []
        ax1.plot(vwap_times, vwap_vals, color="#38bdf8", linewidth=1.2, linestyle="--",
                 label="VWAP", zorder=3)

        # ── Live VWAP overlay — authoritative value from VWAP_BY_TICKER ──────────
        # Shown as a solid horizontal annotation so the model can read the exact
        # level and cross-check it against the cumulative line above.
        if _vwap_store is not None:
            live_vwap_rec = _vwap_store.get(instrument) or {}
            live_vwap_val = live_vwap_rec.get("value")
            if live_vwap_val is not None:
                try:
                    lv = float(live_vwap_val)
                    ax1.axhline(lv, color="#7dd3fc", linewidth=0.9, linestyle="-",
                                alpha=0.55, zorder=2)
                    ax1.text(len(recent) - 1, lv,
                             f" Live VWAP {lv:,.1f}",
                             color="#7dd3fc", fontsize=6.5, va="bottom", zorder=4)
                except (TypeError, ValueError):
                    pass

        # ── Current price label ──
        last_close = recent[-1].get("close", 0) if recent else 0
        ax1.axhline(last_close, color="#f59e0b", linewidth=0.8, linestyle=":")
        ax1.text(len(recent) - 1, last_close, f" {last_close:,.1f}",
                 color="#f59e0b", fontsize=7, va="center")

        ax1.set_title(f"{instrument} — 1m  ({len(recent)} bars)", color="white", fontsize=10)
        ax1.legend(fontsize=7, facecolor="#0b1628", labelcolor="white")

        # ── Volume bars ──
        for i, b in enumerate(recent):
            o, c = b.get("open", 0), b.get("close", 0)
            ax2.bar(i, b.get("volume", 0), color="#22c55e" if c >= o else "#ef4444",
                    width=0.8, alpha=0.7)
        ax2.set_ylabel("Vol", color="#94a3b8", fontsize=7)

        plt.tight_layout(pad=0.4)
        buf = io.BytesIO()
        plt.savefig(buf, format="jpeg", dpi=90, bbox_inches="tight",
                    facecolor="#050c1a")
        result = buf.getvalue()
    finally:
        # Always close — prevents figure leak regardless of how we exit.
        plt.close(fig)
        gc.collect()

    return result


def capture_chart_screenshot(symbol: str = "MNQ", timeout_s: int = _VB_SCREENSHOT_TIMEOUT) -> bytes:
    """Capture MNQ chart.  Returns JPEG bytes.

    Strategy (in priority order):
    1. Matplotlib OHLCV chart from injected Databento bars — always available when
       live market data is flowing; no browser binary required.  This is the
       reliable primary path for server deployments.
    2. Playwright headless Chromium → TradingView embed — optional enhancement
       that adds the full exchange chart.  Requires `playwright install chromium`
       to be run after package install.  Skipped gracefully when the binary is
       absent; Strategy 1 is used instead.

    Raises RuntimeError when both strategies fail so the worker can catch and log.
    """
    errors = []

    # ── Strategy 1: Matplotlib from injected Databento bars (reliable primary) ─
    try:
        if _bars_fn is None:
            raise ValueError("bars_fn not injected — call start() before use")
        bars = _bars_fn(symbol)
        if not bars:
            raise ValueError("no live bars available yet")
        if len(bars) < 5:
            raise ValueError(f"only {len(bars)} bar(s) available — need ≥5 for meaningful analysis")
        return _generate_chart_from_bars(bars, symbol)
    except Exception as exc:
        errors.append(f"matplotlib: {exc}")
        logger.debug("[VISUAL_BRAIN] Matplotlib chart generation failed: %s", exc)

    # ── Strategy 2: Playwright headless Chromium → TradingView embed ──────────
    # Optional: only attempted when the Playwright binary is installed.
    # Install with: playwright install chromium-headless-shell
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-gpu", "--disable-setuid-sandbox"],
            )
            ctx  = browser.new_context(
                viewport={"width": 1024, "height": 600},
                java_script_enabled=True,
            )
            page = ctx.new_page()
            tv_url = _TV_EMBED_URLS.get(symbol, _TV_EMBED_URLS["MNQ"])
            if symbol not in _TV_EMBED_URLS:
                logger.debug("[VISUAL_BRAIN] %s has no TradingView URL; using MNQ fallback", symbol)
            page.goto(tv_url, timeout=timeout_s * 1000, wait_until="networkidle")
            try:
                page.wait_for_selector("canvas", timeout=15000)
                page.wait_for_timeout(3000)   # let chart render
            except Exception:
                page.wait_for_timeout(5000)   # fallback wait
            raw = page.screenshot(full_page=False, type="png")
            browser.close()
        return _compress_image(raw)
    except Exception as exc:
        errors.append(f"playwright: {exc}")
        logger.debug("[VISUAL_BRAIN] Playwright screenshot failed: %s", exc)

    raise RuntimeError(f"All screenshot strategies failed: {'; '.join(errors)}")


# ─────────────────────────────────────────────────────────────────────────────
# Visual analysis via GPT vision
# ─────────────────────────────────────────────────────────────────────────────

# Human-readable labels for each supported instrument — used in the system prompt
# so the model knows what asset it is looking at.
_INSTRUMENT_LABELS: dict = {
    "MNQ": "MNQ (Micro E-mini Nasdaq)",
    "MGC": "MGC (Micro Gold)",
    "MES": "MES (Micro E-mini S&P 500)",
    "MYM": "MYM (Micro E-mini Dow Jones)",
}


def _get_system_prompt(instrument: str) -> str:
    """Return the analysis system prompt with the correct instrument label."""
    label = _INSTRUMENT_LABELS.get(instrument, instrument)
    return f"""You are a disciplined institutional futures trader analyzing a native multi-timeframe market context and a 1-minute {label} chart image.

Your job: describe ONLY what you can directly observe in the chart or the supplied native context. Never guess or predict specific future prices.

Analyze:
- Market structure (HH/HL = bullish; LH/LL = bearish; range)
- Higher highs, higher lows, lower highs, lower lows
- Consolidation, range, breakout, breakdown, failed breakout/breakdown
- Support and resistance interactions
- Liquidity sweeps, reclaims, rejections, retests
- Momentum expansion vs exhaustion
- Continuation vs reversal patterns
- Price location relative to visible VWAP and key levels
- Chop / indecision

Respond with ONLY valid JSON — no markdown fences, no commentary outside the JSON.

Required schema (all fields mandatory):
{{
  "instrument": "{instrument}",
  "timestamp": "<current ISO UTC>",
  "bias": "BULLISH|BEARISH|NEUTRAL",
  "market_state": "TRENDING_UP|TRENDING_DOWN|RANGE|REVERSAL|BREAKOUT|BREAKDOWN|RETEST|CHOP|UNCLEAR",
  "structure": {{
    "short_term": "HH_HL|LH_LL|RANGE|TRANSITION|UNCLEAR",
    "higher_low_intact": true,
    "lower_high_intact": false
  }},
  "last_event": "LIQUIDITY_SWEEP|RECLAIM|REJECTION|BREAKOUT|BREAKDOWN|RETEST|FAILED_BREAKOUT|FAILED_BREAKDOWN|STRUCTURE_SHIFT|NONE",
  "support": {{
    "visible": true,
    "description": "brief description or empty string",
    "approx_price": null
  }},
  "resistance": {{
    "visible": true,
    "description": "brief description or empty string",
    "approx_price": null
  }},
  "long_condition": "what would validate a long entry",
  "short_condition": "what would validate a short entry",
  "action": "LONG_WATCH|SHORT_WATCH|WAIT|NO_TRADE",
  "confidence": 72,
  "state_changed": true,
  "state_change_reason": "one sentence or empty string",
  "summary": "Maximum 2 concise sentences describing what you see.",
  "mode_assessments": {{
    "scalp": {{
      "posture": "LONG_BIAS|SHORT_BIAS|NEUTRAL",
      "setup_status": "TRIGGER_READY|FORMING|WAIT|NO_TRADE",
      "confidence": 65,
      "validation": "Immediate trigger, VWAP, or structure confirmation required",
      "invalidation": "The fast condition that would invalidate this read",
      "reason": "One concise chart-based reason"
    }},
    "intraday_trend": {{
      "posture": "LONG_BIAS|SHORT_BIAS|NEUTRAL",
      "setup_status": "TRIGGER_READY|FORMING|WAIT|NO_TRADE",
      "confidence": 60,
      "timeframe_alignment": "ALIGNED|MIXED|OPPOSED|UNKNOWN",
      "market_phase": "CONTINUATION|PULLBACK|EXHAUSTION|RANGE|UNKNOWN",
      "session_level": "Relevant visible session level or UNKNOWN",
      "validation": "What would validate the intraday continuation or reversal",
      "invalidation": "What would invalidate the intraday read",
      "reason": "One concise chart-based reason"
    }},
    "swing": {{
      "posture": "LONG_BIAS|SHORT_BIAS|NEUTRAL",
      "setup_status": "TRIGGER_READY|FORMING|WAIT|NO_TRADE",
      "confidence": 50,
      "timeframe_alignment": "ALIGNED|MIXED|OPPOSED|UNKNOWN",
      "thesis_quality": "HIGH|MEDIUM|LOW|UNKNOWN",
      "structural_stop": "Visible structural invalidation level or UNKNOWN",
      "target_context": "Larger visible target context or UNKNOWN",
      "validation": "What would validate the higher-timeframe thesis",
      "invalidation": "What would invalidate the swing thesis",
      "reason": "One concise chart-based reason"
    }}
  }}
}}

Rules:
- confidence: integer 0-100
- approx_price: null when unreadable — NEVER hallucinate a price
- state_changed: compare to the previous state summary provided; set true only if something meaningful shifted
- The three mode_assessments are ADVISORY observations, never instructions to place a trade.
- SCALP focuses on immediate trigger quality, VWAP/structure confirmation, and fast invalidation.
- INTRADAY_TREND focuses on 15m/1h alignment, continuation versus exhaustion, and session levels.
- SWING focuses on 1h/4h/daily alignment, thesis quality, structural invalidation, and larger target context.
- The image is primarily a 1-minute chart, but the user message also contains deterministic native-bar context for 5m/15m/1h/4h/1D. Use that context for higher-timeframe bias and alignment; do not downgrade to UNKNOWN merely because those candles are not drawn in the image.
- Treat the native context's timeframe bias, alignment, price, VWAP, and session levels as observed data. Do not fabricate values that are absent or marked UNKNOWN.
- Keep each validation, invalidation, and reason concise and based only on visible chart evidence.
- Respond ONLY with the JSON object, nothing else
"""

_VALID_BIASES      = {"BULLISH", "BEARISH", "NEUTRAL"}
_VALID_STATES      = {"TRENDING_UP","TRENDING_DOWN","RANGE","REVERSAL","BREAKOUT","BREAKDOWN","RETEST","CHOP","UNCLEAR"}
_VALID_EVENTS      = {"LIQUIDITY_SWEEP","RECLAIM","REJECTION","BREAKOUT","BREAKDOWN","RETEST",
                      "FAILED_BREAKOUT","FAILED_BREAKDOWN","STRUCTURE_SHIFT","NONE"}
_VALID_ACTIONS     = {"LONG_WATCH","SHORT_WATCH","WAIT","NO_TRADE"}
_VALID_SHORT_TERM  = {"HH_HL","LH_LL","RANGE","TRANSITION","UNCLEAR"}
_VALID_MODE_POSTURES = {"LONG_BIAS", "SHORT_BIAS", "NEUTRAL"}
_VALID_MODE_SETUP_STATUSES = {"TRIGGER_READY", "FORMING", "WAIT", "NO_TRADE"}
_VALID_ALIGNMENT = {"ALIGNED", "MIXED", "OPPOSED", "UNKNOWN"}
_VALID_INTRADAY_PHASES = {"CONTINUATION", "PULLBACK", "EXHAUSTION", "RANGE", "UNKNOWN"}
_VALID_THESIS_QUALITY = {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}
_MODE_ASSESSMENT_KEYS = {
    "scalp",
    "intraday_trend",
    "swing",
}
_REQUIRED_KEYS     = {"instrument","timestamp","bias","market_state","structure",
                      "last_event","support","resistance","long_condition",
                      "short_condition","action","confidence","state_changed",
                      "state_change_reason","summary","mode_assessments"}


def _build_history_text(recent_history: list[dict]) -> str:
    """Build a compact text summary of recent state transitions."""
    if not recent_history:
        return "No prior observations."
    lines = []
    for obs in recent_history[-_VB_HISTORY_LIMIT:]:
        ts_raw = obs.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            ts = dt.strftime("%H:%M")
        except Exception:
            ts = ts_raw[:5]
        state   = obs.get("market_state", "?")
        event   = obs.get("last_event", "")
        bias    = obs.get("bias", "?")
        action  = obs.get("action", "?")
        conf    = obs.get("confidence", "?")
        changed = "→ SHIFT" if obs.get("state_changed") else ""
        lines.append(f"{ts} | {bias} | {state} | {event} | {action} @{conf}% {changed}")
    return "\n".join(lines)


def _validate_observation(obs: dict) -> tuple[bool, str]:
    """Check required keys and valid enum values.  Returns (ok, reason)."""
    missing = _REQUIRED_KEYS - set(obs.keys())
    if missing:
        return False, f"missing keys: {missing}"
    if obs.get("bias") not in _VALID_BIASES:
        return False, f"invalid bias: {obs.get('bias')}"
    if obs.get("market_state") not in _VALID_STATES:
        return False, f"invalid market_state: {obs.get('market_state')}"
    if obs.get("last_event") not in _VALID_EVENTS:
        return False, f"invalid last_event: {obs.get('last_event')}"
    if obs.get("action") not in _VALID_ACTIONS:
        return False, f"invalid action: {obs.get('action')}"
    struct = obs.get("structure", {})
    if not isinstance(struct, dict):
        return False, "structure must be a dict"
    if struct.get("short_term") not in _VALID_SHORT_TERM:
        return False, f"invalid structure.short_term: {struct.get('short_term')}"
    conf = obs.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not (0 <= conf <= 100):
        return False, f"invalid confidence: {conf}"
    assessments = obs.get("mode_assessments")
    if not isinstance(assessments, dict):
        return False, "mode_assessments must be a dict"
    if set(assessments) != _MODE_ASSESSMENT_KEYS:
        return False, "mode_assessments must contain scalp, intraday_trend, and swing only"
    for mode, assessment in assessments.items():
        ok, reason = _validate_mode_assessment(mode, assessment)
        if not ok:
            return False, reason
    return True, ""


def _validate_mode_assessment(mode: str, assessment: Any) -> tuple[bool, str]:
    """Validate one advisory mode assessment without touching trading logic."""
    if not isinstance(assessment, dict):
        return False, f"mode_assessments.{mode} must be a dict"
    required = {"posture", "setup_status", "confidence", "validation", "invalidation", "reason"}
    if mode in ("intraday_trend", "swing"):
        required |= {"timeframe_alignment"}
    if mode == "intraday_trend":
        required |= {"market_phase", "session_level"}
    if mode == "swing":
        required |= {"thesis_quality", "structural_stop", "target_context"}
    missing = required - set(assessment)
    if missing:
        return False, f"mode_assessments.{mode} missing keys: {missing}"
    if assessment.get("posture") not in _VALID_MODE_POSTURES:
        return False, f"invalid mode_assessments.{mode}.posture"
    if assessment.get("setup_status") not in _VALID_MODE_SETUP_STATUSES:
        return False, f"invalid mode_assessments.{mode}.setup_status"
    confidence = assessment.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not (0 <= confidence <= 100):
        return False, f"invalid mode_assessments.{mode}.confidence"
    if mode in ("intraday_trend", "swing") and assessment.get("timeframe_alignment") not in _VALID_ALIGNMENT:
        return False, f"invalid mode_assessments.{mode}.timeframe_alignment"
    if mode == "intraday_trend" and assessment.get("market_phase") not in _VALID_INTRADAY_PHASES:
        return False, "invalid mode_assessments.intraday_trend.market_phase"
    if mode == "swing" and assessment.get("thesis_quality") not in _VALID_THESIS_QUALITY:
        return False, "invalid mode_assessments.swing.thesis_quality"
    text_fields = {"validation", "invalidation", "reason"}
    if mode == "intraday_trend":
        text_fields.add("session_level")
    if mode == "swing":
        text_fields |= {"structural_stop", "target_context"}
    for field in text_fields:
        if not isinstance(assessment.get(field), str):
            return False, f"mode_assessments.{mode}.{field} must be a string"
    return True, ""


def _log_openai_transport_diagnostic() -> None:
    """Log safe dependency/transport facts when the observer starts."""
    try:
        import openai  # noqa: PLC0415
        openai_version = getattr(openai, "__version__", "unknown")
    except Exception:
        openai_version = "unavailable"
    logger.info(
        "[VISUAL_BRAIN] OpenAI transport diagnostic: openai=%s httpx=%s "
        "trust_env=False tls_verify=True timeout=%ss",
        openai_version,
        getattr(httpx, "__version__", "unknown"),
        _VB_OPENAI_TIMEOUT_SECONDS,
    )


def analyze_visual_market(
    screenshot_bytes: bytes,
    previous_state: Optional[dict],
    recent_history: list[dict],
    instrument: str = "MNQ",
    market_context: Optional[dict] = None,
) -> dict:
    """Send screenshot + compact history to GPT-4o vision; return parsed dict.

    Makes at most 2 API calls (1 retry on invalid JSON or schema violation).
    Logs token counts and estimated cost.
    Raises on total failure — caller wraps in FAIL-OPEN try/except.
    """
    api_key  = os.getenv("AI_INTEGRATIONS_OPENAI_API_KEY", "")
    base_url = os.getenv("AI_INTEGRATIONS_OPENAI_BASE_URL", "https://api.openai.com/v1")

    if not api_key:
        raise RuntimeError("AI_INTEGRATIONS_OPENAI_API_KEY not set")

    from openai import OpenAI  # noqa: PLC0415
    # Use the explicit transport proven on the Windows home PC.  This client is
    # scoped only to the observation worker, disables proxy-environment
    # inheritance, and retains normal TLS certificate verification.
    http_client = httpx.Client(
        trust_env=False,
        timeout=_VB_OPENAI_TIMEOUT_SECONDS,
        verify=True,
    )
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=http_client,
    )

    now_utc = datetime.now(timezone.utc).isoformat()
    cycle = _benchmark_current_cycle()
    if cycle is not None:
        cycle["prompt_timestamp"] = now_utc
    messages = _build_analysis_messages(
        screenshot_bytes, previous_state, recent_history, instrument,
        market_context, now_utc,
    )

    last_exc = None
    try:
        for attempt in range(2):
            attempt_record = _benchmark_record_baseline_attempt(
                attempt + 1, 0, 0
            )
            try:
                resp = client.chat.completions.create(
                    model=_VB_MODEL,
                    max_completion_tokens=_VB_MAX_TOKENS,
                    messages=messages,
                )
                usage = resp.usage
                in_tok  = usage.prompt_tokens     if usage else 0
                out_tok = usage.completion_tokens if usage else 0
                _record_cost(in_tok, out_tok)
                if attempt_record is not None:
                    attempt_record["input_tokens"] = int(in_tok or 0)
                    attempt_record["output_tokens"] = int(out_tok or 0)
                    attempt_record["estimated_cost_usd"] = round(
                        int(in_tok or 0) * _COST_PER_INPUT_TOK
                        + int(out_tok or 0) * _COST_PER_OUTPUT_TOK,
                        8,
                    )
                logger.info(
                    "[VISUAL_BRAIN] model=%s in_tok=%d out_tok=%d est_cost=$%.5f attempt=%d",
                    _VB_MODEL, in_tok, out_tok,
                    in_tok * _COST_PER_INPUT_TOK + out_tok * _COST_PER_OUTPUT_TOK,
                    attempt + 1,
                )

                raw = resp.choices[0].message.content or ""
                # Strip markdown fences if model adds them despite instructions
                raw = raw.strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                    raw = raw.strip()

                obs = json.loads(raw)
                ok, reason = _validate_observation(obs)
                if not ok:
                    raise ValueError(f"Schema violation: {reason}")
                obs["timestamp"] = now_utc   # authoritative server timestamp
                obs["instrument"] = instrument
                # Phase 1 Central Ghost Coordinator: Visual Brain stays explicitly
                # non-trade-like.  It contributes only a deduped telemetry event,
                # never a direction/entry/stop/target observation.
                try:
                    import ghost_coordinator as _gc  # noqa: PLC0415
                    event_id = "%s|%s|%s" % (instrument, now_utc, obs.get("market_state", "UNKNOWN"))
                    if os.getenv("CENTRAL_GHOST_COORDINATOR_FANOUT_ENABLED", "0").lower() in ("true", "1", "yes", "on"):
                        _gc.route_observational_event("visual_brain", event_id)
                    else:
                        _gc.record_observational_event("visual_brain", event_id)
                except Exception:
                    pass
                if attempt_record is not None:
                    attempt_record["ok"] = True
                return obs

            except Exception as exc:
                if attempt_record is not None:
                    text = str(exc).lower()
                    attempt_record["error_category"] = (
                        "schema" if "schema" in text or "json" in text
                        else "unknown"
                    )
                _benchmark_record_baseline_failure(exc)
                last_exc = exc
                logger.warning("[VISUAL_BRAIN] analyze attempt %d failed: %s", attempt + 1, exc)
                if attempt == 0:
                    time.sleep(1)

        raise RuntimeError(f"analyze_visual_market failed after 2 attempts: {last_exc}")
    finally:
        http_client.close()


# ─────────────────────────────────────────────────────────────────────────────
# Database persistence
# ─────────────────────────────────────────────────────────────────────────────

def _insert_observation(obs: dict, screenshot_path: Optional[str] = None) -> Optional[int]:
    """Insert one observation row; returns the new id or None on failure."""
    if not VB_DB_READY:
        return None
    conn = _get_conn()
    if conn is None:
        return None
    try:
        struct = obs.get("structure") or {}
        support = obs.get("support") or {}
        resistance = obs.get("resistance") or {}
        cur_price = None
        try:
            if _price_store is not None:
                inst = obs.get("instrument", "MNQ")
                cur_price = _price_store.get(inst, {}).get("value")
        except Exception:
            pass

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO visual_brain_observations
                  (timestamp, instrument, bias, market_state, short_term_structure,
                   last_event, action, confidence,
                   support_description, support_price,
                   resistance_description, resistance_price,
                   long_condition, short_condition,
                   state_changed, state_change_reason, summary,
                   screenshot_path, raw_json, entry_price_at_obs)
                VALUES
                  (%s, %s, %s, %s, %s,
                   %s, %s, %s,
                   %s, %s,
                   %s, %s,
                   %s, %s,
                   %s, %s, %s,
                   %s, %s, %s)
                RETURNING id
            """, (
                obs.get("timestamp"),
                obs.get("instrument", "MNQ"),
                obs.get("bias"),
                obs.get("market_state"),
                struct.get("short_term"),
                obs.get("last_event"),
                obs.get("action"),
                int(obs.get("confidence") or 0),
                support.get("description") or "",
                support.get("approx_price"),
                resistance.get("description") or "",
                resistance.get("approx_price"),
                obs.get("long_condition") or "",
                obs.get("short_condition") or "",
                bool(obs.get("state_changed")),
                obs.get("state_change_reason") or "",
                obs.get("summary") or "",
                screenshot_path,
                json.dumps(obs),
                cur_price,
            ))
            row = cur.fetchone()
            conn.commit()
            row_id = row[0] if row else None
            if row_id is not None and _research_observation_fn is not None:
                try:
                    _research_observation_fn(row_id, dict(obs), cur_price)
                except Exception as callback_exc:
                    logger.debug("[VISUAL_BRAIN] research observation callback: %s", callback_exc)
            return row_id
    except Exception as exc:
        logger.warning("[VISUAL_BRAIN] DB insert failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# History + status reads
# ─────────────────────────────────────────────────────────────────────────────

def _flatten_obs(obs: dict) -> dict:
    """Flatten the nested model response to match the DB history row shape.

    The model returns nested structure/support/resistance objects; the DB
    stores flat columns (short_term_structure, support_description, etc.).
    This ensures get_last_observation() and get_history() return the same
    shape so the dashboard panel renders correctly from both sources.

    Safe to call on an already-flat dict — setdefault guards prevent
    overwriting values that are already present.
    """
    if not obs:
        return obs
    flat = dict(obs)
    struct = obs.get("structure") or {}
    sup    = obs.get("support")   or {}
    res    = obs.get("resistance") or {}

    # Remove nested objects and promote their keys to top level
    flat.pop("structure",  None)
    flat.pop("support",    None)
    flat.pop("resistance", None)

    flat.setdefault("short_term_structure",   struct.get("short_term"))
    flat.setdefault("support_description",    sup.get("description", ""))
    flat.setdefault("support_price",          sup.get("approx_price"))
    flat.setdefault("resistance_description", res.get("description", ""))
    flat.setdefault("resistance_price",       res.get("approx_price"))

    # DB-only fields absent in the raw model response — default to None/False
    for key in ("id", "screenshot_path", "p1m", "p3m", "p5m",
                "p10m", "p15m", "mfe", "mae"):
        flat.setdefault(key, None)
    flat.setdefault("outcome_resolved", False)
    # Mode assessments live in raw_json rather than dedicated DB columns, keeping
    # the existing table schema stable. Older observations intentionally surface
    # an empty object so callers can show an unavailable state without breaking.
    assessments = flat.get("mode_assessments")
    flat["mode_assessments"] = assessments if isinstance(assessments, dict) else {}
    return flat


def get_last_observation(instrument: str = "MNQ") -> Optional[dict]:
    """Return the most recent observation dict from cache or DB, flattened.

    Always returns the same key shape as get_history() so callers do not need
    to handle two different structures.
    """
    with _VB_LOCK:
        cached = _LAST_OBSERVATION_BY_INST.get(instrument)
        if cached:
            return _flatten_obs(dict(cached))
    if not VB_DB_READY:
        return None
    conn = _get_conn()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT raw_json FROM visual_brain_observations
                WHERE instrument = %s
                ORDER BY timestamp DESC LIMIT 1
            """, (instrument,))
            row = cur.fetchone()
            if row and row[0]:
                raw = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                return _flatten_obs(raw)
        return None
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_history(instrument: str = "MNQ", limit: int = 20) -> list[dict]:
    """Return the last N observations from DB (newest first)."""
    if not VB_DB_READY:
        return []
    conn = _get_conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, timestamp, instrument, bias, market_state,
                       short_term_structure, last_event, action, confidence,
                       support_description, support_price,
                       resistance_description, resistance_price,
                       long_condition, short_condition,
                       state_changed, state_change_reason, summary,
                       screenshot_path, p1m, p3m, p5m, p10m, p15m,
                       mfe, mae, outcome_resolved, raw_json
                FROM visual_brain_observations
                WHERE instrument = %s
                ORDER BY timestamp DESC
                LIMIT %s
            """, (instrument, min(limit, 100)))
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            result = []
            for r in rows:
                row_dict = dict(zip(cols, r))
                # Coerce timestamp to ISO string
                ts = row_dict.get("timestamp")
                if hasattr(ts, "isoformat"):
                    row_dict["timestamp"] = ts.isoformat()
                # Coerce NUMERIC (Decimal) outcome fields to float so Flask can
                # serialize them as JSON numbers.  psycopg2 returns pg NUMERIC as
                # Python Decimal, which json.dumps() rejects and the TS client
                # receives as a quoted string, breaking .toFixed() calls.
                for key in ("support_price", "resistance_price",
                            "p1m", "p3m", "p5m", "p10m", "p15m", "mfe", "mae"):
                    v = row_dict.get(key)
                    if v is not None:
                        row_dict[key] = float(v)
                raw = row_dict.pop("raw_json", None)
                if raw:
                    try:
                        raw_obs = raw if isinstance(raw, dict) else json.loads(raw)
                        if isinstance(raw_obs, dict):
                            row_dict["mode_assessments"] = raw_obs.get("mode_assessments")
                    except Exception:
                        pass
                result.append(_flatten_obs(row_dict))
            return result
    except Exception as exc:
        logger.debug("[VISUAL_BRAIN] get_history: %s", exc)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Ghost outcome backfill watcher
# ─────────────────────────────────────────────────────────────────────────────

def _backfill_ghost_outcomes() -> None:
    """Backfill p1m/p3m/p5m/p10m/p15m/mfe/mae for unresolved observations.

    Uses injected _bars_fn and _price_store — never imports app or databento_brain
    directly (see module docstring for why).

    Concurrency guard: _BACKFILL_RUNNING prevents a new thread from piling up
    while a previous one is still running DB queries.  Without this, 4 instruments
    × every 60s can accumulate hundreds of simultaneous DB connections under load.
    """
    global _BACKFILL_RUNNING
    if _BACKFILL_RUNNING:
        return   # previous run still in progress — skip
    _BACKFILL_RUNNING = True
    try:
        _backfill_ghost_outcomes_inner()
    finally:
        _BACKFILL_RUNNING = False


def _backfill_ghost_outcomes_inner() -> None:
    """Inner implementation — called only when the concurrency guard is held."""
    if not VB_DB_READY:
        return
    if _bars_fn is None:
        return   # bars not injected yet; skip silently

    conn = _get_conn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            # Find unresolved rows older than 15 minutes
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
            cur.execute("""
                SELECT id, timestamp, instrument, action, entry_price_at_obs
                FROM visual_brain_observations
                WHERE outcome_resolved = FALSE
                  AND timestamp < %s
                ORDER BY timestamp DESC
                LIMIT 50
            """, (cutoff,))
            rows = cur.fetchall()

        for row_id, obs_ts, inst, action, entry_px in rows:
            if not entry_px:
                # No entry price recorded at observation time — skip
                conn2 = _get_conn()
                if conn2:
                    try:
                        with conn2.cursor() as c2:
                            c2.execute(
                                "UPDATE visual_brain_observations SET outcome_resolved=TRUE WHERE id=%s",
                                (row_id,)
                            )
                            conn2.commit()
                            if _research_outcome_fn is not None:
                                _research_outcome_fn(row_id, {
                                    "outcome_resolved": True,
                                    "reason": "missing_entry_price",
                                })
                    except Exception:
                        pass
                    finally:
                        try: conn2.close()
                        except Exception: pass
                continue

            bars = _bars_fn(inst or "MNQ") if _bars_fn else []
            if not bars:
                continue

            # Find bars after the observation timestamp
            obs_epoch = obs_ts.timestamp() if hasattr(obs_ts, "timestamp") else 0
            future_bars = [
                b for b in bars
                if (b.get("ts_event") or b.get("ts", 0)) > obs_epoch
            ]
            if not future_bars:
                continue

            # Only compute directional P&L for actionable observations.
            # WAIT and NO_TRADE have no bias — storing them as "SHORT" would
            # corrupt research analytics.  Mark these resolved with NULL outcomes.
            if action not in ("LONG_WATCH", "SHORT_WATCH"):
                conn2 = _get_conn()
                if conn2:
                    try:
                        with conn2.cursor() as c2:
                            c2.execute(
                                "UPDATE visual_brain_observations "
                                "SET outcome_resolved=TRUE WHERE id=%s",
                                (row_id,)
                            )
                            conn2.commit()
                            if _research_outcome_fn is not None:
                                _research_outcome_fn(row_id, {
                                    "outcome_resolved": True,
                                    "reason": "non_actionable_observation",
                                })
                    except Exception:
                        pass
                    finally:
                        try: conn2.close()
                        except Exception: pass
                continue

            direction = 1 if action == "LONG_WATCH" else -1

            def _excursion(bars_slice: list) -> tuple[float, float]:
                """Return (favorable_pct, adverse_pct) for direction."""
                if not bars_slice:
                    return 0.0, 0.0
                highs  = [b.get("high", entry_px) for b in bars_slice]
                lows   = [b.get("low",  entry_px) for b in bars_slice]
                if direction == 1:
                    fav = (max(highs) - entry_px) / entry_px * 100
                    adv = (entry_px - min(lows))  / entry_px * 100
                else:
                    fav = (entry_px - min(lows))  / entry_px * 100
                    adv = (max(highs) - entry_px) / entry_px * 100
                return round(fav, 4), round(adv, 4)

            def _close_at(n_bars: int) -> Optional[float]:
                if len(future_bars) >= n_bars:
                    close = future_bars[n_bars - 1].get("close", entry_px)
                    return round((close - entry_px) / entry_px * direction * 100, 4)
                return None

            p1m  = _close_at(1)
            p3m  = _close_at(3)
            p5m  = _close_at(5)
            p10m = _close_at(10)
            p15m = _close_at(15)
            mfe, mae = _excursion(future_bars[:15])

            conn3 = _get_conn()
            if conn3:
                try:
                    with conn3.cursor() as c3:
                        c3.execute("""
                            UPDATE visual_brain_observations
                            SET p1m=%s, p3m=%s, p5m=%s, p10m=%s, p15m=%s,
                                mfe=%s, mae=%s, outcome_resolved=TRUE
                            WHERE id=%s
                        """, (p1m, p3m, p5m, p10m, p15m, mfe, mae, row_id))
                        conn3.commit()
                    if _research_outcome_fn is not None:
                        _research_outcome_fn(row_id, {
                            "outcome_resolved": True,
                            "p1m": p1m, "p3m": p3m, "p5m": p5m,
                            "p10m": p10m, "p15m": p15m,
                            "mfe": mfe, "mae": mae,
                            "reason": "forward_returns_complete",
                        })
                    logger.debug("[VISUAL_BRAIN] ghost outcome resolved: id=%d p1m=%s p15m=%s",
                                 row_id, p1m, p15m)
                except Exception as ue:
                    logger.debug("[VISUAL_BRAIN] ghost outcome update failed: %s", ue)
                    try: conn3.rollback()
                    except Exception: pass
                finally:
                    try: conn3.close()
                    except Exception: pass
    except Exception as exc:
        logger.debug("[VISUAL_BRAIN] _backfill_ghost_outcomes: %s", exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Main worker loop
# ─────────────────────────────────────────────────────────────────────────────

_VB_TIMERS: dict = {}   # instrument → active threading.Timer
_VB_TIMER_LOCK = threading.RLock()
_VB_START_LOCK = threading.Lock()
_VB_STARTED = False
_BACKFILL_RUNNING = False   # guards against concurrent backfill threads


def _vb_tick(instrument: str = "MNQ") -> None:
    """One observation cycle for one instrument: capture → analyze → persist → reschedule.

    Scheduling contract: `_schedule_next(instrument)` is called EXACTLY ONCE,
    exclusively in `finally`.  Early returns from failure paths must NOT call
    `_schedule_next()` themselves — doing so would double-schedule timers on every
    persistent failure, rapidly creating overlapping model work and runaway API cost.

    The enabled guard is checked BEFORE `try/finally` so that a disabled tick
    is a true no-op: no reschedule, no timers accumulated.  `_schedule_next()`
    itself is also a no-op when disabled, keeping the disabled path byte-identical.
    """
    global _VB_TIMERS

    if not VISUAL_BRAIN_ENABLED:
        return   # disabled → true no-op; no timer accumulated

    benchmark_cycle = _benchmark_begin_cycle(instrument)
    try:
        screenshot_bytes: Optional[bytes] = None
        bars_for_benchmark: list[dict] = []

        # ── Capture ──────────────────────────────────────────────────────────
        # Screenshots are ephemeral analysis inputs — they are NOT persisted to
        # disk.  Saving delete=False temp files would exhaust local storage over
        # time.  screenshot_path is always stored as NULL; use object storage if
        # permanent retention is needed (see Task #189).
        market_context = {}
        try:
            if _bars_fn is not None:
                bars_for_benchmark = list(_bars_fn(instrument) or [])
                market_context = _build_market_context(bars_for_benchmark, instrument)
        except Exception as context_exc:
            logger.warning("[VISUAL_BRAIN] market context failed: %s", context_exc)
        try:
            screenshot_bytes = capture_chart_screenshot(instrument)
        except Exception as cap_exc:
            logger.warning("[VISUAL_BRAIN] screenshot failed: %s", cap_exc)

        if not screenshot_bytes:
            _benchmark_prepare_cycle(
                benchmark_cycle,
                bars=bars_for_benchmark,
                screenshot_bytes=None,
                market_context=market_context,
                previous_state=None,
                recent_history=[],
            )
            logger.warning("[VISUAL_BRAIN] skipping cycle — no screenshot")
            return   # finally → _schedule_next() fires exactly once

        # ── Fetch history ────────────────────────────────────────────────────
        recent_history = get_history(instrument, limit=_VB_HISTORY_LIMIT)
        # get_history returns newest-first; reverse for chronological model context
        recent_history.reverse()

        with _VB_LOCK:
            _cached_prev = _LAST_OBSERVATION_BY_INST.get(instrument)
            prev_state = dict(_cached_prev) if _cached_prev else None

        _benchmark_prepare_cycle(
            benchmark_cycle,
            bars=bars_for_benchmark,
            screenshot_bytes=screenshot_bytes,
            market_context=market_context,
            previous_state=prev_state,
            recent_history=recent_history,
        )

        # ── Analyze ──────────────────────────────────────────────────────────
        if benchmark_cycle is not None:
            _BENCHMARK_LOCAL.cycle = benchmark_cycle
        try:
            obs = analyze_visual_market(
                screenshot_bytes=screenshot_bytes,
                previous_state=prev_state,
                recent_history=recent_history,
                instrument=instrument,
                market_context=market_context,
            )
        except Exception as analyze_exc:
            logger.warning("[VISUAL_BRAIN] analysis failed: %s", analyze_exc)
            return   # finally → _schedule_next() fires exactly once
        finally:
            if benchmark_cycle is not None:
                _BENCHMARK_LOCAL.cycle = None

        # Keep the deterministic context with the observation so the dashboard
        # can show the actual HTF inputs even when the model chooses WAIT.
        obs["market_context"] = market_context

        # ── Persist ──────────────────────────────────────────────────────────
        obs_id = _insert_observation(obs, screenshot_path=None)
        with _VB_LOCK:
            _LAST_OBSERVATION_BY_INST[instrument] = dict(obs)
        logger.info(
            "[VISUAL_BRAIN] obs id=%s bias=%s state=%s event=%s action=%s conf=%s%%",
            obs_id,
            obs.get("bias"),
            obs.get("market_state"),
            obs.get("last_event"),
            obs.get("action"),
            obs.get("confidence"),
        )

        # ── Ghost outcome backfill (non-blocking) ────────────────────────────
        threading.Thread(target=_backfill_ghost_outcomes, daemon=True).start()

        # Optional paired candidate runs only after the canonical observation is
        # persisted and cached. It has no path back into the baseline result.
        _run_paired_benchmark_candidate(benchmark_cycle)

    except Exception as exc:
        logger.error("[VISUAL_BRAIN] tick error (trading engine unaffected): %s", exc)

    finally:
        _benchmark_finish_cycle(benchmark_cycle)
        # Single reschedule point — always reached, never duplicated.
        _schedule_next(instrument)


def _schedule_next(instrument: str = "MNQ", delay: Optional[float] = None) -> None:
    """Schedule the next tick for `instrument`.

    `delay` overrides the interval for the initial staggered start.
    First-tick delays are VISUAL_BRAIN_INTERVAL + i*slot (≥60s) so all instruments
    wait for boot to complete before any screenshot/model work begins.
    When None (the default), uses VISUAL_BRAIN_INTERVAL so regular ticks fire at
    the configured cadence regardless of how long each analysis took.
    """
    global _VB_TIMERS
    if not VISUAL_BRAIN_ENABLED:
        return
    interval = delay if delay is not None else float(VISUAL_BRAIN_INTERVAL)
    t = threading.Timer(interval, _vb_tick, args=(instrument,))
    t.daemon = True
    with _VB_TIMER_LOCK:
        prior = _VB_TIMERS.get(instrument)
        if prior is not None and prior.is_alive():
            prior.cancel()
        _VB_TIMERS[instrument] = t
        t.start()


def start(
    db_conn_fn: Optional[Callable] = None,
    price_store: Optional[dict] = None,
    vwap_store: Optional[dict] = None,
    bars_fn: Optional[Callable] = None,
    research_observation_fn: Optional[Callable] = None,
    research_outcome_fn: Optional[Callable] = None,
    benchmark_state_fn: Optional[Callable] = None,
) -> None:
    """Start the Visual Brain worker.  Call once at boot if VISUAL_BRAIN_ENABLED.

    Args:
        db_conn_fn:  callable() → psycopg2 connection — injected from app.py's
                     _learning_conn so the REAL __main__ module connection pool
                     is used.  Never stored as a module-level `import app`.
        price_store: dict reference to AUTO_PRICE_BY_TICKER from app.py's
                     __main__ globals (not a copy — same object so prices update).
        vwap_store:  dict reference to VWAP_BY_TICKER from app.py's __main__
                     globals — used to overlay the authoritative session VWAP on
                     the chart so the model reads the correct level.
        bars_fn:     callable(instrument: str) → list[dict] wrapping
                     DATABENTO_BARS_BY_INST.get() from app.py's __main__ globals.
    """
    global _db_conn_fn, _price_store, _vwap_store, _bars_fn, _VB_STARTED
    global _research_observation_fn, _research_outcome_fn, _benchmark_state_fn
    if db_conn_fn is not None:
        _db_conn_fn = db_conn_fn
    if price_store is not None:
        _price_store = price_store
    if vwap_store is not None:
        _vwap_store = vwap_store
    if bars_fn is not None:
        _bars_fn = bars_fn
    if research_observation_fn is not None:
        _research_observation_fn = research_observation_fn
    if research_outcome_fn is not None:
        _research_outcome_fn = research_outcome_fn
    if benchmark_state_fn is not None:
        _benchmark_state_fn = benchmark_state_fn

    if not VISUAL_BRAIN_ENABLED:
        logger.info("[VISUAL_BRAIN] disabled (VISUAL_BRAIN_ENABLED not set) — byte-identical mode")
        return
    with _VB_START_LOCK:
        if _VB_STARTED:
            logger.info("[VISUAL_BRAIN] start ignored — observer already scheduled")
            return
        _VB_STARTED = True
    _log_openai_transport_diagnostic()
    logger.info("[VISUAL_BRAIN] enabled — instruments: %s  interval=%ds",
                ", ".join(_VB_SYMBOLS), VISUAL_BRAIN_INTERVAL)
    # Each instrument's first tick is delayed by at least one full VISUAL_BRAIN_INTERVAL
    # (preserving the original "let boot complete first" safety margin) plus a
    # per-instrument slot offset that spreads all first ticks across the 300-second
    # window, preventing a concurrent model-call burst at startup.
    #
    # With n=2 and interval=300s → slots of 150s → delays: 300, 450.
    # With n=1 (single-instrument override) → delay: 300.
    n = max(len(_VB_SYMBOLS), 1)
    slot = float(VISUAL_BRAIN_INTERVAL) / n
    for i, inst in enumerate(_VB_SYMBOLS):
        first_delay = float(VISUAL_BRAIN_INTERVAL) + float(i) * slot
        logger.info("[VISUAL_BRAIN] %s observer first_tick_delay=%.0fs", inst, first_delay)
        _schedule_next(inst, delay=first_delay)
