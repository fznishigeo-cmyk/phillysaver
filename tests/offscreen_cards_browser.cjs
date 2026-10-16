// Serve a synthetic built site from disk through Playwright, with no external
// requests or browser downloads. Reload and Back deliberately omit bfcache.
// node tests/offscreen_cards_browser.cjs <site-directory> [chromium-path]
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  let chromium;
  try { ({chromium} = require('playwright')); }
  catch (e) {
    if (e.code !== 'MODULE_NOT_FOUND') throw e;
    console.log('Chromium check unavailable: Playwright is not installed.');process.exitCode=77;return;
  }
  const root = path.resolve(process.argv[2]);
  const installed = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
  const executablePath = process.argv[3] || (fs.existsSync(installed) ? installed : undefined);
  let browser;
  try { browser = await chromium.launch({headless:true, executablePath}); }
  catch (e) {
    if (!/Operation not permitted|Executable doesn't exist/.test(e.message)) throw e;
    console.log('Chromium check unavailable: sandbox launch denied or no installed browser.');
    process.exitCode=77;return;
  }
  try {
    for (const [width,height] of [[390,844],[1280,800]]) {
      const context = await browser.newContext({viewport:{width,height},isMobile:width===390,hasTouch:width===390});
      await context.route('**/*', route => {
        const request = route.request(), url = new URL(request.url());
        if (request.resourceType()==='image') return route.fulfill({contentType:'image/png',
          body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1cAAAAASUVORK5CYII=','base64')});
        if (url.origin!=='https://wowpriced.test' || request.method()!=='GET') return route.abort();
        let file = path.resolve(root,'.'+url.pathname);
        if (!file.startsWith(root+path.sep) && file!==root) return route.abort();
        if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file=path.join(file,'index.html');
        if (!fs.existsSync(file)) return route.fulfill({status:404,body:''});
        const type = {'.html':'text/html','.json':'application/json','.js':'application/javascript',
          '.css':'text/css','.webmanifest':'application/manifest+json'}[path.extname(file)];
        return route.fulfill({contentType:type || 'application/octet-stream',body:fs.readFileSync(file)});
      });
      const page = await context.newPage(), errors=[];
      page.on('pageerror', e=>errors.push(e.message));
      const ready = async (restoring=false) => {
        await page.waitForFunction(()=>sessionStorage.getItem('wp.browse'));
        // Stored tab state already exists on reload. Wait for the dynamic
        // wall, rather than treating the server's shorter static wall as ready.
        if (restoring) await page.waitForFunction(()=>document.querySelectorAll('#content .card').length>=120);
        else await page.locator('#moremore').waitFor();
        // Includes the late pageshow Saved redraw and the bounded correction.
        await page.waitForTimeout(700);
      };
      const snapshot = () => page.evaluate(()=>{
        const memory=JSON.parse(sessionStorage.getItem('wp.browse'));
        return memory.entries[history.state.wpBrowse].anchor;
      });
      const top = key => page.locator('#content .card[data-key="'+key+'"]').evaluate(el=>el.getBoundingClientRect().top);
      await page.goto('https://wowpriced.test/',{waitUntil:'load'});await ready();
      while (await page.locator('#content .card').count()<120) await page.locator('#moremore').click();
      const card = page.locator('#content .card').nth(119);
      await card.scrollIntoViewIfNeeded();
      await card.evaluate(el=>scrollTo({top:scrollY+el.getBoundingClientRect().top,behavior:'instant'}));
      await page.waitForTimeout(300);
      const saved = await snapshot();assert(saved);
      await page.reload({waitUntil:'load'});await ready(true);
      const reloadError = Math.abs(await top(saved.key)-saved.offset);
      assert(reloadError<=4,`${width}: reload drift ${reloadError}px`);
      const link = page.locator('#content .card[data-key="'+saved.key+'"] .title a');
      // Trigger native navigation without moving the viewport for a click.
      await link.evaluate(el=>el.click());await page.waitForURL('**/deal/**');
      await page.goBack({waitUntil:'load'});await ready(true);
      const backError = Math.abs(await top(saved.key)-saved.offset);
      assert(backError<=4,`${width}: Back drift ${backError}px`);
      const sizing = await page.locator('#content').evaluate(wall=>({
        enabled:getComputedStyle(wall).getPropertyValue('--offscreen-cards').trim()==='auto',
        measured:wall.style.getPropertyValue('--offscreen-card-height'),
        first:Array.from(wall.querySelectorAll('.grid:not(.spotlight-grid) > .card')).slice(0,8)
          .map(el=>getComputedStyle(el).contentVisibility)
      }));
      assert(sizing.first.every(value=>value==='visible'));
      if (sizing.enabled) assert(parseFloat(sizing.measured)>0);
      assert.deepEqual(errors,[]);
      console.log(JSON.stringify({width,height,reloadError,backError,...sizing}));
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
