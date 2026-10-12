/**
 * wowpriced Worker: the daily top-10 sign-up list plus the paywall API, in one
 * deployment with no server of our own. KV (binding SUBS) holds one key per
 * subscriber,
 *   sub:<email> -> {"ts", "token", "confirmed", "confirm_sent_at"}  (also in metadata)
 * No mail is sent from here: the pipeline (GitHub Actions) reads the list via
 * GET /list with the shared NEWSLETTER_SECRET and sends over SMTP, confirm
 * links first (double opt-in, subscribers.js), the daily email to confirmed
 * addresses only.
 *
 *   POST /subscribe              {email} as JSON or a form field -> 200 {ok:true};
 *                                a form post gets a 303 to <site>/subscribe.html?ok=1
 *   GET  /confirm?e=&t=          sets confirmed=true when the token matches -> 200 HTML
 *   GET  /unsubscribe?e=&t=      200 HTML page with one POST button "Unsubscribe"
 *   POST /unsubscribe?e=&t=      deletes when the token matches -> 200 HTML
 *                                (also the RFC 8058 one-click form mail apps send)
 *   GET  /list                   Authorization: Bearer <NEWSLETTER_SECRET>
 *                                -> {emails:[{email, token}]} confirmed only, at most 10k;
 *                                ?pending=1 -> unconfirmed addresses not yet mailed a link
 *   POST /list/confirm-sent      same bearer, {emails:[...]} -> 204, stamps confirm_sent_at
 *   /api/*                       the paywall: accounts, sessions, membership, Stripe
 *                                Checkout + billing portal and the members-only deals
 *                                (api.js, billing.js, docs/paywall.md)
 *   OPTIONS *                    CORS preflight, allowed origins only; else 404
 *
 * Bindings: SUBS (KV); secrets NEWSLETTER_SECRET, SESSION_SECRET,
 * STRIPE_SECRET_KEY (wrangler secret put); vars ALLOWED_ORIGINS (comma list,
 * default https://wowpriced.com, https://www.wowpriced.com and
 * https://fznishigeo-cmyk.github.io), GOOGLE_CLIENT_ID, STRIPE_PAYMENT_LINK,
 * STRIPE_PORTAL_LINK, STRIPE_PRICE_ID, STRIPE_YEARLY_PRICE_ID ($49/year; empty = unavailable),
 * STRIPE_PORTAL_CONFIG, SITE_URL,
 * TRIAL_DAYS, PBKDF2_ITERATIONS (wrangler.toml).
 */

import { EMAIL_RE, allowedOrigins, corsHeaders, json, safeEqual, readJson, overLimit } from "./util.js";
import { page, isConfirmed, getSub, putSub, confirm, unsubscribe } from "./subscribers.js";
import { handleApi } from "./api.js";

const MAX_POSTS_PER_IP_PER_HOUR = 20;
const LIST_MAX = 10000;
const CONFIRM_SENT_MAX = 1000;

function randomToken() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function readEmail(request) {
  const type = request.headers.get("Content-Type") || "";
  let email = "";
  try {
    if (type.includes("application/json")) email = (await request.json()).email;
    else email = (await request.formData()).get("email");
  } catch (e) { /* unreadable body: treated as an invalid address below */ }
  return String(email || "").trim().toLowerCase();
}

// Form posts land on the signup page of the site they came from, which shows
// the result. Only an allowed origin is honored (never an arbitrary Referer),
// and browsers send just the origin cross-site anyway, hence the fixed path.
function backToSite(request, env, query) {
  let origin = "";
  try { origin = new URL(request.headers.get("Referer") || "").origin; } catch (e) { /* none */ }
  const origins = allowedOrigins(env);
  const base = origins.includes(origin) ? origin : origins[0];
  return Response.redirect(`${base}/subscribe.html?${query}`, 303);
}

