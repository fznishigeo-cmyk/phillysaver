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
 * Stripe's answer, {sub, had, customer} (the best live subscription as
 * {status, trial_end} or null; whether any subscription of the account was
 * seen, which uses up its trial; the customer its billing portal opens on),
 * is cached 5 minutes in isolate memory (member:<email>), and ?fresh=1
 * bypasses it at most once a minute per account (fresh:<email>). A Stripe
 * failure counts as "not a member" for that one request (logged, never
 * cached), so an outage can never persist; checkout asks strictly instead,
 * re-reads the account's history uncached (checkoutHistory) and never sells
 * on a failed check. A bound customer that Stripe says does not exist for
 * this key (resource_missing: deleted, or made in the other mode) is gone,
 * not down.
 */

import { memGet, memSet, memDel } from "./util.js";
import { isVerified } from "./auth.js";

const STRIPE_API = "https://api.stripe.com/v1";
const MEMBER_TTL_MS = 5 * 60 * 1000;
const FRESH_EVERY_MS = 60 * 1000;
const MAX_PAGES = 20; // 2,000 subscriptions per status: far beyond this list
const DEFAULT_TRIAL_DAYS = 7;
const NOTHING = { sub: null, had: false, customer: null };
const LIVE = ["active", "trialing"];
// Statuses after which Stripe bills nothing more; every other one (active,
// trialing, past_due, unpaid, incomplete, paused) may still bill.
const ENDED = ["canceled", "incomplete_expired"];
const NOT_ENDED = [...LIVE, "past_due", "unpaid", "incomplete", "paused"];

const enc = new TextEncoder();
const stripeAuth = (env) => `Basic ${btoa(`${env.STRIPE_SECRET_KEY}:`)}`;

// Stripe's JSON; throws with .status (and Stripe's error .code, such as
// "resource_missing") on any non-2xx answer, carrying only those and the path
// (never Stripe's own error text).
async function stripeJson(res, path) {
  if (!res.ok) {
    const err = new Error(`stripe ${res.status} on ${path.split("?")[0]}`);
    err.status = res.status;
    try {
      const code = ((await res.json()).error || {}).code;
      if (typeof code === "string") err.code = code;
    } catch (e) { /* no JSON body: no code */ }
    throw err;
  }
  try { return await res.json(); } catch (e) { throw new Error(`stripe sent no JSON on ${path.split("?")[0]}`); }
}

// Stripe's answer for an object this key cannot see (a customer deleted, or
// made in the other mode or another account): 400/404 resource_missing. A
// 429, a 5xx or a network error is Stripe being unavailable, never this.
export const isMissing = (e) => !!e && (e.status === 400 || e.status === 404) && e.code === "resource_missing";

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
// SHA-256("wowpriced-acct:" + email) while the account's session_version is 0
// (so every reference made before takeovers stays the same), else of
// SHA-256("wowpriced-acct:" + email + ":" + session_version): a takeover
// retires the squatter-era reference, whose checkouts then count for nobody by (c).
export async function accountRef(acct) {
  const version = acct.session_version || 0;
  const text = version ? `wowpriced-acct:${acct.email}:${version}` : `wowpriced-acct:${acct.email}`;
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(text)));
  return [...digest].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

// The Stripe trial of a first checkout, in days (0 = no trial).
export function trialDays(env) {
  const n = Number.parseInt(env.TRIAL_DAYS, 10);
  return Number.isFinite(n) && n >= 0 ? n : DEFAULT_TRIAL_DAYS;
}

// One trial per account and email address, as far as the record knows: gone
// once a claim or a checkout saw a subscription, or once any Stripe customer
// is bound (that customer has subscribed before). me also asks Stripe
// (`had`), and checkout the customers with the account's email.
export const trialAvailable = (acct) => !acct.trial_used && !acct.stripe_customer;

// Stripe's customer id on a subscription: an id, or the expanded object.
const customerOf = (s) => (typeof s.customer === "string" ? s.customer : (s.customer && s.customer.id) || null);

// The subscription to act on in a list: active beats trialing beats any other
// that has not ended (past_due, unpaid, incomplete, paused); null when every
// one has ended (canceled, incomplete_expired).
function bestOpen(subs) {
  return subs.find((s) => s.status === "active") || subs.find((s) => s.status === "trialing")
    || subs.find((s) => !ENDED.includes(s.status)) || null;
}

// The best live subscription of a list as {status, trial_end}: active beats
// trialing; null when none is live.
function bestLive(subs) {
  const pick = bestOpen(subs);
  return pick && LIVE.includes(pick.status) ? { status: pick.status, trial_end: pick.trial_end || null } : null;
}

// Every subscription of one customer, any status.
async function customerSubscriptions(env, customerId) {
  return (await stripeGet(env, `/subscriptions?customer=${encodeURIComponent(customerId)}&status=all&limit=100`)).data || [];
}

// The same, or null when Stripe says the customer does not exist for this key.
async function subscriptionsOrGone(env, customerId) {
  try {
    return await customerSubscriptions(env, customerId);
  } catch (e) {
    if (isMissing(e)) return null;
    throw e;
  }
}

