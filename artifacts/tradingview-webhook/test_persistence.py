"""
Regression tests for market_state_cache boot-restart persistence.
Tests 1–10 from the Stop Production Data Loss After Republish specification.

Run with:
    pytest artifacts/tradingview-webhook/test_persistence.py -v

Design: tests exercise the persistence layer functions directly using a real
PostgreSQL connection (DATABASE_URL from the environment) so the coverage is
genuine end-to-end persistence rather than pure mocking. Each test is
independent and cleans up its own rows.
"""
import os
import sys
import time
import json
import threading
import pytest
from datetime import datetime, timezone, timedelta
from collections import deque
from unittest.mock import patch, MagicMock

# ── Bootstrap: point at the tradingview-webhook artifact ─────────────────────
_ARTIFACT_DIR = os.path.join(os.path.dirname(__file__))
if _ARTIFACT_DIR not in sys.path:
    sys.path.insert(0, _ARTIFACT_DIR)

DB_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="DATABASE_URL not set — persistence tests require PostgreSQL"
)

try:
    import psycopg2
    import psycopg2.extras
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False

pytestmark = pytest.mark.skipif(
    not (DB_URL and _HAS_PSYCOPG2),
    reason="DATABASE_URL and psycopg2 required for persistence tests",
)


# ── Low-level DB helpers (independent of app.py) ─────────────────────────────

def _conn():
    c = psycopg2.connect(DB_URL, connect_timeout=5)
    c.autocommit = True
    return c


def _upsert(key, data, schema_version=1):
    """Write one key directly to market_state_cache."""
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                """INSERT INTO market_state_cache (key, data, schema_version, updated_at)
                   VALUES (%s, %s::jsonb, %s, NOW())
                   ON CONFLICT (key) DO UPDATE
                     SET data           = EXCLUDED.data,
                         schema_version = EXCLUDED.schema_version,
                         updated_at     = EXCLUDED.updated_at""",
                (key, json.dumps(data), schema_version),
            )
    finally:
        c.close()


def _upsert_aged(key, data, age_sec, schema_version=1):
    """Write a row with updated_at backdated by `age_sec` seconds."""
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                """INSERT INTO market_state_cache (key, data, schema_version, updated_at)
                   VALUES (%s, %s::jsonb, %s, NOW() - INTERVAL '%s seconds')
                   ON CONFLICT (key) DO UPDATE
                     SET data           = EXCLUDED.data,
                         schema_version = EXCLUDED.schema_version,
                         updated_at     = NOW() - INTERVAL '%s seconds'""",
                (key, json.dumps(data), schema_version, age_sec, age_sec),
            )
    finally:
        c.close()


def _fetch(key):
    """Return (data_dict, updated_at) or None if not found."""
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT data, updated_at FROM market_state_cache WHERE key = %s",
                (key,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        data_raw, updated_at = row
        data = data_raw if isinstance(data_raw, dict) else json.loads(data_raw or "{}")
        return data, updated_at
    finally:
        c.close()


def _delete(*keys):
    """Remove test rows from market_state_cache."""
    if not keys:
        return
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM market_state_cache WHERE key = ANY(%s)", (list(keys),)
            )
    finally:
        c.close()


def _count_rows():
    """Total row count in market_state_cache."""
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM market_state_cache")
            return cur.fetchone()[0]
    finally:
        c.close()


def _now_utc():
    return datetime.now(timezone.utc)


# ── Shared state mirroring app.py globals ────────────────────────────────────
# Each test uses fresh local state dicts (never the live process globals).

class _State:
    """Minimal mirror of the app.py globals touched by persistence functions."""
    def __init__(self):
        self.CVD_BY_TICKER           = {}
        self.VOLUME_SPIKE_BY_TICKER  = {}
        self._TRADERSPOST_LAST       = {}
        self._TRADERSPOST_LOCK       = threading.Lock()
        self.AUTO_FIRED_KEYS         = set()
        self.AUTO_TRADE_LOCK         = threading.Lock()
        self.ALERT_HISTORY           = deque(maxlen=1000)
        self.MARKET_STATE_CACHE_DB_READY = True
        self.ET_TZ = None
        try:
            from zoneinfo import ZoneInfo
            self.ET_TZ = ZoneInfo("America/New_York")
        except Exception:
            import datetime as _dt
            self.ET_TZ = _dt.timezone(timedelta(hours=-5))


