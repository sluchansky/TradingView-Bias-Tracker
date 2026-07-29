# V1_FIRST_BATCH_BASELINE.md
## Phase 0 Evidence — Baseline Freeze Before First Implementation Batch
**Captured:** July 29, 2026
**Task:** V1-P0-001 through V1-P0-004

---

## 1. Repository State (Stage 1, Step 1)

| Field | Value |
|---|---|
| **Branch** | `polish-v1` |
| **HEAD commit** | `70061cc` — "Add version 1 of the implementation roadmap" |
| **Parent of HEAD** | `2db7a19` — "Add system architecture documentation" |
| **Working tree** | Clean — no modified tracked files |
| **Untracked files** | `attached_assets/Pasted-V1-FIRST-IMPLEMENTATION-BATCH-Baseline-Freeze-Additive-_1785349459821.txt` |
| **Staged changes** | None |
| **Git diff --check** | Passed (no whitespace errors) |
| **Replit checkpoint** | HEAD is a documentation-only commit (IMPLEMENTATION_ROADMAP_V1.md added) |

**Confirmation:** No application code changes had been made when this baseline was captured. The working tree was clean except for one untracked uploaded file.

---

## 2. Interface Location Map (Stage 1, Step 2)

### Interface 1 — Left Brain API v2

| Field | Detail |
|---|---|
| **File** | `artifacts/tradingview-webhook/left_brain_market_intelligence.py` |
| **Canonical boundary** | `compute_left_brain_thesis()` — the `thesis` dict at lines 1342–1355 |
| **Degraded output boundary** | `_neutral_thesis()` — the return dict at lines 1263–1282 |
| **Current keys (thesis dict)** | `available`, `instrument`, `direction`, `strength`, `momentum`, `established_at`, `last_updated_at`, `narrative`, `invalidation`, `playbooks`, `stability`, `timeline` |
| **Current keys (neutral dict)** | Same keys with default/None values |
| **Callers** | `_lb_update_thesis()` in `app.py`, called from `_databento_bar_scan()` |
| **Existing tests** | `check_scalp_golden.sh` (indirect); 128 tests documented in memory (unit + shadow + phase2) |
| **Why canonical** | This is the single function that produces all thesis outputs; both normal and degraded paths return the same schema. ARCH §7 defines Left Brain Interface as `_version: "v2"`. |

### Interface 2 — Expert Interface v1

| Field | Detail |
|---|---|
| **File** | `artifacts/tradingview-webhook/app.py` |
| **Canonical boundary** | `full_analysis()` — `result` dict returned at line 24578 (single return path enforced) |
| **Current guaranteed keys** | `verdict`, `reason`, `strict_direction`, `edge_score`, `is_actionable`, `strict_reason`, `gate_debug`, `grade`, `alert_level`, `trade_plan`, `directions`, `alert_diagnostics`, `analyst`, `trade_debate`, and ~60 additional keys |
| **Callers** | `/status` endpoint, all downstream analysis consumers, Coach, Journal, Partner |
| **Existing tests** | `check_parity.sh`, `check_scalp_golden.sh`, `check_dual_sim.sh`, `check_breakout_mode.sh` (all test sub-functions, not the full result dict) |
| **Why canonical** | `full_analysis()` is the documented single-return-path Expert output. ARCH §7 Expert Interface v1. `check_scalp_golden.sh` captures only `build_strict_trade_plan` and `evaluate_strict_setup` outputs — the `full_analysis()` result dict is NOT in the golden comparison. Adding `_version` to `result` will not affect any existing test. |

### Interface 3 — Partner Interface v1

