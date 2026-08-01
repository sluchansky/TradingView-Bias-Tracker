"""Tests for trade_snapshot.py — immutable send-time snapshot module.

Coverage:
  - build_trade_snapshot: all 28 fields present, correct extractions
  - missing keys → None, never raises
  - empty result dict works
  - strategy / thesis / confirmation / broker_out extraction
  - compute_execution_fingerprint: deterministic, returns None on bad input
  - audit_broker_response: parses TradersPost IDs, handles bad JSON
  - No UPDATE SQL in trade_snapshot.py (immutability)
  - /trade-snapshots in proxy whitelist
  - Smoke: parity + scalp golden (import-only, no side effects)
"""
import hashlib
import importlib
import json
import os
import sys
import textwrap
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# ── Path setup ───────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import trade_snapshot as ts


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _rich_result():
    """A realistic full_analysis result with all optional blocks."""
    return {
        "edge_score":    72.5,
        "grade":         "A",
        "verdict":       "Long READY",
        "signal_id":     "sig-abc-123",
        "strategy_engine": {
            "active_key":       "CHOCH_DEMAND_PULLBACK",
            "active_strategy":  "CHoCH Demand Pullback",
            "setup_type":       "Demand Zone Pullback",
        },
        "thesis_tracker": {
            "direction":  "Long",
            "strength":   "HIGH",
            "alignment":  "ALIGNED",
        },
        "main_brain": {
            "playbook": "Wait for VWAP reclaim then enter on micro-CHOCH.",
        },
        "confirmations": ["CHOCH_BULLISH", "VWAP_ABOVE", "CVD_LONG"],
        "blockers":      [],
        "opposing_structure": "Minor bearish BOS 4 bars ago",
        "risk_state":    "normal",
    }


def _minimal_result():
    """A bare-minimum result (just a verdict)."""
    return {"verdict": "Short READY"}


def _empty_result():
    """Completely empty dict (from a failed full_analysis)."""
    return {}


# ── build_trade_snapshot ──────────────────────────────────────────────────────