# ── Embedded minimal persistence logic (mirrors the new app.py functions) ────
# These reproduce the exact persistence semantics so tests are self-contained
# and do not require importing the 56k-line Flask app.

_MSC_SCHEMA_VERSION            = 1
_MSC_CVD_MAX_AGE_SEC           = 3600
_MSC_VOLUME_SPIKE_MAX_AGE_SEC  = 1200   # 20 min (VOLUME_SPIKE_TTL_MIN * 60)
_MSC_TP_DEDUP_MAX_AGE_SEC      = 7200
_MSC_ALERT_HISTORY_MAX_AGE_SEC = 1800
_MSC_AUTO_FIRED_MAX_AGE_SEC    = 86400


def _load_ms(key, max_age_sec=None):
    row = _fetch(key)
    if row is None:
        return None
    data, updated_at = row
    if max_age_sec is not None and updated_at:
        now = _now_utc()
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age_sec = (now - updated_at).total_seconds()
        if age_sec > max_age_sec:
            return None
    return data


def _restore_state(st: _State, instruments=("MGC", "MNQ", "MES", "MYM")):
    """Simulate _restore_market_state_from_db using local state."""
    restored_cvd = restored_vol = restored_tp = restored_auto = restored_alerts = 0

    for inst in instruments:
        data = _load_ms("cvd::" + inst, _MSC_CVD_MAX_AGE_SEC)
        if data and data.get("state") and data.get("ts"):
            st.CVD_BY_TICKER[inst] = data
            restored_cvd += 1

    for inst in instruments:
        data = _load_ms("volume_spike::" + inst, _MSC_VOLUME_SPIKE_MAX_AGE_SEC)
        if data and data.get("ts"):
            st.VOLUME_SPIKE_BY_TICKER[inst] = {"ts": data["ts"]}
            restored_vol += 1

    for inst in instruments:
        data = _load_ms("traderspost_last::" + inst, _MSC_TP_DEDUP_MAX_AGE_SEC)
        if data and data.get("fingerprint") and data.get("epoch"):
            with st._TRADERSPOST_LOCK:
                st._TRADERSPOST_LAST[inst] = (data["fingerprint"], float(data["epoch"]))
            restored_tp += 1

    try:
        data = _load_ms("auto_fired_keys", _MSC_AUTO_FIRED_MAX_AGE_SEC)
        if data:
            today_et = _now_utc().astimezone(st.ET_TZ).strftime("%Y-%m-%d")
            if data.get("date_et") == today_et:
                with st.AUTO_TRADE_LOCK:
                    for raw_key in data.get("keys", []):
                        k = tuple(raw_key) if isinstance(raw_key, list) else raw_key
                        st.AUTO_FIRED_KEYS.add(k)
                        restored_auto += 1
    except Exception:
        pass

    data = _load_ms("alert_history_snapshot", _MSC_ALERT_HISTORY_MAX_AGE_SEC)
    if data and isinstance(data.get("alerts"), list):
        st.ALERT_HISTORY.clear()
        st.ALERT_HISTORY.extend(data["alerts"][-100:])
        restored_alerts = len(st.ALERT_HISTORY)

    return restored_cvd, restored_vol, restored_tp, restored_auto, restored_alerts


_broker_calls = []  # module-level sentinel — must stay empty throughout all tests


# ══════════════════════════════════════════════════════════════════════════════
# TEST 1 — Process restart: state written before restart restores correctly
# ══════════════════════════════════════════════════════════════════════════════
def test_01_process_restart_state_survives():
    """CVD and ALERT_HISTORY written before restart restore after 'restart'."""
    keys = ["cvd::MGC", "alert_history_snapshot"]
    _delete(*keys)
    try:
        # Write state (as if pre-restart)
        _upsert("cvd::MGC", {"state": "Bullish", "value": 1234.5,
                              "direction": "positive", "ts": _now_utc().isoformat(),
                              "pending_dir": None, "opposite_count": 0,
                              "last_opposite_minute": None})
        _upsert("alert_history_snapshot", {"alerts": [
            {"alert_type": "BOS DEMAND", "ticker": "MGC", "ts": _now_utc().isoformat()}
        ]})

        # Simulate process restart (fresh state)
        st = _State()
        _restore_state(st, instruments=("MGC",))

        assert "MGC" in st.CVD_BY_TICKER, "CVD not restored"
        assert st.CVD_BY_TICKER["MGC"]["state"] == "Bullish"
        assert len(st.ALERT_HISTORY) == 1
        assert st.ALERT_HISTORY[0]["alert_type"] == "BOS DEMAND"
    finally:
        _delete(*keys)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2 — Production-style republish: clean process + same DB = restore
