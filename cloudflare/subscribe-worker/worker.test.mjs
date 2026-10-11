// Tests for the wowpriced Worker, offline: an in-memory KV, a fetch stub for
// Stripe and Google's JWKS, and a locally generated RS256 key pair standing
// in for Google. Run from the repository root:
//   node --test cloudflare/subscribe-worker/
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { generateKeyPairSync, sign as rsaSign, createHash, createHmac, pbkdf2Sync } from "node:crypto";

import worker from "./worker.js";
import { signSession } from "./auth.js";
import { memGet, memClear } from "./util.js";

// ------------------------------------------------------------- fixtures

// Workers KV, enough of it: values + metadata, prefix listing with cursors.
// expirationTtl is recorded, not enforced; the code checks its own timestamps.
class FakeKV {
  constructor() { this.m = new Map(); this.writes = 0; this.reads = []; }
  async get(k) { this.reads.push(k); const v = this.m.get(k); return v ? v.value : null; }
  async put(k, value, opts = {}) {
    this.writes += 1;
    this.m.set(k, { value, metadata: opts.metadata || null, ttl: opts.expirationTtl });
  }
  async delete(k) { this.writes += 1; this.m.delete(k); }
  async list({ prefix = "", cursor, limit = 1000 } = {}) {
    const keys = [...this.m.keys()].filter((k) => k.startsWith(prefix)).sort();
    const start = cursor ? Number(cursor) : 0;
    const page = keys.slice(start, start + limit).map((name) => ({ name, metadata: this.m.get(name).metadata }));
    const done = start + limit >= keys.length;
    return { keys: page, list_complete: done, cursor: done ? undefined : String(start + limit) };
  }
}

const CLIENT_ID = "123-abc.apps.googleusercontent.com";
const PAYMENT_LINK = "https://buy.stripe.com/test_link";
const SESSION_SECRET = "session-secret-for-tests";

function newEnv(over = {}) {
  return { SUBS: new FakeKV(), NEWSLETTER_SECRET: "s3cret", SESSION_SECRET,
           STRIPE_SECRET_KEY: "sk_test_123", GOOGLE_CLIENT_ID: CLIENT_ID, STRIPE_PAYMENT_LINK: PAYMENT_LINK,
           STRIPE_PORTAL_LINK: "https://billing.stripe.com/p/login/test", TRIAL_DAYS: "7", ...over };
}

