// Native Chromium only; every navigation, API response and image is fulfilled offline.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {buildFixture: buildSearchFixture, assertAnalyzeGeometry, assertDiscounts} = require('./search_box_glow_browser.cjs');
function buildFixture(scratch, lazy = true) {return buildSearchFixture(scratch, true, lazy);}
async function main() {
  const {chromium} = require('playwright');
  const scratch = fs.mkdtempSync('/tmp/task304-browser-');
  const shots = path.resolve(process.argv[3] || 'docs/research/page-polish');
  fs.mkdirSync(shots, {recursive:true});
  let browser;
  try {
    const roots = {};
    for (const lazy of [false, true]) roots[lazy] = buildFixture(path.join(scratch, String(lazy)), lazy);
    browser = await chromium.launch({headless:true, executablePath:process.argv[2]});
    for (const lazy of [false, true]) for (const width of [360, 390, 900, 1024, 1280, 1440, 1920]) for (const theme of ['light', 'dark']) {
      const root = roots[lazy], errors = [], unexpected = [];
      const context = await browser.newContext({viewport:{width, height:844}, colorScheme:theme, serviceWorkers:'block'});
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.hostname === 'm.media-amazon.com') return route.fulfill({contentType:'image/svg+xml',
          body:'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320"><rect width="320" height="320" fill="#b9c6cd"/></svg>'});
        if (url.origin !== 'https://wowpriced.test') {unexpected.push(url.origin); return route.fulfill({status:204, body:''});}
        if (url.pathname === '/api/search') {
          assert.equal(new URL(route.request().frame().url()).pathname, '/', 'event discounts never calls the API');
          return route.fulfill({json:{items:[{asin:'B000000901',
          title:'Wireless mouse from Amazon', price_cents:1299, as_of:new Date().toISOString()}]}});
        }
        let file = path.resolve(root, '.' + url.pathname);
        if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
        if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) return route.fulfill({status:204, body:''});
        return route.fulfill({path:file});
      });
      await context.addInitScript(() => {
        localStorage.setItem('wp.analyzeHint', '1');
        window.discountOpens = []; window.discountTracks = [];
        window.open = (...args) => window.discountOpens.push(args);
        window.wpTrack = (...args) => window.discountTracks.push(args);
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      const fits = async () => assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      const name = `${lazy ? 'lazy' : 'inline'}-${width}-${theme}`;
      for (const slug of ['black-friday', 'cyber-monday']) for (const analyze of [false, true]) {
        await page.goto(`https://wowpriced.test/${slug}/`);
        await fits();
        for (const selector of ['#q', '#analyze-search']) {
          const bounds = await page.locator(selector).boundingBox();
          assert(bounds.height >= 44 && bounds.width >= 44);
        }
        assert.equal(await page.locator('#analyze-search').innerText(), 'Analyze');
        assert.equal((await page.locator('.searchfield').boundingBox()).height, 56, '44px pill and two 6px gaps');
        if (width >= 1920) assert.equal((await page.locator('.searchfield').boundingBox()).width, 940, 'wider desktop field');
        await assertAnalyzeGeometry(page);
        await assertDiscounts(page, false);
        await page.screenshot({path:path.join(shots, `${name}-${slug}.png`)});
        await page.locator('#q').fill('mouse');
        await assertAnalyzeGeometry(page);
        await assertDiscounts(page, true);
        const exit = page.locator(width < 900 ? '#search-discounts-mobile' : '#search-discounts');
        for (const button of ['left', 'middle']) await exit.click({button});
        assert.deepEqual(await page.evaluate(() => window.discountOpens), Array(2).fill([
          'https://www.amazon.com/s?k=mouse&rh=p_n_deal_type%3A23566065011&tag=example-20', '_blank', 'noopener']));
        assert.deepEqual(await page.evaluate(() => window.discountTracks.map(call => call[0]).filter(name => name === "search_discounts")),
          ['search_discounts', 'search_discounts']);
        await page.locator('#q').fill('');
        await assertDiscounts(page, false);
        await page.locator('#q').fill('mouse');
        if (analyze) await page.locator('#analyze-search').click();
        else await page.locator('#q').press('Enter');
        await page.waitForURL(url => url.pathname === '/');
        await page.waitForFunction(() => document.getElementById('q').value === 'mouse' && !/[?&](q|analyze)=/.test(location.search));
        if (analyze) await page.locator('.search-analysis[aria-busy="false"]').waitFor();
        await page.locator('#content .card').first().waitFor();
        assert.match(await page.locator('footer').innerText(), /As an Amazon Associate,\s*wowpriced earns from qualifying purchases\./);
        assert.equal(await page.locator('.search-amazon-top small').count(), 0, 'top Amazon disclosure stays in footer');
        assert.match(await page.locator('#content').innerText(), /mouse/i);
        assert.equal(await page.locator('header .hnav #cattabs').count(), 1);
        // The fresh module may settle after the query; it must preserve this view.
        await page.waitForFunction(() => Array.from(document.querySelectorAll('style'))
          .some(el => el.textContent.includes('#fresh-drop-pill')));
        assert.equal(await page.locator('#q').inputValue(), 'mouse');
        assert.match(await page.locator('#content').innerText(), /mouse/i);
        assert.equal(await page.locator('#fresh-all, #drop-confetti').count(), 0);
        assert(await page.locator('#fresh-drop-toast').isHidden());
        await fits();
        await page.screenshot({path:path.join(shots, `${name}-${slug}-${analyze ? 'analyze' : 'search'}.png`)});
      }
      await page.goto('https://wowpriced.test/subscribe.html');
      await page.locator('.support-visual').waitFor(); await fits();
      assert.equal(await page.locator('[data-support]').innerText(), 'Support once');
      assert.equal(await page.locator('[data-support-monthly]').innerText(), 'Support monthly');
      assert.match(await page.locator('[data-support-note]').innerText(), /not tax-deductible and buys nothing extra; every deal stays free\./);
      await page.screenshot({path:path.join(shots, `${name}-support.png`), fullPage:true});
      assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
      await context.close();
    }
    console.log('Page polish browser checks passed: event search/Analyze, responsive layouts, themes and Support screenshots');
  } finally {if (browser) await browser.close(); fs.rmSync(scratch, {recursive:true, force:true});}
}
module.exports = {buildFixture};
if (require.main === module) main().catch(error => {console.error(error); process.exitCode = 1;});
