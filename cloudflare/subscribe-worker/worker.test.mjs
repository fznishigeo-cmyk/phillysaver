// Tests for the wowpriced Worker, offline: an in-memory KV, a fetch stub for
// Stripe and Google's JWKS, and a locally generated RS256 key pair standing
// in for Google. Run from the repository root:
//   node --test cloudflare/subscribe-worker/
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { generateKeyPairSync, sign as rsaSign, createHmac, pbkdf2Sync } from "node:crypto";

import worker from "./worker.js";
import { signSession } from "./auth.js";
import { memGet, memClear } from "./util.js";

// ------------------------------------------------------------- fixtures

// Workers KV, enough of it: values + metadata, prefix listing with cursors.
// expirationTtl is ignored; the code checks its own timestamps.
class FakeKV {
  constructor() { this.m = new Map(); this.writes = 0; }
  async get(k) { const v = this.m.get(k); return v ? v.value : null; }
  async put(k, value, opts = {}) { this.writes += 1; this.m.set(k, { value, metadata: opts.metadata || null }); }
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
// customer ids; subs: customer id -> subscription statuses; sessions:
// Checkout Session id -> object. Lists page like Stripe (limit, has_more,
// starting_after), with pageSize to force paging in a test.
const stripe = { customers: {}, subs: {}, sessions: {}, fail: false, calls: 0, pageSize: 100, urls: [] };
const jwks = { calls: 0 };
const jsonResponse = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function everySubscription() {
  const out = [];
  for (const [email, ids] of Object.entries(stripe.customers)) {
    for (const id of ids) {
      (stripe.subs[id] || []).forEach((status, i) =>
        out.push({ id: `sub_${id}_${i}`, status, customer: { id, object: "customer", email } }));
    }
  }
  return out;
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
    assert.equal(init.headers.Authorization, `Basic ${btoa("sk_test_123:")}`);
    if (stripe.fail) return jsonResponse({ error: { message: "boom" } }, 500);
    if (url.pathname === "/v1/subscriptions" && url.searchParams.get("customer")) {
      assert.equal(url.searchParams.get("status"), "all");
      return jsonResponse({ data: (stripe.subs[url.searchParams.get("customer")] || []).map((status) => ({ status })) });
    }
    if (url.pathname === "/v1/subscriptions") {
      assert.equal(url.searchParams.get("expand[]"), "data.customer");
      const status = url.searchParams.get("status");
      assert.ok(status === "active" || status === "trialing", status);
      const all = everySubscription().filter((s) => s.status === status);
      const after = url.searchParams.get("starting_after");
      const start = after ? all.findIndex((s) => s.id === after) + 1 : 0;
      const size = Math.min(Number(url.searchParams.get("limit")), stripe.pageSize);
      const data = all.slice(start, start + size);
      return jsonResponse({ data, has_more: start + size < all.length });
    }
    const m = url.pathname.match(/^\/v1\/checkout\/sessions\/([^/]+)$/);
    if (m) {
      const session = stripe.sessions[m[1]];
      return session ? jsonResponse(session)
                     : jsonResponse({ error: { type: "invalid_request_error", message: "No such checkout.session" } }, 404);
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
  Object.assign(stripe, { customers: {}, subs: {}, sessions: {}, fail: false, calls: 0, pageSize: 100, urls: [] });
  memClear(); // rate counters and the membership cache are per isolate
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
const account = async (env, email) => JSON.parse(await env.SUBS.get(`acct:${email}`));

// A Google-created account: email verified, so Stripe is asked by email.
const googleAccount = async (env, email = "G.User@Example.com") => (await google(env, idToken({ email }))).json.token;

function subscribed(email, statuses = ["active"], id = "cus_1") {
  stripe.customers[email] = [id];
  stripe.subs[id] = statuses;
}

const COMPLETE = { status: "complete", mode: "subscription", customer: "cus_9",
                   customer_details: { email: "Buyer@Example.com" } };

// ---------------------------------------------------------------- tests

describe("signup and login", () => {
  test("signup creates a trial account and signs it in", async () => {
    const env = newEnv();
    const r = await signup(env, " New@Example.COM ");
    assert.equal(r.status, 200);
    assert.equal(typeof r.json.token, "string");
    assert.equal(r.headers.get("Access-Control-Allow-Origin"), ORIGIN);
    assert.equal(r.headers.get("Cache-Control"), "no-store");
    const expected = {
      email: "new@example.com", member: true, reason: "trial", email_verified: false, claimed: false,
      subscribe_url: `${PAYMENT_LINK}?prefilled_email=new%40example.com`,
      portal_url: "https://billing.stripe.com/p/login/test",
    };
    assert.deepEqual({ ...r.json.me, trial_ends_at: undefined }, { ...expected, trial_ends_at: undefined });
    const ends = Date.parse(r.json.me.trial_ends_at) - Date.now();
    assert.ok(ends > 7 * DAY - 5000 && ends <= 7 * DAY, "trial ends in 7 days");

    const raw = await env.SUBS.get("acct:new@example.com");
    const acct = JSON.parse(raw);
    assert.deepEqual(Object.keys(acct).sort(), ["claimed_at", "created_at", "email", "email_verified", "google_sub", "hash",
                                                "iterations", "salt", "scheme", "stripe_customer", "trial_ends_at"]);
    assert.deepEqual([acct.google_sub, acct.email_verified, acct.stripe_customer, acct.claimed_at], [null, false, null, null]);
    assert.deepEqual([acct.scheme, acct.iterations], [2, 20000]);
    assert.match(acct.salt, /^[A-Za-z0-9_-]{22}$/, "16 random bytes, base64url");
    assert.match(acct.hash, /^[A-Za-z0-9_-]{43}$/, "32-byte PBKDF2 output, base64url");
    assert.ok(!raw.includes(PASSWORD), "the password itself is never stored");
    assert.equal([...env.SUBS.m.keys()].filter((k) => k.startsWith("sub:")).length, 0, "no newsletter record");
    assert.equal(env.SUBS.writes, 1, "one KV write: the account");
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
    assert.equal(r.json.me.reason, "trial");
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

  test("without SESSION_SECRET sign-in is 503 and nothing is written", async () => {
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
    assert.equal(env.SUBS.m.size, 0);
    assert.equal(env.SUBS.writes, 0);
    // The read-only routes keep their usual answers.
    assert.equal((await me(env, "x.y")).status, 401);
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
    assert.deepEqual([r.json.me.reason, r.json.me.email_verified], ["expired", false]);
    let acct = await account(env, "old@example.com");
    assert.deepEqual([acct.scheme, acct.iterations], [2, 20000]);
    assert.notEqual(acct.salt, legacy.salt, "fresh salt");
    assert.equal(acct.hash, b64(pbkdf2Sync(peppered(PASSWORD), Buffer.from(acct.salt, "base64url"), 20000, 32, "sha256")));
    assert.equal(acct.trial_ends_at, legacy.trial_ends_at);
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
    assert.equal(claims.email, "tok@example.com");
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
    assert.equal(stripe.calls, 0, "an unverified address is never looked up");
    travel(8 * DAY);
    let r = await me(env, token);
    assert.deepEqual([r.status, r.json.member, r.json.reason, r.json.email_verified], [200, false, "expired", false]);
    assert.equal((await call(env, "GET", "/api/deals", { token })).status, 402);
    assert.equal(stripe.calls, 0);
    // The real owner signs in with Google: the squatted account is theirs now.
    r = await google(env, idToken({ email: "paying@example.com", sub: "owner" }));
    assert.deepEqual([r.status, r.json.me.member, r.json.me.reason, r.json.me.email_verified], [200, true, "subscribed", true]);
    assert.ok(stripe.calls > 0);
    const acct = await account(env, "paying@example.com");
    assert.deepEqual([acct.google_sub, acct.email_verified, acct.scheme, acct.iterations, acct.salt, acct.hash],
                     ["owner", true, null, null, null, null]);
    r = await login(env, "paying@example.com");
    assert.deepEqual([r.status, r.json], [401, { error: "bad_credentials" }], "the squatter's password is gone");
    assert.equal((await me(env, token)).json.reason, "subscribed", "the old session now belongs to the verified owner");
  });

  test("Google takeover keeps the trial clock; a verified password account is only linked", async () => {
    const env = newEnv();
    const first = (await signup(env, "p@example.com")).json.me.trial_ends_at;
    travel(DAY);
    let r = await google(env, idToken({ email: "p@example.com", sub: "g-p" }));
    assert.equal(r.json.me.trial_ends_at, first, "no new trial");
    assert.equal((await account(env, "p@example.com")).hash, null);
    // Verified through a claim whose payer email matches: Google links, the password stays.
    const { json: { token } } = await signup(env, "buyer@example.com");
    stripe.sessions.cs_test_ok123456 = COMPLETE;
    stripe.subs.cus_9 = ["active"];
    assert.equal((await claim(env, token, "cs_test_ok123456")).json.me.email_verified, true);
    r = await google(env, idToken({ email: "buyer@example.com", sub: "g-b" }));
    assert.equal(r.status, 200);
    const acct = await account(env, "buyer@example.com");
    assert.equal(acct.google_sub, "g-b");
    assert.ok(acct.hash);
    assert.equal((await login(env, "buyer@example.com")).status, 200);
    assert.equal(acct.stripe_customer, "cus_9");
  });

  test("trial, then expired, then subscribed through Stripe by (verified) email", async () => {
    const env = newEnv();
    const token = await googleAccount(env, "m@example.com");
    let r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason], [true, "trial"]);
    travel(8 * DAY);
    r = await me(env, token);
    assert.deepEqual([r.status, r.json.member, r.json.reason], [200, false, "expired"]);
    assert.equal(r.json.subscribe_url, `${PAYMENT_LINK}?prefilled_email=m%40example.com`);
    subscribed("m@example.com", ["canceled", "active"]);
    travel(6 * MIN); // past the cached "no"
    r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason], [true, "subscribed"]);
    // Trialing counts, canceled alone does not; a second customer is checked too.
    stripe.customers["m@example.com"] = ["cus_old", "cus_1"];
    stripe.subs = { cus_old: ["canceled"], cus_1: ["trialing"] };
    travel(6 * MIN);
    assert.equal((await me(env, token)).json.reason, "subscribed");
    stripe.subs = { cus_old: ["canceled"], cus_1: ["past_due"] };
    travel(6 * MIN);
    assert.equal((await me(env, token)).json.reason, "expired");
    assert.ok(stripe.urls.every((u) => !u.startsWith("/v1/customers")), "never the exact-case customers?email= filter");
  });

  test("the email lookup is case-insensitive and pages through Stripe's list", async () => {
    const env = newEnv();
    const token = await googleAccount(env, "Late@Example.com");
    travel(8 * DAY);
    stripe.pageSize = 2;
    for (let i = 0; i < 5; i++) subscribed(`other${i}@example.com`, ["active"], `cus_o${i}`);
    subscribed("LATE@example.COM", ["trialing"], "cus_late");
    let r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason], [true, "subscribed"]);
    const urls = stripe.urls.join("\n");
    assert.match(urls, /status=active&limit=100&expand\[\]=data\.customer/);
    assert.match(urls, /starting_after=sub_cus_o1_0/);
    assert.match(urls, /status=trialing&limit=100/);
    assert.ok(stripe.calls >= 4, `active: 3 pages, trialing: 1 (${stripe.calls})`);
    stripe.calls = 0;
    travel(6 * MIN);
    delete stripe.customers["LATE@example.COM"];
    assert.equal((await me(env, token)).json.reason, "expired");
  });

