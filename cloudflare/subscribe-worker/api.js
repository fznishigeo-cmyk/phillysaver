/**
 * The paywall API (docs/paywall.md "Worker API"), mounted under /api/ by
 * worker.js. JSON in and out; every error is a JSON {error} with a status,
 * never a stack trace. Passwords, tokens and secrets are never logged.
 *
 *   POST /api/signup   {email, password} -> 200 {token, me} | 400 invalid_email
 *                      | 400 weak_password | 409 exists
 *   POST /api/login    {email, password} -> 200 {token, me} | 401 bad_credentials
 *   POST /api/google   {credential}      -> 200 {token, me, new_account} | 401 bad_token
 *   POST /api/claim    Bearer <session>, {session_id} -> 200 {me} | 400 bad_session
 *                      | 403 wrong_account | 404 unknown_session | 409 already_claimed
 *                      | 502 stripe_unavailable | 401
 *   POST /api/checkout Bearer <session>  -> 200 {url} (Stripe Checkout) | 409 already_member
 *                      | 409 payment_issue | 409 contact_support (each with me)
 *                      | 503 checkout_unavailable {fallback_url} | 401
 *   POST /api/portal   Bearer <session>  -> 200 {url} (Stripe billing portal) | 404 no_customer
 *                      | 503 portal_unavailable {fallback_url} | 401
 *   GET  /api/me       Bearer <session>  -> 200 me | 401 (?fresh=1 re-asks Stripe)
 *   GET  /api/deals    Bearer <session>  -> 200 full payload | 402 me | 401
 *   POST /api/logout   Bearer <session>  -> 204 | 401
 *   POST /api/publish  Bearer NEWSLETTER_SECRET, full payload -> 204 | 400 | 401
 *
 * The routes that mint, bind or bill an account answer 503 signin_unavailable
 * while SESSION_SECRET is unset, before any KV write. Abuse counters per
 * client IP per hour live in isolate memory: signup 10, login 20, google 20,
 * claim 10, checkout 10. KV holds acct:<email>, claim:<customer> (the one
 * account a Stripe customer is bound to), deals:full (the full payload, stored
 * verbatim by /api/publish), stripe:price, stripe:portal and checkout:<ref>
 * (billing.js).
 */

import { EMAIL_RE, corsHeaders, json, safeEqual, readJson, overLimit } from "./util.js";
import { getAccount, newAccount, saveAccount, setPassword, checkPassword, needsRehash, isVerified,
         takeOver, signSession, sessionCurrent, sessionFromRequest, verifyGoogleToken } from "./auth.js";
import { meFor, stripeGet, forgetMembership, accountRef, checkoutHistory, portalCustomer,
         trialAvailable } from "./membership.js";
import { checkoutUrl, portalUrl } from "./billing.js";

const LIMITS_PER_IP_PER_HOUR = { signup: 10, login: 20, google: 20, claim: 10, checkout: 10 };
const NEEDS_SIGNING = ["/api/signup", "/api/login", "/api/google", "/api/claim", "/api/checkout"];
const MIN_PASSWORD = 8;
const CHECKOUT_SESSION_RE = /^cs_[A-Za-z0-9_]{8,200}$/;

const cleanEmail = (v) => String(v || "").trim().toLowerCase();
const str = (v) => (typeof v === "string" ? v : "");

// The session is signed before a new or changed account is saved, so a
// signing failure leaves no half-created account behind.
async function signedIn(env, acct, headers, save = false, extra = {}) {
  const token = await signSession(env, acct.email, undefined, acct.session_version || 0);
  if (save) await saveAccount(env, acct);
  return json({ token, me: await meFor(env, acct), ...extra }, 200, headers);
}

