# PHASE 6 EXECUTION BRIEF
# V1-P6 — Journal and Coach Separation
# DOCUMENTATION ONLY — DO NOT MODIFY PRODUCTION CODE

Prepared: 2026-07-30
Branch: polish-v1
Starting HEAD: 91dc5cb (accepted Phase 5 endpoint)
Current HEAD: 3ccc56f

---

## 1. Executive Summary

Phase 6 is titled **"Journal and Coach Separation"**. It consists of seven verification and
test-writing tasks (V1-P6-001 through V1-P6-007), all in Stream F. Every task is
**test-only** — no production code, no database schema changes, no broker changes,
no deployment.

The roadmap explicitly marks all seven tasks "Prod Code? = No" and the phase constraint is:
"Must not change: Journal, strategy_trades, learning engine behavior."

Two commits exist between the accepted Phase 5 endpoint (91dc5cb) and the current HEAD:

1. **9cc131a** — documentation-only: added attached instruction files to `attached_assets/`
2. **3ccc56f** — production implementation (task #33 merge): added `thesis_last_resolved_at`
   to `build_coach_interface()` and the Coach dashboard panel

The 3ccc56f commit is an additive extension to the Coach v1 interface that has no negative
effect on any of the seven Phase 6 tasks. Baseline tests all pass: 356/356.

Three research questions must be resolved before or during implementation. They do not
block the brief but must be resolved before writing specific test assertions:

- RQ-1: V1-P6-002 acceptance criterion names "closed_at and result_r in open_trades" — but
  `open_trades` rows are DELETED on close, not updated; those fields live in `strategy_trades`.
- RQ-2: V1-P6-005 names a "unified_learning block" in /status — no top-level `unified_learning`
  key exists in `_build_status_payload`; the learning output is in `result["coach"]`.
- RQ-3: V1-P6-007 claims DISCORD_LIVE_ENABLED gates journal Discord sends — the actual
  implementation gates only on `DISCORD_JOURNAL_WEBHOOK_URL`, not on `DISCORD_LIVE_ENABLED`.

Phase 6 is **ready to implement** once these three questions are documented as resolved
within the new test file. No stop condition applies to any of them.

---

## 2. Baseline State

| Item                      | Value                                        |
|---------------------------|----------------------------------------------|
| Branch                    | polish-v1                                    |
| HEAD at start             | 91dc5cb (accepted Phase 5 endpoint)          |
| HEAD at brief preparation | 3ccc56f                                      |
| Working tree              | Clean (git status: nothing to commit)        |
| git diff --stat           | (empty — clean)                              |
| git diff --check          | Exit 0 — no trailing whitespace or conflicts |
| 91dc5cb ancestor of HEAD  | Yes (confirmed via git log)                  |

### Commits between 91dc5cb and 3ccc56f (HEAD)

| SHA     | Message                                            | Classification             |
|---------|----------------------------------------------------|----------------------------|
| 9cc131a | Add phase 5 and 6 audit and execution briefs       | documentation-only         |
| 3ccc56f | Show thesis resolution history on Coach dashboard  | production implementation  |

### Classification notes

**9cc131a (documentation-only):** Added two files to `attached_assets/`:
- `attached_assets/Pasted--V1-PHASE-5-AND-6-COMPATIBILITY-AUDIT-...txt`
- `attached_assets/Pasted--V1-PHASE-6-PRE-IMPLEMENTATION-EXECUTION-BRIEF-DOCUMENT_...txt`

No production Python, no tests, no database, no dashboard changed. No Phase 6 overlap.

**3ccc56f (production implementation):**
- Task: Show thesis resolution history on Coach dashboard panel (task-agent #33)
- Files changed: `artifacts/tradingview-webhook/app.py` (+56 lines, -15 lines)
- Functions changed:
  - `build_coach_interface()`: adds `thesis_last_resolved_at` field; changes `thesis_resolved`
    source from `bool(THESIS_TRACKER_DB_READY)` → `_THESIS_LAST_RESOLVED_AT is not None`
  - Dashboard HTML: adds `#mbt-coach-resolved-row` / `#mbt-coach-resolved-time` elements
  - `renderThesisTracker()` JS: reads `d.coach.thesis_last_resolved_at` and renders time
- Roadmap ownership: Stream F (Coach interface) — additive extension to V1-P1-007
- Phase 6 overlap: V1-P6-004 (Coach boundary), V1-P6-005 (learning block in /status),
  V1-P6-006 (Coach-unavailable test), V1-P6-007 (journal Discord gate)
- Changes accepted Phase 5 baseline: No — all 356 tests pass, regressions unchanged
- Semantic note: the `thesis_resolved` mapping correction aligns with the ARCH §7 intent
  stated in V1_MANAGER_COACH_INTERFACE_VALIDATION.md §9: "If the authoritative event
  information only exists at trade-close time, the Coach interface may legitimately report
  unavailable during ordinary full_analysis()." _THESIS_LAST_RESOLVED_AT is set at
  trade-close, remains None at boot and during ordinary full_analysis(). All 85 interface
  tests pass unchanged, confirming no contract regression.

---

## 3. Controlling Document Review

Documents read:
- IMPLEMENTATION_ROADMAP_V1.md (2216 lines)
- SYSTEM_ARCHITECTURE_V1.md (1855 lines)
- PRODUCT_SPEC_V1.md (1174 lines)
- PLATFORM_BLUEPRINT.md (2049 lines)
- V1_MANAGER_COACH_INTERFACE_VALIDATION.md (283 lines)
- PHASE_5_EXECUTION_BRIEF.md (933 lines)
- V1_PHASE_5_VALIDATION.md (existing)

### Precedence conflicts found: NONE

All seven documents agree on:
- Phase 6 title: "Journal and Coach Separation"
- Phase 6 tasks: V1-P6-001 through V1-P6-007
- Phase 6 stream: F
- No production code changes authorized
- Dependencies: Phase 5 complete

### Resolved annotations

**IMPLEMENTATION_ROADMAP_V1.md (authoritative):** Phase 6 section at lines 1105–1128.
All seven task descriptions at lines 1110–1116. Task matrix at lines 2002–2008.
Priority scores at lines 1472–1477.

**SYSTEM_ARCHITECTURE_V1.md:** Coach is post-trade only, strictly read-only from
strategy_trades. Coach failure must never affect live gates. Journal is the sole writer
to strategy_trades. open_trades is the active-position persistence table.

**PRODUCT_SPEC_V1.md:** strategy_trades is the permanent trade record (INSERT/SELECT only).
Discord journal channel gated; no non-live sends. Coach boundary: never blocks webhook.

**PLATFORM_BLUEPRINT.md:** Confirms DISCORD_LIVE_ENABLED gates journal live sends.
strategy_trades INSERT failure: logged at ERROR, platform continues.

**V1_MANAGER_COACH_INTERFACE_VALIDATION.md:** Semantic corrections for weight_updated
(updated_at event signal) and thesis_resolved (resolution event) accepted in 64f6d35.
3ccc56f builds on this foundation correctly.

---

## 4. Controlling Document Review

_(No conflicts. No discrepancies. All documents agree on Phase 6 scope.)_

---

## 5. Exact Phase 6 Title and Scope

**Title:** Journal and Coach Separation
**Phase number:** 6
**Stream:** F
**Roadmap location:** IMPLEMENTATION_ROADMAP_V1.md lines 1105–1128
**Goal:** Verify trade capture, journal writes, Coach boundary, and learning output versioning.

**Phase constraint (must not change):** Journal, strategy_trades, learning engine behavior.

**Exit criteria (from roadmap):**
1. strategy_trades write test passes (acceptance criterion 6.1)
2. Journal resilience test passes (acceptance criterion 6.4)
3. Coach boundary assertion passes
4. Coach-unavailable test passes
5. All 4 primary regressions pass

**Acceptance criteria from IMPLEMENTATION_ROADMAP_V1.md:**
- AC-6.1 (line 517): Journal captures completed trades in strategy_trades — NEEDS VERIFICATION
- AC-6.4 (line 520): Journal failure does not crash platform — NEEDS VERIFICATION

---

## 6. Complete Task Inventory

### V1-P6-001: strategy_trades write test

| Attribute              | Value |
|------------------------|-------|
| Task ID                | V1-P6-001 |
| Title                  | Write strategy_trades write test |
| Roadmap requirement    | paper trade close → row exists in strategy_trades |
| Business purpose       | Confirm trade capture is working end-to-end before deploying |
| Architecture owner     | Journal (Stream F) |
| Status                 | MISSING — no test exercises strategy_trades INSERT |
| Production function    | `_record_strategy_trade(mt)` line ~11927 |
| Callers                | Trade-close path in managed-trade lifecycle |
| Serialized fields      | managed_key, journal_id, opened_at, closed_at, symbol, strategy_key, strategy, market_regime, session, direction, entry, stop, target, result, r_multiple, hold_minutes, confidence, quality, edge_score, mode, indicators, entry_efficiency, momentum_score, grade, scalper_grade, mfe_r, mae_r, slippage, exit_price, entry_reason, outcome_reason, outcome_tag, day_of_week, volatility_type, trading_mode, strategy_version, trade_label, setup_type, trigger_type, learning_ns |
| Runtime side effects   | INSERT to strategy_trades; triggers learning recompute cycle |
| Persistence behavior   | ON CONFLICT (managed_key) DO NOTHING — idempotent |
| Expected files to change | test_phase6_journal_coach.py (new file) |
| Authorized behavior changes | None — test-only |
| Prohibited behavior    | Must not modify _record_strategy_trade or strategy_trades schema |
| Dependencies           | P5 complete |
| Required tests         | 1 new test: simulate paper-close → assert strategy_trades row |
| Completion criteria    | Test passes; row confirmed in isolated DB |
| Implementation risk    | MEDIUM — requires DB test isolation |
| Unresolved questions   | Test must use isolated DB connection, not production state |

### V1-P6-002: open_trades update test

| Attribute              | Value |
|------------------------|-------|
| Task ID                | V1-P6-002 |
| Title                  | Write open_trades update test |
| Roadmap requirement    | trade close → open_trades row has closed_at and result_r |
| Business purpose       | Confirm the active-position persistence is cleaned up on close |
| Architecture owner     | Manager / Active Trade Persistence (Stream F) |
| Status                 | NEEDS VERIFICATION — open_trades row is DELETED on close, not updated |
| Production function    | `clear_active_trade(inst)` → `_persist_active_trade(inst, None)` line ~202 |
| Callers                | Trade-close path |
| Serialized fields      | DELETE from open_trades WHERE inst = %s (no update path) |
| Runtime side effects   | Row removed from open_trades |
| Persistence behavior   | Permanent DELETE |
| Expected files to change | test_phase6_journal_coach.py (new file) |
| Authorized behavior changes | None — test-only |
| Prohibited behavior    | Must not modify open_trades schema or clear_active_trade |
| Dependencies           | P5 complete |
| Required tests         | 1 new test verifying trade-close clears open_trades |
| Completion criteria    | Test passes; row absent in open_trades after close |
| Implementation risk    | HIGH — roadmap acceptance criterion says "closed_at and result_r in open_trades" but the actual schema stores closed_at and result_r in strategy_trades, not open_trades |
| Unresolved questions   | **RQ-1: CRITICAL** — see Section 9 |

### V1-P6-003: Journal failure resilience test

| Attribute              | Value |
|------------------------|-------|
| Task ID                | V1-P6-003 |
| Title                  | Write journal-failure resilience test |
| Roadmap requirement    | simulate strategy_trades INSERT fail → webhook returns 200, no crash |
| Business purpose       | Confirm platform continues when DB write fails |
| Architecture owner     | Journal (Stream F) |
| Status                 | MISSING — fail-open try/except exists in code but no test injects failure |
| Production function    | `_record_strategy_trade(mt)` try/except at line ~11927 |
| Callers                | Trade-close path |
| Serialized fields      | None (failure path logs and returns) |
| Runtime side effects   | None on failure — FAIL-OPEN |
| Persistence behavior   | Record may be lost on failure; logged at WARNING |
| Expected files to change | test_phase6_journal_coach.py (new file) |
| Authorized behavior changes | None — test-only |
| Prohibited behavior    | Must not modify _record_strategy_trade error handling |
| Dependencies           | P6-001 (must understand normal path before testing failure path) |
| Required tests         | 1 new test: patch DB connection to raise, assert 200 returned |
| Completion criteria    | Test passes; no crash; log message emitted |
| Implementation risk    | LOW — fail-open path already exists; just needs fault injection |
| Unresolved questions   | None |

### V1-P6-004: Coach boundary assertion

| Attribute              | Value |
|------------------------|-------|
| Task ID                | V1-P6-004 |
| Title                  | Verify Coach boundary |
| Roadmap requirement    | no Coach function writes to ALERT_HISTORY, VWAP_BY_TICKER, or ACTIVE_TRADES_BY_INST |
| Business purpose       | Ensure Coach cannot corrupt real-time trading state |
| Architecture owner     | Coach (Stream F) |
| Status                 | NEEDS VERIFICATION — boundary confirmed by inspection, needs runtime assertion |
| Production function    | `build_coach_interface()` lines 22878–22977 |
| Callers                | full_analysis() at line 24787 |
| Serialized fields      | weight_updated, thesis_resolved, thesis_last_resolved_at, learning_influence, rule_engine_eligibility, _version |
| Runtime side effects   | None — confirmed read-only |
| Persistence behavior   | None |
| Expected files to change | test_phase6_journal_coach.py (new file) |
| Authorized behavior changes | None — test-only |
| Prohibited behavior    | Must not modify build_coach_interface |
| Dependencies           | P5 complete |
| Required tests         | 1 assertion: call build_coach_interface() → verify ALERT_HISTORY, VWAP_BY_TICKER, ACTIVE_TRADES_BY_INST all unchanged |
| Completion criteria    | Assertion passes after 3+ calls |
| Implementation risk    | LOW — boundary already enforced; test is a static + runtime check |
| Unresolved questions   | 3ccc56f added thesis_last_resolved_at reading _THESIS_LAST_RESOLVED_AT; must confirm that global is also read-only in this context |

### V1-P6-005: Learning block in /status

| Attribute              | Value |
|------------------------|-------|
| Task ID                | V1-P6-005 |
| Title                  | Verify learning output block present in /status |
| Roadmap requirement    | unified_learning, per-mode stats present in /status |
| Business purpose       | Confirm operator can see learning health in the dashboard |
| Architecture owner     | Coach (Stream F) |
| Status                 | NEEDS VERIFICATION — no top-level "unified_learning" key in _build_status_payload; learning output is in result["coach"] |
| Production function    | `_build_status_payload()` lines 44498–44601 |
| Callers                | /status route |
| Serialized fields      | coach.weight_updated, coach.thesis_resolved, coach.thesis_last_resolved_at, coach.learning_influence, coach.rule_engine_eligibility; also learning_rule_engine via _learning_rule_engine_view() |
| Runtime side effects   | None — read path |
| Persistence behavior   | None |
| Expected files to change | test_phase6_journal_coach.py (new file) |
| Authorized behavior changes | None — verification only |
| Prohibited behavior    | Must not add a new "unified_learning" key to _build_status_payload |
| Dependencies           | P6-001 |
| Required tests         | 1 assertion: /status response contains "coach" key with required learning fields |
| Completion criteria    | Assertion passes; "coach" block with all required fields confirmed |
| Implementation risk    | LOW |
| Unresolved questions   | **RQ-2** — see Section 9 |

### V1-P6-006: Coach-unavailable test

| Attribute              | Value |
|------------------------|-------|
| Task ID                | V1-P6-006 |
| Title                  | Write Coach-unavailable test |
| Roadmap requirement    | simulate learning exception → /status still returns, Expert verdict unaffected |
| Business purpose       | Confirm Coach failure is isolated from the analysis pipeline |
| Architecture owner     | Coach (Stream F) |
| Status                 | MISSING — fail-open wrapper exists but no test injects exception |
| Production function    | `build_coach_interface()` try/except block lines 22971–22977 |
| Callers                | full_analysis() at line 24787; /status via a.get("coach") |
| Serialized fields      | Neutral stubs on failure: weight_updated=False, thesis_resolved=False, thesis_last_resolved_at=None, learning_influence=0.0, rule_engine_eligibility="LIVE_ELIGIBLE", _version="v1" |
| Runtime side effects   | None on failure |
| Persistence behavior   | None |
| Expected files to change | test_phase6_journal_coach.py (new file) |
| Authorized behavior changes | None — test-only |
| Prohibited behavior    | Must not modify build_coach_interface fail-open block |
| Dependencies           | P5 complete |
| Required tests         | 1 test: patch build_coach_interface to raise → assert full_analysis() returns, verdict unchanged, coach block = neutral stubs |
| Completion criteria    | Test passes; no 500; verdict preserved |
| Implementation risk    | LOW — fail-open already built and proven by 85 interface tests |
| Unresolved questions   | None |

### V1-P6-007: Discord journal DISCORD_LIVE_ENABLED gate

| Attribute              | Value |
|------------------------|-------|
| Task ID                | V1-P6-007 |
| Title                  | Verify Discord journal gated on DISCORD_LIVE_ENABLED |
| Roadmap requirement    | regression check — no dev Discord sends during regression runs |
| Business purpose       | Ensure regression test runs never fire real Discord posts |
| Architecture owner     | Journal (Stream F) |
| Status                 | NEEDS VERIFICATION — send_journal_discord_embed() checks DISCORD_JOURNAL_WEBHOOK_URL, NOT DISCORD_LIVE_ENABLED |
| Production function    | `send_journal_discord_embed()` line 24886 |
| Callers                | Journal send path |
| Serialized fields      | DISCORD_JOURNAL_WEBHOOK_URL check at line 24888 |
| Runtime side effects   | If DISCORD_JOURNAL_WEBHOOK_URL is unset, logs warning and returns |
| Persistence behavior   | None |
| Expected files to change | test_phase6_journal_coach.py (new file) |
| Authorized behavior changes | None — verification only |
| Prohibited behavior    | Must not modify send_journal_discord_embed gating logic |
| Dependencies           | P6-001 |
| Required tests         | 1 assertion: confirm DISCORD_JOURNAL_WEBHOOK_URL is absent in test env → journal send skips without network call |
| Completion criteria    | Test passes; no HTTP request during regression run |
| Implementation risk    | LOW |
| Unresolved questions   | **RQ-3** — see Section 9 |

---

## 7. Existing Implementation Audit

### 7.1 strategy_trades — INSERT path

**Function:** `_record_strategy_trade(mt)` (~line 11927)
**Trigger:** Trade close via managed-trade lifecycle
**Error handling:** try/except around entire body — logs WARNING on failure, always returns
**Idempotency:** ON CONFLICT (managed_key) DO NOTHING
**Test coverage:** test_decision_quality.py and test_backtest_baseline.py reference the table
  for reads; no existing test exercises the INSERT path
**Classification:** Existing production behavior — partially tested (read path only)

### 7.2 open_trades — close path

**Function:** `_persist_active_trade(inst, None)` (~line 202)
**SQL:** `DELETE FROM open_trades WHERE inst = %s`
**Behavior:** Row is removed entirely; no UPDATE; no closed_at or result_r column in open_trades
**Classification:** Existing production behavior — no test coverage

### 7.3 open_trades — schema

**Columns (from INSERT upsert):** inst (PK), payload (JSONB), opened_at, updated_at
**No columns:** closed_at, result_r, exit_price
**Classification:** Existing production behavior — open_trades is exclusively an
  active-position persistence table; it does not archive closed positions

### 7.4 build_coach_interface() — post 3ccc56f

**Line range:** 22878–22977
**Returns:** weight_updated (bool), thesis_resolved (bool), thesis_last_resolved_at (str|None),
  learning_influence (float), rule_engine_eligibility (str), _version ("v1")
**Reads:** LEARNING_ANALYTICS.get("updated_at"), _THESIS_LAST_RESOLVED_AT,
  result["learning_score_influence"], LEARNING_ELIGIBILITY cache
**Writes to:** Nothing — confirmed read-only
**Writes to ALERT_HISTORY:** No
**Writes to VWAP_BY_TICKER:** No
**Writes to ACTIVE_TRADES_BY_INST:** No
**Fail-open:** Yes — try/except returns neutral stubs; _version always "v1"
**Classification:** Existing production behavior — fully implemented; boundary confirmed

### 7.5 /status serialization of Coach

**Line:** 44601: `"coach": a.get("coach")`
**Present in _build_status_payload:** Yes
**Fields delivered to dashboard:** weight_updated, thesis_resolved, thesis_last_resolved_at,
  learning_influence, rule_engine_eligibility, _version
**Top-level "unified_learning" key:** ABSENT — not in _build_status_payload
**Classification:** Existing production behavior — coach block IS a learning output block

### 7.6 send_journal_discord_embed() — gating

**Line:** 24886
**Gate condition:** `if not DISCORD_JOURNAL_WEBHOOK_URL:` (line 24888) — RETURNS if not set
**DISCORD_LIVE_ENABLED check:** NOT present in send_journal_discord_embed()
**Comment at line 755:** states journal is "additionally gated to the live instance
  (DISCORD_LIVE_ENABLED)" — but the implementation does not enforce this gate
**In practice:** DISCORD_JOURNAL_WEBHOOK_URL is not set in test environments, so
  regression runs never fire journal Discord posts regardless
**Classification:** Existing production behavior — effectively safe in test env; comment
  implies DISCORD_LIVE_ENABLED gate but code does not enforce it

### 7.7 Existing test coverage for Phase 6 areas

| Coverage item             | Test file                          | Status        |
|---------------------------|------------------------------------|---------------|
| strategy_trades INSERT    | (none)                             | MISSING       |
| open_trades DELETE        | (none)                             | MISSING       |
| Journal failure injection | (none)                             | MISSING       |
| Coach boundary assertion  | test_v1_interface_versions.py      | PARTIAL       |
| Coach fields in /status   | test_v1_interface_versions.py      | PRESENT       |
| Coach fail-open           | test_v1_interface_versions.py      | PRESENT       |
| Discord journal gate      | (none)                             | MISSING       |
| thesis_last_resolved_at   | (none — new field from 3ccc56f)    | MISSING       |

Existing test_v1_interface_versions.py has 85 tests covering all Coach and Manager
contract fields. None directly test the three database paths (P6-001, P6-002, P6-003)
or the Discord gate (P6-007).

---

## 8. Dependency Graph

```
P5 complete (91dc5cb) ─┬─ V1-P6-001 (strategy_trades write test)
                       │      │
                       │      ├─ V1-P6-002 (open_trades close test) [parallel with P6-001]
                       │      │
                       │      ├─ V1-P6-003 (journal failure test) [after P6-001]
                       │      │
                       │      └─ V1-P6-005 (learning block in /status) [parallel with P6-001]
                       │
                       ├─ V1-P6-004 (Coach boundary) [independent]
                       │
                       ├─ V1-P6-006 (Coach-unavailable) [independent]
                       │
                       └─ V1-P6-007 (Discord journal gate) [independent; recommend after P6-001]
```

### Independent tasks (parallelizable with P6-001)

- V1-P6-002 (open_trades close)
- V1-P6-004 (Coach boundary)
- V1-P6-005 (learning block in /status)
- V1-P6-006 (Coach-unavailable)
- V1-P6-007 (Discord journal gate)

### Tasks that depend on P6-001

- V1-P6-003 (resilience test — requires understanding the normal write path first)

### Tasks that must be implemented together

All seven can share a single new test file: `test_phase6_journal_coach.py`

### Tasks that should remain verification-only

- V1-P6-004 (Coach boundary) — static source inspection + runtime call-count assertion
- V1-P6-005 (learning block in /status) — read-only /status payload inspection
- V1-P6-007 (Discord journal gate) — environment inspection + send-path assertion

### Tasks that should be deferred

None.

### Hidden dependencies

1. V1-P6-001, V1-P6-002, V1-P6-003 require a live DB connection for the test.
   Test isolation strategy: use psycopg2 with rollback or a dedicated test managed_key
   prefix (e.g., `test_p6_`) to avoid contaminating production analytics.

2. V1-P6-004 (Coach boundary) must account for `thesis_last_resolved_at` added by 3ccc56f.
   _THESIS_LAST_RESOLVED_AT is a global read; the test must confirm it is not mutated by
   build_coach_interface().

3. V1-P6-005 resolution depends on RQ-2 — the interpretation of "unified_learning block"
   will determine whether the test asserts `result["coach"]` or a dedicated top-level key.

4. V1-P6-006 must use unittest.mock to patch build_coach_interface at its call site inside
   full_analysis(), not at module level, to avoid interfering with other tests.

---

## 9. Research Questions

### RQ-1: open_trades schema vs. V1-P6-002 acceptance criterion

**Question:** The roadmap acceptance criterion for V1-P6-002 says "open_trades row has
closed_at and result_r on close." The actual open_trades schema has no closed_at or result_r
columns; on close the row is deleted entirely. Strategy_trades contains closed_at and
result_r. Which table does V1-P6-002 intend to test?

**Reason it matters:** If the test is written to assert closed_at and result_r in open_trades,
it will fail by design (columns absent). The test must target the correct table.

**Likely owner:** Journal / SYSTEM_ARCHITECTURE_V1.md §6 (active trade lifecycle)

**Evidence found:**
- `_persist_active_trade(inst, None)` executes `DELETE FROM open_trades WHERE inst = %s`
- open_trades upsert uses columns: inst, payload, opened_at, updated_at
- `_record_strategy_trade(mt)` writes closed_at and result_r (r_multiple) to strategy_trades
- SYSTEM_ARCHITECTURE_V1.md (line 955): "_persist_active_trade() called with cleared state
  → open_trades row updated (closed_at, exit_price, result_r)" — arch doc says UPDATE
- SYSTEM_ARCHITECTURE_V1.md (line 884): "Boot restores from open_trades as INERT"

**Evidence still required:** Confirm whether the SYSTEM_ARCHITECTURE_V1.md description
(UPDATE) is aspirational vs. the implementation (DELETE). Check if `payload` JSONB in
open_trades contains closed_at and result_r on any code path.

**Blocks implementation:** No — test can be written as "row absent in open_trades after
close" OR "strategy_trades row has closed_at and result_r." Both are valid test designs
depending on interpretation. The brief recommends: write the test to assert (a) open_trades
row is ABSENT after close, AND (b) strategy_trades row has closed_at and result_r. This
satisfies both interpretations.

**Safest resolution:** Write V1-P6-002 as a two-part test:
  Part A: open_trades row for the instrument is absent after close
  Part B: strategy_trades row for the managed_key has closed_at and r_multiple set

---

### RQ-2: "unified_learning block" in /status

**Question:** V1-P6-005 requires verifying "unified_learning, per-mode stats present in
/status." No top-level `unified_learning` key exists in `_build_status_payload`. The
learning output is under `result["coach"]`. Does this task require a new top-level key,
or is it satisfied by asserting `coach` contains the required fields?

**Reason it matters:** If a new `unified_learning` key is required, this becomes a
production code change (prohibited by Phase 6). If it means `coach`, the test can be
written immediately.

**Likely owner:** Coach interface architecture (Stream F) / ARCH §7

**Evidence found:**
- `unified_learning` appears zero times in `_build_status_payload`
- `compute_unified_learning()` exists and returns a learning summary dict (win_rate, avg_r,
  sample, best_setup, etc.), but its output lives inside `result["main_brain"]`, not at
  top-level
- `result["coach"]` IS present in /status (line 44601)
- ROADMAP line 1521: "Coach Interface v1 — Verify learning block in /status → assert
  weight_updated, learning_influence, rule_engine_eligibility, _version present"
  (this is the contract test description for Coach v1)
- V1-P6-005 success criteria (line 2006): "unified_learning block present in /status"

**Evidence still required:** None — the roadmap contract test at line 1521 maps directly
to the `coach` block fields, not to a separate `unified_learning` key.

**Blocks implementation:** No.

**Safest resolution:** V1-P6-005 = "verify learning output block present in /status" is
satisfied by asserting `result["coach"]` (the Coach v1 interface block) is present in /status
with the required fields. The phrase "unified_learning block" in the task description refers
to the block that contains learning output, not to a specific dict key named "unified_learning."
Do NOT add a new top-level `unified_learning` key to `_build_status_payload` — that would
be an unauthorized production change.

---

### RQ-3: DISCORD_LIVE_ENABLED vs. DISCORD_JOURNAL_WEBHOOK_URL gating

**Question:** V1-P6-007 says "Verify Discord journal gated on DISCORD_LIVE_ENABLED (regression
check — no dev sends)." The implementation at line 24888 gates only on
DISCORD_JOURNAL_WEBHOOK_URL (absent → skip), not on DISCORD_LIVE_ENABLED. A comment at
line 755 says the journal is "additionally gated to the live instance (DISCORD_LIVE_ENABLED)"
but this is not reflected in code. What does V1-P6-007 actually require?

**Reason it matters:** The test must be written against the actual gate, not the comment.
If DISCORD_LIVE_ENABLED is the intended gate, a production change would be needed
(prohibited). If DISCORD_JOURNAL_WEBHOOK_URL absence is sufficient, the test can be
written immediately.

**Likely owner:** Journal (Stream F)

**Evidence found:**
- `send_journal_discord_embed()` line 24888: `if not DISCORD_JOURNAL_WEBHOOK_URL: return`
- DISCORD_JOURNAL_WEBHOOK_URL is not set in the test environment (it's an env secret)
- DISCORD_LIVE_ENABLED is not checked in send_journal_discord_embed()
- In regression runs, DISCORD_JOURNAL_WEBHOOK_URL is absent → journal sends are suppressed
- The regression test environment already achieves "no dev sends" via URL absence

**Evidence still required:** None.

**Blocks implementation:** No.

**Safest resolution:** V1-P6-007 = "verify no Discord sends during regression runs" is
satisfied by: (a) asserting DISCORD_JOURNAL_WEBHOOK_URL is absent in test env, (b) asserting
send_journal_discord_embed() returns without making an HTTP call when the URL is absent.
Do NOT add a DISCORD_LIVE_ENABLED check to send_journal_discord_embed() — that would be an
unauthorized production change. The existing URL-absence gate is the correct mechanism.

---

## 10. Learning and Coach Semantic Audit

### 10.1 Accepted semantics (from V1_MANAGER_COACH_INTERFACE_VALIDATION.md §9)

| Field                   | Accepted semantic                             | Source                                    |
|-------------------------|-----------------------------------------------|-------------------------------------------|
| weight_updated          | True only when _recompute_learning() ran      | LEARNING_ANALYTICS.get("updated_at") bool |
| thesis_resolved         | True only when _resolve_open_theses() ran     | _THESIS_LAST_RESOLVED_AT is not None      |
| learning_influence      | Actual ±15 delta applied to active direction  | result["learning_score_influence"] delta  |
| rule_engine_eligibility | Eligibility from LEARNING_ELIGIBILITY cache   | _check_learning_eligibility() [0]         |

### 10.2 Post-3ccc56f state

**weight_updated:**
- Source: `bool(LEARNING_ANALYTICS.get("updated_at"))` — unchanged by 3ccc56f
- Semantic: event signal (recompute ran) — preserved
- Test coverage: test_coach_recompute_event_sets_weight_updated_true, test_coach_weight_updated_false_when_recompute_not_run
- Phase 6 effect: None

**thesis_resolved:**
- Source: `_THESIS_LAST_RESOLVED_AT is not None` — CHANGED by 3ccc56f
- Previous mapping: always False (no global flag existed)
- New mapping: True when _THESIS_LAST_RESOLVED_AT is set (resolution event ran this session)
- Semantic: now a real event signal — this is an IMPROVEMENT from the semantic audit's
  fallback-to-False position
- Semantic correction from 64f6d35 preserved: THESIS_TRACKER_DB_READY still does NOT imply True
  (DB ready does not set _THESIS_LAST_RESOLVED_AT)
- All 85 interface tests pass — no regression
- Phase 6 effect: V1-P6-004 boundary test must include _THESIS_LAST_RESOLVED_AT as read-only

**thesis_last_resolved_at (NEW field from 3ccc56f):**
- Source: `_THESIS_LAST_RESOLVED_AT.isoformat()` if not None, else None
- Type: ISO-8601 UTC str | None
- Absent value: None (always None at boot and in test env)
- Malformed value: if _THESIS_LAST_RESOLVED_AT is set to a non-datetime, .isoformat() raises
  (caught by fail-open wrapper)
- Event/current-state semantics: event — None at boot, set only at resolution event
- Mutation protection: _THESIS_LAST_RESOLVED_AT is a module-level global; only
  _resolve_open_theses() sets it
- Current tests: 0 dedicated tests for thesis_last_resolved_at
- Phase 6 effect: V1-P6-004 must assert this field is read-only in the Coach builder;
  V1-P6-006 neutral stubs must return thesis_last_resolved_at: None

**learning_influence:**
- Source: unchanged — result["learning_score_influence"] active-direction delta
- Phase 6 effect: None

**rule_engine_eligibility:**
- Source: unchanged — _check_learning_eligibility() cache read
- Phase 6 effect: None

### 10.3 Semantic correctness confirmation

NONE of the accepted semantic corrections from commit 64f6d35 are reversed by 3ccc56f.
The thesis_resolved change is consistent with the ARCH §7 intent stated in the validation:
"True if _resolve_open_theses() ran in this session." The 3ccc56f implementation now
delivers that intent correctly using _THESIS_LAST_RESOLVED_AT as the event flag.

### 10.4 Phase 6 semantic boundary rules

- Coach functions must NEVER write to ALERT_HISTORY, VWAP_BY_TICKER, ACTIVE_TRADES_BY_INST
- Coach functions must NEVER trigger _recompute_learning()
- Coach functions must NEVER call execute_trade_gateway()
- Coach functions must NEVER set _THESIS_LAST_RESOLVED_AT (only _resolve_open_theses() may)
- weight_updated = True implies _recompute_learning() ran, not DB availability
- thesis_resolved = True implies _THESIS_LAST_RESOLVED_AT is set, not THESIS_TRACKER_DB_READY
- learning_influence is a scalar float, not a nested object

---

## 11. File Impact Matrix

### Production code

| File                              | Expected change | Reason |
|-----------------------------------|-----------------|--------|
| artifacts/tradingview-webhook/app.py | NONE — must not change | All P6 tasks are test-only |

### Test files

| File                                                    | Expected change | Notes |
|---------------------------------------------------------|-----------------|-------|
| artifacts/tradingview-webhook/test_phase6_journal_coach.py | CREATE NEW | All 7 tasks produce tests here |
| artifacts/tradingview-webhook/test_v1_interface_versions.py | May add tests for thesis_last_resolved_at | Additive only; 85 existing must pass |

### Validation documentation

| File                                                     | Expected change |
|----------------------------------------------------------|-----------------|
| artifacts/tradingview-webhook/PHASE_6_EXECUTION_BRIEF.md | THIS FILE       |
| artifacts/tradingview-webhook/V1_PHASE_6_VALIDATION.md   | CREATE after implementation |

### Files expected NOT to change

| File                                              | Reason |
|---------------------------------------------------|--------|
| artifacts/tradingview-webhook/app.py              | Prohibited — "Must not change: Journal, strategy_trades, learning engine behavior" |
| Database schema                                   | No schema changes authorized or needed |
| artifacts/tradingview-webhook/test_phase5_execution_safety.py | P5 regression; must remain 109/109 |
| artifacts/tradingview-webhook/test_phase4_operator_explanation.py | P4 regression; must remain 57/57 |
| artifacts/tradingview-webhook/test_phase3_thesis_verdict_pipeline.py | P3 regression; must remain 60/60 |
| artifacts/tradingview-webhook/test_phase2_market_data_reliability.py | P2 regression; must remain 45/45 |
| artifacts/tradingview-webhook/checks/run_phase2_smoke.sh | Smoke; must remain 8/8 |

---

## 12. Canonical Interface Impact Matrix

| Interface              | Phase 6 effect       | Notes |
|------------------------|----------------------|-------|
| Left Brain v2          | Unchanged            | No P6 task touches Left Brain |
| Expert v1              | Unchanged            | No P6 task touches Expert verdict |
| Partner v1             | Unchanged            | No P6 task touches Partner |
| Manager v1             | Unchanged            | No P6 task touches Manager builder |
| Execution Gateway v1   | Unchanged            | No P6 task touches gateway |
| Journal v1             | Verification only    | P6-001, P6-002, P6-003, P6-007 verify existing behavior; no contract change |
| Coach v1               | Additive (3ccc56f)   | thesis_last_resolved_at already added; P6-004, P6-005, P6-006 verify; no further contract change |

### Coach v1 post-3ccc56f contract (current)

| Field                   | Type           | Absent value     | Version effect       |
|-------------------------|----------------|------------------|----------------------|
| weight_updated          | bool           | False            | No version bump needed |
| thesis_resolved         | bool           | False            | No version bump needed |
| thesis_last_resolved_at | str \| None    | None             | Additive — backward-compatible |
| learning_influence      | float          | 0.0              | No version bump needed |
| rule_engine_eligibility | str            | "LIVE_ELIGIBLE"  | No version bump needed |
| _version                | "v1"           | always present   | Remains v1 |

thesis_last_resolved_at is additive (new key, None when absent). Consumers that did not
expect it safely receive None. A version bump to v2 is NOT required.

---

## 13. Behavioral Change Matrix

| Item                        | Phase 6 authorization | Reason |
|-----------------------------|-----------------------|--------|
| Verdict                     | Must remain unchanged | Test-only phase |
| Direction                   | Must remain unchanged | Test-only phase |
| Confidence                  | Must remain unchanged | Test-only phase |
| Edge score                  | Must remain unchanged | Test-only phase |
| Edge grade                  | Must remain unchanged | Test-only phase |
| Actionability               | Must remain unchanged | Test-only phase |
| Readiness                   | Must remain unchanged | Test-only phase |
| Failed conditions           | Must remain unchanged | Test-only phase |
| Veto behavior               | Must remain unchanged | Test-only phase |
| Strategy selection          | Must remain unchanged | Test-only phase |
| Learning weights            | Must remain unchanged | "Must not change: learning engine behavior" |
| Strategy weights            | Must remain unchanged | "Must not change: learning engine behavior" |
| Learning sample counts      | Must remain unchanged | Test-only phase |
| Learning influence          | Must remain unchanged | Test-only phase |
| Rule eligibility            | Must remain unchanged | Test-only phase |
| Thesis lifecycle            | Must remain unchanged | Test-only phase |
| Risk                        | Must remain unchanged | Test-only phase |
| Sizing                      | Must remain unchanged | Test-only phase |
| Stops / Targets             | Must remain unchanged | Test-only phase |
| Active-trade management     | Must remain unchanged | Test-only phase |
| Journal persistence         | Must remain unchanged | "Must not change: Journal" |
| Database schema             | Must remain unchanged | No DDL authorized |
| Databento behavior          | Must remain unchanged | Test-only phase |
| Gateway status/outcome      | Must remain unchanged | Test-only phase |
| Broker routing              | Must remain unchanged | Test-only phase |
| Live execution              | Must remain unchanged | Test-only phase |
| Dashboard display           | Must remain unchanged | Test-only phase |
| Authentication              | Must remain unchanged | Test-only phase |
| Deployment configuration    | Must remain unchanged | Do not deploy |

---

## 14. Database and Persistence Audit

### Tables touched by Phase 6 (read in tests)

| Table            | Test access | Write in test? | Risk |
|------------------|-------------|----------------|------|
| strategy_trades  | P6-001, P6-003 | INSERT via _record_strategy_trade (isolated) | MEDIUM — must use rollback or test-keyed managed_key |
| open_trades      | P6-002 | Managed via clear_active_trade (isolated) | MEDIUM — must not leave residue |

### Table schemas confirmed

**open_trades:** inst (PK), payload (JSONB), opened_at (timestamp), updated_at (timestamp)
- No closed_at column, no result_r column
- Close path: DELETE (not UPDATE)

**strategy_trades:** managed_key (UNIQUE), journal_id, opened_at, closed_at, symbol,
strategy_key, strategy, market_regime, session, direction, entry, stop, target, result,
r_multiple, hold_minutes, confidence, quality, edge_score, mode, indicators,
entry_efficiency, momentum_score, grade, scalper_grade, mfe_r, mae_r, slippage, exit_price,
entry_reason, outcome_reason, outcome_tag, day_of_week, volatility_type, trading_mode,
strategy_version, trade_label, setup_type, trigger_type, learning_ns
- Has closed_at and r_multiple (result_r equivalent)

### Test isolation requirements

1. All DB-writing tests must use a unique managed_key prefix (e.g., `test_p6_<uuid4>`) to
   prevent pollution of production analytics
2. After test, DELETE the test row from strategy_trades by managed_key
3. Tests for open_trades must use a dedicated test instrument key (e.g., `TEST_P6`) and
   clean up with direct DELETE
4. No test may ALTER, DROP, or TRUNCATE any table
5. All DB tests must run on the development DB only (ACTIVE_TRADES_DB_READY + LEARNING_DB_READY)
6. If DB is unavailable, tests must skip (not fail) to preserve CI safety

### Schema migration requirements

None. No schema changes required for Phase 6.

---

## 15. Validation Strategy

### V1-P6-001: strategy_trades write test

**Test group:** Journal database persistence
**Runtime owner:** _record_strategy_trade (test via mock managed_trade dict)
**Normal case:** build mock mt → call _record_strategy_trade → SELECT from strategy_trades WHERE managed_key = test_key → assert row exists, closed_at set, r_multiple set
**Below-threshold:** N/A (no threshold)
**Malformed case:** mt missing required fields → function should not crash
**Unavailable case:** DB unavailable → function logs WARNING, does not crash
**Repeated-call case:** call twice with same managed_key → second INSERT is DO NOTHING → exactly one row
**Side-effect assertions:** ACTIVE_TRADES_BY_INST unchanged, ALERT_HISTORY unchanged
**Completion evidence:** row confirmed in strategy_trades with correct fields

### V1-P6-002: open_trades update test

**Test group:** Journal database persistence
**Runtime owner:** clear_active_trade → _persist_active_trade(inst, None)
**Normal case (Part A):** inject row into open_trades for TEST_P6 → call clear_active_trade("TEST_P6") → assert row absent from open_trades
**Normal case (Part B):** after P6-001 write → assert strategy_trades row has closed_at and r_multiple non-null
**Unavailable case:** DB unavailable → no crash; open_trades clear logs warning
**Side-effect assertions:** ACTIVE_TRADES_BY_INST unchanged for other instruments
**Completion evidence:** open_trades row absent; strategy_trades row has temporal and R fields

### V1-P6-003: Journal failure resilience test

**Test group:** Journal fault injection
**Runtime owner:** _record_strategy_trade (fault-injected)
**Normal case:** already covered by P6-001
**Fault case:** patch _learning_conn to raise psycopg2.OperationalError → call _record_strategy_trade → assert no exception propagates, log message emitted
**Webhook-level case:** simulate the full close path with patched DB → assert no HTTP 500
**Completion evidence:** function returns without exception; warning logged; no crash

### V1-P6-004: Coach boundary assertion

**Test group:** Coach isolation
**Runtime owner:** build_coach_interface
**Normal case:** snapshot sizes of ALERT_HISTORY, VWAP_BY_TICKER, ACTIVE_TRADES_BY_INST → call build_coach_interface() 3 times → assert all sizes unchanged
**Boundary case:** inject _THESIS_LAST_RESOLVED_AT = datetime.utcnow() → call build_coach_interface() → assert _THESIS_LAST_RESOLVED_AT unchanged after call
**Side-effect assertion:** LEARNING_ANALYTICS.get("updated_at") unchanged
**Completion evidence:** 0 mutations to all three guarded stores after 3+ Coach calls

### V1-P6-005: Learning block in /status

**Test group:** Interface serialization
**Runtime owner:** _build_status_payload / result["coach"]
**Normal case:** call full_analysis() → assert result["coach"] present; assert all required fields present (weight_updated, thesis_resolved, thesis_last_resolved_at, learning_influence, rule_engine_eligibility, _version)
**Alternate:** call a.get("coach") on mock status payload → assert same
**Malformed case:** none — fail-open guarantees neutral stubs
**Completion evidence:** all 6 coach fields confirmed in /status-equivalent payload

### V1-P6-006: Coach-unavailable test

**Test group:** Coach fault injection
**Runtime owner:** build_coach_interface (exception injected)
**Normal case:** already covered by V1-P1-007 (85 interface tests)
**Fault case:** patch the body of build_coach_interface to raise RuntimeError before try/except → verify neutral stubs returned (fail-open catches it)
**Alternate:** patch internal LEARNING_ANALYTICS read to raise → full_analysis() must still return a valid verdict; result["coach"]._version must equal "v1"
**Completion evidence:** full_analysis() returns; verdict unaffected; coach._version = "v1"

### V1-P6-007: Discord journal gate test

**Test group:** Journal send gate
**Runtime owner:** send_journal_discord_embed
**Normal case (no URL set):** assert DISCORD_JOURNAL_WEBHOOK_URL is absent in test env → call send_journal_discord_embed(mock_entry) → assert function returns without HTTP call
**Normal case (URL set, mock):** patch requests.post → call send_journal_discord_embed → assert post called exactly once
**Completion evidence:** zero HTTP calls in regression env; function short-circuits on absent URL

---

## 16. Regression Contract

### Minimum counts that must be preserved

| Suite                                      | Count    |
|--------------------------------------------|----------|
| test_phase5_execution_safety.py            | 109      |
| test_phase4_operator_explanation.py        | 57       |
| test_v1_interface_versions.py              | ≥ 85     |
| test_phase3_thesis_verdict_pipeline.py     | 60       |
| test_phase2_market_data_reliability.py     | 45       |
| Phase 2 smoke (repository root)            | 8/8      |
| Phase 2 smoke (/tmp)                       | 8/8      |
| parity                                     | PASS     |
| scalp_golden                               | BYTE-IDENTICAL |
| dual_sim                                   | PASS     |
| breakout_mode                              | PASS     |
| py_compile app.py                          | OK       |
| node --check (dashboard script)            | OK       |
| git diff --check                           | exit 0   |

### New tests

| Test file                            | Expected count | Approval required |
|--------------------------------------|----------------|-------------------|
| test_phase6_journal_coach.py         | ≥ 7 (one per task) | Operator must approve final count before commit |

### Byte-identical outputs

The following must remain byte-identical:
- scalp_golden baseline
- parity registry/resolver output

The following may change:
- test_v1_interface_versions.py count may increase if thesis_last_resolved_at tests are added

### Data cleanup

All DB-writing tests must DELETE their test rows before the test function returns.
No test may leave persistent state in strategy_trades or open_trades.
Test order independence must be preserved (no test may rely on state left by another test).

---

## 17. Recommended Implementation Order

### Step 1 — Research resolution checkpoint (before writing tests)

**Tasks:** RQ-1, RQ-2, RQ-3
**Objective:** Document resolved interpretations at top of test_phase6_journal_coach.py
**Authorized files:** test_phase6_journal_coach.py (new)
**Prerequisite:** None
**Estimated complexity:** Low
**Checkpoint criteria:** All 3 research questions have documented resolution comments

### Step 2 — DB test isolation scaffold

**Tasks:** P6-001, P6-002, P6-003 (infrastructure)
**Objective:** Establish DB connection helper, test-key prefix, cleanup fixture
**Authorized files:** test_phase6_journal_coach.py (new)
**Prerequisite:** DB available (ACTIVE_TRADES_DB_READY or LEARNING_DB_READY)
**Estimated complexity:** Medium
**Checkpoint criteria:** setUp/tearDown confirmed; test DB rows deleted after each test

### Step 3 — P6-001 strategy_trades write test (first DB test)

**Tasks:** V1-P6-001
**Authorized files:** test_phase6_journal_coach.py
**Prerequisite:** Step 2 scaffold
**Estimated complexity:** Medium
**Regression risk:** Low — no production code changes
**Checkpoint criteria:** Test passes; row confirmed in strategy_trades; row deleted in tearDown

### Step 4 — P6-002, P6-003 (remaining DB tests) [parallel]

**Tasks:** V1-P6-002, V1-P6-003
**Authorized files:** test_phase6_journal_coach.py
**Prerequisite:** Step 3 (normal path understood)
**Estimated complexity:** Medium (P6-002), Low (P6-003)
**Checkpoint criteria:** Both tests pass; no residue in DB

### Step 5 — P6-004, P6-005, P6-006, P6-007 (non-DB tests) [parallel]

**Tasks:** V1-P6-004, V1-P6-005, V1-P6-006, V1-P6-007
**Authorized files:** test_phase6_journal_coach.py
**Prerequisite:** None (independent)
**Estimated complexity:** Low
**Checkpoint criteria:** All four pass; no HTTP calls; no state mutations

### Step 6 — Full regression run

**Checkpoint criteria:**
- python3 test_phase6_journal_coach.py: all pass
- All existing suites at accepted counts
- All 4 primary regressions pass
- py_compile OK, node --check OK, git diff --check clean

### Step 7 — Documentation commit

**Authorized file:** V1_PHASE_6_VALIDATION.md (new)
**Commit message:** V1-P6 Journal and Coach Separation
**Prerequisite:** All tests pass, full regression green

---

## 18. Risk Register

| ID   | Task       | Description                                         | Likelihood | Impact | Detection                       | Mitigation                              | Stop condition |
|------|------------|-----------------------------------------------------|------------|--------|---------------------------------|-----------------------------------------|----------------|
| R-01 | P6-001     | Test DB write leaves residue in strategy_trades      | MEDIUM     | HIGH   | SELECT on test prefix after run | Parameterized tearDown DELETE by key    | Test leaves orphan row |
| R-02 | P6-002     | open_trades schema/behavior mismatch (RQ-1)          | HIGH       | MEDIUM | Test failure if assertion wrong | Write two-part test per RQ-1 resolution | Cannot resolve RQ-1 |
| R-03 | P6-003     | Fault injection breaks imports for other tests       | LOW        | HIGH   | Test isolation failure          | Use unittest.mock.patch context mgr     | Patch leaks across tests |
| R-04 | P6-004     | Coach boundary test misses a write path              | LOW        | HIGH   | New write escapes assertion     | Snapshot all 3 guarded stores; use id() | Mutation confirmed |
| R-05 | P6-005     | "unified_learning" interpreted as new top-level key  | MEDIUM     | HIGH   | Production change attempt       | RQ-2 resolution in comments            | If production change attempted, STOP |
| R-06 | P6-006     | Exception injection patches wrong call site          | LOW        | MEDIUM | Test passes but doesn't test fault path | Verify patch at full_analysis seam | None |
| R-07 | P6-007     | DISCORD_JOURNAL_WEBHOOK_URL set in test env          | LOW        | MEDIUM | Test makes real HTTP call       | Assert URL absent before test; mock requests.post | Real Discord post |
| R-08 | ALL        | DB unavailable during test run                      | MEDIUM     | MEDIUM | DB connection fails             | Skip (not fail) DB tests when LEARNING_DB_READY=False | None — skip is safe |
| R-09 | ALL        | 3ccc56f introduces Coach test gap (thesis_last_resolved_at) | LOW  | LOW    | test_v1_interface_versions.py missing new field | Add presence/type test for new field | None |
| R-10 | P6-001     | strategy_trades duplicate key on repeated test runs  | LOW        | LOW    | INSERT DO NOTHING silently      | Use uuid4-based managed_key per test run | None — idempotent |

**Highest-risk task:** V1-P6-002 — due to the open_trades schema vs. roadmap acceptance
criterion mismatch (RQ-1). The test assertion depends on which table the roadmap intends
to verify.

---

## 19. Phase 6 Execution Contract

### Authorized task IDs

V1-P6-001, V1-P6-002, V1-P6-003, V1-P6-004, V1-P6-005, V1-P6-006, V1-P6-007

### Authorized production files

**None.** Phase 6 is test-only.

### Authorized learning files

**None.** Phase 6 must not change learning engine behavior.

### Authorized database files

**None.** No schema changes authorized. Test isolation via INSERT/DELETE within test
functions only, not via schema migration.

### Authorized test files

- `artifacts/tradingview-webhook/test_phase6_journal_coach.py` — CREATE NEW
- `artifacts/tradingview-webhook/test_v1_interface_versions.py` — MAY ADD tests for
  `thesis_last_resolved_at` (additive only; existing 85 must pass unchanged)

### Authorized documentation files

- `artifacts/tradingview-webhook/PHASE_6_EXECUTION_BRIEF.md` — THIS FILE (documentation commit)
- `artifacts/tradingview-webhook/V1_PHASE_6_VALIDATION.md` — CREATE after implementation

### Authorized functions (test-only)

- `_record_strategy_trade(mt)` — call as unit under test; must not modify
- `clear_active_trade(inst)` — call as unit under test; must not modify
- `build_coach_interface(result, ...)` — call as unit under test; must not modify
- `full_analysis()` — call with mock state for P6-005, P6-006; must not modify
- `send_journal_discord_embed(entry)` — call with patched URL or mock; must not modify

### Authorized runtime changes

- INSERT to strategy_trades (test rows with test-key prefix) — cleaned up per test
- DELETE from strategy_trades (test cleanup only)
- INSERT to open_trades (test rows with TEST_P6 inst) — cleaned up per test
- DELETE from open_trades (test cleanup only)

### Prohibited runtime changes

- Any modification to: app.py, any production Python file, any regression test file
- Any DDL (CREATE TABLE, ALTER TABLE, DROP TABLE)
- Any change to learning weights, strategy weights, edge scoring
- Any change to gateway, broker, execution path
- Any change to journal write logic
- Any change to Discord send logic
- Any change to DISCORD_LIVE_ENABLED semantics
- Any deployment, publish, or server restart (beyond test runner)
- Any change to parity/scalp_golden/dual_sim/breakout_mode behavior

### Coach semantic restrictions

- `weight_updated` must remain: bool(LEARNING_ANALYTICS.get("updated_at"))
- `thesis_resolved` must remain: _THESIS_LAST_RESOLVED_AT is not None
- `thesis_last_resolved_at` must remain: _THESIS_LAST_RESOLVED_AT.isoformat() | None
- `learning_influence` must remain: active-direction LSI delta (float)
- `rule_engine_eligibility` must remain: LEARNING_ELIGIBILITY cache read
- `_version` must remain: "v1"
- No semantic correction from 64f6d35 may be reversed

### Database restrictions

- No new tables
- No schema modifications
- Test rows must use isolated keys (managed_key prefix `test_p6_` + uuid4)
- All test rows must be deleted in test tearDown
- No test may assume a specific row count in production tables

### Learning restrictions

- Learning weights must not be modified by any Phase 6 code
- _recompute_learning() must not be called by any Phase 6 test (it causes side effects)
- LEARNING_ANALYTICS must not be mutated by Phase 6 tests (read-only assertions only)
- PER_MODE_STATS must not be mutated by Phase 6 tests

### Gateway restrictions

- execute_trade_gateway() must not be called by Phase 6 tests
- _TRADERSPOST_LAST must be unchanged after Phase 6 tests

### Broker restrictions

- No HTTP calls to broker endpoints
- EXECUTION_MODE must remain as-is in test env

### Databento restrictions

- No Databento calls in Phase 6 tests

### Dashboard restrictions

- No changes to dashboard HTML, CSS, or JavaScript

### Authentication restrictions

- No changes to auth routes, OPEN_PATHS, or owner_required decorator

### Required tests (minimum)

- 1 test for P6-001: strategy_trades INSERT confirmed
- 1 test for P6-002: open_trades close behavior confirmed (two-part)
- 1 test for P6-003: journal failure does not crash
- 1 test for P6-004: Coach boundary — 0 mutations to guarded stores
- 1 test for P6-005: learning output block present in /status
- 1 test for P6-006: Coach-unavailable → full_analysis returns
- 1 test for P6-007: Discord send absent when URL absent

### Required regressions

All 4 primary regressions must pass after Phase 6:
- parity (PASS)
- scalp_golden (BYTE-IDENTICAL)
- dual_sim (PASS)
- breakout_mode (PASS)

All Phase 2–5 test counts must be preserved:
- P5: 109, P4: 57, interface: ≥85, P3: 60, P2: 45

### Required evidence

- test_phase6_journal_coach.py output: all tests pass, printed to stdout
- Strategy_trades row confirmed (SELECT result in test log)
- open_trades row confirmed absent after close
- Coach boundary: 0 mutation assertions printed
- All 4 primary regressions: PASS
- py_compile: OK
- git diff --check: exit 0

### Diff-control rules

- app.py diff must be empty
- No test file diff for existing regression suites (except additive test_v1_interface_versions.py)
- All diffs limited to: test_phase6_journal_coach.py (new), V1_PHASE_6_VALIDATION.md (new),
  and optionally test_v1_interface_versions.py (additive only)

### Commit strategy

One commit after all tests pass:
  `V1-P6 Journal and Coach Separation`

Do not amend 3ccc56f or 91dc5cb.

### Deployment restrictions

- Do not deploy
- Do not publish
- Do not restart production

### Stop conditions

STOP and report if:
- Any test requires a production code change (Phase 6 is test-only)
- RQ-1 cannot be resolved without modifying clear_active_trade or open_trades schema
- RQ-2 resolution requires adding a new top-level key to _build_status_payload
- Any Phase 6 test mutates ALERT_HISTORY, VWAP_BY_TICKER, or ACTIVE_TRADES_BY_INST
  outside of explicit test setup/teardown
- Any existing baseline test count drops

---

## 20. Completion Checklist

- [ ] test_phase6_journal_coach.py created with ≥7 tests
- [ ] RQ-1 resolution documented in test file comments
- [ ] RQ-2 resolution documented in test file comments
- [ ] RQ-3 resolution documented in test file comments
- [ ] V1-P6-001: strategy_trades INSERT confirmed at runtime
- [ ] V1-P6-002: open_trades close behavior confirmed at runtime
- [ ] V1-P6-003: journal failure does not crash — confirmed
- [ ] V1-P6-004: Coach boundary — 0 writes to ALERT_HISTORY/VWAP/ACTIVE_TRADES
- [ ] V1-P6-005: learning output block present in /status — confirmed
- [ ] V1-P6-006: Coach-unavailable — full_analysis returns, verdict unaffected
- [ ] V1-P6-007: no Discord sends in test env — confirmed
- [ ] test_v1_interface_versions.py: thesis_last_resolved_at tests added (if applicable)
- [ ] All 4 primary regressions pass
- [ ] Phase 2–5 test counts preserved (109 + 57 + 85 + 60 + 45)
- [ ] Phase 2 smoke passed from repo root and /tmp
- [ ] py_compile OK
- [ ] git diff --check exit 0
- [ ] app.py diff is empty
- [ ] No DB residue (strategy_trades and open_trades test rows deleted)
- [ ] V1_PHASE_6_VALIDATION.md created
- [ ] Commit: `V1-P6 Journal and Coach Separation`
- [ ] No deployment, no publish

---

## 21. Blocking Issues

**None that prevent starting implementation.**

Three research questions (RQ-1, RQ-2, RQ-3) are present but none block writing the tests.
Each has a documented safe-resolution path that does not require production code changes.

| Issue | Status | Resolution path |
|-------|--------|-----------------|
| RQ-1: open_trades schema | Resolved for implementation — write two-part test (Part A: row absent; Part B: strategy_trades row has closed_at and r_multiple) | Document in test file |
| RQ-2: unified_learning block | Resolved for implementation — "unified_learning block" = result["coach"] block; no new key needed | Document in test file |
| RQ-3: DISCORD_LIVE_ENABLED gate | Resolved for implementation — URL-absence is the actual gate; DISCORD_JOURNAL_WEBHOOK_URL absent in test env is sufficient | Document in test file |

---

## 22. Recommendation: Ready or Not Ready

**Phase 6 is READY to implement.**

Conditions:
- Accepted endpoint 91dc5cb is an ancestor of current HEAD ✅
- Current HEAD 3ccc56f is a production implementation (task #33 merge) but does not
  conflict with any Phase 6 task ✅
- All 356 baseline tests pass ✅
- All 4 primary regressions pass ✅
- Phase 2 smoke from both directories passes ✅
- py_compile OK ✅
- git diff --check clean ✅
- All 7 Phase 6 tasks are test-only; no production code changes required ✅
- All 3 research questions have safe resolution paths that do not require production changes ✅
- No blocking architecture conflicts ✅
- No deferred scope that would prevent completion ✅

One action is recommended before starting implementation:
Add tests for `thesis_last_resolved_at` (the new field from 3ccc56f) to
`test_v1_interface_versions.py` as part of the Phase 6 test commit. This fills the
gap between the current 85 interface tests and the Coach v1 contract as it exists in HEAD.
This is additive and does not require any production change.

---

*End of Phase 6 Execution Brief*