  test("Stripe's answer is cached 5 minutes in memory; ?fresh=1 re-asks once a minute", async () => {
    const env = newEnv();
    const token = await googleAccount(env, "c@example.com");
    travel(8 * DAY);
    subscribed("c@example.com");
    assert.equal((await me(env, token)).json.reason, "subscribed");
    assert.equal(memGet("member:c@example.com"), true);
    assert.ok(![...env.SUBS.m.keys()].some((k) => k.startsWith("member:")), "never a KV write");
    const calls = stripe.calls;
    stripe.subs.cus_1 = ["canceled"];
    assert.equal((await me(env, token)).json.reason, "subscribed", "served from the cache");
    assert.equal(stripe.calls, calls);
    assert.equal((await me(env, token, "?fresh=1")).json.reason, "expired", "fresh re-asks Stripe");
    assert.ok(stripe.calls > calls);
    const calls2 = stripe.calls;
    stripe.subs.cus_1 = ["active"];
    assert.equal((await me(env, token, "?fresh=1")).json.reason, "expired", "second fresh within a minute: cache");
    assert.equal(stripe.calls, calls2);
    travel(61 * 1000);
    assert.equal((await me(env, token, "?fresh=1")).json.reason, "subscribed");
    stripe.subs.cus_1 = ["canceled"];
    travel(4 * MIN);
    assert.equal((await me(env, token)).json.reason, "subscribed", "still inside the 5 minutes");
    travel(2 * MIN);
    assert.equal((await me(env, token)).json.reason, "expired", "cache expired");
  });

