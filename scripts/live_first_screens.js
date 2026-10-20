const {routeRobotSearch, FIXTURE_TITLE} = require('./robot_search.cjs');
// Read-only look at the live homepage the way a visitor sees it: the first 24
// cards on a phone and on desktop (title, price, store), clothing per six,
// the empty-search experience, the logo after a search, and /api/search.
// Analytics and ad tags are blocked; nothing is clicked except our own logo.
const { chromium, webkit } = require("playwright");

const SITE = process.env.SITE_URL || "https://wowpriced.com";
const BLOCK = /googletagmanager|google-analytics|googleadservices|doubleclick|googlesyndication|facebook\.net|facebook\.com\/tr|analytics\.tiktok|pinimg\.com\/ct|bat\.bing|clarity\.ms/;
const CLOTHING = /\b(sweatshirts?|hoodies?|crewnecks?|pullovers?|t-shirts?|tees?|shirts?|jackets?|coats?|vests?|sweaters?|cardigans?|dress(es)?|skirts?|jeans|pants|joggers|leggings|shorts|bras?|underwear|socks|pajamas|sneakers|shoes|boots|sandals|slippers)\b/i;
const CARDS = "#content .card[data-key]";

async function firstCards(page) {
  await page.waitForFunction(sel => document.querySelectorAll(sel).length >= 24, CARDS, { timeout: 60000 });
  return page.$$eval(CARDS, cards => cards.slice(0, 24).map(card => ({
    title: (card.querySelector(".title")?.textContent || "").trim().replace(/\s+/g, " "),
    price: (card.querySelector(".now")?.textContent || "").trim(),
    store: (card.querySelector(".storechip")?.textContent || "").trim(),
  })));
}

function report(label, cards) {
  console.log(`\n=== ${label}: first ${cards.length} cards ===`);
  cards.forEach((c, i) => console.log(`${String(i + 1).padStart(2)}. ${c.price.padEnd(8)} ${c.store.slice(0, 14).padEnd(14)} ${CLOTHING.test(c.title) ? "[clothing] " : ""}${c.title.slice(0, 70)}`));
  const windows = [];
  for (let i = 0; i < cards.length; i += 6) windows.push(cards.slice(i, i + 6).filter(c => CLOTHING.test(c.title)).length);
  console.log(`clothing per 6 cards: ${windows.join(", ")} (rule: at most 2)`);
}