// Google stand-in: our own RS256 key pair, published through the JWKS stub.
const { publicKey, privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const strangerKey = generateKeyPairSync("rsa", { modulusLength: 2048 }).privateKey;
const JWK = { ...publicKey.export({ format: "jwk" }), kid: "kid-1", alg: "RS256", use: "sig" };

function idToken(claims = {}, { key = privateKey, kid = "kid-1" } = {}) {
  const now = Math.floor(Date.now() / 1000);
  const payload = { iss: "https://accounts.google.com", aud: CLIENT_ID, sub: "g-sub-1", email: "G.User@Example.com",
                    email_verified: true, iat: now, exp: now + 3600, ...claims };
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const signed = `${b64({ alg: "RS256", kid, typ: "JWT" })}.${b64(payload)}`;
  return `${signed}.${rsaSign("sha256", Buffer.from(signed), key).toString("base64url")}`;
}

// Stripe stand-in. customers: the email as Stripe holds it (any case) ->
// customer ids; subs: customer id -> subscriptions, each a status or
// {status, trial_end, metadata, unindexed} (ids sub_<customer>_<index>;
// unindexed: not yet visible to the Search API, which lags); sessions:
// Checkout Session id -> object (its subscription id is expanded on request);
// links: the Payment Links, linkPrices: link id -> price id; portalConfigs:
// the billing-portal configurations. POSTs are captured in posts ({path,
// body, headers}); an Idempotency-Key replays the first answer and a reuse
// with other params is a 400, as Stripe does. Lists page like Stripe (limit,
// has_more, starting_after), with pageSize to force paging in a test. fail
// breaks every call, failPost the POSTs only, failGet(url) -> a status breaks
// the GETs it picks.
const STRIPE_RESET = () => ({ customers: {}, subs: {}, sessions: {}, links: [{ id: "plink_1", url: PAYMENT_LINK }],
                              linkPrices: { plink_1: "price_999" }, portalConfigs: [], fail: false, failPost: false,
                              failGet: null, calls: 0, pageSize: 100, urls: [], posts: [], idem: {}, created: 0, paid: 0 });
const stripe = STRIPE_RESET();
const jwks = { calls: 0 };
const jsonResponse = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const subObject = (customer, s, i) => ({ id: `sub_${customer}_${i}`, trial_end: null, metadata: {},
                                          ...(typeof s === "string" ? { status: s } : s) });

function everySubscription() {
  const out = [];
  for (const [email, ids] of Object.entries(stripe.customers)) {
    for (const id of ids) {
      (stripe.subs[id] || []).forEach((s, i) => out.push({ ...subObject(id, s, i), customer: { id, object: "customer", email } }));
    }
  }
  return out;
}

function subscriptionById(id) {
  const m = /^sub_(.+)_(\d+)$/.exec(id);
  const s = m && (stripe.subs[m[1]] || [])[Number(m[2])];
  return s ? { ...subObject(m[1], s, Number(m[2])), customer: m[1] } : null;
}

function listPage(all, url) {
  const after = url.searchParams.get("starting_after");
  const start = after ? all.findIndex((x) => x.id === after) + 1 : 0;
  const size = Math.min(Number(url.searchParams.get("limit")), stripe.pageSize);
  return { data: all.slice(start, start + size), has_more: start + size < all.length };
}

function stripePost(url, init) {
  assert.equal(init.headers["Content-Type"], "application/x-www-form-urlencoded");
  stripe.posts.push({ path: url.pathname, body: init.body, headers: init.headers });
  if (stripe.failPost) return jsonResponse({ error: { message: "boom: secret detail" } }, 500);
  const key = init.headers["Idempotency-Key"];
  if (key && stripe.idem[key]) {
    return stripe.idem[key].body === init.body ? jsonResponse(stripe.idem[key].answer)
      : jsonResponse({ error: { type: "idempotency_error", message: "Keys for idempotent requests..." } }, 400);
  }
  let answer;
  if (url.pathname === "/v1/checkout/sessions") {
    stripe.created += 1;
    const id = `cs_test_new${stripe.created}`;
    answer = { id, object: "checkout.session", url: `https://checkout.stripe.com/c/pay/${id}` };
  } else if (url.pathname === "/v1/billing_portal/sessions") {
    const customer = new URLSearchParams(init.body).get("customer");
    answer = { id: "bps_1", object: "billing_portal.session", url: `https://billing.stripe.com/p/session/test_${customer}` };
  } else {
    throw new Error(`unexpected Stripe POST ${url.pathname}`);
  }
  if (key) stripe.idem[key] = { body: init.body, answer };
  return jsonResponse(answer);
}

globalThis.fetch = async (input, init = {}) => {
  const url = new URL(typeof input === "string" ? input : input.url);
  if (url.href === "https://www.googleapis.com/oauth2/v3/certs") {
    jwks.calls += 1;
    return jsonResponse({ keys: [JWK] });
  }
  if (url.origin === "https://api.stripe.com") {
    stripe.calls += 1;
    stripe.urls.push(url.pathname + url.search);
    assert.ok(["sk_test_123", "sk_live_123"].some((k) => init.headers.Authorization === `Basic ${btoa(`${k}:`)}`));
    if (stripe.fail) return jsonResponse({ error: { message: "boom" } }, 500);
    if (init.method === "POST") return stripePost(url, init);
    const broken = stripe.failGet && stripe.failGet(url);
    if (broken) return jsonResponse({ error: { message: "boom" } }, broken);
    if (url.pathname === "/v1/subscriptions/search") {
      const m = /^metadata\['wowpriced_ref'\]:'([0-9a-f]{32})'$/.exec(url.searchParams.get("query"));
      assert.ok(m, url.searchParams.get("query"));
      const data = [];
      for (const [id, list] of Object.entries(stripe.subs)) {
        list.forEach((s, i) => {
          const o = subObject(id, s, i);
          if (!o.unindexed && o.metadata.wowpriced_ref === m[1]) data.push({ ...o, customer: id });
        });
      }
      return jsonResponse({ object: "search_result", data, has_more: false });
    }
    if (url.pathname === "/v1/customers") { // exact-case, as Stripe's filter is
      const email = url.searchParams.get("email");
      return jsonResponse({ data: (stripe.customers[email] || []).map((id) => ({ id, object: "customer", email })), has_more: false });
    }
    if (url.pathname === "/v1/billing_portal/configurations") {
      assert.equal(url.searchParams.get("active"), "true");
      return jsonResponse(listPage(stripe.portalConfigs, url));
    }
    if (url.pathname === "/v1/subscriptions" && url.searchParams.get("customer")) {
      assert.equal(url.searchParams.get("status"), "all");
      const id = url.searchParams.get("customer");
      return jsonResponse({ data: (stripe.subs[id] || []).map((s, i) => ({ ...subObject(id, s, i), customer: id })) });
    }
    if (url.pathname === "/v1/subscriptions") {
      assert.equal(url.searchParams.get("expand[]"), "data.customer");
      const status = url.searchParams.get("status");
      assert.ok(status === "active" || status === "trialing", status);
      return jsonResponse(listPage(everySubscription().filter((s) => s.status === status), url));
    }
    let m = url.pathname.match(/^\/v1\/checkout\/sessions\/([^/]+)$/);
    if (m) {
      assert.equal(url.searchParams.get("expand[]"), "subscription");
      const session = stripe.sessions[m[1]];
      if (!session) return jsonResponse({ error: { type: "invalid_request_error", message: "No such checkout.session" } }, 404);
      const sub = typeof session.subscription === "string" ? subscriptionById(session.subscription) : null;
      return jsonResponse({ id: m[1], ...session, ...(sub ? { subscription: sub } : {}) });
    }
    if (url.pathname === "/v1/payment_links") {
      assert.equal(url.searchParams.get("active"), "true");
      return jsonResponse(listPage(stripe.links, url));
    }
    m = url.pathname.match(/^\/v1\/payment_links\/([^/]+)\/line_items$/);
    if (m) {
      const price = stripe.linkPrices[m[1]];
      return jsonResponse({ data: price ? [{ id: "li_1", object: "item", price: { id: price, object: "price" }, quantity: 1 }] : [] });
    }
  }
  throw new Error(`unexpected fetch ${url.href}`);
};

// Time travel: the Worker reads the clock through Date.now only.
const realNow = Date.now;
let offset = 0;
Date.now = () => realNow() + offset;
const travel = (ms) => { offset += ms; };
const DAY = 86400 * 1000;
const MIN = 60 * 1000;

// A WebCrypto failure on the nth HMAC signature of a request (0 = never).
let failSignAt = 0;
const realSign = SubtleCrypto.prototype.sign;
SubtleCrypto.prototype.sign = function (...args) {
  if (failSignAt && --failSignAt === 0) throw new Error("hsm on fire");
  return realSign.apply(this, args);
};

afterEach(() => {
  offset = 0;
  failSignAt = 0;
  Object.assign(stripe, STRIPE_RESET());
  memClear(); // rate counters, the membership cache and the price are per isolate
});

const BASE = "https://wowpriced-subscribe.example.workers.dev";
const ORIGIN = "https://wowpriced.com";

async function call(env, method, path, { body, token, headers = {}, origin = ORIGIN, ip = "1.2.3.4" } = {}) {
  const h = { Origin: origin, "CF-Connecting-IP": ip, ...headers };
  if (body !== undefined && !h["Content-Type"]) h["Content-Type"] = "application/json";
  if (token) h.Authorization = `Bearer ${token}`;
  const payload = body === undefined || typeof body === "string" || body instanceof URLSearchParams
    ? body : JSON.stringify(body);
  const res = await worker.fetch(new Request(BASE + path, { method, headers: h, body: payload }), env);
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch (e) { /* not JSON */ }
  return { status: res.status, json, text, headers: res.headers };
}

const PASSWORD = "correct horse battery";
const signup = (env, email, password = PASSWORD, opts = {}) =>
  call(env, "POST", "/api/signup", { body: { email, password }, ...opts });
const login = (env, email, password = PASSWORD, opts = {}) =>
  call(env, "POST", "/api/login", { body: { email, password }, ...opts });
const google = (env, credential, opts = {}) => call(env, "POST", "/api/google", { body: { credential }, ...opts });
const me = (env, token, query = "") => call(env, "GET", `/api/me${query}`, { token });
const claim = (env, token, session_id, opts = {}) => call(env, "POST", "/api/claim", { body: { session_id }, token, ...opts });
const checkout = (env, token, opts = {}) => call(env, "POST", "/api/checkout", { body: {}, token, ...opts });
const portal = (env, token, opts = {}) => call(env, "POST", "/api/portal", { token, ...opts });
const account = async (env, email) => JSON.parse(await env.SUBS.get(`acct:${email}`));

// A Google-created account: email verified, so Stripe is asked by email.
const googleAccount = async (env, email = "G.User@Example.com") => (await google(env, idToken({ email }))).json.token;

function subscribed(email, statuses = ["active"], id = "cus_1") {
  stripe.customers[email] = [id];
  stripe.subs[id] = statuses;
}

// The account's client_reference_id, computed independently of the Worker.
const ref = (email) => createHash("sha256").update(`wowpriced-acct:${email}`).digest("hex").slice(0, 32);
// The last Stripe POST, and its form decoded the way Stripe decodes it.
const lastPost = () => stripe.posts[stripe.posts.length - 1];
const posted = () => [...new URLSearchParams(lastPost().body)];
const postedMap = () => Object.fromEntries(posted());
const emailLookups = () => stripe.urls.filter((u) => u.includes("expand[]=data.customer"));
// Keeps a test away from the edge of a 10-minute idempotency slot.
const midSlot = () => { if (Date.now() % (10 * MIN) > 9 * MIN) travel(2 * MIN); };
const isoOf = (unixSeconds) => new Date(unixSeconds * 1000).toISOString();
const paths = () => stripe.urls.map((u) => u.split("?")[0]);
const checkoutPosts = () => stripe.posts.filter((p) => p.path === "/v1/checkout/sessions");

// Completes the last Checkout Session the Worker made, as Stripe would: its
// customer (the bound one, else a new one under customer_email), a
// subscription with the session's metadata (trialing for the trial days it
// carried, else active) and the session itself, complete. indexed: false
// keeps the subscription out of Search for now (Search lags about a minute).
function payLastCheckout({ indexed = true } = {}) {
  assert.equal(lastPost().path, "/v1/checkout/sessions");
  const form = postedMap();
  const n = ++stripe.paid;
  const customer = form.customer || `cus_paid${n}`;
  if (!form.customer) (stripe.customers[form.customer_email] ||= []).push(customer);
  const days = Number(form["subscription_data[trial_period_days]"] || 0);
  const sub = { status: days ? "trialing" : "active", trial_end: days ? Math.floor(Date.now() / 1000) + days * 86400 : null,
                metadata: { wowpriced_ref: form["subscription_data[metadata][wowpriced_ref]"] }, unindexed: !indexed };
  const list = (stripe.subs[customer] ||= []);
  list.push(sub);
  const id = `cs_test_paid${n}abcdef`;
  stripe.sessions[id] = { status: "complete", mode: "subscription", customer, subscription: `sub_${customer}_${list.length - 1}`,
                          client_reference_id: form.client_reference_id };
  return { id, customer, sub };
}

const COMPLETE = { status: "complete", mode: "subscription", customer: "cus_9", subscription: "sub_cus_9_0",
                   client_reference_id: null, customer_details: { email: "Buyer@Example.com" } };
const ME_KEYS = ["email", "member", "reason", "trial_ends_at", "trial_days", "trial_available", "email_verified",
                 "claimed", "subscribe_url", "portal_url"];

// ---------------------------------------------------------------- tests

describe("signup and login", () => {
  test("signup creates a free account with no card-less trial and signs it in", async () => {
    const env = newEnv();
    const r = await signup(env, " New@Example.COM ");
    assert.equal(r.status, 200);
    assert.equal(typeof r.json.token, "string");
    assert.equal(r.headers.get("Access-Control-Allow-Origin"), ORIGIN);
    assert.equal(r.headers.get("Cache-Control"), "no-store");
    assert.deepEqual(Object.keys(r.json.me), ME_KEYS);
    assert.deepEqual(r.json.me, {
      email: "new@example.com", member: false, reason: "none", trial_ends_at: null, trial_days: 7, trial_available: true,
      email_verified: false, claimed: false,
      subscribe_url: `${PAYMENT_LINK}?prefilled_email=new%40example.com`,
      portal_url: "https://billing.stripe.com/p/login/test",
    });

    const raw = await env.SUBS.get("acct:new@example.com");
    const acct = JSON.parse(raw);
    assert.deepEqual(Object.keys(acct).sort(), ["claimed_at", "created_at", "email", "email_verified", "google_sub", "hash",
                                                "iterations", "salt", "scheme", "session_version", "stripe_customer", "trial_used"]);
    assert.deepEqual([acct.google_sub, acct.email_verified, acct.stripe_customer, acct.claimed_at, acct.trial_used, acct.session_version],
                     [null, false, null, null, false, 0]);
    assert.deepEqual([acct.scheme, acct.iterations], [2, 20000]);
    assert.match(acct.salt, /^[A-Za-z0-9_-]{22}$/, "16 random bytes, base64url");
    assert.match(acct.hash, /^[A-Za-z0-9_-]{43}$/, "32-byte PBKDF2 output, base64url");
    assert.ok(!raw.includes(PASSWORD), "the password itself is never stored");
    assert.equal([...env.SUBS.m.keys()].filter((k) => k.startsWith("sub:")).length, 0, "no newsletter record");
    assert.equal(env.SUBS.writes, 1, "one KV write: the account");
    assert.equal((await call(env, "GET", "/api/deals", { token: r.json.token })).status, 402, "signing up unlocks nothing");
    assert.deepEqual(paths(), ["/v1/subscriptions/search"],
                     "an unbound, unverified account: only its own checkouts are looked up, once (then cached)");
  });

  test("an account still inside the old card-less trial is not a member any more", async () => {
    const env = newEnv();
    const now = Date.now();
    await env.SUBS.put("acct:early@example.com", JSON.stringify({
      email: "early@example.com", scheme: null, iterations: null, salt: null, hash: null, google_sub: null,
      email_verified: false, stripe_customer: null, claimed_at: null, session_version: 0,
      created_at: new Date(now - DAY).toISOString(), trial_ends_at: new Date(now + 6 * DAY).toISOString() }));
    const token = await signSession(env, "early@example.com");
    const r = await me(env, token);
    assert.deepEqual([r.status, r.json.member, r.json.reason, r.json.trial_ends_at, r.json.trial_available], [200, false, "none", null, true]);
    assert.equal((await call(env, "GET", "/api/deals", { token })).status, 402);
  });

  test("signup refuses duplicates, weak passwords and bad addresses", async () => {
    const env = newEnv();
    assert.equal((await signup(env, "dup@example.com")).status, 200);
    let r = await signup(env, "DUP@example.com");
    assert.deepEqual([r.status, r.json], [409, { error: "exists" }]);
    r = await signup(env, "weak@example.com", "1234567");
    assert.deepEqual([r.status, r.json], [400, { error: "weak_password" }]);
    r = await signup(env, "weak@example.com", 12345678);
    assert.deepEqual([r.status, r.json], [400, { error: "weak_password" }]);
    for (const bad of ["nope", "nope@", "@example.com", "a b@example.com", ""]) {
      r = await signup(env, bad);
      assert.deepEqual([r.status, r.json], [400, { error: "invalid_email" }], bad);
    }
    r = await call(env, "POST", "/api/signup", { body: "{not json" });
    assert.deepEqual([r.status, r.json], [400, { error: "invalid_email" }]);
    assert.equal(await env.SUBS.get("acct:weak@example.com"), null);
  });

  test("login returns a session for the right password only", async () => {
    const env = newEnv();
    await signup(env, "who@example.com");
    let r = await login(env, "WHO@example.com");
    assert.equal(r.status, 200);
    assert.equal(typeof r.json.token, "string");
    assert.equal(r.json.me.email, "who@example.com");
    assert.equal(r.json.me.reason, "none");
    for (const [email, password] of [["who@example.com", "wrong password"], ["nobody@example.com", PASSWORD],
                                     ["not an email", PASSWORD], ["who@example.com", ""]]) {
      r = await login(env, email, password);
      assert.deepEqual([r.status, r.json], [401, { error: "bad_credentials" }], email);
    }
    r = await call(env, "POST", "/api/login", { body: "[]" });
    assert.deepEqual([r.status, r.json], [401, { error: "bad_credentials" }]);
    assert.equal(env.SUBS.writes, 1, "logins write nothing");
  });

  test("a Google-only account has no password to log in with", async () => {
    const env = newEnv();
    assert.equal((await google(env, idToken())).status, 200);
    const r = await login(env, "g.user@example.com", PASSWORD);
    assert.deepEqual([r.status, r.json], [401, { error: "bad_credentials" }]);
  });

  test("without SESSION_SECRET sign-in and checkout are 503 and nothing is written", async () => {
    const env = newEnv({ SESSION_SECRET: "" });
    const want = [503, { error: "signin_unavailable" }];
    let r = await signup(env, "x@example.com");
    assert.deepEqual([r.status, r.json], want);
    r = await login(env, "x@example.com");
    assert.deepEqual([r.status, r.json], want);
    r = await google(env, idToken());
    assert.deepEqual([r.status, r.json], want);
    r = await claim(env, "whatever", "cs_test_abc");
    assert.deepEqual([r.status, r.json], want);
    r = await checkout(env, "whatever");
    assert.deepEqual([r.status, r.json], want);
    assert.equal(env.SUBS.m.size, 0);
    assert.equal(env.SUBS.writes, 0);
    assert.equal(stripe.calls, 0);
    // The read-only routes keep their usual answers.
    assert.equal((await me(env, "x.y")).status, 401);
    assert.equal((await portal(env, "x.y")).status, 401);
    assert.equal((await call(env, "POST", "/api/publish", { body: '{"deals": []}', token: "s3cret" })).status, 204);
  });

  test("a session-signing failure leaves no half-created account", async () => {
    const env = newEnv();
    failSignAt = 2; // signup signs twice: the password pepper, then the session
    let r = await signup(env, "half@example.com");
    assert.deepEqual([r.status, r.json], [500, { error: "internal" }]);
    assert.equal(await env.SUBS.get("acct:half@example.com"), null);
    assert.equal(env.SUBS.writes, 0);
    failSignAt = 1; // Google sign-in signs once
    r = await google(env, idToken());
    assert.equal(r.status, 500);
    assert.equal(env.SUBS.m.size, 0);
    assert.equal((await signup(env, "half@example.com")).status, 200, "nothing stale blocks the retry");
  });
});

describe("password hashing", () => {
  const b64 = (buf) => Buffer.from(buf).toString("base64url");
  const peppered = (password, secret = SESSION_SECRET) => createHmac("sha256", secret).update(password).digest();
  const legacyHash = (password, salt) => b64(pbkdf2Sync(password, salt, 100000, 32, "sha256"));

  test("scheme 2: HMAC pepper with SESSION_SECRET, then PBKDF2 with the configured rounds", async () => {
    const env = newEnv();
    await signup(env, "p@example.com");
    const acct = await account(env, "p@example.com");
    const salt = Buffer.from(acct.salt, "base64url");
    assert.equal(acct.hash, b64(pbkdf2Sync(peppered(PASSWORD), salt, 20000, 32, "sha256")));
    assert.notEqual(acct.hash, b64(pbkdf2Sync(PASSWORD, salt, 20000, 32, "sha256")), "a leaked KV hash alone is not enough");
    // The round count is a var; a bogus one falls back to 20,000.
    const more = newEnv({ PBKDF2_ITERATIONS: "25000" });
    await signup(more, "p@example.com");
    const a2 = await account(more, "p@example.com");
    assert.equal(a2.iterations, 25000);
    assert.equal(a2.hash, b64(pbkdf2Sync(peppered(PASSWORD), Buffer.from(a2.salt, "base64url"), 25000, 32, "sha256")));
    assert.equal((await login(more, "p@example.com")).status, 200);
    await signup(newEnv({ PBKDF2_ITERATIONS: "lots" }), "p@example.com").then(async (r) => assert.equal(r.json.me.email, "p@example.com"));
    assert.equal((await account(await (async () => { const e = newEnv({ PBKDF2_ITERATIONS: "12" }); await signup(e, "p@example.com"); return e; })(), "p@example.com")).iterations, 20000);
  });

  test("a scheme-1 account (pre-pepper, 100,000 rounds) still logs in and is re-hashed", async () => {
    const env = newEnv();
    const salt = Buffer.from("0123456789abcdef");
    const legacy = { email: "old@example.com", salt: b64(salt), hash: legacyHash(PASSWORD, salt),
                     created_at: "2026-01-01T00:00:00.000Z", trial_ends_at: "2026-01-08T00:00:00.000Z", google_sub: null };
    await env.SUBS.put("acct:old@example.com", JSON.stringify(legacy));
    env.SUBS.writes = 0;
    assert.equal((await login(env, "old@example.com", "wrong")).status, 401);
    assert.equal(env.SUBS.writes, 0);
    let r = await login(env, "old@example.com");
    assert.equal(r.status, 200);
    assert.deepEqual([r.json.me.reason, r.json.me.email_verified, r.json.me.trial_available], ["none", false, true]);
    let acct = await account(env, "old@example.com");
    assert.deepEqual([acct.scheme, acct.iterations], [2, 20000]);
    assert.notEqual(acct.salt, legacy.salt, "fresh salt");
    assert.equal(acct.hash, b64(pbkdf2Sync(peppered(PASSWORD), Buffer.from(acct.salt, "base64url"), 20000, 32, "sha256")));
    assert.equal(acct.trial_ends_at, legacy.trial_ends_at, "an old field is carried along untouched");
    assert.equal(env.SUBS.writes, 1);
    assert.equal((await login(env, "old@example.com")).status, 200);
    assert.equal(env.SUBS.writes, 1, "already current: no re-hash");
    // A new round count re-hashes at the next login too.
    const env2 = newEnv({ SUBS: env.SUBS, PBKDF2_ITERATIONS: "30000" });
    assert.equal((await login(env2, "old@example.com")).status, 200);
    acct = await account(env2, "old@example.com");
    assert.equal(acct.iterations, 30000);
    assert.equal((await login(env2, "old@example.com", "wrong")).status, 401);
  });
});

describe("session tokens", () => {
  test("a token round-trips through /api/me and /api/logout", async () => {
    const env = newEnv();
    const { json: { token } } = await signup(env, "tok@example.com");
    assert.match(token, /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/);
    const claims = JSON.parse(Buffer.from(token.split(".")[0], "base64url").toString());
    assert.deepEqual([claims.email, claims.sv], ["tok@example.com", 0]);
    const ttl = claims.exp - Math.floor(Date.now() / 1000);
    assert.ok(ttl > 30 * 86400 - 5 && ttl <= 30 * 86400, "30 days");
    let r = await me(env, token);
    assert.equal(r.status, 200);
    assert.equal(r.json.email, "tok@example.com");
    r = await call(env, "POST", "/api/logout", { token });
    assert.equal(r.status, 204);
    assert.equal(r.text, "");
    r = await call(env, "POST", "/api/logout");
    assert.deepEqual([r.status, r.json], [401, { error: "unauthorized" }]);
  });

  test("tampered, forged, expired and missing tokens are 401", async () => {
    const env = newEnv();
    const { json: { token } } = await signup(env, "tok@example.com");
    const [body, sig] = token.split(".");
    const flip = (s, i) => s.slice(0, i) + (s[i] === "A" ? "B" : "A") + s.slice(i + 1);
    const forgedBody = Buffer.from(JSON.stringify({ email: "other@example.com", exp: 4102444800 })).toString("base64url");
    const bad = [
      `${body}.${flip(sig, 3)}`, `${flip(body, 5)}.${sig}`, `${forgedBody}.${sig}`, body, `${token}.x`, "", "garbage",
      await signSession(env, "tok@example.com", Math.floor(Date.now() / 1000) - 1),
      await signSession({ SESSION_SECRET: "another secret" }, "tok@example.com"),
    ];
    for (const t of bad) {
      const r = await call(env, "GET", "/api/me", { headers: { Authorization: `Bearer ${t}` } });
      assert.deepEqual([r.status, r.json], [401, { error: "unauthorized" }], t);
    }
    let r = await call(env, "GET", "/api/me", { headers: { Authorization: `Basic ${token}` } });
    assert.equal(r.status, 401);
    r = await me(env);
    assert.equal(r.status, 401);
    // A valid token outlives the clock by less than 30 days only.
    travel(29 * DAY);
    assert.equal((await me(env, token)).status, 200);
    travel(2 * DAY);
    assert.equal((await me(env, token)).status, 401);
    // Without SESSION_SECRET nothing verifies.
    assert.equal((await me(newEnv({ SESSION_SECRET: "" }), token)).status, 401);
  });
});

describe("membership and ownership", () => {
  test("a password sign-up with a subscriber's address inherits nothing; a verified sign-in does", async () => {
    const env = newEnv();
    subscribed("Paying@Example.com");
    const { json: { token } } = await signup(env, "paying@example.com");
    assert.deepEqual(emailLookups(), [], "an unverified address is never looked up");
    let r = await me(env, token);
    assert.deepEqual([r.status, r.json.member, r.json.reason, r.json.email_verified], [200, false, "none", false]);
    assert.equal((await call(env, "GET", "/api/deals", { token })).status, 402);
    assert.deepEqual(emailLookups(), []);
    // The real owner signs in with Google: the squatted account is theirs now.
    r = await google(env, idToken({ email: "paying@example.com", sub: "owner" }));
    assert.deepEqual([r.status, r.json.me.member, r.json.me.reason, r.json.me.email_verified], [200, true, "subscribed", true]);
    assert.ok(stripe.calls > 0);
    const owner = r.json.token;
    const acct = await account(env, "paying@example.com");
    assert.deepEqual([acct.google_sub, acct.email_verified, acct.scheme, acct.iterations, acct.salt, acct.hash, acct.session_version],
                     ["owner", true, null, null, null, null, 1]);
    r = await login(env, "paying@example.com");
    assert.deepEqual([r.status, r.json], [401, { error: "bad_credentials" }], "the squatter's password is gone");
    // ...and so is the squatter's session, however long its token has left; the owner's works.
    for (const path of ["/api/me", "/api/deals"]) {
      r = await call(env, "GET", path, { token });
      assert.deepEqual([r.status, r.json], [401, { error: "unauthorized" }], `squatter ${path}`);
    }
    assert.deepEqual([(await call(env, "POST", "/api/logout", { token })).status, (await claim(env, token, "cs_test_x")).status,
                      (await checkout(env, token)).status, (await portal(env, token)).status], [401, 401, 401, 401]);
    assert.equal((await me(env, owner)).json.reason, "subscribed");
    travel(20 * DAY);
    assert.equal((await me(env, token)).status, 401, "still dead later");
    assert.equal((await me(env, owner)).status, 200);
  });

  test("a squatter who pays with the victim's address gets only what they paid for, then is evicted", async () => {
    const env = newEnv();
    subscribed("victim@example.com", ["active"], "cus_victim");
    const { json: { token } } = await signup(env, "victim@example.com");
    // The squatter buys a subscription on the Payment Link typing victim@ as the payer email.
    stripe.sessions.cs_test_squat1 = { ...COMPLETE, customer: "cus_squatter", subscription: "sub_cus_squatter_0",
                                       customer_details: { email: "victim@example.com" } };
    stripe.subs.cus_squatter = ["active"];
    let r = await claim(env, token, "cs_test_squat1");
    assert.deepEqual([r.status, r.json.me.reason, r.json.me.claimed, r.json.me.email_verified], [200, "subscribed", true, false],
                     "a typed payer email verifies nothing");
    assert.deepEqual(emailLookups(), [], "the victim's subscription is never looked up by email");
    // They cancel: membership ends with their own customer, nothing is inherited from the victim.
    stripe.subs.cus_squatter = ["canceled"];
    travel(6 * MIN);
    assert.deepEqual([(await me(env, token)).json.reason, (await call(env, "GET", "/api/deals", { token })).status], ["lapsed", 402]);
    assert.deepEqual(emailLookups(), []);
    // The owner's Google sign-in still takes the address over: password gone, session dead.
    r = await google(env, idToken({ email: "victim@example.com", sub: "owner" }));
    assert.deepEqual([r.status, r.json.me.reason, r.json.me.email_verified], [200, "subscribed", true]);
    assert.equal((await account(env, "victim@example.com")).hash, null);
    assert.equal((await login(env, "victim@example.com")).status, 401);
    assert.equal((await me(env, token)).status, 401);
  });

  test("Google takeover unbinds the claimed customer; the owner's own subscription still counts by email; a verified account is only linked", async () => {
    const env = newEnv();
    await signup(env, "p@example.com");
    let r = await google(env, idToken({ email: "p@example.com", sub: "g-p" }));
    assert.deepEqual([r.json.me.reason, r.json.me.trial_available], ["none", true]);
    assert.equal((await account(env, "p@example.com")).hash, null);
    // The owner's own password account with a claimed Stripe trial, paid under the owner's address.
    const { json: { token } } = await signup(env, "buyer@example.com");
    const end = Math.floor(Date.now() / 1000) + 7 * 86400;
    stripe.sessions.cs_test_ok123456 = COMPLETE;
    stripe.customers["buyer@example.com"] = ["cus_9"];
    stripe.subs.cus_9 = [{ status: "trialing", trial_end: end }];
    assert.equal((await claim(env, token, "cs_test_ok123456")).json.me.email_verified, false);
    assert.equal(await env.SUBS.get("claim:cus_9"), "buyer@example.com");
    r = await google(env, idToken({ email: "buyer@example.com", sub: "g-b" }));
    assert.deepEqual([r.status, r.json.me.reason, r.json.me.trial_ends_at, r.json.me.claimed, r.json.me.trial_available],
                     [200, "trial", isoOf(end), false, false], "unbound, still a member through the verified email");
    let acct = await account(env, "buyer@example.com");
    assert.deepEqual([acct.google_sub, acct.hash, acct.stripe_customer, acct.claimed_at, acct.trial_used, acct.session_version],
                     ["g-b", null, null, null, false, 1]);
    assert.equal(await env.SUBS.get("claim:cus_9"), null, "the customer is free to claim again");
    assert.equal((await me(env, token)).status, 401, "the pre-takeover session is gone");
    assert.equal((await me(env, r.json.token)).json.reason, "trial");
    // The owner holds the receipt: claiming it again binds it again.
    r = await claim(env, r.json.token, "cs_test_ok123456");
    assert.deepEqual([r.status, r.json.me.claimed, r.json.me.reason], [200, true, "trial"]);
    // An already verified account (one Google made) with a later Google sign-in: linked, nothing bumped.
    r = await google(env, idToken({ email: "buyer@example.com", sub: "g-b2" }));
    const owner = r.json.token;
    acct = await account(env, "buyer@example.com");
    assert.deepEqual([acct.google_sub, acct.session_version], ["g-b2", 1]);
    assert.equal((await me(env, owner)).status, 200);
  });

  test("none, then subscribed through Stripe by (verified) email; trialing is the trial with Stripe's end", async () => {
    const env = newEnv();
    const token = await googleAccount(env, "m@example.com");
    let r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason, r.json.trial_ends_at, r.json.trial_available], [false, "none", null, true]);
    assert.equal(r.json.subscribe_url, `${PAYMENT_LINK}?prefilled_email=m%40example.com`);
    subscribed("m@example.com", ["canceled", "active"]);
    travel(6 * MIN); // past the cached "no"
    r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason, r.json.trial_ends_at], [true, "subscribed", null]);
    // Trialing counts, as the trial; canceled alone does not; a second customer is checked too.
    const end = Math.floor(Date.now() / 1000) + 5 * 86400;
    stripe.customers["m@example.com"] = ["cus_old", "cus_1"];
    stripe.subs = { cus_old: ["canceled"], cus_1: [{ status: "trialing", trial_end: end }] };
    travel(6 * MIN);
    r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason, r.json.trial_ends_at], [true, "trial", isoOf(end)]);
    // Active beats trialing.
    stripe.subs = { cus_old: [{ status: "trialing", trial_end: end }], cus_1: ["active"] };
    travel(6 * MIN);
    r = await me(env, token);
    assert.deepEqual([r.json.reason, r.json.trial_ends_at], ["subscribed", null]);
    stripe.subs = { cus_old: ["canceled"], cus_1: ["past_due"] };
    travel(6 * MIN);
    r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason], [false, "none"], "never bound: none, not lapsed");
    assert.ok(stripe.urls.every((u) => !u.startsWith("/v1/customers")), "never the exact-case customers?email= filter");
  });

  test("the email lookup is case-insensitive and pages through Stripe's list", async () => {
    const env = newEnv();
    const token = await googleAccount(env, "Late@Example.com");
    travel(6 * MIN); // past the "no" cached at sign-in
    stripe.pageSize = 2;
    for (let i = 0; i < 5; i++) subscribed(`other${i}@example.com`, ["active"], `cus_o${i}`);
    subscribed("LATE@example.COM", ["trialing"], "cus_late");
    let r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason], [true, "trial"]);
    const urls = stripe.urls.join("\n");
    assert.match(urls, /status=active&limit=100&expand\[\]=data\.customer/);
    assert.match(urls, /starting_after=sub_cus_o1_0/);
    assert.match(urls, /status=trialing&limit=100/);
    assert.ok(stripe.calls >= 4, `active: 3 pages, trialing: 1 (${stripe.calls})`);
    stripe.calls = 0;
    travel(6 * MIN);
    delete stripe.customers["LATE@example.COM"];
    assert.equal((await me(env, token)).json.reason, "none");
  });

  test("Stripe's answer is cached 5 minutes in memory; ?fresh=1 re-asks once a minute", async () => {
    const env = newEnv();
    const token = await googleAccount(env, "c@example.com");
    travel(6 * MIN);
    subscribed("c@example.com");
    assert.equal((await me(env, token)).json.reason, "subscribed");
    assert.deepEqual(memGet("member:c@example.com"), { sub: { status: "active", trial_end: null }, had: true },
                     "the summary, not a boolean");
    assert.ok(![...env.SUBS.m.keys()].some((k) => k.startsWith("member:")), "never a KV write");
    const calls = stripe.calls;
    stripe.subs.cus_1 = ["canceled"];
    assert.equal((await me(env, token)).json.reason, "subscribed", "served from the cache");
    assert.equal(stripe.calls, calls);
    assert.equal((await me(env, token, "?fresh=1")).json.reason, "none", "fresh re-asks Stripe");
    assert.ok(stripe.calls > calls);
    assert.deepEqual(memGet("member:c@example.com"), { sub: null, had: false }, "no live subscription is cached too");
    const calls2 = stripe.calls;
    stripe.subs.cus_1 = ["active"];
    assert.equal((await me(env, token, "?fresh=1")).json.reason, "none", "second fresh within a minute: cache");
    assert.equal(stripe.calls, calls2);
    travel(61 * 1000);
    assert.equal((await me(env, token, "?fresh=1")).json.reason, "subscribed");
    stripe.subs.cus_1 = ["canceled"];
    travel(4 * MIN);
    assert.equal((await me(env, token)).json.reason, "subscribed", "still inside the 5 minutes");
    travel(2 * MIN);
    assert.equal((await me(env, token)).json.reason, "none", "cache expired");
  });

  test("a Stripe failure means not a member for this request and is not cached", async () => {
    const env = newEnv();
    subscribed("f@example.com");
    stripe.fail = true;
    const token = await googleAccount(env, "f@example.com");
    let r = await me(env, token);
    assert.deepEqual([r.status, r.json.member, r.json.reason], [200, false, "none"]);
    assert.equal(memGet("member:f@example.com"), undefined);
    stripe.fail = false;
    assert.equal((await me(env, token)).json.reason, "subscribed", "the next request asks again");
    // No Stripe key at all: nobody is a member.
    const bare = newEnv({ STRIPE_SECRET_KEY: "", STRIPE_PAYMENT_LINK: "", STRIPE_PORTAL_LINK: "" });
    subscribed("bare@example.com");
    r = await google(bare, idToken({ email: "bare@example.com" }));
    assert.deepEqual([r.json.me.member, r.json.me.reason, r.json.me.subscribe_url, r.json.me.portal_url], [false, "none", "", ""]);
  });
});

