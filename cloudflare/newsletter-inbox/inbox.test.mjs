import { test } from "node:test";
import assert from "node:assert/strict";
import { createInboxWorker, authorized, TTL_SECONDS } from "./inbox.js";

class FakeKV {
  constructor() { this.values = new Map(); this.listCalls = 0; this.deleted = []; }
  async put(key, value, options) { this.values.set(key, { value, ...options }); }
  async get(key) { return this.values.get(key)?.value || null; }
  async delete(key) { this.deleted.push(key); this.values.delete(key); }
  async list({ prefix, cursor, limit }) {
    this.listCalls++;
    const keys = [...this.values.keys()].filter(k => k.startsWith(prefix)).sort();
    const start = Number(cursor || 0);
    const page = keys.slice(start, start + limit).map(name => ({ name, metadata: this.values.get(name).metadata }));
    return { keys: page, list_complete: start + limit >= keys.length,
             cursor: String(start + limit) };
  }
}

function environment(extra = {}) {
  return { INBOX: new FakeKV(), NEWSLETTER_INBOX_TOKEN: "offline-inbox-token",
    NEWSLETTER_FORWARD_TO: "operator@example.com", ...extra };
}

function request(path = "/inbox", { token = "offline-inbox-token", method = "GET", body, headers = {} } = {}) {
  return new Request("https://inbox.example" + path, { method, headers: {
    ...(token === null ? {} : { Authorization: "Bearer " + token }), ...headers,
  }, ...(body === undefined ? {} : { body: typeof body === "string" ? body : JSON.stringify(body) }) });
}

const parsed = { from: { address: "Deals@Partner.Example" }, subject: "Today's deals",
  date: "2026-10-12T10:00:00.000Z", messageId: "<newsletter-1@partner.example>",
  text: "Widget was $100, now $20", html: "<p>Widget was $100, now $20</p>" };

function email(raw = "original MIME bytes", rawSize = new TextEncoder().encode(raw).length) {
  return { from: "bounce@mailer.example", rawSize, raw: new Response(raw).body,
    headers: new Headers({ Subject: "encoded subject", Date: "old date", "Message-ID": "<header-id>" }),
    forwarded: [], async forward(...args) { this.forwarded.push(args); } };
}

const worker = createInboxWorker(async () => parsed);

async function seed(env, number, extra = {}) {
  const id = "msg:" + number.toString(16).padStart(64, "0");
  const received_at = new Date().toISOString();
  const record = { id, received_at, ...parsed, from: parsed.from.address, ...extra };
  await env.INBOX.put(id, JSON.stringify(record), { expirationTtl: TTL_SECONDS,
    metadata: { received_at: record.received_at } });
  return id;
}

test("forward unchanged before parsing, store sender/header/body with seven-day TTL", async () => {
  const env = environment(), message = email();
  const handler = createInboxWorker(async raw => {
    assert.deepEqual(message.forwarded, [[env.NEWSLETTER_FORWARD_TO]]);
    assert.equal(new TextDecoder().decode(raw), "original MIME bytes");
    return parsed;
  });
  await handler.email(message, env);
  assert.equal(env.INBOX.values.size, 1);
  const [id, entry] = [...env.INBOX.values.entries()][0];
  assert.match(id, /^msg:[a-f0-9]{64}$/);
  assert.equal(entry.expirationTtl, 604800);
  const record = JSON.parse(entry.value);
  assert.equal(record.from, "deals@partner.example");
  assert.equal(record.envelope_from, "bounce@mailer.example");
  for (const key of ["subject", "date", "text", "html"]) assert.equal(record[key], parsed[key]);
  assert.equal(record.message_id, parsed.messageId);
  assert.equal(entry.metadata.received_at, record.received_at);
  assert.equal(record.attachments, undefined);
});

test("duplicate raw delivery overwrites one staging key but both copies reach human", async () => {
  const env = environment();
  const first = email(), second = email();
  await worker.email(first, env);
  await worker.email(second, env);
  assert.equal(env.INBOX.values.size, 1);
  assert.equal(first.forwarded.length + second.forwarded.length, 2);
});

test("missing/wrong/different-length bearer cannot list or delete; empty secret fails closed", async () => {
  const env = environment();
  const id = await seed(env, 1);
  for (const token of [null, "", "wrong", "offline-inbox-tokem", "offline-inbox-token-longer"]) {
    assert.equal(await authorized(request("/inbox", { token }), env), false);
    assert.equal((await worker.fetch(request("/inbox", { token }), env)).status, 401);
    assert.equal((await worker.fetch(request("/inbox/ack", { token, method: "POST", body: { ids: [id] } }), env)).status, 401);
  }
  assert.equal(env.INBOX.listCalls, 0);
  assert.equal(env.INBOX.deleted.length, 0);
  assert.equal(await authorized(request(), environment({ NEWSLETTER_INBOX_TOKEN: "" })), false);
  assert.equal(await authorized(request(), env), true);
});

