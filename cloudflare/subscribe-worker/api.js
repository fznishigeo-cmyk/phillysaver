/**
 * The paywall API (docs/paywall.md "Worker API"), mounted under /api/ by
 * worker.js. JSON in and out; every error is a JSON {error} with a status,
 * never a stack trace. Passwords, tokens and secrets are never logged.
 *
 *   POST /api/signup   {email, password} -> 200 {token, me} | 400 invalid_email
 *                      | 400 weak_password | 409 exists
 *   POST /api/login    {email, password} -> 200 {token, me} | 401 bad_credentials
 *   POST /api/google   {credential}      -> 200 {token, me} | 401 bad_token
 *   GET  /api/me       Bearer <session>  -> 200 me | 401 (?fresh=1 re-asks Stripe)
 *   GET  /api/deals    Bearer <session>  -> 200 full payload | 402 me | 401
 *   POST /api/logout   Bearer <session>  -> 204 | 401
 *   POST /api/publish  Bearer NEWSLETTER_SECRET, full payload -> 204 | 400 | 401
 *
 *   deals:full   the full payload, stored verbatim by /api/publish
 *   rl:signup|login|google:<ip>:<hour>   abuse counters (10 / 20 / 20)
 */

import { EMAIL_RE, corsHeaders, json, safeEqual, readJson, rateKey, overLimit } from "./util.js";
import { getAccount, createAccount, saveAccount, checkPassword, signSession, sessionFromRequest,
         verifyGoogleToken } from "./auth.js";
import { meFor } from "./membership.js";

const LIMITS_PER_IP_PER_HOUR = { signup: 10, login: 20, google: 20 };
const MIN_PASSWORD = 8;

const cleanEmail = (v) => String(v || "").trim().toLowerCase();
const str = (v) => (typeof v === "string" ? v : "");

async function signedIn(env, acct, headers) {
  return json({ token: await signSession(env, acct.email), me: await meFor(env, acct) }, 200, headers);
}

async function signup(request, env, headers) {
  const body = await readJson(request);
  const email = cleanEmail(body.email);
  if (!EMAIL_RE.test(email) || email.length > 254) return json({ error: "invalid_email" }, 400, headers);
  if (str(body.password).length < MIN_PASSWORD) return json({ error: "weak_password" }, 400, headers);
  if (await getAccount(env, email)) return json({ error: "exists" }, 409, headers);
  return signedIn(env, await createAccount(env, email, { password: body.password }), headers);
}

async function login(request, env, headers) {
  const body = await readJson(request);
  const email = cleanEmail(body.email);
  const acct = EMAIL_RE.test(email) ? await getAccount(env, email) : null;
  if (!(await checkPassword(acct, str(body.password)))) return json({ error: "bad_credentials" }, 401, headers);
  return signedIn(env, acct, headers);
}

async function google(request, env, headers) {
  const id = await verifyGoogleToken(env, (await readJson(request)).credential);
  if (!id) return json({ error: "bad_token" }, 401, headers);
  let acct = await getAccount(env, id.email);
  if (!acct) acct = await createAccount(env, id.email, { googleSub: id.sub }); // trial starts now
  else if (acct.google_sub !== id.sub) { acct.google_sub = id.sub; await saveAccount(env, acct); }
  return signedIn(env, acct, headers);
}

// The account behind a valid session token, else null (-> 401).
async function requireAccount(request, env) {
  const session = await sessionFromRequest(request, env);
  return session ? getAccount(env, session.email) : null;
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
  if (!(await sessionFromRequest(request, env))) return json({ error: "unauthorized" }, 401, headers);
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

const POSTS = { "/api/signup": signup, "/api/login": login, "/api/google": google, "/api/logout": logout };
const GETS = { "/api/me": me, "/api/deals": deals };

export async function handleApi(request, env, url) {
  const headers = { "Cache-Control": "no-store", ...corsHeaders(request, env) };
  const path = url.pathname;
  try {
    if (request.method === "POST" && path === "/api/publish") return await publish(request, env);
    const route = request.method === "POST" ? POSTS[path] : request.method === "GET" ? GETS[path] : null;
    if (!route) return json({ error: "not_found" }, 404, headers);
    const bucket = path.slice(5); // "signup" | "login" | "google" | ...
    if (LIMITS_PER_IP_PER_HOUR[bucket] &&
        (await overLimit(env, rateKey(request, bucket), LIMITS_PER_IP_PER_HOUR[bucket]))) {
      return json({ error: "rate_limited" }, 429, headers);
    }
    return await route(request, env, headers, url);
  } catch (e) {
    console.error("api failure:", request.method, path, e && e.message); // never the body or headers
    return json({ error: "internal" }, 500, headers);
  }
}