describe("claim", () => {
  test("binds the Checkout Session's customer to the account; the payer email verifies nothing", async () => {
    const env = newEnv();
    const { json: { token } } = await signup(env, "buyer@example.com");
    assert.equal((await me(env, token)).json.reason, "none");
    stripe.urls = [];
    stripe.sessions.cs_test_a1b2c3 = COMPLETE; // its subscription is sub_cus_9_0, the active one
    stripe.subs.cus_9 = ["active", { status: "trialing", trial_end: Math.floor(Date.now() / 1000) + DAY / 1000 }];
    let r = await claim(env, token, "cs_test_a1b2c3");
    assert.equal(r.status, 200);
    assert.deepEqual(Object.keys(r.json), ["me"]);
    assert.deepEqual([r.json.me.member, r.json.me.reason, r.json.me.trial_ends_at, r.json.me.claimed, r.json.me.email_verified],
                     [true, "subscribed", null, true, false], "active beats trialing");
    assert.match(stripe.urls[0], /^\/v1\/checkout\/sessions\/cs_test_a1b2c3\?expand\[\]=subscription$/);
    assert.match(stripe.urls[1], /^\/v1\/subscriptions\?customer=cus_9&status=all/);
    const acct = await account(env, "buyer@example.com");
    assert.equal(acct.stripe_customer, "cus_9");
    assert.equal(acct.trial_used, false, "the claimed subscription never trialed");
    assert.ok(Date.now() - Date.parse(acct.claimed_at) < 5000);
    assert.equal((await call(env, "GET", "/api/deals", { token })).status, 404, "member (nothing published yet)");
    // The bound customer is what counts: cancel it and membership lapses, even
    // when a customer with the same (unverified) email has a live subscription.
    stripe.subs.cus_9 = ["canceled"];
    subscribed("buyer@example.com", ["active"], "cus_other");
    travel(6 * MIN);
    r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason, r.json.trial_available], [false, "lapsed", false]);
    assert.deepEqual(emailLookups(), [], "no lookup by email for an unverified address");
  });

  test("a different payer email still binds (the payer holds the receipt) but does not verify", async () => {
    const env = newEnv();
    const { json: { token } } = await signup(env, "typed@example.com");
    stripe.sessions.cs_live_x9y8z7w6 = { ...COMPLETE, customer: { id: "cus_obj", object: "customer" }, subscription: "sub_cus_obj_0",
                                         customer_details: { email: "someone.else@example.com" } };
    stripe.subs.cus_obj = ["trialing"];
    const r = await claim(env, token, "cs_live_x9y8z7w6");
    assert.deepEqual([r.status, r.json.me.reason, r.json.me.claimed, r.json.me.email_verified], [200, "trial", true, false]);
    assert.equal((await account(env, "typed@example.com")).stripe_customer, "cus_obj");
  });

  test("checkout, then a claim of the trialing session: Stripe's trial, used up; cancel lapses it", async () => {
    const env = newEnv();
    midSlot();
    const { json: { token } } = await signup(env, "flow@example.com");
    let r = await checkout(env, token);
    assert.equal(r.status, 200);
    assert.ok(posted().some(([k, v]) => k === "subscription_data[trial_period_days]" && v === "7"), "first checkout: the trial");
    const end = Math.floor(Date.now() / 1000) + 7 * 86400;
    stripe.sessions.cs_test_flow1234 = { ...COMPLETE, customer: "cus_flow", subscription: "sub_cus_flow_0",
                                         client_reference_id: ref("flow@example.com") };
    stripe.subs.cus_flow = [{ status: "trialing", trial_end: end }];
    r = await claim(env, token, "cs_test_flow1234");
    assert.equal(r.status, 200);
    assert.deepEqual([r.json.me.member, r.json.me.reason, r.json.me.trial_ends_at, r.json.me.trial_available, r.json.me.claimed],
                     [true, "trial", isoOf(end), false, true]);
    let acct = await account(env, "flow@example.com");
    assert.deepEqual([acct.stripe_customer, acct.trial_used], ["cus_flow", true]);
    assert.equal((await call(env, "GET", "/api/deals", { token })).status, 404, "member (nothing published yet)");
    r = await me(env, token);
    assert.deepEqual([r.json.reason, r.json.trial_available], ["trial", false], "and the next time too");
    // A member is never sold a second subscription.
    const posts = stripe.posts.length;
    r = await checkout(env, token);
    assert.deepEqual([r.status, r.json.error, r.json.me.reason], [409, "already_member", "trial"]);
    assert.equal(stripe.posts.length, posts);
    // Cancelled in the portal: lapsed, and a new checkout reuses the customer, without a second trial.
    stripe.subs.cus_flow = [{ status: "canceled", trial_end: end }];
    travel(11 * MIN);
    r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason, r.json.trial_ends_at, r.json.trial_available], [false, "lapsed", null, false]);
    assert.equal((await call(env, "GET", "/api/deals", { token })).status, 402);
    r = await checkout(env, token);
    assert.equal(r.status, 200);
    const form = postedMap();
    assert.equal(form.customer, "cus_flow");
    assert.ok(!("customer_email" in form) && !("subscription_data[trial_period_days]" in form), lastPost().body);
    acct = await account(env, "flow@example.com");
    assert.equal(acct.trial_used, true);
  });

  test("a session our checkout made for another account is 403 wrong_account", async () => {
    const env = newEnv();
    const { json: { token } } = await signup(env, "w@example.com");
    const before = env.SUBS.writes;
    stripe.sessions.cs_test_other123 = { ...COMPLETE, client_reference_id: ref("someone.else@example.com") };
    stripe.subs.cus_9 = ["trialing"];
    let r = await claim(env, token, "cs_test_other123");
    assert.deepEqual([r.status, r.json], [403, { error: "wrong_account" }]);
    let acct = await account(env, "w@example.com");
    assert.deepEqual([acct.stripe_customer, acct.trial_used], [null, false]);
    assert.equal(env.SUBS.writes, before);
    // The account's own reference binds; so does none at all (a Payment Link session).
    stripe.sessions.cs_test_mine1234 = { ...COMPLETE, client_reference_id: ref("w@example.com") };
    r = await claim(env, token, "cs_test_mine1234");
    assert.deepEqual([r.status, r.json.me.reason], [200, "trial"]);
    acct = await account(env, "w@example.com");
    assert.deepEqual([acct.stripe_customer, acct.trial_used], ["cus_9", true]);
  });

  test("bad, unknown and unfinished sessions bind nothing", async () => {
    const env = newEnv();
    const { json: { token: real } } = await signup(env, "b@example.com");
    const before = env.SUBS.writes;
    stripe.calls = 0; // the sign-up's own membership answer
    let n = 0; // a fresh client IP per call: this test alone would trip the 10/hour limit
    const claim = (token, sid) => call(env, "POST", "/api/claim", { body: { session_id: sid }, token, ip: `5.5.5.${n++}` });
    let r = await claim(undefined, "cs_test_a1b2c3");
    assert.deepEqual([r.status, r.json], [401, { error: "unauthorized" }]);
    const token = real;
    for (const bad of ["nope", "", "cs_", "cs_test_a1b2c3/..", 42, undefined]) {
      r = await claim(token, bad);
      assert.deepEqual([r.status, r.json], [400, { error: "bad_session" }], String(bad));
    }
    assert.equal(stripe.calls, 0, "a malformed id never reaches Stripe");
    r = await claim(token, "cs_test_unknown1");
    assert.deepEqual([r.status, r.json], [404, { error: "unknown_session" }]);
    for (const [name, session] of Object.entries({
      open: { ...COMPLETE, status: "open" }, expired: { ...COMPLETE, status: "expired" },
      payment: { ...COMPLETE, mode: "payment" }, no_customer: { ...COMPLETE, customer: null },
      odd_customer: { ...COMPLETE, customer: 7 },
    })) {
      stripe.sessions.cs_test_bad = session;
      r = await claim(token, "cs_test_bad");
      assert.deepEqual([r.status, r.json], [400, { error: "bad_session" }], name);
    }
    stripe.fail = true;
    r = await claim(token, "cs_test_a1b2c3");
    assert.deepEqual([r.status, r.json], [502, { error: "stripe_unavailable" }]);
    stripe.fail = false;
    r = await call(newEnv({ SUBS: env.SUBS, STRIPE_SECRET_KEY: "" }), "POST", "/api/claim", { body: { session_id: "cs_test_a1b2c3" }, token });
    assert.deepEqual([r.status, r.json], [502, { error: "stripe_unavailable" }]);
    const acct = await account(env, "b@example.com");
    assert.deepEqual([acct.stripe_customer, acct.claimed_at, acct.email_verified, acct.trial_used], [null, null, false, false]);
    assert.equal(env.SUBS.writes, before);
  });

  test("10 claims per IP per hour", async () => {
    const env = newEnv();
    const { json: { token } } = await signup(env, "r@example.com");
    for (let i = 0; i < 10; i++) assert.equal((await claim(env, token, "nope", { ip: "4.4.4.4" })).status, 400);
    const r = await claim(env, token, "nope", { ip: "4.4.4.4" });
    assert.deepEqual([r.status, r.json], [429, { error: "rate_limited" }]);
    assert.equal((await claim(env, token, "nope", { ip: "4.4.4.5" })).status, 400);
  });
});

