import { createHash, createHmac, timingSafeEqual, randomBytes } from "node:crypto";

// ── Stateless signed tokens for the watch-only dashboard link ────────────────
// There is NO database. Two artifacts are minted:
//   • a LINK token  (lives in the shareable URL)  — proves the owner minted the
//     link, carries the expiry, and embeds an HMAC of the per-link password.
//   • a SESSION cookie (set after the viewer types the correct password) — lets
//     the read-only page poll /status without re-entering the password, and dies
//     when the link expires.
// Both are HMAC-signed with keys DERIVED from DASHBOARD_PASSWORD, so no new secret
// is required and rotating the admin password invalidates every outstanding link
// (the only revocation lever). Distinct subkey labels + a `typ` field make a URL
// token unusable as a cookie and vice-versa.
// SECURITY NOTE: because the signing keys are derived from DASHBOARD_PASSWORD via
// a single unsalted sha256, anyone holding a shared link token possesses material
// for an OFFLINE dictionary attack against the admin password. This is acceptable
// only while DASHBOARD_PASSWORD is strong/high-entropy — keep it long and random.

// The base secret for key derivation. In production this MUST be the admin
// password. Share links fail closed when it is absent in every environment.
function baseSecret(): string | null {
  const pw = process.env.DASHBOARD_PASSWORD;
  if (pw && pw.length > 0) return pw;
  return null;
}

// True when the feature can operate (i.e. a signing secret exists).
export function viewConfigured(): boolean {
  return baseSecret() !== null;
}

function keyFor(label: string): Buffer {
  const s = baseSecret();
  if (s === null) throw new Error("view link signing secret is not configured");
  return createHash("sha256").update(`${s}:${label}`).digest();
}

function hmac(label: string, data: string): Buffer {
  return createHmac("sha256", keyFor(label)).update(data).digest();
}

function b64url(buf: Buffer | string): string {
  return Buffer.from(buf).toString("base64url");
}

function eq(a: Buffer, b: Buffer): boolean {
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

const LINK_SIG = "view-link-sig-v1";
const LINK_PW = "view-link-pw-v1";
const SESS_SIG = "view-sess-sig-v1";

interface LinkPayload {
  v: number;
  typ: "link";
  exp: number; // epoch ms
  ph: string; // base64 of HMAC(password)
}

interface SessPayload {
  v: number;
  typ: "sess";
  exp: number; // epoch ms — mirrors the link expiry
  nonce: string; // random per-login value prevents a predictable shared cookie
}

// base64 (not url) of the fixed-length HMAC of the password — embedded in the link.
export function passwordHash(password: string): string {
  return hmac(LINK_PW, password).toString("base64");
}

export function mintLinkToken(
  ttlSeconds: number,
  password: string,
): { token: string; exp: number } {
  const exp = Date.now() + Math.max(1, Math.floor(ttlSeconds)) * 1000;
  const payload: LinkPayload = { v: 1, typ: "link", exp, ph: passwordHash(password) };
  const body = b64url(JSON.stringify(payload));
  const sig = b64url(hmac(LINK_SIG, body));
  return { token: `${body}.${sig}`, exp };
}

function verifySigned(
  value: string | undefined | null,
  sigLabel: string,
): any | null {
  if (!value || typeof value !== "string") return null;
  const dot = value.indexOf(".");
  if (dot < 0) return null;
  const body = value.slice(0, dot);
  const sig = value.slice(dot + 1);
  let expected: Buffer;
  let got: Buffer;
  try {
    expected = hmac(sigLabel, body);
    got = Buffer.from(sig, "base64url");
  } catch {
    return null;
  }
  if (!eq(expected, got)) return null;
  let payload: any;
  try {
    payload = JSON.parse(Buffer.from(body, "base64url").toString("utf8"));
  } catch {
    return null;
  }
  if (!payload || payload.v !== 1) return null;
  if (typeof payload.exp !== "number" || payload.exp <= Date.now()) return null;
  return payload;
}

export function verifyLinkToken(token: string | undefined | null): LinkPayload | null {
  const p = verifySigned(token, LINK_SIG);
  if (!p || p.typ !== "link" || typeof p.ph !== "string") return null;
  return p as LinkPayload;
}

// Timing-safe check of an entered password against the token's embedded hash.
export function checkPassword(entered: string, payload: LinkPayload): boolean {
  let a: Buffer;
  let b: Buffer;
  try {
    a = Buffer.from(passwordHash(entered), "base64");
    b = Buffer.from(payload.ph, "base64");
  } catch {
    return false;
  }
  return eq(a, b);
}

export function mintSessionCookie(exp: number): string {
  const payload: SessPayload = {
    v: 1,
    typ: "sess",
    exp,
    nonce: randomBytes(16).toString("base64url"),
  };
  const body = b64url(JSON.stringify(payload));
  const sig = b64url(hmac(SESS_SIG, body));
  return `${body}.${sig}`;
}

export function verifySessionCookie(value: string | undefined | null): SessPayload | null {
  const p = verifySigned(value, SESS_SIG);
  if (!p || p.typ !== "sess" || typeof p.nonce !== "string" || p.nonce.length < 16) return null;
  return p as SessPayload;
}

// Human-shareable auto password (8 hex chars) when the owner doesn't set one.
export function randomPassword(): string {
  return randomBytes(4).toString("hex");
}
