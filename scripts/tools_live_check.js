// Read-only live look at Deal Spotter and Review Checker (operator 2026-10-22:
// "I'll test it and you can make changes while its live"): each tool's start
// page on a phone and a laptop, then one real grade per tool and size. The
// lookups go through the site's own /api/search caps (and its edge cache after
// the first). Screenshots land in tools-live/ for the run's artifact.
// Analytics and ad tags are blocked; nothing else is clicked.
const fs = require("fs");
const { chromium } = require("playwright");

const SITE = process.env.SITE_URL || "https://wowpriced.com";
const BLOCK = /googletagmanager|google-analytics|googleadservices|doubleclick|googlesyndication|facebook\.net|facebook\.com\/tr|analytics\.tiktok|pinimg\.com\/ct|bat\.bing|clarity\.ms/;
const OUT = "tools-live";
const TOOLS = [
  {name: "deal-spotter", path: "/deal-spotter/", input: "#q", result: "#spotter-result",
   message: "#input-error", grade: "#grade-letter", label: "#grade-label", price: "#result-price"},
  {name: "review-checker", path: "/review-checker/", input: "#review-input", result: "#review-report",
   message: "#input-message", grade: "#review-report .grade-letter", label: "#review-report .grade-box p",
   terms: "#review-terms[open]"},
];

