const assert = require("node:assert/strict"), fs = require("node:fs");
const source = fs.readFileSync(require("node:path").join(__dirname, "../scripts/site_check.js"), "utf8");
const code = source.slice(source.indexOf('  fs.mkdirSync("shots"'),
  source.indexOf("  // Include errors from every interaction"));
const run = new (Object.getPrototypeOf(async function () {}).constructor)(
  "step", "page", "device", "support", "fs", "dealPage", "check", "SITE", "SITE_URL", "CARDS", "checkFooter", "doc", code);

(async () => {
  for (const [device, support] of [["desktop", true], ["phone", true], ["phone", false]]) {
    const shots = [], visits = [], scrolls = [], footers = [];
    const site = "https://wowpriced.com/";
    const page = {
      waitForTimeout: async () => {},
      evaluate: async action => {
        global.window = { scrollY: 0, scrollTo: (x, y) => scrolls.push(y) };
        try { action(); } finally { delete global.window; }
      },
      locator: selector => {
        if (selector === "cards") return { nth: index => {
          assert.equal(index, 7);  // the banner shows here, so the loop stops
          return { evaluate: async action => {
            global.window = { scrollY: 0, scrollTo: (x, y) => scrolls.push(y) };
            try { action({ getBoundingClientRect: () => ({ bottom: 2000 }) }); }
            finally { delete global.window; }
          } };
        } };
        assert.equal(selector, "#paywall.support");
        return { isVisible: async () => true,
          waitFor: async options => assert.deepEqual(options, { state: "visible", timeout: 150000 }),  // panel opens after support.panel_delay_s (120 s)
          boundingBox: async () => ({ y: 600, height: 200 }) };
      },
      goto: async (url, options) => {
        visits.push(url);
        assert.equal(options.waitUntil, "load");
        return { status: () => 200, text: async () => '<title>Deal</title><a href="https://store.test/item">Get deal</a>' };
      },
      screenshot: async options => shots.push(options),
    };
    await run(async (n, name, action) => action(), page, device, support,
      { mkdirSync: (dir, options) => { assert.equal(dir, "shots"); assert.equal(options.recursive, true); } },
      site + "deal/first-12345678/", (ok, reason) => { if (!ok) throw new Error(reason); },
      site, new URL(site), "cards", (html, meta) => {footers.push(html); assert.deepEqual(meta, {});}, {meta:{}});
    const expected = [{ path: `shots/${device}-home.png`, fullPage: false }];
    if (device === "phone" && support) expected.push({ path: "shots/phone-support.png", fullPage: false });
    expected.push({ path: `shots/${device}-deal.png`, fullPage: true },
      { path: `shots/${device}-best-today.png`, fullPage: true });
    assert.deepEqual(shots, expected);
    assert.deepEqual(visits, [site + "deal/first-12345678/", site + "best-deals-today/"]);
    assert.equal(footers.length, 2);
    assert.deepEqual(scrolls, device === "phone" && support ? [0, 2001] : [0]);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
