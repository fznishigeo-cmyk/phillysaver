"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const ROOT = path.resolve(__dirname, "../extension");
const rules = JSON.parse(fs.readFileSync(path.join(ROOT, "rules.json")));
const WP = require(path.join(ROOT, "lookup.js"));
const url = "https://www.bestbuy.com/site/widget/6522738.p?skuId=6522738";
const alias = "bestbuy:6522738";
const epoch = Date.parse("2026-10-16T12:00:00Z");
const entry = {kind:"live", store:"bestbuy", item:"Example widget", price:77, at:"2026-10-16T11:48:00Z", verdict:"Lower than usual — $77 is 23% below its usual $100", p_path:"/p/widget-123/", deal_path:"/deal/widget-456/"};
const tick = () => new Promise(resolve => setImmediate(resolve));
class Element {
  constructor(tag) {this.tag = tag; this.children = []; this.attrs = {}; this.listeners = {}; this.style = {}; this.textContent = ""; this.hidden = false; this.checked = false; this.value = "";}
  appendChild(el) {this.children.push(el); el.parent = this; return el;}
  setAttribute(key, value) {this.attrs[key] = value;}
  addEventListener(type, action) {this.listeners[type] = action;}
  attachShadow(options) {assert.equal(options.mode, "closed"); this.closedRoot = new Element("shadow"); return this.closedRoot;}
  remove() {if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this);}
  replaceChildren() {this.children = [];}
  async click() {if (this.listeners.click) await this.listeners.click({preventDefault(){}});}
}
function all(el) {return [el, ...el.children.flatMap(all), ...(el.closedRoot ? all(el.closedRoot) : [])];}
function documentStub() {
  const root = new Element("html"), nodes = {};
  const doc = {documentElement:root, createElement:tag => new Element(tag), createElementNS:(_, tag) => new Element(tag), getElementById:id => nodes[id] ||= new Element(id)};
  for (const property of ["cookie", "body", "links", "querySelector", "querySelectorAll"]) Object.defineProperty(doc, property, {get(){throw Error("Host page was read: " + property);}});
  return doc;
}
function compact(rows, generated_at = new Date(epoch).toISOString()) {
  const fields = [...new Set(Object.values(rows).flatMap(Object.keys))].sort();
  const strings = [...new Set(Object.values(rows).flatMap(row => Object.values(row).map(v => JSON.stringify(v))))].sort();
  const ids = new Map(strings.map((s, i) => [s, i]));
  let previous = "";
  const values = strings.map(s => {
    let n = 0; while (n < previous.length && n < s.length && previous[n] === s[n]) n++;
    const pair = [n, s.slice(n)]; previous = s; return pair;
  });
  return {version:1, generated_at, fields, values, entries:Object.fromEntries(Object.entries(rows).map(([a, r]) =>
    [a, fields.map(f => Object.hasOwn(r, f) ? ids.get(JSON.stringify(r[f])) : -1)]))};
}
async function harness({enabled = true, rows = {[alias]:entry}, href = url, failure = false, status = 200, initial = {}, localData = {}, localFull = false, noContentScript = false, install = true, rawIndex = null} = {}) {
  let now = epoch, handler, inFlight = 0, interval = () => {}, alarm = null, online = !failure;
  const events = {}, timers = new Map(); let timerID = 0;
  const requests = [], changes = [], sync = {...initial}, local = localData;
  if (enabled !== null) sync.enabled = enabled;
  const runtime = {id:"test-extension", getURL:name => "chrome-extension://test/" + name,
    onMessage:{addListener:fn => {handler = fn;}}, onInstalled:{addListener:fn => {events.installed = fn;}}, onStartup:{addListener:fn => {events.startup = fn;}}};
  const storage = {onChanged:{addListener:fn => changes.push(fn)}};
  for (const [area, data] of [["sync", sync], ["local", local]]) storage[area] = {
    get(keys, callback) {if (keys === null) return callback({...data}); const out = typeof keys === "string" ? {[keys]:data[keys]} : {...keys}; if (typeof keys !== "string") for (const key of Object.keys(keys)) if (key in data) out[key] = data[key]; callback(out);},
    set(values, callback) {if (area === "local" && localFull) {runtime.lastError = {message:"quota"}; callback(); delete runtime.lastError; return;} const diff = {}; for (const [key, value] of Object.entries(values)) {diff[key] = {oldValue:data[key],newValue:value}; data[key] = value;} callback(); changes.forEach(fn => fn(diff, area));}
  };
  const chrome = {runtime, storage, alarms:{
    get(name, cb) {cb(alarm && alarm.name === name ? alarm : undefined);},
    create(name, options) {assert.equal(name, "price-index"); assert.equal(options.periodInMinutes, 360); alarm = {name,...options};},
    onAlarm:{addListener:fn => {events.alarm = fn;}}
  }, tabs:{query(_options, cb){cb([{id:1}]);}, sendMessage(_id, _message, cb){if (noContentScript) {runtime.lastError = {message:"No content script"}; cb(); delete runtime.lastError; return;} cb({site:new URL(href).hostname});}}};
  const context = vm.createContext({chrome, URL, URLSearchParams, TextEncoder, Intl, AbortSignal, console, crypto:globalThis.crypto,
    Date:class extends Date {static now(){return now;}}, addEventListener(type, fn){events[type] = fn;},
    setTimeout(fn, ms){const id = ++timerID; timers.set(id,{fn,at:now+ms}); return id;}, clearTimeout(id){timers.delete(id);},
    setInterval(fn, ms){assert.equal(ms, 1000); interval = fn; return 0;}});
  context.fetch = async (address, options) => {
    if (address === runtime.getURL("rules.json")) return {json:async () => rules};
    assert.equal(address, "https://wowpriced.com/check/data/all.json");
    assert.equal(options.credentials, "omit"); assert.equal(options.referrerPolicy, "no-referrer"); assert.equal(options.redirect, "error");
    assert.equal(options.cache, "no-store");
    requests.push(address);
    if (!online) throw Error("offline");
    return {ok:status === 200, text:async () => rawIndex ?? (typeof rows === "string" ? rows : JSON.stringify(compact(rows, new Date(now).toISOString())))};
  };
  function run(file) {vm.runInContext(fs.readFileSync(path.join(ROOT, file), "utf8"), context, {filename:file});}
  run("lookup.js"); run("background.js");
  const realLookup = context.Wowpriced.lookup;
  context.Wowpriced.lookup = async (...args) => {
    inFlight++;
    try {return await realLookup(...args);} finally {inFlight--;}
  };
  const background = handler;
  runtime.sendMessage = (message, callback) => {
    inFlight++;
    background(message, {id:runtime.id}, value => {inFlight--;callback(value);});
  };
  const doc = documentStub(); context.document = doc; context.location = {href};
  doc.getElementById("site-row").hidden = true;
  run("card.js");
  async function settle() {
    await tick();
    const pending = vm.runInContext("pending", context); if (pending) await pending.catch(() => {});
    for (let i = 0; i < 200 && inFlight; i++) await new Promise(resolve => setTimeout(resolve, 2));
    assert.equal(inFlight,0,"all background replies completed");
    await tick();
  }
  if (install) events.installed();
  await settle();
  return {context, doc, requests, sync, local, run, settle,
    now:val => {now = val; for (const [id, timer] of timers) if (timer.at <= now) {timers.delete(id); timer.fn();}},
    online:val => {online = val;}, alarm:async (name = "price-index") => {events.alarm({name}); await settle();},
    startup:async () => {events.startup(); await settle();}, alarmSettings:() => alarm,
    tickURL:async (next, popstate = false) => {context.location.href = next; if (popstate) events.popstate(); else interval(); await settle();},
    set:async values => {await context.Wowpriced.api(storage.sync, "set", values); await settle();},
    lookup:async (key = alias, site = "") => context.Wowpriced.lookup(key, rules, site),
    nodes:() => all(doc.documentElement), background};
}
test("URL fixture parity is shared by content and popup; short links do not resolve", async () => {
  const fixtures = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures/checker_aliases.json")));
  for (const row of fixtures) assert.deepEqual(WP.aliases(row.url, rules), row.aliases, row.url);
  assert.deepEqual(WP.aliases("https://bestbuy.com/site/x.p?skuId=&skuId=123", rules), ["bestbuy:123"]);
});
test("enabled after install without a saved setting; popup agrees", async () => {
  const h = await harness({enabled:null}); h.run("content.js"); await h.settle();
  assert(h.nodes().some(n => n.textContent === entry.verdict));
  assert.equal((await h.lookup()).verdict, entry.verdict);
  h.run("popup.js"); await h.settle();
  assert.equal(h.doc.getElementById("enabled").checked, true);
  h.doc.getElementById("product-url").value = url;
  await h.doc.getElementById("check-form").listeners.submit({preventDefault(){}});
  assert(all(h.doc.getElementById("result")).some(n => n.textContent === entry.verdict));
});
test("explicit global off makes no lookup requests or card", async () => {
  const h = await harness({enabled:false}); h.run("content.js"); await h.settle();
  assert.equal(h.requests.length, 1); assert.equal(h.doc.documentElement.children.length, 0);
  assert.equal(await h.lookup(), null);
});
test("live content writes a closed card with exact verdict, checked time and safe history URL", async () => {
  const h = await harness();
  const originalLink = new Element("a");
  originalLink.setAttribute("href", "https://retailer.example/?tag=original");
  originalLink.textContent = "Buy";
  h.doc.documentElement.appendChild(originalLink);
  const originalLinks = JSON.stringify(originalLink.attrs);
  // Host fields throw if read. The content may create and append its own nodes only.
  h.run("content.js"); await h.settle();
  assert.equal(h.doc.documentElement.children.length, 2);
  const nodes = h.nodes();
  assert(nodes.some(n => n.textContent === entry.verdict));
  assert(nodes.some(n => n.textContent === "$77.00"));
  assert(nodes.some(n => n.textContent === "checked 12 min ago" && n.title === "2026-10-16T11:48:00.000Z"));
  const link = nodes.find(n => n.tag === "a" && n !== originalLink);
  assert.equal(link.href, "https://wowpriced.com/p/widget-123/?ref=ext"); assert.equal(link.target, "_blank"); assert.equal(link.rel, "noopener noreferrer");
  assert.equal(JSON.stringify(originalLink.attrs), originalLinks);
  assert.equal(originalLink.textContent, "Buy");
  assert.equal(h.requests.length, 1);
});
test("dismiss removes the card and it stays dismissed on this page", async () => {
  const h = await harness(); h.run("content.js"); await h.settle();
  await h.nodes().find(n => n.attrs["aria-label"] === "Dismiss price check").click();
  await h.set({enabled:false}); await h.set({enabled:true});
  assert.equal(h.doc.documentElement.children.length, 0);
});
test("URL changes replace the product card, clear misses and reset dismissal", async () => {
  const h = await harness({rows:{[alias]:entry, "bestbuy:1234567":{...entry,verdict:"OTHER"}}});
  h.run("content.js"); await h.settle();
  await h.tickURL(url); assert.equal(h.requests.length, 1, "unchanged URLs do not refresh");
  await h.tickURL("https://www.bestbuy.com/site/other/1234567.p?skuId=1234567");
  assert(h.nodes().some(n => n.textContent === "OTHER"));
  assert(!h.nodes().some(n => n.textContent === entry.verdict));
  await h.nodes().find(n => n.attrs["aria-label"] === "Dismiss price check").click();
  await h.tickURL(url, true);
  assert(h.nodes().some(n => n.textContent === entry.verdict));
  await h.tickURL("https://www.bestbuy.com/site/searchpage.jsp?st=x");
  assert.equal(h.doc.documentElement.children.length, 0);
});
test("navigation invalidates an outstanding product lookup", async () => {
  const h = await harness(); let resolve;
  h.context.Wowpriced.lookup = () => new Promise(r => {resolve = r;});
  h.run("content.js"); await h.settle();
  await h.tickURL("https://www.bestbuy.com/site/searchpage.jsp?st=x");
  resolve(WP.presentation(entry, alias, rules)); await h.settle();
  assert.equal(h.doc.documentElement.children.length, 0);
});
test("site hide persists; popup unhide restores it; global off removes it", async () => {
  const h = await harness(); h.run("content.js"); await h.settle();
  await h.nodes().find(n => n.textContent === "Hide on this site").click(); await h.settle();
  assert.equal(h.sync.hiddenSites["www.bestbuy.com"], true); assert.equal(h.doc.documentElement.children.length, 0);
  assert.equal(await h.lookup(alias, "www.bestbuy.com"), null);
  await h.set({hiddenSites:{}}); assert.equal(h.doc.documentElement.children.length, 1);
  await h.set({enabled:false}); assert.equal(h.doc.documentElement.children.length, 0);
});
test("unknown and unavailable entries show nothing", async () => {
  for (const options of [{rows:{}}, {failure:true}, {status:404}, {rows:"[]"}, {rows:"{"}, {rows:" ".repeat(1500000)}]) {
    const h = await harness(options); h.run("content.js"); await h.settle(); assert.equal(h.doc.documentElement.children.length, 0);
  }
  for (const href of ["https://walmart.com/ip/not-a-product", "https://ebay.com/itm/123?var=456", "https://amazon.com/dp/B0123456789"]) {
    const h = await harness({href}); h.run("content.js"); await h.settle();
    assert.equal(h.requests.length,1,"unsupported product identities do not trigger downloads");
    assert.equal(h.doc.documentElement.children.length,0);
  }
});
test("Amazon: full mode shows verdict and checked price; link-only entries and link mode show item plus button only", async () => {
  assert.equal(rules.AMAZON_MODE, "full");
  const key = "amazon:B012345678", rich = {...entry,store:"amazon"};
  let h = await harness({href:"https://amazon.com/dp/B012345678", rows:{[key]:rich}});
  h.run("content.js"); await h.settle();
  assert(h.nodes().some(n => /\$77\.00/.test(n.textContent))); assert(h.nodes().some(n => /^checked /.test(n.textContent)));
  const linkOnly = {kind:"live",store:"amazon",item:"Example widget",deal_path:"/deal/example/"};
  h = await harness({href:"https://amazon.com/dp/B012345678", rows:{[key]:linkOnly}});
  h.run("content.js"); await h.settle();
  const nodes = h.nodes(); assert(nodes.some(n => n.textContent === "Example widget")); assert(nodes.some(n => n.tag === "a"));
  assert(!nodes.some(n => /\$|checked |Lower than/.test(n.textContent)));
  const model = WP.presentation(rich, key, {...rules, AMAZON_MODE:"link"});
  assert.equal(model.detail, ""); assert.equal(model.verdict, ""); assert.equal(model.at, "");
});
test("live-only stores cannot expose history verdicts; ended facts are clearly labeled", () => {
  for (const store of ["ebay"]) {
    assert.equal(WP.presentation({...entry,store}, store + ":123", rules).verdict, "");
    assert.equal(WP.presentation({...entry,store,kind:"ended"},store + ":123",rules), null);
  }
  // Walmart allowed history, lowest price and alerts (operator call, 2026-10-16).
  assert.equal(WP.presentation({...entry,store:"walmart"}, "walmart:123", rules).verdict, entry.verdict);
  assert.equal(WP.presentation({...entry,store:"walmart",kind:"ended"}, "walmart:123", rules).detail, "Last verified price $77.00 · deal ended");
  const model = WP.presentation({...entry,kind:"ended",p_path:""}, alias, rules);
  assert.equal(model.detail, "Last verified price $77.00 · deal ended"); assert.equal(model.href, "https://wowpriced.com/deal/widget-456/?ref=ext");
});
test("unsafe paths never become links; untrusted text remains literal text", async () => {
  for (const p_path of ["//evil.example/p/x", "https://evil.example", "/p/x?redirect=evil", "/p/../x", "/p/%2f%2fevil", "javascript:alert(1)"]) assert.equal(WP.presentation({...entry,p_path}, alias, rules), null);
  const h = await harness({rows:{[alias]:{...entry,item:'<img src="https://evil.example/pixel">',verdict:"<script>bad()</script>"}}});
  h.run("content.js"); await h.settle(); assert(h.nodes().some(n => n.textContent === "<script>bad()</script>")); assert(!h.nodes().some(n => n.tag === "img"));
});
test("product visits and concurrent lookups make zero network requests after installation", async () => {
  const h = await harness(); assert.equal(h.requests.length, 1);
  assert.equal(h.alarmSettings().periodInMinutes, 360);
  h.requests.length = 0;
  h.run("content.js"); await h.settle();
  const results = await Promise.all([h.lookup(),h.lookup(),h.lookup("bestbuy:missing")]);
  assert.equal(results[0].verdict, entry.verdict); assert.equal(results[2], null);
  assert.equal(h.requests.length, 0);
  assert.deepEqual(Object.keys(h.local), ["priceIndex"]);
  assert.equal(h.local.priceIndex.data.version, 1);
  assert.deepEqual(Object.keys(h.sync).sort(), ["enabled"]);
});
test("six-hour alarm refreshes the whole index; fresh local cache survives worker restart offline", async () => {
  const h = await harness();
  h.now(epoch + 6*60*60000); await h.alarm("unrelated"); assert.equal(h.requests.length, 1);
  await h.alarm(); assert.equal(h.requests.length, 2);
  assert.equal(h.local.priceIndex.downloaded_at, epoch + 6*60*60000);
  const saved = JSON.parse(JSON.stringify(h.local));
  // Start the restarted worker at its persisted timestamp, with a fresh cache.
  const restarted = await harness({localData:saved, install:false, failure:true});
  restarted.now(epoch + 6*60*60000);
  assert.equal((await restarted.lookup()).verdict,entry.verdict);
  assert.equal(restarted.requests.length,0);
  await restarted.startup(); assert.equal(restarted.requests.length,1);
  assert.equal((await restarted.lookup()).verdict,entry.verdict);
});
test("missing or malformed local indexes stay invisible and never cause a page request", async () => {
  for (const localData of [{}, {priceIndex:{data:{version:9},downloaded_at:epoch}}]) {
    const h = await harness({localData,install:false});
    h.run("content.js"); await h.settle(); assert.equal(await h.lookup(),null);
    assert.equal(h.doc.documentElement.children.length,0); assert.equal(h.requests.length,0);
    await h.alarm(); assert.equal(h.requests.length,1);
    assert.equal((await h.lookup()).verdict,entry.verdict);
  }
});
test("an alarm updates an open product card locally and clears an old popup result", async () => {
  const rows = {[alias]:entry};
  const h = await harness({rows}); h.run("content.js"); h.run("popup.js"); await h.settle();
  h.doc.getElementById("product-url").value = url;
  await h.doc.getElementById("check-form").listeners.submit({preventDefault(){}});
  assert(h.nodes().some(n=>n.textContent===entry.verdict));
  assert(h.doc.getElementById("result").children.length);
  rows[alias] = {...entry,verdict:"Updated verified facts"}; await h.alarm();
  assert(h.nodes().some(n=>n.textContent==="Updated verified facts"));
  assert(!h.nodes().some(n=>n.textContent===entry.verdict));
  assert.equal(h.doc.getElementById("result").children.length,0);
  assert.equal(h.requests.length,2,"only installation and alarm download");
});
test("24-hour limit uses both publication and download clocks, even after a new download", async () => {
  const h = await harness(); h.run("content.js"); await h.settle();
  h.now(epoch + 24*60*60000 - 1); assert.equal((await h.lookup()).verdict,entry.verdict);
  h.now(epoch + 24*60*60000); assert.equal(await h.lookup(),null);
  assert.equal(h.doc.documentElement.children.length,0,"an open card expires too");
  h.now(epoch + 25*60*60000); h.run("content.js"); await h.settle();
  assert.equal(h.doc.documentElement.children.length,0); assert.equal(h.requests.length,1);
  await h.alarm(); assert.equal((await h.lookup()).verdict,entry.verdict);
  const old = await harness({rawIndex:JSON.stringify(compact({[alias]:entry}, new Date(epoch - 25*60*60000).toISOString()))});
  assert.equal(await old.lookup(),null,"recently downloaded old data is still stale");
  const future = await harness({rawIndex:JSON.stringify(compact({[alias]:entry}, new Date(epoch + 60000).toISOString()))});
  assert.equal(await future.lookup(),null);
});
test("failed refresh retains fresh cache; quota failure with no cache stays invisible", async () => {
  const h = await harness(); h.online(false); await h.alarm();
  assert.equal((await h.lookup()).verdict,entry.verdict);
  h.now(epoch + 25*60*60000); await h.alarm(); assert.equal(await h.lookup(),null);
  const full = await harness({localFull:true});
  assert.equal(await full.lookup(),null); assert.equal(Object.keys(full.local).length,0);
});
test("background rejects foreign and invalid lookup messages without a fetch", async () => {
  const h = await harness(); h.requests.length = 0;
  assert.equal(h.background({type:"rules"}, {id:"foreign"}, () => assert.fail()), false);
  assert.equal(await new Promise(resolve => h.background({type:"lookup",alias:42},{id:"test-extension"},resolve)), null);
  assert.equal(h.requests.length, 0);
});
test("malformed downloaded indexes are rejected without any shard fallback", async () => {
  const good = compact({[alias]:entry});
  const variants = [{...good,version:2}, {...good,generated_at:"bad"}, {...good,values:[[1,'"bad"']]},
    {...good,entries:{[alias]:[999]}}, {...good,fields:["item","item"]}];
  for (const value of variants) {
    const h = await harness({rawIndex:JSON.stringify(value)});
    assert.equal(await h.lookup(),null); assert.equal(h.requests.length,1);
  }
});
test("turning off while lookup is pending prevents a late card", async () => {
  const h = await harness(); let resolve;
  h.context.Wowpriced.lookup = () => new Promise(r => {resolve = r;});
  h.run("content.js"); await h.settle(); await h.set({enabled:false});
  resolve(WP.presentation(entry, alias, rules)); await h.settle(); assert.equal(h.doc.documentElement.children.length, 0);
});
test("popup uses the downloaded index offline, offers site/global switches, and stays quiet while off", async () => {
  const h = await harness({enabled:false}); h.online(false); h.run("popup.js"); await h.settle();
  const get = id => h.doc.getElementById(id);
  assert.equal(get("enabled").checked, false); assert.equal(get("site-row").hidden, false);
  get("product-url").value = url;
  await get("check-form").listeners.submit({preventDefault(){}}); assert.equal(h.requests.length, 1);
  get("enabled").checked = true; await get("enabled").listeners.change(); await h.settle();
  await get("check-form").listeners.submit({preventDefault(){}});
  assert.equal(h.requests.length, 1); assert(all(get("result")).some(n => n.textContent === entry.verdict));
  h.now(epoch + 24*60*60000); assert.equal(get("result").children.length,0,"open popup prices expire");
  get("hide").checked = true; await get("hide").listeners.change(); assert.equal(h.sync.hiddenSites["www.bestbuy.com"], true);
  get("product-url").value = "https://amzn.to/short";
  await get("check-form").listeners.submit({preventDefault(){}}); assert.equal(h.requests.length, 1); assert.match(get("status").textContent, /Open short links first/);
  const other = await harness({enabled:null, noContentScript:true, initial:{hiddenSites:{"www.bestbuy.com":true, "www.target.com":true, "www.ebay.com":false}}});
  other.run("popup.js"); await other.settle();
  const list = other.doc.getElementById("hidden-sites");
  assert.equal(other.doc.getElementById("site-row").hidden, true);
  assert.deepEqual(list.children.map(b => b.textContent), ["Unhide www.bestbuy.com", "Unhide www.target.com"]);
  await list.children[0].click(); await other.settle();
  assert.equal(other.sync.hiddenSites["www.bestbuy.com"], false);
  assert.equal(other.sync.hiddenSites["www.target.com"], true);
  assert.deepEqual(list.children.map(b => b.textContent), ["Unhide www.target.com"]);
});
