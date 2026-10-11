/**
 * Membership for the paywall (docs/paywall.md). There is no card-less trial:
 * a member is an account that reaches an active or trialing Stripe
 * subscription (a card on file; Stripe runs the free trial) one of two ways:
 *   (a) the Stripe customer bound to the account by POST /api/claim, or
 *   (b) a customer with the account's email, only when the address is
 *       verified (Google sign-in), so a password sign-up with someone else's
 *       address inherits nothing, whatever a Checkout payer typed.
 *
 * Stripe's answer, the best live subscription as {status, trial_end} or null,
 * is cached 5 minutes in isolate memory (member:<email>), and ?fresh=1
 * bypasses it at most once a minute per account (fresh:<email>). A Stripe
 * failure counts as "not a member" for that one request (logged, never
 * cached), so an outage can never persist.
 */

import { memGet, memSet, memDel } from "./util.js";
import { isVerified } from "./auth.js";

const STRIPE_API = "https://api.stripe.com/v1";
const MEMBER_TTL_MS = 5 * 60 * 1000;
const FRESH_EVERY_MS = 60 * 1000;
const MAX_PAGES = 20; // 2,000 subscriptions per status: far beyond this list
const DEFAULT_TRIAL_DAYS = 7;

const stripeAuth = (env) => `Basic ${btoa(`${env.STRIPE_SECRET_KEY}:`)}`;

// Stripe's JSON; throws with .status on any non-2xx answer, carrying only the
// status and the path (never Stripe's own error text).
async function stripeJson(res, path) {
  if (!res.ok) {
    const err = new Error(`stripe ${res.status} on ${path.split("?")[0]}`);
    err.status = res.status;
    throw err;
  }
  try { return await res.json(); } catch (e) { throw new Error(`stripe sent no JSON on ${path.split("?")[0]}`); }
}

// GET from Stripe as JSON; throws with .status on any non-2xx answer.
export async function stripeGet(env, path) {
  return stripeJson(await fetch(`${STRIPE_API}${path}`, { headers: { Authorization: stripeAuth(env) } }), path);
}

// POST a form to Stripe, built from an ordered list of [key, value] pairs.
// Every value is form-encoded, so a literal "{" goes out as %7B and Stripe
// reads the brace back. Same errors as stripeGet.
export async function stripePost(env, path, params, headers = {}) {
  const body = new URLSearchParams(params.map(([k, v]) => [k, String(v)])).toString();
  const res = await fetch(`${STRIPE_API}${path}`, {
    method: "POST", body,
    headers: { Authorization: stripeAuth(env), "Content-Type": "application/x-www-form-urlencoded", ...headers },
  });
  return stripeJson(res, path);
}

// The Stripe trial of a first checkout, in days (0 = no trial).
export function trialDays(env) {
  const n = Number.parseInt(env.TRIAL_DAYS, 10);
  return Number.isFinite(n) && n >= 0 ? n : DEFAULT_TRIAL_DAYS;
}

// One trial per account: gone once a claim saw one, or once any Stripe
// customer is bound (that customer has subscribed before).
export const trialAvailable = (acct) => !acct.trial_used && !acct.stripe_customer;

// The best live subscription of a list as {status, trial_end}: active beats
// trialing; null when none is live.
function bestLive(subs) {
  const pick = subs.find((s) => s.status === "active") || subs.find((s) => s.status === "trialing");
  return pick ? { status: pick.status, trial_end: pick.trial_end || null } : null;
}

async function customerSubscription(env, customerId) {
  return bestLive((await stripeGet(env, `/subscriptions?customer=${encodeURIComponent(customerId)}&status=all&limit=100`)).data || []);
}

// As dealradar/publish/members.py does: page the active, then the trialing
// subscriptions with their customers expanded and compare emails lowercased
// (Stripe's customers?email= filter is exact-case). Active is asked first, so
// it beats trialing.
async function emailSubscription(env, email) {
  for (const status of ["active", "trialing"]) {
    let after = "";
    for (let page = 0; page < MAX_PAGES; page++) {
      const body = await stripeGet(env, `/subscriptions?status=${status}&limit=100&expand[]=data.customer${after}`);
      const data = body.data || [];
      for (const s of data) {
        const c = s.customer;
        if (c && typeof c === "object" && String(c.email || "").trim().toLowerCase() === email) return bestLive([s]);
      }
      if (!body.has_more || !data.length) break;
      after = `&starting_after=${encodeURIComponent(data[data.length - 1].id)}`;
    }
  }
  return null;
}

// {status, trial_end} or null from Stripe; undefined when it could not be asked.
async function stripeSubscription(env, acct) {
  if (!env.STRIPE_SECRET_KEY) {
    console.warn("stripe check skipped: STRIPE_SECRET_KEY unset");
    return undefined;
  }
  try {
    const bound = acct.stripe_customer ? await customerSubscription(env, acct.stripe_customer) : null;
    if (bound) return bound;
    return isVerified(acct) ? await emailSubscription(env, acct.email) : null;
  } catch (e) {
    console.warn("stripe check failed:", e && e.message);
    return undefined;
  }
}

// Drops the cached answer, e.g. right after a claim bound a customer.
export function forgetMembership(email) { memDel(`member:${email}`); }

// The account's best live subscription ({status, trial_end}) or null.
async function liveSubscription(env, acct, fresh = false) {
  const email = acct.email;
  if (!acct.stripe_customer && !isVerified(acct)) return null; // nothing ties this account to Stripe: no call, no cache
  if (fresh) { // honored once a minute per account; otherwise the cache answers
    if (memGet(`fresh:${email}`)) fresh = false;
    else memSet(`fresh:${email}`, true, FRESH_EVERY_MS);
  }
  if (!fresh) {
    const cached = memGet(`member:${email}`);
    if (cached !== undefined) return cached;
  }
  const sub = await stripeSubscription(env, acct);
  if (sub === undefined) return null;
  memSet(`member:${email}`, sub, MEMBER_TTL_MS);
  return sub;
}

// The `me` object every signed-in response carries.
export async function meFor(env, acct, fresh = false) {
  const sub = await liveSubscription(env, acct, fresh);
  const reason = sub ? (sub.status === "active" ? "subscribed" : "trial") : acct.stripe_customer ? "lapsed" : "none";
  const link = env.STRIPE_PAYMENT_LINK || "";
  return {
    email: acct.email,
    member: !!sub,
    reason,
    trial_ends_at: reason === "trial" && sub.trial_end ? new Date(sub.trial_end * 1000).toISOString() : null,
    trial_days: trialDays(env),
    trial_available: trialAvailable(acct),
    email_verified: isVerified(acct),
    claimed: !!acct.stripe_customer,
    subscribe_url: link ? `${link}${link.includes("?") ? "&" : "?"}prefilled_email=${encodeURIComponent(acct.email)}` : "",
    portal_url: env.STRIPE_PORTAL_LINK || "",
  };
}
