// Real Chromium on a local publication: no search ring on click/tap, ring on Tab.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

(async () => {
  const root = path.resolve(process.argv[2]);
  let browser;
  try {
    browser = await chromium.launch({headless:true, executablePath:'/opt/pw-browsers/chromium'});
  } catch (e) {
    console.log('Chromium cannot launch: '+e.message.split('\n')[0]);
    process.exitCode=77;return;
  }
  try {
    for (const page of ['/', '/black-friday/']) for (const [width,height] of [[390,844],[1280,800]]) {
      const mobile = width === 390;
      const context = await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile});
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://wowpriced.test') return route.fulfill({status:204, body:''});
        let file = path.join(root, url.pathname);
        if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
        if (!fs.existsSync(file)) return route.fulfill({status:404, body:''});
        const type = {'.html':'text/html','.js':'application/javascript','.css':'text/css','.json':'application/json'}[path.extname(file)];
        return route.fulfill({contentType:type || 'application/octet-stream', body:fs.readFileSync(file)});
      });
      const tab = await context.newPage();
      await tab.goto('https://wowpriced.test' + page);
      const box = tab.locator('#q');
      const ring = () => tab.evaluate(() => {
        const q = document.getElementById('q');
        return document.activeElement === q ? getComputedStyle(q).outlineStyle : 'unfocused';
      });
      const press = () => mobile ? box.tap() : box.click();
      await press();
      assert.equal(await ring(), 'none', `${page} ${width}: click/tap shows no ring`);
      await box.blur();
      // Tab from the element just before the box.
      await tab.evaluate(() => {
        const q = document.getElementById('q'); let prev = null;
        for (const el of document.querySelectorAll('a,button,input,select,[tabindex]')) {
          if (el === q) break;
          if (el.offsetParent !== null && !el.disabled && el.tabIndex >= 0) prev = el;
        }
        if (prev) prev.focus();
      });
      let reached = false;
      for (let i = 0; i < 6 && !reached; i++) {
        await tab.keyboard.press('Tab');
        reached = await tab.evaluate(() => document.activeElement && document.activeElement.id === 'q');
      }
      assert.ok(reached, `${page} ${width}: Tab reaches the search box`);
      assert.equal(await ring(), 'solid', `${page} ${width}: Tab keeps the ring`);
      await box.blur();
      await press();
      assert.equal(await ring(), 'none', `${page} ${width}: click after Tab shows no ring`);
      console.log(`${page} ${width}: click/tap none, Tab solid, click again none`);
      await context.close();
    }
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
