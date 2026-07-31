"""test_timeline_formatter.py — Timeline event detail formatter tests.

Python port of the TypeScript fmtEventDetail() function in MainBrain.tsx,
covering Cases A–J from the spec. Tests both the formatter logic and
the THESIS_TRANSITION display field extraction.
"""

import json
import unittest


# ── Python port of fmtEventDetail (mirrors MainBrain.tsx) ────────────────────

def fmt_event_detail(v):
    """Python port of fmtEventDetail() from MainBrain.tsx.

    Converts any timeline `details` value to a human-readable string.
    Never returns '[object Object]'.
    """
    if v is None:
        return ''
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float, bool)):
        return str(v).lower() if isinstance(v, bool) else str(v)
    if isinstance(v, list):
        parts = []
        for x in v:
            if x is None:
                continue
            if isinstance(x, dict):
                parts.append(fmt_event_detail(x))
            else:
                parts.append(str(x))
        return ' · '.join(p for p in parts if p)
    if isinstance(v, dict):
        # Preferred semantic fields — highest priority first
        if isinstance(v.get('summary'),     str) and v['summary']:     return v['summary']
        if isinstance(v.get('message'),     str) and v['message']:     return v['message']
        if isinstance(v.get('reason'),      str) and v['reason']:      return v['reason']
        if isinstance(v.get('description'), str) and v['description']: return v['description']
        if isinstance(v.get('label'),       str) and v['label']:       return v['label']
        # Transition-style
        if v.get('from') is not None and v.get('to') is not None:
            return f"{v['from']} → {v['to']}"
        if v.get('prev_status') is not None and v.get('new_status') is not None:
            return f"{v['prev_status']} → {v['new_status']}"
        if v.get('previous_state') is not None and v.get('new_state') is not None:
            return f"{v['previous_state']} → {v['new_state']}"
        # Single-field scalar extractions
        if isinstance(v.get('direction'),  str) and v['direction']:  return v['direction']
        if isinstance(v.get('status'),     str) and v['status']:     return v['status']
        if isinstance(v.get('event_type'), str) and v['event_type']: return v['event_type']
        # Safe compact JSON fallback — scalars only, max 6 keys, truncated
        try:
            keys = [k for k in v if v[k] is not None
                    and not isinstance(v[k], (dict, list))][:6]
            if not keys:
                return ''
            subset = {k: v[k] for k in keys}
            s = json.dumps(subset)
            return s[:120] + '…' if len(s) > 120 else s
        except Exception:
            return '[complex object]'
    return str(v)


def safe_fallback_json(v, max_depth=1, max_len=120):
    """Safe compact JSON fallback that catches circular references and limits depth."""
    class _Limiter:
        def __init__(self, depth):
            self.depth = depth
        def __call__(self, obj):
            if self.depth <= 0:
                return '[...]'
            if isinstance(obj, dict):
                self.depth -= 1
                result = {k: self(v_) for k, v_ in list(obj.items())[:6]
                          if not isinstance(v_, (dict, list)) or self.depth > 0}
                self.depth += 1
                return result
            return obj
    try:
        limited = _Limiter(max_depth)(v)
        s = json.dumps(limited)
        return (s[:max_len] + '…') if len(s) > max_len else s
    except Exception:
        return '[complex object]'


def extract_thesis_transition_fields(details):
    """Extract display fields for a THESIS_TRANSITION detail dict.
    Returns dict of {label: value} for non-null fields only.
    Mirrors ThesisTransitionDetail in MainBrain.tsx.
    """
    if not isinstance(details, dict):
        return {}
    result = {}
    if details.get('prev_status') is not None:
        result['Previous'] = str(details['prev_status'])
    if details.get('direction') is not None:
        result['Direction'] = str(details['direction'])
    if details.get('primary_reason') is not None:
        result['Reason'] = str(details['primary_reason'])
    if details.get('new_confidence') is not None:
        if details.get('prev_confidence') is not None:
            result['Confidence'] = f"{details['prev_confidence']} → {details['new_confidence']}"
        else:
            result['Confidence'] = str(details['new_confidence'])
    return result


