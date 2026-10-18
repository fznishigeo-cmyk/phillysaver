// Run outside the sandbox. All page/API/image requests are fulfilled from fixtures.
// Usage: node tests/search_more_browser.cjs /path/to/chromium [screenshot-directory]
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');

function buildFixture(scratch) {
  const build = spawnSync('.venv/bin/python', ['-c', `
import sys
from pathlib import Path
from dealradar.config import load_config
from dealradar.publish import site
from tests.test_support import NOW, render
from tests.test_publish_site import make_deal
site.util_now = lambda: NOW
cfg = load_config()
cfg.tracking = {}
cfg.max_photoless_h = None
cfg.non_amazon_freshness_enabled = False
cfg.keepa_click_price_rule = "off"
cfg.affiliate_amazon_tag = "example-20"
cfg.search_more = {"enabled": True, "pages": 3, "min_query_chars": 2}
# make_deal's default clock is outside the Amazon confirmation window.
deals = [make_deal(dedupe_key=f"task293-{i}", item=f"Wireless mouse {i}",
    store="Amazon", brand="Example", canonical_url=f"https://www.amazon.com/dp/B0{i:08d}",
    asin=f"B0{i:08d}", codes=[], conditions=[], category="electronics", price_rechecked_at=NOW.isoformat(),
    image_url="https://m.media-amazon.com/images/I/mouse.jpg") for i in range(1, 25)]
deals.append(make_deal(dedupe_key="hair-tie", item="Hair tie organizer", codes=[], conditions=[],
    image_url="https://m.media-amazon.com/images/I/tie.jpg"))
render(Path(sys.argv[1]), cfg, deals)
`, scratch], {encoding:'utf8'});
  assert.equal(build.status, 0, build.stderr);
  const root = path.join(scratch, 'site');
  const published = JSON.parse(fs.readFileSync(path.join(root, 'deals.json'), 'utf8')).deals;
  assert.equal(published.filter(d => d.item.startsWith('Wireless mouse ')).length, 24,
    'browser fixture must publish all 24 confirmed Amazon mice before waiting for local cards');
  return root;
}

async function waitFor(page, condition, action, diagnostics) {
  try {return await action();}
  catch (error) {
    if (error.name !== 'TimeoutError') throw error;
    const state = await page.evaluate(() => ({query:document.querySelector('#q')?.value,
      cards:document.querySelectorAll('#content .card').length,
      amazonCards:document.querySelectorAll('#amazon-results article').length,
      moreCards:document.querySelectorAll('#search-more article').length,
      content:document.querySelector('#content')?.innerText.slice(0, 500)})).catch(() => ({pageUnavailable:true}));
    throw new Error('Timed out waiting for: ' + condition + '\n' +
      JSON.stringify({state, ...diagnostics}), {cause:error});
  }
}

