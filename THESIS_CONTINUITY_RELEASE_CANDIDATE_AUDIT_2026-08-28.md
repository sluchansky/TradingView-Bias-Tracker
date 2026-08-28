# Thesis Continuity Release-Candidate Audit

## Certification decision

**BLOCKED — do not release.**

The mode-scoped thesis continuity implementation passed its focused regression,
development PostgreSQL restart/restore, heartbeat-idempotency, parity, golden,
simulation, broker-boundary, frontend/API, journal-quarantine, and static
Windows-safety checks. The overall release candidate is not certified because
two independent validation contracts remain red:

1. The Phase-5 gateway suite has a wall-clock-dependent Long fixture that is
   rejected by the intentional Asia-session safety floor before the paper/manual
   contract assertions run.
2. The Order Flow Edge Score test still supplies the obsolete
   `bos_confirmed`/`bos` contract, while the shared scorer now accepts the
   canonical `structure_allocation` input. It therefore expects 35 but receives
   the correct 15 points for its supplied inputs.

No tag, push, publish, deployment, production migration, production mutation,
Windows runtime change, execution enablement, or broker transmission was
performed.

## Scope and revision

- Audited source revision: `c745877114bea1718699e9e700ec312f7b281cee`
- Observation date: 2026-08-28 UTC
- Environment: development only
- Trading workflow safety pins remained active:
  `EXECUTION_MODE=disabled`, `MANUAL_ORDER_ENABLED=0`,
  `LIVE_RUNNER_ENABLED=0`
- PostgreSQL guard was not weakened or changed.

## Persistence preflight and restart result

- `scripts/test_persistence_guard.py`: **10 passed**
- Source policy: **passed**
- Read-only development PostgreSQL reconnect: **passed repeatedly**
- Database reported by the unchanged guard: `heliumdb`, 86 public tables,
  5 analysis-bot tables
- Controlled development workflow restarts: **passed**
- Backend readiness after cleanup: `/ping` returned
  `{"status":"ok","trading_mode":"SCALP"}`
- The earlier PostgreSQL preflight report was not reproducible and is classified
  as transient after repeated unchanged source-policy and reconnect passes.

## Real development restore proof

The existing SCALP rows for MGC, MNQ, MES, and MYM retained their durable thesis
identities through restart while live evidence advanced naturally.

To prove the second canonical mode against the real development database, four
uniquely identified temporary `INTRADAY_TREND` snapshots and exact transition
events were inserted, the disabled-execution development backend was restarted,
and the read API was checked:

- MGC, MNQ, MES, and MYM each restored independently as
  `mode=INTRADAY_TREND`, `status=FORMING_SHORT`, `confidence=61`
- Each restored snapshot returned `entryStatus=WAIT`, `entryPaused=true`, and
  `restoredAwaitingFreshEvaluation=true`
- Each mode-scoped history returned exactly its matching deterministic event
  with `transitionIndex=0`
- All temporary snapshots and events were deleted
- Cleanup query returned `snapshots=0, events=0`
- The backend was restarted after cleanup so no synthetic in-memory thesis
  remained

Focused tests separately proved confirmed reversal ordering and atomicity:
prior thesis `INVALIDATED` first, replacement thesis `FORMING` second, stable
event identity, replay deduplication, rollback behavior, bounded-history
restore, and fresh-evaluation entry safety.

## Live heartbeat observation

A 70-second read-only observation sampled all four SCALP instruments every five
seconds and compared:

- thesis ID
- direction
- lifecycle status
- confidence and confidence state
- reversal epoch
- evidence epoch
- durable timeline head and bounded count

Result: **zero idempotency violations**.

- Repeated evidence epochs did not change thesis ID, direction, status,
  confidence, confidence state, reversal epoch, or transition history.
- Observed confidence changes occurred only when the evidence epoch advanced.
- The first post-restart samples were excluded from steady-state judgment
  because restored snapshots were converging to current live evidence.
