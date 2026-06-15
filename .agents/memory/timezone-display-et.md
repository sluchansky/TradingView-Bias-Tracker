---
name: ET display vs UTC storage
description: Timestamps are displayed in America/New_York via fmt_et() but stored UTC; Discord embeds' native `timestamp` field must stay ISO-UTC (Discord localizes it per viewer).
---

# Timezone: display ET, store UTC

`artifacts/tradingview-webhook/app.py` stores every timestamp in **UTC**
(`now_utc().isoformat()`) and converts to **US Eastern** only at the display
layer via `fmt_et(value, fmt)` (defined near `now_utc`). `fmt_et` accepts a
datetime *or* an ISO string, treats naive datetimes as UTC, returns `""` for
`None`, and echoes back an unparseable string. `ZoneInfo("America/New_York")`
handles the EST/EDT switch automatically.

**Non-obvious rule — do NOT convert the Discord embed `timestamp` field.** A
Discord embed's top-level `"timestamp"` must stay ISO-8601 UTC; the Discord
client localizes it to each viewer. Only the human-readable text fields/footers
(heartbeat "Last alert" / "Check-in", EOD date + footer, the "Received …" footer,
and the journal card "🕐 Time" field) go through `fmt_et`.

**Why:** keeping storage UTC preserves age math and ordering, and avoids
double-localizing the field Discord already localizes. Convert only at render.

**How to apply:** any new user-visible timestamp text → wrap in `fmt_et`. Any new
Discord embed `"timestamp"` → leave as `now_utc().isoformat()` / stored ISO. The
dashboard HTML and `/status` JSON do not render timestamps; API JSON timestamps
stay ISO-UTC (data contract, not display).
