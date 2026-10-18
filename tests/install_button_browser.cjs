// Full Chromium against a local HTTP publication; every external request is intercepted.
// node tests/install_button_browser.cjs <site-directory> <screenshot-directory>
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {chromium} = require('playwright');
const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1';
const placeholder = '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120"><rect width="120" height="120" fill="#edf1f3"/></svg>';

(async () => {
  const root = path.resolve(process.argv[2]), shots = path.resolve(process.argv[3]);
  let browser, server;
  try {
    browser = await chromium.launch({headless:true, executablePath:chromium.executablePath(),
      args:['--disable-crash-reporter','--disable-breakpad']});
  } catch (e) {
    console.log('Chromium cannot launch: ' + e.message.split('\n')[0]);
    process.exitCode = 77;
    return;
  }
  try {
    server = http.createServer((req,res) => {
      let file = path.resolve(root, '.' + new URL(req.url,'http://localhost').pathname);
      if (file !== root && !file.startsWith(root + path.sep)) {res.writeHead(403);res.end();return;}
      if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file,'index.html');
      if (!fs.existsSync(file)) {res.writeHead(req.url === '/favicon.ico' ? 204 : 404);res.end();return;}
      const type = {'.html':'text/html','.json':'application/json','.webmanifest':'application/manifest+json',
        '.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png'}[path.extname(file)];
      res.writeHead(200,{'Content-Type':type || 'application/octet-stream','Cache-Control':'no-store'});
      res.end(fs.readFileSync(file));
    });
    await new Promise((resolve,reject) => {server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
    const origin = `http://127.0.0.1:${server.address().port}`;
    fs.mkdirSync(shots,{recursive:true});
    async function contextFor(options, fresh = false) {
      const context = await browser.newContext(options);
      // Phones show the header control after 3 minutes on the site; most cases start engaged.
      if (!fresh) await context.addInitScript(() => { try { localStorage.setItem('wp.install.engagedMs', '180000'); } catch (e) {} });
      // Keep the synthetic prompt deterministic even when Chrome offers a real one.
      await context.addInitScript(() => {
        addEventListener('beforeinstallprompt',event => {
          if (event.isTrusted) {event.preventDefault();event.stopImmediatePropagation();}
        });
      });
      await context.route('**/*',route => {
        if (new URL(route.request().url()).origin === origin) return route.continue();
        return route.fulfill(route.request().resourceType() === 'image'
          ? {contentType:'image/svg+xml',body:placeholder}
          : {contentType:'application/json',body:'{}'});
      });
      return context;
    }
    async function offer(page,outcome = 'dismissed') {
      await page.evaluate(outcome => {
        const event = new Event('beforeinstallprompt',{cancelable:true});
        event.prompt = () => {window.installPrompts = (window.installPrompts || 0)+1;return Promise.resolve();};
        event.userChoice = Promise.resolve({outcome});
        dispatchEvent(event);
      },outcome);
    }
    const report = {chromium:browser.version(),installability:[],screenshots:[]};
    for (const [width,height] of [[390,844],[1440,900]]) {
      for (const colorScheme of ['light','dark']) {
        const context = await contextFor({viewport:{width,height},isMobile:width === 390,
          hasTouch:width === 390,colorScheme,reducedMotion:'reduce'});
        const page = await context.newPage(), errors = [];
        page.on('pageerror',e => errors.push(e.message));
        page.on('console',e => {if(e.type() === 'error') errors.push(e.text());});
        await page.goto(origin,{waitUntil:'networkidle'});
        const cdp = await context.newCDPSession(page);
        await cdp.send('Page.enable');
        const manifest = await cdp.send('Page.getAppManifest');
        assert.deepEqual(manifest.errors,[]);
        const result = await cdp.send('Page.getInstallabilityErrors');
        // Playwright contexts are incognito; Chrome reports that one by design. Anything else is a real blocker.
        assert.deepEqual(result.installabilityErrors.filter(e => e.errorId !== 'in-incognito'),[]);
        report.installability.push({width,colorScheme,...result});
        const before = await page.locator('.search').boundingBox();
        await offer(page);
        await page.locator('#pwa-header-install').waitFor({state:'visible'});
        assert.deepEqual(await page.locator('.search').boundingBox(),before);
        const button = await page.locator('#pwa-header-install').boundingBox();
        const join = await page.locator('#subscribe').boundingBox();
        assert(button.height >= 44 && button.width >= 44);
        assert(Math.abs(button.height-join.height) <= 1);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        const name = `home-${width}x${height}-${colorScheme}.png`;
        await page.screenshot({path:path.join(shots,name)});
        report.screenshots.push(name);
        await page.locator('#pwa-header-install').click();
        await page.waitForFunction(() => localStorage.getItem('wp.install.dismissedUntil'));
        assert.equal(await page.evaluate(() => window.installPrompts),1);
        assert(await page.locator('#pwa-header-install').isHidden());
        await page.reload({waitUntil:'networkidle'});
        await offer(page);
        assert(await page.locator('#pwa-header-install').isHidden());
        assert.deepEqual(errors,[]);
        await context.close();
      }
    }
    {
      // A first-time phone visitor: the header control waits for 3 minutes on the site.
      const context = await contextFor({viewport:{width:390,height:844},isMobile:true,hasTouch:true}, true);
      const page = await context.newPage();
      await page.goto(origin,{waitUntil:'networkidle'});
      await offer(page);
      assert(await page.locator('#pwa-header-install').isHidden(), 'phone header waits for engagement');
      await context.close();
    }
    for (const colorScheme of ['light','dark']) {
      const context = await contextFor({viewport:{width:390,height:844},userAgent:iphone,
        isMobile:true,hasTouch:true,colorScheme,reducedMotion:'reduce'});
      const page = await context.newPage(), errors = [];
      page.on('pageerror',e => errors.push(e.message));
      page.on('console',e => {if(e.type() === 'error') errors.push(e.text());});
      await page.goto(origin,{waitUntil:'networkidle'});
      await page.locator('#pwa-header-install').click();
      assert(await page.locator('#pwa-help').evaluate(el => el.open && el.matches(':modal')));
      assert(await page.locator('#pwa-help-close').evaluate(el => el === document.activeElement));
      for (const key of ['Tab','Tab','Shift+Tab','Shift+Tab']) {
        await page.keyboard.press(key);
        assert(await page.locator('#pwa-help').evaluate(el => el.contains(document.activeElement)));
      }
      const name = `ios-sheet-390x844-${colorScheme}.png`;
      await page.screenshot({path:path.join(shots,name)});
      report.screenshots.push(name);
      await page.keyboard.press('Escape');
      await page.locator('#pwa-help').waitFor({state:'hidden'});
      assert(await page.locator('#pwa-help').isHidden());
      assert(await page.locator('#pwa-header-install').evaluate(el => el === document.activeElement));
      await page.locator('#pwa-header-install').click();
      await page.locator('#pwa-help-steps').click();
      assert(await page.locator('#pwa-help').evaluate(el => el.open));
      await page.mouse.click(2,2);
      assert(await page.locator('#pwa-help').isHidden());
      assert(await page.locator('#pwa-header-install').evaluate(el => el === document.activeElement));
      await page.locator('#pwa-ios').click();
      assert(await page.locator('#pwa-help').evaluate(el => el.open && el.matches(':modal')));
      for (const key of ['Shift+Tab','Tab']) {
        await page.keyboard.press(key);
        assert(await page.locator('#pwa-help').evaluate(el => el.contains(document.activeElement)));
      }
      await page.locator('#pwa-help-close').click();
      assert(await page.locator('#pwa-help').isHidden());
      assert(await page.locator('#pwa-ios').evaluate(el => el === document.activeElement));
      assert.deepEqual(errors,[]);
      await context.close();
    }
    fs.writeFileSync(path.join(shots,'chromium.json'),JSON.stringify(report,null,2)+'\n');
    console.log('PASS: Chromium installability, prompt, iOS dialog, focus, no layout shift/overflow or console errors; six screenshots');
  } finally {
    if (server) await new Promise(resolve => server.close(resolve));
    await browser.close();
  }
})().catch(error => {console.error(error);process.exitCode = 1;});
