---
name: Trend brake (don't-fight-the-trend)
description: Flag-gated demote-only money-path veto that blocks a setup fighting the real-price higher-timeframe trend; why it exists and its non-obvious constraints.
---

# Trend brake (TREND_BRAKE_ENABLED)

Demote-only, fail-OPEN money-path veto: an actionable LONG is demoted to WAIT (null
trade plan) when BOTH independent real-price signals oppose it — 1H AND 4H HTF bias ==
"bear" AND price < VWAP (fresh) — mirror for SHORT. Default OFF → byte-identical
goldens; env `TREND_BRAKE_ENABLED=1` arms it. Lives at the full_analysis veto seam
right after the swing_strategy_filter block, alongside the scalp/swing/strategy demote
blocks (same WAIT + null-plan mutation so is_actionable flips and the gateway can't act).

**Default changed to ON (2026-07-27):** the flag now defaults to ON. Goldens pin it OFF with `TREND_BRAKE_ENABLED=0` in check_scalp_golden.sh so they stay byte-identical. Env `TREND_BRAKE_ENABLED=0` disables it. The trend_brake_smoke.py ON-path guard still validates the veto logic.

**Why:** the inbound TradingView alert feed can be one-sided (all bullish/demand), so
the bot forms only LONG setups and takes them into falling markets (root cause of "bot
training mode is doing awful": 23 long / 1 short in a day). Alert-derived bias is
therefore useless as a safety signal — the brake keys off REAL price instead (auto HTF
bar fetch that fills HTF_STATE_BY_INST source="auto" + the auto VWAP feed), which no
alert-setup gap can bias.

**How to apply / invariants:**
- Trust HTF bias ONLY when `freshness[tf].source == "auto"` AND not stale — a
  chart/alert-tagged HTF overlay (the P3 inbound-HTF path) or a stale record is ignored
  (fail open). This is what keeps the brake independent of the alert stream; do NOT
  loosen it to accept chart/alert sources. Read the gated bias from the SAME freshness
  record you source/stale-validated (not the top-level bias_1h/4h) so they can't drift.
- SCALP has no swing_ctx (it's SWING-only), so the seam computes its own via
  compute_swing_context(); AND `_refresh_htf_if_due` must include the brake flag in its
  guard, or SCALP + MI-off never fetches HTF and the brake is silently inert (this was
  the architect's initial FAIL).
- FAIL-OPEN by design (unlike the neighbouring fail-CLOSED mode vetoes): a bug or any
  missing/undecided/non-auto/stale signal must never start blocking trades. Requires
  BOTH signals to oppose so a healthy pullback long (below VWAP in a bull HTF trend) is
  NOT blocked.
- Goldens run flag-OFF (byte-identical); the ON-path logic is guarded by its own smoke
  (`.local/state/trend_brake_smoke.py` / `check_trend_brake.sh`), not the goldens.
