/**
 * The paywall API (docs/paywall.md "Worker API"), mounted under /api/ by
 * worker.js. JSON in and out; every error is a JSON {error} with a status,
 * never a stack trace. Passwords, tokens and secrets are never logged.
 *
 *   POST /api/signup   {email, password} -> 200 {token, me} | 400 invalid_email
 *                      | 400 weak_password | 409 exists
 *   POST /api/login    {email, password} -> 200 {token, me} | 401 bad_credentials
 *   POST /api/google   {credential}      -> 200 {token, me} | 401 bad_token
 *   POST /api/claim    Bearer <session>, {session_id} -> 200 {me} | 400 bad_session
 *                      | 404 unknown_session | 502 stripe_unavailable | 401
 *   GET  /api/me       Bearer <session>  -> 200 me | 401 (?fresh=1 re-asks Stripe)
 *   GET  /api/deals    Bearer <session>  -> 200 full payload | 402 me | 401
 *   POST /api/logout   Bearer <session>  -> 204 | 401
 *   POST /api/publish  Bearer NEWSLETTER_SECRET, full payload -> 204 | 400 | 401
 *
 * The four routes that mint or bind an account answer 503 signin_unavailable
 * while SESSION_SECRET is unset, before any KV write. Abuse counters per
 * client IP per hour live in isolate memory: signup 10, login 20, google 20,
 * claim 10. KV holds acct:<email> and deals:full (the full payload, stored
 * verbatim by /api/publish).
 */

import { EMAIL_RE, corsHeaders, json, safeEqual, readJson, overLimit } from "./util.js";
import { getAccount, newAccount, saveAccount, setPassword, checkPassword, needsRehash, isVerified,
         takeOver, signSession, sessionCurrent, sessionFromRequest, verifyGoogleToken } from "./auth.js";
import { meFor, stripeGet, forgetMembership } from "./membership.js";

const LIMITS_PER_IP_PER_HOUR = { signup: 10, login: 20, google: 20, claim: 10 };
const NEEDS_SIGNING = ["/api/signup", "/api/login", "/api/google", "/api/claim"];
const MIN_PASSWORD = 8;
const CHECKOUT_SESSION_RE = /^cs_[A-Za-z0-9_]{8,200}$/;

const cleanEmail = (v) => String(v || "").trim().toLowerCase();
const str = (v) => (typeof v === "string" ? v : "");

// The session is signed before a new or changed account is saved, so a
// signing failure leaves no half-created account behind.
async function signedIn(env, acct, headers, save = false) {
  const token = await signSession(env, acct.email, undefined, acct.session_version || 0);
  if (save) await saveAccount(env, acct);
  return json({ token, me: await meFor(env, acct) }, 200, headers);
}

async function signup(request, env, headers) {
  const body = await readJson(request);
  const email = cleanEmail(body.email);
  if (!EMAIL_RE.test(email) || email.length > 254) return json({ error: "invalid_email" }, 400, headers);
  if (str(body.password).length < MIN_PASSWORD) return json({ error: "weak_password" }, 400, headers);
  if (await getAccount(env, email)) return json({ error: "exists" }, 409, headers);
  const acct = newAccount(env, email);
  await setPassword(env, acct, body.password);
  return signedIn(env, acct, headers, true);
}

async function login(request, env, headers) {
  const body = await readJson(request);
  const email = cleanEmail(body.email);
  const acct = EMAIL_RE.test(email) ? await getAccount(env, email) : null;
  if (!(await checkPassword(env, acct, str(body.password)))) return json({ error: "bad_credentials" }, 401, headers);
  const rehash = needsRehash(env, acct); // older scheme or round count: upgrade while the password is in hand
  if (rehash) await setPassword(env, acct, body.password);
  return signedIn(env, acct, headers, rehash);
}

async function google(request, env, headers) {
  const id = await verifyGoogleToken(env, (await readJson(request)).credential);
  if (!id) return json({ error: "bad_token" }, 401, headers);
  let acct = await getAccount(env, id.email);
  let save = true;
  if (!acct) acct = newAccount(env, id.email, { googleSub: id.sub }); // trial starts now
  else if (!isVerified(acct)) { // a password squat on a verified address
    takeOver(acct, id.sub);
    forgetMembership(acct.email); // the owner's email lookup must not see the squatter's cached "no"
  } else if (acct.google_sub !== id.sub) acct.google_sub = id.sub;
  else save = false;
  return signedIn(env, acct, headers, save);
}

