"""Tradovate REST execution layer for the AI Trading Partner webhook server.

Self-contained client + order layer. Imports only ``requests`` + stdlib so it
can be dropped next to ``app.py`` and imported as ``tradovate``.

Safety model (real money is involved):
  * Execution is OFF by default and the environment defaults to DEMO (paper).
    Nothing here runs unless the operator explicitly turns execution on AND
    Tradovate credentials are present.
  * Every programmatic order carries ``isAutomated: True`` (regulatory req).
  * A broker rejection ALWAYS returns ``{"ok": False, ...}`` — it is never
    massaged into a false success.
  * Secret values are never logged or echoed back to callers.

The networked paths (auth / account / contract / orders) are validated
end-to-end by the ``/broker/test`` self-test once real demo credentials are
present; the pure helpers (tick rounding, two-target split, contract maturity
parsing) are unit-tested offline.
"""

import os
import math
import time
import logging
import threading
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

DEMO_BASE = "https://demo.tradovateapi.com/v1"
LIVE_BASE = "https://live.tradovateapi.com/v1"
APP_VERSION = "1.0"
HTTP_TIMEOUT = 12          # seconds per request
CONTRACT_TTL = 6 * 3600    # re-resolve front-month contracts at least every 6h
TOKEN_SKEW = 60            # renew/re-auth this many seconds before expiry

# Per-instrument tick size + product root used for rounding and contract lookup.
_TICK = {"MNQ": 0.25, "MGC": 0.1}
_PRODUCT = {"MNQ": "MNQ", "MGC": "MGC"}

_SECRET_KEYS = (
    "TRADOVATE_USERNAME", "TRADOVATE_PASSWORD", "TRADOVATE_CID",
    "TRADOVATE_SEC", "TRADOVATE_APP_ID", "TRADOVATE_DEVICE_ID",
)

# Futures month codes -> calendar month, for maturity sorting.
_MONTH_CODE = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
               "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}

# ── Module state (guarded by locks) ────────────────────────────────────────
_token_lock = threading.Lock()
_order_lock = threading.Lock()
_token = {"access": None, "exp": 0.0}     # exp is epoch seconds
_auth_backoff_until = 0.0                  # do not attempt auth before this epoch
_account = None                            # {"id", "spec", "name"}
_contracts = {}                            # sym -> {"id", "name", "exp"}
_conn_cache = {"ts": 0.0, "result": None}  # cached self-test for /broker/status

# Runtime execution toggle. Initialised from env; flips at runtime via
# set_execution(); reverts to the env default on process restart (safer).
_exec_state = os.getenv("TRADOVATE_LIVE_EXECUTION", "off").strip().lower() in (
    "on", "true", "1", "yes",
)


# ── Config helpers ──────────────────────────────────────────────────────────
def _env(key, default=""):
    return os.getenv(key, default)


def missing_secrets():
    return [k for k in _SECRET_KEYS if not os.getenv(k)]


def creds_present():
    return not missing_secrets()


def is_live_env():
    return _env("TRADOVATE_ENV", "demo").strip().lower() == "live"


def env_name():
    return "live" if is_live_env() else "demo"


def base_url():
    return LIVE_BASE if is_live_env() else DEMO_BASE


def max_contracts():
    try:
        return max(1, int(_env("TRADOVATE_MAX_CONTRACTS", "2")))
    except ValueError:
        return 2


def execution_on():
    """The runtime toggle. Actual placement also re-checks creds_present()."""
    return _exec_state


def mode_label():
    if not _exec_state:
        return "Tracking-only"
    return "LIVE · Real" if is_live_env() else "LIVE · Demo"


def exec_secret_configured():
    """A server-side TRADOVATE_EXEC_SECRET is required to authorise any broker
    action — without it live execution cannot be enabled or fire."""
    return bool(_env("TRADOVATE_EXEC_SECRET"))


def set_execution(on):
    """Flip the runtime execution toggle. Enabling requires both credentials and
    a configured TRADOVATE_EXEC_SECRET (the per-request authorisation key)."""
    global _exec_state
    on = bool(on)
    if on and not creds_present():
        return {"ok": False,
                "error": "Cannot enable live execution — Tradovate credentials are not configured.",
                "missing": missing_secrets()}
    if on and not exec_secret_configured():
        return {"ok": False,
                "error": "Cannot enable live execution — TRADOVATE_EXEC_SECRET is not set. "
                         "Set it first so broker actions require an authorisation key."}
    _exec_state = on
    if on:
        _conn_cache["result"] = None  # force a fresh self-test on next status
    return {"ok": True, "execution_on": _exec_state,
            "mode_label": mode_label(), "env": env_name()}


