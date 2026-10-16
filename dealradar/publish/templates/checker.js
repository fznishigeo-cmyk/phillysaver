"use strict";
// URL identities and shard addressing use the publisher's rendered rules.
const checkRules = JSON.parse(document.getElementById("check-rules").textContent);
const checkOptions = JSON.parse(document.getElementById("check-options").textContent);

function checkURL(value) {
  if (typeof value !== "string" || /[\s\\<>"\x00-\x1f]/u.test(value)) return null;
  const parts = /^([^:/?#]+):\/\/([^/?#]+)([^?#]*)(?:\?([^#]*))?(?:#.*)?$/.exec(value);
  if (!parts || !checkRules.schemes.includes(parts[1].toLowerCase())) return null;
  try {
    const parsed = new URL(value);
    if (!parsed.hostname || parsed.username || parsed.password || (parsed.port && !["80", "443"].includes(parsed.port))) return null;
    // WHATWG URLs normalize paths, Unicode and default ports; the publisher
    // preserves those spellings. Use the raw components after validating the address.
    const authority = parts[2].slice(parts[2].lastIndexOf("@") + 1);
    const host = (authority.startsWith("[") ? authority.split("]")[0] + "]" : authority.split(":")[0]).toLowerCase();
    return {scheme:parts[1].toLowerCase(), authority:parts[2].toLowerCase(), host,
      path:parts[3], query:new URLSearchParams(parts[4] || ""), raw:value};
  } catch (_) { return null; }
}

function checkHost(host, domain) {
  return host === domain || (checkRules.subdomains && host.endsWith("." + domain));
}

function checkShort(parts) {
  return !!parts && checkRules.short_hosts.some(host => checkHost(parts.host, host));
}

function checkMatch(pattern, path) {
  // The publisher's \d accepts Unicode decimal digits; JavaScript's \d is ASCII.
  return new RegExp("^(?:" + pattern.replace(/\\d/g, "\\p{Decimal_Number}") + ")$", "u").exec(path);
}

function checkEncode(value) {
  // urllib.parse.urlencode (quote_plus), including its ~ and * behavior.
  return encodeURIComponent(value).replace(/[!'()*]/g, c => "%" + c.charCodeAt(0).toString(16).toUpperCase()).replace(/%20/g, "+");
}

function checkAliases(value) {
  const p = checkURL(value);
  if (!p || checkShort(p)) return [];
  const store = Object.keys(checkRules.hosts).find(host => checkHost(p.host, host));
  const name = checkRules.hosts[store] || "other";
  const amazon = new RegExp(checkRules.amazon.pattern, checkRules.amazon.flags).exec(p.raw);
  if (name === "amazon" && amazon) return ["amazon:" + amazon[1].toUpperCase()];
  if (name === "bestbuy") {
    const rule = checkRules.bestbuy;
    const match = rule.paths.map(pattern => checkMatch(pattern, p.path)).find(Boolean);
    const ids = p.query.getAll(rule.query).filter(Boolean).concat(match ? [match[1]] : []);
    if (ids.length && ids.every(id => id === ids[0]) && checkMatch(rule.id, ids[0])) return [name + ":" + ids[0]];
  }
  if (name === "ebay") {
    const match = checkMatch(checkRules.ebay.path, p.path);
    if (match && !p.query.getAll(checkRules.ebay.reject_query).some(Boolean)) return [name + ":" + match[1]];
  }
  if (["walmart", "woot", "target"].includes(name)) {
    const match = checkMatch(checkRules[name].path, p.path);
    if (match) return [name + ":" + match[1]];
  }
  const rule = checkRules.canonical;
  if (new RegExp(rule.amazon_host).test(p.host) && amazon) return ["url:https://www.amazon.com/dp/" + amazon[1].toUpperCase()];
  const kept = [];
  p.query.forEach((v, k) => {
    const lower = k.toLowerCase();
    if (!lower.startsWith(rule.tracking_prefix) && !rule.tracking_exact.includes(lower)) kept.push(checkEncode(k) + "=" + checkEncode(v));
  });
  return ["url:" + p.scheme + "://" + p.authority + p.path + (kept.length ? "?" + kept.join("&") : "")];
}

async function checkShard(alias, count = checkOptions.shards) {
  const digest = new Uint8Array(await crypto.subtle.digest(checkRules.shard.hash, new TextEncoder().encode(alias)));
  const bits = Math.log2(count), digits = Math.ceil(bits / 4);
  const hex = Array.from(digest, n => n.toString(16).padStart(2, "0")).join("");
  const bucket = parseInt(hex.slice(0, digits), 16) >>> (digits * 4 - bits);
  return bucket.toString(16).padStart(Math.max(checkRules.shard.min_hex_width, digits), "0");
}

(function () {
  const form = document.getElementById("check-form"), input = document.getElementById("product-url");
  const result = document.getElementById("check-result");
  let sequence = 0, pending = false;
  const money = n => "$" + n.toFixed(2);
  const date = iso => new Date(iso).toLocaleDateString("en-US", {year:"numeric", month:"short", day:"numeric", timeZone:"UTC"});
  function text(tag, value, cls) {
    const node = document.createElement(tag);node.textContent = value;
    if (cls) node.className = cls;
    return node;
  }
  function link(box, label, path, family) {
    if (typeof path !== "string" || !new RegExp("^/" + family + "/[a-zA-Z0-9_-]+/$").test(path)) return;
    const node = text("a", label);node.href = path;box.append(node);
  }
  function checked(at) {
    const time = text("time", "", "check-meta"), value = Date.parse(at);
    time.dateTime = at;time.title = new Date(value).toUTCString();
    const minutes = Math.floor(Math.max(0, Date.now() - value) / 60000);
    time.textContent = "checked " + (minutes < 1 ? "just now" : minutes < 60 ? minutes + " minute" + (minutes === 1 ? "" : "s") + " ago" :
      minutes < 1440 ? Math.floor(minutes / 60) + " hour" + (minutes < 120 ? "" : "s") + " ago" :
      Math.floor(minutes / 1440) + " day" + (minutes < 2880 ? "" : "s") + " ago");
    return time;
  }
  function show(entry, parts) {
    result.replaceChildren();
    const hasPrice = typeof entry.price === "number" && Number.isFinite(entry.price);
    if (hasPrice && entry.verdict) result.append(text("h2", entry.verdict, "check-verdict"));
    result.append(text(hasPrice && entry.verdict ? "p" : "h2", entry.item, "check-item"));
    const actions = text("div", "", "check-actions");
    if (!hasPrice) {
      link(actions, "See the deal", entry.deal_path, "deal");result.append(actions);return;
    }
    if (entry.kind === "ended") result.append(text("p", "Last verified price " + money(entry.price) + " on " + date(entry.at) + " — this deal has ended"));
    else result.append(text("p", money(entry.price), "detail-price"));
    const stores = {amazon:"Amazon", bestbuy:"Best Buy", target:"Target", walmart:"Walmart", ebay:"eBay", woot:"Woot"};
    const label = stores[entry.store] || (entry.store === "other" && parts ? parts.host.replace(/^www\./, "") : "");
    const meta = text("p", label ? label + " · " : "", "check-meta");meta.append(checked(entry.at));result.append(meta);
    const credits = (Array.isArray(entry.credit) ? entry.credit : []).filter(c => c && typeof c.name === "string" && c.name);
    if (credits.length) {
      const via = text("p", "via ", "check-meta");
      credits.forEach((c, i) => {
        if (i) via.append(text("span", ", "));
        if (typeof c.url === "string" && /^https?:\/\//i.test(c.url)) {
          const a = text("a", c.name);a.href = c.url;a.rel = "noopener";via.append(a);
        } else via.append(text("span", c.name));
      });
      result.append(via);
    }
    if (entry.amazon_as_of) {
      result.append(text("p", "Amazon price as of " + entry.amazon_as_of, "amazon-as-of"));
      const details = document.createElement("details");details.className = "amazon-price-details";
      details.append(text("summary", "Details"), text("p", checkOptions.disclaimer));result.append(details);
    }
    if (entry.checks && entry.since) result.append(text("p", entry.checks + " recorded checks since " + date(entry.since), "check-meta"));
    if (entry.kind === "live") link(actions, "See the deal", entry.deal_path, "deal");
    link(actions, "Price history", entry.p_path, "p");result.append(actions);
  }
  function notFound(value) {
    result.replaceChildren(text("h2", "We haven't verified a price for this product yet.", "check-verdict"));
    const parts = checkURL(value);
    let words = "";
    if (parts) {
      try {
        const skip = ["product","products","site","offers","itm","ip","dp","gp","ref","p","searchpage"];
        words = decodeURIComponent(parts.path).split("/").filter(s => /^[\p{L}\p{N}]+(?:[-_+ ][\p{L}\p{N}]+)*$/u.test(s) && /[a-z]{3}/i.test(s) && !skip.includes(s.toLowerCase()) && !/^(?=.*\d)[a-z\d]+$/i.test(s)).sort((a, b) => /[-_+ ]/.test(b) - /[-_+ ]/.test(a) || b.length - a.length)[0] || "";
      }
      catch (_) { /* Malformed escapes do not become a search. */ }
    }
    words = words.replace(/[^a-zA-Z0-9]+/g, " ").trim().slice(0, 120);
    const a = text("a", words ? "Search our deals for “" + words + "”" : "Search our deals");
    a.href = "/";
    if (words) a.addEventListener("click", () => {
      try {
        let m = JSON.parse(sessionStorage.getItem("wp.browse") || "null");
        if (!m || m.version !== 1 || !m.entries || typeof m.entries !== "object" || Array.isArray(m.entries)) m = {version: 1, last: "", entries: {}};
        const id = "check-" + Date.now().toString(36);
        m.entries[id] = {query: words};m.last = id;
        sessionStorage.setItem("wp.browse", JSON.stringify(m));
      } catch (_) { /* storage blocked: plain home */ }
    });
    result.append(a);
  }
  async function run() {
    const id = ++sequence;
    pending = false;
    const value = input.value.trim(), parts = checkURL(value);
    if (checkShort(parts)) {
      result.replaceChildren(text("p", "Open the link first, then paste the full product address"));return;
    }
    const keys = checkAliases(value);
    if (!keys.length) {result.replaceChildren(text("p", "Paste a full http or https product link."));return;}
    result.replaceChildren(text("p", "Checking our verified records…"));
    pending = true;
    try {
      // Replacing browser history makes a shareable result without navigation
      // or sending the pasted address in a request body, query or referrer.
      history.replaceState(null, "", "/check/?u=" + encodeURIComponent(value));
      for (const alias of keys) {
        const name = await checkShard(alias);
        if (id !== sequence) return;
        const response = await fetch("/check/data/" + name + ".json", {cache:"no-store", credentials:"omit", redirect:"error", referrerPolicy:"no-referrer"});
        if (!response.ok) throw new Error("unavailable");
        const rows = await response.json();
        if (id !== sequence) return;
        if (Object.prototype.hasOwnProperty.call(rows, alias)) {show(rows[alias], parts);return;}
      }
      notFound(value);
    } catch (_) {
      if (id === sequence) result.replaceChildren(text("p", "We couldn't load our verified records. Please try Check again."));
    } finally {
      if (id === sequence) pending = false;
    }
  }
  form.addEventListener("submit", event => {event.preventDefault();run();});
  // Invalidate an unfinished lookup as soon as someone changes the input.
  input.addEventListener("input", () => {++sequence; if (pending) {pending = false; result.replaceChildren();}});
  const prefill = new URLSearchParams(location.search).get("u");
  if (prefill) {input.value = prefill;run();}
})();
