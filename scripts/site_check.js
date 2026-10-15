// site_check.js: the hourly look at the live site a visitor gets (operator,
// 2026-10-12: "go to wowpriced.com and check the images yourself"). Reads
// deals.json, loads every live deal's photo in Chromium the way a visitor's
// browser does (no referrer), opens the home page, and prints which deals
// have no photo or a broken one, grouped by store, source and host. It also
// walks the visitor controls on desktop and phone; failed steps or photos below
// the config.yaml site_check floors exit 1. Read-only;
// analytics and ad tags are blocked so the check is not counted as a visit.
// Everything printed is already public on the site.
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const { amazonTag, checkAmazonTag, checkSovrnLinks, overrideLinks, overrideNote,
        photoRules, checkPhotos } = require("./site_check_logic.cjs");

const SITE = (process.env.SITE || "https://wowpriced.com/").replace(/\/?$/, "/");
const BLOCK = /googletagmanager|google-analytics|googleadservices|doubleclick|googlesyndication|facebook\.net|facebook\.com\/tr|analytics\.tiktok|pinimg\.com\/ct|bat\.bing/;
const PHOTO_TIMEOUT_MS = 15000, CONCURRENCY = 12, EXAMPLES = 12;

const host = (u) => { try { return new URL(u).host.replace(/^www\./, ""); } catch (e) { return "?"; } };
const source = (d) => ((d.credits || [])[0] || {}).name || "?";
function top(rows, key, n = 10) {
  const c = {};
  for (const r of rows) { const k = key(r) || "?"; c[k] = (c[k] || 0) + 1; }
  return Object.entries(c).sort((a, b) => b[1] - a[1]).slice(0, n).map(([k, v]) => `${v} ${k}`).join(", ");
}
const line = (d) => `  ${d.store || "?"} | ${String(d.item || "").slice(0, 60)} | via ${source(d)} | ${host(d.url)}`;

const CARDS = "#content .card[data-key]";
const STEP_TIMEOUT_MS = 10000;
const SITE_URL = new URL(SITE);
// Our own site, still paced at 1 request/s. 40 ran out on 2026-10-13 as the
// walk grew (more hubs), and the last page failed as a bare ERR_FAILED.
const SITE_REQUEST_LIMIT = 60;
let siteRequests = 0, siteQueue = Promise.resolve(), lastSiteRequest = 0, lastAbort = "";
async function siteRequest(photo = false) {
  if (!photo && ++siteRequests > SITE_REQUEST_LIMIT) {
    throw new Error(`site request limit (${SITE_REQUEST_LIMIT})`);
  }
  const pending = siteQueue.then(async () => {
    const delay = Math.max(0, 1000 - (Date.now() - lastSiteRequest));
    if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
    lastSiteRequest = Date.now();
  });
  siteQueue = pending;
  await pending;
}
async function readSite(path) {
  let url = new URL(path, SITE);
  // Follow the site's own redirects (Cloudflare Pages sends /about.html to
  // /about with a 308); never one that leaves the site.
  for (let hop = 0; hop < 3; hop++) {
    if (url.origin !== SITE_URL.origin) throw new Error("off-site URL");
    await siteRequest();
    const response = await fetch(url, { cache: "no-store", redirect: "manual",
                                        signal: AbortSignal.timeout(15000) });
    const next = response.headers.get("location");
    if (![301, 302, 303, 307, 308].includes(response.status) || !next) return response;
    url = new URL(next, url);
  }
  throw new Error("too many redirects");
}
async function guardPage(page) {
  page.setDefaultTimeout(STEP_TIMEOUT_MS);
  // The weekly sign-up prompt would cover the page mid-walk: mark it already seen.
  await page.addInitScript(() => {
    try { localStorage.setItem("wp.join.promptUntil", String(Date.now() + 7 * 864e5)); } catch (e) { /* no storage: no prompt */ }
  });
  await page.route("**/*", async (route) => {
    const request = route.request(), url = new URL(request.url());
    if (BLOCK.test(url.href) || request.method() !== "GET" ||
        (url.origin !== SITE_URL.origin && request.resourceType() !== "image")) {
      return route.abort();
    }
    try {
      if (url.origin === SITE_URL.origin) await siteRequest(request.resourceType() === "image");
      await route.continue();
    } catch (e) { lastAbort = String(e.message || e); await route.abort(); }
  });
}
function check(ok, reason) { if (!ok) throw new Error(reason); }

