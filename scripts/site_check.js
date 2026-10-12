// site_check.js: the hourly look at the live site a visitor gets (operator,
// 2026-10-12: "go to wowpriced.com and check the images yourself"). Reads
// deals.json, loads every live deal's photo in Chromium the way a visitor's
// browser does (no referrer), opens the home page, and prints which deals
// have no photo or a broken one, grouped by store, source and host. Read-only;
// analytics and ad tags are blocked so the check is not counted as a visit.
// Everything printed is already public on the site.
const { chromium } = require("playwright");
const fs = require("fs");

const SITE = (process.env.SITE || "https://wowpriced.com/").replace(/\/?$/, "/");
const BLOCK = /googletagmanager|google-analytics|facebook\.net|facebook\.com\/tr|analytics\.tiktok|pinimg\.com\/ct|bat\.bing/;
const PHOTO_TIMEOUT_MS = 15000, CONCURRENCY = 12, EXAMPLES = 12;

const host = (u) => { try { return new URL(u).host.replace(/^www\./, ""); } catch (e) { return "?"; } };
const source = (d) => ((d.credits || [])[0] || {}).name || "?";
function top(rows, key, n = 10) {
  const c = {};
  for (const r of rows) { const k = key(r) || "?"; c[k] = (c[k] || 0) + 1; }
  return Object.entries(c).sort((a, b) => b[1] - a[1]).slice(0, n).map(([k, v]) => `${v} ${k}`).join(", ");
}
const line = (d) => `  ${d.store || "?"} | ${String(d.item || "").slice(0, 60)} | via ${source(d)} | ${host(d.url)}`;

(async () => {
  const out = [];
  const say = (s) => { out.push(s); console.log(s); };
  const res = await fetch(SITE + "deals.json", { cache: "no-store" });
  if (!res.ok) throw new Error(`deals.json HTTP ${res.status}`);
  const doc = await res.json();
  const alive = (doc.deals || []).filter((d) => d.status === "alive" && !d.locked);
  const none = alive.filter((d) => !d.image_url);
  const withPhoto = alive.filter((d) => d.image_url);

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.route(BLOCK, (r) => r.abort());
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 160)));
  await page.goto(SITE, { waitUntil: "load", timeout: 60000 });
  await page.waitForTimeout(5000);
  const shown = await page.evaluate(() => ({
    cards: document.querySelectorAll("#content .card[data-key]").length,
    clock: (document.getElementById("countdown") || {}).textContent || "",
    tagline: (document.getElementById("tagline") || {}).textContent || "",
  }));
  // Every photo, loaded in the page the way the wall loads it.
  const urls = [...new Set(withPhoto.map((d) => d.image_url))];
  const failed = new Set(await page.evaluate(async ([urls, ms, conc]) => {
    const bad = [];
    let i = 0;
    async function worker() {
      while (i < urls.length) {
        const u = urls[i++];
        const ok = await new Promise((done) => {
          const img = new Image();
          img.referrerPolicy = "no-referrer";
          const t = setTimeout(() => done(false), ms);
          img.onload = () => { clearTimeout(t); done(img.naturalWidth > 1); };
          img.onerror = () => { clearTimeout(t); done(false); };
          img.src = u;
        });
        if (!ok) bad.push(u);
      }
    }
    await Promise.all(Array.from({ length: conc }, worker));
    return bad;
  }, [urls, PHOTO_TIMEOUT_MS, CONCURRENCY]));
  await browser.close();
  const broken = withPhoto.filter((d) => failed.has(d.image_url));

  const pct = (n) => (alive.length ? Math.round((100 * n) / alive.length) : 0);
  const good = withPhoto.length - broken.length;
  say(`## Site check ${new Date().toISOString().slice(0, 16)}Z`);
  say(`live deals ${alive.length}, cards on the page ${shown.cards}, clock "${shown.clock}", tagline "${shown.tagline}"`);
  say(`photos showing: ${good} (${pct(good)}%) | no photo: ${none.length} (${pct(none.length)}%) | broken: ${broken.length} (${pct(broken.length)}%)`);
  say(`page errors: ${errors.length ? errors.join(" / ") : "none"}`);
  if (none.length) {
    say(`\n### No photo (${none.length})`);
    say(`by store: ${top(none, (d) => d.store)}`);
    say(`by source: ${top(none, source)}`);
    // A deal that needs steps links to its post, so only a direct link's
    // host says where the pipeline looked for the photo.
    const direct = none.filter((d) => !d.needs_steps);
    say(`by link host (direct links, ${direct.length}): ${top(direct, (d) => host(d.url), 15)}`);
    say(`deals that link to their post (codes/steps): ${none.length - direct.length}`);
    say(`newest:`);
    none.sort((a, b) => String(b.first_seen_at).localeCompare(String(a.first_seen_at)))
      .slice(0, EXAMPLES).forEach((d) => say(line(d)));
  }
  if (broken.length) {
    say(`\n### Broken photo (${broken.length})`);
    say(`by photo host: ${top(broken, (d) => host(d.image_url))}`);
    say(`by store: ${top(broken, (d) => d.store)}`);
    broken.slice(0, EXAMPLES).forEach((d) => say(line(d) + ` | photo ${host(d.image_url)}`));
    // Why they fail: what the photo host answers outside the page (status,
    // type, http or https), for a few of them.
    for (const d of broken.slice(0, 3)) {
      let why;
      try {
        const r = await fetch(d.image_url, { redirect: "follow", headers: { "User-Agent":
          "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36" } });
        why = `HTTP ${r.status} ${r.headers.get("content-type") || "?"}`;
      } catch (e) { why = String(e && e.cause && e.cause.code || e).slice(0, 60); }
      say(`  photo check: ${new URL(d.image_url).protocol} ${d.image_url.slice(0, 120)} -> ${why}`);
    }
  }
  if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, out.join("\n") + "\n");
})().catch((e) => { console.error(e); process.exit(1); });
