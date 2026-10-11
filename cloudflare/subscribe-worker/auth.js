/**
 * Accounts, sessions and Google sign-in for the paywall (docs/paywall.md).
 *
 *   acct:<email>  {email, salt, hash, created_at, trial_ends_at, google_sub}
 *                 salt/hash are base64url; both null for a Google-only account
 *   jwks:google   Google's signing keys, cached one hour (also in memory)
 *
 * A session is a stateless token base64url(json).base64url(hmac-sha256) of
 * {email, exp} (30 days) signed with SESSION_SECRET; nothing is stored.
 * Passwords are PBKDF2-SHA256, 100 000 iterations, 16-byte random salt.
 */

import { EMAIL_RE, b64url, unb64url, safeEqual } from "./util.js";

const SESSION_DAYS = 30;
const PBKDF2_ITERATIONS = 100000;
const DEFAULT_TRIAL_DAYS = 7;
const GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs";
const GOOGLE_ISSUERS = ["accounts.google.com", "https://accounts.google.com"];
const JWKS_TTL_MS = 3600 * 1000;
const DUMMY_SALT = new Uint8Array(16); // keeps a miss as slow as a wrong password

const enc = new TextEncoder();
const dec = new TextDecoder();

// ------------------------------------------------------------- sessions

async function hmacKey(secret) {
  return crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
}

async function hmac(secret, text) {
  return b64url(await crypto.subtle.sign("HMAC", await hmacKey(secret), enc.encode(text)));
}

export async function signSession(env, email, exp = Math.floor(Date.now() / 1000) + SESSION_DAYS * 86400) {
  const body = b64url(enc.encode(JSON.stringify({ email, exp })));
  return `${body}.${await hmac(env.SESSION_SECRET, body)}`;
}

// The {email, exp} claims of a valid, unexpired token; null otherwise.
export async function verifySession(env, token) {
  if (!env.SESSION_SECRET || typeof token !== "string") return null;
  const parts = token.split(".");
  if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
  if (!safeEqual(await hmac(env.SESSION_SECRET, parts[0]), parts[1])) return null;
  let claims;
  try { claims = JSON.parse(dec.decode(unb64url(parts[0]))); } catch (e) { return null; }
  if (!claims || typeof claims.email !== "string" || !(claims.exp > Date.now() / 1000)) return null;
  return claims;
}

export async function sessionFromRequest(request, env) {
  const auth = request.headers.get("Authorization") || "";
  return auth.startsWith("Bearer ") ? verifySession(env, auth.slice(7).trim()) : null;
}

// ------------------------------------------------------------- accounts

export async function hashPassword(password, salt) {
  const key = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations: PBKDF2_ITERATIONS }, key, 256);
  return b64url(bits);
}

export async function getAccount(env, email) {
  const raw = await env.SUBS.get(`acct:${email}`);
  try { return raw ? JSON.parse(raw) : null; } catch (e) { return null; }
}

export async function saveAccount(env, acct) {
  await env.SUBS.put(`acct:${acct.email}`, JSON.stringify(acct));
}

function trialDays(env) {
  const n = Number.parseInt(env.TRIAL_DAYS, 10);
  return Number.isFinite(n) && n >= 0 ? n : DEFAULT_TRIAL_DAYS;
}

// The trial starts now, whichever way the account is created.
export async function createAccount(env, email, { password, googleSub } = {}) {
  const now = Date.now();
  const acct = { email, salt: null, hash: null, created_at: new Date(now).toISOString(),
                 trial_ends_at: new Date(now + trialDays(env) * 86400000).toISOString(),
                 google_sub: googleSub || null };
  if (password) {
    const salt = crypto.getRandomValues(new Uint8Array(16));
    acct.salt = b64url(salt);
    acct.hash = await hashPassword(password, salt);
  }
  await saveAccount(env, acct);
  return acct;
}

// Always derives a hash, so an unknown address, a Google-only account and a
// wrong password all take the same time and give the same answer.
export async function checkPassword(acct, password) {
  const salt = acct && acct.salt ? unb64url(acct.salt) : DUMMY_SALT;
  const got = await hashPassword(password, salt);
  return !!(acct && acct.hash) && safeEqual(got, acct.hash);
}

// --------------------------------------------------------------- google

let jwksMemo = null; // {keys, fetched_at} for this isolate

async function googleKeys(env, refresh = false) {
  const now = Date.now();
  if (!refresh && jwksMemo && now - jwksMemo.fetched_at < JWKS_TTL_MS) return jwksMemo.keys;
  if (!refresh) {
    try {
      const cached = JSON.parse(await env.SUBS.get("jwks:google"));
      if (cached && Array.isArray(cached.keys) && now - cached.fetched_at < JWKS_TTL_MS) {
        jwksMemo = cached;
        return cached.keys;
      }
    } catch (e) { /* no usable cache: fetch */ }
  }
  const res = await fetch(GOOGLE_JWKS_URL);
  if (!res.ok) throw new Error(`jwks fetch ${res.status}`);
  const { keys } = await res.json();
  if (!Array.isArray(keys)) throw new Error("jwks shape");
  jwksMemo = { keys, fetched_at: now };
  await env.SUBS.put("jwks:google", JSON.stringify(jwksMemo), { expirationTtl: 3600 });
  return keys;
}

async function rs256Valid(jwk, signedPart, signature) {
  if (!jwk || jwk.kty !== "RSA" || !jwk.n || !jwk.e) return false;
  const key = await crypto.subtle.importKey("jwk", { kty: "RSA", n: jwk.n, e: jwk.e },
                                            { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  return crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, signature, enc.encode(signedPart));
}

// A JWT part decoded as JSON, or null; a malformed token never reaches a log.
function jwtPart(s) {
  try { return JSON.parse(dec.decode(unb64url(s))); } catch (e) { return null; }
}

// Verifies a Google ID token (RS256 against Google's JWKS, aud, iss, exp,
// verified email). {email, sub} when good, null otherwise; never throws.
export async function verifyGoogleToken(env, credential) {
  if (!env.GOOGLE_CLIENT_ID || typeof credential !== "string") return null;
  const parts = credential.split(".");
  if (parts.length !== 3) return null;
  const header = jwtPart(parts[0]);
  const claims = jwtPart(parts[1]);
  let signature;
  try { signature = unb64url(parts[2]); } catch (e) { return null; }
  if (!header || !claims || header.alg !== "RS256" || typeof header.kid !== "string") return null;
  try {
    let jwk = (await googleKeys(env)).find((k) => k.kid === header.kid);
    if (!jwk) jwk = (await googleKeys(env, true)).find((k) => k.kid === header.kid); // keys rotate
    if (!(await rs256Valid(jwk, `${parts[0]}.${parts[1]}`, signature))) return null;
  } catch (e) {
    console.warn("google sign-in check failed:", e && e.message); // JWKS fetch or WebCrypto, not the token
    return null;
  }
  if (claims.aud !== env.GOOGLE_CLIENT_ID || !GOOGLE_ISSUERS.includes(claims.iss)) return null;
  if (!(claims.exp > Date.now() / 1000)) return null;
  if (claims.email_verified !== true && claims.email_verified !== "true") return null;
  const email = String(claims.email || "").trim().toLowerCase();
  if (!EMAIL_RE.test(email) || email.length > 254 || typeof claims.sub !== "string") return null;
  return { email, sub: claims.sub };
}
