// tool_words_analyzer (operator 2026-10-23 ~20:05Z, ~20:10Z) in real Chromium, entirely offline, at a
// phone (390x844) and a laptop (1280x800): typed words open the link's analyzer box on both tools,
// wait as long as a link, then show today's words box; no /api/search call. Switch off: the box at once.
// Usage: node words_analyzer_browser.cjs <build dir> [<screenshot dir>]
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
for name, on in (("on", True), ("off", False)):
    cfg = load_config()
    cfg.keepa_click_price_rule = cfg.keepa_buybox_rule = "off"
    assert cfg.tools_merge is True and cfg.tool_words_analyzer is True
    cfg.tool_words_analyzer = on
    (Path(sys.argv[1]) / name).mkdir(parents=True, exist_ok=True)
    with patch.object(site, "util_now", lambda: NOW): render(Path(sys.argv[1]) / name, cfg, rows())
`;
const NOW = new Date('2026-10-12T00:10:00Z'), ASIN = 'B0MERGE001';
// Each tool's two links (word_links, operator 2026-10-23 A51/A52): [blue, yellow], no $50 cap.
const LINKS = {review:['https://www.amazon.com/s?k=vacuum&rh=p_n_deal_type%3A210906366011&tag=wowpriced-20',
  'https://www.amazon.com/s?k=vacuum&rh=p_n_deal_type%3A23566065011&s=exact-aware-popularity-rank&tag=wowpriced-20'],
  spotter:['https://www.amazon.com/s?k=vacuum&rh=p_n_deal_type%3A23566064011&tag=wowpriced-20',
  'https://www.amazon.com/s?k=vacuum&rh=p_n_deal_type%3A210906366011&s=exact-aware-popularity-rank&tag=wowpriced-20']};
const SPOTTER_STEPS = ['Searching our verified deals..', 'Finding discounts on Amazon..', 'Finding top-rated picks..'];
(async () => {
  let browser;
  try { browser = await chromium.launch({executablePath:'/opt/pw-browsers/chromium', headless:true}); }
  catch (error) { console.log('Chromium unavailable: ' + error.message.split('\n')[0]); process.exitCode = 77; return; }
  try {
    const built = spawnSync('.venv/bin/python', ['-c', BUILD, scratch], {encoding:'utf8'});
    assert.equal(built.status, 0, built.stderr);
    for (const [width, height] of [[390, 844], [1280, 800]]) {
      const context = await browser.newContext({viewport:{width, height}, colorScheme:'light'});
      const errors = [], calls = [];
      let root = path.join(scratch, 'on', 'site');
      await context.addInitScript(() => {
        Math.random = () => 0;  // the shortest wait: 10 s, as a link's
        try { localStorage.setItem('wp.spotter.cookies', '1'); localStorage.setItem('wp.tools.cookies', '1'); } catch (_) {}
        document.addEventListener('click', e => { const a = e.target.closest && e.target.closest('a[href^="https://www.amazon.com/"]');
          if (a) e.preventDefault(); }, true);
        let wpTrack; window.tracked = [];
        Object.defineProperty(window, 'wpTrack', {configurable:true, set(fn) { wpTrack = fn; }, get() {
          return wpTrack && ((name, params) => { window.tracked.push([name, params]); return wpTrack(name, params); });
        }});
      });
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.hostname === 'm.media-amazon.com') return route.fulfill({contentType:'image/svg+xml', body:'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"><rect width="300" height="300" fill="#eef4ff"/></svg>'});
        if (url.origin !== 'https://wowpriced.test') return route.fulfill({status:204, body:''});
        if (url.pathname === '/api/search') {
          calls.push(url.search);
          const asin = url.searchParams.get('asin'), as_of = new Date(NOW.getTime() - 4 * 60000).toISOString();
          const item = {asin, title:'Wireless over-ear headphones with a folding design', image:'https://m.media-amazon.com/images/I/fixture.jpg',
            price_cents:4699, list_price_cents:5999, as_of};
          return route.fulfill({contentType:'application/json', body:JSON.stringify(url.searchParams.get('grade') === 'deal' ?
            {items:[item], deal_grade:{grade:'B', label:'Good price', facts:['21% below the list price']}} :
            {items:[item], review:{grade:'A', label:'Great — a strong set of review signals', checks:[]}, price_check:{grade:'B', label:'Good price'}})});
        }
        let file = path.join(root, url.pathname);
        if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
        if (!fs.existsSync(file)) return route.fulfill({status:404, body:''});
        return route.fulfill({contentType:{'.html':'text/html', '.js':'application/javascript', '.css':'text/css', '.json':'application/json', '.svg':'image/svg+xml'}[path.extname(file)] || 'application/octet-stream', body:fs.readFileSync(file)});
      });
      const page = await context.newPage(); page.on('pageerror', e => errors.push(e.message));
      await page.clock.install({time:NOW});
      const shot = name => page.screenshot({path:path.join(shots, `words-${name}-${width}.png`), animations:'disabled'});
      const noScroll = async () => { const s = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]); assert(s[0] <= s[1], 'horizontal scroll ' + s); };
      const text = selector => page.locator(selector).textContent();
      const focused = () => page.evaluate(() => document.activeElement.id || document.activeElement.className);
      async function wordsBox(rated) {
        assert.equal((await page.locator('#name-results .words-title').innerText()).trim(), 'Shopping for vacuum?');
        const discounts = page.locator('#name-results .words-discounts');
        const [blue, yellow] = LINKS[new URL(page.url()).pathname.startsWith('/review-checker/') ? 'review' : 'spotter'];
        assert.equal(await discounts.getAttribute('href'), blue);
        assert.equal(await page.locator('#name-results .name-button').getAttribute('href'), yellow);
        assert.equal((await page.locator('#name-results .name-button').innerText()).trim(), rated);
        assert.equal(await focused(), 'words-discounts');
        await page.locator('#name-results .words-deal').first().waitFor();
        assert.equal(await page.locator('#name-results .words-deals h3').textContent(), 'Matching verified deals on wowpriced');
        const deals = await page.locator('#name-results .words-deal-title').allInnerTexts();
        assert(deals.length === 2 && deals.every(t => /vacuum/i.test(t)), deals.join(' | '));
      }

      // Fake Deal Spotter: the link's blue analyzer (radar, progress, today's deals, ×), steps about the search.
      await page.goto('https://wowpriced.test/deal-spotter/'); await page.clock.runFor(500);
      await page.locator('#q').fill('vacuum'); await page.locator('.hero-box button').click();
      const modal = page.locator('#analyze-modal');
      assert(await modal.isVisible()); assert(await page.locator('#name-results').isHidden());
      assert.equal(await text('#modal-product'), 'vacuum');
      assert.equal(await text('#modal-status'), 'Searching for deals, please wait..');
      assert.equal(await text('#modal-step'), SPOTTER_STEPS[0]);
      assert.equal(await page.locator('.rec-card').count(), 3);
      assert(await page.locator('#modal-deals').isVisible()); assert.equal(await focused(), 'cancel-analysis');
      assert(await page.locator('.radar').isVisible());
      assert.equal(await page.evaluate(() => document.body.style.overflow), 'hidden');
      await page.clock.runFor(5000);
      // The page clock also runs in real time between steps: allow a little drift.
      assert.match(await text('#modal-percent'), /^5\d\.\d%$/); assert.equal(await text('#modal-step'), SPOTTER_STEPS[1]);
      assert.match(await page.locator('[role="progressbar"]').getAttribute('aria-valuenow'), /^5\d$/);
      await noScroll(); await shot('spotter-modal');
      await page.clock.runFor(2000); assert.equal(await text('#modal-step'), SPOTTER_STEPS[2]);
      assert(await modal.isVisible(), 'a link\'s wait: 10 s at the shortest');
      await page.clock.runFor(3100);
      assert(await modal.isHidden()); assert(await page.locator('#name-results').isVisible());
      await wordsBox('See highly rated vacuum on Amazon →');
      assert.equal(await page.evaluate(() => document.body.style.overflow), '');
      await page.locator('#name-results').evaluate(el => el.scrollIntoView({block:'center', behavior:'instant'}));
      await noScroll(); await shot('spotter-result');
      assert.deepEqual(calls, [], 'typed words must not call /api/search');
      assert.deepEqual(await page.evaluate(() => window.tracked.splice(0).filter(([name]) => name.startsWith('tool_'))), []);
      // A link afterwards: unchanged (its own status, steps and lookup).
      await page.locator('#q').fill(ASIN);
      await Promise.all([page.waitForResponse(r => r.url().includes('/api/search')), page.locator('.hero-box button').click()]);
      assert.equal(await text('#modal-status'), 'Analyzing the price, please wait..');
      assert.equal(await text('#modal-step'), 'Finding the product..');
      await page.clock.runFor(10100);
      assert(await modal.isHidden()); assert.equal(await text('#grade-letter'), 'B');
      assert.deepEqual(calls, ['?asin=' + ASIN + '&grade=deal']); calls.length = 0;
      // × cancels like a link: no box, focus back on the button that started it.
      await page.goto('https://wowpriced.test/deal-spotter/'); await page.clock.runFor(500);
      await page.locator('#q').fill('vacuum'); await page.locator('.hero-box button').click();
      await page.clock.runFor(3000); await page.locator('#cancel-analysis').click();
      assert(await modal.isHidden()); assert(await page.locator('#name-results').isHidden());
      assert(await page.locator('.hero-box button').evaluate(el => el === document.activeElement));
      await page.clock.runFor(20000); assert(await page.locator('#name-results').isHidden());
      // Adult words: no box, so no analyzer (today's hint).
      await page.locator('#q').fill('vibrator'); await page.locator('.hero-box button').click();
      assert(await modal.isHidden()); assert(await page.locator('#name-results').isHidden());
      assert.equal(await text('#input-error'), 'For an A, B or C grade, paste the product’s Amazon link.');
      // The homepage buttons' ?q= address runs the same analyzer, on a keyword page too.
      await page.goto('https://wowpriced.test/amazon-discount-finder/?q=vacuum'); await page.clock.runFor(500);
      assert(await modal.isVisible()); assert.equal(await text('#modal-product'), 'vacuum');
      await page.clock.runFor(10000);
      await wordsBox('See highly rated vacuum on Amazon →');
      assert.deepEqual(calls, []);

      // Review Checker: the link's check box (spinner, progress, ×), steps about the search.
      await page.goto('https://wowpriced.test/review-checker/'); await page.clock.runFor(500);
      await page.locator('#hero-input').fill('vacuum'); await page.locator('#hero-form button').click();
      const loading = page.locator('#review-loading');
      assert(await loading.isVisible()); assert(await page.locator('#name-results').isHidden());
      assert.equal(await text('#loading-product'), 'vacuum');
      assert.equal(await text('#loading-copy'), 'Searching for deals, please wait..');
      assert.equal(await text('#loading-step'), SPOTTER_STEPS[0]);
      assert.equal(await focused(), 'loading-close'); assert(await page.locator('#review-loading .spinner').isVisible());
      await page.clock.runFor(5000);
      assert.match(await text('#review-percent'), /^5\d%$/); assert.equal(await text('#loading-step'), SPOTTER_STEPS[1]);
      await noScroll(); await shot('review-modal');
      await page.clock.runFor(2000); assert.equal(await text('#loading-step'), SPOTTER_STEPS[2]); assert(await loading.isVisible());
      await page.clock.runFor(3100);
      assert(await loading.isHidden()); assert(await page.locator('#name-results').isVisible());
      await wordsBox('See highly rated vacuum on Amazon →');
      await page.locator('#name-results').evaluate(el => el.scrollIntoView({block:'center', behavior:'instant'}));
      await noScroll(); await shot('review-result');
      assert.deepEqual(calls, [], 'typed words must not call /api/search');
      await page.locator('#name-results .words-discounts').click();
      assert.deepEqual(await page.evaluate(() => window.tracked.splice(0).filter(([name]) => ['deal_click', 'tool_grade', 'tool_feedback'].includes(name))), [['deal_click', {store:'Amazon'}]]);
      // A link afterwards: unchanged.
      await page.locator('#hero-input').fill(ASIN);
      await Promise.all([page.waitForResponse(r => r.url().includes('/api/search')), page.locator('#hero-form button').click()]);
      assert.equal(await text('#loading-copy'), 'Checking the reviews, please wait..');
      assert.equal(await text('#loading-step'), 'Finding the product..');
      await page.clock.runFor(10100); await page.locator('#review-report').waitFor();
      assert.deepEqual(calls, ['?asin=' + ASIN + '&grade=reviews']); calls.length = 0;
      // × cancels: no box.
      await page.goto('https://wowpriced.test/review-checker/'); await page.clock.runFor(500);
      await page.locator('#hero-input').fill('vacuum'); await page.locator('#hero-form button').click();
      await page.clock.runFor(3000); await page.locator('#loading-close').click();
      assert(await loading.isHidden()); assert(await page.locator('#name-results').isHidden());
      await page.clock.runFor(20000); assert(await page.locator('#name-results').isHidden());

      // Switch off: today's behaviour, the box at once with no analyzer.
      root = path.join(scratch, 'off', 'site');
      for (const [url, field, button, box] of [['/deal-spotter/', '#q', '.hero-box button', '#analyze-modal'],
                                              ['/review-checker/', '#hero-input', '#hero-form button', '#review-loading']]) {
        await page.goto('https://wowpriced.test' + url); await page.clock.runFor(500);
        await page.locator(field).fill('vacuum'); await page.locator(button).click();
        assert(await page.locator(box).isHidden(), url + ' off: no analyzer');
        assert(await page.locator('#name-results').isVisible(), url + ' off: box at once');
        await wordsBox('See highly rated vacuum on Amazon →');
      }
      assert.deepEqual(calls, []);

      assert.deepEqual(errors, []);
      await context.close();
      console.log(`${width}: ok`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
