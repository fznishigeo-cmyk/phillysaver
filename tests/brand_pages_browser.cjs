// Real browser, local publication only. External traffic is always intercepted.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const pages = ['about', 'join', 'contact', 'careers', 'hacks', 'how-it-works'];
const DAY = 24 * 60 * 60 * 1000;

async function clearSignupMemory(page) {
  await page.evaluate(() => {
    localStorage.removeItem('wp.join.promptUntil');
    localStorage.removeItem('wp.join.joined');
  });
}

(async () => {
  const root = path.resolve(process.argv[2]), output = path.resolve(process.argv[3]);
  let browser;
  try {
    browser = await chromium.launch({headless:true, executablePath:'/opt/pw-browsers/chromium'});
  } catch (error) {
    console.log('Chromium cannot launch: ' + error.message.split('\n')[0]);
    process.exitCode = 77; return;
  }
  fs.mkdirSync(output, {recursive:true});
  try {
    for (const [width, height] of [[390, 844], [1440, 900]]) {
      for (const colorScheme of ['light', 'dark']) {
        const context = await browser.newContext({viewport:{width,height}, colorScheme,
          reducedMotion:'reduce', isMobile:width===390, hasTouch:width===390, serviceWorkers:'block'});
        const unexpected = [];
        await context.route('**/*', route => {
          const request = route.request(), url = new URL(request.url());
          if (request.resourceType() === 'image' && url.hostname === 'images.example.invalid') {
            // Fixture photos do not exist. Preserve their reserved boxes without fake product art.
            return route.fulfill({contentType:'image/png',body:Buffer.from(
              'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1cAAAAASUVORK5CYII=','base64')});
          }
          if (url.origin !== 'https://wowpriced.test' || request.method() !== 'GET') {
            unexpected.push(url.origin); return route.abort();
          }
          let file = path.resolve(root, '.' + url.pathname);
          if (!file.startsWith(root + path.sep)) return route.abort();
          if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
          if (!fs.existsSync(file)) return route.fulfill({status:404,body:''});
          return route.fulfill({contentType:{'.html':'text/html','.json':'application/json','.css':'text/css',
            '.js':'application/javascript'}[path.extname(file)] || 'application/octet-stream',body:fs.readFileSync(file)});
        });
        const page = await context.newPage(), errors = [];
        page.on('pageerror', error=>errors.push(error.message));
        await page.clock.install({time:new Date('2026-10-18T00:00:00Z')});
        await page.clock.pauseAt(new Date('2026-10-18T00:01:00Z'));
        await page.addInitScript(() => { history.scrollRestoration = 'manual'; });
        for (const name of pages) {
          if (page.url().startsWith('https://wowpriced.test/')) {
            await clearSignupMemory(page);
          }
          await page.goto('https://wowpriced.test/' + name + '.html');
          await page.evaluate(()=>scrollTo(0, 0));
          assert.equal(await page.locator('h1').count(), 1);
          assert(await page.locator('.brand-deal').count() >= 3);
          assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth));
          const heading = await page.locator('h1').evaluate(el=>parseFloat(getComputedStyle(el).fontSize));
          assert(heading >= (width === 390 ? 32 : 44));
          assert(await page.locator('.brand-page').evaluate(el=>el.getBoundingClientRect().width <= 1100));
          if (name === 'about') {
            assert.deepEqual(await page.locator('.brand-difference strong').allTextContents(),
              ['Our why', 'How it works', 'Our promise', 'Who we are']);
            assert.deepEqual(await page.locator('.brand-difference .brand-actions a').evaluateAll(
              links=>links.map(el=>[el.getAttribute('href'), getComputedStyle(el).borderRadius])),
              [['/careers', '999px'], ['/contact', '999px']]);
          }
          await page.screenshot({path:path.join(output, `${name}-${width}x${height}-${colorScheme}.png`),fullPage:true});
          if (name === 'join') {
            await page.clock.runFor(30000);
            assert.equal(await page.locator('#join-prompt').count(), 0);
          } else {
            // install() alone keeps time running during screenshots and navigation.
            // runFor() drains callbacks in order; the 25s timeout queues a refresh frame.
            await page.clock.runFor(24999);
            assert(await page.locator('#join-prompt').isHidden());
            await page.clock.runFor(33);
            assert(await page.locator('#join-prompt').isVisible());
            assert.equal(await page.locator('#join-title').innerHTML(), 'Save up to <span>90% OFF</span>');
            assert.equal(await page.locator('#join-description').textContent(),
              "The day's best verified deals in one free email. Unsubscribe anytime.");
            const remaining = await page.evaluate(()=>Number(localStorage.getItem('wp.join.promptUntil')) - Date.now());
            assert(remaining <= DAY && remaining >= DAY - 32);
            assert(await page.locator('#join-email').isVisible());
            assert(await page.locator('#join-email-choice').isHidden());
            assert(await page.locator('#join-back').isHidden());
            assert(await page.locator('#join-google').isHidden());
            if (name === 'about') await page.screenshot({path:path.join(output,
              `join-prompt-${width}x${height}-${colorScheme}.png`)});
            await page.keyboard.press('Escape');
            assert(await page.locator('#join-prompt').isHidden());
            await page.reload(); await page.clock.runFor(30000);
            assert(await page.locator('#join-prompt').isHidden());
            if (name === 'about') {
              // Navigation keeps the daily memory; only independent cases clear it.
              await page.goto('https://wowpriced.test/contact.html');
              await page.clock.runFor(30000);
              assert(await page.locator('#join-prompt').isHidden());
              await page.goto('https://wowpriced.test/about.html');
            }
            await clearSignupMemory(page);
            await page.reload();
            // Native scroll delivery precedes the two queued refresh frames.
            await page.evaluate(() => new Promise(resolve => {
              addEventListener('scroll', () => resolve(), {once:true});
              scrollTo(0, (document.documentElement.scrollHeight - innerHeight) / 2 + 1);
            }));
            await page.clock.runFor(32);
            assert(await page.locator('#join-prompt').isVisible());
            await page.locator('[data-join-close]').click();
            assert(await page.locator('#join-prompt').isHidden());
          }
        }
        // Returning subscribers stay quiet even with an expired daily prompt key.
        await page.evaluate(() => {
          localStorage.setItem('wp.join.joined', '1');
          localStorage.removeItem('wp.join.promptUntil');
        });
        await page.goto('https://wowpriced.test/about.html');
        await page.clock.runFor(30000);
        assert(await page.locator('#join-prompt').isHidden());
        assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
        await context.close();
      }
    }
    console.log('brand pages: 24 page screenshots and 4 signup screenshots; layout and native popup checks passed');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
