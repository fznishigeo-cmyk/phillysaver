/**
 * Newsletter subscribers: the KV record behind sub:<email>, and the two pages
 * a subscriber reaches from the email, confirm and unsubscribe (worker.js
 * routes them and owns /subscribe, /list and /list/confirm-sent).
 *
 *   sub:<email> -> {ts, token, confirmed, confirm_sent_at}  (also in metadata,
 *                  so GET /list pages 1,000 subscribers per KV operation)
 *
 * Double opt-in: POST /subscribe writes confirmed=false; the pipeline mails a
 * confirm link to every pending address (GET /list?pending=1, then
 * POST /list/confirm-sent stamps confirm_sent_at); GET /confirm?e=&t= sets
 * confirmed=true; only confirmed addresses get the daily email. A record
 * written before double opt-in (no "confirmed" key) counts as confirmed, so
 * nobody who signed up earlier is dropped.
 *
 * Unsubscribe: GET shows a page with one POST button (a mail scanner that
 * prefetches links must not remove anyone); POST, from that button or the RFC
 * 8058 one-click form mail apps send, deletes the record when the token
 * matches. Both read e= and t= from the query, POST also from form fields.
 */

import { EMAIL_RE, allowedOrigins, safeEqual } from "./util.js";

// Enough escaping for text and double-quoted attributes (apostrophes stay).
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// The one page style every HTML answer of the Worker uses.
export function page(title, text, link, status = 200, extra = "") {
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(title)}</title></head>
<body style="font:16px/1.5 system-ui,sans-serif;max-width:560px;margin:60px auto;padding:0 16px">
<h1 style="font-size:24px">${esc(title)}</h1><p>${esc(text)}</p>${extra}<p><a href="${esc(link.href)}">${esc(link.text)}</a></p>
</body></html>`;
  return new Response(html, { status, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
}

export function isConfirmed(rec) {
  return !("confirmed" in rec) || rec.confirmed === true;
}

export async function getSub(env, email) {
  const raw = await env.SUBS.get(`sub:${email}`);
  try { return raw ? JSON.parse(raw) : null; } catch (e) { return null; }
}

export async function putSub(env, email, rec) {
  await env.SUBS.put(`sub:${email}`, JSON.stringify(rec), { metadata: rec });
}

// {email, rec} for a link whose token matches a stored record, {email} when
// there is no such record, null when the link is bad. A POST also accepts
// form fields e and t (the button on the GET page sends the query anyway).
async function fromLink(env, url, form) {
  const pick = (k) => url.searchParams.get(k) || (form && form.get(k)) || "";
  const email = pick("e").trim().toLowerCase();
  const token = pick("t").trim();
  if (!EMAIL_RE.test(email)) return null;
  const rec = await getSub(env, email);
  if (!rec) return { email };
  const stored = typeof rec.token === "string" ? rec.token : "";
  return token && safeEqual(stored, token) ? { email, rec } : null;
}

const badLink = (site) => page("That link isn't valid", "This link doesn't match our records. Use the link in your latest email from wowpriced.",
                               { href: site, text: "Back to wowpriced" }, 403);

export async function confirm(url, env) {
  const site = allowedOrigins(env)[0];
  const hit = await fromLink(env, url);
  if (!hit || !hit.rec) return badLink(site);
  if (!isConfirmed(hit.rec)) await putSub(env, hit.email, { ...hit.rec, confirmed: true });
  return page("You're confirmed", "Every new verified deal now lands in your inbox as one daily email. Welcome aboard.",
              { href: `${site}/subscribe.html?confirmed=1`, text: "Back to wowpriced" });
}

export async function unsubscribe(request, url, env) {
  const site = allowedOrigins(env)[0];
  let form = null;
  if (request.method === "POST") {
    try { form = await request.formData(); } catch (e) { /* no form body (one-click posts may send none) */ }
  }
  const hit = await fromLink(env, url, form);
  if (!hit) return badLink(site);
  if (request.method !== "POST") {
    const action = `/unsubscribe?e=${encodeURIComponent(hit.email)}&t=${encodeURIComponent(hit.rec ? hit.rec.token : "")}`;
    return page("Unsubscribe from wowpriced?", `Stop the daily deal email to ${hit.email}.`, { href: site, text: "Keep my subscription" }, 200,
                `<form method="post" action="${esc(action)}"><button type="submit" style="font:inherit;padding:8px 16px">Unsubscribe</button></form>`);
  }
  if (hit.rec) await env.SUBS.delete(`sub:${hit.email}`);
  // Already gone counts as success: clicking the button twice must not error.
  return page("You're unsubscribed", "No more emails from wowpriced. Sorry to see you go.",
              { href: `${site}/subscribe.html?unsubscribed=1`, text: "Changed your mind? Subscribe again" });
}