- `EVIDENCE_UNCHANGED` reason-code annotation is intentionally observational;
  the regression contract defines the no-op around identity, state, confidence,
  evidence epoch, and transition history.

## Validation matrix

### Passed

- Focused thesis/final-veto suite: **153 passed**
- Persistent continuity alone: **15 passed**
- Persistence suite: **11 passed**
- Final broker transmission boundary: **17 passed**, 4 subtests passed
- Phase-5 suite with only the unrelated Asia floor neutralized in-process:
  **110 passed**
- Parity smoke: **passed**
- SCALP golden: **passed, byte-identical**
- Dual simulation and money-path isolation: **passed**
- Breakout smoke: **passed**
- ORB/backtest adapter: **120 passed**
- Visual Brain 2.0, event gating, multi-instrument, and cost benchmark:
  **132 passed**, 10 subtests passed
- Main Brain cognitive smoke: **passed**
- Prop protection smoke: **passed**
- Training gate smoke: **19 checks passed**
- Training metrics smoke: **40/40 passed**
- Instrument isolation: **passed for SCALP and SWING alias**
- Broker-send smoke: **passed**
- Execution enable/arm/race suites: **143 passed**
  (11 thread warnings retained for separate reliability work)
- Diagnostics/accounting set excluding the known order-flow mismatch:
  **66 passed**, 4 subtests passed
- Learning-score golden: **passed, byte-identical**
- Simulation realism: **passed**
- Home TypeScript check: **passed**
- API TypeScript check: **passed**
- Home tests: **39 passed**
- API tests: **68 passed**
- Home production build: **passed**
- API production build: **passed**
- Journal quarantine, each file in a fresh process:
  - Native Journal API: **27 passed**
  - Phase A: **90 passed**, 4 subtests passed
  - Phase B: **56 passed**
  - Phase C: **53 passed**
  - Phase 7K: **55 passed**
  - Coaching drill-down: **41 passed**
  - Coaching intraday: **86 passed**
  - Coaching correlations: **87 passed**
- Windows-format and backup portability tests: **18 passed**
- Static high-risk `shell=True` / `os.system` scan: **no matches**
- `git diff --check`: **passed**
- Visual app check: login surface rendered and browser console had no errors

### Blocking failures

#### 1. Phase-5 gateway fixture is session-time dependent

Natural isolated run:

```text
29 failed, 81 passed
```

All 29 failures were early safety rejections of the test's Long, zero/missing
Edge Score fixture during the Asia-session window. The production guard behaved
as designed. With only that unrelated constant neutralized in the test process,
the suite passed **110/110**. Application code and runtime safety were not
changed.

Required resolution: make the fixture deterministic by freezing session time or
by supplying a direction/Edge Score that explicitly satisfies unrelated guards.
This belongs to the existing Python safety-suite reliability work.

#### 2. Order Flow test uses the pre-structure-allocation contract

Natural isolated run:

```text
1 failed, 3 passed
expected 35, received 15
```

`compute_trade_edge_components()` is keyed by the canonical
`structure_allocation` component. The failing test supplies
`{"bos_confirmed": true}`, and the display fixture supplies only `bos`; neither
is a scored key anymore. The +15 Order Flow modifier is therefore the only
earned contribution.

Required resolution: reconcile the test and shared gate/display contract, then
rerun diagnostics, parity, SCALP golden, and money-path isolation. This belongs
to the existing order-flow score-mismatch work and is safety-critical.

## Non-blocking harness observation

The repository root build command also attempted the unrelated
`mockup-sandbox` artifact outside its managed workflow and failed because the
required `PORT` injection was absent. The requested product packages were then
built directly and both passed. This does not certify the mockup sandbox and is
not a blocker for the Trading Webhook home/API product.

## Release gate

Do not tag, push, publish, deploy, migrate production, or alter Windows runtime
behavior until both blocking tests are corrected and their relevant focused
matrix is green. Thesis continuity itself is accepted by this audit; the
combined release candidate remains **BLOCKED**.