| Field | Detail |
|---|---|
| **File** | `artifacts/tradingview-webhook/app.py` |
| **Canonical boundary** | `compute_main_brain()` — `mb_out` dict returned at line 20140 |
| **Degraded output boundary** | `_main_brain_neutral()` — return dict at lines 17758–17792 |
| **Current keys (mb_out)** | `status`, `headline`, `summary`, `market_brain`, `strategy_brain`, `risk_brain`, `trade_manager`, `favored_direction`, `edge_score`, `edge_grade`, `what_now`, `invalidation`, `mission`, `mission_progress`, `signals`, `confidence_pct`, `long_bias_pct`, `short_bias_pct`, `trade_quality`, `risk_level`, `bull_case`, `bear_case`, `management_read`, `performance_review`, `lessons`, `prop_rule`, `disclaimer`, `reason`, `observations`, `conflict_resolver`, `verdict_board`, `learning_memory`, `synthesis`, `unified` |
| **Callers** | `full_analysis()` (→ `result["main_brain"]`), dashboard Brain Contract JS |
| **Existing tests** | `check_main_brain_cognitive.sh`, `check_main_brain_judge.sh` |
| **Why canonical** | `compute_main_brain()` is the sole Partner synthesis entry point; `_main_brain_neutral()` is the only degraded output path. ARCH §7 Partner Interface v1. |

### Interface 4 — Manager Interface v1

| Field | Detail |
|---|---|
| **File** | `artifacts/tradingview-webhook/app.py` |
| **Canonical boundary** | `_active_trade_mgmt_block()` — return dict at lines 34279–34284 |
| **Current keys** | `enabled`, `count`, `positions`, `updated_at` |
| **Callers** | `/status` endpoint (→ `"active_trade_mgmt"` key) |
| **Existing tests** | `check_active_trade_mgmt.sh` |
| **Why canonical** | The Manager Interface in ARCH §7 defines output containing `active_trade`, `managed_trade`, `gateway_debug`, `auto_trade_enabled`, `_version`. In the current implementation, these fields are distributed across `full_analysis()` result and the `/status` endpoint. The `_active_trade_mgmt_block()` function is the single management-focused output object that captures active trade status and advisory recommendations — the core Manager responsibility. This is the closest existing single canonical boundary. Note: the architecture defines the Manager Interface aspirationally; the full consolidated Manager dict (ARCH §7 shape) is a V1 build target, not a current implementation. Adding `_version` here establishes the versioning boundary for the management output layer. No money-path code reads this dict. |

### Interface 5 — Execution Gateway Interface v1

| Field | Detail |
|---|---|
| **File** | `artifacts/tradingview-webhook/app.py` |
| **Canonical boundary** | `execute_trade_gateway()` — all non-error return dicts: `manual_required` (line 48275), `simulated` (line 48296), `sent` (line 48370) |
| **Current keys (sent)** | `status`, `provider`, `mode`, `broker_verify_required`, `plan`, `order` |
| **Current keys (simulated)** | `status`, `provider`, `mode`, `broker_verify_required`, `message`, `plan` |
| **Current keys (manual_required)** | `status`, `provider`, `mode`, `broker_verify_required`, `message`, `plan` |
| **Callers** | `/traderspost` handler, `_maybe_auto_execute()` |
| **Existing tests** | `check_broker_send.sh`, `check_prop_guard.sh` |
| **Why canonical** | `execute_trade_gateway()` is the ONLY function that produces broker-facing output and the single execution decision boundary. All success paths return a dict with a `status` field. ARCH §7 Execution Gateway Interface v1. Adding `_version` to all three non-error return paths ensures complete versioning. Error returns (status="error") are not the canonical interface contract. |

### Interface 6 — Journal Interface v1

| Field | Detail |
|---|---|
| **File** | `artifacts/tradingview-webhook/app.py` |
| **Canonical boundary** | `_build_card_entry()` — the `entry` dict, augmented through lines 29586–29707, returned at line 29707 |
| **Current keys** | `datetime`, `symbol`, `instrument`, `direction`, `setup_stage`, `strict_label`, `strict_score`, `verdict`, `entry_zone`, `stop_loss`, `target1`, `target2`, `rr`, `target3`, `be_level`, `partial_level`, `runner_target`, `edge_score`, `edge_grade`, `trade_strength`, `volatility_type`, `atr_pts`, `trade_thesis`, `why_qualifies`, `why`, `setup_notes`, `score_breakdown`, `risk_adjustments`, `indicators`, `vwap_status`, `cvd_state`, `structure_status`, `last_alert_times`, `session_preferred`, `session_bonus`, `session_window`, `stage_next_step`, `stage_invalidation`, `market_intelligence`, `_swing_context`, `setup_categories`, `confidence_governor`, `trade_memory`, and many more |
| **Callers** | Discord card sends, `LAST_READY_BY_TICKER` store, strategy_trades insert (via journal pipeline) |
| **Existing tests** | Implicit through all regression workflows |
| **Why canonical** | `_build_card_entry()` is the documented single source for all journal and card content (per `analysis-data-quirks.md` memory). Every trade record flowing to Discord, `strategy_trades`, and `LAST_READY_BY_TICKER` passes through this function. ARCH §7 Journal Interface v1. |