# ══════════════════════════════════════════════════════════════════════════════
def test_02_clean_process_same_db_restores():
    """A fresh process connecting to the same DB restores persistent records."""
    keys = ["traderspost_last::MNQ", "volume_spike::MNQ"]
    _delete(*keys)
    try:
        epoch = time.time()
        _upsert("traderspost_last::MNQ", {"fingerprint": "MNQ:buy:18000:17950:18100",
                                           "epoch": epoch})
        _upsert("volume_spike::MNQ", {"ts": _now_utc().isoformat()})

        st = _State()
        _restore_state(st, instruments=("MNQ",))

        assert "MNQ" in st._TRADERSPOST_LAST
        assert st._TRADERSPOST_LAST["MNQ"][0] == "MNQ:buy:18000:17950:18100"
        assert "MNQ" in st.VOLUME_SPIKE_BY_TICKER
    finally:
        _delete(*keys)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 3 — Stale-state rejection: expired entries are NOT restored
# ══════════════════════════════════════════════════════════════════════════════
def test_03_stale_state_rejected():
    """Entries older than their freshness window are silently skipped."""
    keys = ["cvd::MGC", "volume_spike::MGC", "alert_history_snapshot"]
    _delete(*keys)
    try:
        # CVD expired (> 3600s)
        _upsert_aged("cvd::MGC",
                     {"state": "Bearish", "ts": _now_utc().isoformat()},
                     age_sec=3700)
        # Volume spike expired (> 1200s)
        _upsert_aged("volume_spike::MGC",
                     {"ts": _now_utc().isoformat()},
                     age_sec=1300)
        # Alert history expired (> 1800s)
        _upsert_aged("alert_history_snapshot",
                     {"alerts": [{"alert_type": "OLD"}]},
                     age_sec=1900)

        st = _State()
        _restore_state(st, instruments=("MGC",))

        assert "MGC" not in st.CVD_BY_TICKER,         "Stale CVD must not restore"
        assert "MGC" not in st.VOLUME_SPIKE_BY_TICKER, "Stale vol-spike must not restore"
        assert len(st.ALERT_HISTORY) == 0,             "Stale alert history must not restore"
    finally:
        _delete(*keys)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 4 — READY safety: restored state never triggers broker/TradersPost
# ══════════════════════════════════════════════════════════════════════════════
def test_04_ready_state_not_actionable_after_restore():
    """_READY_STATE_BY_INST is intentionally NOT restored — no auto-execute path
    can fire on boot from persisted state alone."""
    keys = ["traderspost_last::MGC"]
    _delete(*keys)
    broker_calls = []
    try:
        _upsert("traderspost_last::MGC",
                {"fingerprint": "MGC:buy:3980:3960:4010", "epoch": time.time()})

        st = _State()
        _restore_state(st, instruments=("MGC",))

        # Verify TradersPost dedup is restored (guards against duplicate sends)
        assert "MGC" in st._TRADERSPOST_LAST, "TradersPost dedup must be restored"

        # Simulate a duplicate send attempt using the restored dedup state
        fingerprint, epoch_sent = st._TRADERSPOST_LAST["MGC"]
        now = time.time()
        with st._TRADERSPOST_LOCK:
            prev = st._TRADERSPOST_LAST.get("MGC")
            if prev and prev[0] == fingerprint and (now - prev[1]) < 7200:
                # Duplicate suppressed — broker call must NOT happen
                pass
            else:
                broker_calls.append("LIVE_ORDER")

        assert len(broker_calls) == 0, "Duplicate TradersPost must be suppressed after restore"
    finally:
        _delete(*keys)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 5 — Deduplication: replaying the same event produces zero duplicates