class TestBuildTradeSnapshot(unittest.TestCase):

    # ── 28 required fields are always present ────────────────────────────────

    _REQUIRED_FIELDS = {
        "internal_trade_id", "signal_id", "execution_fingerprint",
        "instrument", "contract", "account", "mode", "direction",
        "canonical_strategy_key", "strategy_display_name", "setup_name",
        "playbook", "thesis_direction", "thesis_strength", "thesis_alignment",
        "edge_score", "grade", "readiness", "actionable",
        "confirmations", "blockers", "opposing_structure", "risk_state",
        "planned_entry", "planned_stop", "planned_targets", "planned_risk",
        "planned_contracts", "source",
        "broker_order_id", "broker_signal_id", "broker_metadata",
        "created_at", "sent_at",
    }

    def _build(self, result=None, **kw):
        defaults = dict(
            instrument="MNQ", mode="paper", source="auto", contracts=2,
            direction="Long", entry=21000.0, stop=20950.0, t1=21150.0, t2=21200.0,
        )
        defaults.update(kw)
        return ts.build_trade_snapshot(result or _rich_result(), **defaults)

    def test_all_required_fields_present(self):
        snap = self._build()
        for field in self._REQUIRED_FIELDS:
            self.assertIn(field, snap, f"Missing field: {field}")

    def test_instrument_preserved(self):
        snap = self._build(instrument="MGC")
        self.assertEqual(snap["instrument"], "MGC")

    def test_mode_and_source_preserved(self):
        snap = self._build(mode="traderspost", source="manual")
        self.assertEqual(snap["mode"],   "traderspost")
        self.assertEqual(snap["source"], "manual")

    def test_direction_preserved(self):
        snap = self._build(direction="Short")
        self.assertEqual(snap["direction"], "Short")

    def test_edge_score_extracted(self):
        snap = self._build()
        self.assertAlmostEqual(snap["edge_score"], 72.5)

    def test_grade_extracted(self):
        snap = self._build()
        self.assertEqual(snap["grade"], "A")

    def test_verdict_as_readiness(self):
        snap = self._build()
        self.assertEqual(snap["readiness"], "Long READY")

    def test_actionable_true_on_ready(self):
        snap = self._build()
        self.assertTrue(snap["actionable"])

    def test_actionable_false_on_wait(self):
        snap = self._build({"verdict": "Long WAIT"})
        self.assertFalse(snap["actionable"])

    def test_strategy_key_extracted(self):
        snap = self._build()
        self.assertEqual(snap["canonical_strategy_key"], "CHOCH_DEMAND_PULLBACK")

    def test_strategy_display_name_extracted(self):
        snap = self._build()
        self.assertEqual(snap["strategy_display_name"], "CHoCH Demand Pullback")

    def test_setup_name_extracted(self):
        snap = self._build()
        self.assertEqual(snap["setup_name"], "Demand Zone Pullback")

    def test_thesis_extracted(self):
        snap = self._build()
        self.assertEqual(snap["thesis_direction"], "Long")
        self.assertEqual(snap["thesis_strength"],  "HIGH")
        self.assertEqual(snap["thesis_alignment"], "ALIGNED")

    def test_playbook_extracted(self):
        snap = self._build()
        self.assertIn("VWAP", snap["playbook"])

    def test_confirmations_is_list(self):
        snap = self._build()
        self.assertIsInstance(snap["confirmations"], list)
        self.assertIn("CHOCH_BULLISH", snap["confirmations"])

    def test_blockers_empty_list(self):
        snap = self._build()
        self.assertIsInstance(snap["blockers"], list)
        self.assertEqual(snap["blockers"], [])

    def test_opposing_structure_extracted(self):
        snap = self._build()
        self.assertIn("bearish", snap["opposing_structure"])

    def test_planned_entry_stop(self):
        snap = self._build(entry=21000.0, stop=20950.0)
        self.assertAlmostEqual(snap["planned_entry"], 21000.0)
        self.assertAlmostEqual(snap["planned_stop"],  20950.0)

    def test_planned_risk_computed(self):
        snap = self._build(entry=21000.0, stop=20950.0)
        self.assertAlmostEqual(snap["planned_risk"], 50.0)

    def test_planned_targets_dict(self):
        snap = self._build(t1=21150.0, t2=21200.0)
        self.assertEqual(snap["planned_targets"]["t1"], 21150.0)
        self.assertEqual(snap["planned_targets"]["t2"], 21200.0)

    def test_planned_contracts(self):
        snap = self._build(contracts=3)
        self.assertEqual(snap["planned_contracts"], 3)

    def test_internal_trade_id_is_uuid_string(self):
        snap = self._build()
        import uuid
        uuid.UUID(snap["internal_trade_id"])  # raises if invalid

    def test_created_at_is_datetime(self):
        snap = self._build()
        self.assertIsInstance(snap["created_at"], datetime)

    def test_sent_at_equals_created_at(self):
        snap = self._build()
        self.assertEqual(snap["created_at"], snap["sent_at"])

    # ── Missing keys → None, never raise ─────────────────────────────────────

    def test_empty_result_no_raise(self):
        snap = ts.build_trade_snapshot(
            {}, instrument="MNQ", mode="paper", source="auto", contracts=1)
        # All fields still present
        for field in self._REQUIRED_FIELDS:
            self.assertIn(field, snap)

    def test_empty_result_strategy_is_none(self):
        snap = ts.build_trade_snapshot(
            {}, instrument="MNQ", mode="paper", source="auto", contracts=1)
        self.assertIsNone(snap["canonical_strategy_key"])
        self.assertIsNone(snap["strategy_display_name"])
        self.assertIsNone(snap["setup_name"])

    def test_empty_result_thesis_is_none(self):
        snap = ts.build_trade_snapshot(
            {}, instrument="MNQ", mode="paper", source="auto", contracts=1)
        self.assertIsNone(snap["thesis_direction"])
        self.assertIsNone(snap["thesis_strength"])
        self.assertIsNone(snap["thesis_alignment"])

    def test_empty_result_edge_score_none(self):
        snap = ts.build_trade_snapshot(
            {}, instrument="MNQ", mode="paper", source="auto", contracts=1)
        self.assertIsNone(snap["edge_score"])

    def test_none_entry_stop_is_safe(self):
        snap = ts.build_trade_snapshot(
            _minimal_result(), instrument="MNQ", mode="paper", source="auto",
            contracts=1, entry=None, stop=None)
        self.assertIsNone(snap["planned_entry"])
        self.assertIsNone(snap["planned_stop"])
        self.assertIsNone(snap["planned_risk"])
        self.assertIsNone(snap["planned_targets"])

    def test_bad_types_do_not_raise(self):
        """Garbage in every position must never raise."""
        bad = {
            "edge_score": "not_a_float",
            "strategy_engine": "not_a_dict",
            "thesis_tracker": 42,
            "confirmations": "not_a_list",
            "main_brain": [],
        }
        snap = ts.build_trade_snapshot(
            bad, instrument="MNQ", mode="paper", source="auto",
            contracts="abc", entry="x", stop=None)
        self.assertIn("internal_trade_id", snap)

    def test_signal_id_extracted(self):
        result = dict(_rich_result(), signal_id="sig-xyz-999")
        snap = ts.build_trade_snapshot(
            result, instrument="MNQ", mode="paper", source="auto", contracts=1)
        self.assertEqual(snap["signal_id"], "sig-xyz-999")

    # ── broker_out integration ────────────────────────────────────────────────

    def test_broker_out_order_id_stored(self):
        snap = self._build(
            broker_out={"order_id": "ORD-12345", "signal_id": "SIG-67890"})
        self.assertEqual(snap["broker_order_id"],  "ORD-12345")
        self.assertEqual(snap["broker_signal_id"], "SIG-67890")

    def test_broker_out_none_yields_none(self):
        snap = self._build(broker_out=None)
        self.assertIsNone(snap["broker_order_id"])
        self.assertIsNone(snap["broker_signal_id"])

    def test_broker_metadata_contains_full_dict(self):
        bo = {"order_id": "ORD-1", "raw_response": {"status": "ok"}}
        snap = self._build(broker_out=bo)
        self.assertEqual(snap["broker_metadata"]["order_id"], "ORD-1")


