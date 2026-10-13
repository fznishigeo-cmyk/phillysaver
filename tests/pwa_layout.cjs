// 390x844 normal-browser check of two local publishes, with all network mocked.
// node tests/pwa_layout.cjs <before-site-directory> <after-site-directory>
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

(async () => {
  const dirs = process.argv.slice(2);
  if (dirs.length !== 2) throw Error('Provide the before and after site directories.');
  const browser = await chromium.launch({headless: true});
  try {
    const results = [];
    for (const directory of dirs) {
      const root = path.resolve(directory);
      const context = await browser.newContext({viewport: {width: 390, height: 844},
        isMobile: true, hasTouch: true, colorScheme: 'light'});
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.hostname !== 'wowpriced.test') return route.abort();
        let file = path.resolve(root, '.' + url.pathname);
        if (file !== root && !file.startsWith(root + path.sep)) return route.abort();
        if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
        if (!fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
        const type = {'.html': 'text/html', '.json': 'application/json',
          '.webmanifest': 'application/manifest+json', '.png': 'image/png'}[path.extname(file)];
        return route.fulfill({status: 200, contentType: type || 'text/plain', body: fs.readFileSync(file)});
      });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto('https://wowpriced.test/', {waitUntil: 'networkidle'});
      await page.locator('.grid .card').first().waitFor();
      const top = await page.locator('.grid .card').first().evaluate(card => card.getBoundingClientRect().top + scrollY);
      const outgoing = await page.locator('.grid a.getdeal').evaluateAll(links => links.map(link =>
        [link.getAttribute('href'), link.getAttribute('target'), link.getAttribute('rel')]));
      if (await page.locator('#pwa-install').count()) {
        assert.equal(await page.locator('#pwa-install').isVisible(), false);
        await page.evaluate(() => {
          const event = new Event('beforeinstallprompt', {cancelable: true});
          event.prompt = () => Promise.resolve();
          event.userChoice = Promise.resolve({outcome: 'dismissed'});
          dispatchEvent(event);
        });
        const button = await page.locator('#pwa-install').boundingBox();
        assert.ok(button.height >= 44 && button.width >= 44);
        assert.equal(await page.locator('.grid .card').first().evaluate(card => card.getBoundingClientRect().top + scrollY), top);
      }
      assert.deepEqual(errors, []);
      results.push({firstCardTop: top, outgoing});
      await context.close();
    }
    assert.equal(results[0].firstCardTop, results[1].firstCardTop);
    assert.deepEqual(results[0].outgoing, results[1].outgoing);
    console.log(JSON.stringify({viewport: '390x844', before: results[0].firstCardTop,
      after: results[1].firstCardTop, shift: results[1].firstCardTop - results[0].firstCardTop}));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
