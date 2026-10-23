// Native command: node tests/drop_shuffle_browser.cjs /path/to/chromium
// Every request is fulfilled locally; --build-only validates fixtures without a browser.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {spawnSync} = require('node:child_process');

function buildFixture(scratch) {
  const result = spawnSync('.venv/bin/python', ['-c', `
import sys, re
from html import unescape
from datetime import timedelta
from pathlib import Path
from dealradar.config import load_config
from dealradar.publish import site, drop_fresh
from tests.test_support import NOW, render
from tests.test_publish_site import make_deal
site.util_now = lambda: NOW + timedelta(minutes=16, seconds=37)
cfg = load_config()
cfg.tracking = {}
cfg.join_prompt = False
cfg.keepa_click_price_rule = "off"
cfg.non_amazon_freshness_enabled = False
cfg.seo_event_pages_events[0]['date'] = NOW.date().isoformat()
rows = [make_deal(dedupe_key=f"task300-{i}", item=f"Product{i} kitchen set", brand=f"Brand{i}",
    category=["home", "kitchen", "electronics", "beauty", "health", "grocery", "toys_games", "kids_baby"][i % 8],
    store=["Walmart", "Target", "Best Buy", "Woot", "eBay"][i % 5], codes=[], conditions=[],
    image_url="https://images.example.invalid/card.png", first_seen_at=NOW.isoformat(),
    price_rechecked_at=NOW.isoformat()) for i in range(323)]
for i, color in enumerate(["Black", "White", "Blue", "Red"]):
    rows[i].update(item=f"Acme countertop storage container set ({color})", store="Walmart", category="kitchen", brand="Acme")
out, doc, *_ = render(Path(sys.argv[1]), cfg, rows)
pool = drop_fresh.rotation(doc['deals'] + doc.get('spotlight', []), cfg.front_page)
mark = int(site.util_now().timestamp()) // (cfg.drop_minutes * 60)
expected = drop_fresh.page(pool, mark, cfg.front_page, drop_fresh.rules(cfg.front_page))
html = re.sub(r'<script.*?</script>', '', (out / 'index.html').read_text(), flags=re.S)
titles = [unescape(re.sub('<[^>]+>', '', text)) for text in re.findall(r'<h3 class="title">(.*?)</h3>', html)]
assert titles == [d['item'] for d in expected], 'static paint must use publication time, including a late run'
`, scratch], {encoding:'utf8'});
  assert.equal(result.status, 0, result.stdout + result.stderr);
  const root = path.join(scratch, 'site'), doc = JSON.parse(fs.readFileSync(path.join(root, 'deals.json'), 'utf8'));
  assert(doc.deals.length >= 320, 'browser fixture exercises more than three fresh pages');
  const home = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  assert(home.includes("import('/drop-fresh.js?v="));
  assert(/<header>[\s\S]*<div class="hnav"><nav id="cattabs"[\s\S]*<\/header>/.test(home));
  assert(/<\/header><nav class="hrow event-links"/.test(home), 'seasonal links follow the polished header');
  return {root, doc};
}