# ── Deduplication (mirrors TimelinePanel dedup logic) ────────────────────────

def dedup_events(events):
    """Dedup by (event_type, timestamp, event_label) fingerprint."""
    seen = set()
    result = []
    for e in events:
        fp = f"{e.get('event_type')}::{e.get('timestamp')}::{e.get('event_label')}"
        if fp not in seen:
            seen.add(fp)
            result.append(e)
    return result


# ═══════════════════════════════════════════════════════════════════════════════

class TestCaseA_StringDetail(unittest.TestCase):
    """Case A — detail is a string: renders unchanged."""

    def test_a1_simple_string(self):
        self.assertEqual(fmt_event_detail("FORMING_LONG"), "FORMING_LONG")

    def test_a2_string_with_spaces(self):
        self.assertEqual(fmt_event_detail("Bullish structure confirmed"), "Bullish structure confirmed")

    def test_a3_empty_string(self):
        self.assertEqual(fmt_event_detail(""), "")

    def test_a4_string_with_special_chars(self):
        s = "Direction: LONG → READY"
        self.assertEqual(fmt_event_detail(s), s)


class TestCaseB_TransitionObject(unittest.TestCase):
    """Case B — detail is {from: ..., to: ...}: readable transition text."""

    def test_b1_from_to(self):
        result = fmt_event_detail({"from": "NEUTRAL", "to": "FORMING_LONG"})
        self.assertIn("NEUTRAL", result)
        self.assertIn("FORMING_LONG", result)
        self.assertIn("→", result)

    def test_b2_prev_status_new_status(self):
        result = fmt_event_detail({"prev_status": "NEUTRAL", "new_status": "FORMING_LONG"})
        self.assertEqual(result, "NEUTRAL → FORMING_LONG")

    def test_b3_previous_state_new_state(self):
        result = fmt_event_detail({"previous_state": "WAIT", "new_state": "READY_LONG"})
        self.assertEqual(result, "WAIT → READY_LONG")

    def test_b4_thesis_transition_full_detail(self):
        """Real THESIS_TRANSITION detail shape from _mb_decision_timeline."""
        detail = {
            "direction":           "LONG",
            "prev_status":         "NEUTRAL",
            "new_status":          "FORMING_LONG",
            "prev_confidence":     42,
            "new_confidence":      68,
            "primary_reason":      "Bullish structure developing",
            "invalidation_reason": None,
        }
        result = fmt_event_detail(detail)
        # prev_status and new_status are present — should use transition pattern
        self.assertIn("NEUTRAL", result)
        self.assertIn("FORMING_LONG", result)
        self.assertNotIn("[object Object]", result)
        self.assertNotIn("[object", result)


class TestCaseC_StateAndDirectionObject(unittest.TestCase):
    """Case C — detail is {state: ..., direction: ...}: readable state/direction."""

    def test_c1_state_direction(self):
        result = fmt_event_detail({"state": "FORMING_LONG", "direction": "LONG"})
        # state is not in priority list; direction is extracted
        self.assertNotIn("[object Object]", result)
        self.assertTrue(len(result) > 0)

    def test_c2_direction_only(self):
        result = fmt_event_detail({"direction": "BULLISH"})
        self.assertEqual(result, "BULLISH")

    def test_c3_status_only(self):
        result = fmt_event_detail({"status": "READY_LONG"})
        self.assertEqual(result, "READY_LONG")

    def test_c4_structure_event_detail(self):
        """Real STRUCTURE_EVENT detail shape."""
        detail = {
            "direction":           "BULLISH",
            "alert_type":          "BOS DEMAND",
            "instrument":          "MNQ",
            "candidate_direction": "LONG",
        }
        result = fmt_event_detail(detail)
        # direction is the single-field fallback
        self.assertEqual(result, "BULLISH")
        self.assertNotIn("[object Object]", result)