describe("checkout", () => {
  test("a new account: card up front (named: the only method), the trial, the account's reference, a literal {CHECKOUT_SESSION_ID}", async () => {
    const env = newEnv();
    midSlot();
    const { json: { token } } = await signup(env, "New@Example.com");
    const r = await checkout(env, token);
    assert.deepEqual([r.status, Object.keys(r.json)], [200, ["url"]]);
    assert.equal(r.json.url, "https://checkout.stripe.com/c/pay/cs_test_new1");
    assert.equal(r.headers.get("Access-Control-Allow-Origin"), ORIGIN);
    const post = lastPost();
    assert.equal(post.path, "/v1/checkout/sessions");
    const REF = ref("new@example.com");
    assert.match(REF, /^[0-9a-f]{32}$/);
    assert.deepEqual(posted(), [
      ["mode", "subscription"],
      ["line_items[0][price]", "price_999"],
      ["line_items[0][quantity]", "1"],
      ["payment_method_types[0]", "card"],
      ["payment_method_collection", "always"],
      ["client_reference_id", REF],
      ["customer_email", "new@example.com"],
      ["subscription_data[trial_period_days]", "7"],
      ["subscription_data[metadata][wowpriced_ref]", REF],
      ["success_url", "https://wowpriced.com/welcome.html?session_id={CHECKOUT_SESSION_ID}"],
      ["cancel_url", "https://wowpriced.com/?checkout=cancel"],
    ]);
    assert.ok(post.body.includes("session_id%3D%7BCHECKOUT_SESSION_ID%7D"), "the braces are form-encoded exactly once");
    assert.ok(!post.body.includes("%257B"), "never double-encoded");
    assert.equal(post.headers["Idempotency-Key"], `${REF}-${Math.floor(Date.now() / (10 * MIN))}`);
    // A double click: the same session; Stripe made one.
    const again = await checkout(env, token);
    assert.deepEqual([again.status, again.json.url, stripe.created], [200, r.json.url, 1]);
    // Checkout writes nothing: no trial is used up until a claim sees one.
    assert.deepEqual([(await account(env, "new@example.com")).trial_used, (await me(env, token)).json.trial_available], [false, true]);
  });

  test("no second trial: a bound customer is reused, a used trial or TRIAL_DAYS 0 sends none", async () => {
    const env = newEnv();
    const { json: { token } } = await signup(env, "back@example.com");
    stripe.sessions.cs_test_back1234 = { ...COMPLETE, customer: "cus_back", subscription: "sub_cus_back_0" };
    stripe.subs.cus_back = ["canceled"];
    let r = await claim(env, token, "cs_test_back1234");
    assert.deepEqual([r.json.me.reason, r.json.me.trial_available], ["lapsed", false]);
    assert.equal((await checkout(env, token)).status, 200);
    let form = postedMap();
    assert.equal(form.customer, "cus_back");
    assert.ok(!("customer_email" in form) && !("subscription_data[trial_period_days]" in form), lastPost().body);
    assert.equal(form["subscription_data[metadata][wowpriced_ref]"], ref("back@example.com"));
    // trial_used without a bound customer: the email, no trial.
    const { json: { token: t2 } } = await signup(env, "used@example.com");
    await env.SUBS.put("acct:used@example.com", JSON.stringify({ ...(await account(env, "used@example.com")), trial_used: true }));
    assert.equal((await me(env, t2)).json.trial_available, false);
    assert.equal((await checkout(env, t2)).status, 200);
    form = postedMap();
    assert.equal(form.customer_email, "used@example.com");
    assert.ok(!("subscription_data[trial_period_days]" in form));
    // TRIAL_DAYS sets me.trial_days and the trial; 0 means none, a bogus value means 7.
    for (const [days, want, param] of [["0", 0, undefined], ["3", 3, "3"], ["bogus", 7, "7"]]) {
      const e = newEnv({ TRIAL_DAYS: days });
      const s = await signup(e, `t${want}@example.com`);
      assert.equal(s.json.me.trial_days, want, days);
      assert.equal((await checkout(e, s.json.token, { ip: `3.3.3.${want}` })).status, 200);
      assert.equal(postedMap()["subscription_data[trial_period_days]"], param, days);
    }
  });

  test("the price: the Payment Link's, found once, cached in memory and KV; STRIPE_PRICE_ID wins", async () => {
    const env = newEnv();
    stripe.pageSize = 2;
    stripe.links = [{ id: "plink_a", url: "https://buy.stripe.com/other1" }, { id: "plink_b", url: "https://buy.stripe.com/other2" },
                    { id: "plink_c", url: "https://buy.stripe.com/TEST_LINK" }, { id: "plink_d", url: `${PAYMENT_LINK}/` }];
    stripe.linkPrices = { plink_c: "price_wrong_case", plink_d: "price_monthly" };
    const { json: { token } } = await signup(env, "p@example.com");
    let r = await checkout(env, token);
    assert.equal(r.status, 200);
    assert.equal(postedMap()["line_items[0][price]"], "price_monthly", "case-sensitive path, trailing slash ignored");
    const linkCalls = () => stripe.urls.filter((u) => u.startsWith("/v1/payment_links"));
    assert.deepEqual(linkCalls(), ["/v1/payment_links?active=true&limit=100",
                                   "/v1/payment_links?active=true&limit=100&starting_after=plink_b",
                                   "/v1/payment_links/plink_d/line_items?limit=1"]);
    const kv = env.SUBS.m.get("stripe:price");
    assert.deepEqual([JSON.parse(kv.value), kv.ttl], [{ link: PAYMENT_LINK, price: "price_monthly" }, 86400]);
    // The next checkout asks Stripe for no Payment Link.
    stripe.urls = [];
    assert.equal((await checkout(env, token)).status, 200);
    assert.deepEqual(linkCalls(), []);
    // A cold isolate: one KV read, still no lookup.
    memClear();
    env.SUBS.reads = [];
    assert.equal((await checkout(env, token)).status, 200);
    assert.deepEqual([linkCalls(), env.SUBS.reads.filter((k) => k === "stripe:price").length], [[], 1]);
    assert.equal(postedMap()["line_items[0][price]"], "price_monthly");
    // Another Payment Link does not trust the cached price of the old one.
    const moved = newEnv({ SUBS: env.SUBS, STRIPE_PAYMENT_LINK: "https://buy.stripe.com/other2" });
    stripe.linkPrices.plink_b = "price_other";
    travel(11 * MIN); // a new idempotency slot: these params differ from the last session's
    assert.equal((await checkout(moved, token)).status, 200);
    assert.equal(postedMap()["line_items[0][price]"], "price_other");
    // STRIPE_PRICE_ID overrides: no lookup at all.
    stripe.urls = [];
    const fixed = newEnv({ STRIPE_PRICE_ID: "price_fixed" });
    const s = await signup(fixed, "f@example.com");
    assert.equal((await checkout(fixed, s.json.token)).status, 200);
    assert.equal(postedMap()["line_items[0][price]"], "price_fixed");
    assert.deepEqual([linkCalls(), fixed.SUBS.m.has("stripe:price")], [[], false]);
  });

  test("a member gets 409 already_member and nothing is sold twice", async () => {
    const env = newEnv();
    subscribed("member@example.com");
    const token = await googleAccount(env, "member@example.com");
    const r = await checkout(env, token);
    assert.deepEqual([r.status, r.json.error, r.json.me.email, r.json.me.reason], [409, "already_member", "member@example.com", "subscribed"]);
    assert.deepEqual(stripe.posts, []);
    assert.equal((await checkout(env, undefined)).status, 401);
  });

  test("503 checkout_unavailable: the Payment Link fallback once the checks passed, none when they could not run", async () => {
    const env = newEnv();
    const { json: { token } } = await signup(env, "u@example.com");
    const fallback = `${PAYMENT_LINK}?prefilled_email=u%40example.com`;
    const want = (url) => [503, { error: "checkout_unavailable", fallback_url: url }];
    stripe.failPost = true; // Stripe refuses the session
    let r = await checkout(env, token, { ip: "6.6.6.1" });
    assert.deepEqual([r.status, r.json], want(fallback));
    assert.ok(!r.text.includes("boom") && !r.text.includes("secret"), r.text);
    stripe.failPost = false;
    stripe.fail = true; // Stripe down altogether: nobody can tell whether this account already pays
    memClear();
    r = await checkout(newEnv({ SUBS: env.SUBS }), token, { ip: "6.6.6.2" });
    assert.deepEqual([r.status, r.json], want(""), "never sell on a failed check, not even through the Payment Link");
    stripe.fail = false;
    // No price: no Payment Link matches, and no STRIPE_PRICE_ID.
    const lost = newEnv({ SUBS: env.SUBS, STRIPE_PAYMENT_LINK: "https://buy.stripe.com/nowhere" });
    r = await checkout(lost, token, { ip: "6.6.6.3" });
    assert.deepEqual([r.status, r.json], want("https://buy.stripe.com/nowhere?prefilled_email=u%40example.com"));
    assert.deepEqual(JSON.parse(lost.SUBS.m.get("stripe:price").value), { link: PAYMENT_LINK, price: "price_999" },
                     "a miss is never cached");
    // No Payment Link and no price id: nothing to fall back to.
    r = await checkout(newEnv({ SUBS: env.SUBS, STRIPE_PAYMENT_LINK: "" }), token, { ip: "6.6.6.4" });
    assert.deepEqual([r.status, r.json], want(""));
    // No Stripe key: membership cannot be asked either.
    const posts = stripe.posts.length;
    memClear();
    r = await checkout(newEnv({ SUBS: env.SUBS, STRIPE_SECRET_KEY: "" }), token, { ip: "6.6.6.5" });
    assert.deepEqual([r.status, r.json], want(""));
    assert.equal(stripe.posts.length, posts);
  });

  test("10 checkouts per IP per hour", async () => {
    const env = newEnv();
    const { json: { token } } = await signup(env, "r@example.com");
    for (let i = 0; i < 10; i++) assert.equal((await checkout(env, token, { ip: "4.4.4.4" })).status, 200);
    const r = await checkout(env, token, { ip: "4.4.4.4" });
    assert.deepEqual([r.status, r.json], [429, { error: "rate_limited" }]);
    assert.equal((await checkout(env, token, { ip: "4.4.4.5" })).status, 200);
  });
});