async function pickAsin() {
  const given = (process.env.ASIN || "").trim().toUpperCase();
  if (/^(?=.*\d)[A-Z0-9]{10}$/.test(given)) return given;
  // The first Amazon deal in the slim file Deal Spotter reads, else in deals.json.
  // Each file's Amazon row count is printed: an empty prices file is itself a finding.
  for (const file of ["/deal-spotter-prices.json", "/deals.json"]) {
    const response = await fetch(SITE + file, {headers: {"User-Agent": "wowpriced-tools-check/1.0"}});
    const doc = response.ok ? await response.json() : {};
    const rows = [...(doc.deals || []), ...(doc.spotlight || [])];
    const asins = rows.map(row => (String(row.url || "").match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})(?:[/?#]|$)/i) || [])[1])
      .filter(Boolean);
    console.log(`${file}: HTTP ${response.status}, ${rows.length} rows, ${asins.length} Amazon product links`);
    if (asins.length) return asins[0].toUpperCase();
  }
  throw new Error("no Amazon deal found in deal-spotter-prices.json or deals.json");
}

// Why deals.json Amazon rows are or are not in the prices file: one count per
// check deal_spotter.price_index applies. Informational; never fails the run.
async function amazonBreakdown() {
  const response = await fetch(SITE + "/deals.json", {headers: {"User-Agent": "wowpriced-tools-check/1.0"}});
  const deals = response.ok ? ((await response.json()).deals || []) : [];
  const host = row => { try { return new URL(String(row.url || "")).hostname; } catch { return ""; } };
  const amazon = deals.filter(row => /^(?:(?:www|smile|m)\.)?amazon\.com$/.test(host(row)));
  const count = test => amazon.filter(test).length;
  console.log(`deals.json: ${deals.length} rows, ${count(() => true)} with an amazon.com link ` +
    `(${deals.filter(row => /amzn|amazon/.test(host(row))).length} with any Amazon host); of those: ` +
    `not alive ${count(row => row.status !== "alive")}, locked ${count(row => row.locked)}, ` +
    `price_unconfirmed ${count(row => row.price_unconfirmed)}, price_aged ${count(row => row.price_aged)}, ` +
    `click_price_unknown ${count(row => row.click_price_unknown)}, ` +
    `"not yet confirmed" ${count(row => String(row.headline || "").includes("not yet confirmed"))}`);
}

(async () => {
  fs.mkdirSync(OUT, {recursive: true});
  await amazonBreakdown().catch(e => console.log(`deals.json breakdown: ${String(e.message || e).slice(0, 120)}`));
  const asin = await pickAsin();
  console.log(`checking ASIN ${asin} on ${SITE}`);
  const browser = await chromium.launch();
  let failures = 0;
  try {
    for (const [size, options] of [["phone", {viewport: {width: 390, height: 844}, isMobile: true, deviceScaleFactor: 2}],
                                   ["laptop", {viewport: {width: 1280, height: 800}}]]) {
      for (const tool of TOOLS) {
        const page = await browser.newPage({...options, serviceWorkers: "block"});
        const errors = [];
        page.on("pageerror", e => errors.push(String(e.message).slice(0, 120)));
        await page.route("**/*", route => BLOCK.test(route.request().url()) ? route.abort() : route.continue());
        const response = await page.goto(SITE + tool.path, {waitUntil: "domcontentloaded", timeout: 45000});
        await page.waitForTimeout(1500);
        // Review Checker's terms notice opens about 1.5 s after load on a first visit.
        if (tool.terms && await page.waitForSelector(tool.terms, {timeout: 5000}).catch(() => null)) {
          await page.screenshot({path: `${OUT}/${tool.name}-${size}-terms.png`});
          await page.click("#cookies-ok");
        }
        await page.screenshot({path: `${OUT}/${tool.name}-${size}-start.png`});
        await page.fill(tool.input, asin);
        // Observe the page's lookup; never issue another API request.
        const apiItem = page.waitForResponse(response => {
          const url = new URL(response.url());
          return url.origin === new URL(SITE).origin && url.pathname === "/api/search" &&
            url.searchParams.get("asin") === asin &&
            url.searchParams.get("grade") === (tool.name === "deal-spotter" ? "deal" : "reviews");
        }, {timeout: 75000}).then(response => response.json())
          .then(data => data?.items?.[0] || {}).catch(() => ({}));
        await page.press(tool.input, "Enter");
        let grade = "", label = "", price = "", shipping = "", note = "";
        try {
          await page.waitForSelector(`${tool.result}:not([hidden])`, {timeout: 75000});
          await page.waitForTimeout(800);
          grade = (await page.textContent(tool.grade) || "").trim();
          label = (await page.textContent(tool.label) || "").trim();
          // The shown price (public product facts), e.g. "$153.84 + $25.15 shipping".
          if (tool.price) price = (await page.textContent(tool.price) || "").replace(/\s+/g, " ").replace(/(\S)(\+|incl\.)/, "$1 $2").trim();
          // The note under it: "+ $25.15 shipping", "incl. shipping" (Buy Box price with shipping, C16) or none.
          const shippingNote = tool.price && page.locator(`${tool.price} .result-shipping`);
          if (shippingNote && await shippingNote.count()) shipping = ((await shippingNote.first().textContent()) || "").trim();
          await page.screenshot({path: `${OUT}/${tool.name}-${size}-result.png`});
        } catch (_) {
          note = (await page.textContent(tool.message).catch(() => "") || "").trim() || "no result within 75 s";
          await page.screenshot({path: `${OUT}/${tool.name}-${size}-no-result.png`});
        }
        const ok = response && response.status() === 200 && ["A", "B", "C"].includes(grade) && !errors.length;
        if (!ok) failures++;
        console.log(`${tool.name} ${size}: HTTP ${response && response.status()} grade=${grade || "-"} "${label}"` +
                    `${price ? " price=" + JSON.stringify(price) + " shipping=" + JSON.stringify(shipping || "none") : ""}` +
                    `${note ? " note=" + JSON.stringify(note) : ""}${errors.length ? " page errors: " + errors.join(" | ") : ""}`);
        const item = await apiItem;
        console.log(`${tool.name} api: price_cents=${item.price_cents ?? "none"} ` +
                    `shipping_cents=${item.shipping_cents ?? "none"} ` +
                    `price_includes_shipping=${item.price_includes_shipping ?? "none"} ` +
                    `list_price_cents=${item.list_price_cents ?? "none"} as_of=${item.as_of ?? "none"}`);
        await page.close();
      }
    }
  } finally {
    await browser.close();
  }
  console.log(failures ? `tools live check: ${failures} problem(s)` : "tools live check: ok");
  process.exitCode = failures ? 1 : 0;
})().catch(error => { console.error(String(error.message || error)); process.exitCode = 1; });