class TestCaseD_MessageField(unittest.TestCase):
    """Case D — detail contains message: message is preferred."""

    def test_d1_message_preferred_over_other_fields(self):
        result = fmt_event_detail({
            "message": "Order sent successfully",
            "direction": "LONG",
            "status": "OK",
        })
        self.assertEqual(result, "Order sent successfully")

    def test_d2_summary_preferred_over_message(self):
        result = fmt_event_detail({
            "summary": "Summary text",
            "message": "Message text",
        })
        self.assertEqual(result, "Summary text")

    def test_d3_reason_preferred_over_direction(self):
        result = fmt_event_detail({
            "reason": "Bearish thesis invalidated",
            "direction": "SHORT",
        })
        self.assertEqual(result, "Bearish thesis invalidated")

    def test_d4_description_preferred_over_label(self):
        result = fmt_event_detail({
            "description": "Full description",
            "label": "Short label",
        })
        self.assertEqual(result, "Full description")


class TestCaseE_ArrayDetail(unittest.TestCase):
    """Case E — detail is an array: safe readable formatting."""

    def test_e1_string_array(self):
        result = fmt_event_detail(["BULLISH", "FORMING_LONG", "READY"])
        self.assertIn("BULLISH", result)
        self.assertIn("FORMING_LONG", result)
        self.assertNotIn("[object Object]", result)

    def test_e2_array_with_objects(self):
        result = fmt_event_detail([
            "prefix",
            {"direction": "LONG"},
        ])
        self.assertNotIn("[object Object]", result)
        self.assertTrue(len(result) > 0)

    def test_e3_empty_array(self):
        result = fmt_event_detail([])
        self.assertEqual(result, '')

    def test_e4_array_with_none(self):
        result = fmt_event_detail([None, "value", None])
        self.assertEqual(result, "value")

    def test_e5_mixed_scalars(self):
        result = fmt_event_detail([42, True, "text"])
        self.assertNotIn("[object Object]", result)


class TestCaseF_NullDetail(unittest.TestCase):
    """Case F — detail is null/None: no [object Object]."""

    def test_f1_none_returns_empty(self):
        result = fmt_event_detail(None)
        self.assertEqual(result, '')
        self.assertNotIn("[object Object]", result)

    def test_f2_none_is_not_object_object(self):
        result = fmt_event_detail(None)
        self.assertNotEqual(result, '[object Object]')

    def test_f3_empty_dict(self):
        """Empty dict: no displayable fields → safe empty result."""
        result = fmt_event_detail({})
        self.assertNotIn("[object Object]", result)


class TestCaseG_UnknownObjectShape(unittest.TestCase):
    """Case G — unknown object shape: compact JSON fallback."""

    def test_g1_unknown_scalar_fields(self):
        result = fmt_event_detail({"state": "FORMING_LONG", "count": 5})
        # "state" is not in priority list; falls through to JSON fallback
        # (unless 'status' key is present — 'state' is not 'status')
        self.assertNotIn("[object Object]", result)
        self.assertTrue(len(result) > 0)

    def test_g2_fallback_is_valid_json_or_scalar(self):
        """Fallback output is parseable JSON or a simple scalar string."""
        result = fmt_event_detail({"foo": "bar", "baz": 42})
        self.assertNotIn("[object Object]", result)
        # Should be a compact JSON representation
        try:
            parsed = json.loads(result.rstrip('…'))
            self.assertIsInstance(parsed, dict)
        except json.JSONDecodeError:
            # Not JSON — could be a single extracted value, still acceptable
            pass

    def test_g3_gateway_send_detail(self):
        """Real GATEWAY_SEND detail shape."""
        result = fmt_event_detail({"epoch_sent": 1722394800.0})
        self.assertNotIn("[object Object]", result)
        self.assertIn("epoch_sent", result)

    def test_g4_nested_object_falls_back_safely(self):
        """Deeply nested object that has no priority fields."""
        result = fmt_event_detail({"nested": {"deeply": {"key": "value"}}})
        self.assertNotIn("[object Object]", result)