// The account behind a valid session token minted since its last takeover,
// else null (-> 401).
async function requireAccount(request, env) {
  const session = await sessionFromRequest(request, env);
  const acct = session ? await getAccount(env, session.email) : null;
  return acct && sessionCurrent(acct, session) ? acct : null;
}

// Binds the Stripe customer of a completed subscription Checkout Session to
// the account: the payer holds the receipt (the session id from the Payment
// Link's redirect), so the bound customer's subscription is theirs whatever
// email they typed. The payer email is typed too, so it verifies nothing.
async function claim(request, env, headers) {
  const acct = await requireAccount(request, env);
  if (!acct) return json({ error: "unauthorized" }, 401, headers);
  const id = str((await readJson(request)).session_id);
  if (!CHECKOUT_SESSION_RE.test(id)) return json({ error: "bad_session" }, 400, headers);
  if (!env.STRIPE_SECRET_KEY) return json({ error: "stripe_unavailable" }, 502, headers);
  let session;
  try {
    session = await stripeGet(env, `/checkout/sessions/${id}`);
  } catch (e) {
    if (e.status === 404) return json({ error: "unknown_session" }, 404, headers);
    console.warn("claim failed:", e && e.message);
    return json({ error: "stripe_unavailable" }, 502, headers);
  }
  const customer = typeof session.customer === "string" ? session.customer : session.customer && session.customer.id;
  if (session.status !== "complete" || session.mode !== "subscription" || !customer || typeof customer !== "string") {
    return json({ error: "bad_session" }, 400, headers);
  }
  acct.stripe_customer = customer;
  acct.claimed_at = new Date(Date.now()).toISOString();
  await saveAccount(env, acct);
  forgetMembership(acct.email); // the next answer must see the new customer
  return json({ me: await meFor(env, acct) }, 200, headers);
}

async function me(request, env, headers, url) {
  const acct = await requireAccount(request, env);
  if (!acct) return json({ error: "unauthorized" }, 401, headers);
  return json(await meFor(env, acct, url.searchParams.get("fresh") === "1"), 200, headers);
}

async function deals(request, env, headers) {
  const acct = await requireAccount(request, env);
  if (!acct) return json({ error: "unauthorized" }, 401, headers);
  const status = await meFor(env, acct);
  if (!status.member) return json(status, 402, headers);
  const payload = await env.SUBS.get("deals:full");
  if (payload === null) return json({ error: "no_payload" }, 404, headers); // nothing published yet
  return new Response(payload, { status: 200, headers: { "Content-Type": "application/json; charset=utf-8", ...headers } });
}

async function logout(request, env, headers) {
  if (!(await requireAccount(request, env))) return json({ error: "unauthorized" }, 401, headers);
  return new Response(null, { status: 204, headers });
}

// The pipeline's upload: stored as the exact bytes it sent.
async function publish(request, env) {
  const auth = request.headers.get("Authorization") || "";
  if (!env.NEWSLETTER_SECRET || !safeEqual(auth, `Bearer ${env.NEWSLETTER_SECRET}`)) {
    return json({ error: "unauthorized" }, 401);
  }
  const text = await request.text();
  let parsed = null;
  try { parsed = JSON.parse(text); } catch (e) { /* not JSON */ }
  if (!parsed || !Array.isArray(parsed.deals)) return json({ error: "bad_payload" }, 400);
  await env.SUBS.put("deals:full", text);
  return new Response(null, { status: 204 });
}

const POSTS = { "/api/signup": signup, "/api/login": login, "/api/google": google, "/api/claim": claim,
                "/api/logout": logout };
const GETS = { "/api/me": me, "/api/deals": deals };

export async function handleApi(request, env, url) {
  const headers = { "Cache-Control": "no-store", ...corsHeaders(request, env) };
  const path = url.pathname;
  try {
    if (request.method === "POST" && path === "/api/publish") return await publish(request, env);
    const route = request.method === "POST" ? POSTS[path] : request.method === "GET" ? GETS[path] : null;
    if (!route) return json({ error: "not_found" }, 404, headers);
    if (NEEDS_SIGNING.includes(path) && !env.SESSION_SECRET) return json({ error: "signin_unavailable" }, 503, headers);
    const bucket = path.slice(5); // "signup" | "login" | "google" | "claim" | ...
    if (LIMITS_PER_IP_PER_HOUR[bucket] && overLimit(request, bucket, LIMITS_PER_IP_PER_HOUR[bucket])) {
      return json({ error: "rate_limited" }, 429, headers);
    }
    return await route(request, env, headers, url);
  } catch (e) {
    console.error("api failure:", request.method, path, e && e.message); // never the body or headers
    return json({ error: "internal" }, 500, headers);
  }
}