### Interface 7 — Coach Interface v1

| Field | Detail |
|---|---|
| **File** | `artifacts/tradingview-webhook/app.py` |
| **Canonical boundary** | `result["learning_score_influence"]` dict, constructed at lines 23331–23338 (normal path) and 24063–24070 (market-closed override) inside `full_analysis()` |
| **Current keys** | `enabled`, `armed`, `max_delta`, `meta`, `Long`, `Short` |
| **Callers** | `/status` endpoint (→ `"learning_score_influence"` key), `_check_auto_trade()` reads `meta.active_key` |
| **Existing tests** | `check_learning_score_golden.sh` |
| **Why canonical** | `learning_score_influence` is the Coach's canonical output block within `full_analysis()` — it carries the per-direction weight adjustment that feeds downstream edge scoring (the sole money-path learning effect). Both construction paths (normal and market-closed override) must carry `_version` for contract completeness. ARCH §7 Coach Interface v1. |

---

## 3. Baseline Test Results (Stage 1, Step 3)

### Primary Regression Workflows

All 4 primary regressions ran and passed immediately before the Stage 2 implementation began:

| Workflow | Command | Result |
|---|---|---|
| `parity` | `bash .local/state/check_parity.sh` | **PASS** — "PARITY OK (registry/resolver identical to baseline)" |
| `scalp_golden` | `bash .local/state/check_scalp_golden.sh` | **PASS** — "SCALP GOLDEN OK (byte-identical to baseline)" |
| `dual_sim` | `bash .local/state/check_dual_sim.sh` | **PASS** — "DUAL-SIM SMOKE OK (MODE=SCALP — fidelity + money-path isolation)" |
| `breakout_mode` | `bash .local/state/check_breakout_mode.sh` | **PASS** — "BREAKOUT SMOKE OK" |

Evidence: Workflow log outputs captured in `/tmp/logs/` (see `parity_*.log`, `scalp_golden_*.log`, `dual_sim_*.log`, `breakout_mode_*.log` at baseline timestamp).

### Golden Test Impact Analysis

`check_scalp_golden.sh` compares output of `build_strict_trade_plan()` and `evaluate_strict_setup()` only. The golden script filters `evaluate_strict_setup()` output to 4 fields: `label`, `direction`, `score`, `missing`. The `full_analysis()` result dict is NOT captured by the golden comparison. Adding `_version` to the 7 canonical interface dicts will NOT affect any golden comparison.

`check_parity.sh` compares registry-derived values: `INSTRUMENT_SPECS`, `ACCOUNT_PROFILES`, `TRADERSPOST_TICKER`, `ALERTS_MUTED`, resolver functions. None of these are touched by the `_version` additions.

---

## 4. Known Warnings and Pre-existing Conditions

| Item | Description |
|---|---|
| Pending re-publish | obs-infra closure and SWEEP_RECLAIM fixes are in dev, not yet in production (blocked by Replit registry issue ticket #481442) |
| dual_sim WARNING | `WARNING:app:dual-sim shadow verdict (SWING) failed: 'str' object has no attribute 'get'` — pre-existing, appears in dual_sim test but test passes with PASS result |
| Databento in dev | `DATABENTO_ENABLED=1` but live Databento feed active (DATABENTO_API_KEY present in production env). All 4 instruments receiving live Databento bar signals. |
| `_version` fields absent | No `_version` field exists in any of the 7 component output dicts before this batch. This is the pre-change state this batch corrects. |

---

## 5. Explicit Confirmation

**No code changes had been made when this baseline was captured.** This document was written before Stage 2 (Add Version Metadata) began. The baseline reflects the repository at commit `70061cc`.