async function main() {
  const {chromium} = require('playwright');
  const scratch = fs.mkdtempSync('/tmp/task293g-browser-');
  let browser;
  try {
    const root = buildFixture(scratch), shots = process.argv[3] && path.resolve(process.argv[3]);
    if (shots) fs.mkdirSync(shots, {recursive:true});
    browser = await chromium.launch({headless:true, executablePath:process.argv[2]});
    for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({viewport:{width, height:844}, colorScheme:theme, serviceWorkers:'block',
        reducedMotion:width === 390 ? 'reduce' : 'no-preference'});
      const calls = [], errors = [], unexpected = [];
      let limited = false;
      const item = (n, title = 'Wireless mouse from Amazon') => ({asin:'B0' + String(n).padStart(8, '0'), title,
        price_cents:1299, list_price_cents:1999, image:'https://m.media-amazon.com/images/I/mouse.jpg',
        as_of:new Date(Date.now() - 60000).toISOString()});
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.hostname === 'm.media-amazon.com') return route.fulfill({contentType:'image/svg+xml', body:
          '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320"><rect width="320" height="320" fill="#f5f6f7"/><rect x="100" y="55" width="120" height="210" rx="60" fill="#39565d"/><path d="M160 55v95m-60 0h120" stroke="#80c5ca" stroke-width="6"/></svg>'});
        if (url.origin !== 'https://wowpriced.test') {
          unexpected.push(url.origin); return route.fulfill({status:204, body:''});
        }
        if (url.pathname === '/api/search') {
          calls.push(url.search);
          if (url.searchParams.has('asin')) {
            assert.deepEqual([...url.searchParams.keys()], ['asin']);
            assert.equal(url.searchParams.get('asin'), 'B000000901');
            return route.fulfill({contentType:'application/json', body:JSON.stringify({items:[item(901, 'Coffee grinder')]})});
          }
          assert.deepEqual([...url.searchParams.keys()], ['q', 'page']);
          const q = url.searchParams.get('q'), page = Number(url.searchParams.get('page'));
          assert([0, 1, 2].includes(page));
          let data;
          if (q === 'hair tire' || q === 'cofee') data = {items:[]};
          else if (q === 'coffee') data = {items:[item(900, 'Coffee maker')]};
          else if (limited && page === 1) data = {items:[], reason:'rate_limited'};
          else data = {items:[item(1), item(100 + page), item(100 + page), item(200 + page, 'Anal lubricant')]};
          return route.fulfill({contentType:'application/json', body:JSON.stringify(data)});
        }
        const file = path.join(root, url.pathname === '/' ? 'index.html' : url.pathname);
        return route.fulfill(fs.existsSync(file) && fs.statSync(file).isFile() ? {path:file} : {status:204, body:''});
      });
      await context.addInitScript(() => {
        window.searchMoreEvents = [];
        window.wpTrack = (...args) => window.searchMoreEvents.push(args);
        localStorage.setItem('wp.analyzeHint', '1');
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
      const wait = (condition, action) => waitFor(page, width + ' ' + theme + ': ' + condition, action,
        {calls, errors, unexpected});
      await wait('home navigation', () => page.goto('https://wowpriced.test/'));
      await wait('at least 20 initial local cards in #content', () =>
        page.waitForFunction(() => document.querySelectorAll('#content .card').length >= 20));
      const q = page.locator('#q');
      await q.fill('mouse');
      await wait('mouse: related Amazon section attached', () => page.locator('#search-more').waitFor({state:'attached'}));
      await page.waitForTimeout(800);
      assert.equal(calls.length, 0, 'no API request before scrolling to the end of local results');
      const more = page.locator('#search-more');
      for (let n = 0; n < 3; n++) {
        await wait('mouse: scroll to Amazon page ' + n, () =>
          page.locator('#search-more [data-search-amazon]').scrollIntoViewIfNeeded());
        await wait('mouse: Amazon page ' + n + ' rendered (' + (n + 1) + ' unique cards)', () =>
          page.waitForFunction(n => document.querySelectorAll('#search-more article').length >= n, n + 1));
      }
      assert.deepEqual(calls, [0, 1, 2].map(n => '?q=mouse&page=' + n));
      assert.equal(await more.locator('article').count(), 3);
      assert(!(await more.innerText()).includes('lubricant'));
      assert((await more.innerText()).includes('Amazon price as of '));
      assert.equal(await more.locator('.flag[aria-label="35% off List Price"]').count(), 3);
      assert.equal(await more.locator('s').first().textContent(), 'List Price $19.99');
      const links = await page.locator('#content .card a.getdeal').evaluateAll(nodes => nodes.map(n => n.href)
        .filter(href => href.includes('amazon.com/dp/')).map(href => new URL(href).pathname));
      assert.equal(new Set(links).size, links.length, 'no ASIN already on the wall is repeated');
      const final = more.locator('[data-search-amazon]');
      assert((await final.getAttribute('href')).includes('tag=example-20'));
      assert.equal(await final.getAttribute('rel'), 'sponsored nofollow noopener');
      if (width === 390) assert.equal(await final.evaluate(el => getComputedStyle(el).animationName), 'none');
      await page.evaluate(() => {window.open = (...args) => {window.openedAmazon = args;};});
      await final.click();
      assert.equal(await page.evaluate(() => window.openedAmazon[0]), 'https://www.amazon.com/s?k=mouse&tag=example-20');
      if (shots) await page.screenshot({path:path.join(shots, width + '-' + theme + '-pages.png'), fullPage:true});

      // A typo with no local or Amazon match uses a title bigram and can be undone.
      await q.fill('hair tire');
      await wait('spelling: hair tire corrected to hair tie', () =>
        page.waitForFunction(() => document.querySelector('#q').value === 'hair tie'));
      assert((await page.locator('#search-spelling').innerText()).includes('Showing results for hair tie'));
      const undoBeforeBlur = await page.locator('[data-search-original]').boundingBox();
      await q.evaluate(el => el.blur());
      assert.deepEqual(await page.locator('[data-search-original]').boundingBox(), undoBeforeBlur,
        'blurring the search field must not move the undo link during a click');
      await q.focus();
      await page.locator('[data-search-original]').click();
      assert.equal(await q.inputValue(), 'hair tire');
      await page.waitForTimeout(1300);
      assert.equal(await q.inputValue(), 'hair tire', 'undo stays literal after pending work settles');
      assert.equal(calls.filter(q => q.startsWith('?q=hair%20tire')).length, 1);
      await q.fill('hair tir');
      await q.fill('hair tire');
      await wait('spelling: editing re-enables hair tire correction', () =>
        page.waitForFunction(() => document.querySelector('#q').value === 'hair tie'));
      await page.locator('[data-search-original]').focus();
      await page.locator('[data-search-original]').press('Enter');
      assert.equal(await q.inputValue(), 'hair tire', 'keyboard undo also restores the original');
      assert.equal(calls.filter(q => q.startsWith('?q=hair%20tire')).length, 1);
      // Built-in vocabulary can also lead to a single corrected Amazon request.
      await q.fill('cofee');
      await wait('spelling: cofee corrected to coffee', () =>
        page.waitForFunction(() => document.querySelector('#q').value === 'coffee'));
      await wait('coffee: current Amazon card visible', () => page.locator('#amazon-results article').waitFor({state:'visible'}));
      assert.equal(calls.filter(q => q === '?q=coffee&page=0').length, 1);
      assert.equal(await page.locator('#amazon-results .flag').innerText(), '-35%');
      assert.equal(await page.locator('#amazon-results s').textContent(), 'List Price $19.99');
      if (shots) await page.screenshot({path:path.join(shots, width + '-' + theme + '-spelling.png'), fullPage:true});

      await q.fill('https://www.amazon.com/dp/B000000901');
      await q.press('Enter');
      await wait('Analyze: Live Amazon price card with List Price visible', () =>
        page.locator('.analyze-lead #amazon-results article').waitFor({state:'visible'}));
      assert.equal(await page.locator('.analyze-lead .flag').innerText(), '-35%');
      assert.equal(await page.locator('.analyze-lead s').textContent(), 'List Price $19.99');
      assert.equal(calls.filter(q => q === '?asin=B000000901').length, 1);
      if (shots) await page.screenshot({path:path.join(shots, width + '-' + theme + '-analyze.png'), fullPage:true});

      limited = true; await q.fill('wireless');
      await wait('wireless: related Amazon section attached', () => page.locator('#search-more').waitFor({state:'attached'}));
      await wait('wireless: scroll to first Amazon page', () => page.locator('#search-more [data-search-amazon]').scrollIntoViewIfNeeded());
      await wait('wireless: first Amazon card visible', () => page.locator('#search-more article').waitFor({state:'visible'}));
      await wait('wireless: scroll to rate-limited page', () => page.locator('#search-more [data-search-amazon]').scrollIntoViewIfNeeded());
      await wait('wireless: stop paging after rate_limited', () => page.locator('#search-more .search-more-load').waitFor({state:'hidden'}));
      await page.waitForTimeout(1300);
      assert.deepEqual(calls.filter(q => q.startsWith('?q=wireless')), ['?q=wireless&page=0', '?q=wireless&page=1']);
      assert(await page.locator('#search-more [data-search-amazon]').isVisible());
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.equal(await page.evaluate(() => JSON.stringify(window.searchMoreEvents).includes('hair')), false);
      assert.deepEqual(unexpected, []); assert.deepEqual(errors, []);
      console.log('Search more Chromium ' + width + ' ' + theme + ': passed');
      await context.close();
    }
  } finally {
    try {if (browser) await browser.close();}
    finally {fs.rmSync(scratch, {recursive:true, force:true});}
  }
}

module.exports = {buildFixture, waitFor};
if (require.main === module) main().catch(error => {console.error(error); process.exitCode = 1;});