function checkFooter(html, meta) {
  const footers = [...html.matchAll(/<footer>([\s\S]*?)<\/footer>/g)];
  check(footers.length === 1, "expected one shared footer");
  const footer = footers[0][1], year = new Date(meta.generated_at).getUTCFullYear();
  check(footer.includes(`© ${year} wowpriced`), "missing footer copyright");
  const nav = (footer.match(/<nav id="footerlinks"[^>]*>([\s\S]*?)<\/nav>/) || [])[1] || "";
  const links = [...nav.matchAll(/<a href="([^"]+)">([^<]+)<\/a>/g)].map(m => [m[1], m[2]]);
  check(JSON.stringify(links) === JSON.stringify([
    ["/about.html", "About"], ["/how-it-works.html", "How it works"], ["/hacks.html", "Deal hacks"],
    ["/careers.html", "Work with us"], ["/contact.html", "Contact"],
    ["/subscribe.html", "Join &amp; support"], ["/privacy.html", "Privacy"], ["/disclosure.html", "Disclosure"]
  ]), "footer link line changed");
  const escape = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#x27;"})[c]);
  const disclosure = escape(meta.disclosure || ""), disclosureLink = '<a href="/disclosure.html">Disclosure</a>';
  const linked = disclosure.startsWith("Disclosure:") ? disclosureLink + disclosure.slice(10)
    : disclosure + (disclosure ? " " : "") + disclosureLink;
  check(footer.includes(`<span id="metaline">${linked}</span>`), "footer disclosure changed");
  check(!/wowpriced is free for everyone\.|Once: any amount|Membership —|Every deal links to the seller/.test(footer),
    "old footer copy remains");
  if ((meta.support || {}).enabled) check(footer.includes('id="footer-signup"'), "missing footer signup");
}