def _norm(sym):
    return "MNQ" if "MNQ" in str(sym).upper() else "MGC"


def _round(sym, price):
    """Round a price to the instrument tick (Tradovate rejects off-tick prices)."""
    if price is None:
        return None
    tick = _TICK[_norm(sym)]
    return round(round(float(price) / tick) * tick, 4)


def _opp(action):
    return "Sell" if action == "Buy" else "Buy"


# ── Contract maturity parsing (pure, offline-testable) ──────────────────────
def _decode_year(yr_str):
    try:
        y = int(yr_str)
    except (ValueError, TypeError):
        return 9999
    if len(str(yr_str)) >= 2:
        return 2000 + y if y < 100 else y
    cur = datetime.now(timezone.utc).year
    decade = (cur // 10) * 10
    cand = decade + y
    if cand < cur:
        cand += 10  # single-digit year already rolled past -> next decade
    return cand


def _maturity_key(name, root):
    """Sort key (year, month) parsed from a contract name like 'MNQM6'."""
    tail = str(name)[len(root):]
    if not tail:
        return (9999, 99)
    month = _MONTH_CODE.get(tail[0].upper(), 99)
    return (_decode_year(tail[1:]), month)


def _front_month(candidates, root):
    return sorted(candidates, key=lambda c: _maturity_key(c.get("name", ""), root))[0]


def _split_legs(n, t1, t2):
    """Two-target mapping: 1 contract -> single bracket to T1; 2+ -> split
    (ceil to T1, floor to T2). Returns a list of (qty, target_price)."""
    if n >= 2 and t2 is not None:
        q1 = math.ceil(n / 2)
        q2 = n - q1
        legs = [(q1, t1)]
        if q2 > 0:
            legs.append((q2, t2))
        return legs
    return [(n, t1)]


# ── Auth + low-level API ────────────────────────────────────────────────────
def _auth_headers():
    return {"Authorization": f"Bearer {_token['access']}",
            "Content-Type": "application/json"}


def _parse_exp(s):
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, TypeError):
        return time.time() + 70 * 60


def _do_auth():
    """Full credential auth. Sets _token or an _auth_backoff_until window."""
    global _auth_backoff_until
    body = {
        "name": _env("TRADOVATE_USERNAME"),
        "password": _env("TRADOVATE_PASSWORD"),
        "appId": _env("TRADOVATE_APP_ID"),
        "appVersion": APP_VERSION,
        "cid": _env("TRADOVATE_CID"),
        "sec": _env("TRADOVATE_SEC"),
        "deviceId": _env("TRADOVATE_DEVICE_ID"),
    }
    try:
        r = requests.post(base_url() + "/auth/accesstokenrequest",
                          json=body, timeout=HTTP_TIMEOUT)
    except requests.RequestException as exc:
        _auth_backoff_until = time.time() + 30
        return False, f"auth request failed: {exc.__class__.__name__}"

    try:
        d = r.json()
    except ValueError:
        _auth_backoff_until = time.time() + 30
        return False, f"auth returned non-JSON (HTTP {r.status_code})"

    if d.get("p-captcha"):
        _auth_backoff_until = time.time() + 300
        return False, ("Tradovate requires a captcha — log in once on the "
                       "Tradovate website, then retry.")
    if d.get("p-ticket"):
        wait = int(d.get("p-time", 60) or 60)
        _auth_backoff_until = time.time() + wait
        return False, f"Tradovate auth penalty active — retry in ~{wait}s."

    tok = d.get("accessToken")
    if not tok:
        _auth_backoff_until = time.time() + 30
        msg = d.get("errorText") or d.get("errmsg") or f"HTTP {r.status_code}"
        return False, f"auth failed: {msg}"

    _token["access"] = tok
    _token["exp"] = _parse_exp(d.get("expirationTime"))
    logger.info("Tradovate auth OK (%s) — token valid ~%dm",
                env_name(), int((_token["exp"] - time.time()) / 60))
    return True, None