  test("a Stripe failure means not subscribed for this request and is not cached", async () => {
    const env = newEnv();
    subscribed("f@example.com");
    stripe.fail = true;
    const token = await googleAccount(env, "f@example.com");
    let r = await me(env, token);
    assert.deepEqual([r.status, r.json.member, r.json.reason], [200, true, "trial"], "the trial still counts");
    assert.equal(memGet("member:f@example.com"), undefined);
    travel(8 * DAY);
    r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason], [false, "expired"]);
    stripe.fail = false;
    assert.equal((await me(env, token)).json.reason, "subscribed", "the next request asks again");
    // No Stripe key at all: trials only.
    const bare = newEnv({ STRIPE_SECRET_KEY: "", STRIPE_PAYMENT_LINK: "", STRIPE_PORTAL_LINK: "" });
    subscribed("bare@example.com");
    r = await google(bare, idToken({ email: "bare@example.com" }));
    assert.deepEqual([r.json.me.reason, r.json.me.subscribe_url, r.json.me.portal_url], ["trial", "", ""]);
  });

  test("TRIAL_DAYS sets the trial length", async () => {
    const env = newEnv({ TRIAL_DAYS: "0" });
    const r = await signup(env, "none@example.com");
    assert.deepEqual([r.json.me.member, r.json.me.reason], [false, "expired"]);
    const r2 = await signup(newEnv({ TRIAL_DAYS: "bogus" }), "d@example.com");
    assert.ok(Date.parse(r2.json.me.trial_ends_at) - Date.now() > 6 * DAY, "default 7");
  });
});

