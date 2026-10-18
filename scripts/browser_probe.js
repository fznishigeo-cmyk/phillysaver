const {routeRobotSearch} = require('./robot_search.cjs');
// browser_probe.js: open the live site the way a visitor does, scroll down,
// and record for a while what moves: layout shifts (with the elements that
// moved), scroll position, page height and which nodes change. Read-only;
// analytics and ad tags are blocked so the probe is not counted as a visit.
const { chromium } = require("playwright");

const SITE = process.env.SITE || "https://wowpriced.com/";
const SECONDS = Number(process.env.SECONDS || 60);
const BLOCK = /googletagmanager|google-analytics|facebook\.net|facebook\.com\/tr|analytics\.tiktok|pinimg\.com\/ct|bat\.bing/;

(async () => {
  const browser = await chromium.launch();
  for (const vp of [{ name: "desktop", width: 1440, height: 900 }, { name: "phone", width: 390, height: 844, mobile: true }]) {
    const page = await browser.newPage({ serviceWorkers: "block", viewport: { width: vp.width, height: vp.height }, isMobile: !!vp.mobile });
    await page.route(BLOCK, (r) => r.abort());
    await routeRobotSearch(page);
    await page.addInitScript(() => {
      window.__shifts = [];
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) {
          const who = (e.sources || []).map((s) => {
            const n = s.node; if (!n || n.nodeType !== 1) return "?";
            return n.tagName.toLowerCase() + (n.id ? "#" + n.id : "") +
              (n.className && typeof n.className === "string" ? "." + n.className.trim().split(/\s+/).slice(0, 3).join(".") : "") +
              ` ${Math.round(s.previousRect.y)}->${Math.round(s.currentRect.y)}`;
          });
          window.__shifts.push([Math.round(e.startTime), +e.value.toFixed(4), e.hadRecentInput, who.slice(0, 4).join(" | ")]);
        }
      }).observe({ type: "layout-shift", buffered: true });
    });
    await page.goto(SITE, { waitUntil: "load", timeout: 60000 });
    await page.waitForTimeout(6000);
    await page.evaluate(() => window.scrollTo(0, 2600));
    await page.waitForTimeout(2000);
    const start = await page.evaluate(() => {
      window.__shifts = [];
      window.__muts = {};
      new MutationObserver((ms) => {
        for (const m of ms) {
          const el = m.target.nodeType === 1 ? m.target : m.target.parentElement;
          const id = el ? el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/)[0] : "") : "?";
          const k = m.type + " " + id + (m.attributeName ? " @" + m.attributeName : "");
          window.__muts[k] = (window.__muts[k] || 0) + 1;
        }
      }).observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
      window.__track = [];
      setInterval(() => window.__track.push([Math.round(performance.now()), Math.round(scrollY), document.documentElement.scrollHeight]), 500);
      return Math.round(scrollY);
    });
    await page.waitForTimeout(SECONDS * 1000);
    const r = await page.evaluate(() => ({ shifts: window.__shifts, muts: window.__muts, track: window.__track }));
    const ys = []; for (const [t, y, h] of r.track) { const k = y + "/" + h; if (!ys.length || ys[ys.length - 1][1] !== k) ys.push([t, k]); }
    console.log(`\n=== ${vp.name} ${vp.width}x${vp.height}: scrolled to ${start}, watched ${SECONDS}s`);
    console.log("scrollY/pageHeight changes:", ys.map(([t, k]) => `${t}ms:${k}`).join("  ") || "none");
    console.log("layout shifts (ms, score, after-input, moved elements):");
    for (const s of r.shifts) console.log("  " + s.join("  "));
    if (!r.shifts.length) console.log("  none");
    console.log("DOM changes (count, kind):");
    for (const [k, v] of Object.entries(r.muts).sort((a, b) => b[1] - a[1]).slice(0, 15)) console.log(`  ${v}  ${k}`);
    // A card under a resting pointer must sit still (a :hover lift made it
    // bob on its own): park the mouse 1px inside a card's bottom edge.
    if (!vp.mobile) {
      const box = await page.evaluate(() => {
        const c = document.querySelectorAll("#content .card[data-key]")[9];
        if (!c) return null;
        c.scrollIntoView({ block: "center" }); c.id = "probecard";
        const r = c.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.bottom - 1 };
      });
      if (box) {
        await page.mouse.move(box.x, box.y);
        const moves = await page.evaluate(() => new Promise((done) => {
          const c = document.getElementById("probecard"); let last = null, n = 0;
          const t = setInterval(() => { const k = getComputedStyle(c).transform; if (k !== last) { n++; last = k; } }, 50);
          setTimeout(() => { clearInterval(t); done(n - 1); }, 5000);
        }));
        console.log(`card under a resting mouse moved ${moves} times in 5s (0 = steady)`);
      }
    }
    await page.close();
  }
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
