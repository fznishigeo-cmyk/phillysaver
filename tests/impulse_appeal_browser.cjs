// Serve only the disposable fixture through intercepted browser requests.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
(async () => {
  const {chromium} = require(require.resolve('playwright', {paths: ['/opt/node-tools', __dirname]}));
  let browser;
  try {
    browser = await chromium.launch({executablePath: '/opt/pw-browsers/chromium',
      args: ['--no-sandbox', '--disable-dev-shm-usage']});
  } catch (error) {
    console.log('Chromium cannot launch: ' + error.message);
    process.exitCode = 77;
    return;
  }
  try {
    const root = path.resolve(process.argv[2]);
    const doc = JSON.parse(fs.readFileSync(path.join(root, 'deals.json'), 'utf8'));
    const expected = JSON.parse(fs.readFileSync(path.join(root, 'expected.json'), 'utf8'));
    const allure = new Set(doc.deals.filter(d => d.allure).map(d => d.key));
    assert(allure.size > 0, 'smoke fixture must exercise allure');
    for (const viewport of [{width: 390, height: 844}, {width: 1280, height: 800}]) {
      for (const colorScheme of ['light', 'dark']) {
        const context = await browser.newContext({viewport, colorScheme});
        const errors = [], outside = [];
        await context.route('**/*', route => {
          const url = new URL(route.request().url());
          if (route.request().resourceType() === 'image') return route.fulfill({contentType: 'image/png',
            body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1cAAAAASUVORK5CYII=', 'base64')});
          if (url.origin !== 'https://wowpriced.test') {outside.push(url.origin); return route.abort();}
          let file = path.resolve(root, '.' + url.pathname);
          if (!file.startsWith(root + path.sep) && file !== root) return route.abort();
          if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
          if (!fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
          const contentType = {'.html': 'text/html', '.json': 'application/json', '.js': 'application/javascript',
            '.css': 'text/css', '.webmanifest': 'application/manifest+json'}[path.extname(file)];
          return route.fulfill({contentType: contentType || 'application/octet-stream', body: fs.readFileSync(file)});
        });
        const page = await context.newPage();
        page.on('pageerror', error => errors.push(error.message));
        await page.clock.setFixedTime(Date.parse(doc.meta.published_at) + 1000);
        await page.goto('https://wowpriced.test/', {waitUntil: 'load'});
        await page.waitForSelector('#content .card[data-key]');
        for (const tab of [0, 1]) {
          await page.locator(`#cattabs [data-i="${tab}"]`).click();
          const keys = await page.locator('#content .grid:not(.spotlight-grid) .card[data-key]')
            .evaluateAll(cards => cards.map(card => card.dataset.key));
          if (tab === 0) assert.deepEqual(keys, expected.slice(0, keys.length), 'native order differs from Python');
          const positions = keys.flatMap((key, i) => allure.has(key) ? [i] : []);
          assert.equal(positions[0], 2);
          assert(positions.every((pos, i) => !i || pos - positions[i - 1] >= 4));
        }
        assert.deepEqual(errors, []);
        assert.deepEqual(outside, []);
        await context.close();
      }
    }
    console.log('Chromium phone/desktop light/dark: allure 3/4 order agrees with Python.');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
