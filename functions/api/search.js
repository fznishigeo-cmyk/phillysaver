// Pages Function: current US product facts only. Never log upstream URLs/errors.
const RESERVATION = 110; // search (10) + up to 100 first-page product tokens
const SCHEMA = `CREATE TABLE IF NOT EXISTS search_budget (
  day TEXT PRIMARY KEY, tokens INTEGER NOT NULL, minute INTEGER NOT NULL,
  count INTEGER NOT NULL, last INTEGER NOT NULL
)`;
const DISPATCH_SCHEMA = `CREATE TABLE IF NOT EXISTS search_dispatch (
  id INTEGER PRIMARY KEY CHECK (id = 1), held INTEGER NOT NULL, ready INTEGER NOT NULL
)`;
// `ready` is the lease expiry while held and the cooldown end once released. A
// holder the runtime cancels (the browser aborts a stale query) never runs its
// release, so its lease lapses instead of switching search off for good.
const LEASE = 15000; // well past the 4 s upstream timeout
const ACQUIRE = `INSERT INTO search_dispatch (id, held, ready) VALUES (1, 1, ?)
  ON CONFLICT(id) DO UPDATE SET held = 1, ready = excluded.ready
  WHERE search_dispatch.ready <= ? RETURNING id`;
const GATE = "SELECT ready FROM search_dispatch WHERE id = 1";
const DEBIT = `INSERT INTO search_budget (day, tokens, minute, count, last)
  SELECT ?, ?, ?, 1, ? WHERE ? >= ? AND ? > 0
    AND NOT EXISTS (SELECT 1 FROM search_budget WHERE last > ? - 1000)
  ON CONFLICT(day) DO UPDATE SET
    tokens = search_budget.tokens + excluded.tokens,
    count = CASE WHEN search_budget.minute = excluded.minute THEN search_budget.count + 1 ELSE 1 END,
    minute = excluded.minute, last = excluded.last
  WHERE search_budget.tokens <= ? - excluded.tokens
    AND (search_budget.minute != excluded.minute OR search_budget.count < ?)
  RETURNING day, tokens, minute, count, last`;

export function normalizeQuery(value) {
  if (typeof value !== "string") return "";
  const q = value.trim().toLowerCase().replace(/\s+/g, " ");
  return /^[\p{L}\p{N} -]{2,60}$/u.test(q) ? q : "";
}

function json(body, status = 200) {
  return Response.json(body, {status, headers:{"Cache-Control":"no-store", "X-Robots-Tag":"noindex"}});
}

async function settings(context) {
  if (!context.env.ASSETS) return null;
  const url = new URL("/search-config.json", context.request.url);
  const response = await context.env.ASSETS.fetch(new Request(url));
  if (!response.ok) return null;
  const cfg = await response.json();
  if (!["preview", "on"].includes(cfg.amazon_results) ||
      ![cfg.cache_hours, cfg.tokens_per_day, cfg.max_per_minute].every(Number.isSafeInteger) ||
      cfg.cache_hours < 0 || cfg.cache_hours > 24 || cfg.tokens_per_day < 0 || cfg.max_per_minute < 0) return null;
  return cfg;
}

async function reserve(kv, limits, cfg, now) {
  const day = new Date(now).toISOString().slice(0, 10), key = "search:v1:" + day;
  const minute = Math.floor(now / 60000);
  const stored = await kv.get(key, "json");
  if (stored !== null && (!stored || !Number.isSafeInteger(stored.tokens) || stored.tokens < 0 ||
      !Number.isSafeInteger(stored.count) || stored.count < 0 ||
      !Number.isSafeInteger(stored.minute) || !Number.isSafeInteger(stored.last))) return "budget_unavailable";
  if ((stored?.tokens || 0) + RESERVATION > cfg.tokens_per_day) return "budget_exhausted";
  if ((stored?.minute === minute && stored.count >= cfg.max_per_minute) ||
      (stored && now - stored.last < 1000) || cfg.max_per_minute === 0) return "rate_limited";
  // KV is eventually consistent: it cannot be the authority for a money cap.
  // D1 executes this conditional debit atomically, across regions/isolates.
  // Every attempt remains charged, including cache/write errors and timeouts.
  const results = await limits.batch([
    limits.prepare(SCHEMA),
    limits.prepare(DEBIT).bind(day, RESERVATION, minute, now, cfg.tokens_per_day, RESERVATION,
      cfg.max_per_minute, now, cfg.tokens_per_day, cfg.max_per_minute),
    limits.prepare("DELETE FROM search_budget WHERE day < ?").bind(
      new Date(now - 2 * 86400000).toISOString().slice(0, 10))
  ]);
  const next = results[1]?.results?.[0];
  if (!next) return "limit_reached";
  await kv.put(key, JSON.stringify(next), {expirationTtl:172800});
  return "";
}

