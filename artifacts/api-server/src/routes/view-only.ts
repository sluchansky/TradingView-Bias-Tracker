import { Router } from "express";
import http from "http";
import {
  viewConfigured,
  verifyLinkToken,
  checkPassword,
  mintSessionCookie,
  verifySessionCookie,
} from "./view-tokens";
import { sameOrigin } from "./dashboard-auth";

// ── Watch-only, expiring, password-protected dashboard link ──────────────────
// Mounted at /view and NOT behind dashboardAuth. It has its own self-contained
// auth: a signed LINK token in the URL + a per-link password → a signed view
// SESSION cookie. Viewers get exactly ONE data path: GET /view/api/status.
// Every other method/path under /view/api is 403 fail-closed, so a viewer can
// never reach a mutation route or an owner-only endpoint. The dashboard HTML is
// fetched from Flask and transformed here (controls hidden + all non-status
// fetches stubbed client-side) so app.py is left byte-identical.

const DEV = process.env.NODE_ENV === "development";
const FLASK_PORT = 8000;
const COOKIE = "vsess";
const STATUS_PATH = "/view/api/status"; // the ONLY path the injected fetch allows

// ── read-only <head> injection: hide controls, stub non-status fetches, add
//    copy deterrents (best-effort — the server-side /status-only gate is the
//    real protection), and a "VIEW ONLY" badge. ────────────────────────────
const READONLY_HEAD = `
<meta name="referrer" content="no-referrer">
<style id="ro-style">
  #view-nav,#btn-share-view{display:none!important}
  #view-backtest,#view-tradezella,#view-research,#view-academy{display:none!important}
  #mode-row,#adv-row{display:none!important}
  #btn-enter,#btn-close,#btn-be,#btn-stop-managing,#mbmt-btn,#ri-btn{display:none!important}
  .mode-btn,.mute-pill{display:none!important}
  [id^="mb-chat"]{display:none!important}
  html,body{-webkit-user-select:none;-moz-user-select:none;user-select:none}
  #ro-badge{position:fixed;top:8px;right:8px;z-index:2147483647;background:rgba(6,2,20,.82);
    color:#9ee9ff;font:600 11px/1 -apple-system,system-ui,sans-serif;padding:6px 10px;border-radius:999px;
    border:1px solid rgba(120,220,255,.45);letter-spacing:.06em;pointer-events:none}
</style>
<script>
(function(){
  try{
    window.READ_ONLY=true;
    var ALLOW=${JSON.stringify(STATUS_PATH)};
    var _fetch=window.fetch?window.fetch.bind(window):null;
    window.fetch=function(input,init){
      var url=(typeof input==='string')?input:((input&&input.url)||'');
      var path=String(url).split('?')[0];
      var method=((init&&init.method)||(input&&input.method)||'GET').toUpperCase();
      if(_fetch&&(method==='GET'||method==='HEAD')&&path===ALLOW){return _fetch(input,init);}
      return Promise.resolve(new Response('{}',{status:200,headers:{'Content-Type':'application/json'}}));
    };
    var block=function(e){e.preventDefault();e.stopPropagation();return false;};
    ['contextmenu','selectstart','copy','cut','dragstart'].forEach(function(ev){
      document.addEventListener(ev,block,{capture:true});
    });
    document.addEventListener('keydown',function(e){
      var k=(e.key||'').toLowerCase();
      if(e.key==='F12'){block(e);return;}
      if((e.ctrlKey||e.metaKey)&&!e.shiftKey&&(k==='u'||k==='s'||k==='p')){block(e);return;}
      if((e.ctrlKey||e.metaKey)&&e.shiftKey&&(k==='i'||k==='j'||k==='c')){block(e);return;}
    },{capture:true});
    var strip=function(){
      try{
        ['#btn-enter','#btn-close','#btn-be','#btn-stop-managing','#mbmt-btn','#ri-btn','#view-nav','#btn-share-view']
          .forEach(function(s){document.querySelectorAll(s).forEach(function(n){n.remove();});});
        document.querySelectorAll('.mode-btn,.mute-pill').forEach(function(n){n.remove();});
        if(!document.getElementById('ro-badge')){
          var b=document.createElement('div');b.id='ro-badge';b.textContent='VIEW ONLY';
          (document.body||document.documentElement).appendChild(b);
        }
      }catch(_){}
    };
    if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',strip);}else{strip();}
  }catch(_){}
})();
</script>`;