test("GET since uses received time, hides expired staging and disables HTTP caching", async () => {
  const env = environment();
  const fresh = await seed(env, 1, { date: "1999-01-01T00:00:00Z" });
  await seed(env, 2, { received_at: new Date(Date.now() - 8 * 86400000).toISOString() });
  const since = new Date(Date.now() - 3600000).toISOString();
  const response = await worker.fetch(request("/inbox?since=" + encodeURIComponent(since)), env);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.deepEqual((await response.json()).messages.map(m => m.id), [fresh]);
  assert.equal((await worker.fetch(request("/inbox?since=oops"), env)).status, 400);
});

test("ack deletes only validated inbox ids and repeated acknowledgement is harmless", async () => {
  const env = environment();
  const first = await seed(env, 1), other = await seed(env, 2);
  for (let i = 0; i < 2; i++) {
    const response = await worker.fetch(request("/inbox/ack", { method: "POST", body: { ids: [first] } }), env);
    assert.deepEqual(await response.json(), { acknowledged: 1 });
  }
  assert.equal(await env.INBOX.get(first), null);
  assert.ok(await env.INBOX.get(other));
  const invalid = await worker.fetch(request("/inbox/ack", { method: "POST", body: { ids: [other, "someone-else"] } }), env);
  assert.equal(invalid.status, 400);
  assert.ok(await env.INBOX.get(other));
});

test("raw and parsed size caps forward oversized mail without staging or partial bodies", async () => {
  const env = environment({ MAX_RAW_BYTES: "8" });
  let parses = 0;
  const handler = createInboxWorker(async () => { parses++; return parsed; });
  const declared = email("123456789");
  await handler.email(declared, env);
  assert.equal(declared.forwarded.length, 1);
  assert.equal(parses, 0);
  const streamed = email("123456789", 1);
  await handler.email(streamed, env);
  assert.equal(streamed.forwarded.length, 1);
  assert.equal(parses, 0);
  const bodyEnv = environment({ MAX_MESSAGE_BYTES: "64" });
  const largeBody = email();
  await worker.email(largeBody, bodyEnv);
  assert.equal(largeBody.forwarded.length, 1);
  assert.equal(bodyEnv.INBOX.values.size, 0);
  assert.equal(env.INBOX.values.size, 0);
});

test("malformed MIME is still forwarded; missing destination fails before accepting mail", async () => {
  const env = environment();
  const handler = createInboxWorker(async () => { throw new Error("bad MIME"); });
  const message = email();
  await handler.email(message, env);
  assert.equal(message.forwarded.length, 1);
  assert.equal(env.INBOX.values.size, 0);
  await assert.rejects(worker.email(email(), environment({ NEWSLETTER_FORWARD_TO: "" })), /destination missing/);
});

test("response size determines a bounded page and cursor traverses every message", async () => {
  const env = environment({ MAX_MESSAGE_BYTES: "1024", MAX_RESPONSE_BYTES: "4096", PAGE_SIZE: "25" });
  const ids = [];
  for (let i = 1; i <= 5; i++) ids.push(await seed(env, i));
  let cursor = "", read = [];
  do {
    const response = await worker.fetch(request("/inbox?cursor=" + cursor), env);
    const text = await response.text();
    assert.ok(new TextEncoder().encode(text).byteLength <= 4096);
    const payload = JSON.parse(text);
    assert.ok(payload.messages.length <= 1);
    read.push(...payload.messages.map(m => m.id));
    cursor = payload.next_cursor;
  } while (cursor);
  assert.deepEqual(read, ids);
});

test("ack request byte/id caps are enforced before any delete", async () => {
  const env = environment();
  const id = await seed(env, 1);
  for (const options of [
    { body: { ids: [id] }, headers: { "Content-Length": "99999" } },
    { body: " ".repeat(17000) },
    { body: { ids: Array(101).fill(id) } },
    { body: "invalid json" },
  ]) {
    const response = await worker.fetch(request("/inbox/ack", { method: "POST", ...options }), env);
    assert.ok([400, 413].includes(response.status));
  }
  assert.equal(env.INBOX.deleted.length, 0);
});

test("wrong methods/routes and KV errors return safe, fixed errors", async () => {
  const env = environment();
  assert.equal((await worker.fetch(request("/inbox", { method: "POST" }), env)).status, 405);
  assert.equal((await worker.fetch(request("/inbox/ack"), env)).status, 405);
  assert.equal((await worker.fetch(request("/elsewhere"), env)).status, 404);
  env.INBOX.list = async () => { throw new Error("private body and token"); };
  const response = await worker.fetch(request(), env);
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "inbox_unavailable" });
});

test("forwarding and storage errors cannot expose private data in thrown errors", async () => {
  const env = environment();
  const failure = email();
  failure.forward = async () => { throw new Error("PRIVATE ADDRESS AND MESSAGE"); };
  await assert.rejects(worker.email(failure, env), { message: "newsletter inbox forwarding failed" });
  assert.equal(env.INBOX.values.size, 0);
  env.INBOX.put = async () => { throw new Error("PRIVATE ADDRESS AND MESSAGE"); };
  await assert.rejects(worker.email(email(), env), { message: "newsletter inbox storage failed" });
});