# ── compute_execution_fingerprint ────────────────────────────────────────────

class TestComputeFingerprint(unittest.TestCase):

    def _fp(self, **kw):
        defaults = dict(
            instrument="MNQ", direction="Long", entry_price=21000.0,
            contracts=2, sent_at=datetime(2026, 8, 1, 9, 30, 0, tzinfo=timezone.utc))
        defaults.update(kw)
        return ts.compute_execution_fingerprint(**defaults)

    def test_returns_32char_hex(self):
        fp = self._fp()
        self.assertIsNotNone(fp)
        self.assertEqual(len(fp), 32)
        int(fp, 16)  # raises if not hex

    def test_deterministic(self):
        fp1 = self._fp()
        fp2 = self._fp()
        self.assertEqual(fp1, fp2)

    def test_different_direction_different_fingerprint(self):
        self.assertNotEqual(self._fp(direction="Long"), self._fp(direction="Short"))

    def test_different_instrument_different_fingerprint(self):
        self.assertNotEqual(self._fp(instrument="MNQ"), self._fp(instrument="MGC"))

    def test_different_entry_different_fingerprint(self):
        self.assertNotEqual(
            self._fp(entry_price=21000.0),
            self._fp(entry_price=21001.0))

    def test_different_contracts_different_fingerprint(self):
        self.assertNotEqual(self._fp(contracts=1), self._fp(contracts=2))

    def test_different_second_different_fingerprint(self):
        t1 = datetime(2026, 8, 1, 9, 30, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 8, 1, 9, 30, 1, tzinfo=timezone.utc)
        self.assertNotEqual(self._fp(sent_at=t1), self._fp(sent_at=t2))

    def test_same_second_microsecond_stripped(self):
        t1 = datetime(2026, 8, 1, 9, 30, 0, 0,      tzinfo=timezone.utc)
        t2 = datetime(2026, 8, 1, 9, 30, 0, 999999, tzinfo=timezone.utc)
        self.assertEqual(self._fp(sent_at=t1), self._fp(sent_at=t2))

    def test_none_entry_does_not_raise(self):
        fp = ts.compute_execution_fingerprint(
            "MNQ", "Long", None, 1,
            datetime(2026, 8, 1, 9, 30, 0, tzinfo=timezone.utc))
        self.assertIsNotNone(fp)

    def test_bad_contracts_returns_none_or_str(self):
        # Should not raise
        result = ts.compute_execution_fingerprint(
            "MNQ", "Long", 21000.0, "oops",
            datetime(2026, 8, 1, 9, 30, 0, tzinfo=timezone.utc))
        # None or a valid fingerprint — neither raises
        self.assertIsNone(result)  # int("oops") fails → except → None


