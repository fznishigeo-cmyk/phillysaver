/**
 * Helpers shared by worker.js (newsletter routes + router) and the paywall
 * modules (api.js, auth.js, membership.js). No bindings; the only state is
 * the per-isolate memory at the bottom.
 */

export const DEFAULT_ORIGINS =
  "https://wowpriced.com,https://www.wowpriced.com,https://fznishigeo-cmyk.github.io";
export const EMAIL_RE = /^[a-z0-9._%+'-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$/;

export function allowedOrigins(env) {
  return (env.ALLOWED_ORIGINS || DEFAULT_ORIGINS).split(",").map((s) => s.trim()).filter(Boolean);
}

// CORS for the allowed origins only; empty for anyone else. One preflight
// answer serves every route, so it advertises the API's headers and methods.
export function corsHeaders(request, env) {
  const origin = request.headers.get("Origin") || "";
  if (!allowedOrigins(env).includes(origin)) return {};
  return { "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
           "Access-Control-Allow-Headers": "Authorization, Content-Type", "Vary": "Origin" };
}

export function json(body, status = 200, headers = {}) {
  headers = { "Content-Type": "application/json; charset=utf-8", ...headers };
  return new Response(JSON.stringify(body), { status, headers });
}

// Constant-time string compare for bearer secrets, tokens and hashes.
export function safeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// The request body as an object; {} when it is missing or not a JSON object.
export async function readJson(request) {
  try {
    const v = await request.json();
    return v && typeof v === "object" ? v : {};
  } catch (e) { return {}; }
}

// base64url without padding, as JWTs and the session token use it.
export function b64url(bytes) {
  let s = "";
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function unb64url(str) { // throws on malformed input
  const s = String(str).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(s + "=".repeat((4 - (s.length % 4)) % 4)), (c) => c.charCodeAt(0));
}

// ------------------------------------------------------- isolate memory
// Best-effort state that must not cost KV writes (the Free plan allows 1,000
// a day): abuse counters and the membership cache. An isolate recycles
// whenever Cloudflare likes, which only means a fresh counter or one more
// Stripe call; KV holds only accounts, subscribers, deals:full and the JWKS.

const memory = new Map(); // key -> {value, expires_at}
const MEMORY_SWEEP_AT = 10000;

export function memGet(key) {
  const hit = memory.get(key);
  if (!hit) return undefined;
  if (hit.expires_at <= Date.now()) { memory.delete(key); return undefined; }
  return hit.value;
}

export function memSet(key, value, ttlMs) {
  if (memory.size >= MEMORY_SWEEP_AT) {
    for (const [k, v] of memory) if (v.expires_at <= Date.now()) memory.delete(k);
  }
  memory.set(key, { value, expires_at: Date.now() + ttlMs });
}

export function memDel(key) { memory.delete(key); }
export function memClear() { memory.clear(); } // tests only

// Light abuse control: a counter per client IP per clock hour and bucket.
// Per isolate, so the count is approximate; fine against spam.
export function overLimit(request, bucket, max) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const key = `rl:${bucket}:${ip}:${Math.floor(Date.now() / 3600000)}`;
  const n = (memGet(key) || 0) + 1;
  memSet(key, n, 7200000);
  return n > max;
}
