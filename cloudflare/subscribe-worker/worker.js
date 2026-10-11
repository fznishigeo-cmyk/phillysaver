/**
 * wowpriced Worker: the free daily email list plus the paywall API, in one
 * deployment with no server of our own. KV (binding SUBS) holds one key per
 * subscriber,
 *   sub:<email> -> {"ts": "<ISO time>", "token": "<32 hex>"}  (token also in metadata)
 * plus short-lived abuse counters rl:<ip>:<hour>. No mail is sent from here:
 * the pipeline (GitHub Actions) reads the list via GET /list with the shared
 * NEWSLETTER_SECRET and sends over SMTP.
 *
 *   POST /subscribe              {email} as JSON or a form field -> 200 {ok:true};
 *                                a form post gets a 303 to <site>/subscribe.html?ok=1
 *   GET|POST /unsubscribe?e=&t=  deletes when the token matches -> 200 HTML
 *                                (POST is the RFC 8058 one-click form mail apps send)
 *   GET /list                    Authorization: Bearer <NEWSLETTER_SECRET>
 *                                -> {emails:[{email, token}]}, at most 10k
 *   /api/*                       the paywall: accounts, sessions, membership and
 *                                the members-only deals (api.js, docs/paywall.md)
 *   OPTIONS *                    CORS preflight, allowed origins only; else 404
 *
 * Bindings: SUBS (KV); secrets NEWSLETTER_SECRET, SESSION_SECRET,
 * STRIPE_SECRET_KEY (wrangler secret put); vars ALLOWED_ORIGINS (comma list,
 * default https://wowpriced.com, https://www.wowpriced.com and
 * https://fznishigeo-cmyk.github.io), GOOGLE_CLIENT_ID, STRIPE_PAYMENT_LINK,
 * STRIPE_PORTAL_LINK, TRIAL_DAYS (wrangler.toml).
 */

import { EMAIL_RE, allowedOrigins, corsHeaders, json, safeEqual, rateKey, overLimit } from "./util.js";
import { handleApi } from "./api.js";

const MAX_POSTS_PER_IP_PER_HOUR = 20;
const LIST_MAX = 10000;

function page(title, text, link, status = 200) {
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title></head>
<body style="font:16px/1.5 system-ui,sans-serif;max-width:560px;margin:60px auto;padding:0 16px">
<h1 style="font-size:24px">${title}</h1><p>${text}</p><p><a href="${link.href}">${link.text}</a></p>
</body></html>`;
  return new Response(html, { status, headers: { "Content-Type": "text/html; charset=utf-8" } });
}

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
  if (await overLimit(env, rateKey(request), MAX_POSTS_PER_IP_PER_HOUR)) {
    return isForm
      ? page("Slow down", "Too many sign-ups from your network right now; please try again in an hour.",
             { href: allowedOrigins(env)[0], text: "Back to wowpriced" }, 429)
      : json({ ok: false, error: "too many requests" }, 429, cors);
  }
  if (!EMAIL_RE.test(email) || email.length > 254) {
    return isForm ? backToSite(request, env, "err=email")
                  : json({ ok: false, error: "invalid email" }, 400, cors);
  }
  const key = `sub:${email}`;
  if (!(await env.SUBS.get(key))) { // idempotent: signing up twice keeps the first token
    const record = { ts: new Date().toISOString(), token: randomToken() };
    await env.SUBS.put(key, JSON.stringify(record), { metadata: record });
  }
  return isForm ? backToSite(request, env, "ok=1") : json({ ok: true }, 200, cors);
}

async function unsubscribe(url, env) {
  const email = (url.searchParams.get("e") || "").trim().toLowerCase();
  const token = url.searchParams.get("t") || "";
  const site = allowedOrigins(env)[0];
  const raw = email ? await env.SUBS.get(`sub:${email}`) : null;
  if (raw) {
    let stored = "";
    try { stored = JSON.parse(raw).token || ""; } catch (e) { /* malformed: never matches */ }
    if (!token || !safeEqual(stored, token)) {
      return page("That link isn't valid", "This unsubscribe link doesn't match our records. Use the link in your latest email.",
                  { href: site, text: "Back to wowpriced" }, 403);
    }
    await env.SUBS.delete(`sub:${email}`);
  }
  // Already gone counts as success: clicking the link twice must not error.
  return page("You're unsubscribed", "No more emails from wowpriced. Sorry to see you go.",
              { href: `${site}/subscribe.html?unsubscribed=1`, text: "Changed your mind? Subscribe again" });
}

async function list(request, env) {
  const auth = request.headers.get("Authorization") || "";
  if (!env.NEWSLETTER_SECRET || !safeEqual(auth, `Bearer ${env.NEWSLETTER_SECRET}`)) {
    return json({ error: "unauthorized" }, 401);
  }
  // Tokens ride in key metadata, so a page of 1000 subscribers costs one KV
  // operation (a get per key would blow the per-invocation budget).
  const emails = [];
  let cursor;
  do {
    const res = await env.SUBS.list({ prefix: "sub:", cursor, limit: 1000 });
    for (const k of res.keys) {
      if (k.metadata && k.metadata.token) emails.push({ email: k.name.slice(4), token: k.metadata.token });
    }
    cursor = res.list_complete ? undefined : res.cursor;
  } while (cursor && emails.length < LIST_MAX);
  return json({ emails: emails.slice(0, LIST_MAX) });
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
    if ((method === "GET" || method === "POST") && url.pathname === "/unsubscribe") return unsubscribe(url, env);
    if (method === "GET" && url.pathname === "/list") return list(request, env);
    return new Response("not found", { status: 404 });
  },
};
