---
name: Astral emoji as \u surrogate escapes → runtime 500
description: Why an emoji written as a UTF-16 surrogate-pair \uXXXX escape inside a Python string passes py_compile but 500s when Flask encodes the response, and how to avoid it.
---

Writing an astral (non-BMP) emoji as a UTF-16 **surrogate-pair** escape inside a Python
string — e.g. `'\ud83e\uddea'` for 🧪 or `'\ud83d\udccb'` for 📋 — compiles into two
**lone surrogate** codepoints. This passes `python -m py_compile` (valid string literal)
but throws `UnicodeEncodeError: 'utf-8' codec can't encode ... surrogates not allowed`
the moment the string is UTF-8 encoded — e.g. when Flask builds the HTTP response — giving
a runtime **500 only on the route that serves that string** (here `/dashboard`).

**Why it's sneaky:** grepping the source for surrogate *characters* (even with
`errors='surrogatepass'`) finds nothing, because on disk the bytes are the literal ASCII
text `\ud83e...`; the surrogates only exist after Python compiles the `\u` escapes. So the
file looks clean while the loaded module is broken.

**How to apply:**
- In Python source, never represent an astral emoji as a `\uXXXX\uXXXX` surrogate pair.
  Use the actual glyph (`'🧪'`), or `'\U0001F9EA'` (capital-U 8-digit), or a BMP char
  like `'\u26a0'` (⚠, which is single-codepoint and encodes fine).
- `py_compile` is NOT sufficient to validate a route that returns a big HTML/JS string.
  Smoke-test the actual route over HTTP (`curl /dashboard`) or render it in-process and
  call `.encode('utf-8')` — a 500 here is an encode error, not a logic error.
- Quick locate: `rg -nF 'ud83'` (the hex tail is plain ASCII in the file).
