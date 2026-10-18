// Run outside the sandbox. All page/API/image requests are fulfilled from fixtures.
// Usage: node tests/search_more_browser.cjs /path/to/chromium [screenshot-directory]
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');

function buildFixture(scratch, lazy = true) {
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
cfg.search_analyze["lazy"] = sys.argv[2] == "true"
# make_deal's default clock is outside the Amazon confirmation window.
deals = [make_deal(dedupe_key=f"task293-{i}", item=f"Wireless mouse {i}",
    store="Amazon", brand="Example", canonical_url=f"https://www.amazon.com/dp/B0{i:08d}",
    asin=f"B0{i:08d}", codes=[], conditions=[], category="electronics", price_rechecked_at=NOW.isoformat(),
    image_url="https://m.media-amazon.com/images/I/mouse.jpg") for i in range(1, 25)]
deals.append(make_deal(dedupe_key="hair-tie", item="Hair tie organizer", codes=[], conditions=[],
    image_url="https://m.media-amazon.com/images/I/tie.jpg"))
render(Path(sys.argv[1]), cfg, deals)
`, scratch, String(lazy)], {encoding:'utf8'});
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
  const scratch = fs.mkdtempSync('/tmp/task300-search-more-browser-');
  let browser;
  try {
    const roots = {};
    for (const lazy of [false, true]) {
      const folder = path.join(scratch, String(lazy)); fs.mkdirSync(folder);
      roots[lazy] = buildFixture(folder, lazy);
    }
    const shots = process.argv[3] && path.resolve(process.argv[3]);
    if (shots) fs.mkdirSync(shots, {recursive:true});
    try {browser = await chromium.launch({headless:true, executablePath:process.argv[2]});}
    catch (error) {
      if (!/Operation not permitted|Executable doesn't exist|Failed to launch|sandbox|Target .* closed/.test(error.message)) throw error;
      console.log('Chromium unavailable: native launch failed; no screenshots captured'); process.exitCode = 77; return;
    }
    for (const lazy of [false, true]) for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
      const root = roots[lazy], mode = lazy ? 'lazy' : 'inline';
      const context = await browser.newContext({viewport:{width, height:844}, colorScheme:theme, serviceWorkers:'block',
        reducedMotion:width === 390 ? 'reduce' : 'no-preference'});
      const calls = [], errors = [], unexpected = [], imports = [];
      let releaseAnalyze;
      const analyzeReady = new Promise(resolve => {releaseAnalyze = resolve;});
      let stopReason = '';
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
          if (q === 'cupping set') data = {items:[], reason:'budget_exhausted'};
          else if (q === 'hair tire' || q === 'cofee') data = {items:[]};
          else if (q === 'coffee') data = {items:[item(900, 'Coffee maker')]};
          else if (stopReason && page === 1) data = {items:[], reason:stopReason};
          else data = {items:[item(1), item(100 + page), item(100 + page), item(200 + page, 'Anal lubricant')]};
          return route.fulfill({contentType:'application/json', body:JSON.stringify(data)});
        }
        if (url.pathname === '/search-analyze.js') {
          imports.push(url.search);
          await analyzeReady;
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
      const wait = (condition, action) => waitFor(page, mode + ' ' + width + ' ' + theme + ': ' + condition, action,
        {calls, errors, unexpected});
      await wait('home navigation', () => page.goto('https://wowpriced.test/'));
      await wait('at least 20 initial local cards in #content', () =>
        page.waitForFunction(() => document.querySelectorAll('#content .card').length >= 20));
      assert.equal(imports.length, 0, 'no Analyze asset at first paint');
      const q = page.locator('#q');
      await q.fill('mouse');
      await wait('mouse: related Amazon section attached', () => page.locator('#search-more').waitFor({state:'attached'}));
      releaseAnalyze();
      await page.waitForTimeout(800);
      assert.equal(calls.length, 0, 'no API request before scrolling to the end of local results');
      const more = page.locator('#search-more');
      const topButton = page.locator('#search-amazon-result [data-search-amazon]');
      assert(['See all "mouse" on Amazon\u00a0→', 'Analyze "mouse" on Amazon\u00a0→'].includes(await topButton.textContent()));
      assert((await topButton.getAttribute('href')).includes('tag=example-20'));
      assert.equal(await topButton.getAttribute('rel'), 'sponsored nofollow noopener');
      assert.equal(await topButton.evaluate(el => getComputedStyle(el).animationName), width === 390 ? 'none' : 'wpspin');
      assert(await topButton.evaluate(el => el.getBoundingClientRect().bottom <= document.querySelector('#content .card').getBoundingClientRect().top));
      assert.equal(await page.locator('[data-search-amazon].getdeal').count(), 1, 'one large Amazon button');
      assert.equal(await more.locator('[data-search-amazon]').textContent(), 'More on Amazon →');
      assert.equal(await more.locator('[data-search-amazon]').evaluate(el => getComputedStyle(el).fontSize), '14px');
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
      if (shots) await page.screenshot({path:path.join(shots, mode + '-' + width + '-' + theme + '-pages.png'), fullPage:true});

      await q.fill('cupping set');
      await wait('cupping set: budget_exhausted response handled', () => page.waitForFunction(() =>
        window.searchMoreEvents.some(([event, data]) => event === 'search_amazon_results' && data.count === 0)));
      assert.equal(await page.locator('#search-recovery-title').textContent(), 'See every "cupping set" price on Amazon');
      assert.equal(await page.locator('#amazon-results').innerHTML(), '');
      assert.equal(await page.locator('#content .grid, #content .card').count(), 0);
      assert(!(await page.locator('#content').innerText()).includes('Live Amazon prices'));
      const fallback = page.locator('.search-recovery [data-search-amazon]');
      assert(await fallback.isVisible());
      assert.equal(await fallback.textContent(), 'Compare all "cupping set" prices on Amazon\u00a0→');
      assert.equal(await fallback.getAttribute('rel'), 'sponsored noopener');
      assert.equal(await fallback.evaluate(el => getComputedStyle(el).animationName), width === 390 ? 'none' : 'wpspin');
      await fallback.click();
      assert.equal(await page.evaluate(() => window.openedAmazon[0]), 'https://www.amazon.com/s?k=cupping%20set&tag=example-20');
      assert.deepEqual(calls.filter(q => q.startsWith('?q=cupping%20set')), ['?q=cupping%20set&page=0']);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      if (shots) await page.screenshot({path:path.join(shots, mode + '-' + width + '-' + theme + '-empty.png'), fullPage:true});

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
      assert.equal(await page.locator('#search-recovery-title').textContent(), 'Live Amazon prices for "coffee"');
      assert.equal(await page.locator('#amazon-results .flag').innerText(), '-35%');
      assert.equal(await page.locator('#amazon-results s').textContent(), 'List Price $19.99');
      if (shots) await page.screenshot({path:path.join(shots, mode + '-' + width + '-' + theme + '-spelling.png'), fullPage:true});

      await q.fill('https://www.amazon.com/dp/B000000901');
      await q.press('Enter');
      await wait('Analyze: Live Amazon price card with List Price visible', () =>
        page.locator('.analyze-lead #amazon-results article').waitFor({state:'visible'}));
      assert.equal(await page.locator('.analyze-lead .flag').innerText(), '-35%');
      assert.equal(await page.locator('.analyze-lead s').textContent(), 'List Price $19.99');
      assert.equal(calls.filter(q => q === '?asin=B000000901').length, 1);
      assert.equal(imports.length, lazy ? 1 : 0);
      if (shots) await page.screenshot({path:path.join(shots, mode + '-' + width + '-' + theme + '-analyze.png'), fullPage:true});

      for (const [query, reason] of [['wireless', 'rate_limited'], ['wireless mouse', 'budget_exhausted']]) {
        stopReason = reason; await q.fill(query);
        await wait(query + ': related Amazon section attached', () => page.locator('#search-more').waitFor({state:'attached'}));
        await wait(query + ': scroll to first Amazon page', () => page.locator('#search-more [data-search-amazon]').scrollIntoViewIfNeeded());
        await wait(query + ': first Amazon card visible', () => page.locator('#search-more article').waitFor({state:'visible'}));
        await wait(query + ': scroll to limited page', () => page.locator('#search-more [data-search-amazon]').scrollIntoViewIfNeeded());
        await wait(query + ': stop paging after ' + reason, () => page.locator('#search-more .search-more-load').waitFor({state:'hidden'}));
        await page.waitForTimeout(1300);
        const prefix = '?q=' + encodeURIComponent(query) + '&page=';
        assert.deepEqual(calls.filter(q => q.startsWith(prefix)), [prefix + '0', prefix + '1']);
        assert.equal(await page.locator('#search-more article').count(), 1);
        assert(!(await page.locator('#search-more .grid').innerText()).includes(reason));
        assert(await page.locator('#search-more [data-search-amazon]').isVisible());
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.equal(await page.evaluate(() => JSON.stringify(window.searchMoreEvents).includes('hair')), false);
      assert.deepEqual(unexpected, []); assert.deepEqual(errors, []);
      console.log('Search more Chromium ' + mode + ' ' + width + ' ' + theme + ': passed');
      await context.close();
    }
  } finally {
    try {if (browser) await browser.close();}
    finally {fs.rmSync(scratch, {recursive:true, force:true});}
  }
}

module.exports = {buildFixture, waitFor};
if (require.main === module) main().catch(error => {console.error(error); process.exitCode = 1;});