def _renew():
    try:
        r = requests.get(base_url() + "/auth/renewaccesstoken",
                         headers=_auth_headers(), timeout=HTTP_TIMEOUT)
        d = r.json()
    except (requests.RequestException, ValueError):
        return False
    tok = d.get("accessToken")
    if tok:
        _token["access"] = tok
        _token["exp"] = _parse_exp(d.get("expirationTime"))
        return True
    return False


def _ensure_token():
    """Return (ok, err). Renews/re-auths as needed; respects backoff window."""
    with _token_lock:
        now = time.time()
        if _token["access"] and now < _token["exp"] - TOKEN_SKEW:
            return True, None
        if now < _auth_backoff_until:
            return False, "auth backoff active after a recent failure; retry shortly"
        if _token["access"] and now < _token["exp"] and _renew():
            return True, None
        return _do_auth()


def _api(method, path, body=None):
    """Authenticated request. Returns (json_or_none, err_or_none).
    Re-authenticates once on a 401."""
    ok, err = _ensure_token()
    if not ok:
        return None, err
    url = base_url() + path
    try:
        r = requests.request(method, url, headers=_auth_headers(),
                             json=body, timeout=HTTP_TIMEOUT)
    except requests.RequestException as exc:
        return None, f"network error: {exc.__class__.__name__}"

    if r.status_code == 401:
        with _token_lock:
            _token["access"] = None
        ok, err = _ensure_token()
        if not ok:
            return None, err
        try:
            r = requests.request(method, url, headers=_auth_headers(),
                                 json=body, timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:
            return None, f"network error: {exc.__class__.__name__}"

    try:
        d = r.json()
    except ValueError:
        d = None
    if r.status_code >= 400:
        msg = (d.get("errorText") if isinstance(d, dict) else None) or f"HTTP {r.status_code}"
        return None, msg
    return d, None


# ── Account + contract resolution ───────────────────────────────────────────
def _resolve_account():
    global _account
    if _account:
        return _account, None
    d, err = _api("GET", "/account/list")
    if err:
        return None, err
    if not isinstance(d, list) or not d:
        return None, "no Tradovate accounts found for these credentials"
    spec = _env("TRADOVATE_ACCOUNT_SPEC")
    chosen = None
    if spec:
        chosen = next((a for a in d if a.get("name") == spec), None)
        if not chosen:
            return None, f"account spec '{spec}' not found"
    else:
        chosen = d[0]
    _account = {"id": chosen.get("id"), "spec": chosen.get("name"),
                "name": chosen.get("name")}
    return _account, None


def _resolve_contract(sym):
    sym = _norm(sym)
    now = time.time()
    cached = _contracts.get(sym)
    if cached and now < cached["exp"]:
        return cached, None

    override = _env(f"TRADOVATE_{sym}_CONTRACT")
    if override:
        d, err = _api("GET", f"/contract/find?name={override}")
        if err or not isinstance(d, dict) or not d.get("id"):
            return None, err or f"contract '{override}' not found"
        rec = {"id": d["id"], "name": d.get("name", override), "exp": now + CONTRACT_TTL}
        _contracts[sym] = rec
        return rec, None

    root = _PRODUCT[sym]
    d, err = _api("GET", f"/contract/suggest?t={root}&l=10")
    if err:
        return None, err
    if not isinstance(d, list) or not d:
        return None, f"no contracts suggested for {sym}"
    cands = [c for c in d if str(c.get("name", "")).startswith(root)] or d
    chosen = _front_month(cands, root)
    rec = {"id": chosen.get("id"), "name": chosen.get("name"), "exp": now + CONTRACT_TTL}
    _contracts[sym] = rec
    return rec, None


# ── Order helpers ───────────────────────────────────────────────────────────
def _order_ids(d):
    if not isinstance(d, dict):
        return d
    return {k: d.get(k) for k in ("orderId", "oso1Id", "oso2Id", "ocoId")
            if d.get(k) is not None}


def _place_oso(account, contract, action, qty, stop_price, target_price):
    """One OSO: a market entry that, on fill, sends an OCO stop+target pair."""
    body = {
        "accountSpec": account["spec"],
        "accountId": account["id"],
        "action": action,
        "symbol": contract["name"],
        "orderQty": int(qty),
        "orderType": "Market",
        "isAutomated": True,
        "bracket1": {"action": _opp(action), "orderType": "Stop",
                     "stopPrice": stop_price, "isAutomated": True},
        "bracket2": {"action": _opp(action), "orderType": "Limit",
                     "price": target_price, "isAutomated": True},
    }
    return _api("POST", "/order/placeoso", body)


def _list_working(account_id, contract_id):
    """Working orders for a contract, joined with their latest orderVersion so
    callers can see orderType / stopPrice / orderQty."""
    orders, err = _api("GET", "/order/list")
    if err:
        return None, err
    versions, _ = _api("GET", "/orderVersion/list")
    vmap = {}
    for v in (versions or []):
        oid = v.get("orderId")
        if oid is None:
            continue
        if oid not in vmap or v.get("id", 0) > vmap[oid].get("id", 0):
            vmap[oid] = v
    out = []
    for o in (orders or []):
        if o.get("accountId") != account_id or o.get("contractId") != contract_id:
            continue
        if o.get("ordStatus") not in ("Working", "Pending", "Accepted", "Suspended"):
            continue
        v = vmap.get(o.get("id"), {})
        out.append({**o, "orderType": v.get("orderType"),
                    "stopPrice": v.get("stopPrice"), "price": v.get("price"),
                    "orderQty": v.get("orderQty")})
    return out, None


def _cancel_working(account_id, contract_id):
    """Cancel every working order for the contract. Returns
    ``(cancelled, failed, list_err)``. The caller must fail hard if it cannot
    even *list* working orders (``list_err``) or if any cancel failed —
    treating either as success could leave a working stop/limit on a flat
    position that later triggers and opens a new unintended position."""
    working, err = _list_working(account_id, contract_id)
    if err:
        return 0, 0, err
    cancelled = 0
    failed = 0
    for o in working:
        _, cerr = _api("POST", "/order/cancelorder", {"orderId": o.get("id")})
        if cerr:
            failed += 1
        else:
            cancelled += 1
    return cancelled, failed, None


def _position_qty(account_id, contract_id):
    d, err = _api("GET", "/position/list")
    if err or not isinstance(d, list):
        return 0
    for p in d:
        if p.get("accountId") == account_id and p.get("contractId") == contract_id:
            return p.get("netPos", 0) or 0
    return 0


# ── Public execution API ────────────────────────────────────────────────────
def place_bracket(direction, sym, entry, stop, t1, t2, contracts):
    """Place a market-entry bracket (entry + OCO stop/target). Two targets are
    split across contracts when contracts >= 2. Returns a structured dict;
    ``ok`` is False on any rejection (and ``orders`` lists what *did* place, so
    a partial fill is never hidden)."""
    if not creds_present():
        return {"ok": False, "error": "Tradovate credentials not configured",
                "missing": missing_secrets()}

    sym = _norm(sym)
    action = "Buy" if str(direction).lower().startswith("l") else "Sell"
    try:
        n = int(contracts)
    except (TypeError, ValueError):
        n = 1
    n = max(1, min(n, max_contracts()))

    stop_r = _round(sym, stop)
    t1_r = _round(sym, t1)
    t2_r = _round(sym, t2) if t2 is not None else None
    if stop_r is None or t1_r is None:
        return {"ok": False, "error": "missing stop/target price"}

    with _order_lock:
        acct, err = _resolve_account()
        if err:
            return {"ok": False, "error": f"account: {err}"}
        con, err = _resolve_contract(sym)
        if err:
            return {"ok": False, "error": f"contract: {err}"}

        placed = []
        for qty, tgt in _split_legs(n, t1_r, t2_r):
            d, err = _place_oso(acct, con, action, qty, stop_r, tgt)
            if err:
                return {"ok": False, "error": err, "orders": placed,
                        "partial": bool(placed),
                        "contracts": sum(p["qty"] for p in placed),
                        "account": acct["spec"],
                        "contract": con["name"], "env": env_name()}
            placed.append({"qty": qty, "target": tgt, "ids": _order_ids(d)})

        logger.info("Tradovate bracket placed (%s): %s %s x%d @stop %s",
                    env_name(), action, con["name"], n, stop_r)
        return {"ok": True, "orders": placed, "action": action,
                "account": acct["spec"], "accountId": acct["id"],
                "contract": con["name"], "contractId": con["id"],
                "stop": stop_r, "contracts": n, "env": env_name()}


def flatten(sym):
    """Cancel working orders for the instrument and liquidate any open position."""
    if not creds_present():
        return {"ok": False, "error": "Tradovate credentials not configured"}
    sym = _norm(sym)
    with _order_lock:
        acct, err = _resolve_account()
        if err:
            return {"ok": False, "error": f"account: {err}"}
        con, err = _resolve_contract(sym)
        if err:
            return {"ok": False, "error": f"contract: {err}"}
        cancelled, cancel_failed, list_err = _cancel_working(acct["id"], con["id"])
        if list_err:
            return {"ok": False,
                    "error": f"could not list working orders ({list_err}) — "
                             "position NOT flattened; review the account manually",
                    "contract": con["name"], "env": env_name()}
        if cancel_failed:
            return {"ok": False,
                    "error": f"could not cancel {cancel_failed} working order(s) — "
                             "position NOT flattened; review the account manually",
                    "cancelled": cancelled, "cancel_failed": cancel_failed,
                    "contract": con["name"], "env": env_name()}
        pos = _position_qty(acct["id"], con["id"])
        liquidated = False
        if pos:
            _, err = _api("POST", "/order/liquidateposition",
                          {"accountId": acct["id"], "contractId": con["id"],
                           "admin": False})
            if err:
                return {"ok": False, "error": f"liquidate: {err}",
                        "cancelled": cancelled}
            liquidated = True
        logger.info("Tradovate flatten (%s): %s cancelled=%d pos=%s",
                    env_name(), con["name"], cancelled, pos)
        return {"ok": True, "cancelled": cancelled, "liquidated": liquidated,
                "position": pos, "contract": con["name"], "env": env_name()}


def move_stop_to_breakeven(sym, entry_price):
    """Modify every working stop order for the instrument to the entry price."""
    if not creds_present():
        return {"ok": False, "error": "Tradovate credentials not configured"}
    sym = _norm(sym)
    with _order_lock:
        acct, err = _resolve_account()
        if err:
            return {"ok": False, "error": f"account: {err}"}
        con, err = _resolve_contract(sym)
        if err:
            return {"ok": False, "error": f"contract: {err}"}
        be = _round(sym, entry_price)
        working, err = _list_working(acct["id"], con["id"])
        if err:
            return {"ok": False, "error": err}
        stops = [o for o in working if o.get("orderType") == "Stop"]
        if not stops:
            return {"ok": False, "error": "no working stop order found to modify"}
        modified = []
        for o in stops:
            body = {"orderId": o.get("id"), "orderType": "Stop",
                    "stopPrice": be, "isAutomated": True}
            if o.get("orderQty") is not None:
                body["orderQty"] = o.get("orderQty")
            _, err = _api("POST", "/order/modifyorder", body)
            if err:
                return {"ok": False, "error": err, "modified": modified}
            modified.append(o.get("id"))
        logger.info("Tradovate breakeven (%s): %s -> %s (%d stops)",
                    env_name(), con["name"], be, len(modified))
        return {"ok": True, "breakeven": be, "modified": modified,
                "contract": con["name"], "env": env_name()}


# ── Status / self-test ──────────────────────────────────────────────────────
def self_test():
    """Auth + account + contract resolution end to end. Places no orders."""
    if not creds_present():
        return {"ok": False, "stage": "config",
                "error": "credentials not set", "missing": missing_secrets()}
    ok, err = _ensure_token()
    if not ok:
        return {"ok": False, "stage": "auth", "error": err}
    acct, err = _resolve_account()
    if err:
        return {"ok": False, "stage": "account", "error": err}
    out = {"ok": True, "env": env_name(), "account": acct["spec"], "contracts": {}}
    for s in ("MGC", "MNQ"):
        con, err = _resolve_contract(s)
        out["contracts"][s] = con["name"] if not err else f"error: {err}"
    return out


def status_snapshot(force=False):
    """Config + cached connection state for the dashboard. The dashboard polls
    this, so the live broker self-test only runs on first use or when forced
    (avoids a broker round-trip every few seconds)."""
    snap = {
        "env": env_name(),
        "live_env": is_live_env(),
        "execution_on": execution_on(),
        "creds_present": creds_present(),
        "missing_secrets": missing_secrets(),
        "max_contracts": max_contracts(),
        "can_enable": creds_present() and exec_secret_configured(),
        "mode_label": mode_label(),
        "exec_secret_required": True,
        "exec_secret_configured": exec_secret_configured(),
    }
    if force or (creds_present() and _conn_cache["result"] is None):
        _conn_cache["result"] = self_test()
        _conn_cache["ts"] = time.time()
    snap["connection"] = _conn_cache["result"]
    snap["connection_checked_at"] = _conn_cache["ts"] or None
    return snap
