/**
 * Helpers shared by worker.js (newsletter routes + router) and the paywall
 * modules (api.js, auth.js, membership.js). No state, no bindings.
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

// Light abuse control: a KV counter per client IP per clock hour, keyed
// rl:[<bucket>:]<ip>:<hour>. KV is eventually consistent, so the count is
// approximate; fine against spam. Counters expire after two hours.
export function rateKey(request, bucket = "") {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  return ["rl", ...(bucket ? [bucket] : []), ip, Math.floor(Date.now() / 3600000)].join(":");
}

export async function overLimit(env, key, max) {
  const n = Number(await env.SUBS.get(key)) + 1;
  await env.SUBS.put(key, String(n), { expirationTtl: 7200 });
  return n > max;
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
