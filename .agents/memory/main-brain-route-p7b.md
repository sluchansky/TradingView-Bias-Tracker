---
name: Main Brain route + builder (Phase 7B)
description: GET /main-brain read-only aggregation; edge_breakdown.components list quirk; OPEN_PATHS location.
---

## Rule

`build_main_brain_payload(result, instrument)` is the canonical builder for the Main Brain payload.
It takes the `full_analysis()` result dict as input (caller must call `full_analysis()` once and pass it in).
The route handler at `GET /main-brain` does: call `full_analysis()` → pass result to builder → cache.

**Why:** Same pattern as `build_manager_interface` / `build_coach_interface`. Keeps the builder pure (no IO) and the route thin.

**How to apply:** When adding new sections to the Main Brain payload, add a `_mb_<section>()` helper and wire it into `build_main_brain_payload()`. Never call `full_analysis()` from inside the builder.

---

## edge_breakdown.components is a list-of-dicts in the live result

`result["edge_breakdown"]["components"]` is a **list** like `[{"name": "bos_confirmed", "score": 20}, ...]` — NOT a `dict`.

Tests that construct fake results often use a dict `{"bos_confirmed": 20}` which works fine. But `dict(list)` throws `TypeError` at runtime on the live result.

**Why:** The edge breakdown builder (inside `_analysis_edge_breakdown`) builds components as a list for display ordering; `/status` serialises it directly.

**How to apply:** Any code that consumes `edge_breakdown["components"]` must handle both list-of-dicts and plain-dict. The normaliser pattern is:

```python
comps_raw = eb.get("components")
if isinstance(comps_raw, dict):
    components = dict(comps_raw)
elif isinstance(comps_raw, (list, tuple)):
    components = {}
    for c in comps_raw:
        if isinstance(c, dict):
            k = c.get("name") or c.get("key") or c.get("label")
            v = c.get("score") if c.get("score") is not None else c.get("value")
            if k is not None:
                components[str(k)] = v
else:
    components = {}
```

---

## OPEN_PATHS lives in dashboard-auth.ts (not flask-proxy.ts)

The Express auth bypass list (`OPEN_PATHS`) is declared in:

    artifacts/api-server/src/routes/dashboard-auth.ts

as `new Set(["/", "/ping", "/webhook", "/vrm"])`.

The `flask-proxy.ts` file does NOT define OPEN_PATHS — it only has the `BOT1_ROUTES` whitelist of routes to proxy. Comments in `flask-proxy.ts` mention "NOT in dashboard-auth OPEN_PATHS" which can confuse a string-search parser.

**Why:** Tests that verify a new route is owner-only must check `dashboard-auth.ts`, not `flask-proxy.ts`.

**How to apply:**

```python
import re
with open("artifacts/api-server/src/routes/dashboard-auth.ts") as f:
    content = f.read()
m = re.search(r'OPEN_PATHS\s*=\s*new\s+Set\s*\(\s*\[([^\]]*)\]', content, re.DOTALL)
open_set = m.group(1) if m else ""
assert '"/my-route"' not in open_set
```

---

## Deferred items (Phase 7C)

- `_DECISION_EVENT_LOG_BY_INST` full event deque (VERDICT_GENERATED, TRADE_OPENED, TRADE_CLOSED, JOURNAL_WRITTEN, STRATEGY_SELECTED, GATEWAY_OUTCOME)
- `execution_gateway.last_outcome` — no `_LAST_GATEWAY_RESULT_BY_INST` store yet
- `strategy_scanner.sample_count` / `historical_expectancy`