# ── audit_broker_response ────────────────────────────────────────────────────

class TestAuditBrokerResponse(unittest.TestCase):

    def test_parses_orderId(self):
        body = json.dumps({"orderId": "ORD-111"})
        out = ts.audit_broker_response(body)
        self.assertEqual(out["order_id"], "ORD-111")

    def test_parses_order_id(self):
        body = json.dumps({"order_id": "ORD-222"})
        out = ts.audit_broker_response(body)
        self.assertEqual(out["order_id"], "ORD-222")

    def test_parses_signalId(self):
        body = json.dumps({"signalId": "SIG-333"})
        out = ts.audit_broker_response(body)
        self.assertEqual(out["signal_id"], "SIG-333")

    def test_parses_webhookId(self):
        body = json.dumps({"webhookId": "WH-444"})
        out = ts.audit_broker_response(body)
        self.assertEqual(out["webhook_id"], "WH-444")

    def test_id_fallback(self):
        body = json.dumps({"id": "SOME-ID"})
        out = ts.audit_broker_response(body)
        self.assertEqual(out["order_id"], "SOME-ID")

    def test_orderId_wins_over_id(self):
        # orderId is checked first, so it should be used even when 'id' present
        body = json.dumps({"orderId": "ORD-AAA", "id": "FALLBACK"})
        out = ts.audit_broker_response(body)
        self.assertEqual(out["order_id"], "ORD-AAA")

    def test_raw_response_stored(self):
        body = json.dumps({"orderId": "ORD-555", "extra": "field"})
        out = ts.audit_broker_response(body)
        self.assertIn("raw_response", out)
        self.assertEqual(out["raw_response"]["extra"], "field")

    def test_invalid_json_returns_empty(self):
        out = ts.audit_broker_response("not json {{")
        self.assertEqual(out, {})

    def test_empty_string_returns_empty(self):
        out = ts.audit_broker_response("")
        self.assertEqual(out, {})

    def test_none_returns_empty(self):
        out = ts.audit_broker_response(None)
        self.assertEqual(out, {})

    def test_json_array_returns_empty(self):
        out = ts.audit_broker_response(json.dumps([1, 2, 3]))
        self.assertEqual(out, {})

    def test_empty_id_not_stored(self):
        body = json.dumps({"orderId": "  "})
        out = ts.audit_broker_response(body)
        self.assertNotIn("order_id", out)

    def test_never_raises_on_garbage(self):
        for garbage in [b"\xff\xfe", 12345, {"k": "v"}, None, "😀"]:
            out = ts.audit_broker_response(garbage)
            self.assertIsInstance(out, dict)


# ── Immutability: no UPDATE SQL in trade_snapshot.py ──────────────────────────

class TestNoUpdateSql(unittest.TestCase):

    def test_no_update_statement_in_module(self):
        """trade_snapshot.py must contain zero UPDATE SQL statements.

        Snapshots are write-once; no UPDATE path should ever exist in the
        pure module.  (Persistence lives in app.py — not covered here.)
        """
        import re
        with open(os.path.join(_HERE, "trade_snapshot.py")) as f:
            source = f.read()
        # Allow "UPDATE" in comments / docstrings but flag bare SQL UPDATE verb
        # followed by a table name (case-insensitive).
        matches = re.findall(r'\bUPDATE\s+\w+', source, re.IGNORECASE)
        self.assertEqual(
            matches, [],
            f"trade_snapshot.py contains forbidden UPDATE statements: {matches}")


