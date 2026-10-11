/**
 * Stripe Checkout and the customer portal for the paywall (docs/paywall.md
 * "Checkout and billing"). The trial is Stripe's: a first checkout carries
 * TRIAL_DAYS of trial with the card collected up front, then the price bills
 * monthly until cancelled (in the portal, or by the operator on request).
 *
 *   checkoutUrl(env, acct, me, gone) a subscription Checkout Session's URL
 *   portalUrl(env, customer)         a billing portal session's URL for that customer
 *
 * Both throw on any Stripe error or missing setting; api.js answers 503 with
 * the Payment Link or portal link as a fallback (none when checkout could not
 * expire the account's last open session) and logs status + path only.
 *
 * The price is STRIPE_PRICE_ID when set, else the price of the Payment Link
 * STRIPE_PAYMENT_LINK, looked up once and cached 6 hours in isolate memory
 * and a day in KV ("stripe:price" = {link, price}), so a cold isolate costs at
 * most one KV read. The portal configuration is STRIPE_PORTAL_CONFIG when
 * set, else the active one scripts/stripe_setup.py made (metadata
 * app=wowpriced), cached the same way ("stripe:portal" = {mode, config}) and
 * dropped when Stripe refuses it; without one Stripe uses the dashboard's
 * default. The account's last Checkout Session is kept in KV
 * ("checkout:<ref>" = {id, url, trial, params}, an hour), so a second
 * checkout reuses it while it is open or expires it first: one open session
 * per account.
 */

import { memGet, memSet, memDel } from "./util.js";
import { accountRef, stripeGet, stripePost, trialDays, isMissing } from "./membership.js";

const DEFAULT_SITE = "https://wowpriced.com";
const CACHE_MEMORY_MS = 6 * 3600 * 1000;
const CACHE_KV_TTL_S = 86400;
const PRICE_KV_KEY = "stripe:price";
const PORTAL_KV_KEY = "stripe:portal";
const LIST_PAGES = 5; // 500 active Payment Links or portal configurations
const IDEMPOTENCY_SLOT_MS = 10 * 60 * 1000;
// Stripe keeps a session open at least 30 minutes after it creates it; one
// more minute covers the clock difference between us and Stripe.
const SESSION_OPEN_S = 31 * 60;
const LAST_SESSION_KV_TTL_S = 3600; // longer than any session lives (41 minutes)
const SESSION_ID_RE = /^cs_[A-Za-z0-9_]{8,200}$/;

const enc = new TextEncoder();

export const siteUrl = (env) => (env.SITE_URL || DEFAULT_SITE).replace(/\/+$/, "");

const keyMode = (env) => (/^[a-z]+_live_/.test(env.STRIPE_SECRET_KEY) ? "live" : "test");

// A Payment Link URL as compared: origin (lowercased by URL) + path without a
// trailing slash, case kept; "" when it does not parse.
function linkKey(link) {
  try {
    const u = new URL(String(link || "").trim());
    return `${u.origin}${u.pathname.replace(/\/+$/, "")}`;
  } catch (e) { return ""; }
}

// The first object of a Stripe list (`path` has a query already) that `pred`
// accepts within LIST_PAGES pages, or null.
async function findListed(env, path, pred) {
  let after = "";
  for (let page = 0; page < LIST_PAGES; page++) {
    const body = await stripeGet(env, `${path}&limit=100${after}`);
    const data = body.data || [];
    const hit = data.find(pred);
    if (hit) return hit;
    if (!body.has_more || !data.length) break;
    after = `&starting_after=${encodeURIComponent(data[data.length - 1].id)}`;
  }
  return null;
}

// The price of the active Payment Link whose URL is `link`, "" when none matches.
async function discoverPrice(env, link) {
  const hit = await findListed(env, "/payment_links?active=true", (p) => linkKey(p.url) === link);
  if (!hit) return "";
  const items = await stripeGet(env, `/payment_links/${encodeURIComponent(hit.id)}/line_items?limit=1`);
  const price = items.data && items.data[0] && items.data[0].price;
  return (typeof price === "string" ? price : price && price.id) || "";
}