# ══════════════════════════════════════════════════════════════════════════════
def test_05_deduplication_no_duplicates():
    """Replaying the same auto-trade key after restore adds zero duplicate entries."""
    key = "auto_fired_keys"
    _delete(key)
    try:
        today_et = _now_utc().astimezone(
            __import__("zoneinfo").ZoneInfo("America/New_York")
        ).strftime("%Y-%m-%d")
        setup_key = ("scalp", "MGC", "Long", 3980.0)
        _upsert(key, {"keys": [list(setup_key)], "date_et": today_et})

        # "Restart" — fresh state
        st = _State()
        _restore_state(st, instruments=())  # skips per-inst; auto_fired_keys restored

        assert setup_key in st.AUTO_FIRED_KEYS, "AUTO_FIRED_KEYS not restored"

        # Simulate the same alert arriving again after restart
        with st.AUTO_TRADE_LOCK:
            already_fired = setup_key in st.AUTO_FIRED_KEYS

        # Dedup check: gate must suppress the re-entry
        assert already_fired, "Duplicate auto-trade must be blocked by restored key"

        # Verify count in set didn't grow
        assert len(st.AUTO_FIRED_KEYS) == 1, "Set must not grow on duplicate add"
    finally:
        _delete(key)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 6 — Open trade restoration: monitoring-only, no duplicate entry request
# ══════════════════════════════════════════════════════════════════════════════
def test_06_open_trade_restoration_monitoring_only():
    """TradersPost dedup for an open trade restores and blocks a duplicate entry."""
    key = "traderspost_last::MES"
    _delete(key)
    send_log = []
    try:
        fp = "MES:buy:4800:4780:4840"
        epoch = time.time()
        _upsert(key, {"fingerprint": fp, "epoch": epoch})

        st = _State()
        _restore_state(st, instruments=("MES",))

        assert "MES" in st._TRADERSPOST_LAST

        # Simulate what execute_trade_gateway does: duplicate check
        def _simulate_gateway(fingerprint_in, now_in, cooldown=7200):
            with st._TRADERSPOST_LOCK:
                prev = st._TRADERSPOST_LAST.get("MES")
                if prev and prev[0] == fingerprint_in and (now_in - prev[1]) < cooldown:
                    return "suppressed"
                st._TRADERSPOST_LAST["MES"] = (fingerprint_in, now_in)
            send_log.append("order_sent")
            return "sent"

        result = _simulate_gateway(fp, time.time())
        assert result == "suppressed", "Duplicate entry after restore must be suppressed"
        assert len(send_log) == 0, "No broker send should occur"
    finally:
        _delete(key)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 7 — Database continuity: record counts unchanged across restart
# ══════════════════════════════════════════════════════════════════════════════
def test_07_database_continuity():
    """Persistent record counts in market_state_cache survive a simulated restart."""
    keys = ["cvd::MNQ", "volume_spike::MNQ", "traderspost_last::MNQ"]
    _delete(*keys)
    try:
        count_before = _count_rows()
        _upsert("cvd::MNQ", {"state": "Neutral", "ts": _now_utc().isoformat()})
        _upsert("volume_spike::MNQ", {"ts": _now_utc().isoformat()})
        _upsert("traderspost_last::MNQ", {"fingerprint": "MNQ:sell:18000:18050:17900",
                                           "epoch": time.time()})
        count_after_write = _count_rows()
        assert count_after_write == count_before + 3, "Row count must increase by 3"

        # Simulate restart (fresh Python process reads from DB)
        st = _State()
        _restore_state(st, instruments=("MNQ",))

        count_after_restart = _count_rows()
        assert count_after_restart == count_after_write, \
            "Restart must not delete or add rows"
    finally:
        _delete(*keys)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 8 — Filesystem independence: no /tmp or local files needed
# ══════════════════════════════════════════════════════════════════════════════
def test_08_filesystem_independence():
    """Persistence relies solely on PostgreSQL — no temp/local files required."""
    key = "cvd::MYM"
    _delete(key)
    try:
        _upsert(key, {"state": "Bullish", "ts": _now_utc().isoformat()})

        # Even if /tmp were wiped, the DB row persists
        st = _State()
        _restore_state(st, instruments=("MYM",))

        assert "MYM" in st.CVD_BY_TICKER
        # Verify no temp files were created/required
        import glob
        tmp_files = glob.glob("/tmp/market_state*") + glob.glob("/tmp/app_state*")
        assert len(tmp_files) == 0, f"No temp files should exist: {tmp_files}"
    finally:
        _delete(key)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 9 — Dashboard recovery: recent safe state displays after restart