describe("portal", () => {
  test("a bound customer gets a billing portal session; else 404; Stripe trouble is 503 with the portal link", async () => {
    const env = newEnv({ SITE_URL: "https://www.wowpriced.com/" });
    assert.deepEqual([(await portal(env, undefined)).status], [401]);
    const { json: { token } } = await signup(env, "pay@example.com");
    stripe.calls = 0; // the sign-up's own membership answer
    let r = await portal(env, token);
    assert.deepEqual([r.status, r.json], [404, { error: "no_customer" }]);
    assert.equal(stripe.calls, 0);
    stripe.sessions.cs_test_pay12345 = { ...COMPLETE, customer: "cus_pay", subscription: "sub_cus_pay_0" };
    stripe.subs.cus_pay = ["active"];
    assert.equal((await claim(env, token, "cs_test_pay12345")).status, 200);
    r = await portal(env, token);
    assert.deepEqual([r.status, r.json], [200, { url: "https://billing.stripe.com/p/session/test_cus_pay" }]);
    assert.equal(lastPost().path, "/v1/billing_portal/sessions");
    assert.deepEqual(posted(), [["customer", "cus_pay"], ["return_url", "https://www.wowpriced.com/"]]);
    stripe.failPost = true;
    r = await portal(env, token);
    assert.deepEqual([r.status, r.json], [503, { error: "portal_unavailable", fallback_url: "https://billing.stripe.com/p/login/test" }]);
    assert.ok(!r.text.includes("boom"));
    stripe.failPost = false;
    r = await portal(newEnv({ SUBS: env.SUBS, STRIPE_SECRET_KEY: "", STRIPE_PORTAL_LINK: "" }), token);
    assert.deepEqual([r.status, r.json], [503, { error: "portal_unavailable", fallback_url: "" }]);
  });
});