async function priceId(env) {
  if (env.STRIPE_PRICE_ID) return env.STRIPE_PRICE_ID;
  const link = linkKey(env.STRIPE_PAYMENT_LINK);
  if (!link) return "";
  const memo = memGet(`price:${link}`);
  if (memo) return memo;
  let cached = null;
  try { cached = JSON.parse(await env.SUBS.get(PRICE_KV_KEY)); } catch (e) { /* none or unreadable: discover */ }
  let price = cached && cached.link === link && typeof cached.price === "string" ? cached.price : "";
  if (!price) {
    price = await discoverPrice(env, link);
    if (!price) return ""; // never cache a miss: the next checkout looks again
    try { // a full KV write quota must not stop a checkout
      await env.SUBS.put(PRICE_KV_KEY, JSON.stringify({ link, price }), { expirationTtl: CACHE_KV_TTL_S });
    } catch (e) { console.warn("price cache write failed"); }
  }
  memSet(`price:${link}`, price, CACHE_MEMORY_MS);
  return price;
}

// The discovered portal configuration (bpc_...) or "" for the dashboard
// default. A cached id is kept per key mode: a test-mode id means nothing to
// a live key. fresh skips both caches (after Stripe refused the cached one).
async function portalConfig(env, fresh = false) {
  const mode = keyMode(env);
  if (!fresh) {
    const memo = memGet(`portal:${mode}`);
    if (memo) return memo;
    let cached = null;
    try { cached = JSON.parse(await env.SUBS.get(PORTAL_KV_KEY)); } catch (e) { /* none or unreadable: discover */ }
    if (cached && cached.mode === mode && typeof cached.config === "string" && cached.config) {
      memSet(`portal:${mode}`, cached.config, CACHE_MEMORY_MS);
      return cached.config;
    }
  }
  const hit = await findListed(env, "/billing_portal/configurations?active=true",
                               (c) => (c.metadata || {}).app === "wowpriced");
  if (!hit || typeof hit.id !== "string") return ""; // never cache a miss
  try {
    await env.SUBS.put(PORTAL_KV_KEY, JSON.stringify({ mode, config: hit.id }), { expirationTtl: CACHE_KV_TTL_S });
  } catch (e) { console.warn("portal cache write failed"); }
  memSet(`portal:${mode}`, hit.id, CACHE_MEMORY_MS);
  return hit.id;
}

// Drops the cached configuration, from memory and KV.
async function forgetPortalConfig(env) {
  memDel(`portal:${keyMode(env)}`);
  try { await env.SUBS.delete(PORTAL_KV_KEY); } catch (e) { console.warn("portal cache delete failed"); }
}

// Stripe refused the request itself (4xx), as opposed to being unavailable (429, 5xx).
const refused = (e) => !!e && e.status >= 400 && e.status < 500 && e.status !== 429;

// First 16 hex chars of SHA-256 of a Checkout Session's params (form-encoded).
async function paramsHash(params) {
  const body = new URLSearchParams(params).toString();
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(body)));
  return [...digest].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

// The account's last Checkout Session (KV checkout:<ref>), while Stripe still
// shows it open: its url when it was made with these very params, else it is
// expired here (an answer that it is no longer open is fine) and "" comes
// back, as when there is none. Any other Stripe failure throws, marked
// `unsafe`: an open session might remain, so api.js offers no Payment Link.
async function reuseOrExpire(env, ref, hash) {
  let last = null;
  try { last = JSON.parse(await env.SUBS.get(`checkout:${ref}`)); } catch (e) { /* none or unreadable */ }
  if (!last || typeof last.id !== "string" || !SESSION_ID_RE.test(last.id)) return "";
  try {
    let session;
    try {
      session = await stripeGet(env, `/checkout/sessions/${last.id}`);
    } catch (e) {
      if (isMissing(e)) return "";
      throw e;
    }
    if (session.status !== "open") return ""; // complete or expired: nothing left to pay
    if (last.params === hash && typeof session.url === "string" && session.url) return session.url;
    try {
      await stripePost(env, `/checkout/sessions/${last.id}/expire`, []);
    } catch (e) {
      if (!refused(e)) throw e; // a 4xx: it completed or expired meanwhile
    }
    return "";
  } catch (e) {
    e.unsafe = true;
    throw e;
  }
}