// (c) The subscriptions the Worker's checkouts made for the account under its
// current reference, any status. Search is eventually consistent (about a
// minute behind).
async function refSubscriptions(env, acct) {
  const query = encodeURIComponent(`metadata['wowpriced_ref']:'${await accountRef(acct)}'`);
  return (await stripeGet(env, `/subscriptions/search?query=${query}&limit=100`)).data || [];
}

// Checkout's own check, uncached and strict (any Stripe failure throws):
// every subscription, any status, of the bound customer, of the account's own
// checkouts (the reference), of the customers whose email is exactly
// acct.email (Stripe's filter is exact-case; the Worker's checkouts send the
// lowercased address) and of the customer a takeover released. Answers
// {open, released, had, gone}: open, the best of the account's own that has
// not ended (bestOpen); released, whether the released customer still has
// one; had, whether any of the account's own exists (no trial; a gone bound
// customer counts too); gone, whether Stripe says the bound customer does not
// exist for this key (checkout then bills customer_email instead).
export async function checkoutHistory(env, acct) {
  const bound = acct.stripe_customer ? await subscriptionsOrGone(env, acct.stripe_customer) : [];
  const subs = [...(bound || []), ...(await refSubscriptions(env, acct))];
  const customers = (await stripeGet(env, `/customers?email=${encodeURIComponent(acct.email)}&limit=100`)).data || [];
  for (const c of customers) {
    if (c.id !== acct.stripe_customer) subs.push(...(await customerSubscriptions(env, c.id)));
  }
  // A verified address also counts its (b) subscriptions compared lowercased:
  // customers?email= above is exact-case, and a payer who typed H1@Example.com
  // must still never be sold a second subscription (review finding).
  if (isVerified(acct)) {
    const viaEmail = await emailSubscription(env, acct.email, NOT_ENDED);
    if (viaEmail && !subs.some((s) => s.id && s.id === viaEmail.id)) subs.push(viaEmail);
  }
  const released = acct.released_customer ? (await subscriptionsOrGone(env, acct.released_customer)) || [] : [];
  return { open: bestOpen(subs), released: !!bestOpen(released), had: bound === null || subs.length > 0, gone: bound === null };
}

// As dealradar/publish/members.py does: page the subscriptions of each of
// statuses in turn (LIVE: the active, then the trialing) with their customers
// expanded and compare emails lowercased (Stripe's customers?email= filter is
// exact-case). An earlier status beats a later one, so active beats trialing.
// The subscription found, or null.
async function emailSubscription(env, email, statuses) {
  for (const status of statuses) {
    let after = "";
    for (let page = 0; page < MAX_PAGES; page++) {
      const body = await stripeGet(env, `/subscriptions?status=${status}&limit=100&expand[]=data.customer${after}`);
      const data = body.data || [];
      for (const s of data) {
        const c = s.customer;
        if (c && typeof c === "object" && String(c.email || "").trim().toLowerCase() === email) return s;
      }
      if (!body.has_more || !data.length) break;
      after = `&starting_after=${encodeURIComponent(data[data.length - 1].id)}`;
    }
  }
  return null;
}

// {sub, had, customer} from Stripe: (a), then (c), then (b), the first live
// subscription wins; undefined when Stripe could not be asked. customer is
// the one the account's billing portal opens on: the live subscription's
// (whichever way it was found), else that of one of (a) or (c) that has not
// ended (a payment to fix), else, for a verified account, that of one on its
// email that has not ended (the same; never a membership), else the bound
// customer, else null. A bound customer gone for this key counts as history
// and opens nothing.
async function stripeAnswer(env, acct) {
  if (!env.STRIPE_SECRET_KEY) {
    console.warn("stripe check skipped: STRIPE_SECRET_KEY unset");
    return undefined;
  }
  try {
    const bound = acct.stripe_customer ? await subscriptionsOrGone(env, acct.stripe_customer) : [];
    const mine = bound || [];
    const own = bestLive(mine) ? [] : await refSubscriptions(env, acct);
    const open = bestOpen([...mine, ...own]);
    let live = open && LIVE.includes(open.status) ? open : null;
    // (b): a live one, else, when (a) and (c) have nothing open, one that may still bill
    const mail = !live && isVerified(acct) ? await emailSubscription(env, acct.email, open ? LIVE : NOT_ENDED) : null;
    if (mail && LIVE.includes(mail.status)) live = mail;
    const sub = live ? { status: live.status, trial_end: live.trial_end || null } : null;
    const customer = customerOf(live || open || mail || {}) || (bound && acct.stripe_customer) || null;
    return { sub, had: !!sub || !!mail || bound === null || mine.length > 0 || own.length > 0, customer };
  } catch (e) {
    console.warn("stripe check failed:", e && e.message);
    return undefined;
  }
}

// Drops the cached answer, e.g. right after a claim bound a customer.
export function forgetMembership(email) { memDel(`member:${email}`); }

// The account's Stripe answer, {sub, had, customer}. A failure is NOTHING
// for this request, or a throw when strict.
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

// The customer the account's billing portal opens on (stripeAnswer), or
// null; throws when Stripe could not be asked.
export async function portalCustomer(env, acct) {
  return (await membership(env, acct, false, true)).customer || null;
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
