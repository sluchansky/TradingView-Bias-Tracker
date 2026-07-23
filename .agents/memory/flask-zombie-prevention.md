---
name: Flask zombie-prevention pattern
description: How non-daemon Timer threads cause eternal 502s in prod, and the three-guard fix applied to __main__.
---

# Flask Zombie-Prevention: Three Hard-Exit Guards

## The Problem
`threading.Timer()` threads created in `__main__` are **non-daemon** by default.
When `app.run()` exits for any reason (crash, SIGTERM, port error), those threads
keep the Flask **process** alive. `prod-start.sh`'s `wait -n $FLASK_PID $EXPRESS_PID`
never fires → Express never restarts → eternal 502 zombie state (threads alive, no HTTP server).

**Key diagnostic:** If `INFO:werkzeug:` access logs are **absent** but heartbeat/shadow
logs continue → zombie confirmed. Response time 1-4ms in proxy logs = ECONNREFUSED,
not timeout, which means nothing is bound on the target port.

## Root Cause Chain
1. Flask starts, `app.run()` runs (port bound OK)
2. Something crashes Flask's main thread OR SIGTERM arrives for redeploy
3. Main thread exits, Werkzeug socket is GC'd (port freed)
4. Non-daemon Timer threads keep the process alive
5. `wait -n $FLASK_PID` never fires → no restart → 502 forever

**SIGTERM subtlety:** If a Python SIGTERM trampoline is installed but the main thread
is dead, SIGTERM is silently consumed (queued for dead thread, never processed). 
`kill $FLASK_PID` alone cannot kill the zombie in this state — SIGKILL required.

## The Three-Guard Fix (applied to `__main__`)

All three guards are at the start of `if __name__ == "__main__":` (before DB init):

### Guard 1: SIGTERM handler
```python
import signal as _signal
def _sigterm_hard_exit(signum, frame):
    logger.critical("[prod-boot] SIGTERM received — os._exit(0) for clean restart")
    os._exit(0)
_signal.signal(_signal.SIGTERM, _sigterm_hard_exit)
```
**Why:** prod-start.sh's `shutdown()` sends SIGTERM during redeploy. Without this,
SIGTERM is queued for the dead main thread and silently dropped → zombie survives
into the new deployment → EADDRINUSE on the new Flask's `app.run()`.

### Guard 2: sys.excepthook
```python
import sys as _sys
_orig_excepthook = _sys.excepthook
def _main_fatal_hook(exc_type, exc_value, exc_tb):
    try:
        logger.critical("[prod-boot] Fatal unhandled exception: %s: %s", exc_type.__name__, exc_value)
    except Exception:
        pass
    _orig_excepthook(exc_type, exc_value, exc_tb)
    os._exit(1)
_sys.excepthook = _main_fatal_hook
```
**Why:** Any crash in `__main__` before `app.run()` (DB init, config, etc.) would
otherwise exit only the main thread, leaving non-daemon timers running forever.

### Guard 3: app.run() finally block
```python
logger.info("[prod-boot] Flask app.run() starting on port %d", port)
try:
    app.run(host="0.0.0.0", port=port, debug=False)
except Exception as _boot_exc:
    logger.critical("[prod-boot] Flask app.run() raised: %s", _boot_exc, exc_info=True)
finally:
    logger.critical("[prod-boot] Flask server exited — calling os._exit(1) for clean restart")
    os._exit(1)
```
**Why:** `finally` runs even for `SystemExit` (from SIGINT/keyboard interrupt).
The `logger.info` pre-flight log lets deployment logs show whether `app.run()` was
even reached (absent = crash happened before it).

## Diagnosis Checklist for Future 502s
1. Check deployment logs for `INFO:werkzeug:` — absent = zombie or pre-bind crash
2. Check for `[prod-boot]` log lines — absent = crash happened very early
3. Check for `[prod-boot] Flask app.run() starting on port N` — absent = crash in DB init block
4. Check for `[prod-boot] Flask app.run() raised:` — present = port conflict or config error
5. Response time in proxy: 1-4ms = ECONNREFUSED (not listening); >1s = timeout (overloaded)

## How to Apply
**Why:** Any change that adds non-daemon threads to `__main__` must not remove these
guards. The three guards together ensure that no matter where the crash happens —
before, during, or after `app.run()` — the process always dies immediately, allowing
prod-start.sh to detect the exit and trigger a clean restart.