// A subscription Checkout Session for the account. A bound customer is
// reused (Stripe keeps one customer per payer) unless Stripe says it is gone
// for this key; otherwise Checkout creates one from the account email. The
// card is the only payment method (named, as the Payment Link does: this
// account has refused the dashboard's defaults). The trial rides along only
// while me.trial_available. One open session per account: the last one is
// reused while open with the same params, else expired before a new one, and
// every new one expires after 31 to 41 minutes. The Idempotency-Key (ref +
// 10-minute slot + params hash) makes a double click one session, and a
// change of params a new one instead of Stripe's 400.
export async function checkoutUrl(env, acct, me, gone = false) {
  if (!env.STRIPE_SECRET_KEY) throw new Error("STRIPE_SECRET_KEY unset");
  const price = await priceId(env);
  if (!price) throw new Error("no price: set STRIPE_PRICE_ID or an active STRIPE_PAYMENT_LINK");
  const ref = await accountRef(acct);
  const site = siteUrl(env);
  const trial = !!me.trial_available && trialDays(env) > 0;
  const params = [
    ["mode", "subscription"],
    ["line_items[0][price]", price],
    ["line_items[0][quantity]", "1"],
    ["payment_method_types[0]", "card"],
    ["payment_method_collection", "always"],
    ["client_reference_id", ref],
    acct.stripe_customer && !gone ? ["customer", acct.stripe_customer] : ["customer_email", acct.email],
  ];
  if (trial) params.push(["subscription_data[trial_period_days]", String(trialDays(env))]);
  params.push(
    ["subscription_data[metadata][wowpriced_ref]", ref],
    // Stripe fills {CHECKOUT_SESSION_ID}: the braces go in literally (stripePost form-encodes them once).
    ["success_url", `${site}/welcome.html?session_id={CHECKOUT_SESSION_ID}`],
    ["cancel_url", `${site}/?checkout=cancel`],
  );
  const hash = await paramsHash(params);
  const open = await reuseOrExpire(env, ref, hash);
  if (open) return open;
  const slot = Math.floor(Date.now() / IDEMPOTENCY_SLOT_MS);
  // From the slot's end, so a retry inside the slot sends the very same params.
  const expiresAt = Math.floor(((slot + 1) * IDEMPOTENCY_SLOT_MS) / 1000) + SESSION_OPEN_S;
  const session = await stripePost(env, "/checkout/sessions", [...params, ["expires_at", String(expiresAt)]],
                                   { "Idempotency-Key": `${ref}-${slot}-${hash}` });
  if (typeof session.url !== "string" || !session.url) throw new Error("checkout session without a url");
  try { // one write per new session; without it the next checkout cannot find this one (it still expires)
    await env.SUBS.put(`checkout:${ref}`, JSON.stringify({ id: session.id, url: session.url, trial, params: hash }),
                       { expirationTtl: LAST_SESSION_KV_TTL_S });
  } catch (e) { console.warn("checkout session write failed"); }
  return session.url;
}

// A billing portal session (cancel, change card, invoices) for `customer`,
// under STRIPE_PORTAL_CONFIG or the wowpriced portal configuration when there
// is one. A discovered configuration Stripe refuses (a 4xx: deactivated,
// replaced, another account's) is dropped from both caches and looked up
// again once; when that finds no other one, or Stripe refuses it too, the
// session goes without a configuration (the dashboard default).
export async function portalUrl(env, customer) {
  if (!env.STRIPE_SECRET_KEY) throw new Error("STRIPE_SECRET_KEY unset");
  const open = (config) => stripePost(env, "/billing_portal/sessions", [
    ["customer", customer],
    ...(config ? [["configuration", config]] : []),
    ["return_url", `${siteUrl(env)}/`],
  ]);
  const config = env.STRIPE_PORTAL_CONFIG || (await portalConfig(env));
  let session = null;
  try {
    session = await open(config);
  } catch (e) {
    if (!config || env.STRIPE_PORTAL_CONFIG || !refused(e)) throw e;
    await forgetPortalConfig(env);
    const again = await portalConfig(env, true);
    if (again && again !== config) {
      try { session = await open(again); } catch (e2) { if (!refused(e2)) throw e2; }
    }
    if (!session) session = await open("");
  }
  if (typeof session.url !== "string" || !session.url) throw new Error("portal session without a url");
  return session.url;
}
