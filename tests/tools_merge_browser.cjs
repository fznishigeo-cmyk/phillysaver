// tools_merge + soft_buttons (operator 2026-10-23) in real Chromium, entirely offline, at a
// phone (390x844) and a laptop (1280x800), light and dark. Captures stay under pytest's tmp_path.
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');
const scratch = process.argv[2];
fs.mkdirSync(scratch, {recursive:true});
const BUILD = `
from pathlib import Path
from unittest.mock import patch
import sys
from tests.test_support import render, NOW
from tests.test_tools_merge import rows
from dealradar.config import load_config
from dealradar.publish import site
cfg = load_config()
cfg.keepa_click_price_rule = cfg.keepa_buybox_rule = "off"
assert cfg.tools_merge is True and cfg.soft_buttons is True
assert cfg.review_checker_word_links is True and cfg.deal_spotter_word_links is True
assert cfg.hide_spotter_recommends is True and cfg.plural_highly_rated is True and cfg.equal_word_buttons is True
with patch.object(site, "util_now", lambda: NOW): render(Path(sys.argv[1]), cfg, rows())
`;
const NOW = new Date('2026-10-12T00:10:00Z'), ASIN = 'B0MERGE001';
const lum = rgb => rgb.reduce((sum, c, i) => { const v = c / 255; return sum + [.2126,.7152,.0722][i] * (v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4); }, 0);
const contrast = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); };
async function contrastAudit(page, scope) {
  const rows = await page.evaluate(scope => {
    const rgb = color => (color.match(/[\d.]+/g) || []).map(Number);
    return [...document.querySelectorAll(scope + ' :is(h1,h2,h3,p,small,summary,a,button,li,strong,span)')].flatMap(el => {
      const style = getComputedStyle(el), box = el.getBoundingClientRect();
      if (!el.checkVisibility({checkOpacity:true, checkVisibilityCSS:true}) || box.width < 2 || box.height < 2 ||
          box.bottom <= 0 || box.top >= innerHeight || el.closest('.sr-only,[role="img"],[aria-hidden="true"]') ||
          !el.textContent.trim() || [...el.childNodes].every(n => n.nodeType !== 3 || !n.textContent.trim())) return [];
      let bg;
      for (let parent = el; parent; parent = parent.parentElement) {
        const s = getComputedStyle(parent), value = rgb(s.backgroundColor);
        if (s.backgroundImage !== 'none' && !value.length) return [];
        if (value.length === 3 || value[3] === 1) { bg = value.slice(0, 3); break; }
        if (s.backgroundImage !== 'none') return [];
      }
      const size = parseFloat(style.fontSize), weight = parseInt(style.fontWeight) || 400;
      return [{text:el.textContent.trim().slice(0, 50), fg:rgb(style.color).slice(0, 3), bg:bg || [255,255,255],
        minimum:size >= 24 || size >= 18.66 && weight >= 700 ? 3 : 4.5, disabled:!!el.closest(':disabled')}];
    });
  }, scope);
  for (const row of rows) if (!row.disabled) assert(contrast(row.fg, row.bg) >= row.minimum, row.text + ': contrast ' + contrast(row.fg, row.bg).toFixed(2));
}
(async () => {
  let browser;
  try { browser = await chromium.launch({executablePath:'/opt/pw-browsers/chromium', headless:true}); }
  catch (error) { console.log('Chromium unavailable: ' + error.message.split('\n')[0]); process.exitCode = 77; return; }
  try {
    const built = spawnSync('.venv/bin/python', ['-c', BUILD, scratch], {encoding:'utf8'});
    assert.equal(built.status, 0, built.stderr);
    const root = path.join(scratch, 'site');
    for (const [width, height] of [[390, 844], [1280, 800]]) {
      for (const colorScheme of ['light', 'dark']) {
        const context = await browser.newContext({viewport:{width, height}, colorScheme});
        const errors = [], calls = [], homeCalls = [], state = {grade:'B', included:false, priceCheck:true};
        let page;
        await context.addInitScript(() => {
          Math.random = () => 0;
          try { localStorage.setItem('wp.spotter.cookies', '1'); localStorage.setItem('wp.tools.cookies', '1'); } catch (_) {}
          window.amazonClicks = [];
          document.addEventListener('click', e => { const a = e.target.closest && e.target.closest('a[href^="https://www.amazon.com/"]');
            if (a) { window.amazonClicks.push(a.href); e.preventDefault(); } }, true);
          let wpTrack; window.tracked = [];
          Object.defineProperty(window, 'wpTrack', {configurable:true, set(fn) { wpTrack = fn; }, get() {
            return wpTrack && ((name, params) => { if (['deal_click', 'tool_grade', 'tool_feedback'].includes(name)) window.tracked.push([name, params]); return wpTrack(name, params); });
          }});
        });
        await context.route('**/*', async route => {
          const url = new URL(route.request().url());
          if (url.hostname === 'm.media-amazon.com') return route.fulfill({contentType:'image/svg+xml', body:'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"><rect width="300" height="300" fill="#eef4ff"/></svg>'});
          if (url.origin !== 'https://wowpriced.test') return route.fulfill({status:204, body:''});
          if (url.pathname === '/api/search') {
            (route.request().frame().page() === page ? calls : homeCalls).push(url.search);
            const asin = url.searchParams.get('asin'), kind = url.searchParams.get('grade');
            const item = {asin, title:'Wireless over-ear headphones with a folding design', image:'https://m.media-amazon.com/images/I/fixture.jpg',
              price_cents:4699, list_price_cents:5999, monthlySold:1000, category:'Over-Ear Headphones',
              as_of:new Date(NOW.getTime() - 4 * 60000).toISOString(), ...(state.included ? {price_includes_shipping:true} : {})};
            const body = kind === 'deal' ? {items:[item], deal_grade:{grade:state.grade, label:{A:'Great price', B:'Good price', C:'Fair price'}[state.grade],
              facts:['21% below the list price', 'Verified deal on wowpriced']}} :
              {items:[{asin, title:item.title, image:item.image, price_cents:item.price_cents, as_of:item.as_of}],
               review:{grade:'A', label:'Great — a strong set of review signals', checks:[{id:'highly_rated', title:'Highly rated', result:'A warm reception.', detail:'Detail.'}]},
               ...(state.priceCheck ? {price_check:{grade:'B', label:'Good price'}} : {})};
            return route.fulfill({contentType:'application/json', body:JSON.stringify(body)});
          }
          let file = path.join(root, url.pathname);
          if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
          if (!fs.existsSync(file)) return route.fulfill({status:404, body:''});
          return route.fulfill({contentType:{'.html':'text/html', '.js':'application/javascript', '.css':'text/css', '.json':'application/json', '.svg':'image/svg+xml'}[path.extname(file)] || 'application/octet-stream', body:fs.readFileSync(file)});
        });
        page = await context.newPage(); page.on('pageerror', e => errors.push(e.message));
        await page.clock.install({time:NOW});
        const shot = name => page.screenshot({path:path.join(scratch, `${width}-${colorScheme}-${name}.png`), animations:'disabled'});
        const noScroll = async () => { const s = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]); assert(s[0] <= s[1], 'horizontal scroll ' + s); };
        const tracked = () => page.evaluate(() => window.tracked.splice(0));

        // Homepage: the two buttons keep their place and look, with the new names, links and softer colors.
        await page.goto('https://wowpriced.test/'); await page.clock.runFor(500);
        const spot = page.locator('#search-discounts'), rev = page.locator('#analyze-search');
        assert.equal((await spot.innerText()).replace(/\s+/g, ' ').trim(), 'Fake Deal Spotter' + (width >= 900 ? '' : ' →'));
        assert.equal((await rev.innerText()).replace(/\s+/g, ' ').trim(), 'Review Checker' + (width >= 900 ? '' : ' →'));
        await page.locator('#q').fill('running shoes');
        assert.equal(await spot.getAttribute('href'), '/deal-spotter/?q=running%20shoes');
        assert.equal(await rev.getAttribute('href'), '/review-checker/?q=running%20shoes');
        for (const button of [spot, rev]) {
          const look = await button.evaluate(el => ({image:getComputedStyle(el).backgroundImage, shadow:getComputedStyle(el).boxShadow, color:getComputedStyle(el).color, height:el.getBoundingClientRect().height}));
          const stops = (look.image.match(/rgb\(\d+, \d+, \d+\)/g) || []).map(c => c.match(/\d+/g).map(Number));
          assert(stops.length >= 3, look.image);
          for (const stop of stops) assert(contrast(stop, [255, 255, 255]) >= 4.5, 'button stop ' + stop);
          assert.equal(look.color, 'rgb(255, 255, 255)'); assert(look.height >= 44, 'tap height ' + look.height);
          assert(/rgba\((147, 51, 234, 0\.12|59, 130, 246, 0\.1)\)/.test(look.shadow), 'softer glow ' + look.shadow);
        }
        assert(/rgb\(42, 99, 203\)/.test(await spot.evaluate(el => getComputedStyle(el).backgroundImage)), 'soft blue');
        assert(/rgb\(133, 64, 204\)/.test(await rev.evaluate(el => getComputedStyle(el).backgroundImage)), 'soft purple');
        await noScroll(); await shot('home');
        // The tools get their own tab. The homepage's own search may still look up the typed words
        // after this point (search-more.js's page-0 lookup landed after a plain calls.length = 0 in
        // 6 runs of 10, 2026-10-23); that lookup is the homepage's, never the tools'.
        const home = page; page = await context.newPage(); page.on('pageerror', e => errors.push(e.message));
        calls.length = 0;
        await home.evaluate(() => fetch('/api/search?q=running%20shoes&page=0').then(r => r.json()));
        assert(homeCalls.includes('?q=running%20shoes&page=0')); assert.deepEqual(calls, []);
        await home.close();

        // Spotter home: the guide tabs, the Love button and the footer columns.
        await page.goto('https://wowpriced.test/deal-spotter/'); await page.clock.runFor(500);
        const tabs = page.locator('.guide-tabs [role="tab"]');
        assert.deepEqual(await tabs.allInnerTexts(), ['Price grade', 'Verified deals', 'Price details', 'Find discounts']);
        await tabs.nth(1).click(); assert(await page.locator('#gpanel-1').isVisible()); assert(await page.locator('#gpanel-0').isHidden());
        await page.keyboard.press('ArrowRight'); assert.equal(await page.evaluate(() => document.activeElement.id), 'gtab-2');
        assert(await page.locator('#gpanel-2').isVisible());
        await page.locator('.trusted-guide').scrollIntoViewIfNeeded(); await contrastAudit(page, '.trusted-guide'); await shot('guide');
        assert.equal(await page.locator('.love-cta').getAttribute('href'), '/best-deals-today/');
        assert.deepEqual(await page.locator('.merged-footer .footer-columns h3').allInnerTexts(), ['wowpriced', 'Tools', 'Support', 'Social']);
        await page.locator('.merged-footer').scrollIntoViewIfNeeded(); await contrastAudit(page, '.merged-footer'); await noScroll();
        assert(!/as mentioned|as seen on|million|testimonial|add to chrome|AI-powered|proprietary|★/i.test(await page.locator('body').innerText()));

        // Typed words: the link's analyzer and wait (tool_words_analyzer), then one box, no lookup;
        // its Amazon links are deal_clicks.
        await page.evaluate(() => scrollTo(0, 0));
        await page.locator('#q').fill('running shoes'); await page.locator('.hero-box button').click();
        assert(await page.locator('#analyze-modal').isVisible()); assert(await page.locator('#name-results').isHidden());
        assert.equal(await page.locator('#modal-product').textContent(), 'running shoes');
        assert.equal(await page.locator('#modal-step').textContent(), 'Searching our verified deals..');
        await page.clock.runFor(10100); assert(await page.locator('#analyze-modal').isHidden());
        const discounts = page.locator('#name-results .words-discounts');
        assert.equal(await discounts.getAttribute('href'), 'https://www.amazon.com/s?k=running%20shoes&rh=p_n_deal_type%3A23566064011&tag=wowpriced-20');
        assert.equal((await discounts.innerText()).trim(), 'See running shoes discounts on Amazon →');
        assert.equal(await page.locator('#name-results .name-button').getAttribute('href'), 'https://www.amazon.com/s?k=running%20shoes&rh=p_n_deal_type%3A210906366011&s=exact-aware-popularity-rank&tag=wowpriced-20');
        assert(await discounts.evaluate(el => el === document.activeElement));
        await page.locator('#name-results .words-deal').first().waitFor();
        assert.match(await page.locator('#name-results .words-deal').first().innerText(), /running shoes/i);
        assert.deepEqual(calls, [], 'typed words must not call /api/search');
        await discounts.click(); assert.deepEqual(await tracked(), [['deal_click', {store:'Amazon'}]]);
        await contrastAudit(page, '#name-results'); await noScroll(); await shot('words');
        // Enter instead of the button, then time for any debounce, retry or late handler: still no lookup.
        await page.locator('#q').fill('air fryer'); await page.locator('#q').press('Enter');
        assert(await page.locator('#analyze-modal').isVisible()); await page.clock.runFor(10100);
        assert.match(await discounts.getAttribute('href'), /k=air%20fryer&rh=p_n_deal_type%3A23566064011&tag=/);
        await page.clock.runFor(3000);
        assert.deepEqual(calls, [], 'typed words must not call /api/search, even later');

        // A link: the blue analyzer, then the report. No "wowpriced recommends" strip in it
        // (hide_spotter_recommends, operator 2026-10-24): progress, steps and "See today's deals" stay.
        await page.locator('#q').fill(ASIN);
        await Promise.all([page.waitForResponse(r => r.url().includes('/api/search')), page.locator('.hero-box button').click()]);
        await page.clock.runFor(3000);
        assert(await page.locator('#analyze-modal').isVisible());
        assert.equal(await page.locator('#modal-status').textContent(), 'Analyzing the price, please wait..');
        assert.equal(await page.locator('#analyze-modal :is(.recommends, .rec-card)').count(), 0);
        assert(Number(await page.locator('#analyze-modal [role="progressbar"]').getAttribute('aria-valuenow')) > 0);
        assert((await page.locator('#modal-step').textContent()).trim());
        assert(await page.locator('#modal-deals').isVisible()); assert.equal(await page.locator('#modal-deals').getAttribute('href'), '/');
        await shot('loading');
        await page.clock.runFor(7200);
        assert(await page.locator('#analyze-modal').isHidden());
        assert.equal(await page.locator('#grade-letter').textContent(), 'B');
        assert.equal(await page.locator('#grade-letter').getAttribute('class'), 'grade-letter b');
        assert.deepEqual(await tracked(), [['tool_grade', {method:'deal_b'}]]);
        assert.equal(await page.locator('#report-checked').textContent(), '4 min ago');
        assert.equal(await page.locator('#report-category').textContent(), 'Over-Ear Headphones');
        assert.equal(await page.locator('#result-list').textContent(), 'Amazon list price: $59.99');
        assert.deepEqual(await page.locator('#result-facts li').allInnerTexts(),
          ['A verified deal on wowpriced', 'Popular: 1K+ bought in past month']);
        await page.locator('#report-ours .ours-line').waitFor();
        assert.match(await page.locator('#report-ours').innerText(), /Verified deal\s+\$21\.00 on wowpriced/);
        assert.match(await page.locator('#report-ours a').getAttribute('href'), /^\/deal\/[a-z0-9-]+\/$/);
        assert.match(await page.locator('#result-similar').getAttribute('href'), /^https:\/\/www\.amazon\.com\/s\?k=.*tag=wowpriced-20$/);
        assert.equal(await page.locator('#review-link').getAttribute('href'), '/review-checker/?asin=' + ASIN);
        const report = await page.locator('#spotter-result').innerText();
        assert(!/% off|price history|90-day|usual price|average price|★|\b\d[\d,]* (?:ratings|reviews)\b/i.test(report), 'no second verdict, past prices, stars or counts');
        assert.equal(await page.locator('.grade-letter').count(), 1);
        assert.match(page.url(), /\/deal-spotter\/\?asin=B0MERGE001$/);
        await page.locator('.grade-help summary').click(); assert(await page.locator('.grade-help').evaluate(el => el.open));
        for (const selector of ['#result-buy', '.side-deal a']) {
          await page.locator(selector).first().click();
          assert.deepEqual(await tracked(), [['deal_click', {store:'Amazon'}]], selector);
        }
        assert.equal(await page.locator('.side-deal').count(), 5);
        await page.locator('[data-helpful="helpful"]').click();
        assert.deepEqual(await tracked(), [['tool_feedback', {method:'helpful'}]]);
        assert.equal(await page.evaluate(() => window.dataLayer.filter(a => a[0] === 'event' && a[1] === 'tool_feedback').map(a => a[2].method).pop()), 'helpful');
        assert(await page.locator('[data-helpful="not_helpful"]').isDisabled());
        assert.equal(await page.locator('#helpful-status').textContent(), 'Thanks for telling us.');
        await page.evaluate(() => scrollTo(0, 0)); await contrastAudit(page, '#spotter-result'); await noScroll(); await shot('report');
        await page.locator('.report-rows').scrollIntoViewIfNeeded(); await contrastAudit(page, '#spotter-result'); await shot('rows');
        // Amazon's Buy Box price without a shipping split says it includes shipping (C16).
        state.included = true; state.grade = 'C';
        await Promise.all([page.waitForResponse(r => r.url().includes('/api/search')), page.locator('#reanalyze').click()]);
        await page.clock.runFor(10100);
        assert.equal(await page.locator('#result-price').textContent(), '$46.99incl. shipping');
        assert.equal(await page.locator('#result-price .result-shipping').textContent(), 'incl. shipping');
        assert(await page.locator('[data-helpful="helpful"]').isEnabled());
        state.included = false; state.grade = 'B';

        // Keyword landing pages: their own headline, the same tool, the address kept.
        calls.length = 0;
        await page.goto('https://wowpriced.test/amazon-discount-finder/?q=air%20fryer'); await page.clock.runFor(500);
        assert.equal(await page.locator('#start h1').textContent(), 'Amazon Discount Finder');
        assert.equal(await page.locator('#q').inputValue(), 'air fryer');
        assert(await page.locator('#analyze-modal').isVisible()); await page.clock.runFor(10000);
        assert.match(await page.locator('#name-results .words-discounts').getAttribute('href'), /k=air%20fryer&rh=p_n_deal_type%3A23566064011&tag=/);
        assert.equal(calls.length, 0); await noScroll(); await shot('finder');
        await Promise.all([page.waitForResponse(r => r.url().includes('/api/search')), page.goto('https://wowpriced.test/amazon-deal-checker/?asin=' + ASIN)]);
        await page.clock.runFor(10100);
        assert.equal(await page.locator('#grade-letter').textContent(), 'B');
        assert.match(page.url(), /\/amazon-deal-checker\/\?asin=B0MERGE001$/);
        await tracked(); await noScroll(); await shot('checker');

        // Review Checker: menus, typed words and the one Price check row.
        await page.goto('https://wowpriced.test/review-checker/'); await page.clock.runFor(500);
        assert(!/Deal Checker|Discount Finder/.test(await page.locator('header').innerText()));
        calls.length = 0;
        await page.locator('#hero-input').fill('air fryer'); await page.locator('#hero-form button').click();
        assert(await page.locator('#review-loading').isVisible()); await page.clock.runFor(10100);
        assert.match(await page.locator('#name-results .words-discounts').getAttribute('href'), /k=air%20fryer&rh=p_n_deal_type%3A210906366011/);
        assert.equal(calls.length, 0); await contrastAudit(page, '#name-results'); await noScroll(); await shot('review-words');
        await page.goto('https://wowpriced.test/review-checker/?asin=' + ASIN); await page.clock.runFor(10100);
        await page.locator('#review-report').waitFor();
        assert.equal(await page.locator('.price-check h3').textContent(), 'Price check: B — Good price');
        assert.equal(await page.locator('.price-check a').getAttribute('href'), '/deal-spotter/?asin=' + ASIN);
        assert.equal(await page.locator('#review-report').getByText('Is the price right?').count(), 0);
        await page.locator('.price-check').scrollIntoViewIfNeeded(); await contrastAudit(page, '#review-report'); await noScroll(); await shot('review-report');
        state.priceCheck = false;
        await page.goto('https://wowpriced.test/review-checker/?asin=' + ASIN); await page.clock.runFor(10100);
        await page.locator('#review-report').waitFor();
        assert.equal(await page.locator('.price-check').count(), 0);
        assert.equal(await page.locator('.price-check-link a').textContent(), 'Check this price →');
        state.priceCheck = true;

        // The words box's two links per tool (word_links, operator 2026-10-23 A51/A52): same texts,
        // order and new tab; Best Sellers / Featured with the deal filter; no $50 cap (p_36). The yellow one
        // names the plural (plural_highly_rated), its link the typed words; both as wide (equal_word_buttons).
        for (const [slug, blue, yellow] of [
          ['review-checker', 'rh=p_n_deal_type%3A210906366011', 'rh=p_n_deal_type%3A23566065011&s=exact-aware-popularity-rank'],
          ['deal-spotter', 'rh=p_n_deal_type%3A23566064011', 'rh=p_n_deal_type%3A210906366011&s=exact-aware-popularity-rank']]) {
          calls.length = 0;
          await page.goto(`https://wowpriced.test/${slug}/?q=vacuum`); await page.clock.runFor(25100);  // past any analyzer wait
          assert.deepEqual(await page.locator('#name-results .words-buttons a').evaluateAll(links => links.map(a =>
            [a.className, a.innerText.trim(), a.getAttribute('href'), a.target, a.rel])), [
            ['words-discounts', 'See vacuum discounts on Amazon →', `https://www.amazon.com/s?k=vacuum&${blue}&tag=wowpriced-20`, '_blank', 'sponsored noopener'],
            ['name-button', 'See highly rated vacuums on Amazon →', `https://www.amazon.com/s?k=vacuum&${yellow}&tag=wowpriced-20`, '_blank', 'sponsored noopener']], slug);
          const widths = await page.locator('#name-results .words-buttons a').evaluateAll(links => links.map(a => a.getBoundingClientRect().width));
          assert(Math.abs(widths[0] - widths[1]) <= 1, slug + ' widths ' + widths);
          assert.equal(calls.length, 0); await noScroll();
        }

        assert.deepEqual(errors, []);
        await context.close();
        console.log(`${width} ${colorScheme}: ok`);
      }
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
