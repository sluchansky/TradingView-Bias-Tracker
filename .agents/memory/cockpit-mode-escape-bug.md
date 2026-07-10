---
name: Cockpit Mode backslash-n escape bug
description: JS string literals in Python triple-quoted strings need double-backslash for \n or they become raw newlines
---

## The Rule

Any `\n` inside a JavaScript string literal (e.g., `join('\n')` or `'text.\n\nMore text'`) that lives inside a Python triple-quoted string (`"""..."""`) will be evaluated by Python as a raw newline character (0x0A) when Flask renders the page. That raw newline breaks the JS string literal, causing a `SyntaxError: Invalid or unexpected token` at the character after the opening `'`.

**Why:** Python's triple-quoted strings still process backslash escapes. `\n` → newline, `\t` → tab, `\u2026` → `…`. The JS is inside such a string, so all JS escape sequences are interpreted by Python before the browser ever sees them.

## Fix

Use double-backslash in the Python source: `\\n` in the Python file → Python evaluates to the two-char sequence `\n` → JS sees `\n` = valid newline escape.

**Critical:** The edit tool JSON parameter also interprets `\n` as a real newline. To get `\\n` in the Python source via the edit tool, you'd need `\\\\n` in the JSON. This is error-prone — prefer a Python `bytes.replace()` script instead.

## Safe Fix Script Pattern

```python
path = "artifacts/tradingview-webhook/app.py"
with open(path, "rb") as f:
    src = f.read()

# Find the section boundaries
s = src.find(b"SECTION_START_MARKER")
e = src.find(b"SECTION_END_MARKER") + len(b"SECTION_END_MARKER")
section = src[s:e]

PLACEHOLDER = b'\x00DOUBLEN\x00'
DOUBLE_BSN  = b'\\\\n'   # bytes: 0x5C 0x5C 0x6E
SINGLE_BSN  = b'\\n'    # bytes: 0x5C 0x6E

# 1. Protect already-correct double-backslash-n
tmp = section.replace(DOUBLE_BSN, PLACEHOLDER)
# 2. Replace remaining single-backslash-n with double
tmp2 = tmp.replace(SINGLE_BSN, DOUBLE_BSN)
# 3. Restore
fixed = tmp2.replace(PLACEHOLDER, DOUBLE_BSN)

src2 = src[:s] + fixed + src[e:]
with open(path, "wb") as f:
    f.write(src2)
```

## How to Apply

- Any time you add new JS code to the dashboard (via the edit tool or write tool), scan for `\n` in string literals
- The golden tests (dual_sim, breakout_mode) node-check the served script — they'll catch this immediately
- The error appears as: `SyntaxError: Invalid or unexpected token` with an arrow pointing to the character after an opening `'` that's immediately followed by a newline in the extracted script
- py_compile DOES NOT catch this (it's a Python-valid file with the wrong output)

## Other Affected Sequences

Same issue applies to `\t`, `\r`, `\uXXXX` (becomes the literal Unicode char), `\xXX` in Python triple-quoted strings. For dashboard JS, always double the backslash or use `String.fromCharCode(N)` / `String.fromCharCode(9)` etc.
