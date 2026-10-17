// Real Chromium, entirely intercepted local fixtures; only launch failures skip.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(require.resolve('playwright', {paths:['/opt/node-tools', __dirname]}));

(async () => {
  let browser;
  try {
    browser = await chromium.launch({headless:true, executablePath:'/opt/pw-browsers/chromium',
      args:['--no-sandbox', '--disable-dev-shm-usage']});
  } catch (error) {
    if (!/Operation not permitted|Executable doesn't exist|Target page, context or browser has been closed/.test(error.message)) throw error;
    console.log('Chromium cannot launch (exit 77): ' + error.message.split('\n')[0]);
    process.exitCode = 77; return;
  }
  const root = path.resolve(process.argv[2]);
  const doc = JSON.parse(fs.readFileSync(path.join(root, 'deals.json'), 'utf8'));
  const niche = new Set(doc.deals.filter(d => d.niche).map(d => d.key));
  assert.equal(niche.size, 3);
  try {
    for (const [width, height] of [[390,844], [1280,800]]) {
      for (const colorScheme of ['light', 'dark']) {
        const context = await browser.newContext({viewport:{width,height}, colorScheme});
        const errors = [], outside = [];
        await context.route('**/*', route => {
          const request = route.request(), url = new URL(request.url());
          if (request.resourceType() === 'image') return route.fulfill({contentType:'image/svg+xml',
            body:'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400"/>'});
          if (url.origin !== 'https://wowpriced.test' || request.method() !== 'GET') {
            outside.push(url.origin); return route.abort();
          }
          let file = path.resolve(root, '.' + decodeURIComponent(url.pathname));
          assert(file === root || file.startsWith(root + path.sep));
          if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
          assert(fs.existsSync(file), url.pathname);
          const contentType = {'.html':'text/html', '.json':'application/json',
            '.js':'application/javascript', '.css':'text/css', '.xml':'application/xml'}[path.extname(file)];
          return route.fulfill({contentType:contentType || 'application/octet-stream', body:fs.readFileSync(file)});
        });
        const page = await context.newPage();
        page.on('pageerror', error => errors.push(error.message));
        page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
        await page.goto('https://wowpriced.test/', {waitUntil:'load'});
        await page.waitForSelector('#content .card[data-key]');
        for (const sort of ['top:-1', 'fresh:-1']) {
          await page.selectOption('#sortsel', sort);
          const keys = await page.locator('#content .card[data-key]').evaluateAll(cards =>
            cards.map(card => card.dataset.key));
          assert.equal(keys.length, 43);
          assert(keys.slice(0, 40).every(key => !niche.has(key)), 'ordinary cards lead');
          assert(keys.slice(40).every(key => niche.has(key)), 'utility cards follow');
        }
        assert.deepEqual(errors, []);
        assert.deepEqual(outside, []);
        await context.close();
      }
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
