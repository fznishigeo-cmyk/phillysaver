// Read-only look at the live homepage the way a visitor sees it: the first 24
// cards on a phone and on desktop (title, price, store), clothing per six,
// the empty-search experience, the logo after a search, and /api/search.
// Analytics and ad tags are blocked; nothing is clicked except our own logo.
const { chromium } = require("playwright");

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
      const page = await browser.newPage(options);
      await page.route("**/*", route => BLOCK.test(route.request().url()) ? route.abort() : route.continue());
      await page.goto(SITE + "/", { waitUntil: "domcontentloaded", timeout: 45000 });
      report(label, await firstCards(page));
      if (label === "desktop") {
        // Empty search, then the logo: a visitor must land back on the fresh homepage.
        await page.fill("#q", "hijab");
        await page.waitForTimeout(2500);
        const empty = await page.evaluate(() => ({
          amazon: !!document.querySelector("#amazon-results section"),
          button: !!document.querySelector(".search-amazon a"),
          cards: document.querySelectorAll("#content .card[data-key]").length,
        }));
        console.log(`\nsearch "hijab": Amazon results ${empty.amazon ? "shown" : "not shown"}; Search Amazon button ${empty.button ? "yes" : "no"}; deal cards ${empty.cards}`);
        await page.click("h1.brand a");
        await page.waitForTimeout(2500);
        const after = await page.evaluate(sel => ({ q: document.getElementById("q").value, cards: document.querySelectorAll(sel).length }), CARDS);
        console.log(`after logo click: search box "${after.q}", cards ${after.cards} ${after.q ? "(BUG: search kept)" : "(fresh homepage)"}`);
      }
      await page.close();
    }
    for (const q of ["cupping", "hijab"]) {
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