describe("one subscription, one trial, one account per payment", () => {
  test("an unclaimed checkout of a password account: the next checkout is 409 while Search lags, never a second subscription", async () => {
    const env = newEnv();
    midSlot();
    const { json: { token } } = await signup(env, "pw@example.com");
    assert.equal((await checkout(env, token)).status, 200);
    assert.equal(postedMap()["subscription_data[trial_period_days]"], "7");
    payLastCheckout({ indexed: false }); // paid; welcome.html's claim never landed, and Search has not caught up
    travel(11 * MIN); // a new idempotency slot, past every cache
    let r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.trial_available], [false, true], "Search does not show it yet");
    r = await checkout(env, token);
    assert.deepEqual([r.status, r.json.error, r.json.me.trial_available], [409, "already_member", false]);
    assert.deepEqual([checkoutPosts().length, stripe.created], [1, 1], "no second session");
    assert.ok(stripe.urls.includes("/v1/customers?email=pw%40example.com&limit=100"), stripe.urls.join("\n"));
  });

  test("an unclaimed checkout of a password account counts once Search sees it: member, the deals, 409 on checkout", async () => {
    const env = newEnv();
    midSlot();
    const { json: { token } } = await signup(env, "pw@example.com");
    assert.equal((await checkout(env, token)).status, 200);
    const { sub } = payLastCheckout();
    travel(11 * MIN);
    let r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason, r.json.trial_ends_at, r.json.trial_available, r.json.claimed],
                     [true, "trial", isoOf(sub.trial_end), false, false]);
    assert.deepEqual(emailLookups(), [], "found by the account's reference, not by its (unverified) email");
    assert.equal((await call(env, "GET", "/api/deals", { token })).status, 404, "member (nothing published yet)");
    r = await checkout(env, token);
    assert.deepEqual([r.status, r.json.error, r.json.me.reason], [409, "already_member", "trial"]);
    assert.equal(stripe.created, 1);
    const { json: { token: other } } = await signup(env, "other@example.com");
    assert.equal((await me(env, other)).json.member, false, "another account's reference never matches");
  });

  test("a Google account that never claims gets one trial: once it ends, checkout sends no second one", async () => {
    const env = newEnv();
    midSlot();
    const token = await googleAccount(env, "gg@example.com");
    assert.equal((await checkout(env, token)).status, 200);
    assert.equal(postedMap()["subscription_data[trial_period_days]"], "7");
    const { customer } = payLastCheckout(); // finished in another browser: no claim
    travel(11 * MIN);
    let r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason, r.json.trial_available], [true, "trial", false]);
    stripe.subs[customer][0].status = "canceled"; // cancelled through the portal's email login
    travel(8 * DAY);
    r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason, r.json.trial_available], [false, "none", false]);
    assert.equal((await checkout(env, token)).status, 200);
    const form = postedMap();
    assert.ok(!("subscription_data[trial_period_days]" in form), lastPost().body);
    assert.deepEqual([form.customer_email, stripe.created], ["gg@example.com", 2]);
  });

  test("a verified account trialing by email is offered no trial; once it lapses, checkout finds the history and records it", async () => {
    const env = newEnv();
    midSlot();
    const token = await googleAccount(env, "em@example.com");
    const end = Math.floor(Date.now() / 1000) + 3 * 86400;
    subscribed("em@example.com", [{ status: "trialing", trial_end: end }], "cus_em"); // not the Worker's checkout: no reference
    travel(6 * MIN);
    let r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason, r.json.trial_ends_at, r.json.trial_available], [true, "trial", isoOf(end), false]);
    stripe.subs.cus_em = [{ status: "canceled", trial_end: end }];
    travel(6 * MIN);
    r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.trial_available], [false, true], "the email lookup sees live subscriptions only");
    assert.equal((await checkout(env, token)).status, 200);
    assert.ok(!("subscription_data[trial_period_days]" in postedMap()), lastPost().body);
    assert.equal((await account(env, "em@example.com")).trial_used, true, "recorded on the account");
    assert.equal((await me(env, token)).json.trial_available, false);
  });

  test("a Payment Link session binds one account: another account's claim is 409 already_claimed", async () => {
    const env = newEnv();
    stripe.sessions.cs_test_plink123 = { ...COMPLETE, customer: "cus_P", subscription: "sub_cus_P_0" }; // no client_reference_id
    stripe.subs.cus_P = ["active"];
    const a = (await signup(env, "a@example.com")).json.token;
    const b = (await signup(env, "b@example.com")).json.token;
    let r = await claim(env, a, "cs_test_plink123");
    assert.deepEqual([r.status, r.json.me.reason, r.json.me.claimed], [200, "subscribed", true]);
    assert.equal(await env.SUBS.get("claim:cus_P"), "a@example.com");
    const writes = env.SUBS.writes;
    r = await claim(env, b, "cs_test_plink123");
    assert.deepEqual([r.status, r.json], [409, { error: "already_claimed" }]);
    assert.equal(env.SUBS.writes, writes, "nothing written");
    assert.deepEqual([(await account(env, "b@example.com")).stripe_customer, (await me(env, b)).json.member], [null, false]);
    assert.equal((await portal(env, b)).status, 404, "no portal for the payer's customer");
    assert.equal((await checkout(env, b)).status, 200);
    assert.deepEqual([postedMap().customer, postedMap().customer_email], [undefined, "b@example.com"], "never billed onto it");
    const before = env.SUBS.writes;
    r = await claim(env, a, "cs_test_plink123");
    assert.equal(r.status, 200, "the payer claiming again is fine");
    assert.equal(env.SUBS.writes, before + 1, "and rewrites the account only");
  });

  test("Google takeover unbinds the squatter's customer: the owner checks out on their own, with their trial", async () => {
    const env = newEnv();
    midSlot();
    const { json: { token: squat } } = await signup(env, "owner@example.com");
    stripe.sessions.cs_test_squat999 = { ...COMPLETE, customer: "cus_S", subscription: "sub_cus_S_0",
                                         customer_details: { email: "squatter@evil.com" } };
    stripe.customers["squatter@evil.com"] = ["cus_S"];
    stripe.subs.cus_S = [{ status: "trialing", trial_end: Math.floor(Date.now() / 1000) + 7 * 86400 }];
    let r = await claim(env, squat, "cs_test_squat999");
    assert.deepEqual([r.status, r.json.me.reason, r.json.me.trial_available], [200, "trial", false]);
    stripe.subs.cus_S[0].status = "canceled";
    r = await google(env, idToken({ email: "owner@example.com", sub: "owner" }));
    assert.deepEqual([r.json.me.member, r.json.me.reason, r.json.me.claimed, r.json.me.trial_available], [false, "none", false, true]);
    const acct = await account(env, "owner@example.com");
    assert.deepEqual([acct.stripe_customer, acct.claimed_at, acct.trial_used], [null, null, false]);
    assert.equal(await env.SUBS.get("claim:cus_S"), null);
    assert.equal((await portal(env, r.json.token)).status, 404, "no portal into the squatter's customer");
    assert.equal((await checkout(env, r.json.token)).status, 200);
    const form = postedMap();
    assert.deepEqual([form.customer, form.customer_email, form["subscription_data[trial_period_days]"]],
                     [undefined, "owner@example.com", "7"]);
    // The squatter's receipt now binds to their own account only.
    const { json: { token: own } } = await signup(env, "squatter@evil.com");
    assert.equal((await claim(env, own, "cs_test_squat999")).status, 200);
    assert.equal(await env.SUBS.get("claim:cus_S"), "squatter@evil.com");
    // A squatter's Worker checkout carried the owner's address, so Stripe has a
    // subscription for that address: the owner's account gets no trial on it.
    const { json: { token: s2 } } = await signup(env, "own2@example.com");
    assert.equal((await checkout(env, s2, { ip: "2.2.2.2" })).status, 200);
    stripe.subs[payLastCheckout().customer][0].status = "canceled";
    r = await google(env, idToken({ email: "own2@example.com", sub: "own2" }));
    assert.deepEqual([r.json.me.claimed, r.json.me.trial_available], [false, false]);
  });

  test("a failed email lookup during checkout's membership check is 503 with no fallback, never a session", async () => {
    const env = newEnv();
    const token = await googleAccount(env, "pg@example.com");
    for (let i = 0; i < 3; i++) subscribed(`o${i}@example.com`, ["active"], `cus_o${i}`);
    subscribed("pg@example.com", ["active"], "cus_pg");
    stripe.pageSize = 1;
    stripe.failGet = (url) => (url.searchParams.get("starting_after") ? 500 : 0); // the second page fails
    let r = await checkout(env, token);
    assert.deepEqual([r.status, r.json], [503, { error: "checkout_unavailable", fallback_url: "" }]);
    assert.deepEqual(checkoutPosts(), []);
    stripe.failGet = null;
    travel(61 * 1000);
    r = await checkout(env, token);
    assert.deepEqual([r.status, r.json.error, r.json.me.reason], [409, "already_member", "subscribed"]);
  });

  test("a bound member whose subscription read is rate-limited gets 503 from checkout, never a second subscription", async () => {
    const env = newEnv({ STRIPE_PRICE_ID: "price_fixed" });
    const { json: { token } } = await signup(env, "bm@example.com");
    stripe.sessions.cs_test_bm123456 = { ...COMPLETE, customer: "cus_M", subscription: "sub_cus_M_0" };
    stripe.subs.cus_M = ["active"];
    assert.equal((await claim(env, token, "cs_test_bm123456")).json.me.reason, "subscribed");
    stripe.failGet = (url) => (url.pathname === "/v1/subscriptions" && url.searchParams.get("customer") ? 429 : 0);
    travel(6 * MIN);
    let r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason], [false, "lapsed"], "/api/me cannot tell during the failure");
    r = await checkout(env, token);
    assert.deepEqual([r.status, r.json], [503, { error: "checkout_unavailable", fallback_url: "" }]);
    assert.deepEqual(checkoutPosts(), []);
  });

  test("Stripe reads failing while writes work: checkout is 503 and posts nothing, at the membership or the history check", async () => {
    const env = newEnv();
    const { json: { token } } = await signup(env, "tr@example.com");
    const end = Math.floor(Date.now() / 1000) + 5 * 86400;
    stripe.sessions.cs_test_tr123456 = { ...COMPLETE, customer: "cus_bm", subscription: "sub_cus_bm_0" };
    stripe.subs.cus_bm = [{ status: "trialing", trial_end: end }];
    assert.equal((await claim(env, token, "cs_test_tr123456")).json.me.reason, "trial");
    stripe.failGet = () => 429; // every GET
    let r = await checkout(env, token);
    assert.deepEqual([r.status, r.json], [503, { error: "checkout_unavailable", fallback_url: "" }]);
    // Lapsed now, and only the history check (customers by email) fails.
    stripe.subs.cus_bm = [{ status: "canceled", trial_end: end }];
    stripe.failGet = (url) => (url.pathname === "/v1/customers" ? 503 : 0);
    travel(61 * 1000);
    r = await checkout(env, token);
    assert.deepEqual([r.status, r.json], [503, { error: "checkout_unavailable", fallback_url: "" }]);
    assert.deepEqual(checkoutPosts(), []);
    stripe.failGet = null;
    travel(61 * 1000);
    assert.equal((await checkout(env, token)).status, 200);
    assert.equal(postedMap().customer, "cus_bm");
  });

  test("the portal session uses the wowpriced portal configuration (found once, cached per key mode); STRIPE_PORTAL_CONFIG wins", async () => {
    const env = newEnv();
    stripe.pageSize = 1;
    stripe.portalConfigs = [{ id: "bpc_dashboard", metadata: {} }, { id: "bpc_wow", metadata: { app: "wowpriced" } }];
    const { json: { token } } = await signup(env, "pc@example.com");
    stripe.sessions.cs_test_pc123456 = { ...COMPLETE, customer: "cus_pc", subscription: "sub_cus_pc_0" };
    stripe.subs.cus_pc = ["active"];
    assert.equal((await claim(env, token, "cs_test_pc123456")).status, 200);
    assert.equal((await portal(env, token)).status, 200);
    assert.deepEqual(posted(), [["customer", "cus_pc"], ["configuration", "bpc_wow"], ["return_url", "https://wowpriced.com/"]]);
    const configCalls = () => stripe.urls.filter((u) => u.startsWith("/v1/billing_portal/configurations"));
    assert.deepEqual(configCalls(), ["/v1/billing_portal/configurations?active=true&limit=100",
                                     "/v1/billing_portal/configurations?active=true&limit=100&starting_after=bpc_dashboard"]);
    assert.deepEqual(JSON.parse(env.SUBS.m.get("stripe:portal").value), { mode: "test", config: "bpc_wow" });
    stripe.urls = [];
    assert.equal((await portal(env, token)).status, 200);
    memClear(); // a cold isolate: KV answers
    assert.equal((await portal(env, token)).status, 200);
    assert.deepEqual([configCalls(), postedMap().configuration], [[], "bpc_wow"]);
    memClear(); // a live key never trusts the test-mode id
    assert.equal((await portal(newEnv({ SUBS: env.SUBS, STRIPE_SECRET_KEY: "sk_live_123" }), token)).status, 200);
    assert.equal(configCalls().length, 2, "looked up again");
    stripe.urls = [];
    assert.equal((await portal(newEnv({ SUBS: env.SUBS, STRIPE_PORTAL_CONFIG: "bpc_env" }), token)).status, 200);
    assert.deepEqual([configCalls(), postedMap().configuration], [[], "bpc_env"]);
  });
});