async function subscribe(request, env) {
  const isForm = !(request.headers.get("Content-Type") || "").includes("application/json");
  const cors = corsHeaders(request, env);
  const email = await readEmail(request);
  if (overLimit(request, "subscribe", MAX_POSTS_PER_IP_PER_HOUR)) {
    return isForm
      ? page("Slow down", "Too many sign-ups from your network right now; please try again in an hour.",
             { href: allowedOrigins(env)[0], text: "Back to wowpriced" }, 429)
      : json({ ok: false, error: "too many requests" }, 429, cors);
  }
  if (!EMAIL_RE.test(email) || email.length > 254) {
    return isForm ? backToSite(request, env, "err=email")
                  : json({ ok: false, error: "invalid email" }, 400, cors);
  }
  // Idempotent: signing up twice keeps the first token, and an address that
  // is still unconfirmed simply stays pending (the confirm email goes once).
  if (!(await getSub(env, email))) {
    await putSub(env, email, { ts: new Date(Date.now()).toISOString(), token: randomToken(), confirmed: false, confirm_sent_at: "" });
  }
  return isForm ? backToSite(request, env, "ok=1") : json({ ok: true }, 200, cors);
}

function pipelineAuthorized(request, env) {
  const auth = request.headers.get("Authorization") || "";
  return !!env.NEWSLETTER_SECRET && safeEqual(auth, `Bearer ${env.NEWSLETTER_SECRET}`);
}

async function list(request, env, url) {
  if (!pipelineAuthorized(request, env)) return json({ error: "unauthorized" }, 401);
  const pending = url.searchParams.get("pending") === "1";
  // Everything the filter needs rides in key metadata, so a page of 1000
  // subscribers costs one KV operation (a get per key would blow the budget).
  const emails = [];
  let cursor;
  do {
    const res = await env.SUBS.list({ prefix: "sub:", cursor, limit: 1000 });
    for (const k of res.keys) {
      const m = k.metadata;
      if (!m || !m.token) continue;
      const wanted = pending ? !isConfirmed(m) && !m.confirm_sent_at : isConfirmed(m);
      if (wanted) emails.push({ email: k.name.slice(4), token: m.token });
    }
    cursor = res.list_complete ? undefined : res.cursor;
  } while (cursor && emails.length < LIST_MAX);
  return json({ emails: emails.slice(0, LIST_MAX) });
}

// The pipeline reports which pending addresses it mailed a confirm link to,
// so the next /list?pending=1 leaves them out (one confirm email per address).
async function confirmSent(request, env) {
  if (!pipelineAuthorized(request, env)) return json({ error: "unauthorized" }, 401);
  const { emails } = await readJson(request);
  if (!Array.isArray(emails) || emails.length > CONFIRM_SENT_MAX) return json({ error: "bad_request" }, 400);
  const now = new Date(Date.now()).toISOString();
  for (const e of emails) {
    const email = String(e || "").trim().toLowerCase();
    const rec = EMAIL_RE.test(email) ? await getSub(env, email) : null;
    if (rec && !isConfirmed(rec)) await putSub(env, email, { ...rec, confirm_sent_at: now });
  }
  return new Response(null, { status: 204 });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const { method } = request;
    if (method === "OPTIONS") {
      const cors = corsHeaders(request, env);
      return new Response(null, { status: Object.keys(cors).length ? 204 : 403, headers: cors });
    }
    if (url.pathname.startsWith("/api/")) return handleApi(request, env, url);
    if (method === "POST" && url.pathname === "/subscribe") return subscribe(request, env);
    if (method === "GET" && url.pathname === "/confirm") return confirm(url, env);
    if ((method === "GET" || method === "POST") && url.pathname === "/unsubscribe") return unsubscribe(request, url, env);
    if (method === "GET" && url.pathname === "/list") return list(request, env, url);
    if (method === "POST" && url.pathname === "/list/confirm-sent") return confirmSent(request, env);
    return new Response("not found", { status: 404 });
  },
};
