/**
 * Membership for the paywall (docs/paywall.md). A member is anyone still
 * inside the free trial that started at sign-up, or whose account reaches an
 * active or trialing Stripe subscription one of two ways:
 *   (a) the Stripe customer bound to the account by POST /api/claim, or
 *   (b) a customer with the account's email, only when the address is
 *       verified (Google sign-in), so a password sign-up with someone else's
 *       address inherits nothing, whatever a Checkout payer typed.
 *
 * Stripe's answer is cached 5 minutes in isolate memory (member:<email>), and
 * ?fresh=1 bypasses it at most once a minute per account (fresh:<email>). A
 * Stripe failure counts as "not subscribed" for that one request (logged,
 * never cached), so an outage can never lock a trial user out or persist.
 */

import { memGet, memSet, memDel } from "./util.js";
import { isVerified } from "./auth.js";

const STRIPE_API = "https://api.stripe.com/v1";
const MEMBER_TTL_MS = 5 * 60 * 1000;
const FRESH_EVERY_MS = 60 * 1000;
const MAX_PAGES = 20; // 2,000 subscriptions per status: far beyond this list

// GET from Stripe as JSON; throws with .status on any non-2xx answer.
export async function stripeGet(env, path) {
  const res = await fetch(`${STRIPE_API}${path}`, { headers: { Authorization: `Basic ${btoa(`${env.STRIPE_SECRET_KEY}:`)}` } });
  if (!res.ok) {
    const err = new Error(`stripe ${res.status} on ${path.split("?")[0]}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

const live = (s) => s.status === "active" || s.status === "trialing";

async function customerSubscribed(env, customerId) {
  const subs = (await stripeGet(env, `/subscriptions?customer=${encodeURIComponent(customerId)}&status=all&limit=100`)).data || [];
  return subs.some(live);
}

// As dealradar/publish/members.py does: page the active and trialing
// subscriptions with their customers expanded and compare emails lowercased
// (Stripe's customers?email= filter is exact-case).
async function emailSubscribed(env, email) {
  for (const status of ["active", "trialing"]) {
    let after = "";
    for (let page = 0; page < MAX_PAGES; page++) {
      const body = await stripeGet(env, `/subscriptions?status=${status}&limit=100&expand[]=data.customer${after}`);
      const data = body.data || [];
      for (const s of data) {
        const c = s.customer;
        if (c && typeof c === "object" && String(c.email || "").trim().toLowerCase() === email) return true;
      }
      if (!body.has_more || !data.length) break;
      after = `&starting_after=${encodeURIComponent(data[data.length - 1].id)}`;
    }
  }
  return false;
}

// true/false from Stripe, null when it could not be asked.
async function stripeSubscribed(env, acct) {
  if (!env.STRIPE_SECRET_KEY) {
    console.warn("stripe check skipped: STRIPE_SECRET_KEY unset");
    return null;
  }
  try {
    if (acct.stripe_customer && (await customerSubscribed(env, acct.stripe_customer))) return true;
    return isVerified(acct) ? await emailSubscribed(env, acct.email) : false;
  } catch (e) {
    console.warn("stripe check failed:", e && e.message);
    return null;
  }
}

// Drops the cached answer, e.g. right after a claim bound a customer.
export function forgetMembership(email) { memDel(`member:${email}`); }

export async function isSubscribed(env, acct, fresh = false) {
  const email = acct.email;
  if (!acct.stripe_customer && !isVerified(acct)) return false; // nothing ties this account to Stripe: no call, no cache
  if (fresh) { // honored once a minute per account; otherwise the cache answers
    if (memGet(`fresh:${email}`)) fresh = false;
    else memSet(`fresh:${email}`, true, FRESH_EVERY_MS);
  }
  if (!fresh) {
    const cached = memGet(`member:${email}`);
    if (cached !== undefined) return cached;
  }
  const member = await stripeSubscribed(env, acct);
  if (member === null) return false;
  memSet(`member:${email}`, member, MEMBER_TTL_MS);
  return member;
}

// The `me` object every signed-in response carries.
export async function meFor(env, acct, fresh = false) {
  const subscribed = await isSubscribed(env, acct, fresh);
  const trial = Date.now() < (Date.parse(acct.trial_ends_at || "") || 0);
  const link = env.STRIPE_PAYMENT_LINK || "";
  return {
    email: acct.email,
    member: subscribed || trial,
    reason: subscribed ? "subscribed" : trial ? "trial" : "expired",
    trial_ends_at: acct.trial_ends_at || null,
    email_verified: isVerified(acct),
    claimed: !!acct.stripe_customer,
    subscribe_url: link ? `${link}${link.includes("?") ? "&" : "?"}prefilled_email=${encodeURIComponent(acct.email)}` : "",
    portal_url: env.STRIPE_PORTAL_LINK || "",
  };
}