describe("deals and publish", () => {
  const PAYLOAD = '{"deals": [ {"item": "Widget", "final_price": 9.5} ],\n "meta": {"count": 1}}';

  test("publish needs the list secret and a deals array, then stores verbatim", async () => {
    const env = newEnv();
    let r = await call(env, "POST", "/api/publish", { body: PAYLOAD });
    assert.deepEqual([r.status, r.json], [401, { error: "unauthorized" }]);
    r = await call(env, "POST", "/api/publish", { body: PAYLOAD, token: "wrong" });
    assert.equal(r.status, 401);
    r = await call(newEnv({ NEWSLETTER_SECRET: "" }), "POST", "/api/publish", { body: PAYLOAD, token: "" });
    assert.equal(r.status, 401);
    for (const bad of ["{not json", '{"meta": {}}', '{"deals": {}}', "[]", ""]) {
      r = await call(env, "POST", "/api/publish", { body: bad, token: "s3cret" });
      assert.deepEqual([r.status, r.json], [400, { error: "bad_payload" }], bad);
    }
    assert.equal(await env.SUBS.get("deals:full"), null);
    r = await call(env, "POST", "/api/publish", { body: PAYLOAD, token: "s3cret" });
    assert.deepEqual([r.status, r.text], [204, ""]);
    assert.equal(await env.SUBS.get("deals:full"), PAYLOAD);
  });

  test("deals: 401 without a session, 402 with me when not a member, 200 verbatim when one", async () => {
    const env = newEnv();
    let r = await call(env, "GET", "/api/deals");
    assert.deepEqual([r.status, r.json], [401, { error: "unauthorized" }]);
    const token = await googleAccount(env, "d@example.com");
    await call(env, "POST", "/api/publish", { body: PAYLOAD, token: "s3cret" });
    r = await call(env, "GET", "/api/deals", { token });
    assert.equal(r.status, 402);
    assert.deepEqual([r.json.email, r.json.member, r.json.reason], ["d@example.com", false, "none"]);
    assert.ok(!r.text.includes("Widget"), "no deal leaks to a non-member");
    subscribed("d@example.com", [{ status: "trialing", trial_end: Math.floor(Date.now() / 1000) + 86400 }]);
    travel(6 * MIN);
    r = await call(env, "GET", "/api/deals", { token });
    assert.equal(r.status, 200, "Stripe's trial unlocks everything");
    assert.equal(r.text, PAYLOAD);
    assert.equal(r.headers.get("Content-Type"), "application/json; charset=utf-8");
    assert.equal(r.headers.get("Access-Control-Allow-Origin"), ORIGIN);
    stripe.subs.cus_1 = ["canceled"];
    travel(6 * MIN);
    assert.equal((await call(env, "GET", "/api/deals", { token })).status, 402);
    const fresh = newEnv();
    subscribed("e@example.com");
    const t2 = await googleAccount(fresh, "e@example.com");
    r = await call(fresh, "GET", "/api/deals", { token: t2 });
    assert.deepEqual([r.status, r.json], [404, { error: "no_payload" }], "member, nothing published yet");
  });
});

describe("abuse control", () => {
  test("10 signups, 20 logins and 20 google posts per IP per hour, counted in memory", async () => {
    const env = newEnv();
    const ip = "9.9.9.9";
    for (let i = 0; i < 10; i++) assert.equal((await signup(env, `s${i}@example.com`, PASSWORD, { ip })).status, 200);
    let r = await signup(env, "s11@example.com", PASSWORD, { ip });
    assert.deepEqual([r.status, r.json], [429, { error: "rate_limited" }]);
    assert.equal((await signup(env, "s11@example.com", PASSWORD, { ip: "8.8.8.8" })).status, 200, "other IP");
    travel(3600 * 1000);
    assert.equal((await signup(env, "s12@example.com", PASSWORD, { ip })).status, 200, "next hour");

    for (let i = 0; i < 20; i++) assert.equal((await login(env, "s0@example.com", "wrong", { ip })).status, 401);
    r = await login(env, "s0@example.com", PASSWORD, { ip });
    assert.deepEqual([r.status, r.json], [429, { error: "rate_limited" }]);

    for (let i = 0; i < 20; i++) assert.equal((await google(env, "x", { ip })).status, 401);
    r = await google(env, idToken(), { ip });
    assert.deepEqual([r.status, r.json], [429, { error: "rate_limited" }]);
    assert.equal(memGet(`rl:signup:${ip}:${Math.floor(Date.now() / 3600000)}`), 1, "this hour's counter");
    assert.ok(![...env.SUBS.m.keys()].some((k) => k.startsWith("rl:")), "counters never touch KV");
  });
});

describe("CORS", () => {
  test("preflight and responses for the allowed origins only", async () => {
    const env = newEnv();
    let r = await call(env, "OPTIONS", "/api/login", { headers: { "Access-Control-Request-Method": "POST" } });
    assert.equal(r.status, 204);
    assert.equal(r.headers.get("Access-Control-Allow-Origin"), ORIGIN);
    assert.equal(r.headers.get("Access-Control-Allow-Methods"), "GET, POST, OPTIONS");
    assert.equal(r.headers.get("Access-Control-Allow-Headers"), "Authorization, Content-Type");
    assert.equal(r.headers.get("Access-Control-Allow-Credentials"), null);
    assert.equal(r.headers.get("Vary"), "Origin");
    r = await call(env, "OPTIONS", "/api/claim", { origin: "https://fznishigeo-cmyk.github.io" });
    assert.equal(r.headers.get("Access-Control-Allow-Origin"), "https://fznishigeo-cmyk.github.io");
    r = await call(env, "OPTIONS", "/api/login", { origin: "https://evil.example" });
    assert.equal(r.status, 403);
    assert.equal(r.headers.get("Access-Control-Allow-Origin"), null);
    r = await signup(env, "c@example.com", PASSWORD, { origin: "https://evil.example" });
    assert.equal(r.status, 200);
    assert.equal(r.headers.get("Access-Control-Allow-Origin"), null);
    r = await me(env, r.json.token);
    assert.equal(r.headers.get("Access-Control-Allow-Origin"), ORIGIN);
    r = await call(newEnv({ ALLOWED_ORIGINS: "https://only.example" }), "OPTIONS", "/api/me");
    assert.equal(r.status, 403);
  });
});

describe("Google sign-in", () => {
  test("a valid ID token creates a verified account on first sign-in and reuses it after", async () => {
    const env = newEnv();
    travel(30 * DAY); // whatever an earlier test cached is stale now
    let r = await google(env, idToken());
    assert.equal(r.status, 200);
    assert.equal(typeof r.json.token, "string");
    assert.deepEqual([r.json.me.email, r.json.me.member, r.json.me.reason, r.json.me.email_verified, r.json.me.trial_available],
                     ["g.user@example.com", false, "none", true, true]);
    const acct = await account(env, "g.user@example.com");
    assert.deepEqual([acct.google_sub, acct.scheme, acct.salt, acct.hash, acct.email_verified, acct.trial_used],
                     ["g-sub-1", null, null, null, true, false]);
    assert.equal(acct.trial_ends_at, undefined, "no card-less trial");
    assert.equal((await me(env, r.json.token)).status, 200);
    const fetched = jwks.calls;
    const writes = env.SUBS.writes;
    travel(30 * MIN);
    r = await google(env, idToken());
    assert.equal(r.status, 200);
    assert.deepEqual(await account(env, "g.user@example.com"), acct, "the same account on the next sign-in");
    assert.equal(jwks.calls, fetched, "JWKS served from the in-memory cache");
    assert.equal(env.SUBS.writes, writes, "an unchanged account is not re-written");
    travel(31 * MIN); // the hour is up: fetched again, and written to KV too
    assert.equal((await google(env, idToken())).status, 200);
    assert.equal(jwks.calls, fetched + 1);
    assert.ok(env.SUBS.m.has("jwks:google"));
  });

  test("a pre-paywall-v2 Google-only record counts as verified; a password one does not", async () => {
    const env = newEnv();
    const base = { created_at: "2026-01-01T00:00:00.000Z", trial_ends_at: "2026-01-08T00:00:00.000Z" };
    await env.SUBS.put("acct:oldg@example.com", JSON.stringify({ ...base, email: "oldg@example.com", salt: null, hash: null, google_sub: "g-old" }));
    await env.SUBS.put("acct:oldp@example.com", JSON.stringify({ ...base, email: "oldp@example.com", salt: "x", hash: "y", google_sub: null }));
    subscribed("oldg@example.com");
    subscribed("oldp@example.com", ["active"], "cus_p");
    assert.equal((await me(env, await signSession(env, "oldg@example.com"))).json.reason, "subscribed");
    assert.equal((await me(env, await signSession(env, "oldp@example.com"))).json.reason, "none");
  });

  test("bad audience, issuer, expiry, signature, kid or email are 401 bad_token, without a fetch", async () => {
    const env = newEnv();
    const now = Math.floor(Date.now() / 1000);
    const bad = {
      aud: idToken({ aud: "other-client.apps.googleusercontent.com" }),
      iss: idToken({ iss: "https://accounts.example.com" }),
      exp: idToken({ exp: now - 1 }),
      unverified: idToken({ email_verified: false }),
      no_email: idToken({ email: undefined }),
      alg: (() => { const [, p, s] = idToken().split("."); const h = Buffer.from(JSON.stringify({ alg: "HS256", kid: "kid-1" })).toString("base64url"); return `${h}.${p}.${s}`; })(),
      garbage: "not.a.token",
      empty: "",
      number: 42,
    };
    const fetched = jwks.calls;
    for (const [name, credential] of Object.entries(bad)) {
      const r = await google(env, credential);
      assert.deepEqual([r.status, r.json], [401, { error: "bad_token" }], name);
    }
    assert.equal(jwks.calls, fetched, "claims are checked before any network call");
    const r = await google(env, idToken({}, { key: strangerKey }));
    assert.deepEqual([r.status, r.json], [401, { error: "bad_token" }], "signature");
    assert.deepEqual((await call(env, "POST", "/api/google", { body: {} })).status, 401);
    assert.equal(await env.SUBS.get("acct:g.user@example.com"), null, "no account from a bad token");
    assert.equal((await google(newEnv({ GOOGLE_CLIENT_ID: "" }), idToken({ aud: "" }))).status, 401,
                 "Google sign-in is off without a client id");
  });

  test("an unknown kid refetches the JWKS at most once a minute", async () => {
    const env = newEnv();
    travel(31 * DAY); // past any cache or refetch an earlier test left
    assert.equal((await google(env, idToken())).status, 200); // the hourly fetch, out of the way
    const before = jwks.calls;
    let r = await google(env, idToken({}, { kid: "rotated" }));
    assert.deepEqual([r.status, r.json], [401, { error: "bad_token" }]);
    assert.equal(jwks.calls, before + 1);
    r = await google(env, idToken({}, { kid: "rotated-again" }));
    assert.deepEqual([r.status, r.json], [401, { error: "bad_token" }]);
    assert.equal(jwks.calls, before + 1, "throttled: no second fetch inside a minute");
    assert.equal((await google(env, idToken())).status, 200, "the real key still works");
    travel(61 * 1000);
    assert.equal((await google(env, idToken({}, { kid: "rotated" }))).status, 401);
    assert.equal(jwks.calls, before + 2);
  });
});

