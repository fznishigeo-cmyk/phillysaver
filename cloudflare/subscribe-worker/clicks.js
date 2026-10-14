/** Anonymous aggregates only. No visitor/IP/cookie keys, raw events or logging. */
import { safeEqual, json } from "./util.js";

// One INTEGER PRIMARY KEY row/hour, no secondary indexes. One accepted click
// writes one aggregate row; the atomic hourly cap bounds all isolates together.
export const ADD_CLICK = `INSERT INTO click_hours(hour,total,counts,quota) VALUES (?1,1,json_object(?2,1),?4)
ON CONFLICT(hour) DO UPDATE SET total=total+1,
counts=json_set(counts,?3,COALESCE(json_extract(counts,?3),0)+1)
WHERE total < quota`;
const HOUR = 3600000;
const silent = () => new Response(null, {status: 204, headers: {"Cache-Control": "no-store"}});

export function createClickHandler() {
  // Best effort pre-D1 load shedding. These are deal counters, never people.
  let minute = -1, requests = 0, deals = new Map();
  let readMinute = -1, reads = 0;
  return async function handleClicks(request, env) {
    const url = new URL(request.url);
    const reading = url.pathname === "/c/counts";
    if (env.CLICK_SIGNAL_ENABLED !== "true") return reading
      ? json({error: "disabled"}, 404, {"Cache-Control": "no-store"}) : silent();
    const cap = Number(env.CLICK_SIGNAL_HOURLY_CAP);
    const globalLimit = Number(env.CLICK_SIGNAL_ISOLATE_PER_MINUTE);
    const dealLimit = Number(env.CLICK_SIGNAL_DEAL_PER_MINUTE);
    if (!env.CLICKS || !Number.isInteger(cap) || cap < 1 || cap > 3000
        || !Number.isInteger(globalLimit) || globalLimit < 1 || globalLimit > 600
        || !Number.isInteger(dealLimit) || dealLimit < 1 || dealLimit > 60) return reading
      ? json({error: "unavailable"}, 503, {"Cache-Control": "no-store"}) : silent();
    try {
      const now = Date.now(), hour = Math.floor(now / HOUR), clockMinute = Math.floor(now / 60000);
      if (reading) {
        const token = env.CLICK_SIGNAL_TOKEN;
        if (request.method !== "GET" || !token || !safeEqual(request.headers.get("Authorization") || "", `Bearer ${token}`))
          return json({error: "unauthorized"}, 401, {"Cache-Control": "no-store"});
        if (readMinute !== clockMinute) { readMinute = clockMinute; reads = 0; }
        if (++reads > 2) return json({error: "limited"}, 429, {"Cache-Control": "no-store"});
        const raw = url.searchParams.get("since");
        const since = Number(raw);
        if (!raw || !/^\d+$/.test(raw) || !Number.isSafeInteger(since) || since < hour - 168 || since > hour)
          return json({error: "range"}, 400, {"Cache-Control": "no-store"});
        const until = Math.min(since + 6, hour);
        // At most one new row/hour: a successful polling heartbeat makes zero
        // counts distinguishable from pre-activation or unobserved hours.
        await env.CLICKS.prepare("INSERT OR IGNORE INTO click_hours(hour,total,counts,quota) VALUES (?,0,'{}',?)").bind(hour, cap).run();
        const result = await env.CLICKS.prepare("SELECT hour,total,counts,quota FROM click_hours WHERE hour>=? AND hour<? ORDER BY hour").bind(since, until).all();
        const stored = new Map(result.results.map(r => [r.hour, r]));
        const hours = [];
        for (let h = since; h < until; h++) {
          const row = stored.get(h);
          hours.push({hour: h, counts: row ? JSON.parse(row.counts) : {},
            available: !!row, capped: !!row && row.total >= row.quota});
        }
        // At most 24 expired rows/day in steady state, bounded even after an outage.
        await env.CLICKS.prepare("DELETE FROM click_hours WHERE hour IN (SELECT hour FROM click_hours WHERE hour<? ORDER BY hour LIMIT 24)").bind(hour - 168).run();
        return json({since, until, hours}, 200, {"Cache-Control": "no-store"});
      }
      if (request.method !== "POST" || url.search || request.headers.get("Sec-GPC") === "1"
          || request.headers.get("X-WP-Opt-Out") === "1" || request.headers.has("Cookie")) return silent();
      // Exact first-party origins only; no Referer, cookie or IP inspection.
      const origins = String(env.CLICK_SIGNAL_ORIGINS || "").split(",");
      if (!origins.includes(request.headers.get("Origin"))) return silent();
      const ua = request.headers.get("User-Agent") || "";
      if (!ua || /bot|crawl|spider|headless|curl|wget|python|preview|lighthouse/i.test(ua)) return silent();
      if (minute !== clockMinute) { minute = clockMinute; requests = 0; deals = new Map(); }
      if (++requests > globalLimit) return silent();
      // Read a bounded stream even when Content-Length is absent or forged.
      if (Number(request.headers.get("Content-Length") || 0) > 256 || !request.body) return silent();
      const reader = request.body.getReader();
      let size = 0, chunks = [];
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > 256) { await reader.cancel(); return silent(); }
        chunks.push(value);
      }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      const data = JSON.parse(new TextDecoder().decode(bytes));
      // A slow body must not keep an old hour's write allowance alive.
      if (Math.floor(Date.now() / HOUR) !== hour) return silent();
      if (!data || Object.keys(data).sort().join(",") !== "hour,key,position,surface"
          || typeof data.key !== "string" || !/^[0-9a-f]{16}$/.test(data.key)
          || !["wall", "deal_page", "hub", "search"].includes(data.surface)
          || !["1-8", "9-30", "31-60", "61-120", "121+", "detail"].includes(data.position)
          || data.hour !== hour) return silent();
      const count = (deals.get(data.key) || 0) + 1;
      deals.set(data.key, count);
      if (count > dealLimit) return silent();
      // Surface/position are bounded context, discarded after validation. Only
      // the per-deal hourly count survives. No client timestamp finer than hour.
      await env.CLICKS.prepare(ADD_CLICK).bind(hour, data.key, '$."' + data.key + '"', cap).run();
      return silent();
    } catch (_) {
      return reading ? json({error: "unavailable"}, 503, {"Cache-Control": "no-store"}) : silent();
    }
  };
}
export const handleClicks = createClickHandler();