function esc(s: string): string {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// referrer defaults to no-referrer (keeps the ?t=<token> URL out of the Referer
// header). The LOGIN page overrides this to "same-origin": a form POST from a
// document under "no-referrer" makes browsers send Origin: null, which the
// sameOrigin() CSRF check rejects — locking every legitimate viewer out. With
// "same-origin" the browser still sends a real Origin for our own POST (so login
// works) while suppressing it cross-origin (so CSRF protection is unaffected).
function pageShell(title: string, inner: string, referrer: string = "no-referrer"): string {
  return `<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="${referrer}"><title>${esc(title)}</title>
<style>
  :root{color-scheme:dark}
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
    background:radial-gradient(1200px 800px at 50% -10%,#1a0740,#0d0221 60%);
    font:15px/1.5 -apple-system,system-ui,Segoe UI,Roboto,sans-serif;color:#e8e6f5}
  .card{width:min(92vw,360px);background:rgba(20,10,45,.72);border:1px solid rgba(140,120,220,.3);
    border-radius:16px;padding:26px 24px;box-shadow:0 20px 60px rgba(0,0,0,.5)}
  h1{font-size:18px;margin:0 0 4px}
  p{margin:0 0 16px;color:#b6b0d6;font-size:13px}
  label{display:block;font-size:12px;color:#b6b0d6;margin:0 0 6px}
  input{width:100%;box-sizing:border-box;padding:11px 12px;border-radius:10px;
    border:1px solid rgba(140,120,220,.4);background:rgba(8,4,22,.7);color:#fff;font-size:15px}
  button{width:100%;margin-top:16px;padding:12px;border:0;border-radius:10px;cursor:pointer;
    background:linear-gradient(135deg,#7c5cff,#4aa8ff);color:#fff;font-weight:700;font-size:15px}
  .err{color:#ff9db0;font-size:13px;margin:12px 0 0}
  .muted{color:#8b85ad;font-size:11px;margin-top:16px}
</style></head><body><div class="card">${inner}</div></body></html>`;
}

function loginPage(token: string, error: string | null): string {
  return pageShell(
    "View-only dashboard",
    `<h1>🔒 View-only dashboard</h1>
<p>Enter the password to watch the live dashboard. This is read-only — no controls.</p>
<form method="POST" action="/view/login" autocomplete="off">
  <input type="hidden" name="t" value="${esc(token)}">
  <label for="pw">Password</label>
  <input id="pw" name="password" type="password" autofocus autocomplete="off" inputmode="text">
  <button type="submit">Unlock</button>
  ${error ? `<div class="err">${esc(error)}</div>` : ""}
</form>
<div class="muted">Access expires automatically.</div>`,
    "same-origin",
  );
}

function expiredPage(): string {
  return pageShell(
    "Link expired",
    `<h1>⌛ Link unavailable</h1>
<p>This view-only link is invalid or has expired. Please ask the owner for a new one.</p>`,
  );
}

function notConfiguredPage(): string {
  return pageShell(
    "Unavailable",
    `<h1>Unavailable</h1><p>View-only access is not configured.</p>`,
  );
}

function parseCookies(req: any): Record<string, string> {
  const raw = req.headers.cookie;
  const out: Record<string, string> = {};
  if (!raw) return out;
  for (const part of String(raw).split(";")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    const k = part.slice(0, i).trim();
    if (!k) continue;
    out[k] = decodeURIComponent(part.slice(i + 1).trim());
  }
  return out;
}

function noStoreHtml(res: any): void {
  res.set("content-type", "text/html; charset=utf-8");
  res.set("cache-control", "no-store");
  res.set("referrer-policy", "no-referrer");
}

function fetchFlaskDashboard(): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const r = http.request(
      { hostname: "localhost", port: FLASK_PORT, path: "/dashboard", method: "GET" },
      (pr) => {
        const chunks: Buffer[] = [];
        pr.on("data", (c) => chunks.push(c));
        pr.on("end", () =>
          resolve({ status: pr.statusCode ?? 200, body: Buffer.concat(chunks).toString("utf8") }),
        );
      },
    );
    r.on("error", reject);
    r.end();
  });
}

