---
name: Focus vs Mute decoupling (dashboard)
description: Why the dashboard's per-pair "focus" (show/hide) and "mute" (silence Discord) are two independent controls, and the invariants that keep them from interfering.
---

# Focus vs Mute are SEPARATE controls

The dashboard has two per-pair (MGC/MNQ) controls that used to be conflated:

- **Focus** = DISPLAY-ONLY, per-device, `localStorage`. `toggleInstrument()` only
  shows/hides a pair's tab on the current browser. It MUST NOT call `/alerts/mute`
  or touch any server state.
- **Mute** = SERVER-SIDE, GLOBAL. `toggleMute()` → POST `/alerts/mute`
  (`{instrument,muted}`); GET returns `{status:"ok", muted:{MGC,MNQ}}`. Drives the
  server `ALERTS_MUTED` map read by `_alerts_muted()` in the dispatch path. Painted
  by `renderMuteUI()` into `#mute-MGC`/`#mute-MNQ`; hydrated on boot by
  `loadAlertMutes()` into the JS global `MUTE_STATE`.

**Why:** they were merged — unchecking "focus" hid a pair AND globally muted its
Discord alerts server-side, so the operator had a powerful global silence with no
labeled control and no way to hide a pair locally without muting it for everyone.

## Invariants any future change must keep
- Focus never writes server state; mute never writes focus `localStorage`.
- Mute is **in-memory by design**: a restart/republish resets to all-UNMUTED.
  This is the fail-safe default — a stale silent mute that drops real trade alerts
  forever is worse for a trading tool than re-enabling alerts on restart. Do NOT
  "fix" this by persisting mute unless you also add an expiry/visible warning.
- Mute only suppresses NEW-SETUP alerts (READY card/@everyone, A+ mirror, EARLY,
  tiered WATCH/ARMED, new-entry journal embed, zone-mitigated notice). Lifecycle/
  outcome/execution alerts for an ALREADY-ACTIVE position are NEVER muted.
- `toggleMute()` paints optimistically then reverts on non-`ok`/failure; the server
  response is authoritative (re-assign `MUTE_STATE` from `d.muted`).
- POST `/alerts/mute` is owner-only (Basic Auth) + same-origin CSRF — a bare curl
  POST without an `Origin`/`Referer` matching Host is correctly 403'd; GET isn't.
