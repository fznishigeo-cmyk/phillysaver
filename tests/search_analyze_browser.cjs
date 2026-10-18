// Native Chromium, with every request fulfilled offline; screenshots are review artifacts.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');

function buildFixture(scratch, lazy) {
  fs.mkdirSync(scratch, {recursive:true});
  const build = spawnSync('.venv/bin/python', ['-c', `
import sys
from pathlib import Path
from dealradar.publish import site
from tests.test_search_analyze import analyze_config
from tests.test_support import NOW, render
from tests.test_publish_site import make_deal
site.util_now = lambda: NOW
cfg = analyze_config(strip=True)
cfg.search_analyze["lazy"] = sys.argv[2] == "true"
cfg.static_photos = True
render(Path(sys.argv[1]), cfg, [make_deal(item="Steel kitchen kettle", codes=[])])
`, scratch, String(lazy)], {encoding:'utf8'});
  assert.equal(build.status, 0, build.stderr);
  return path.join(scratch, 'site');
}

async function run(root, executablePath, shots) {
  const {chromium} = require('playwright');
  let browser;
  try {browser = await chromium.launch({headless:true, executablePath});}
  catch (error) {
    if (!/Operation not permitted|Executable doesn't exist|Failed to launch|sandbox|Target .* closed/.test(error.message)) throw error;
    console.log('Chromium unavailable: sandbox denied native launch; no screenshots captured'); process.exitCode = 77; return;
  }
  root = path.resolve(root);
  const lazy = fs.existsSync(path.join(root, 'search-analyze.js'));
  if (shots) fs.mkdirSync(shots, {recursive:true});
  try {
    for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({viewport:{width, height:1000}, colorScheme:theme,
        reducedMotion:width === 390 ? 'reduce' : 'no-preference'});
      const calls = [], errors = [], unexpected = [], imports = [];
      let releasePrice, releaseAnalyze;
      const priceReady = new Promise(resolve => {releasePrice = resolve;});
      const analyzeReady = new Promise(resolve => {releaseAnalyze = resolve;});
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://wowpriced.test') {
          if (url.hostname === 'm.media-amazon.com') return route.fulfill({contentType:'image/svg+xml', body:
            '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320" viewBox="0 0 320 320"><rect width="320" height="320" fill="#f5f6f7"/><path d="M208 110Q290 90 270 190Q250 220 215 185" fill="none" stroke="#263e46" stroke-width="15"/><path d="M85 120L65 94L42 95L76 160L85 236Q155 260 228 236L220 120Z" fill="#b9c6cd"/><ellipse cx="152" cy="120" rx="68" ry="14" fill="#5d7681"/><path d="M125 104Q130 80 178 104" fill="none" stroke="#263e46" stroke-width="12"/></svg>'});
          unexpected.push(url.origin); return route.fulfill({status:204, body:''});
        }
        if (url.pathname === '/api/search') {
          calls.push(url.search);
          assert.deepEqual([...url.searchParams.keys()], ['asin']);
          await priceReady;
          return route.fulfill({contentType:'application/json', body:JSON.stringify({items:[{asin:'B012345678',
            title:'Steel electric kettle with temperature control', image:'https://m.media-amazon.com/images/I/photo.jpg',
            price_cents:3499, as_of:new Date().toISOString()}]})});
        }
        if (url.pathname === '/search-analyze.js') {
          imports.push(url.search);
          assert.match(url.search, /^\?v=[a-f0-9]{12}$/);
          await analyzeReady;
        }
        let file = path.resolve(root, '.' + url.pathname);
        if (!file.startsWith(root + path.sep) && file !== root) return route.fulfill({status:404, body:''});
        if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
        if (!fs.existsSync(file)) return route.fulfill({status:404, body:''});
        return route.fulfill({contentType:({'.html':'text/html', '.json':'application/json', '.js':'application/javascript',
          '.css':'text/css', '.svg':'image/svg+xml', '.png':'image/png'})[path.extname(file)] || 'application/octet-stream', body:fs.readFileSync(file)});
      });
      await context.addInitScript(() => {window.analyzeEvents = []; window.wpTrack = (...args) => window.analyzeEvents.push(args);});
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
      await page.goto('https://wowpriced.test/');
      await page.waitForFunction(() => document.querySelector('#content .card'));
      assert.equal(imports.length, 0, 'Analyze loads only after using the search');
      assert.equal(calls.length, 0);
      const query = page.locator('#q');
      await query.focus();
      await query.evaluate(el => {
        el.dispatchEvent(new Event('paste', {bubbles:true}));
        el.value = 'https://www.amazon.com/dp/B012345678?tag=PRIVATE&email=PRIVATE';
        el.dispatchEvent(new Event('input', {bubbles:true}));
      });
      if (width === 390) await page.locator('#analyze-search').click(); else await query.press('Enter');
      if (lazy) {
        await page.waitForTimeout(200);
        assert.equal(calls.length, 0, 'paste and submit wait for the held module');
        await page.locator('#analyze-search').click();
      }
      releaseAnalyze();
      await page.locator('.analyze-progress').waitFor({state:'visible'});
      if (width === 390) assert.equal(await page.locator('.analyze-steps [data-state="working"]').first()
        .evaluate(el => getComputedStyle(el, '::before').animationName), 'none');
      releasePrice();
      const card = page.locator('.analyze-lead #amazon-results article');
      await card.waitFor({state:'visible'});
      assert.equal(calls.length, 1); assert.equal(calls[0], '?asin=B012345678');
      assert.equal(imports.length, lazy ? 1 : 0);
      assert((await card.innerText()).includes('$34.99'));
      assert((await card.innerText()).includes('Amazon price as of '));
      assert.equal(await card.locator('a').getAttribute('href'), 'https://www.amazon.com/dp/B012345678?tag=example-20');
      assert((await card.locator('a').getAttribute('rel')).includes('sponsored'));
      assert(await page.locator('.analyze-related .card').count() > 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.equal(await page.evaluate(() => JSON.stringify(sessionStorage).includes('PRIVATE')), false);
      assert.equal(await page.evaluate(() => JSON.stringify(window.analyzeEvents).includes('B012345678')), false);
      const box = await page.locator('.searchfield').boundingBox(), button = await page.locator('#analyze-search').boundingBox();
      assert(button.x >= box.x && button.x + button.width <= box.x + box.width);
      assert.deepEqual(unexpected, []); assert.deepEqual(errors, []);
      if (shots) await page.screenshot({path:path.join(shots, width + '-' + theme + '.png'), fullPage:true});
      if (lazy) {
        // A new document has a new loader; fail just its module request.
        await context.route('**/search-analyze.js?*', route => route.fulfill({status:503,
          contentType:'application/javascript', body:''}));
        errors.length = 0;
        await page.reload();
        await page.waitForFunction(() => document.querySelector('#content .card'));
        await query.fill('https://amazon.com/dp/B012345678?email=PRIVATE');
        await page.locator('#analyze-search').click();
        const fallback = page.locator('#search-amazon-result a');
        await fallback.waitFor({state:'visible'});
        assert.equal(await fallback.getAttribute('href'), 'https://www.amazon.com/?tag=example-20');
        assert((await fallback.getAttribute('rel')).includes('sponsored'));
        await query.fill('kettle'); await query.press('Enter');
        await page.locator('#content .card').first().waitFor({state:'visible'});
        assert.equal(calls.length, 1, 'failed import leaves plain local search usable');
        assert.equal(await page.evaluate(() => JSON.stringify(sessionStorage).includes('PRIVATE')), false);
        assert(errors.every(error => /Failed to load resource.*503/.test(error)), errors.join('\n'));
      }
      await context.close();
    }
    console.log('Analyze search Chromium ' + (lazy ? 'lazy' : 'inline') + ': 390/1440 light/dark passed');
  } finally {await browser.close();}
}

// Native usage: node tests/search_analyze_browser.cjs /path/to/chromium [screenshots]
async function main() {
  if (process.argv.length >= 5) return run(process.argv[2], process.argv[3], process.argv[4]);
  const scratch = fs.mkdtempSync('/tmp/task295-analyze-browser-');
  try {
    for (const lazy of [false, true]) {
      const mode = lazy ? 'lazy' : 'inline';
      const root = buildFixture(path.join(scratch, mode), lazy);
      await run(root, process.argv[2], process.argv[3] && path.join(process.argv[3], mode));
      if (process.exitCode === 77) return;
    }
  } finally {fs.rmSync(scratch, {recursive:true, force:true});}
}

module.exports = {buildFixture};
if (require.main === module) main().catch(error => {console.error(error); process.exitCode = 1;});
