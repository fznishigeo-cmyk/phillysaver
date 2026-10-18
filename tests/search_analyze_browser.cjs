// Native Chromium, with every request fulfilled offline; screenshots are review artifacts.
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
    console.log('Chromium unavailable: sandbox denied native launch; no screenshots captured'); process.exitCode = 77; return;
  }
  const root = path.resolve(process.argv[2]), shots = path.resolve(process.argv[4]);
  fs.mkdirSync(shots, {recursive:true});
  try {
    for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({viewport:{width, height:1000}, colorScheme:theme,
        reducedMotion:width === 390 ? 'reduce' : 'no-preference'});
      const calls = [], errors = [], unexpected = [];
      let releasePrice;
      const priceReady = new Promise(resolve => {releasePrice = resolve;});
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
      const query = page.locator('#q');
      await query.fill('https://www.amazon.com/dp/B012345678?tag=PRIVATE&email=PRIVATE');
      if (width === 390) await page.locator('#analyze-search').click(); else await query.press('Enter');
      await page.locator('.analyze-progress').waitFor({state:'visible'});
      if (width === 390) assert.equal(await page.locator('.analyze-steps [data-state="working"]').first()
        .evaluate(el => getComputedStyle(el, '::before').animationName), 'none');
      releasePrice();
      const card = page.locator('.analyze-lead #amazon-results article');
      await card.waitFor({state:'visible'});
      assert.equal(calls.length, 1); assert.equal(calls[0], '?asin=B012345678');
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
      await page.screenshot({path:path.join(shots, width + '-' + theme + '.png'), fullPage:true});
      await context.close();
    }
    console.log('Analyze search Chromium: 390/1440 light/dark passed; no errors or horizontal scroll');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
