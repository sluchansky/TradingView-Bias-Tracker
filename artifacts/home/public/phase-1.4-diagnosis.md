# Phase 1.4 Advanced View Failure — Diagnosis Report

## Root Cause

`initUnifiedDash()` unconditionally sets `data-unified="1"` on `<html>`, which
is a guard condition inside the Advanced View CSS rule — bypassing the entire
rule permanently.

---

## Evidence

### Step 1 — Server renders UNIFIED_DASH = true by default

`app.py` line 1779:
```python
UNIFIED_DASHBOARD_ENABLED = os.environ.get("UNIFIED_DASHBOARD_ENABLED", "1").strip() == "1"
```

Default is `"1"`. Line 54055 injects it into the served HTML:
```python
html = html.replace("__UNIFIED_DASH__", "1" if UNIFIED_DASHBOARD_ENABLED else "0")
```

The browser receives:
```js
var UNIFIED_DASH = ('1' === '1');  // → true
```

### Step 2 — initUnifiedDash() immediately sets data-unified="1" on `<html>`

`app.py` lines 50941–50944:
```js
function initUnifiedDash() {
  if (!UNIFIED_DASH) return;
  // Set data-unified="1" on <html> so the Advanced-panels CSS gate defers to
  // ln-hidden (section nav) instead of its own show/hide list.
  document.documentElement.setAttribute('data-unified', '1');
  ...
  setLiveSection(saved);
}
```

This runs on every page load. `data-unified="1"` is permanently present on `<html>`.

### Step 3 — The CSS rule had :not([data-unified="1"]) as a guard

`app.py` line 42053 (Phase 1.4 as-written):
```css
html:not([data-adv="1"]):not([data-unified="1"]) #view-live .mod:not(...):not(.mb-hidden){display:none !important}
```

The selector required `<html>` to have **neither** `data-adv="1"` **nor**
`data-unified="1"`. Since `data-unified="1"` is always present,
`html:not([data-unified="1"])` **never matched**. The rule fired zero times
regardless of the toggle state.

---

## Toggle State at Runtime (Advanced View OFF)

| Item | Value |
|---|---|
| `document.documentElement.dataset.adv` | `"0"` (toggle works correctly) |
| `document.documentElement.dataset.unified` | `"1"` (always set by initUnifiedDash) |
| `localStorage.getItem('dashAdv')` | `"0"` |
| `#adv-toggle` class | no `.on` class |
| CSS rule selector match | **NO** — `html:not([data-unified="1"])` fails |

---

## The CSS Rule That Should Have Hidden Panels

```css
html:not([data-adv="1"]):not([data-unified="1"]) #view-live .mod:not(...):not(.mb-hidden){display:none !important}
```

The `:not([data-unified="1"])` clause caused the rule to never apply when
UNIFIED_DASH is enabled.

---

## The Mechanism Preventing Hiding

When `initUnifiedDash()` calls `setLiveSection(sec)`, that function controls
panel visibility via the `.ln-hidden` class only:

```js
if (active.indexOf(id) !== -1) { el.classList.remove('ln-hidden'); }
else                            { el.classList.add('ln-hidden'); }
```

`.ln-hidden` is:
```css
.ln-hidden { display:none !important }
```

This only hides panels in **inactive sections**. ADVANCED panels registered in
the active section have `.ln-hidden` removed, making them fully visible. No
mechanism hid them based on their ADVANCED category.

### Computed display per ADVANCED panel when Advanced View was OFF

| Panel | Computed display | Why |
|---|---|---|
| mod-swingstrat | `block` | In active section, ln-hidden removed, CSS rule bypassed |
| mod-strategy | `none` | `style="display:none"` from server-side flag gate |
| mod-sessionq | `none` | `style="display:none"` from server-side flag gate |
| mod-trademgmt | `none` | `style="display:none"` from server-side flag gate |
| mod-training | `none` | `style="display:none"` from server-side flag gate |
| mod-report | `none` | `.mb-hidden { display:none !important }` |
| mod-analyst | `none` | `.mb-hidden { display:none !important }` |
| mod-pro | `none` | `.mb-hidden { display:none !important }` |
| mod-entryq | `none` | `.mb-hidden { display:none !important }` |
| mod-debate | `none` | `.mb-hidden { display:none !important }` |
| mod-learning | `none` | `.mb-hidden { display:none !important }` |
| mod-memory | `none` | `.mb-hidden { display:none !important }` |
| mod-governor | `none` | `.mb-hidden { display:none !important }` |

The user observed "all panels visible" because the one non-mb-hidden,
non-flag-gated ADVANCED panel — `mod-swingstrat` — was fully visible and
the toggle had zero effect on it.

---

## Fix Applied

Removed `:not([data-unified="1"])` from the CSS rule at `app.py` line 42053.

**Before:**
```css
html:not([data-adv="1"]):not([data-unified="1"]) #view-live .mod:not(...):not(.mb-hidden){display:none !important}
```

**After:**
```css
html:not([data-adv="1"]) #view-live .mod:not(...):not(.mb-hidden){display:none !important}
```

The section-nav `.ln-hidden` (`display:none !important`) and the Advanced View
rule (`display:none !important`) are non-conflicting — both `!important` rules
coexist safely. ADVANCED panels are now hidden by the CSS rule whenever
Advanced View is OFF, regardless of which section is active.

All 4 golden tests pass after the fix.
