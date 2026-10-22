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
   message: "#input-error", grade: "#grade-letter", label: "#grade-label"},
  {name: "review-checker", path: "/review-checker/", input: "#review-input", result: "#review-report",
   message: "#input-message", grade: "#review-report .grade-letter", label: "#review-report .grade-box p"},
];

async function pickAsin() {
  const given = (process.env.ASIN || "").trim().toUpperCase();
  if (/^(?=.*\d)[A-Z0-9]{10}$/.test(given)) return given;
  // The first verified Amazon deal on the live site (the slim file Deal Spotter reads).
  const response = await fetch(SITE + "/deal-spotter-prices.json", {headers: {"User-Agent": "wowpriced-tools-check/1.0"}});
  const doc = await response.json();
  for (const row of [...(doc.deals || []), ...(doc.spotlight || [])]) {
    const match = String(row.url || "").match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})(?:[/?#]|$)/i);
    if (match) return match[1].toUpperCase();
  }
  throw new Error("no Amazon deal found in deal-spotter-prices.json");
}

(async () => {
  fs.mkdirSync(OUT, {recursive: true});
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
        await page.screenshot({path: `${OUT}/${tool.name}-${size}-start.png`});
        await page.fill(tool.input, asin);
        await page.press(tool.input, "Enter");
        let grade = "", label = "", note = "";
        try {
          await page.waitForSelector(`${tool.result}:not([hidden])`, {timeout: 75000});
          await page.waitForTimeout(800);
          grade = (await page.textContent(tool.grade) || "").trim();
          label = (await page.textContent(tool.label) || "").trim();
          await page.screenshot({path: `${OUT}/${tool.name}-${size}-result.png`});
        } catch (_) {
          note = (await page.textContent(tool.message).catch(() => "") || "").trim() || "no result within 75 s";
          await page.screenshot({path: `${OUT}/${tool.name}-${size}-no-result.png`});
        }
        const ok = response && response.status() === 200 && ["A", "B", "C"].includes(grade) && !errors.length;
        if (!ok) failures++;
        console.log(`${tool.name} ${size}: HTTP ${response && response.status()} grade=${grade || "-"} "${label}"` +
                    `${note ? " note=" + JSON.stringify(note) : ""}${errors.length ? " page errors: " + errors.join(" | ") : ""}`);
        await page.close();
      }
    }
  } finally {
    await browser.close();
  }
  console.log(failures ? `tools live check: ${failures} problem(s)` : "tools live check: ok");
  process.exitCode = failures ? 1 : 0;
})().catch(error => { console.error(String(error.message || error)); process.exitCode = 1; });