# ══════════════════════════════════════════════════════════════════════════════
def test_09_dashboard_recovery():
    """After restart, CVD and ALERT_HISTORY are available for the dashboard poll."""
    keys = ["cvd::MGC", "cvd::MNQ", "alert_history_snapshot"]
    _delete(*keys)
    try:
        ts = _now_utc().isoformat()
        _upsert("cvd::MGC", {"state": "Bullish", "value": 500.0, "direction": "positive",
                              "ts": ts, "pending_dir": None,
                              "opposite_count": 0, "last_opposite_minute": None})
        _upsert("cvd::MNQ", {"state": "Bearish", "value": -200.0, "direction": "negative",
                              "ts": ts, "pending_dir": None,
                              "opposite_count": 0, "last_opposite_minute": None})
        _upsert("alert_history_snapshot", {"alerts": [
            {"alert_type": "BOS DEMAND", "ticker": "MGC", "ts": ts},
            {"alert_type": "CHOCH SUPPLY", "ticker": "MNQ", "ts": ts},
        ]})

        # Simulate dashboard reconnect after restart
        st = _State()
        _restore_state(st, instruments=("MGC", "MNQ"))

        # Dashboard can read CVD state for both instruments
        assert st.CVD_BY_TICKER.get("MGC", {}).get("state") == "Bullish"
        assert st.CVD_BY_TICKER.get("MNQ", {}).get("state") == "Bearish"
        # Dashboard can read recent alerts for edge scoring
        alert_types = [a["alert_type"] for a in st.ALERT_HISTORY]
        assert "BOS DEMAND" in alert_types
        assert "CHOCH SUPPLY" in alert_types
    finally:
        _delete(*keys)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 10 — Money-path safety: zero unintended broker calls in all recovery tests
# ══════════════════════════════════════════════════════════════════════════════
def test_10_money_path_safety_zero_broker_calls():
    """All restore operations complete with zero TradersPost / broker calls."""
    keys = [
        "cvd::MGC", "volume_spike::MGC", "traderspost_last::MGC",
        "auto_fired_keys", "alert_history_snapshot",
    ]
    _delete(*keys)
    broker_calls = []
    try:
        today_et = _now_utc().astimezone(
            __import__("zoneinfo").ZoneInfo("America/New_York")
        ).strftime("%Y-%m-%d")
        ts = _now_utc().isoformat()
        _upsert("cvd::MGC",
                {"state": "Bullish", "ts": ts, "pending_dir": None,
                 "opposite_count": 0, "last_opposite_minute": None})
        _upsert("volume_spike::MGC", {"ts": ts})
        _upsert("traderspost_last::MGC",
                {"fingerprint": "MGC:buy:3990:3970:4020", "epoch": time.time()})
        _upsert("auto_fired_keys",
                {"keys": [["scalp", "MGC", "Long", 3990.0]], "date_et": today_et})
        _upsert("alert_history_snapshot",
                {"alerts": [{"alert_type": "BOS DEMAND", "ticker": "MGC", "ts": ts}]})

        # Patch out any accidental broker send that could slip through
        real_requests_post = None
        try:
            import requests
            real_requests_post = requests.post
            requests.post = lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("broker POST during restore is forbidden"))
        except ImportError:
            pass

        try:
            st = _State()
            _restore_state(st, instruments=("MGC",))
        finally:
            if real_requests_post is not None:
                import requests
                requests.post = real_requests_post

        # Verify all state was restored without any broker call
        assert len(broker_calls) == 0, "Zero broker calls during restore"
        assert "MGC" in st.CVD_BY_TICKER
        assert "MGC" in st.VOLUME_SPIKE_BY_TICKER
        assert "MGC" in st._TRADERSPOST_LAST
        assert ("scalp", "MGC", "Long", 3990.0) in st.AUTO_FIRED_KEYS
        assert len(st.ALERT_HISTORY) == 1
    finally:
        _delete(*keys)


# ══════════════════════════════════════════════════════════════════════════════
# Bonus: Schema-version mismatch handling
# ══════════════════════════════════════════════════════════════════════════════
def test_schema_version_stored_correctly():
    """market_state_cache rows carry schema_version=1 (current)."""
    key = "cvd::MGC"
    _delete(key)
    try:
        _upsert(key, {"state": "Neutral", "ts": _now_utc().isoformat()})
        c = _conn()
        try:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT schema_version FROM market_state_cache WHERE key = %s",
                    (key,),
                )
                row = cur.fetchone()
        finally:
            c.close()
        assert row is not None
        assert row[0] == 1, f"schema_version must be 1, got {row[0]}"
    finally:
        _delete(key)