function stamp(minutes, now) {
  if (!Number.isSafeInteger(minutes) || minutes < 0) return "";
  const ms = Date.UTC(2011, 0, 1) + minutes * 60000;
  return ms <= now && now - ms <= 86400000 ? new Date(ms).toISOString() : "";
}

export function currentItems(products, now = Date.now()) {
  const items = [], seen = new Set();
  for (const p of Array.isArray(products) ? products : []) {
    if (!p || typeof p.asin !== "string" || !/^[A-Z0-9]{10}$/.test(p.asin) || seen.has(p.asin) ||
        typeof p.title !== "string" || !p.title.trim() || !p.stats) continue;
    const current = p.stats.current;
    if (!Array.isArray(current)) continue;
    const buybox = p.stats.buyBoxIsUnqualified !== true && Number.isSafeInteger(current[18]) && current[18] > 0 && stamp(p.stats.lastBuyBoxUpdate, now);
    const price = buybox ? current[18] : current[1];
    const asOf = buybox || stamp(p.lastUpdate, now);
    if (!Number.isSafeInteger(price) || price <= 0 || !asOf) continue;
    const names = (Array.isArray(p.images) ? p.images : []).map(img =>
      typeof img === "string" ? img : img && (img.l || img.m));
    if (typeof p.imagesCSV === "string") names.push(p.imagesCSV.split(",")[0]);
    const filename = names.find(name => typeof name === "string" &&
      /^[A-Za-z0-9+_.%-]+\.(?:jpg|jpeg|png|webp)$/i.test(name)) || "";
    const image = filename ? "https://m.media-amazon.com/images/I/" + filename : "";
    items.push({asin:p.asin, title:p.title.trim().slice(0, 500), image, price_cents:price, as_of:asOf});
    seen.add(p.asin);
    if (items.length === 10) break;
  }
  return items;
}

async function keepa(q, key) {
  const controller = new AbortController();
  let timer;
  try {
    return await Promise.race([
      (async () => {
        const url = new URL("https://api.keepa.com/search");
        Object.entries({key, domain:"1", type:"product", term:q, page:"0", stats:"1", history:"0"})
          .forEach(([k, v]) => url.searchParams.set(k, v));
        const response = await fetch(url, {signal:controller.signal, redirect:"error",
          headers:{"User-Agent":"dealradar/0.1 (+https://wowpriced.com; contact: hello@wowpriced.com)"}});
        if (response.status === 429) {
          // Honor Keepa's own cooldown (seconds, max 1 h); 60 s when it gives none.
          const error = new Error("keepa_error"), after = Number(response.headers.get("Retry-After"));
          error.cooldown = 1000 * (after >= 1 ? Math.min(after, 3600) : 60);
          throw error;
        }
        if (!response.ok) throw new Error("keepa_error");
        const data = await response.json();
        if (!data || data.error || !Array.isArray(data.products)) throw new Error("keepa_error");
        return data;
      })(),
      new Promise((resolve, reject) => { timer = setTimeout(() => {
        controller.abort(); reject(new Error("timeout"));
      }, 4000); })
    ]);
  } finally { clearTimeout(timer); }
}