describe("claim", () => {
  test("binds the Checkout Session's customer to the account; a matching payer email verifies it", async () => {
    const env = newEnv();
    const { json: { token } } = await signup(env, "buyer@example.com");
    travel(8 * DAY);
    assert.equal((await me(env, token)).json.reason, "expired");
    stripe.sessions.cs_test_a1b2c3 = COMPLETE;
    stripe.subs.cus_9 = ["active"];
    let r = await claim(env, token, "cs_test_a1b2c3");
    assert.equal(r.status, 200);
    assert.deepEqual(Object.keys(r.json), ["me"]);
    assert.deepEqual([r.json.me.member, r.json.me.reason, r.json.me.claimed, r.json.me.email_verified], [true, "subscribed", true, true]);
    assert.match(stripe.urls[0], /^\/v1\/checkout\/sessions\/cs_test_a1b2c3$/);
    assert.match(stripe.urls[1], /^\/v1\/subscriptions\?customer=cus_9&status=all/);
    const acct = await account(env, "buyer@example.com");
    assert.equal(acct.stripe_customer, "cus_9");
    assert.ok(Date.now() - Date.parse(acct.claimed_at) < 5000);
    assert.equal((await call(env, "GET", "/api/deals", { token })).status, 404, "member (nothing published yet)");
    // The bound customer is what counts: cancel it and membership ends.
    stripe.subs.cus_9 = ["canceled"];
    travel(6 * MIN);
    assert.equal((await me(env, token)).json.reason, "expired");
    // ...unless the (now verified) email has another live subscription.
    subscribed("buyer@example.com", ["active"], "cus_other");
    travel(6 * MIN);
    assert.equal((await me(env, token)).json.reason, "subscribed");
  });

  test("a different payer email still binds (the payer holds the receipt) but does not verify", async () => {
    const env = newEnv();
    const { json: { token } } = await signup(env, "typed@example.com");
    travel(8 * DAY);
    stripe.sessions.cs_live_x9y8z7w6 = { ...COMPLETE, customer: { id: "cus_obj", object: "customer" },
                                   customer_details: { email: "someone.else@example.com" } };
    stripe.subs.cus_obj = ["trialing"];
    const r = await claim(env, token, "cs_live_x9y8z7w6");
    assert.deepEqual([r.status, r.json.me.reason, r.json.me.claimed, r.json.me.email_verified], [200, "subscribed", true, false]);
    assert.equal((await account(env, "typed@example.com")).stripe_customer, "cus_obj");
  });

  test("bad, unknown and unfinished sessions bind nothing", async () => {
    const env = newEnv();
    const { json: { token: real } } = await signup(env, "b@example.com");
    const before = env.SUBS.writes;
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
    assert.deepEqual([acct.stripe_customer, acct.claimed_at, acct.email_verified], [null, null, false]);
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
    r = await call(env, "GET", "/api/deals", { token });
    assert.deepEqual([r.status, r.json], [404, { error: "no_payload" }], "member, nothing published yet");
    await call(env, "POST", "/api/publish", { body: PAYLOAD, token: "s3cret" });
    r = await call(env, "GET", "/api/deals", { token });
    assert.equal(r.status, 200);
    assert.equal(r.text, PAYLOAD);
    assert.equal(r.headers.get("Content-Type"), "application/json; charset=utf-8");
    assert.equal(r.headers.get("Access-Control-Allow-Origin"), ORIGIN);
    travel(8 * DAY);
    r = await call(env, "GET", "/api/deals", { token });
    assert.equal(r.status, 402);
    assert.deepEqual([r.json.email, r.json.member, r.json.reason], ["d@example.com", false, "expired"]);
    assert.ok(!r.text.includes("Widget"), "no deal leaks to a non-member");
    subscribed("d@example.com");
    travel(6 * MIN);
    r = await call(env, "GET", "/api/deals", { token });
    assert.deepEqual([r.status, r.text], [200, PAYLOAD]);
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
    assert.deepEqual([r.json.me.email, r.json.me.member, r.json.me.reason, r.json.me.email_verified],
                     ["g.user@example.com", true, "trial", true]);
    const acct = await account(env, "g.user@example.com");
    assert.deepEqual([acct.google_sub, acct.scheme, acct.salt, acct.hash, acct.email_verified], ["g-sub-1", null, null, null, true]);
    assert.equal((await me(env, r.json.token)).status, 200);
    const fetched = jwks.calls;
    const writes = env.SUBS.writes;
    travel(30 * MIN);
    r = await google(env, idToken());
    assert.equal(r.status, 200);
    assert.equal(r.json.me.trial_ends_at, acct.trial_ends_at, "no new trial on the next sign-in");
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
    assert.equal((await me(env, await signSession(env, "oldp@example.com"))).json.reason, "expired");
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
    for (const [method, path] of [["GET", "/api/nope"], ["GET", "/api/signup"], ["GET", "/api/claim"], ["DELETE", "/api/me"], ["PUT", "/api/publish"]]) {
      const r = await call(env, method, path);
      assert.deepEqual([r.status, r.json], [404, { error: "not_found" }], path);
    }
  });
});
