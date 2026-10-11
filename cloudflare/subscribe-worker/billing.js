/**
 * Stripe Checkout and the customer portal for the paywall (docs/paywall.md
 * "Checkout and billing"). The trial is Stripe's: a first checkout carries
 * TRIAL_DAYS of trial with the card collected up front, then the price bills
 * monthly until cancelled (in the portal, or by the operator on request).
 *
 *   checkoutUrl(env, acct, me) a new subscription Checkout Session's URL
 *   portalUrl(env, acct)       a billing portal session's URL for the bound customer
 *
 * Both throw on any Stripe error or missing setting; api.js answers 503 with
 * the Payment Link or portal link as a fallback and logs status + path only.
 *
 * The price is STRIPE_PRICE_ID when set, else the price of the Payment Link
 * STRIPE_PAYMENT_LINK, looked up once and cached 6 hours in isolate memory
 * and a day in KV ("stripe:price" = {link, price}), so a cold isolate costs at
 * most one KV read. The portal configuration is STRIPE_PORTAL_CONFIG when
 * set, else the active one scripts/stripe_setup.py made (metadata
 * app=wowpriced), cached the same way ("stripe:portal" = {mode, config});
 * without one Stripe uses the dashboard's default.
 */

import { memGet, memSet } from "./util.js";
import { accountRef, stripeGet, stripePost, trialDays } from "./membership.js";

const DEFAULT_SITE = "https://wowpriced.com";
const CACHE_MEMORY_MS = 6 * 3600 * 1000;
const CACHE_KV_TTL_S = 86400;
const PRICE_KV_KEY = "stripe:price";
const PORTAL_KV_KEY = "stripe:portal";
const LIST_PAGES = 5; // 500 active Payment Links or portal configurations
const IDEMPOTENCY_SLOT_MS = 10 * 60 * 1000;

export const siteUrl = (env) => (env.SITE_URL || DEFAULT_SITE).replace(/\/+$/, "");

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

// The portal configuration (bpc_...) or "" for the dashboard default. A
// cached id is kept per key mode: a test-mode id means nothing to a live key.
async function portalConfig(env) {
  if (env.STRIPE_PORTAL_CONFIG) return env.STRIPE_PORTAL_CONFIG;
  const mode = /^[a-z]+_live_/.test(env.STRIPE_SECRET_KEY) ? "live" : "test";
  const memo = memGet(`portal:${mode}`);
  if (memo) return memo;
  let cached = null;
  try { cached = JSON.parse(await env.SUBS.get(PORTAL_KV_KEY)); } catch (e) { /* none or unreadable: discover */ }
  let config = cached && cached.mode === mode && typeof cached.config === "string" ? cached.config : "";
  if (!config) {
    const hit = await findListed(env, "/billing_portal/configurations?active=true",
                                 (c) => (c.metadata || {}).app === "wowpriced");
    if (!hit || typeof hit.id !== "string") return ""; // never cache a miss
    config = hit.id;
    try {
      await env.SUBS.put(PORTAL_KV_KEY, JSON.stringify({ mode, config }), { expirationTtl: CACHE_KV_TTL_S });
    } catch (e) { console.warn("portal cache write failed"); }
  }
  memSet(`portal:${mode}`, config, CACHE_MEMORY_MS);
  return config;
}

// A subscription Checkout Session for the account. A bound customer is
// reused (Stripe keeps one customer per payer); otherwise Checkout creates
// one from the account email. The card is the only payment method (named,
// as the Payment Link does: this account has refused the dashboard's
// defaults). The trial rides along only while me.trial_available. The
// Idempotency-Key (ref + 10-minute slot) makes a double click one session.
export async function checkoutUrl(env, acct, me) {
  if (!env.STRIPE_SECRET_KEY) throw new Error("STRIPE_SECRET_KEY unset");
  const price = await priceId(env);
  if (!price) throw new Error("no price: set STRIPE_PRICE_ID or an active STRIPE_PAYMENT_LINK");
  const ref = await accountRef(acct.email);
  const site = siteUrl(env);
  const days = trialDays(env);
  const params = [
    ["mode", "subscription"],
    ["line_items[0][price]", price],
    ["line_items[0][quantity]", "1"],
    ["payment_method_types[0]", "card"],
    ["payment_method_collection", "always"],
    ["client_reference_id", ref],
    acct.stripe_customer ? ["customer", acct.stripe_customer] : ["customer_email", acct.email],
  ];
  if (me.trial_available && days > 0) params.push(["subscription_data[trial_period_days]", String(days)]);
  params.push(
    ["subscription_data[metadata][wowpriced_ref]", ref],
    // Stripe fills {CHECKOUT_SESSION_ID}: the braces go in literally (stripePost form-encodes them once).
    ["success_url", `${site}/welcome.html?session_id={CHECKOUT_SESSION_ID}`],
    ["cancel_url", `${site}/?checkout=cancel`],
  );
  const slot = Math.floor(Date.now() / IDEMPOTENCY_SLOT_MS);
  const session = await stripePost(env, "/checkout/sessions", params, { "Idempotency-Key": `${ref}-${slot}` });
  if (typeof session.url !== "string" || !session.url) throw new Error("checkout session without a url");
  return session.url;
}

// A billing portal session (cancel, change card, invoices) for the bound
// customer, under the wowpriced portal configuration when there is one.
export async function portalUrl(env, acct) {
  if (!env.STRIPE_SECRET_KEY) throw new Error("STRIPE_SECRET_KEY unset");
  const config = await portalConfig(env);
  const session = await stripePost(env, "/billing_portal/sessions", [
    ["customer", acct.stripe_customer],
    ...(config ? [["configuration", config]] : []),
    ["return_url", `${siteUrl(env)}/`],
  ]);
  if (typeof session.url !== "string" || !session.url) throw new Error("portal session without a url");
  return session.url;
}