async function signup(request, env, headers) {
  const body = await readJson(request);
  const email = cleanEmail(body.email);
  if (!EMAIL_RE.test(email) || email.length > 254) return json({ error: "invalid_email" }, 400, headers);
  if (str(body.password).length < MIN_PASSWORD) return json({ error: "weak_password" }, 400, headers);
  if (await getAccount(env, email)) return json({ error: "exists" }, 409, headers);
  const acct = newAccount(email);
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
  const new_account = !acct;
  let save = true;
  let unbound = null;
  if (!acct) acct = newAccount(id.email, { googleSub: id.sub }); // free: no access until a Stripe subscription
  else if (!isVerified(acct)) { // a password squat on a verified address
    unbound = takeOver(acct, id.sub);
    forgetMembership(acct.email); // the owner's email lookup must not see the squatter's cached "no"
  } else if (acct.google_sub !== id.sub) acct.google_sub = id.sub;
  else save = false;
  const res = await signedIn(env, acct, headers, save, { new_account });
  if (unbound) await releaseClaim(env, unbound, acct.email);
  return res;
}

// After a takeover the squatter's customer is bound to nobody: their own
// account may claim it. A failure here never fails the owner's sign-in.
async function releaseClaim(env, customer, email) {
  try {
    if ((await env.SUBS.get(`claim:${customer}`)) === email) await env.SUBS.delete(`claim:${customer}`);
  } catch (e) { console.warn("claim release failed"); }
}

// The account behind a valid session token minted since its last takeover,
// else null (-> 401).
async function requireAccount(request, env) {
  const session = await sessionFromRequest(request, env);
  const acct = session ? await getAccount(env, session.email) : null;
  return acct && sessionCurrent(acct, session) ? acct : null;
}

// Binds the Stripe customer of a completed subscription Checkout Session to
// the account: the payer holds the receipt (the session id from the success
// redirect of our Checkout or the Payment Link), so the bound customer's
// subscription is theirs whatever email they typed. The payer email is typed
// too, so it verifies nothing. A session our checkout made for another
// account, or for this one before a takeover (client_reference_id: not the
// account's current reference), is refused, and so is a customer already
// bound to another account (claim:<customer>, written by its first claim:
// one payment, one account); a claimed subscription that is or was trialing
// uses up the account's one trial.
async function claim(request, env, headers) {
  const acct = await requireAccount(request, env);
  if (!acct) return json({ error: "unauthorized" }, 401, headers);
  const id = str((await readJson(request)).session_id);
  if (!CHECKOUT_SESSION_RE.test(id)) return json({ error: "bad_session" }, 400, headers);
  if (!env.STRIPE_SECRET_KEY) return json({ error: "stripe_unavailable" }, 502, headers);
  let session;
  try {
    session = await stripeGet(env, `/checkout/sessions/${id}?expand[]=subscription`);
  } catch (e) {
    if (e.status === 404) return json({ error: "unknown_session" }, 404, headers);
    console.warn("claim failed:", e && e.message);
    return json({ error: "stripe_unavailable" }, 502, headers);
  }
  const customer = typeof session.customer === "string" ? session.customer : session.customer && session.customer.id;
  if (session.status !== "complete" || session.mode !== "subscription" || !customer || typeof customer !== "string") {
    return json({ error: "bad_session" }, 400, headers);
  }
  if (session.client_reference_id && session.client_reference_id !== (await accountRef(acct))) {
    return json({ error: "wrong_account" }, 403, headers);
  }
  const holder = await env.SUBS.get(`claim:${customer}`);
  if (holder && holder !== acct.email) return json({ error: "already_claimed" }, 409, headers);
  const sub = session.subscription && typeof session.subscription === "object" ? session.subscription : null;
  if (sub && (sub.status === "trialing" || sub.trial_end)) acct.trial_used = true;
  acct.stripe_customer = customer;
  acct.claimed_at = new Date(Date.now()).toISOString();
  if (holder !== acct.email) await env.SUBS.put(`claim:${customer}`, acct.email); // before the account: a retry finds it ours
  await saveAccount(env, acct);
  forgetMembership(acct.email); // the next answer must see the new customer
  return json({ me: await meFor(env, acct) }, 200, headers);
}

