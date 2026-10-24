// Operator (Nishi) 2026-10-24 ~03:55Z/~04:00Z in real Chromium, entirely offline, at a phone (390x844)
// and a laptop (1440x900): "vacuum" typed on both tools. The analyzer box has no "wowpriced recommends"
// strip (hide_spotter_recommends), the yellow button says "vacuums" (plural_highly_rated) and the two
// buttons are as wide as each other (equal_word_buttons). Switches off: today's box, strip and widths.
// Usage: node spotter_vacuum_browser.cjs <build dir> [<screenshot dir>]
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');
const scratch = process.argv[2], shots = process.argv[3] || scratch;
fs.mkdirSync(scratch, {recursive:true}); fs.mkdirSync(shots, {recursive:true});
const BUILD = `
from pathlib import Path
from unittest.mock import patch
import sys
from tests.test_support import render, NOW
from tests.test_words_analyzer import rows
from dealradar.config import load_config
from dealradar.publish import site
SWITCHES = ("hide_spotter_recommends", "plural_highly_rated", "equal_word_buttons")
for name, on in (("on", True), ("off", False)):
    cfg = load_config()
    cfg.keepa_click_price_rule = cfg.keepa_buybox_rule = "off"
    assert cfg.tools_merge is True and cfg.tool_words_analyzer is True and all(getattr(cfg, k) is True for k in SWITCHES)
    for key in SWITCHES:
        setattr(cfg, key, on)
    (Path(sys.argv[1]) / name).mkdir(parents=True, exist_ok=True)
    with patch.object(site, "util_now", lambda: NOW): render(Path(sys.argv[1]) / name, cfg, rows())
`;
const NOW = new Date('2026-10-12T00:10:00Z');
(async () => {
  let browser;
  try { browser = await chromium.launch({executablePath:'/opt/pw-browsers/chromium', headless:true}); }
  catch (error) { console.log('Chromium unavailable: ' + error.message.split('\n')[0]); process.exitCode = 77; return; }
  try {
    const built = spawnSync('.venv/bin/python', ['-c', BUILD, scratch], {encoding:'utf8'});
    assert.equal(built.status, 0, built.stderr);
    for (const [label, width, height] of [['laptop', 1440, 900], ['phone', 390, 844]]) {
      const context = await browser.newContext({viewport:{width, height}, colorScheme:'light'});
      const errors = [], calls = [];
      let root;
      await context.addInitScript(() => {
        Math.random = () => 0;  // the shortest wait: 10 s
        try { localStorage.setItem('wp.spotter.cookies', '1'); localStorage.setItem('wp.tools.cookies', '1'); } catch (_) {}
      });
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.hostname === 'm.media-amazon.com') return route.fulfill({contentType:'image/svg+xml', body:'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"><rect width="300" height="300" fill="#eef4ff"/></svg>'});
        if (url.origin !== 'https://wowpriced.test') return route.fulfill({status:204, body:''});
        if (url.pathname === '/api/search') { calls.push(url.search); return route.fulfill({status:500, body:''}); }
        let file = path.join(root, url.pathname);
        if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
        if (!fs.existsSync(file)) return route.fulfill({status:404, body:''});
        return route.fulfill({contentType:{'.html':'text/html', '.js':'application/javascript', '.css':'text/css', '.json':'application/json', '.svg':'image/svg+xml'}[path.extname(file)] || 'application/octet-stream', body:fs.readFileSync(file)});
      });
      const page = await context.newPage(); page.on('pageerror', e => errors.push(e.message));
      await page.clock.install({time:NOW});
      const shot = name => page.screenshot({path:path.join(shots, `spotter-vacuum-${label}-${name}.png`), animations:'disabled'});
      const noScroll = async () => { const s = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]); assert(s[0] <= s[1], 'horizontal scroll ' + s); };
      // Each button: [text, href, width, height, text centre - button centre, its text's own width with padding].
      const buttons = () => page.locator('#name-results .words-buttons a').evaluateAll(links => links.map(a => {
        const box = a.getBoundingClientRect(), range = document.createRange(), style = getComputedStyle(a);
        range.selectNodeContents(a);
        const text = range.getBoundingClientRect();
        return [a.innerText.trim(), a.getAttribute('href'), box.width, box.height, text.left + text.width / 2 - (box.left + box.width / 2),
                text.width + parseFloat(style.paddingLeft) + parseFloat(style.paddingRight) + parseFloat(style.borderLeftWidth) + parseFloat(style.borderRightWidth)];
      }));
      const row = () => page.locator('#name-results .words-buttons').evaluate(el => el.getBoundingClientRect().width);
      async function words(url, field, button) {
        await page.goto('https://wowpriced.test' + url); await page.clock.runFor(500);
        await page.locator(field).fill('vacuum'); await page.locator(button).click();
        await page.clock.runFor(25100);  // past any analyzer wait
        await page.locator('#name-results .words-deal').first().waitFor();
        await page.locator('#name-results').evaluate(el => el.scrollIntoView({block:'center', behavior:'instant'}));
        return buttons();
      }
      const TOOLS = [['/deal-spotter/', '#q', '.hero-box button'], ['/amazon-discount-finder/', '#q', '.hero-box button'],
                     ['/review-checker/', '#hero-input', '#hero-form button']];

      // Switches off: the strip in the spotter's analyzer box, "vacuum", the buttons at their own widths.
      root = path.join(scratch, 'off', 'site');
      await page.goto('https://wowpriced.test/deal-spotter/'); await page.clock.runFor(500);
      await page.locator('#q').fill('vacuum'); await page.locator('.hero-box button').click();
      assert(await page.locator('#analyze-modal').isVisible());
      assert.equal(await page.locator('#analyze-modal .rec-card').count(), 3);
      const before = {};
      for (const [url, field, button] of TOOLS) {
        before[url] = await words(url, field, button);
        assert.equal(before[url][1][0], 'See highly rated vacuum on Amazon →');
      }

      // Switches on.
      root = path.join(scratch, 'on', 'site');
      await page.goto('https://wowpriced.test/deal-spotter/'); await page.clock.runFor(500);
      await page.locator('#q').fill('vacuum'); await page.locator('.hero-box button').click();
      const modal = page.locator('#analyze-modal');
      assert(await modal.isVisible()); assert(await page.locator('#name-results').isHidden());
      assert.equal(await page.locator('#analyze-modal :is(.recommends, .rec-card)').count(), 0);
      assert.equal(await page.locator('#modal-product').textContent(), 'vacuum');
      assert.equal(await page.locator('#modal-status').textContent(), 'Searching for deals, please wait..');
      assert.equal(await page.locator('#modal-step').textContent(), 'Searching our verified deals..');
      await page.clock.runFor(5000);
      assert.match(await page.locator('#modal-percent').textContent(), /^5\d\.\d%$/);
      assert.equal(await page.locator('#modal-step').textContent(), 'Finding discounts on Amazon..');
      const deals = page.locator('#modal-deals');
      assert(await deals.isVisible()); assert.equal(await deals.getAttribute('href'), '/');
      assert.equal((await deals.innerText()).trim(), "See today's deals ›");
      // Nothing left between the steps and the button: the box still fits the screen without a scroll.
      const [step, bottom, dialog] = await page.evaluate(() => ['#modal-step', '.modal-bottom', '#analyze-modal'].map(s => document.querySelector(s).getBoundingClientRect()));
      assert(bottom.top - step.bottom >= 0 && bottom.top - step.bottom <= 30, 'step to button ' + (bottom.top - step.bottom));
      assert(dialog.top >= 0 && dialog.bottom <= height, 'analyzer box on screen');
      await noScroll(); await shot('analyzer');
      for (const [url, field, button] of TOOLS) {
        const found = await words(url, field, button), [blue, yellow] = found, [oldBlue, oldYellow] = before[url];
        assert.equal(yellow[0], 'See highly rated vacuums on Amazon →', url); assert.equal(blue[0], oldBlue[0], url);
        assert.equal(yellow[1], oldYellow[1], url); assert.equal(blue[1], oldBlue[1], url);  // the typed words' links
        assert.match(yellow[1], /[?&]k=vacuum&/);
        assert(Math.abs(blue[2] - yellow[2]) <= 1, url + ' widths ' + blue[2] + ' ' + yellow[2]);
        for (const [i, b] of found.entries()) {
          assert(Math.abs(b[4]) <= 1, url + ' text off centre ' + b[4]);
          assert.equal(b[3], before[url][i][3], url + ' height');
        }
        if (label === 'phone') {
          // Phones already stretched both to the full width: kept.
          assert.equal(blue[2], oldBlue[2], url); assert(Math.abs(blue[2] - await row()) <= 1, url + ' full width');
        } else {
          // The wider one's own width, not the box's.
          assert(Math.abs(yellow[2] - Math.max(blue[5], yellow[5])) <= 1, url + ' wider one ' + yellow[2] + ' ' + yellow[5]);
          assert(Math.abs(oldBlue[2] - oldYellow[2]) > 1, url + ' off: unequal');
        }
        await noScroll();
        if (url !== '/amazon-discount-finder/') await shot(url === '/deal-spotter/' ? 'words' : 'review-words');
      }
      assert.deepEqual(calls, [], 'typed words must not call /api/search');
      assert.deepEqual(errors, []);
      await context.close();
      console.log(`${width}x${height}: ok`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