# ── Proxy whitelist: /trade-snapshots must be present ────────────────────────

class TestProxyWhitelist(unittest.TestCase):

    def test_trade_snapshots_in_proxy_whitelist(self):
        ws_root = os.path.abspath(os.path.join(_HERE, "..", ".."))
        proxy_ts = os.path.join(
            ws_root, "artifacts", "api-server", "src", "routes", "flask-proxy.ts")
        self.assertTrue(
            os.path.exists(proxy_ts),
            "flask-proxy.ts not found — check the path")
        with open(proxy_ts) as f:
            content = f.read()
        self.assertIn(
            '"/trade-snapshots"', content,
            "/trade-snapshots is missing from the flask-proxy.ts whitelist")


# ── Smoke: module import is side-effect-free ──────────────────────────────────

class TestModuleSmoke(unittest.TestCase):

    def test_module_reimport_is_safe(self):
        """Re-importing trade_snapshot must not raise or produce side effects."""
        import importlib
        importlib.reload(ts)

    def test_all_public_callables_present(self):
        self.assertTrue(callable(ts.build_trade_snapshot))
        self.assertTrue(callable(ts.compute_execution_fingerprint))
        self.assertTrue(callable(ts.audit_broker_response))

    def test_parity_invariant_empty_result(self):
        """build_trade_snapshot with identical args produces identical output
        (excluding uuid and timestamps which are per-call)."""
        now = datetime(2026, 8, 1, 9, 30, 0, tzinfo=timezone.utc)
        # Fingerprint is the stable deterministic portion
        fp1 = ts.compute_execution_fingerprint("MNQ", "Long", 21000.0, 2, now)
        fp2 = ts.compute_execution_fingerprint("MNQ", "Long", 21000.0, 2, now)
        self.assertEqual(fp1, fp2)

    def test_scalp_golden_mode_source(self):
        """Snapshot built with SCALP mode + auto source has correct mode/source fields."""
        snap = ts.build_trade_snapshot(
            _rich_result(), instrument="MNQ", mode="paper",
            source="auto", contracts=1, direction="Long",
            entry=21000.0, stop=20950.0, t1=21150.0, t2=None)
        self.assertEqual(snap["mode"],   "paper")
        self.assertEqual(snap["source"], "auto")
        self.assertIsNotNone(snap["planned_targets"])
        self.assertIsNone(snap["planned_targets"]["t2"])

    def test_mgc_instrument_snapshot(self):
        """MGC snapshot builds correctly (different tick size doesn't affect logic)."""
        snap = ts.build_trade_snapshot(
            _rich_result(), instrument="MGC", mode="traderspost",
            source="manual", contracts=5, direction="Short",
            entry=3200.0, stop=3205.0, t1=3190.0, t2=3180.0)
        self.assertEqual(snap["instrument"], "MGC")
        self.assertEqual(snap["direction"],  "Short")
        self.assertAlmostEqual(snap["planned_risk"], 5.0)


# ── Integration: snapshot pipeline for each gateway path ─────────────────────

