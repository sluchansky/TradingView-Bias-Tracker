---
name: Databento Side enum JSON serialization trap
description: databento's Side enum implements __eq__ against strings, so `side in ("A", "B")` returns True while side is still an enum object — crashing json.dumps.
---

# Databento Side enum JSON serialization trap

## The rule
Never use `x if x in (str_a, str_b) else default` when `x` might be a databento enum. The `in` check uses `__eq__`, which databento enums implement against strings. If the check passes, `x` is still the enum object — `json.dumps({"side": x})` then raises `TypeError: Object of type Side is not JSON serializable`.

## Why
`Side.__eq__("A")` returns True for `Side.A` in databento's Python library. So `side if side in ("A", "B") else "N"` assigns the enum to `_side` when `side == "A"` — it does not convert it to a string.

## How to apply
Whenever normalizing a databento enum to a plain string for JSON serialization, use explicit equality checks that always produce a string literal:

```python
if side == "A":
    _side: str = "A"
elif side == "B":
    _side = "B"
else:
    _side = "N"
```

Or equivalently:
```python
_side = getattr(side, "value", str(side))  # only if .value is guaranteed to be a plain string
_side = _side if _side in ("A", "B") else "N"
```

The pattern applies to any databento enum (Schema, SType, Action, etc.) being placed into a dict that will be JSON-serialized.

**Surface:** `_databento_tick_broadcast()` in app.py, which fans out to SSE queues. The crash only occurs when the Databento live feed is connected and ticks arrive — it never triggers in tests (which don't run the live feed).
