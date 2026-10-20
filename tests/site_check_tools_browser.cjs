// Native monitor timing against a local publication. No requests reach the network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {buildFixture} = require('./tool_pages_browser.cjs');
const {monitor, SITE} = require('./site_check_tools.cjs');
const {routeRobotSearch} = require('../scripts/robot_search.cjs');

async function run(executablePath) {
  const {chromium} = require('playwright');
  const browser = await chromium.launch({headless:true, executablePath});
  const scratch = fs.mkdtempSync('/tmp/task348-browser-');
  try {
    const root = buildFixture(scratch), source = fs.readFileSync(path.join(__dirname, '../scripts/site_check.js'), 'utf8');
    // Same pacing and guard; continue becomes fallback only to serve local files below.
    const guardCode = source.slice(source.indexOf('const CARDS ='), source.indexOf('function checkFooter('))
      .replace('await route.continue();', 'await route.fallback();');
    const guard = vm.runInNewContext(guardCode + '\n({guardPage, requests:() => siteRequests})',
      {SITE, URL, BLOCK:/google|facebook|tiktok|clarity|bing/, routeRobotSearch, Date, Promise, setTimeout});
    let seconds = 0;
    for (const width of [1440, 390]) {
      const context = await browser.newContext({viewport:{width, height:900}, isMobile:width === 390,
        serviceWorkers:'block', userAgent:'wowpriced-site-check/1.0 (+https://wowpriced.com; hello@wowpriced.com)'});
      const escaped = [], documents = [], searches = [], errors = [];
      await context.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url());
        if (url.origin !== new URL(SITE).origin) { escaped.push(url.href); return route.abort(); }
        if (url.pathname === '/api/search') { searches.push(url.href); return route.abort(); }
        if (request.isNavigationRequest()) documents.push(url.pathname);
        let file = path.resolve(root, '.' + url.pathname);
        assert(file.startsWith(root + path.sep) || file === root);
        if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
        return route.fulfill({status:fs.existsSync(file) ? 200 : 404,
          contentType:({'.html':'text/html','.js':'application/javascript','.css':'text/css','.json':'application/json'})[path.extname(file)] || 'application/octet-stream',
          body:fs.existsSync(file) ? fs.readFileSync(file) : ''});
      });
      const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
      await guard.guardPage(page);
      // Home photos are local too, before the Tools-only guard takes over.
      await page.route('https://m.media-amazon.com/**', route => route.abort());
      await page.goto(SITE, {waitUntil:'load'});
      await page.locator('#q').waitFor({state:'visible'});
      const before = guard.requests(), started = performance.now();
      const note = await monitor()(page, 'example-20');
      const elapsed = (performance.now() - started) / 1000; seconds += elapsed;
      assert.deepEqual(documents, ['/', '/amazon-deal-checker/', '/amazon-discount-finder/']);
      assert.deepEqual(searches, []); assert.deepEqual(escaped, []); assert.deepEqual(errors, []);
      console.log(`Chromium ${width}: ${note}; ${guard.requests() - before} added site requests; ${elapsed.toFixed(2)}s measured`);
      await context.close();
    }
    console.log(`Tools added site-check time: ${seconds.toFixed(2)}s; site requests including both homes ${guard.requests()}/120`);
  } finally { await browser.close(); fs.rmSync(scratch, {recursive:true, force:true}); }
}
if (require.main === module) run(process.argv[2]).catch(error => {console.error(error.message); process.exitCode = 1;});
