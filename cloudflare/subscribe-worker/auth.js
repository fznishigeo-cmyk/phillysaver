/**
 * Accounts, sessions and Google sign-in for the paywall (docs/paywall.md).
 *
 *   acct:<email>  {email, scheme, iterations, salt, hash, created_at, trial_ends_at,
 *                  google_sub, email_verified, stripe_customer, claimed_at,
 *                  session_version}
 *                 scheme/iterations/salt/hash are null for a Google-only account
 *   jwks:google   Google's signing keys, cached one hour (also in memory)
 *
 * A session is a stateless token base64url(json).base64url(hmac-sha256) of
 * {email, sv, exp} (30 days) signed with SESSION_SECRET; nothing is stored.
 * sv is the account's session_version at signing: a takeover bumps the
 * account's version, so every token minted before it (the squatter's) dies.
 *
 * Passwords, scheme 2: HMAC-SHA256(SESSION_SECRET, password) (a server-side
 * pepper, so KV hashes alone cannot be brute-forced), then PBKDF2-SHA256 over
 * that with a 16-byte random salt and PBKDF2_ITERATIONS rounds (default
 * 20,000: ~3 ms CPU, inside the Workers Free plan's 10 ms). Scheme 1 (accounts
 * written before the pepper) ran PBKDF2 on the bare password, 100,000 rounds;
 * each account stores its own params, is verified with them, and is re-hashed
 * with the current ones after a good login. Rotating SESSION_SECRET therefore
 * invalidates every scheme-2 password as well as every session.
 *
 * email_verified is true for an account created or taken over by Google
 * sign-in; false for a password sign-up (anyone can type an address, and the
 * payer email on a Stripe Checkout is typed too, so a claim never verifies).
 * Membership by Stripe email lookup is only granted to verified accounts
 * (membership.js).
 */

import { EMAIL_RE, b64url, unb64url, safeEqual } from "./util.js";

const SESSION_DAYS = 30;
const SCHEME = 2;
const DEFAULT_ITERATIONS = 20000;
const LEGACY_ITERATIONS = 100000; // scheme 1
const DEFAULT_TRIAL_DAYS = 7;
const GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs";
const GOOGLE_ISSUERS = ["accounts.google.com", "https://accounts.google.com"];
const JWKS_TTL_MS = 3600 * 1000;
const JWKS_REFETCH_EVERY_MS = 60 * 1000;
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

export async function signSession(env, email, exp = Math.floor(Date.now() / 1000) + SESSION_DAYS * 86400, version = 0) {
  const body = b64url(enc.encode(JSON.stringify({ email, sv: version, exp })));
  return `${body}.${await hmac(env.SESSION_SECRET, body)}`;
}

// The {email, sv, exp} claims of a valid, unexpired token; null otherwise.
export async function verifySession(env, token) {
  if (!env.SESSION_SECRET || typeof token !== "string") return null;
  const parts = token.split(".");
  if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
  if (!safeEqual(await hmac(env.SESSION_SECRET, parts[0]), parts[1])) return null;
  let claims;
  try { claims = JSON.parse(dec.decode(unb64url(parts[0]))); } catch (e) { return null; }
  if (!claims || typeof claims.email !== "string" || !(claims.exp > Date.now() / 1000)) return null;
  if (typeof claims.sv !== "number") claims.sv = 0; // tokens from before session_version
  return claims;
}

// A token minted before the account's last takeover no longer opens it.
export function sessionCurrent(acct, claims) {
  return (acct.session_version || 0) === claims.sv;
}

export async function sessionFromRequest(request, env) {
  const auth = request.headers.get("Authorization") || "";
  return auth.startsWith("Bearer ") ? verifySession(env, auth.slice(7).trim()) : null;
}

// ------------------------------------------------------------ passwords

export function iterations(env) {
  const n = Number.parseInt(env.PBKDF2_ITERATIONS, 10);
  return Number.isFinite(n) && n >= 1000 ? n : DEFAULT_ITERATIONS;
}

async function deriveHash(env, password, { scheme, iterations: rounds, salt }) {
  let material = enc.encode(password);
  if (scheme === SCHEME) material = await crypto.subtle.sign("HMAC", await hmacKey(env.SESSION_SECRET), material);
  const key = await crypto.subtle.importKey("raw", material, "PBKDF2", false, ["deriveBits"]);
  return b64url(await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations: rounds }, key, 256));
}