(async () => {
  const browser = await chromium.launch();
  try {
    for (const [label, options] of [["phone", { viewport: { width: 390, height: 844 }, isMobile: true }],
                                    ["desktop", { viewport: { width: 1440, height: 900 } }]]) {
      const page = await browser.newPage({...options, serviceWorkers: "block"});
      await page.route("**/*", route => BLOCK.test(route.request().url()) ? route.abort() : route.continue());
      await routeRobotSearch(page);
      await page.goto(SITE + "/", { waitUntil: "domcontentloaded", timeout: 45000 });
      report(label, await firstCards(page));
      if (label === "desktop") {
        // Keep scrolling: Trending must keep loading cards (operator 2026-10-17 "it just stops").
        const counts = [];
        for (let i = 0; i < 12; i++) {
          counts.push(await page.$$eval(CARDS, cards => cards.length));
          await page.mouse.wheel(0, 30000); await page.waitForTimeout(900);
        }
        const more = await page.evaluate(() => [...document.querySelectorAll("button")].some(b => /show more deals/i.test(b.textContent) && b.offsetParent !== null));
        const facts = await page.evaluate(async () => {
          const j = await fetch("/deals.json?t=" + Date.now(), {cache: "no-store"}).then(r => r.json()).catch(e => ({error: String(e)}));
          const deals = Array.isArray(j.deals) ? j.deals : [];
          const count = (f) => deals.filter(f).length;
          return {keys: Object.keys(j).slice(0, 12), deals: deals.length, teaser: Array.isArray(j.teaser) ? j.teaser.length : null,
                  dead: count(d => d.status === "dead"), noPhoto: count(d => !d.image), openBox: count(d => (d.conditions || []).some(c => /used|open_box/.test(c))),
                  clickUnknown: count(d => d.click_price_unknown), locked: count(d => d.locked), tab: (document.querySelector('[aria-selected="true"], .tab.on, .active') || {}).textContent || "",
                  q: (document.getElementById("q") || {}).value || "", searchcount: (document.getElementById("searchcount") || {}).textContent || "",
                  grids: [...document.querySelectorAll("#content .grid")].map(g => g.querySelectorAll(".card").length)};
        });
        console.log(`deals.json and page: ${JSON.stringify(facts)}`);
        console.log(`\nscrolling Trending: cards ${counts.join(" -> ")}; Show more deals button ${more ? "visible" : "not visible"}`);
        await page.evaluate(() => window.scrollTo(0, 0));
        // Empty search, then the logo: a visitor must land back on the fresh homepage.
        await page.fill("#q", "hijab");
        await page.waitForTimeout(2500);
        const empty = await page.evaluate(() => ({
          amazon: !!document.querySelector("#amazon-results section"),
          button: !!document.querySelector(".search-amazon a"),
          cards: document.querySelectorAll("#content .card[data-key]").length,
        }));
        if (empty.amazon) throw new Error("empty robot search rendered Amazon cards");
        console.log(`\nsearch "hijab": Amazon results ${empty.amazon ? "shown" : "not shown"}; Search Amazon button ${empty.button ? "yes" : "no"}; deal cards ${empty.cards}`);
        await page.click("h1.brand a");
        await page.waitForTimeout(2500);
        const after = await page.evaluate(sel => ({ q: document.getElementById("q").value, cards: document.querySelectorAll(sel).length }), CARDS);
        console.log(`after logo click: search box "${after.q}", cards ${after.cards} ${after.q ? "(BUG: search kept)" : "(fresh homepage)"}`);
      }
      await page.close();
    }
    // Search with no deal match, in Chromium and in WebKit (Safari's engine): Amazon cards,
    // the big button, and the affiliate tag on the page the button opens (that request is
    // aborted: the check never loads Amazon).
    const engines = [["chromium", browser]];
    try { engines.push(["webkit (Safari)", await webkit.launch()]); } catch (error) { console.log(`\nwebkit unavailable: ${error.message.split("\n")[0]}`); }
    for (const [name, engine] of engines) {
      for (const [label, options] of [["phone", { viewport: { width: 390, height: 844 }, ...(name === "chromium" ? { isMobile: true } : {}) }],
                                      ["desktop", { viewport: { width: 1440, height: 900 } }]]) {
        const context = await engine.newContext({...options, serviceWorkers: "block"}), opened = [];
        await context.route("**/*", route => {
          const url = route.request().url();
          if (/^https:\/\/(www\.)?amazon\.com\//.test(url)) { opened.push(url); return route.abort(); }
          return BLOCK.test(url) ? route.abort() : route.continue();
        });
        await routeRobotSearch(context, true);
        const page = await context.newPage();
        await page.goto(SITE + "/", { waitUntil: "domcontentloaded", timeout: 45000 });
        await firstCards(page);
        await page.fill("#q", "cupping set");
        let cards = 0;
        try { await page.locator("#amazon-results article").first().waitFor({ state: "visible", timeout: 12000 }); cards = await page.locator("#amazon-results article").count(); } catch (e) { /* reported below */ }
        await page.waitForFunction(() => {
          const photos = [...document.querySelectorAll("#amazon-results article img")];
          return photos.length === 1 && photos.every(img => img.complete && img.naturalWidth > 0);
        }, null, {timeout: 12000});
        const state = await page.evaluate(() => ({
          heading: (document.getElementById("search-recovery-title") || {}).textContent || "",
          button: ((document.querySelector(".search-amazon-top .getdeal") || document.querySelector(".search-amazon a") || document.querySelector("#analyze-search")) || {}).innerText || "",
          photos: [...document.querySelectorAll("#amazon-results article img")].filter(img => img.complete && img.naturalWidth > 0).length,
          overflow: document.documentElement.scrollWidth > innerWidth,
        }));
        if (cards !== 1 || !(await page.locator("#amazon-results").innerText()).includes(FIXTURE_TITLE))
          throw new Error("robot search fixture card missing");
        if (state.photos !== 1) throw new Error("robot search fixture photo missing");
        let button = page.locator(".search-amazon-top .getdeal:visible, .search-amazon a:visible").first();
        if (!await button.count()) button = page.locator("#analyze-search:visible");
        if (await button.count()) { await button.click().catch(() => {}); await page.waitForTimeout(1500); }
        const tagged = opened.some(url => url.includes("tag=wowpriced-20"));
        console.log(`\n[${name} ${label}] search "cupping set": Amazon cards ${cards}, photos loaded ${state.photos}; heading "${state.heading}"; button "${state.button.trim()}"; ` +
          `opens Amazon ${opened.length ? (tagged ? "WITH tag=wowpriced-20" : "WITHOUT our tag (BUG)") : "(no click captured)"}; ${state.overflow ? "SIDEWAYS SCROLL (BUG)" : "no sideways scroll"}`);
        await context.close();
      }
      if (engine !== browser) await engine.close();
    }
    // One fixed page-zero query; the deployed search cache retains it for 6 h.
    for (const q of ["cupping"]) {
      const res = await fetch(`${SITE}/api/search?q=${encodeURIComponent(q)}`, { headers: { "User-Agent": "wowpriced-live-check/1.0 (+https://wowpriced.com; hello@wowpriced.com)" } });
      const body = await res.text();
      let summary = body.slice(0, 200);
      try { const j = JSON.parse(body); summary = `items ${Array.isArray(j.items) ? j.items.length : "?"}, reason ${j.reason || "-"}, detail ${j.detail || "-"}${j.items && j.items[0] ? `, first "${String(j.items[0].title).slice(0, 50)}" $${(j.items[0].price_cents / 100).toFixed(2)}` : ""}`; } catch (e) { /* print raw head */ }
      console.log(`/api/search?q=${q}: HTTP ${res.status}; ${summary}`);
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