describe("newsletter routes", () => {
  const form = (env, path, body, headers = {}) => call(env, "POST", path, { body: new URLSearchParams(body),
    headers: { "Content-Type": "application/x-www-form-urlencoded", ...headers } });
  const list = (env, query = "") => call(env, "GET", `/list${query}`, { token: "s3cret" });
  const emails = (r) => r.json.emails.map((e) => e.email);

  test("subscribe writes a pending record; confirm-sent and confirm move it along; only confirmed addresses are listed", async () => {
    const env = newEnv();
    let r = await call(env, "POST", "/subscribe", { body: { email: " You@Example.COM " } });
    assert.deepEqual([r.status, r.json, r.headers.get("Access-Control-Allow-Origin")], [200, { ok: true }, ORIGIN]);
    const first = JSON.parse(await env.SUBS.get("sub:you@example.com"));
    assert.deepEqual(Object.keys(first).sort(), ["confirm_sent_at", "confirmed", "token", "ts"]);
    assert.deepEqual([first.confirmed, first.confirm_sent_at], [false, ""]);
    assert.match(first.token, /^[0-9a-f]{32}$/);
    assert.deepEqual(env.SUBS.m.get("sub:you@example.com").metadata, first);
    r = await call(env, "POST", "/subscribe", { body: { email: "you@example.com" } });
    assert.deepEqual([r.status, r.json], [200, { ok: true }]);
    assert.deepEqual(JSON.parse(await env.SUBS.get("sub:you@example.com")), first, "idempotent, still pending");
    r = await form(env, "/subscribe", { email: "Second@example.com" }, { Referer: "https://www.wowpriced.com/" });
    assert.deepEqual([r.status, r.headers.get("Location")], [303, "https://www.wowpriced.com/subscribe.html?ok=1"]);
    r = await form(env, "/subscribe", { email: "nope" }, { Referer: "https://evil.example/" });
    assert.deepEqual([r.status, r.headers.get("Location")], [303, "https://wowpriced.com/subscribe.html?err=email"]);
    r = await call(env, "POST", "/subscribe", { body: { email: "nope@" } });
    assert.deepEqual([r.status, r.json], [400, { ok: false, error: "invalid email" }]);
    // A record from before double opt-in has no "confirmed" key: confirmed.
    const legacy = { ts: "2026-01-01T00:00:00.000Z", token: "a".repeat(32) };
    await env.SUBS.put("sub:early@example.com", JSON.stringify(legacy), { metadata: legacy });

    assert.equal((await call(env, "GET", "/list")).status, 401);
    assert.equal((await call(env, "GET", "/list", { token: "wrong" })).status, 401);
    assert.equal((await call(env, "GET", "/list?pending=1", { token: "wrong" })).status, 401);
    r = await list(env);
    assert.deepEqual(r.json, { emails: [{ email: "early@example.com", token: legacy.token }] });
    r = await list(env, "?pending=1");
    const second = JSON.parse(await env.SUBS.get("sub:second@example.com"));
    assert.deepEqual(r.json, { emails: [{ email: "second@example.com", token: second.token }, { email: "you@example.com", token: first.token }] });

    // The pipeline mailed one of them a confirm link.
    r = await call(env, "POST", "/list/confirm-sent", { body: { emails: ["You@example.com", "unknown@example.com", "early@example.com"] }, token: "s3cret" });
    assert.deepEqual([r.status, r.text], [204, ""]);
    const stamped = JSON.parse(await env.SUBS.get("sub:you@example.com"));
    assert.ok(Date.now() - Date.parse(stamped.confirm_sent_at) < 5000);
    assert.deepEqual({ ...stamped, confirm_sent_at: "" }, first);
    assert.equal(env.SUBS.m.get("sub:you@example.com").metadata.confirm_sent_at, stamped.confirm_sent_at);
    assert.deepEqual(JSON.parse(await env.SUBS.get("sub:early@example.com")), legacy, "a confirmed record is left alone");
    assert.deepEqual(emails(await list(env, "?pending=1")), ["second@example.com"]);
    assert.deepEqual(emails(await list(env)), ["early@example.com"], "sent is not confirmed");
    assert.equal((await call(env, "POST", "/list/confirm-sent", { body: { emails: [] } })).status, 401);
    for (const bad of [{}, { emails: "you@example.com" }, "{not json"]) {
      assert.equal((await call(env, "POST", "/list/confirm-sent", { body: bad, token: "s3cret" })).status, 400);
    }

    // The confirm link.
    r = await call(env, "GET", `/confirm?e=you@example.com&t=${"0".repeat(32)}`);
    assert.equal(r.status, 403);
    assert.match(r.text, /isn't valid/);
    assert.equal((await call(env, "GET", "/confirm?e=you@example.com")).status, 403);
    assert.equal((await call(env, "GET", `/confirm?e=nobody@example.com&t=${first.token}`)).status, 403);
    assert.equal((await call(env, "GET", "/confirm")).status, 403);
    assert.deepEqual(emails(await list(env)), ["early@example.com"], "nothing confirmed by a bad link");
    r = await call(env, "GET", `/confirm?e=You@Example.com&t=${first.token}`);
    assert.equal(r.status, 200);
    assert.equal(r.headers.get("Content-Type"), "text/html; charset=utf-8");
    assert.match(r.text, /You're confirmed/);
    assert.ok(r.text.includes('href="https://wowpriced.com/subscribe.html?confirmed=1"'));
    const confirmed = JSON.parse(await env.SUBS.get("sub:you@example.com"));
    assert.deepEqual(confirmed, { ...stamped, confirmed: true });
    assert.equal(env.SUBS.m.get("sub:you@example.com").metadata.confirmed, true);
    assert.deepEqual(emails(await list(env)), ["early@example.com", "you@example.com"]);
    assert.deepEqual(emails(await list(env, "?pending=1")), ["second@example.com"]);
    const writes = env.SUBS.writes;
    assert.equal((await call(env, "GET", `/confirm?e=you@example.com&t=${first.token}`)).status, 200, "clicking twice is fine");
    assert.equal(env.SUBS.writes, writes, "and writes nothing");
    assert.equal((await call(env, "POST", "/subscribe", { body: { email: "you@example.com" } })).status, 200);
    assert.deepEqual(JSON.parse(await env.SUBS.get("sub:you@example.com")), confirmed, "re-subscribing keeps a confirmed record");
  });

  test("unsubscribe: GET shows the button, POST (button or one-click) deletes", async () => {
    const env = newEnv();
    await call(env, "POST", "/subscribe", { body: { email: "you@example.com" } });
    const tok = JSON.parse(await env.SUBS.get("sub:you@example.com")).token;
    let r = await call(env, "GET", "/unsubscribe?e=you@example.com&t=badtoken");
    assert.equal(r.status, 403);
    assert.equal((await call(env, "POST", "/unsubscribe?e=you@example.com&t=badtoken")).status, 403);
    assert.equal((await call(env, "GET", "/unsubscribe?e=nope&t=x")).status, 403);
    assert.ok(await env.SUBS.get("sub:you@example.com"), "kept");
    r = await call(env, "GET", `/unsubscribe?e=You@example.com&t=${tok}`);
    assert.equal(r.status, 200);
    assert.match(r.text, /Unsubscribe from wowpriced\?/);
    assert.ok(r.text.includes(`<form method="post" action="/unsubscribe?e=you%40example.com&amp;t=${tok}">`), r.text);
    assert.match(r.text, /<button type="submit"[^>]*>Unsubscribe<\/button>/);
    assert.ok(await env.SUBS.get("sub:you@example.com"), "a GET (link scanners prefetch) removes nobody");
    assert.equal(env.SUBS.writes, 1);
    // RFC 8058 one-click: a mail app POSTs the List-Unsubscribe URL as is.
    r = await form(env, `/unsubscribe?e=you@example.com&t=${tok}`, { "List-Unsubscribe": "One-Click" });
    assert.equal(r.status, 200);
    assert.match(r.text, /You're unsubscribed/);
    assert.ok(r.text.includes('href="https://wowpriced.com/subscribe.html?unsubscribed=1"'));
    assert.equal(await env.SUBS.get("sub:you@example.com"), null);
    assert.equal((await form(env, `/unsubscribe?e=you@example.com&t=${tok}`, { "List-Unsubscribe": "One-Click" })).status, 200, "twice is fine");
    assert.equal((await call(env, "POST", `/unsubscribe?e=you@example.com&t=${tok}`)).status, 200, "no body at all");
    // The page's button can also carry the link as form fields.
    await call(env, "POST", "/subscribe", { body: { email: "again@example.com" } });
    const tok2 = JSON.parse(await env.SUBS.get("sub:again@example.com")).token;
    r = await form(env, "/unsubscribe", { e: "again@example.com", t: tok2 });
    assert.equal(r.status, 200);
    assert.equal(await env.SUBS.get("sub:again@example.com"), null);
    assert.equal((await call(env, "GET", "/unsubscribe?e=gone@example.com&t=x")).status, 200, "unknown address: the page, then success");
  });

  test("subscribe keeps its own 20-per-hour counter", async () => {
    const env = newEnv();
    for (let i = 0; i < 20; i++) {
      assert.equal((await call(env, "POST", "/subscribe", { body: { email: `u${i}@example.com` }, ip: "7.7.7.7" })).status, 200);
    }
    const r = await call(env, "POST", "/subscribe", { body: { email: "u21@example.com" }, ip: "7.7.7.7" });
    assert.deepEqual([r.status, r.json], [429, { ok: false, error: "too many requests" }]);
    assert.equal((await signup(env, "still@example.com", PASSWORD, { ip: "7.7.7.7" })).status, 200, "separate buckets");
    const f = await form(env, "/subscribe", { email: "u22@example.com" }, { "CF-Connecting-IP": "7.7.7.7" });
    assert.equal(f.status, 429);
    assert.match(f.text, /Slow down/);
  });

  test("everything else is 404", async () => {
    const env = newEnv();
    for (const [method, path] of [["GET", "/"], ["GET", "/subscribe"], ["DELETE", "/list"], ["GET", "/api"],
                                  ["POST", "/confirm"], ["GET", "/list/confirm-sent"]]) {
      const r = await call(env, method, path);
      assert.deepEqual([r.status, r.text], [404, "not found"], path);
    }
    for (const [method, path] of [["GET", "/api/nope"], ["GET", "/api/signup"], ["GET", "/api/claim"], ["DELETE", "/api/me"],
                                  ["PUT", "/api/publish"], ["GET", "/api/checkout"], ["GET", "/api/portal"]]) {
      const r = await call(env, method, path);
      assert.deepEqual([r.status, r.json], [404, { error: "not_found" }], path);
    }
  });
});