// Sets (or replaces) the account's password with the current scheme.
export async function setPassword(env, acct, password) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  acct.scheme = SCHEME;
  acct.iterations = iterations(env);
  acct.salt = b64url(salt);
  acct.hash = await deriveHash(env, password, { scheme: SCHEME, iterations: acct.iterations, salt });
}

// The params a stored hash was made with; null when the account has none.
function storedParams(acct) {
  if (!acct || !acct.hash || !acct.salt) return null;
  try {
    return { scheme: acct.scheme || 1, iterations: acct.iterations || LEGACY_ITERATIONS, salt: unb64url(acct.salt) };
  } catch (e) { return null; } // malformed salt: never matches
}

// Always derives a hash, so an unknown address, a Google-only account and a
// wrong password all take the same time and give the same answer.
export async function checkPassword(env, acct, password) {
  const params = storedParams(acct);
  const got = await deriveHash(env, password, params || { scheme: SCHEME, iterations: iterations(env), salt: DUMMY_SALT });
  return !!params && safeEqual(got, acct.hash);
}

// After a good login: the hash predates the current scheme or round count.
export function needsRehash(env, acct) {
  return acct.scheme !== SCHEME || acct.iterations !== iterations(env);
}

// ------------------------------------------------------------- accounts

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

// A fresh account, not yet saved (the caller signs the session first, so a
// signing failure leaves nothing behind). The trial starts now either way.
export function newAccount(env, email, { googleSub } = {}) {
  const now = Date.now();
  return { email, scheme: null, iterations: null, salt: null, hash: null,
           created_at: new Date(now).toISOString(),
           trial_ends_at: new Date(now + trialDays(env) * 86400000).toISOString(),
           google_sub: googleSub || null, email_verified: !!googleSub, stripe_customer: null, claimed_at: null,
           session_version: 0 };
}

// Accounts written before email_verified existed count as verified only when
// Google alone could have created them (a google_sub and no password).
export function isVerified(acct) {
  if (acct.email_verified !== undefined) return acct.email_verified === true;
  return !!acct.google_sub && !acct.hash;
}

// A verified Google sign-in takes over an unverified (password) account on the
// same address: whoever typed that address at sign-up does not own it. Their
// password goes and their sessions die (the version bump); a Stripe customer
// bound by a claim stays, since the payer holds that receipt.
export function takeOver(acct, googleSub) {
  Object.assign(acct, { google_sub: googleSub, email_verified: true, scheme: null, iterations: null, salt: null, hash: null,
                        session_version: (acct.session_version || 0) + 1 });
}

// --------------------------------------------------------------- google

let jwksMemo = null; // {keys, fetched_at} for this isolate
let jwksRefetchedAt = 0; // a forced refetch (unknown kid) at most once a minute per isolate

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
  } else {
    if (now - jwksRefetchedAt < JWKS_REFETCH_EVERY_MS) return jwksMemo ? jwksMemo.keys : [];
    jwksRefetchedAt = now;
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

// Verifies a Google ID token: the claims first (aud, iss, exp, verified
// email: no network for junk), then RS256 against Google's JWKS. {email, sub}
// when good, null otherwise; never throws.
export async function verifyGoogleToken(env, credential) {
  if (!env.GOOGLE_CLIENT_ID || typeof credential !== "string") return null;
  const parts = credential.split(".");
  if (parts.length !== 3) return null;
  const header = jwtPart(parts[0]);
  const claims = jwtPart(parts[1]);
  let signature;
  try { signature = unb64url(parts[2]); } catch (e) { return null; }
  if (!header || !claims || header.alg !== "RS256" || typeof header.kid !== "string") return null;
  if (claims.aud !== env.GOOGLE_CLIENT_ID || !GOOGLE_ISSUERS.includes(claims.iss)) return null;
  if (!(claims.exp > Date.now() / 1000)) return null;
  if (claims.email_verified !== true && claims.email_verified !== "true") return null;
  const email = String(claims.email || "").trim().toLowerCase();
  if (!EMAIL_RE.test(email) || email.length > 254 || typeof claims.sub !== "string") return null;
  try {
    let jwk = (await googleKeys(env)).find((k) => k.kid === header.kid);
    if (!jwk) jwk = (await googleKeys(env, true)).find((k) => k.kid === header.kid); // keys rotate
    if (!(await rs256Valid(jwk, `${parts[0]}.${parts[1]}`, signature))) return null;
  } catch (e) {
    console.warn("google sign-in check failed:", e && e.message); // JWKS fetch or WebCrypto, not the token
    return null;
  }
  return { email, sub: claims.sub };
}