(async () => {
  const scratch = fs.mkdtempSync('/tmp/task300-drop-browser-');
  let browser;
  try {
    const {root, doc} = buildFixture(scratch);
    const family = new Set(doc.deals.filter(d => d.item.startsWith('Acme countertop')).map(d => d.key));
    assert.equal(family.size,4,'every variant remains in search inventory');
    function checkSpacing(keys) {
      const positions=keys.flatMap((key,i)=>family.has(key)?[i]:[]);
      assert(positions.filter(i=>i<48).length<=2);
      for(let i=1;i<positions.length;i++) assert(positions[i]-positions[i-1]>8);
    }
    if (process.argv[2] === '--build-only') {console.log('Drop browser fixture passed');return;}
    const {chromium} = require('playwright');
    browser = await chromium.launch({headless:true, executablePath:process.argv[2] || '/opt/pw-browsers/chromium'});
    for (const width of [360, 390, 1280]) for (const motion of ['reduce','no-preference']) {
      const context = await browser.newContext({viewport:{width,height:844}, reducedMotion:motion, serviceWorkers:'block'});
      const errors=[], outside=[], requests=[];
      let releaseFresh;
      const freshReady = new Promise(resolve => {releaseFresh=resolve;});
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (route.request().resourceType() === 'image') return route.fulfill({contentType:'image/svg+xml',body:
          '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320"><rect width="320" height="320" fill="#39565d"/></svg>'});
        if (url.origin !== 'https://wowpriced.test') {outside.push(url.origin);return route.abort();}
        if (url.pathname === '/deals.json') requests.push(route.request().method());
        if (url.pathname === '/drop-fresh.js') await freshReady;
        let file = path.resolve(root, '.' + url.pathname);
        if (!file.startsWith(root + path.sep) && file !== root) return route.abort();
        if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file=path.join(file,'index.html');
        if (!fs.existsSync(file)) return route.fulfill({status:404,body:''});
        const type={'.html':'text/html','.json':'application/json','.js':'application/javascript','.css':'text/css','.webmanifest':'application/manifest+json'}[path.extname(file)];
        return route.fulfill({contentType:type || 'application/octet-stream',headers:{etag:'unchanged'},body:fs.readFileSync(file)});
      });
      const page=await context.newPage();
      page.on('pageerror', e=>errors.push(e.message));
      page.on('console', m=>{if(m.type()==='error') errors.push(m.text());});
      const step=doc.meta.cadence_minutes*60000, start=Math.floor(Date.parse(doc.meta.published_at)/step)*step;
      await page.clock.setFixedTime(start+step-2000);
      await page.goto('https://wowpriced.test/',{waitUntil:'domcontentloaded'});
      await page.waitForFunction(()=>!document.querySelector('#filtertoggle').disabled);
      await page.waitForTimeout(100);
      const firstPaint=()=>page.locator('#content .grid .card').evaluateAll(nodes=>nodes.slice(0,4).map(el=>{
        const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};}));
      const staticGeometry=await firstPaint();
      assert.equal(staticGeometry.length,4,'static cards exist before the lazy drop asset');
      await page.evaluate(()=>{
        window.mountCLS=0;
        new PerformanceObserver(list=>list.getEntries().forEach(e=>{if(!e.hadRecentInput) window.mountCLS+=e.value;}))
          .observe({type:'layout-shift'});
      });
      releaseFresh();
      await page.waitForFunction(()=>sessionStorage.getItem('wp.browse'));
      await page.waitForTimeout(800);
      assert.deepEqual(await firstPaint(),staticGeometry,'lazy CSS and hydration preserve the first card slots');
      const mountCLS=await page.evaluate(()=>window.mountCLS), dropCLS=[];
      assert(mountCLS<=0.001,'lazy drop mount must not cause CLS');
      assert.equal(await page.locator('header .hnav #cattabs').count(),1);
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'integrated header fits');
      // Task 317: desktop category tabs wrap (no scroll row); phones keep their scrolling tab row.
      assert.equal(await page.locator('#cattabs').evaluate(el=>getComputedStyle(el).overflowX),width>=900?'visible':'auto','header stylesheet applied');
      assert(await page.locator('header + .event-links').isVisible());
      const cards='#content .grid:not(.spotlight-grid) .card[data-key]';
      const order=()=>page.locator(cards).evaluateAll(nodes=>nodes.map(el=>el.dataset.key));
      const geometry=()=>page.locator(cards).evaluateAll(nodes=>nodes.slice(0,15).map(el=>{
        const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};}));
      assert.equal(await page.locator('.just-dropped').count(),0,'no drop badges on first load');
      assert.equal(await page.locator('#drop-confetti').count(),0,'no confetti on first load');
      assert(await page.locator('#fresh-drop-toast').isHidden());
      let previous=await order();
      assert.equal(previous.length,90);
      assert.equal(await page.locator('#moremore').count(),0);
      assert(await page.locator('#fresh-all').isVisible());
      checkSpacing(previous);
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
        const animations=await page.locator(cards).evaluateAll(nodes=>nodes.flatMap(el=>el.getAnimations().map(a=>a.effect.getTiming())));
        if(motion==='reduce') assert.deepEqual(animations,[],'reduced motion has no card animation');
        else assert(animations.every(t=>t.duration+t.delay<=600),'entire stagger ends within 600ms');
        await page.waitForTimeout(800);
        const current=await order();
        checkSpacing(current);
        assert.equal(current.length,90);
        const newKeys=current.filter(key=>!previous.includes(key));
        assert(newKeys.length>=68,'at least 75% of ninety cards change');
        assert(current.slice(0,15).every(key=>!previous.includes(key)),'first fifteen all new');
        current.forEach((key,i)=>{if(previous.includes(key)) assert(i>=15 && Math.abs(i-previous.indexOf(key))>=12);});
        if(motion==='reduce') assert.equal(await page.locator('#drop-confetti').count(),0);
        else {
          assert.equal(await page.locator('#drop-confetti').count(),1);
          const style=await page.locator('#drop-confetti').evaluate(el=>({position:getComputedStyle(el).position,pointerEvents:getComputedStyle(el).pointerEvents}));
          assert.deepEqual(style,{position:'fixed',pointerEvents:'none'});
        }
        const badges=await page.locator(cards).evaluateAll(nodes=>nodes.filter(el=>el.querySelector('.just-dropped')).map(el=>el.dataset.key));
        assert.deepEqual(badges.sort(),newKeys.slice().sort(),'only the new head cards are badged');
        assert.equal(await page.locator('#fresh-drop-toast').textContent(),newKeys.length+' new deals just dropped');
        assert(await page.locator('#fresh-drop-toast').isVisible());
        assert(await page.locator('#fresh-drop-toast').evaluate(el=>{
          const r=el.getBoundingClientRect();return r.x>=0 && r.right<=innerWidth && r.y>=0 && r.bottom<=innerHeight;
        }),'toast stays in the viewport beside the relocated nav');
        assert.deepEqual(await geometry(),originalGeometry,'same card-slot geometry after a clock-only drop');
        assert.equal(await page.evaluate(()=>scrollY),0);
        assert(await page.evaluate(()=>window.dropCLS)<=0.001,'clock-only drops must not cause CLS');
        dropCLS.push(await page.evaluate(()=>window.dropCLS));
        await page.waitForFunction(()=>!document.getElementById('drop-confetti'),null,{timeout:12000});
        await page.reload({waitUntil:'load'});
        await page.waitForFunction(key=>document.querySelector('#content .grid:not(.spotlight-grid) .card')?.dataset.key===key,current[0]);
        await page.waitForTimeout(800);
        assert.deepEqual(await order(),current,'exact reload in the current mark');
        assert.equal(await page.locator('.just-dropped').count(),0,'reload does not replay a moment');
        await page.locator('#fresh-all').click();
        assert((await page.locator('#cattabs .cattab[data-i="1"]').getAttribute('class')).includes('active'));
        checkSpacing(await order());
        await page.goBack();
        await page.waitForTimeout(800);
        assert.deepEqual(await order(),current,'Back retains the current selection');
        previous=current;
        // Reload removes observers; begin another measurement after restoration settles.
        await page.evaluate(()=>{window.dropCLS=0;new PerformanceObserver(list=>list.getEntries().forEach(e=>{
          if(!e.hadRecentInput) window.dropCLS+=e.value;})).observe({type:'layout-shift'});});
      }
      // Retain the visible reader anchor through another clock mark.
      await page.locator(cards).nth(12).scrollIntoViewIfNeeded();
      await page.waitForTimeout(800);
      const anchor=await page.locator(cards).evaluateAll(nodes=>{
        const el=nodes.find(el=>el.getBoundingClientRect().bottom>0);
        return {key:el.dataset.key,top:el.getBoundingClientRect().top};
      });
      const heldOrder=await order(), heldY=await page.evaluate(()=>scrollY);
      await page.clock.setFixedTime(start+step*4);
      await page.waitForTimeout(1600);
      assert.deepEqual(await order(),heldOrder,'scrolled wall remains untouched');
      assert.equal(await page.evaluate(()=>scrollY),heldY);
      assert.equal(await page.locator('.just-dropped').count(),0,'previous badges expire at the next mark');
      assert(await page.locator('#fresh-drop-pill').isVisible());
      assert.match(await page.locator('#fresh-drop-pill').textContent(),/^(?:[6-8][0-9]|90) new deals just dropped ↑$/);
      const anchored=page.locator(cards+'[data-key="'+anchor.key+'"]');
      assert(Math.abs((await anchored.boundingBox()).y-anchor.top)<=2,'scrolled reader retains the same anchor');
      await page.locator('#fresh-drop-pill').click();
      await page.waitForTimeout(700);
      assert.equal(await page.evaluate(()=>scrollY),0);
      assert((await order()).filter(key=>!heldOrder.includes(key)).length>=68);
      assert.equal(await page.locator('.just-dropped').count(),(await order()).filter(key=>!heldOrder.includes(key)).length);
      assert(await page.locator('#fresh-drop-pill').isHidden());
      await page.waitForTimeout(4200);
      assert(await page.locator('#fresh-drop-toast').isHidden(),'toast expires after about four seconds');
      assert(requests.length>=4);
      assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);
      console.log(JSON.stringify({width,motion,drops:3,unchangedPublication:true,layout:'unchanged',mountCLS,dropCLS}));
      await context.close();
    }
  } finally {if(browser) await browser.close();fs.rmSync(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
