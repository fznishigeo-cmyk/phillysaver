/* Shared by the content script, popup and background; URL input only. */
(function (root) {
  "use strict";
  function urlParts(value, rules) {
    if (typeof value !== "string" || /[\s\\<>"\x00-\x1f]/.test(value)) return null;
    try {
      const url = new URL(value);
      if (!rules.schemes.includes(url.protocol.slice(0, -1)) || !url.hostname ||
          url.username || url.password || (url.port && !["80", "443"].includes(url.port))) return null;
      return url;
    } catch (_) { return null; }
  }
  function domain(host, name) { return host === name || host.endsWith("." + name); }
  function site(value, rules) {
    const url = urlParts(value, rules);
    return url ? url.hostname : "";
  }
  function full(pattern, value) { return new RegExp("^(?:" + pattern + ")$").exec(value); }
  function aliases(value, rules) {
    const url = urlParts(value, rules);
    if (!url || rules.short_hosts.some(host => domain(url.hostname, host))) return [];
    const raw = /^(https?):\/\/([^/?#]+)([^?#]*)(?:\?[^#]*)?(?:#.*)?$/i.exec(value);
    if (!raw) return [];
    const pathname = raw[3]; // Preserve Python urlsplit's raw path, including Unicode.
    const store = Object.keys(rules.hosts).find(host => domain(url.hostname, host));
    const name = store ? rules.hosts[store] : "other";
    const asin = new RegExp(rules.amazon.pattern, rules.amazon.flags).exec(value);
    if (name === "amazon" && asin) return ["amazon:" + asin[1].toUpperCase()];
    if (name === "bestbuy") {
      const match = rules.bestbuy.paths.map(p => full(p, pathname)).find(Boolean);
      const ids = url.searchParams.getAll(rules.bestbuy.query).filter(Boolean);
      if (match) ids.push(match[1]);
      if (ids.length && new Set(ids).size === 1 && full(rules.bestbuy.id, ids[0])) return ["bestbuy:" + ids[0]];
    }
    if (name === "ebay" && !url.searchParams.getAll(rules.ebay.reject_query).some(Boolean)) {
      const match = full(rules.ebay.path, pathname);
      if (match) return ["ebay:" + match[1]];
    }
    if (["walmart", "woot", "target"].includes(name)) {
      const match = full(rules[name].path, pathname);
      if (match) return [name + ":" + match[1]];
    }
    // Match Python's urlencode (including '~', which URLSearchParams escapes).
    const pairs = new URLSearchParams();
    for (const [key, val] of url.searchParams) {
      const lower = key.toLowerCase();
      if (!lower.startsWith(rules.canonical.tracking_prefix) && !rules.canonical.tracking_exact.includes(lower)) pairs.append(key, val);
    }
    // Python canonicalizes international Amazon domains too, even as a fallback.
    if (/(^|\.)amazon\.[a-z]{2,3}(\.[a-z]{2})?$/.test(url.hostname) && asin) return ["url:https://www.amazon.com/dp/" + asin[1].toUpperCase()];
    const query = pairs.toString().replace(/%7E/gi, "~").replace(/\*/g, "%2A");
    return ["url:" + raw[1].toLowerCase() + "://" + raw[2].toLowerCase() + raw[3] + (query ? "?" + query : "")];
  }
  async function shard(alias, count = 256) {
    const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(alias)));
    const bits = Math.log2(count), digits = Math.ceil(bits / 4);
    const hex = Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
    return (parseInt(hex.slice(0, digits), 16) >>> (digits * 4 - bits)).toString(16).padStart(Math.max(2, digits), "0");
  }
  function historyURL(entry) {
    const path = entry.p_path || entry.deal_path;
    return typeof path === "string" && /^\/(?:p|deal)\/[a-zA-Z0-9/_-]+\/?$/.test(path)
      ? "https://wowpriced.com" + path + "?ref=ext" : "";
  }
  function presentation(entry, alias, rules) {
    if (!entry || typeof entry.item !== "string" || !["live", "ended"].includes(entry.kind)) return null;
    if (entry.store === "ebay" && entry.kind !== "live") return null;  // eBay listings end; Walmart OK since 2026-10-16
    const href = historyURL(entry);
    if (!href) return null;
    const amazon = alias.startsWith("amazon:") || entry.store === "amazon";
    const linkOnly = (amazon && rules.AMAZON_MODE === "link") || !("price" in entry);
    const model = {item: entry.item, href, verdict: "", detail: "", at: ""};
    if (!linkOnly && typeof entry.price === "number" && Number.isFinite(entry.price) && entry.price >= 0) {
      if (entry.store !== "ebay" && typeof entry.verdict === "string") model.verdict = entry.verdict;
      model.detail = (entry.kind === "ended" ? "Last verified price " : "") + new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"}).format(entry.price);
      if (entry.kind === "ended") model.detail += " · deal ended";
      if (typeof entry.at === "string" && Number.isFinite(Date.parse(entry.at))) model.at = entry.at;
    }
    return model;
  }
  function relativeTime(at, now = Date.now()) {
    const minutes = Math.max(0, Math.floor((now - Date.parse(at)) / 60000));
    const days = Math.floor(minutes / 1440);
    return minutes < 1 ? "just now" : minutes < 60 ? minutes + " min ago" : minutes < 1440 ? Math.floor(minutes / 60) + " hr ago" : days + (days === 1 ? " day ago" : " days ago");
  }
  // Callback APIs work with chrome.* in both supported browsers.
  function api(object, method, ...args) {
    return new Promise((resolve, reject) => object[method](...args, result => {
      const error = chrome.runtime.lastError;
      if (error) reject(new Error(error.message)); else resolve(result);
    }));
  }
  async function lookup(alias, rules, site = "") {
    return api(chrome.runtime, "sendMessage", {type: "lookup", alias, site});
  }
  root.Wowpriced = {aliases, shard, site, presentation, historyURL, relativeTime, api, lookup};
  if (typeof module !== "undefined") module.exports = root.Wowpriced;
})(globalThis);
