// Native command: node tests/drop_shuffle_browser.cjs /path/to/chromium
// Every request is fulfilled locally; --build-only validates fixtures without a browser.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {spawnSync} = require('node:child_process');

function buildFixture(scratch) {
  const result = spawnSync('.venv/bin/python', ['-c', `
import sys
from datetime import timedelta
from pathlib import Path
from dealradar.config import load_config
from dealradar.publish import site
from tests.test_support import NOW, render
from tests.test_publish_site import make_deal
site.util_now = lambda: NOW + timedelta(seconds=37)
cfg = load_config()
cfg.tracking = {}
cfg.join_prompt = False
cfg.keepa_click_price_rule = "off"
cfg.non_amazon_freshness_enabled = False
rows = [make_deal(dedupe_key=f"task300-{i}", item=f"Product{i} kitchen set", brand=f"Brand{i}",
    category=["home", "kitchen", "electronics", "beauty"][i % 4],
    store=["Walmart", "Target", "Best Buy", "Woot", "eBay"][i % 5], codes=[], conditions=[],
    image_url="https://images.example.invalid/card.png", first_seen_at=NOW.isoformat(),
    price_rechecked_at=NOW.isoformat()) for i in range(144)]
render(Path(sys.argv[1]), cfg, rows)
`, scratch], {encoding:'utf8'});
  assert.equal(result.status, 0, result.stdout + result.stderr);
  const root = path.join(scratch, 'site'), doc = JSON.parse(fs.readFileSync(path.join(root, 'deals.json'), 'utf8'));
  assert(doc.deals.filter(d => d.pick != null).length >= 72, 'browser fixture needs at least 72 ranked picks');
  assert(fs.readFileSync(path.join(root, 'index.html'), 'utf8').includes('function shuffleDropPicks('));
  return {root, doc};
}

(async () => {
  const scratch = fs.mkdtempSync('/tmp/task300-drop-browser-');
  let browser;
  try {
    const {root, doc} = buildFixture(scratch);
    if (process.argv[2] === '--build-only') {console.log('Drop browser fixture passed');return;}
    const {chromium} = require('playwright');
    browser = await chromium.launch({headless:true, executablePath:process.argv[2] || '/opt/pw-browsers/chromium'});
    for (const width of [390, 1280]) for (const motion of ['reduce','no-preference']) {
      const context = await browser.newContext({viewport:{width,height:844}, reducedMotion:motion, serviceWorkers:'block'});
      const errors=[], outside=[], requests=[];
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (route.request().resourceType() === 'image') return route.fulfill({contentType:'image/svg+xml',body:
          '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320"><rect width="320" height="320" fill="#39565d"/></svg>'});
        if (url.origin !== 'https://wowpriced.test') {outside.push(url.origin);return route.abort();}
        if (url.pathname === '/deals.json') requests.push(route.request().method());
        let file = path.resolve(root, '.' + url.pathname);
        if (!file.startsWith(root + path.sep) && file !== root) return route.abort();
        if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file=path.join(file,'index.html');
        if (!fs.existsSync(file)) return route.fulfill({status:404,body:''});
        const type={'.html':'text/html','.json':'application/json','.js':'application/javascript','.webmanifest':'application/manifest+json'}[path.extname(file)];
        return route.fulfill({contentType:type || 'application/octet-stream',headers:{etag:'unchanged'},body:fs.readFileSync(file)});
      });
      const page=await context.newPage();
      page.on('pageerror', e=>errors.push(e.message));
      page.on('console', m=>{if(m.type()==='error') errors.push(m.text());});
      const step=doc.meta.cadence_minutes*60000, start=Math.floor(Date.parse(doc.meta.published_at)/step)*step;
      await page.clock.setFixedTime(start+step-2000);
      await page.goto('https://wowpriced.test/',{waitUntil:'load'});
      await page.waitForFunction(()=>sessionStorage.getItem('wp.browse'));
      await page.waitForTimeout(800);
      const cards='#content .grid:not(.spotlight-grid) .card[data-key]';
      const order=()=>page.locator(cards).evaluateAll(nodes=>nodes.map(el=>el.dataset.key));
      const geometry=()=>page.locator(cards).evaluateAll(nodes=>nodes.slice(0,15).map(el=>{
        const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};}));
      let previous=await order();
      const originalGeometry=await geometry();
      await page.evaluate(()=>{
        window.dropCLS=0;
        new PerformanceObserver(list=>list.getEntries().forEach(e=>{if(!e.hadRecentInput) window.dropCLS+=e.value;}))
          .observe({type:'layout-shift'});
      });
      for(let drop=1;drop<=3;drop++) {
        await page.clock.setFixedTime(start+step*drop);
        await page.waitForFunction(({selector, previous})=>
          Array.from(document.querySelectorAll(selector)).slice(0,15).map(el=>el.dataset.key).join(',')!==previous,
        {selector:cards,previous:previous.slice(0,15).join(',')});
        await page.waitForFunction(()=>document.getElementById('countdown').textContent!=='0:00');
        await page.waitForTimeout(800);
        const current=await order();
        assert.notDeepEqual(current.slice(0,15),previous.slice(0,15));
        assert.deepEqual(await geometry(),originalGeometry,'same card-slot geometry after a clock-only drop');
        assert.equal(await page.evaluate(()=>scrollY),0);
        assert(await page.evaluate(()=>window.dropCLS)<=0.001,'clock-only drops must not cause CLS');
        await page.reload({waitUntil:'load'});
        await page.waitForFunction(key=>document.querySelector('#content .grid:not(.spotlight-grid) .card')?.dataset.key===key,current[0]);
        await page.waitForTimeout(800);
        assert.deepEqual(await order(),current,'exact reload in the current mark');
        await page.locator('#cattabs .cattab').nth(1).click();
        await page.goBack();
        await page.waitForTimeout(800);
        assert.deepEqual(await order(),current,'Back retains the current selection');
        previous=current;
        // Reload removes observers; begin another measurement after restoration settles.
        await page.evaluate(()=>{window.dropCLS=0;new PerformanceObserver(list=>list.getEntries().forEach(e=>{
          if(!e.hadRecentInput) window.dropCLS+=e.value;})).observe({type:'layout-shift'});});
      }
      assert(requests.length>=4);
      assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);
      console.log(JSON.stringify({width,motion,drops:3,unchangedPublication:true,layout:'unchanged'}));
      await context.close();
    }
  } finally {if(browser) await browser.close();fs.rmSync(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
