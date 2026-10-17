// Real Chromium; all requests are served from local files or mocked responses.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  let chromium;
  try {({chromium} = require('playwright'));}
  catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND') throw error;
    console.log('Chromium unavailable: Playwright missing'); process.exitCode = 77; return;
  }
  let browser;
  try {browser = await chromium.launch({headless:true, executablePath:process.argv[3]});}
  catch (error) {
    if (!/Operation not permitted|Executable doesn't exist|Failed to launch|sandbox|Target .* closed/.test(error.message)) throw error;
    console.log('Chromium unavailable: cannot launch local browser'); process.exitCode = 77; return;
  }
  const root = path.resolve(process.argv[2]);
  try {
    for (const width of [390, 1280]) {
      const context = await browser.newContext({viewport:{width, height:844}}), calls = [], external = [], errors = [];
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://wowpriced.test') {
          if (!url.href.startsWith('https://m.media-amazon.com/images/I/')) external.push(url.href);
          return route.abort();
        }
        if (url.pathname === '/api/search') {
          calls.push(url.searchParams.get('q'));
          return route.fulfill({contentType:'application/json', body:JSON.stringify({items:
            url.searchParams.get('q') === 'hijabs' ? [] : [{asin:'B012345678', title:'Cupping set',
              image:'https://m.media-amazon.com/images/I/photo.jpg', price_cents:1599, as_of:new Date().toISOString()}]})});
        }
        let file = path.resolve(root, '.' + url.pathname);
        if (!file.startsWith(root + path.sep) && file !== root) return route.abort();
        if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
        if (!fs.existsSync(file)) return route.fulfill({status:404, body:''});
        return route.fulfill({contentType:({'.html':'text/html', '.json':'application/json', '.js':'application/javascript',
          '.css':'text/css'})[path.extname(file)] || 'application/octet-stream', body:fs.readFileSync(file)});
      });
      await context.addInitScript(() => {window.resultEvents = []; window.wpTrack = (...args) => window.resultEvents.push(args);});
      const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
      await page.goto('https://wowpriced.test/');
      await page.waitForFunction(() => document.querySelector('#content .card'));
      await page.locator('#q').fill('cupping'); await page.waitForTimeout(700);
      assert.deepEqual(calls, []); assert.equal(await page.locator('#amazon-results article').count(), 0);
      await page.goto('https://wowpriced.test/?preview=search');
      // This tab intentionally restores its unmatched private search.
      await page.waitForFunction(() => !document.querySelector('#filterfields').disabled &&
        document.querySelector('#q').value === 'cupping');
      await page.locator('#amazon-results article').waitFor({state:'visible'});
      assert.deepEqual(calls, ['cupping']);
      assert.equal(await page.locator('#content .card[data-key]').count(), 0);
      await page.locator('#q').fill('kettle'); await page.waitForTimeout(700); assert.deepEqual(calls, ['cupping']);
      await page.locator('#q').fill('  CUPPING ');
      await page.locator('#amazon-results article').waitFor({state:'visible'});
      assert.deepEqual(calls, ['cupping']);
      const card = page.locator('#amazon-results article'), link = card.locator('a');
      assert((await card.innerText()).includes('Amazon price as of '));
      assert((await card.innerText()).includes('$15.99'));
      assert.equal(await link.getAttribute('href'), 'https://www.amazon.com/dp/B012345678?tag=example-20');
      assert.equal(await link.getAttribute('target'), '_blank');
      assert.equal(await link.getAttribute('rel'), 'sponsored nofollow noopener');
      assert(await page.locator('[data-search-amazon]').isVisible());
      assert(await page.locator('#amazon-results').evaluate(el => !!(el.compareDocumentPosition(
        document.querySelector('[data-search-amazon]')) & Node.DOCUMENT_POSITION_FOLLOWING)));
      assert.deepEqual(await page.evaluate(() => window.resultEvents.filter(e => e[0] === 'search_amazon_results')),
        [['search_amazon_results', {count:1}]]);
      await page.locator('#q').fill('hijabs'); await page.waitForTimeout(700);
      assert.equal(await page.locator('#amazon-results article').count(), 0);
      assert(await page.locator('[data-search-amazon]').isVisible());
      await page.locator('#q').fill('cupping'); await page.locator('#amazon-results article').waitFor({state:'visible'});
      assert.deepEqual(calls, ['cupping', 'hijabs']);
      for (const query of ['a@b.com', 'https://example.test', 'order 123456']) {
        await page.locator('#q').fill(query); await page.waitForTimeout(500);
      }
      assert.deepEqual(calls, ['cupping', 'hijabs']);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.deepEqual(errors, []); assert.deepEqual(external, []);
      await context.close();
    }
    console.log('Amazon result Chromium smoke passed');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
