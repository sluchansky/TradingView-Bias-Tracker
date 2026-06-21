---
name: Dashboard inline-JS backslash-escape bug
description: A single-backslash \n/\t/\r inside the dashboard's plain triple-quoted HTML string silently breaks the ENTIRE inline script; how to detect it.
---

The dashboard HTML is a **plain** (not raw) Python triple-quoted string: `html = """..."""`.
Python therefore interprets backslash escapes *inside it before it ever reaches the browser*.

**The trap:** writing a JS string literal like `s += '\n⚠ '` with a SINGLE backslash.
Python converts `\n` → a real newline, `\t` → a real tab, `\r` → CR. A raw newline
inside a single/double-quoted JS string is a **SyntaxError: Invalid or unexpected token**,
which aborts parsing of the *whole* `<script>` block. Result: every `onclick` handler is
undefined and the `/status` poller never starts — the user sees "none of the buttons work"
and "the page doesn't update until I refresh," even though only one render helper was edited.

**Why it hides:** `py_compile` PASSES (the Python is valid), and the bug only surfaces in the
*served* bytes. This is the same class of failure as the emoji surrogate-escape bug.

**The fix:** double the backslash in the Python source — `'\\n⚠ '` — so the browser receives
a valid JS `\n`. Same for `\\t`, `\\r`. NOTE: `\uXXXX` BMP escapes (e.g. `\u2713`, `\u26a0`)
are FINE — Python turns them into the literal glyph, which is valid JS string content; do NOT
"fix" those. Only single-backslash `n`/`t`/`r`/`b`/`f`/`v`/`0` are dangerous.

**How to apply / detect:**
- After editing any dashboard JS, EXTRACT the served `<script>` blocks and run `node --check` on
  each — py_compile is insufficient. Quick recipe:
  `curl -s localhost:8000/dashboard` → regex out `<script>...</script>` → `node --check` each block.
- To scan the source: within the dashboard string range, grep for `(?<!\\)\\[ntrbfv0]` — any hit
  inside a JS string literal must be doubled.
