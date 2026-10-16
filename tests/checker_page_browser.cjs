// A real loopback static server, installed Chromium, and no external requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {once} = require('node:events');

(async () => {
  let chromium;
  try { ({chromium} = require('playwright')); }
  catch (e) {if (e.code !== 'MODULE_NOT_FOUND') throw e;console.log('Playwright unavailable');process.exitCode=77;return;}
  const root = path.resolve(process.argv[2]);
  const fixtures = JSON.parse(fs.readFileSync(path.join(root,'browser-fixtures.json')));
  const executablePath = '/opt/pw-browsers/chromium';
  let browser;
  try {browser = await chromium.launch({headless:true, executablePath});}
  catch (e) {
    if (!/Operation not permitted|Executable doesn't exist/.test(e.message)) throw e;
    console.log('Chromium unavailable: sandbox launch denied or no installed browser');process.exitCode=77;return;
  }
  const requests = [], outside = [], errors = [], trackerViews = [];
  const server = http.createServer((req,res) => {
    requests.push(req.url);
    const url = new URL(req.url,'http://localhost');
    let file = path.resolve(root,'.'+decodeURIComponent(url.pathname));
    if (!file.startsWith(root + path.sep) && file !== root) {res.writeHead(403).end();return;}
    if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file,'index.html');
    if (!fs.existsSync(file)) {res.writeHead(404).end();return;}
    res.setHeader('content-type', {'.html':'text/html; charset=utf-8','.json':'application/json','.js':'application/javascript'}[path.extname(file)] || 'application/octet-stream');
    res.end(fs.readFileSync(file));
  });
  try {
    server.listen(0,'127.0.0.1');await once(server,'listening');
    const origin = 'http://127.0.0.1:' + server.address().port;
    for (const [width,height,colorScheme] of [[390,844,'light'],[1280,800,'dark']]) {
      const context = await browser.newContext({viewport:{width,height},colorScheme});
      await context.exposeFunction('recordTrackerLocation', view => trackerViews.push(view));
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (['www.googletagmanager.com','www.clarity.ms','connect.facebook.net'].includes(url.hostname)) {
          return route.fulfill({contentType:'application/javascript',body:'window.recordTrackerLocation({url:location.href,referrer:document.referrer});'});
        }
        if (url.origin !== origin) {outside.push(route.request().url());return route.abort();}
        return route.continue();
      });
      await context.addInitScript(now => {Date.now = () => Date.parse(now);}, fixtures.now);
      const page = await context.newPage();page.on('pageerror',e => errors.push(e.message));
      const result = page.locator('#check-result'), input = page.getByLabel('Product link',{exact:true});
      const check = async url => {
        await input.fill(url);await page.getByRole('button',{name:'Check',exact:true}).click();
        await page.waitForFunction(() => {
          const text = document.getElementById('check-result').textContent;
          return text && !text.includes('Checking our verified records');
        });
      };
      await page.goto(origin+'/check/');
      // Keyboard only: first Tab is the brand, second is the labeled input.
      await page.keyboard.press('Tab');await page.keyboard.press('Tab');
      assert(await input.evaluate(el => el === document.activeElement));
      await page.keyboard.type(fixtures.urls.bestbuy);await page.keyboard.press('Tab');
      assert(await page.getByRole('button',{name:'Check',exact:true}).evaluate(el => el === document.activeElement));
      await page.keyboard.press('Enter');await result.getByRole('link',{name:'See the deal',exact:true}).waitFor();
      assert.match(await result.innerText(), /Yes — \$10.00/);
      assert.match(await result.innerText(), /Best Buy · checked just now/);
      assert.match(await result.innerText(), /via Slickdeals/);
      assert.equal(await result.getByRole('link',{name:'Slickdeals',exact:true}).getAttribute('rel'),'noopener');
      assert(await result.locator('time').getAttribute('title'));
      assert.equal(await result.getByRole('link',{name:'Price history',exact:true}).count(),1);
      assert.equal(new URL(page.url()).searchParams.get('u'), fixtures.urls.bestbuy);
      await check(fixtures.urls.target);
      assert.match(await result.innerText(), /Last verified price \$10.00 on .* — this deal has ended/);
      assert.equal(await result.getByRole('link',{name:'See the deal',exact:true}).count(),0);
      await check(fixtures.urls.woot);
      assert.equal(await result.getByRole('heading').textContent(), 'Widget woot');
      assert.equal(await result.getByRole('link').textContent(), 'See the deal');
      assert.equal(await result.getByRole('link').count(),1);
      assert.equal(await result.locator('time').count(),0);
      await check(fixtures.urls.amazon);
      assert((await result.innerText()).includes('Amazon price as of '+fixtures.amazon_as_of));
      await result.getByText('Details',{exact:true}).click();
      assert.match(await result.innerText(), /Product prices and availability are accurate/);
      await check('https://shop.example/products/unverified-air-fryer?private=must-not-leave');
      assert.match(await result.innerText(), /We haven't verified a price for this product yet\./);
      const search = result.getByRole('link');
      assert.equal(await search.getAttribute('href'), '/');
      const beforeShort = requests.filter(u => u.startsWith('/check/data/')).length;
      await check('https://amzn.to/short');
      assert.equal((await result.innerText()).trim(), 'Open the link first, then paste the full product address');
      assert.equal(requests.filter(u => u.startsWith('/check/data/')).length,beforeShort);
      // Prefill runs immediately; no form submission or retailer request.
      await page.goto(origin+'/check/?u='+encodeURIComponent(fixtures.urls.bestbuy));
      assert.equal(await input.inputValue(),fixtures.urls.bestbuy);
      await result.getByRole('link',{name:'See the deal',exact:true}).waitFor();
      assert.match(await result.innerText(), /Yes — \$10.00/);
      // Tracking scripts see only a plain home URL; the query stays in this tab.
      const beforeTrackers = trackerViews.length;
      await check('https://shop.example/products/widget');await result.getByRole('link').click();
      await page.waitForURL(url => url.origin === origin && url.pathname === '/');
      await page.waitForFunction(() => document.getElementById('q').value === 'widget');
      assert.equal(new URL(page.url()).search,'');
      await page.waitForFunction(() => window.dataLayer && window.clarity && window.fbq);
      for(let i=0;i<100&&trackerViews.length<beforeTrackers+3;i++) await new Promise(resolve=>setTimeout(resolve,5));
      assert.equal(trackerViews.length,beforeTrackers+3);
      assert(trackerViews.slice(beforeTrackers).every(view=>!view.url.includes('q=')&&!view.referrer.includes('q=')));
      // Reader geometry and targets in both themes.
      await page.goto(origin+'/check/');
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      for (const selector of ['#product-url','#check-form button','.brand a']) {
        assert((await page.locator(selector).boundingBox()).height >= 44,selector);
      }
      await context.close();
    }
    assert.deepEqual(outside,[]);assert.deepEqual(errors,[]);
    for (const request of requests.filter(u => u.startsWith('/check/data/'))) assert.match(request,/^\/check\/data\/[a-f0-9]{2}\.json$/);
    assert(!requests.some(u => u.includes('must-not-leave')));
  } finally {server.close();await browser.close();}
})().catch(e => {console.error(e);process.exitCode=1;});