function transformDashboard(html: string): string {
  const MARK = "const BASE = '/api';";
  if (!html.includes(MARK)) {
    throw new Error("dashboard BASE marker not found — refusing to serve un-scoped page");
  }
  let out = html.replace(MARK, "const BASE = '/view/api';");
  const head = out.indexOf("</head>");
  if (head < 0) throw new Error("dashboard </head> not found");
  out = out.slice(0, head) + READONLY_HEAD + out.slice(head);
  return out;
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

// Small in-memory, per-token login throttle (best-effort; process-local).
const FAILS = new Map<string, { n: number; reset: number }>();
function rateLimited(token: string): boolean {
  const e = FAILS.get(token);
  if (!e || Date.now() > e.reset) return false;
  return e.n >= 5;
}
function noteFail(token: string): void {
  const now = Date.now();
  const e = FAILS.get(token);
  if (!e || now > e.reset) FAILS.set(token, { n: 1, reset: now + 60_000 });
  else e.n += 1;
  if (FAILS.size > 5000) {
    for (const [k, v] of FAILS) if (now > v.reset) FAILS.delete(k);
  }
}

export function createViewOnlyRouter(): Router {
  const router = Router();

  // GET /view — expired/invalid → expired page; no session → login; session → page.
  router.get("/", async (req: any, res: any) => {
    if (!viewConfigured()) {
      noStoreHtml(res);
      res.status(503).send(notConfiguredPage());
      return;
    }
    const token = typeof req.query.t === "string" ? req.query.t : "";
    const link = verifyLinkToken(token);
    if (!link) {
      noStoreHtml(res);
      res.status(200).send(expiredPage());
      return;
    }
    const sess = verifySessionCookie(parseCookies(req)[COOKIE]);
    if (!sess) {
      noStoreHtml(res);
      res.status(200).send(loginPage(token, null));
      return;
    }
    let data: { status: number; body: string };
    try {
      data = await fetchFlaskDashboard();
    } catch {
      res.status(502).set("cache-control", "no-store").send("Dashboard unavailable");
      return;
    }
    if (data.status !== 200) {
      res.status(502).set("cache-control", "no-store").send("Dashboard unavailable");
      return;
    }
    let outHtml: string;
    try {
      outHtml = transformDashboard(data.body);
    } catch {
      res.status(500).set("cache-control", "no-store").send("Dashboard render error");
      return;
    }
    noStoreHtml(res);
    res.status(200).send(outHtml);
  });

  // POST /view/login — verify password against the token; set the view cookie.
  router.post("/login", async (req: any, res: any) => {
    if (!viewConfigured()) {
      res.status(503).json({ error: "not configured" });
      return;
    }
    if (!sameOrigin(req)) {
      res.status(403).send("Cross-origin request rejected");
      return;
    }
    const buf: Buffer = Buffer.isBuffer(req.body) ? req.body : Buffer.alloc(0);
    const form = new URLSearchParams(buf.toString("utf8"));
    const token = form.get("t") ?? "";
    const password = form.get("password") ?? "";
    const link = verifyLinkToken(token);
    if (!link) {
      noStoreHtml(res);
      res.status(200).send(expiredPage());
      return;
    }
    if (rateLimited(token)) {
      await delay(400);
      noStoreHtml(res);
      res.status(429).send(loginPage(token, "Too many attempts — wait a minute and try again."));
      return;
    }
    await delay(300); // fixed delay dampens online guessing / timing
    if (!password || !checkPassword(password, link)) {
      noteFail(token);
      noStoreHtml(res);
      res.status(200).send(loginPage(token, "Incorrect password."));
      return;
    }
    const cookieVal = mintSessionCookie(link.exp);
    const maxAge = Math.max(1, Math.floor((link.exp - Date.now()) / 1000));
    res.setHeader(
      "Set-Cookie",
      `${COOKIE}=${encodeURIComponent(cookieVal)}; Path=/view; HttpOnly; SameSite=Lax; Max-Age=${maxAge}${
        DEV ? "" : "; Secure"
      }`,
    );
    res.set("cache-control", "no-store");
    res.redirect(302, `/view?t=${encodeURIComponent(token)}`);
  });

  // GET /view/logout — clear the view session cookie.
  router.get("/logout", (_req: any, res: any) => {
    res.setHeader("Set-Cookie", `${COOKIE}=; Path=/view; HttpOnly; SameSite=Lax; Max-Age=0`);
    noStoreHtml(res);
    res.status(200).send(pageShell("Logged out", "<h1>Logged out</h1><p>You can close this tab.</p>"));
  });

  // GET /view/api/status — the ONLY data path viewers get. Upstream path is
  // HARDCODED to /status (traversal-proof); only the query is relayed.
  router.get("/api/status", (req: any, res: any) => {
    if (!viewConfigured()) {
      res.status(503).json({ error: "not configured" });
      return;
    }
    if (!verifySessionCookie(parseCookies(req)[COOKIE])) {
      res.status(401).json({ error: "unauthorized" });
      return;
    }
    const query = Object.keys(req.query).length
      ? "?" + new URLSearchParams(req.query as Record<string, string>).toString()
      : "";
    const preq = http.request(
      { hostname: "localhost", port: FLASK_PORT, path: "/status" + query, method: "GET" },
      (pr) => {
        res.status(pr.statusCode ?? 200);
        const ct = pr.headers["content-type"];
        if (ct) res.set("content-type", ct);
        res.set("cache-control", "no-store");
        pr.pipe(res);
      },
    );
    preq.on("error", () => res.status(502).json({ error: "unavailable" }));
    preq.end();
  });

  // Everything else under /view/api → fail closed (JSON so callers' r.json() is safe).
  router.use("/api", (_req: any, res: any) => {
    res.status(403).json({ error: "forbidden (view-only)" });
  });

  return router;
}