class TestCaseH_CircularObject(unittest.TestCase):
    """Case H — circular object: safe fallback without crashing."""

    def test_h1_circular_does_not_raise(self):
        """Python's json.dumps() raises ValueError on circular refs.
        Our formatter must not propagate the exception."""
        obj = {"a": 1}
        obj["self"] = obj  # circular reference
        # fmt_event_detail skips object values in JSON fallback, so it should
        # only include scalar fields and not raise
        try:
            result = fmt_event_detail(obj)
            self.assertNotIn("[object Object]", result)
            self.assertNotEqual(result, '')  # "a": 1 is scalar
        except Exception as exc:
            self.fail(f"Circular object caused exception: {exc}")

    def test_h2_deep_recursion_does_not_raise(self):
        """Highly nested dicts should not cause a RecursionError."""
        obj = {}
        cur = obj
        for _ in range(50):
            cur["child"] = {}
            cur = cur["child"]
        cur["leaf"] = "value"
        try:
            result = fmt_event_detail(obj)
            self.assertNotIn("[object Object]", result)
        except RecursionError:
            self.fail("Deep object caused RecursionError")

    def test_h3_returns_string_not_raises(self):
        """No matter the input, the formatter returns a string."""
        inputs = [
            {"a": {"b": {"c": {"d": {}}}}},
            [{"x": [{"y": {}}]}],
            {"__proto__": "poisoned"},
        ]
        for inp in inputs:
            result = fmt_event_detail(inp)
            self.assertIsInstance(result, str)
            self.assertNotIn("[object Object]", result)


class TestCaseI_LargeNestedPayload(unittest.TestCase):
    """Case I — large nested payload: output is truncated safely."""

    def test_i1_many_scalar_keys_truncated_to_six(self):
        """Only first 6 scalar keys included in JSON fallback."""
        big = {f"key_{i}": f"value_{i}" for i in range(50)}
        result = fmt_event_detail(big)
        self.assertNotIn("[object Object]", result)
        # At most 6 keys in the fallback
        try:
            parsed = json.loads(result.rstrip('…'))
            self.assertLessEqual(len(parsed), 6)
        except json.JSONDecodeError:
            pass  # possibly ellipsis-truncated

    def test_i2_long_string_value_truncated(self):
        """Output string is at most 120 chars + ellipsis."""
        big = {f"k{i}": "x" * 30 for i in range(10)}
        result = fmt_event_detail(big)
        self.assertLessEqual(len(result), 125)  # 120 + '…' margin
        self.assertNotIn("[object Object]", result)

    def test_i3_large_array_scalars_only(self):
        """Large array of scalars renders without crash."""
        arr = [f"event_{i}" for i in range(100)]
        result = fmt_event_detail(arr)
        self.assertNotIn("[object Object]", result)
        # Array items joined with ·
        self.assertIn("event_0", result)

    def test_i4_nested_object_values_excluded_from_fallback(self):
        """Nested objects are excluded from JSON fallback (only scalars included)."""
        payload = {
            "name": "test",
            "nested": {"key": "secret_data_not_shown"},
            "count": 42,
        }
        result = fmt_event_detail(payload)
        self.assertNotIn("[object Object]", result)
        # 'name' and 'count' are scalar — they should appear
        # 'nested' is a dict — should be excluded from JSON fallback
        self.assertNotIn("secret_data_not_shown", result)