async function walk(page, doc, device) {
  const results = [], errors = [];
  let shown = { cards: 0, top: 0, clock: "", tagline: "" }, html = "", dealPage = "";
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 160)));
  async function step(n, name, action) {
    try {
      const note = await action();
      results.push({ n, name, ok: true, ...(note ? { note } : {}) });
    } catch (e) {
      // Playwright errors include call logs; keep the summary to one short line.
      // A request our own guard aborted surfaces as a bare ERR_FAILED: name why.
      const why = lastAbort ? ` (guard: ${lastAbort})` : "";
      const reason = (String(e.message || e).split("\n")[0].slice(0, 140) + why).slice(0, 200);
      results.push({ n, name, ok: false, reason });
    }
  }
  await step(1, "Home", async () => {
    const response = await page.goto(SITE, { waitUntil: "load", timeout: 60000 });
    check(response && response.status() === 200, `HTTP ${response && response.status()}`);
    html = await response.text();
    check(!/keepa/i.test(html + JSON.stringify(doc)), "private Amazon source in published data");
    check(!/srcwall|srcsel/.test(html), "source wall or filter still present");
    checkFooter(html, doc.meta || {});
    await page.waitForFunction(() => document.querySelectorAll("#content .card[data-key]").length >= 24,
      null, { timeout: STEP_TIMEOUT_MS });
    await page.waitForTimeout(1000);
    shown = await page.evaluate(() => ({
      cards: document.querySelectorAll("#content .card[data-key]").length, top: 0,
      clock: (document.getElementById("countdown") || {}).textContent || "",
      tagline: (document.getElementById("tagline") || {}).textContent || "",
    }));
    check(/^\d+:[0-5]\d$/.test(shown.clock.trim()), `clock "${shown.clock}"`);
    check(/\b[\d,]+\+? live deals\b/.test(shown.tagline), "missing live-deals tagline");
    check(!errors.length, "page error");
  });
  // Preserve the photo report's first-screen measurement before loading more.
  if (shown.cards) {
    try {
      for (const i of [7, 15, 23]) {
        await page.locator(CARDS).nth(i).scrollIntoViewIfNeeded();
        await page.waitForTimeout(1500);
      }
      await page.waitForTimeout(2000);
      shown.top = await page.locator(CARDS).evaluateAll((cards) => cards.slice(0, 24)
        .filter((c) => [...c.querySelectorAll("img")].some((i) => i.complete && i.naturalWidth > 1)).length);
    } catch { /* A photo shortfall alone does not fail the walk-through. */ }
  }
  await step(2, "Load more", async () => {
    const before = await page.locator(CARDS).count();
    check(before >= 60, `only ${before} cards before scroll`);
    await page.locator(CARDS).nth(59).scrollIntoViewIfNeeded();
    await page.waitForFunction((before) => document.querySelectorAll("#content .card[data-key]").length > before,
      before, { timeout: STEP_TIMEOUT_MS });
  });
  await step(3, "Category / All", async () => {
    const oldCard = await page.locator(CARDS).first().elementHandle();
    try {
      await page.locator('#cattabs .cattab[data-i="1"]').click();
      // The tab re-renders the wall: its cards, or the empty-state line
      // when that category has nothing right now (not a failure).
      await page.waitForFunction((old) => !old.isConnected &&
        document.querySelector('#cattabs .cattab[data-i="1"].active') &&
        document.querySelector("#content .card[data-key], #content .empty"), oldCard,
      { timeout: STEP_TIMEOUT_MS });
    } finally {
      await page.locator('#cattabs .cattab[data-i="0"]').click();
    }
    await page.waitForFunction(() => document.querySelector('#cattabs .cattab[data-i="0"].active') &&
      document.querySelector("#content .card[data-key]"), null, { timeout: STEP_TIMEOUT_MS });
  });
  await step(4, "Search / clear", async () => {
    const title = await page.locator(CARDS).first().locator(".title").innerText();
    const words = title.match(/[\p{L}\p{N}]+/gu) || [];
    const word = words.find((w) => w.length >= 3) || words[0];
    check(word, "no title word");
    try {
      await page.locator("#q").fill(word);
      await page.waitForFunction((word) =>
        document.querySelector("#searchcount").textContent.includes('"' + word + '"') &&
        [...document.querySelectorAll("#content .card[data-key] .title")]
          .some((c) => c.textContent.toLowerCase().includes(word.toLowerCase())), word,
      { timeout: STEP_TIMEOUT_MS });
    } finally {
      await page.locator("#q").fill("");
      await page.waitForFunction(() => document.querySelector("#searchcount").textContent === "",
        null, { timeout: STEP_TIMEOUT_MS });
    }
    check(await page.locator(CARDS).count() > 0, "no cards after clearing search");
  });
  await step(5, "Get deal URL (unopened)", async () => {
    const href = await page.locator(CARDS).first().locator("a.getdeal").getAttribute("href");
    check(href, "empty URL");
    const url = new URL(href), name = url.hostname.toLowerCase();
    check(url.protocol === "https:" && name && name !== "wowpriced.com" &&
      !name.endsWith(".wowpriced.com") && name !== SITE_URL.hostname, "invalid or own-site deal URL");
  });
  const support = Boolean(((doc.meta || {}).support || {}).enabled);
  await step(6, support ? "Join page" : "Trial dialog / Escape", async () => {
    if (support) {
      const href = await page.locator("#subscribe").getAttribute("href");
      check(href && href.endsWith("/join"), "Join link must point to /join");
      const response = await readSite("join"), body = await response.text();
      check(response.status === 200, `Join HTTP ${response.status}`);
      check(/<input\b[^>]*type=["']email["']/i.test(body), "Join page missing email field");
      check(body.includes('id="join-step-one"') && body.includes('id="join-email-fields" hidden'),
        "Join page must start on step one");
      if (await page.evaluate(() => innerWidth > 640)) {
        try {
          await page.locator("#subscribe").click();
          await page.locator("#join-prompt").waitFor({ state: "visible" });
          await page.locator("#join-email-choice").click();
          await page.locator('#join-email-fields input[type="email"]').waitFor({ state: "visible" });
          check(await page.locator('#join-email-fields button[type="submit"]').isDisabled(),
            "Continue must be disabled without an email");
          await page.locator("#join-back").click();
          await page.locator("#join-email-choice").waitFor({ state: "visible" });
        } finally { await page.keyboard.press("Escape"); }
        await page.locator("#join-prompt").waitFor({ state: "hidden" });
      }
      return;
    }
    try {
      await page.locator("#subscribe").click();
      await page.locator("#authmodal").waitFor({ state: "visible" });
      await page.locator('#authmodal input[type="email"]').waitFor({ state: "visible" });
    } finally { await page.keyboard.press("Escape"); }
    await page.locator("#authmodal").waitFor({ state: "hidden" });
  });
  const plansExpected = !support && Number((doc.meta || {}).yearly_price_usd) > 0;
  await step(7, plansExpected ? "Yearly choice" : "Yearly choice (n/a)", async () => {
    if (plansExpected) {
      check(/<input\b[^>]*value=["']year["'][^>]*>/i.test(html) && /Yearly/.test(html),
        "missing Yearly plan choice in page source");
    }
  });
  await step(8, "Public pages / hubs", async () => {
    const problems = [];
    async function read(path, kind = "html") {
      try {
        const response = await readSite(path), body = await response.text();
        check(response.status === 200, `HTTP ${response.status}`);
        if (kind === "html") {
          check(/<title\b[^>]*>\s*[^<]+<\/title>/i.test(body), "missing title");
          checkFooter(body, doc.meta || {});
        }
        // XML and plain text have no HTML title.
        if (kind === "xml") check(/<urlset\b/.test(body), "invalid sitemap");
        if (kind === "text") check(/User-agent:/i.test(body), "invalid robots.txt");
        return body;
      } catch (e) { problems.push(`${path}: ${String(e.message).split("\n")[0]}`); return ""; }
    }
    // Cloudflare's extensionless URLs leave room in the request budget for the new pages.
    for (const path of ["about", "how-it-works", "contact", "privacy", "disclosure", "subscribe"]) {
      await read(path);
    }
    const sitemap = await read("sitemap.xml", "xml");
    await read("robots.txt", "text");
    const urls = [...sitemap.matchAll(/<loc>\s*([^<]+)\s*<\/loc>/g)].map((m) => m[1].trim());
    const hubs = urls
      .filter((u) => { try { return /^\/deals\/(?!under-)[^/]+\/$/.test(new URL(u).pathname); } catch { return false; } })
      .slice(0, 3);
    if (hubs.length < 3) problems.push("fewer than 3 category hubs in sitemap");
    for (const hub of hubs) await read(hub);
    const store = urls.find((u) => { try { return /^\/stores\/[^/]+\/$/.test(new URL(u).pathname); } catch { return false; } });
    if (store) await read(store);
    const price = urls.find((u) => { try { return new URL(u).pathname === "/deals/under-25/"; } catch { return false; } });
    if (price) await read(price);
    // With seo.deal_pages_indexable off, deal pages are noindex and left out
    // of the sitemap: visit a live deal's page from deals.json instead.
    dealPage = urls
      .find((u) => { try { return /^\/deal\/[^/]+\/$/.test(new URL(u).pathname); } catch { return false; } })
      || ((doc.deals || []).find((d) => typeof d.deal_path === "string"
        && /^\/deal\/[a-z0-9-]+\/$/.test(d.deal_path)) || {}).deal_path;
    if (!dealPage) problems.push("no deal page in sitemap or deals.json");
    check(!problems.length, problems.join("; "));
  });
  await step(9, "Publish freshness", async () => {
    const age = Date.now() - Date.parse((doc.meta || {}).published_at);
    check(Number.isFinite(age) && age >= 0 && age < 45 * 60000, "publish missing, future, or >=45 minutes old");
  });
  if (device === "desktop") await step(10, "Affiliate links", async () => {
    const config = fs.readFileSync(path.join(__dirname, "../config.yaml"), "utf8");
    const result = checkAmazonTag(doc, amazonTag(config));
    check(result.ok, result.reason);
    const sovrn = checkSovrnLinks(doc);
    check(sovrn.ok, sovrn.reason);
    // Network links (Sovrn onboarding): a report, never a failure (deals end).
    const extra = overrideNote(doc, overrideLinks(config));
    return [result.note || "ok", `sovrn: ${sovrn.links} links`, extra && `network links: ${extra}`].filter(Boolean).join("; ");
  });
  fs.mkdirSync("shots", { recursive: true });
  await step(11, "Home screenshot", async () => {
    await page.evaluate(() => window.scrollTo(0, 0));
    // Home is deliberately just the first screen, never the endless wall.
    await page.screenshot({ path: `shots/${device}-home.png`, fullPage: false });
  });
  if (device === "phone" && support) await step(12, "Support banner screenshot", async () => {
    // The banner waits until no other ask (the in-wall Join box after card
    // 8, the footer) is on screen: scroll a few cards further, step by step.
    for (const n of [7, 11, 15, 19]) {
      await page.locator(CARDS).nth(n).evaluate((card) =>
        window.scrollTo(0, window.scrollY + card.getBoundingClientRect().bottom + 1));
      await page.waitForTimeout(300);
      if (await page.locator("#paywall.support").isVisible()) break;
    }
    await page.locator("#paywall.support").waitFor({ state: "visible" });
    const box = await page.locator("#paywall.support").boundingBox();
    check(box && box.y < 844 && box.y + box.height > 0, "banner outside phone viewport");
    await page.screenshot({ path: "shots/phone-support.png", fullPage: false });
  });
  // Browser visits replace step 8's old HTML fetches: one navigation each,
  // with the same content checks and request/analytics guard as home.
  for (const [name, url] of [["deal", dealPage], ["best-today", "best-deals-today/"]]) {
    await step(name === "deal" ? 13 : 14, `${name} page / screenshot`, async () => {
      check(url, "no deal page in sitemap or deals.json");
      check(new URL(url, SITE).origin === SITE_URL.origin, "off-site URL");
      const response = await page.goto(new URL(url, SITE).href, { waitUntil: "load", timeout: 60000 });
      await page.screenshot({ path: `shots/${device}-${name}.png`, fullPage: true });
      check(response && response.status() === 200, `HTTP ${response && response.status()}`);
      const body = await response.text();
      checkFooter(body, doc.meta || {});
      check(/<title\b[^>]*>\s*[^<]+<\/title>/i.test(body), "missing title");
      check(/<a\b[^>]*href=["']https?:\/\/[^"']+["'][^>]*>Get deal<\/a>/i.test(body), "missing Get deal link");
    });
  }
  // Include errors from every interaction in the home/no-page-errors step.
  if (errors.length) Object.assign(results[0], { ok: false, reason: `page errors: ${errors.length}` });
  return { device, results, shown, errors };
}

(async () => {
  const out = [];
  const say = (s) => { out.push(s); console.log(s); };
  const res = await readSite("deals.json");
  if (!res.ok) throw new Error(`deals.json HTTP ${res.status}`);
  const doc = await res.json();
  const alive = (doc.deals || []).filter((d) => d.status === "alive" && !d.locked);
  const none = alive.filter((d) => !d.image_url);
  const withPhoto = alive.filter((d) => d.image_url);

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await guardPage(page);
  const desktop = await walk(page, doc, "desktop");
  const { shown, errors } = desktop;
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
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true });
  await guardPage(phone);
  const mobile = await walk(phone, doc, "phone");
  await browser.close();
  const broken = withPhoto.filter((d) => failed.has(d.image_url));

  const pct = (n) => (alive.length ? Math.round((100 * n) / alive.length) : 0);
  const good = withPhoto.length - broken.length;
  say(`## Site check ${new Date().toISOString().slice(0, 16)}Z`);
  say(`live deals ${alive.length}, cards rendered ${shown.cards} of ${alive.length} live, clock "${shown.clock}", tagline "${shown.tagline}"`);
  say(`photos showing: ${good} (${pct(good)}%) | no photo: ${none.length} (${pct(none.length)}%) | broken: ${broken.length} (${pct(broken.length)}%)`);
  say(`first screens: ${shown.top} of the top 24 cards show a photo`);
  // Photo floors (config.yaml site_check): a shortfall turns the run red so
  // the hourly failure watch sees it before a visitor does.
  const photos = checkPhotos({ live: alive.length, showing: good, broken: broken.length, firstScreen: shown.top },
    photoRules(fs.readFileSync(path.join(__dirname, "../config.yaml"), "utf8")));
  say(`photo floors: ${photos.ok ? "ok" : "FAIL " + photos.reason}`);
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
  // Category audit: live deals per category with sample titles, so the
  // sorting into tabs can be judged from the real data (all public).
  say("\n### Categories (live deals)");
  const byCat = {};
  for (const d of alive) (byCat[d.category || "other"] = byCat[d.category || "other"] || []).push(d);
  for (const [cat, rows] of Object.entries(byCat).sort((a, b) => b[1].length - a[1].length)) {
    say(`${cat}: ${rows.length}`);
    const step = Math.max(1, Math.floor(rows.length / (cat === "other" ? 25 : 8)));
    for (let i = 0; i < rows.length && i / step < (cat === "other" ? 25 : 8); i += step) {
      say(`  ${rows[i].store} | ${String(rows[i].item).slice(0, 90)}`);
    }
  }
  say("\n### Walk-through");
  for (const walk of [desktop, mobile]) {
    for (const step of walk.results) {
      say(`${walk.device} ${step.n}. ${step.name}: ${step.ok ? step.note || "ok" : "FAIL " + step.reason}`);
    }
  }
  if (!photos.ok || [desktop, mobile].some((walk) => walk.results.some((step) => !step.ok))) process.exitCode = 1;
  if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, out.join("\n") + "\n");
})().catch((e) => { console.error(e); process.exit(1); });