// A Stripe Checkout Session URL for the signed-in account (card up front;
// the Stripe trial only while the account has never had a subscription).
// Membership is re-asked strictly (?fresh=1 rules), then the account's whole
// history uncached (the bound customer, its own checkouts, the customers with
// its email, a customer a takeover released). Never a second subscription:
// any one that has not ended is 409 already_member (active, trialing) or
// payment_issue (past_due, unpaid, incomplete, paused: the card is fixed in
// the portal), and a released customer's is contact_support unless another
// account has claimed it. Any one at all means no trial (recorded as
// trial_used, one KV write). A Stripe failure on any check is 503 with no
// fallback: never sell on a failed check.
async function checkout(request, env, headers) {
  const acct = await requireAccount(request, env);
  if (!acct) return json({ error: "unauthorized" }, 401, headers);
  let status, history;
  try {
    status = await meFor(env, acct, true, true);
    if (status.member) return json({ error: "already_member", me: status }, 409, headers);
    history = await checkoutHistory(env, acct);
    if (history.had) status = { ...status, trial_available: false };
    if (history.open) {
      const live = history.open.status === "active" || history.open.status === "trialing";
      return json({ error: live ? "already_member" : "payment_issue", me: status }, 409, headers);
    }
    if (history.released) {
      const holder = await env.SUBS.get(`claim:${acct.released_customer}`);
      if (!holder || holder === acct.email) return json({ error: "contact_support", me: status }, 409, headers);
    }
  } catch (e) {
    console.warn("checkout check failed:", e && e.message); // status + path, never Stripe's text
    return json({ error: "checkout_unavailable", fallback_url: "" }, 503, headers);
  }
  if (history.had && trialAvailable(acct)) {
    acct.trial_used = true;
    try { await saveAccount(env, acct); } catch (e) { console.warn("trial_used write failed"); } // the checkout still has no trial
  }
  try {
    return json({ url: await checkoutUrl(env, acct, status, history.gone) }, 200, headers);
  } catch (e) {
    console.warn("checkout unavailable:", e && e.message); // status + path, never Stripe's text
    return json({ error: "checkout_unavailable", fallback_url: e && e.unsafe ? "" : status.subscribe_url }, 503, headers);
  }
}

// A Stripe billing portal session (cancel, card, invoices) for the account's
// customer: the bound one, else the customer of the subscription membership
// found for it (its own checkout, or its verified email).
async function portal(request, env, headers) {
  const acct = await requireAccount(request, env);
  if (!acct) return json({ error: "unauthorized" }, 401, headers);
  try {
    const customer = await portalCustomer(env, acct);
    if (!customer) return json({ error: "no_customer" }, 404, headers);
    return json({ url: await portalUrl(env, customer) }, 200, headers);
  } catch (e) {
    console.warn("portal unavailable:", e && e.message);
    return json({ error: "portal_unavailable", fallback_url: env.STRIPE_PORTAL_LINK || "" }, 503, headers);
  }
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
                "/api/checkout": checkout, "/api/portal": portal, "/api/logout": logout };
const GETS = { "/api/me": me, "/api/deals": deals };

export async function handleApi(request, env, url) {
  const headers = { "Cache-Control": "no-store", ...corsHeaders(request, env) };
  const path = url.pathname;
  try {
    if (request.method === "POST" && path === "/api/publish") return await publish(request, env);
    const route = request.method === "POST" ? POSTS[path] : request.method === "GET" ? GETS[path] : null;
    if (!route) return json({ error: "not_found" }, 404, headers);
    if (NEEDS_SIGNING.includes(path) && !env.SESSION_SECRET) return json({ error: "signin_unavailable" }, 503, headers);
    const bucket = path.slice(5); // "signup" | "login" | "google" | "claim" | "checkout" | ...
    if (LIMITS_PER_IP_PER_HOUR[bucket] && overLimit(request, bucket, LIMITS_PER_IP_PER_HOUR[bucket])) {
      return json({ error: "rate_limited" }, 429, headers);
    }
    return await route(request, env, headers, url);
  } catch (e) {
    console.error("api failure:", request.method, path, e && e.message); // never the body or headers
    return json({ error: "internal" }, 500, headers);
  }
}