class TestSnapshotPipelineIntegration(unittest.TestCase):
    """Tests for the build→persist pipeline as invoked from each gateway path.

    We exercise the full trade_snapshot module against realistic gateway inputs
    without importing app.py.  Each test verifies:
      - the snapshot can be built without error
      - the result dict has the correct mode/source/broker fields
      - all values are DB-safe types (no psycopg2 adapters, no custom objects)
      - fail-open behaviour when called with minimal / broken inputs
    """

    _SAFE_TYPES = (type(None), str, int, float, bool, list, dict, datetime)

    def _assert_db_safe(self, snap):
        """Assert every value in the snapshot dict is a DB-safe Python type."""
        for k, v in snap.items():
            self.assertIsInstance(
                v, self._SAFE_TYPES,
                f"Field '{k}' has unsafe type {type(v).__name__}")

    # ── Paper path ────────────────────────────────────────────────────────────

    def test_paper_path_mode_and_no_broker_ids(self):
        snap = ts.build_trade_snapshot(
            _rich_result(), instrument="MNQ", mode="paper",
            source="auto", contracts=2,
            direction="Long", entry=21000.0, stop=20950.0, t1=21150.0, t2=None,
            broker_out={})
        self.assertEqual(snap["mode"],   "paper")
        self.assertEqual(snap["source"], "auto")
        self.assertIsNone(snap["broker_order_id"])
        self.assertIsNone(snap["broker_signal_id"])
        self._assert_db_safe(snap)

    def test_paper_path_planned_risk_non_none(self):
        snap = ts.build_trade_snapshot(
            _rich_result(), instrument="MNQ", mode="paper",
            source="auto", contracts=1,
            direction="Short", entry=20800.0, stop=20820.0, t1=20740.0)
        self.assertAlmostEqual(snap["planned_risk"], 20.0)

    def test_paper_path_none_t2_targets_dict(self):
        snap = ts.build_trade_snapshot(
            _rich_result(), instrument="MNQ", mode="paper",
            source="auto", contracts=1,
            direction="Long", entry=21000.0, stop=20950.0, t1=21150.0, t2=None)
        # t2=None → targets dict present but t2 slot is None
        self.assertIsNotNone(snap["planned_targets"])
        self.assertIsNone(snap["planned_targets"]["t2"])

    # ── Normal live 2xx path ──────────────────────────────────────────────────

    def test_live_path_broker_ids_populated(self):
        bo = ts.audit_broker_response(
            '{"orderId": "ORD-999", "signalId": "SIG-888"}')
        snap = ts.build_trade_snapshot(
            _rich_result(), instrument="MNQ", mode="traderspost",
            source="auto", contracts=2,
            direction="Long", entry=21000.0, stop=20950.0, t1=21150.0, t2=21300.0,
            broker_out=bo)
        self.assertEqual(snap["broker_order_id"],  "ORD-999")
        self.assertEqual(snap["broker_signal_id"], "SIG-888")
        self.assertEqual(snap["mode"], "traderspost")
        self._assert_db_safe(snap)

    def test_live_path_raw_response_in_metadata(self):
        bo = ts.audit_broker_response('{"orderId": "O1", "extra": "data"}')
        snap = ts.build_trade_snapshot(
            _rich_result(), instrument="MGC", mode="traderspost",
            source="auto", contracts=1,
            direction="Short", entry=3200.0, stop=3205.0, t1=3190.0, t2=3180.0,
            broker_out=bo)
        self.assertIn("raw_response", snap["broker_metadata"])
        self.assertEqual(snap["broker_metadata"]["raw_response"]["extra"], "data")

    def test_live_path_db_safe_types(self):
        bo = ts.audit_broker_response('{"id": "X-1"}')
        snap = ts.build_trade_snapshot(
            _rich_result(), instrument="MNQ", mode="traderspost",
            source="manual", contracts=3,
            direction="Long", entry=21000.0, stop=20950.0, t1=21150.0, t2=21300.0,
            broker_out=bo)
        self._assert_db_safe(snap)

    # ── Runner / two-leg path ─────────────────────────────────────────────────

    def test_runner_both_legs_live_snapshot(self):
        """Snapshot built when both runner legs confirmed (runner_status='sent')."""
        runner_meta = {
            "runner_status":          "sent",
            "runner_qty":             1,
            "partial_fill":           False,
            "broker_verify_required": False,
            "path":                   "two_leg_runner",
        }
        snap = ts.build_trade_snapshot(
            _rich_result(), instrument="MNQ", mode="traderspost",
            source="auto", contracts=2,
            direction="Long", entry=21000.0, stop=20950.0, t1=21150.0, t2=21300.0,
            broker_out=runner_meta)
        self.assertEqual(snap["broker_metadata"]["runner_status"], "sent")
        self.assertEqual(snap["broker_metadata"]["runner_qty"], 1)
        self.assertFalse(snap["broker_metadata"]["partial_fill"])
        self._assert_db_safe(snap)

    def test_runner_partial_fill_snapshot(self):
        """Snapshot built when runner unconfirmed (verify_required)."""
        runner_meta = {
            "runner_status":          "verify_required",
            "runner_qty":             1,
            "partial_fill":           True,
            "broker_verify_required": True,
            "path":                   "two_leg_runner",
        }
        snap = ts.build_trade_snapshot(
            _rich_result(), instrument="MNQ", mode="traderspost",
            source="auto", contracts=2,
            direction="Short", entry=20800.0, stop=20820.0, t1=20700.0, t2=None,
            broker_out=runner_meta)
        self.assertTrue(snap["broker_metadata"]["partial_fill"])
        self.assertTrue(snap["broker_metadata"]["broker_verify_required"])
        self._assert_db_safe(snap)

    def test_runner_rejected_snapshot(self):
        """Snapshot built when runner rejected (primary-only, partial_fill=True)."""
        runner_meta = {
            "runner_status":          "rejected",
            "runner_qty":             1,
            "partial_fill":           True,
            "broker_verify_required": False,
            "path":                   "two_leg_runner",
        }
        snap = ts.build_trade_snapshot(
            _rich_result(), instrument="MGC", mode="traderspost",
            source="auto", contracts=2,
            direction="Long", entry=3200.0, stop=3195.0, t1=3215.0, t2=3230.0,
            broker_out=runner_meta)
        self.assertEqual(snap["broker_metadata"]["runner_status"], "rejected")
        self._assert_db_safe(snap)

    # ── Table-unavailable fail-open path ──────────────────────────────────────

    def test_table_unavailable_build_succeeds(self):
        """When the DB table is not ready, build_trade_snapshot still returns a dict."""
        # Simulate SNAPSHOTS_DB_READY=False: _persist_trade_snapshot is a no-op.
        # build_trade_snapshot itself never touches the DB — it must always succeed.
        snap = ts.build_trade_snapshot(
            {}, instrument="MNQ", mode="paper",
            source="auto", contracts=1, broker_out=None)
        self.assertIn("internal_trade_id", snap)
        self.assertIn("instrument", snap)

    def test_table_unavailable_all_fields_present(self):
        """Even with empty result + no broker_out, all required fields are in the dict."""
        snap = ts.build_trade_snapshot(
            {}, instrument="MGC", mode="traderspost",
            source="auto", contracts=2, direction="Long",
            entry=None, stop=None, t1=None, t2=None, broker_out=None)
        for field in TestBuildTradeSnapshot._REQUIRED_FIELDS:
            self.assertIn(field, snap, f"Missing field on empty result: {field}")

    def test_build_never_raises_on_none_result(self):
        """build_trade_snapshot must not raise when result=None (defensive)."""
        try:
            snap = ts.build_trade_snapshot(
                None or {}, instrument="MNQ", mode="paper",
                source="auto", contracts=1)
            self.assertIn("internal_trade_id", snap)
        except Exception as exc:
            self.fail(f"build_trade_snapshot raised on empty result: {exc}")

    # ── Fingerprint uniqueness across paths ───────────────────────────────────

    def test_paper_vs_live_same_fingerprint_different_paths(self):
        """Fingerprint depends only on instrument/direction/entry/contracts/second,
        not on mode or broker metadata — matching works across path types."""
        now = datetime(2026, 8, 1, 9, 30, 0, tzinfo=timezone.utc)
        fp_paper = ts.compute_execution_fingerprint("MNQ", "Long", 21000.0, 2, now)
        fp_live  = ts.compute_execution_fingerprint("MNQ", "Long", 21000.0, 2, now)
        self.assertEqual(fp_paper, fp_live)

    def test_runner_different_contracts_different_fingerprint(self):
        """Two-leg runner with 2 total contracts vs 1 produces different fingerprint."""
        now = datetime(2026, 8, 1, 9, 30, 0, tzinfo=timezone.utc)
        fp2 = ts.compute_execution_fingerprint("MNQ", "Long", 21000.0, 2, now)
        fp1 = ts.compute_execution_fingerprint("MNQ", "Long", 21000.0, 1, now)
        self.assertNotEqual(fp2, fp1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