export async function onRequest(context) {
  const {request, env} = context;
  if (request.method !== "GET") return json({items:[], reason:"method_not_allowed"}, 405);
  const params = new URL(request.url).searchParams;
  const q = normalizeQuery(params.get("q"));
  if (!q || params.getAll("q").length !== 1) return json({items:[], reason:"invalid_query"}, 400);
  try {
    const cfg = await settings(context);
    if (!cfg) return json({items:[], reason:"disabled"});
    const cache = globalThis.caches && caches.default;
    // Limits are part of the key: switching OFF or changing policy cannot reuse
    // a result under older settings. Keys never include the API secret.
    const cacheKey = new Request(new URL("/api/search?q=" + encodeURIComponent(q) +
      "&policy=" + encodeURIComponent(JSON.stringify(cfg)), request.url));
    const cached = cache && cfg.cache_hours && await cache.match(cacheKey);
    if (cached) return json(await cached.json());
    if (!env.KEEPA_KEY || !env.SEARCH_BUDGET || typeof env.SEARCH_BUDGET.get !== "function" ||
        typeof env.SEARCH_BUDGET.put !== "function") return json({items:[], reason:"unavailable"});
    if (!env.SEARCH_LIMITS || typeof env.SEARCH_LIMITS.prepare !== "function" ||
        typeof env.SEARCH_LIMITS.batch !== "function") return json({items:[], reason:"budget_unavailable"});
    // A held lease or an upstream cooldown answers before any tokens are debited
    // (advisory read; the atomic acquire below is the authority).
    const gate = await env.SEARCH_LIMITS.batch([
      env.SEARCH_LIMITS.prepare(DISPATCH_SCHEMA), env.SEARCH_LIMITS.prepare(GATE)]);
    if ((gate[1]?.results?.[0]?.ready || 0) > Date.now()) return json({items:[], reason:"rate_limited"});
    const started = Date.now();
    const reason = await reserve(env.SEARCH_BUDGET, env.SEARCH_LIMITS, cfg, started);
    if (reason) return json({items:[], reason});
    // Hold the shared lease through actual dispatch and response completion.
    const acquired = Date.now(), lease = acquired + LEASE;
    const dispatch = await env.SEARCH_LIMITS.batch([
      env.SEARCH_LIMITS.prepare(DISPATCH_SCHEMA),
      env.SEARCH_LIMITS.prepare(ACQUIRE).bind(lease, acquired)
    ]);
    if (!dispatch[1]?.results?.length) return json({items:[], reason:"rate_limited"});
    let data, cooldown = 1000;
    try {
      // A reservation covers its own minute (so its own UTC day) only: one that
      // waited into the next window is spent, never dispatched against it.
      if (Math.floor(Date.now() / 60000) !== Math.floor(started / 60000)) return json({items:[], reason:"rate_limited"});
      try { data = await keepa(q, env.KEEPA_KEY); }
      catch (error) {
        cooldown = error.cooldown || cooldown;
        return json({items:[], reason:error.message === "timeout" ? "timeout" : "keepa_error"});
      }
      // Never refund a reservation using an absent/untrusted receipt. An oversized
      // receipt halts further searches for this UTC day, leaving pipeline work alone.
      if (Number.isFinite(data.tokensConsumed) && data.tokensConsumed > RESERVATION) {
        const day = new Date(started).toISOString().slice(0, 10);
        await env.SEARCH_LIMITS.prepare("UPDATE search_budget SET tokens = ? WHERE day = ?")
          .bind(Number.MAX_SAFE_INTEGER, day).run();
        await env.SEARCH_BUDGET.put("search:v1:" + day,
          JSON.stringify({tokens:Number.MAX_SAFE_INTEGER, minute:Math.floor(Date.now() / 60000), count:0, last:Date.now()}),
          {expirationTtl:172800});
        return json({items:[], reason:"budget_exhausted"});
      }
    } finally {
      // Release only our own lease: a holder delayed past it cannot free a later one.
      await env.SEARCH_LIMITS.prepare("UPDATE search_dispatch SET held = 0, ready = ? WHERE id = 1 AND ready = ?")
        .bind(Date.now() + cooldown, lease).run();
    }
    const body = {items:currentItems(data.products)};
    const ttl = Math.floor(Math.min(cfg.cache_hours * 3600, ...body.items.map(
      item => (Date.parse(item.as_of) + 86400000 - Date.now()) / 1000)));
    if (cache && ttl > 0) {
      try {
        await cache.put(cacheKey, Response.json(body, {headers:{"Cache-Control":"public, max-age=" + ttl}}));
      } catch (_) { /* A cache failure cannot turn into an upstream retry. */ }
    }
    return json(body);
  } catch (_) { return json({items:[], reason:"budget_unavailable"}); }
}
