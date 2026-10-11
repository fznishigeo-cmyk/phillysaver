/**
 * Membership for the paywall (docs/paywall.md). There is no card-less trial:
 * a member is an account that reaches an active or trialing Stripe
 * subscription (a card on file; Stripe runs the free trial) one of three ways:
 *   (a) the Stripe customer bound to the account by POST /api/claim,
 *   (c) a subscription the Worker's own checkout made for the account: its
 *       metadata wowpriced_ref is the account reference (Stripe's Search
 *       API, any status, one call), so a checkout counts even when its claim
 *       never landed, or
 *   (b) a customer with the account's email, only when the address is
 *       verified (Google sign-in), so a password sign-up with someone else's
 *       address inherits nothing, whatever a Checkout payer typed.
 *
 * Stripe's answer, {sub, had} (the best live subscription as {status,
 * trial_end} or null, and whether any subscription of the account was seen,
 * which uses up its trial), is cached 5 minutes in isolate memory
 * (member:<email>), and ?fresh=1 bypasses it at most once a minute per
 * account (fresh:<email>). A Stripe failure counts as "not a member" for that
 * one request (logged, never cached), so an outage can never persist;
 * checkout asks strictly instead and never sells on a failed check.
 */

import { memGet, memSet, memDel } from "./util.js";
import { isVerified } from "./auth.js";

const STRIPE_API = "https://api.stripe.com/v1";
const MEMBER_TTL_MS = 5 * 60 * 1000;
const FRESH_EVERY_MS = 60 * 1000;
const MAX_PAGES = 20; // 2,000 subscriptions per status: far beyond this list
const DEFAULT_TRIAL_DAYS = 7;
const NOTHING = { sub: null, had: false };

const enc = new TextEncoder();
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

// The account reference: Checkout's client_reference_id and the subscription
// metadata wowpriced_ref, the first 32 hex chars of
// SHA-256("wowpriced-acct:" + email).
export async function accountRef(email) {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(`wowpriced-acct:${email}`)));
  return [...digest].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

// The Stripe trial of a first checkout, in days (0 = no trial).
export function trialDays(env) {
  const n = Number.parseInt(env.TRIAL_DAYS, 10);
  return Number.isFinite(n) && n >= 0 ? n : DEFAULT_TRIAL_DAYS;
}

// One trial per account, as far as the record knows: gone once a claim or a
// checkout saw a subscription, or once any Stripe customer is bound (that
// customer has subscribed before). me also asks Stripe (`had`).
export const trialAvailable = (acct) => !acct.trial_used && !acct.stripe_customer;

// The best live subscription of a list as {status, trial_end}: active beats
// trialing; null when none is live.
function bestLive(subs) {
  const pick = subs.find((s) => s.status === "active") || subs.find((s) => s.status === "trialing");
  return pick ? { status: pick.status, trial_end: pick.trial_end || null } : null;
}

// Every subscription of one customer, any status.
async function customerSubscriptions(env, customerId) {
  return (await stripeGet(env, `/subscriptions?customer=${encodeURIComponent(customerId)}&status=all&limit=100`)).data || [];
}

// (c) The subscriptions the Worker's checkouts made for the account, any
// status. Search is eventually consistent (about a minute behind).
async function refSubscriptions(env, email) {
  const query = encodeURIComponent(`metadata['wowpriced_ref']:'${await accountRef(email)}'`);
  return (await stripeGet(env, `/subscriptions/search?query=${query}&limit=100`)).data || [];
}

// Checkout's history check: every subscription (any status) of the Stripe
// customers whose email is exactly `email` (Stripe's filter is exact-case;
// the Worker's checkouts send the lowercased address), as {sub, had}. Throws
// on any Stripe error.
export async function emailHistory(env, email) {
  const customers = (await stripeGet(env, `/customers?email=${encodeURIComponent(email)}&limit=100`)).data || [];
  let had = false;
  for (const c of customers) {
    const subs = await customerSubscriptions(env, c.id);
    const live = bestLive(subs);
    if (live) return { sub: live, had: true };
    had = had || subs.length > 0;
  }
  return { sub: null, had };
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

// {sub, had} from Stripe: (a), then (c), then (b), the first live
// subscription wins; undefined when Stripe could not be asked.
async function stripeAnswer(env, acct) {
  if (!env.STRIPE_SECRET_KEY) {
    console.warn("stripe check skipped: STRIPE_SECRET_KEY unset");
    return undefined;
  }
  try {
    const bound = acct.stripe_customer ? await customerSubscriptions(env, acct.stripe_customer) : [];
    let sub = bestLive(bound);
    const own = sub ? [] : await refSubscriptions(env, acct.email);
    sub = sub || bestLive(own) || (isVerified(acct) ? await emailSubscription(env, acct.email) : null);
    return { sub, had: !!sub || bound.length > 0 || own.length > 0 };
  } catch (e) {
    console.warn("stripe check failed:", e && e.message);
    return undefined;
  }
}

// Drops the cached answer, e.g. right after a claim bound a customer.
export function forgetMembership(email) { memDel(`member:${email}`); }

// The account's Stripe answer, {sub, had}. A failure is NOTHING for this
// request, or a throw when strict.
async function membership(env, acct, fresh, strict) {
  const email = acct.email;
  if (fresh) { // honored once a minute per account; otherwise the cache answers
    if (memGet(`fresh:${email}`)) fresh = false;
    else memSet(`fresh:${email}`, true, FRESH_EVERY_MS);
  }
  if (!fresh) {
    const cached = memGet(`member:${email}`);
    if (cached !== undefined) return cached;
  }
  const answer = await stripeAnswer(env, acct);
  if (answer === undefined) {
    if (strict) throw new Error("stripe membership check failed");
    return NOTHING;
  }
  memSet(`member:${email}`, answer, MEMBER_TTL_MS);
  return answer;
}

// The Payment Link for the account, "" without one.
function subscribeUrl(env, email) {
  const link = env.STRIPE_PAYMENT_LINK || "";
  return link ? `${link}${link.includes("?") ? "&" : "?"}prefilled_email=${encodeURIComponent(email)}` : "";
}

// The `me` object every signed-in response carries. strict: throws when
// Stripe could not be asked (POST /api/checkout never sells on a failed check).
export async function meFor(env, acct, fresh = false, strict = false) {
  const { sub, had } = await membership(env, acct, fresh, strict);
  const reason = sub ? (sub.status === "active" ? "subscribed" : "trial") : acct.stripe_customer ? "lapsed" : "none";
  return {
    email: acct.email,
    member: !!sub,
    reason,
    trial_ends_at: reason === "trial" && sub.trial_end ? new Date(sub.trial_end * 1000).toISOString() : null,
    trial_days: trialDays(env),
    trial_available: trialAvailable(acct) && !had,
    email_verified: isVerified(acct),
    claimed: !!acct.stripe_customer,
    subscribe_url: subscribeUrl(env, acct.email),
    portal_url: env.STRIPE_PORTAL_LINK || "",
  };
}