class TestCaseJ_ThesisTransitionRender(unittest.TestCase):
    """Case J — timeline renders an affected THESIS_TRANSITION fixture.
    Expected: the output contains no [object Object].
    """

    THESIS_TRANSITION_FIXTURE = {
        "event_type":  "THESIS_TRANSITION",
        "event_label": "Thesis → FORMING_LONG",
        "timestamp":   "2026-07-31T03:00:00+00:00",
        "source":      "THESIS_TRANSITION",
        "is_derived":  False,
        "details": {
            "direction":           "LONG",
            "prev_status":         "NEUTRAL",
            "new_status":          "FORMING_LONG",
            "prev_confidence":     42,
            "new_confidence":      68,
            "primary_reason":      "Bullish structure developing",
            "invalidation_reason": None,
        },
    }

    def test_j1_details_do_not_produce_object_object(self):
        """Core bug regression: details object must never yield [object Object]."""
        result = fmt_event_detail(self.THESIS_TRANSITION_FIXTURE["details"])
        self.assertNotIn("[object Object]", result)
        self.assertNotIn("[object", result)
        self.assertTrue(len(result) > 0, "Non-null details must produce non-empty output")

    def test_j2_transition_produces_readable_text(self):
        """NEUTRAL → FORMING_LONG pattern is detected."""
        result = fmt_event_detail(self.THESIS_TRANSITION_FIXTURE["details"])
        self.assertIn("NEUTRAL", result)
        self.assertIn("FORMING_LONG", result)

    def test_j3_structured_fields_extracted(self):
        """ThesisTransitionDetail extracts the expected display fields."""
        fields = extract_thesis_transition_fields(
            self.THESIS_TRANSITION_FIXTURE["details"])
        self.assertIn("Previous", fields)
        self.assertEqual(fields["Previous"], "NEUTRAL")
        self.assertIn("Direction", fields)
        self.assertEqual(fields["Direction"], "LONG")
        self.assertIn("Reason", fields)
        self.assertIn("Bullish", fields["Reason"])
        self.assertIn("Confidence", fields)
        self.assertEqual(fields["Confidence"], "42 → 68")

    def test_j4_all_fields_absent_when_none(self):
        """Fields with None values are excluded from structured display."""
        fields = extract_thesis_transition_fields({
            "direction":       None,
            "prev_status":     None,
            "new_status":      "FORMING_LONG",
            "primary_reason":  None,
        })
        # prev_status is None → Previous not in output
        self.assertNotIn("Previous", fields)
        # direction is None → Direction not in output
        self.assertNotIn("Direction", fields)
        # Reason is None → not in output
        self.assertNotIn("Reason", fields)

    def test_j5_multiple_thesis_events_none_produce_object_object(self):
        """Simulates multiple timeline events — none should show [object Object]."""
        events = [
            {
                "event_type":  "THESIS_TRANSITION",
                "event_label": "Thesis → FORMING_LONG",
                "timestamp":   "2026-07-31T03:00:00+00:00",
                "details": {"prev_status": "NEUTRAL", "new_status": "FORMING_LONG",
                             "direction": "LONG", "primary_reason": "BOS detected"},
            },
            {
                "event_type":  "THESIS_TRANSITION",
                "event_label": "Thesis → READY_LONG",
                "timestamp":   "2026-07-31T03:05:00+00:00",
                "details": {"prev_status": "FORMING_LONG", "new_status": "READY_LONG",
                             "direction": "LONG", "primary_reason": "CHOCH confirmed"},
            },
            {
                "event_type":  "STRUCTURE_EVENT",
                "event_label": "BOS DEMAND",
                "timestamp":   "2026-07-31T02:58:00+00:00",
                "details": {"direction": "BULLISH", "alert_type": "BOS DEMAND",
                             "instrument": "MNQ", "candidate_direction": "LONG"},
            },
            {
                "event_type":  "READY_SIGNAL",
                "event_label": "READY signal — LONG",
                "timestamp":   "2026-07-31T03:07:00+00:00",
                "details": {"direction": "LONG", "edge_score": 80,
                             "alert_type": "MNQ BULLISH CONFIRMATION"},
            },
        ]
        for evt in events:
            result = fmt_event_detail(evt["details"])
            self.assertNotIn("[object Object]", result,
                             f"Event {evt['event_type']} produced [object Object]")
            self.assertIsInstance(result, str)


