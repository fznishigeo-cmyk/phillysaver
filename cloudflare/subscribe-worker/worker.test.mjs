// Tests for the wowpriced Worker, offline: an in-memory KV, a fetch stub for
// Stripe and Google's JWKS, and a locally generated RS256 key pair standing
// in for Google. Run from the repository root:
//   node --test cloudflare/subscribe-worker/
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { generateKeyPairSync, sign as rsaSign } from "node:crypto";

import worker from "./worker.js";
import { signSession } from "./auth.js";

// ------------------------------------------------------------- fixtures

// Workers KV, enough of it: values + metadata, prefix listing with cursors.
// expirationTtl is ignored; the code checks its own timestamps.
class FakeKV {
  constructor() { this.m = new Map(); }
  async get(k) { const v = this.m.get(k); return v ? v.value : null; }
  async put(k, value, opts = {}) { this.m.set(k, { value, metadata: opts.metadata || null }); }
  async delete(k) { this.m.delete(k); }
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

function newEnv(over = {}) {
  return { SUBS: new FakeKV(), NEWSLETTER_SECRET: "s3cret", SESSION_SECRET: "session-secret-for-tests",
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

// Stripe stand-in: customers by email, subscription statuses by customer id.
const stripe = { customers: {}, subs: {}, fail: false, calls: 0 };
const jwks = { calls: 0 };
const jsonResponse = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

globalThis.fetch = async (input, init = {}) => {
  const url = new URL(typeof input === "string" ? input : input.url);
  if (url.href === "https://www.googleapis.com/oauth2/v3/certs") {
    jwks.calls += 1;
    return jsonResponse({ keys: [JWK] });
  }
  if (url.origin === "https://api.stripe.com") {
    stripe.calls += 1;
    assert.equal(init.headers.Authorization, `Basic ${btoa("sk_test_123:")}`);
    if (stripe.fail) return jsonResponse({ error: { message: "boom" } }, 500);
    if (url.pathname === "/v1/customers") {
      const ids = stripe.customers[url.searchParams.get("email")] || [];
      return jsonResponse({ data: ids.map((id) => ({ id, object: "customer" })) });
    }
    if (url.pathname === "/v1/subscriptions") {
      assert.equal(url.searchParams.get("status"), "all");
      return jsonResponse({ data: (stripe.subs[url.searchParams.get("customer")] || []).map((status) => ({ status })) });
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

afterEach(() => {
  offset = 0;
  stripe.customers = {};
  stripe.subs = {};
  stripe.fail = false;
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
const me = (env, token, query = "") => call(env, "GET", `/api/me${query}`, { token });

function subscribed(email, statuses = ["active"]) {
  stripe.customers[email] = ["cus_1"];
  stripe.subs.cus_1 = statuses;
}

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
      email: "new@example.com", member: true, reason: "trial",
      subscribe_url: `${PAYMENT_LINK}?prefilled_email=new%40example.com`,
      portal_url: "https://billing.stripe.com/p/login/test",
    };
    assert.deepEqual({ ...r.json.me, trial_ends_at: undefined }, { ...expected, trial_ends_at: undefined });
    const ends = Date.parse(r.json.me.trial_ends_at) - Date.now();
    assert.ok(ends > 7 * DAY - 5000 && ends <= 7 * DAY, "trial ends in 7 days");

    const raw = await env.SUBS.get("acct:new@example.com");
    const acct = JSON.parse(raw);
    assert.deepEqual(Object.keys(acct).sort(), ["created_at", "email", "google_sub", "hash", "salt", "trial_ends_at"]);
    assert.equal(acct.google_sub, null);
    assert.match(acct.salt, /^[A-Za-z0-9_-]{22}$/, "16 random bytes, base64url");
    assert.match(acct.hash, /^[A-Za-z0-9_-]{43}$/, "32-byte PBKDF2 output, base64url");
    assert.ok(!raw.includes(PASSWORD), "the password itself is never stored");
    assert.equal([...env.SUBS.m.keys()].filter((k) => k.startsWith("sub:")).length, 0, "no newsletter record");
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
  });

  test("a Google-only account has no password to log in with", async () => {
    const env = newEnv();
    assert.equal((await call(env, "POST", "/api/google", { body: { credential: idToken() } })).status, 200);
    const r = await login(env, "g.user@example.com", PASSWORD);
    assert.deepEqual([r.status, r.json], [401, { error: "bad_credentials" }]);
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

describe("membership", () => {
  test("trial, then expired, then subscribed through Stripe", async () => {
    const env = newEnv();
    const { json: { token, me: first } } = await signup(env, "m@example.com");
    assert.deepEqual([first.member, first.reason], [true, "trial"]);
    travel(8 * DAY);
    let r = await me(env, token);
    assert.deepEqual([r.status, r.json.member, r.json.reason], [200, false, "expired"]);
    assert.equal(r.json.subscribe_url, `${PAYMENT_LINK}?prefilled_email=m%40example.com`);
    subscribed("m@example.com", ["canceled", "active"]);
    travel(6 * 60 * 1000); // past the cached "no"
    r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason], [true, "subscribed"]);
    // Trialing counts, canceled alone does not; a second customer is checked too.
    stripe.customers["m@example.com"] = ["cus_old", "cus_1"];
    stripe.subs = { cus_old: ["canceled"], cus_1: ["trialing"] };
    travel(6 * 60 * 1000);
    assert.equal((await me(env, token)).json.reason, "subscribed");
    stripe.subs = { cus_old: ["canceled"], cus_1: ["past_due"] };
    travel(6 * 60 * 1000);
    assert.equal((await me(env, token)).json.reason, "expired");
  });

  test("Stripe's answer is cached 5 minutes; ?fresh=1 re-asks once a minute", async () => {
    const env = newEnv();
    const { json: { token } } = await signup(env, "c@example.com");
    travel(8 * DAY);
    subscribed("c@example.com");
    assert.equal((await me(env, token)).json.reason, "subscribed");
    const cached = JSON.parse(await env.SUBS.get("member:c@example.com"));
    assert.deepEqual(Object.keys(cached).sort(), ["checked_at", "member"]);
    assert.equal(cached.member, true);
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
    travel(4 * 60 * 1000);
    assert.equal((await me(env, token)).json.reason, "subscribed", "still inside the 5 minutes");
    travel(2 * 60 * 1000);
    assert.equal((await me(env, token)).json.reason, "expired", "cache expired");
  });

  test("a Stripe failure means not subscribed for this request and is not cached", async () => {
    const env = newEnv();
    subscribed("f@example.com");
    stripe.fail = true;
    const { json: { token } } = await signup(env, "f@example.com");
    let r = await me(env, token);
    assert.deepEqual([r.status, r.json.member, r.json.reason], [200, true, "trial"], "the trial still counts");
    assert.equal(await env.SUBS.get("member:f@example.com"), null);
    travel(8 * DAY);
    r = await me(env, token);
    assert.deepEqual([r.json.member, r.json.reason], [false, "expired"]);
    stripe.fail = false;
    assert.equal((await me(env, token)).json.reason, "subscribed", "the next request asks again");
    // No Stripe key at all: trials only.
    const bare = newEnv({ STRIPE_SECRET_KEY: "", STRIPE_PAYMENT_LINK: "", STRIPE_PORTAL_LINK: "" });
    r = await signup(bare, "f@example.com");
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
    const { json: { token } } = await signup(env, "d@example.com");
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
    travel(6 * 60 * 1000);
    r = await call(env, "GET", "/api/deals", { token });
    assert.deepEqual([r.status, r.text], [200, PAYLOAD]);
  });
});

describe("abuse control", () => {
  test("10 signups, 20 logins and 20 google posts per IP per hour", async () => {
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

    for (let i = 0; i < 20; i++) {
      assert.equal((await call(env, "POST", "/api/google", { body: { credential: "x" }, ip })).status, 401);
    }
    r = await call(env, "POST", "/api/google", { body: { credential: idToken() }, ip });
    assert.deepEqual([r.status, r.json], [429, { error: "rate_limited" }]);
    assert.ok(env.SUBS.m.has(`rl:signup:${ip}:${Math.floor(Date.now() / 3600000)}`));
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
    r = await call(env, "OPTIONS", "/api/me", { origin: "https://fznishigeo-cmyk.github.io" });
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
  const google = (env, credential, opts = {}) => call(env, "POST", "/api/google", { body: { credential }, ...opts });

  test("a valid ID token creates the account on first sign-in and reuses it after", async () => {
    const env = newEnv();
    let r = await google(env, idToken());
    assert.equal(r.status, 200);
    assert.equal(typeof r.json.token, "string");
    assert.deepEqual([r.json.me.email, r.json.me.member, r.json.me.reason], ["g.user@example.com", true, "trial"]);
    const acct = JSON.parse(await env.SUBS.get("acct:g.user@example.com"));
    assert.deepEqual([acct.google_sub, acct.salt, acct.hash], ["g-sub-1", null, null]);
    assert.equal((await me(env, r.json.token)).status, 200);
    const fetched = jwks.calls;
    travel(30 * 60 * 1000);
    r = await google(env, idToken());
    assert.equal(r.status, 200);
    assert.equal(r.json.me.trial_ends_at, acct.trial_ends_at, "no new trial on the next sign-in");
    assert.equal(jwks.calls, fetched, "JWKS served from the in-memory cache");
    travel(31 * 60 * 1000); // the hour is up: fetched again, and written to KV too
    assert.equal((await google(env, idToken())).status, 200);
    assert.equal(jwks.calls, fetched + 1);
    assert.ok(env.SUBS.m.has("jwks:google"));
  });

  test("Google sign-in links to an existing password account", async () => {
    const env = newEnv();
    const first = (await signup(env, "g.user@example.com")).json.me.trial_ends_at;
    const r = await google(env, idToken({ sub: "g-sub-2" }));
    assert.equal(r.status, 200);
    assert.equal(r.json.me.trial_ends_at, first);
    const acct = JSON.parse(await env.SUBS.get("acct:g.user@example.com"));
    assert.equal(acct.google_sub, "g-sub-2");
    assert.equal((await login(env, "g.user@example.com")).status, 200, "the password still works");
  });

  test("bad audience, issuer, expiry, signature, kid or email are 401 bad_token", async () => {
    const env = newEnv();
    const now = Math.floor(Date.now() / 1000);
    const bad = {
      aud: idToken({ aud: "other-client.apps.googleusercontent.com" }),
      iss: idToken({ iss: "https://accounts.example.com" }),
      exp: idToken({ exp: now - 1 }),
      signature: idToken({}, { key: strangerKey }),
      unverified: idToken({ email_verified: false }),
      no_email: idToken({ email: undefined }),
      alg: (() => { const [, p, s] = idToken().split("."); const h = Buffer.from(JSON.stringify({ alg: "HS256", kid: "kid-1" })).toString("base64url"); return `${h}.${p}.${s}`; })(),
      garbage: "not.a.token",
      empty: "",
      number: 42,
    };
    for (const [name, credential] of Object.entries(bad)) {
      const r = await google(env, credential);
      assert.deepEqual([r.status, r.json], [401, { error: "bad_token" }], name);
    }
    assert.deepEqual((await call(env, "POST", "/api/google", { body: {} })).status, 401);
    assert.equal(await env.SUBS.get("acct:g.user@example.com"), null, "no account from a bad token");
    const r = await google(newEnv({ GOOGLE_CLIENT_ID: "" }), idToken({ aud: "" }));
    assert.equal(r.status, 401, "Google sign-in is off without a client id");
  });

  test("an unknown kid refetches the JWKS once, then fails", async () => {
    const env = newEnv();
    const before = jwks.calls;
    const r = await google(env, idToken({}, { kid: "rotated" }));
    assert.deepEqual([r.status, r.json], [401, { error: "bad_token" }]);
    assert.equal(jwks.calls, before + 1);
    assert.equal((await google(env, idToken())).status, 200, "the real key still works");
  });
});

describe("newsletter routes (unchanged)", () => {
  test("subscribe, list and unsubscribe", async () => {
    const env = newEnv();
    let r = await call(env, "POST", "/subscribe", { body: { email: " You@Example.COM " } });
    assert.deepEqual([r.status, r.json, r.headers.get("Access-Control-Allow-Origin")], [200, { ok: true }, ORIGIN]);
    const first = JSON.parse(await env.SUBS.get("sub:you@example.com"));
    assert.match(first.token, /^[0-9a-f]{32}$/);
    assert.equal(env.SUBS.m.get("sub:you@example.com").metadata.token, first.token);
    r = await call(env, "POST", "/subscribe", { body: { email: "you@example.com" } });
    assert.equal(JSON.parse(await env.SUBS.get("sub:you@example.com")).token, first.token, "idempotent");
    r = await call(env, "POST", "/subscribe", { body: new URLSearchParams({ email: "Second@example.com" }),
      headers: { "Content-Type": "application/x-www-form-urlencoded", Referer: "https://www.wowpriced.com/" } });
    assert.deepEqual([r.status, r.headers.get("Location")], [303, "https://www.wowpriced.com/subscribe.html?ok=1"]);
    r = await call(env, "POST", "/subscribe", { body: new URLSearchParams({ email: "nope" }),
      headers: { "Content-Type": "application/x-www-form-urlencoded", Referer: "https://evil.example/" } });
    assert.deepEqual([r.status, r.headers.get("Location")], [303, "https://wowpriced.com/subscribe.html?err=email"]);
    r = await call(env, "POST", "/subscribe", { body: { email: "nope@" } });
    assert.deepEqual([r.status, r.json], [400, { ok: false, error: "invalid email" }]);

    assert.equal((await call(env, "GET", "/list")).status, 401);
    assert.equal((await call(env, "GET", "/list", { token: "wrong" })).status, 401);
    r = await call(env, "GET", "/list", { token: "s3cret" });
    assert.deepEqual(r.json, { emails: [{ email: "second@example.com", token: JSON.parse(await env.SUBS.get("sub:second@example.com")).token },
                                        { email: "you@example.com", token: first.token }] });

    r = await call(env, "GET", "/unsubscribe?e=you@example.com&t=badtoken");
    assert.equal(r.status, 403);
    r = await call(env, "GET", `/unsubscribe?e=You@example.com&t=${first.token}`);
    assert.equal(r.status, 200);
    assert.match(r.text, /You're unsubscribed/);
    assert.equal(await env.SUBS.get("sub:you@example.com"), null);
    r = await call(env, "POST", `/unsubscribe?e=you@example.com&t=${first.token}`, { body: "List-Unsubscribe=One-Click",
      headers: { "Content-Type": "application/x-www-form-urlencoded" } });
    assert.equal(r.status, 200);
  });

  test("subscribe keeps its own 20-per-hour counter", async () => {
    const env = newEnv();
    for (let i = 0; i < 20; i++) {
      assert.equal((await call(env, "POST", "/subscribe", { body: { email: `u${i}@example.com` }, ip: "7.7.7.7" })).status, 200);
    }
    const r = await call(env, "POST", "/subscribe", { body: { email: "u21@example.com" }, ip: "7.7.7.7" });
    assert.deepEqual([r.status, r.json], [429, { ok: false, error: "too many requests" }]);
    assert.equal((await signup(env, "still@example.com", PASSWORD, { ip: "7.7.7.7" })).status, 200, "separate buckets");
  });

  test("everything else is 404", async () => {
    const env = newEnv();
    for (const [method, path] of [["GET", "/"], ["GET", "/subscribe"], ["DELETE", "/list"], ["GET", "/api"]]) {
      const r = await call(env, method, path);
      assert.deepEqual([r.status, r.text], [404, "not found"], path);
    }
    for (const [method, path] of [["GET", "/api/nope"], ["GET", "/api/signup"], ["DELETE", "/api/me"], ["PUT", "/api/publish"]]) {
      const r = await call(env, method, path);
      assert.deepEqual([r.status, r.json], [404, { error: "not_found" }], path);
    }
  });
});
