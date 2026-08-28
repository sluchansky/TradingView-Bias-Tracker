# Windows safe release candidate audit — 2026-08-28

## Verdict

**Native-Windows approved for the exact safe candidate commit:** yes.

The candidate keeps execution, manual orders, the live runner, coordinator
fan-out, live Discord, Visual Brain, Visual Brain benchmarking, and candidate
vision disabled in the supported Windows launchers. The source-only,
frontend/API, focused Python safety, proxy, and Linux startup checks pass.

**Final native-Windows release approval:** PASS for
`9c01bc0a1c513010686f50d1ab4ff26d486ef997`. GitHub Actions run
[`33132928059`](https://github.com/sluchansky/TradingView-Bias-Tracker/actions/runs/33132928059)
completed successfully on `windows-latest`, including the full workspace
typecheck and `scripts/windows/Test-WindowsDashboard.ps1`. The temporary
validation branch was removed after the run; the Actions record remains.

The repository-wide Python collection-order defect is also still present and
already has a separate project task. It does not invalidate the focused release
checks below, but it must be resolved before treating an arbitrary-order full
Python run as a green release gate.

## Frozen candidate scope

- Branch: `replit-dev`
- Base commit inspected: `f4864cd1df0487ee51693bf21244990778b994c7`
- Exact candidate validated on Windows:
  `9c01bc0a1c513010686f50d1ab4ff26d486ef997`
- Initial worktree: clean
- Upstream relation at audit time: 77 commits ahead, 2 commits behind
- Candidate changes in this audit are limited to Windows safety defaults,
  Windows topology tests/documentation, and deterministic regression fixtures.
- The exact candidate was pushed only to a temporary GitHub validation branch
  so the checked-in push workflow could run. The branch was deleted afterward.
  No protected branch moved and no publish, deployment, tag, production access,
  database access, provider access, or execution enablement occurred.

## Safety-default audit

The release-safe Windows launcher now rejects any inherited/local value other
than the following:

| Setting | Required candidate value |
|---|---:|
| `EXECUTION_MODE` | `disabled` |
| `MANUAL_ORDER_ENABLED` | `0` |
| `LIVE_RUNNER_ENABLED` | `0` |
| `CENTRAL_GHOST_COORDINATOR_FANOUT_ENABLED` | `0` |
| `DISCORD_LIVE` | `0` |
| `DISCORD_LIVE_ENABLED` | `0` |
| `REPLIT_DEPLOYMENT` | `0` |
| `VISUAL_BRAIN_ENABLED` | `0` |
| `VISUAL_BRAIN_BENCHMARK_ENABLED` | `0` |
| `VISUAL_BRAIN_BENCHMARK_CANDIDATE_ENABLED` | `0` |

`Start-TradingBot.ps1` and `Start-TradingDashboard.ps1` carry the same paid-
vision-off defaults. `Test-WindowsDashboard.ps1` clears provider, notification,
database, execution, and paid-vision inputs before startup. Databento remains
the only explicit opt-in local-data switch and does not enable execution.

The proxy remains loopback-only and proxy-only. It does not import database or
Object Storage routes, and the launcher refuses occupied owned ports before
starting.

## Validation results

### Package and frontend/mobile

| Check | Result |
|---|---|
| `pnpm install --frozen-lockfile` with pinned pnpm 10.26.1 | PASS |
| Home Vitest suite | PASS — 6 files, 39 tests |
| Responsive operator-console contract | PASS — included in home suite |
| Visual Brain mode display-safety contract | PASS — included in home suite |
| Home TypeScript typecheck | PASS |
| Home Vite production build | PASS |
| API Vitest suite | PASS — 4 files, 66 tests |
| Windows local-topology safety assertions | PASS — included in API suite |
| API TypeScript typecheck | PASS |
| API build, including `dist/windows-local-proxy.mjs` | PASS |
| Full workspace `pnpm run typecheck` | PASS |
| Mockup-sandbox typecheck/build with registered `PORT`/`BASE_PATH` | PASS |

The home build emitted non-fatal sourcemap and large-chunk warnings. They did
not fail the build.

The unauthenticated 390×844 preview rendered the responsive Main Brain access
card without browser-console errors. No dashboard password was read or exposed,
so the authenticated operator surface was not exercised.

### Native Windows exact-candidate release gate

| Check | Result |
|---|---|
| GitHub Actions head SHA | PASS — `9c01bc0a1c513010686f50d1ab4ff26d486ef997` |
| Full workspace typecheck (Windows) | PASS — [job `98726312174`](https://github.com/sluchansky/TradingView-Bias-Tracker/actions/runs/33132928059/job/98726312174) |
| Release-safe Windows dashboard smoke | PASS — [job `98726312306`](https://github.com/sluchansky/TradingView-Bias-Tracker/actions/runs/33132928059/job/98726312306) |
| Direct Flask `/ping` | PASS — HTTP readiness reached with `status=ok` |
| Proxied `/api/ping` | PASS — launcher readiness gate completed before the smoke returned |
| Safe startup environment | PASS — execution, manual orders, live runner, coordinator fan-out, Discord, Visual Brain, candidate benchmarking, Databento, and database access disabled |
| Shutdown | PASS — launcher-owned process tree stopped and owned listeners on 8000, 8080, and 24319 were released; port 3000 is not used by this launcher |

The Windows smoke ran with an isolated Python 3.11 virtual environment and a
fresh frozen pnpm install. Its success line was:
`Windows dashboard smoke passed: /ping reached and launcher-owned process tree stopped cleanly.`
GitHub's final orphan-process cleanup reported no named Python or Node process
requiring termination. The runner emitted a non-fatal warning that older action
releases are being forced onto the hosted runner's Node.js 24 runtime; it did
not affect any job result.

### Trading/safety regression matrix

| Check | Result |
|---|---|
| Registry/resolver parity | PASS — byte-identical baseline |
| SCALP golden | PASS — byte-identical baseline |
| Dual-simulation smoke and served-dashboard JavaScript syntax | PASS |
| Breakout-mode smoke and served-dashboard JavaScript syntax | PASS |
| Visual Brain + MES focused Python suite | PASS — 137 tests, 10 subtests |
| Native journal + authoritative verdict-history focused suite | PASS — 230 tests, 4 subtests |
| Python `compileall` over webhook, scripts, and local state checks | PASS |
| Persistence policy, source-only | PASS — 3 startup files, 1 migration |
| Static Windows safety/topology checklist | PASS — 11 assertions |

Two stale/nondeterministic test fixtures were corrected without changing
runtime behavior:

1. The Visual Brain state-persistence test now supplies the completed bar
   required by event-driven gating.
2. The MES diagnostic fixture pins a non-preferred session instead of allowing
   wall-clock session bonus drift.

### Startup/proxy evidence available in Replit

- Isolated Flask startup with database, Databento, execution, Discord, fan-out,
  and all paid-vision flags disabled: PASS.
- Direct `/ping`: HTTP 200 with `status=ok`.
- Built loopback Windows proxy pointed at that Flask process: PASS.
- Proxied `/api/ping`: HTTP 200 with `status=ok`.
- Test Flask and proxy processes were stopped after the smoke.

The configured development workflow itself was unavailable because its
read-only PostgreSQL reconnect guard returned `OperationalError`; consequently
the normal development `/api/ping` returned 502. This is an environment/database
connectivity blocker, not a Windows launcher or proxy failure. No production
database was accessed.

## Known blockers and limitations

1. **Arbitrary-order full Python collection is not green.** A repository-wide
   `pytest -q` stopped during collection because
   `test_journal_coaching_correlations_7o3.py` and
   `test_phase4_operator_explanation.py` received a Flask object where the
   `app` module was expected. This is already tracked separately.
2. **Configured development database reconnect failed.** Source-only
   persistence policy and database-free startup passed; database repair and
   production access were outside this audit.

## Journal quarantine

No SQL, journal repair, outcome update, reseed, reconciliation, or production
database command was run. Existing `STATUS_UNKNOWN` rows were not modified, and
no execution outcome was fabricated. The Windows smoke explicitly cleared
`DATABASE_URL`, provider keys, Discord webhooks, and live-data inputs.

## Release boundary

The exact candidate commit
`9c01bc0a1c513010686f50d1ab4ff26d486ef997` is approved for the documented
safe Windows startup configuration. This approval does not automatically extend
to later commits. Preserve the required disabled values above; enabling
execution, live alerts, provider access, database access, Visual Brain, or
candidate vision requires a separate reviewed operator decision.