class TestDeduplication(unittest.TestCase):
    """Part 5 — Deduplication of exact duplicate events."""

    def test_dup1_exact_duplicates_removed(self):
        events = [
            {"event_type": "THESIS_TRANSITION", "timestamp": "2026-07-31T03:00:00",
             "event_label": "Thesis → FORMING_LONG"},
            {"event_type": "THESIS_TRANSITION", "timestamp": "2026-07-31T03:00:00",
             "event_label": "Thesis → FORMING_LONG"},  # exact duplicate
        ]
        result = dedup_events(events)
        self.assertEqual(len(result), 1)

    def test_dup2_different_timestamps_kept(self):
        events = [
            {"event_type": "THESIS_TRANSITION", "timestamp": "2026-07-31T03:00:00",
             "event_label": "Thesis → FORMING_LONG"},
            {"event_type": "THESIS_TRANSITION", "timestamp": "2026-07-31T03:05:00",
             "event_label": "Thesis → FORMING_LONG"},  # same label, different ts
        ]
        result = dedup_events(events)
        self.assertEqual(len(result), 2)

    def test_dup3_different_labels_kept(self):
        events = [
            {"event_type": "THESIS_TRANSITION", "timestamp": "2026-07-31T03:00:00",
             "event_label": "Thesis → FORMING_LONG"},
            {"event_type": "THESIS_TRANSITION", "timestamp": "2026-07-31T03:00:00",
             "event_label": "Thesis → READY_LONG"},
        ]
        result = dedup_events(events)
        self.assertEqual(len(result), 2)

    def test_dup4_different_event_types_kept(self):
        events = [
            {"event_type": "THESIS_TRANSITION", "timestamp": "2026-07-31T03:00:00",
             "event_label": "Thesis → FORMING_LONG"},
            {"event_type": "STRUCTURE_EVENT", "timestamp": "2026-07-31T03:00:00",
             "event_label": "Thesis → FORMING_LONG"},
        ]
        result = dedup_events(events)
        self.assertEqual(len(result), 2)

    def test_dup5_empty_list(self):
        self.assertEqual(dedup_events([]), [])

    def test_dup6_no_duplicates_unchanged(self):
        events = [
            {"event_type": "A", "timestamp": "T1", "event_label": "L1"},
            {"event_type": "B", "timestamp": "T2", "event_label": "L2"},
            {"event_type": "C", "timestamp": "T3", "event_label": "L3"},
        ]
        result = dedup_events(events)
        self.assertEqual(len(result), 3)


class TestThesisTransitionStructuredDisplay(unittest.TestCase):
    """Additional tests for ThesisTransitionDetail field extraction."""

    def test_tt1_only_available_fields_shown(self):
        fields = extract_thesis_transition_fields({
            "direction":      "SHORT",
            "prev_status":    "NEUTRAL",
            "new_status":     "FORMING_SHORT",
            "primary_reason": None,  # absent
        })
        self.assertIn("Previous", fields)
        self.assertIn("Direction", fields)
        self.assertNotIn("Reason", fields)

    def test_tt2_confidence_transition_both_sides(self):
        fields = extract_thesis_transition_fields({
            "prev_confidence": 30,
            "new_confidence":  70,
        })
        self.assertIn("Confidence", fields)
        self.assertEqual(fields["Confidence"], "30 → 70")

    def test_tt3_confidence_one_side_only(self):
        fields = extract_thesis_transition_fields({
            "new_confidence": 75,
        })
        self.assertIn("Confidence", fields)
        self.assertEqual(fields["Confidence"], "75")

    def test_tt4_empty_detail_dict(self):
        fields = extract_thesis_transition_fields({})
        self.assertEqual(fields, {})

    def test_tt5_non_dict_returns_empty(self):
        self.assertEqual(extract_thesis_transition_fields(None), {})
        self.assertEqual(extract_thesis_transition_fields("string"), {})
        self.assertEqual(extract_thesis_transition_fields([]), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
