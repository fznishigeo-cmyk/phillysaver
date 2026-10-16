"use strict";
// Chrome uses a service worker; Firefox 140+ uses the same file as an event page.
if (typeof importScripts === "function") importScripts("lookup.js");
const rulesReady = fetch(chrome.runtime.getURL("rules.json")).then(response => response.json());
const INDEX_KEY = "priceIndex", ALARM = "price-index", MAX_AGE = 24 * 60 * 60 * 1000;
let current = null, pending = null;

function unpack(record) {
  const data = record && record.data;
  if (!data || data.version !== 1 || !Number.isFinite(record.downloaded_at) ||
      !Number.isFinite(Date.parse(data.generated_at)) ||
      !Array.isArray(data.fields) || !data.fields.every(f => typeof f === "string") ||
      new Set(data.fields).size !== data.fields.length || !Array.isArray(data.values) ||
      !data.entries || Array.isArray(data.entries) || typeof data.entries !== "object") throw Error("invalid index");
  let previous = "";
  const values = data.values.map(pair => {
    if (!Array.isArray(pair) || pair.length !== 2 || !Number.isInteger(pair[0]) ||
        pair[0] < 0 || pair[0] > previous.length || typeof pair[1] !== "string") throw Error("invalid value");
    previous = previous.slice(0, pair[0]) + pair[1];
    return JSON.parse(previous);
  });
  for (const row of Object.values(data.entries)) {
    if (!Array.isArray(row) || row.length !== data.fields.length ||
        !row.every(id => Number.isInteger(id) && id >= -1 && id < values.length)) throw Error("invalid row");
  }
  return {data, values, downloaded_at: record.downloaded_at};
}
function expires(index) {
  return Math.min(index.downloaded_at, Date.parse(index.data.generated_at)) + MAX_AGE;
}
function fresh(index) {
  return index && index.downloaded_at <= Date.now() && Date.parse(index.data.generated_at) <= Date.now() && expires(index) > Date.now();
}
const indexReady = Wowpriced.api(chrome.storage.local, "get", INDEX_KEY).then(saved => {
  try { current = unpack(saved[INDEX_KEY]); } catch (_) { /* wait for the scheduled download */ }
}, () => {});

function refreshIndex() {
  if (pending) return pending;
  pending = (async () => {
    await indexReady;
    const response = await fetch("https://wowpriced.com/check/data/all.json", {
      credentials: "omit", referrerPolicy: "no-referrer", redirect: "error", cache: "no-store",
      signal: AbortSignal.timeout(10000)
    });
    if (!response.ok) throw Error("index unavailable");
    const body = await response.text();
    if (new TextEncoder().encode(body).length >= 1500000) throw Error("index too large");
    const record = {downloaded_at: Date.now(), data: JSON.parse(body)};
    const next = unpack(record);
    if (!fresh(next)) throw Error("index expired");
    // Keep the compact representation on disk, never a browsing log or expanded rows.
    await Wowpriced.api(chrome.storage.local, "set", {[INDEX_KEY]: record});
    current = next;
  })().catch(() => { /* offline: retain only still-fresh local facts */ })
    .finally(() => { pending = null; });
  return pending;
}
async function ensureAlarm() {
  const alarm = await Wowpriced.api(chrome.alarms, "get", ALARM);
  if (!alarm) chrome.alarms.create(ALARM, {delayInMinutes: 360, periodInMinutes: 360});
}
function start() { ensureAlarm().catch(() => {}); refreshIndex(); }
chrome.runtime.onInstalled.addListener(start);
chrome.runtime.onStartup.addListener(start);
chrome.alarms.onAlarm.addListener(alarm => {if (alarm.name === ALARM) refreshIndex();});
// Recreate a lost alarm when a worker wakes, without fetching on a page visit.
ensureAlarm().catch(() => {});

async function handle(message) {
  const rules = await rulesReady;
  if (message.type === "rules") return rules;
  if (message.type !== "lookup" || typeof message.alias !== "string" || message.alias.length > 8192) return null;
  const settings = await Wowpriced.api(chrome.storage.sync, "get", {enabled: true, hiddenSites: {}});
  if (settings.enabled !== true || (message.site && settings.hiddenSites[message.site])) return null;
  await indexReady;
  // Missing/expired data stays invisible until an install/startup/alarm refresh.
  // No lookup, including a miss, can make a network request.
  if (!fresh(current) || !Object.hasOwn(current.data.entries, message.alias)) return null;
  const row = current.data.entries[message.alias];
  const entry = Object.fromEntries(current.data.fields.flatMap((field, i) => row[i] < 0 ? [] : [[field, current.values[row[i]]]]));
  const model = Wowpriced.presentation(entry, message.alias, rules);
  return model ? {...model, expiresAt: expires(current)} : null;
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || !message || !["rules", "lookup"].includes(message.type)) return false;
  handle(message).then(respond, () => respond(null));
  return true;
});
