// Real Chromium, entirely offline; all captures stay under pytest's tmp_path.
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');
const scratch = process.argv[2];
fs.mkdirSync(scratch,{recursive:true});
const BUILD = `
from pathlib import Path
from unittest.mock import patch
import sys
from tests.test_support import render,NOW
from tests.test_publish_site import make_deal
from dealradar.config import load_config
from dealradar.publish import site
cfg=load_config()
rows=[make_deal(dedupe_key='spotter|'+str(i),asin='B0'+str(i).zfill(8),
 item='Everyday wireless headphones '+str(i), store='Amazon',codes=[],stack_steps=[],
 canonical_url='https://www.amazon.com/dp/B0'+str(i).zfill(8),
 image_url='https://m.media-amazon.com/images/I/fixture.jpg',
 final_price=20+i,regular_price=50,pct_at_checkout=60,abs_savings=30-i,
 regular_price_basis='List price',verify_tier='verified',
 price_rechecked_at=NOW.isoformat(),critic={'plausible':True,'confidence':.99,'reason':'Fixture'}) for i in range(4)]
with patch.object(site,'util_now',lambda:NOW): render(Path(sys.argv[1]),cfg,rows)
`;
const NOW = new Date('2026-10-22T01:00:00Z');
async function contrastAudit(page) {
  const rows = await page.evaluate(() => {
    const rgb = color => (color.match(/[\d.]+/g) || []).map(Number);
    return [...document.querySelectorAll('h1,h2,h3,p,small,summary,a,button,input,.grade-letter,.mini-grade')].flatMap(el => {
      const style = getComputedStyle(el), box = el.getBoundingClientRect();
      if (!el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}) || box.width < 2 || box.height < 2 ||
          box.bottom <= 0 || box.top >= innerHeight || el.closest('.sr-only,[role="img"]') ||
          style.clipPath !== 'none' || !(el.textContent.trim() || el.placeholder)) return [];
      const top = document.elementFromPoint(Math.max(0,Math.min(innerWidth-1,box.x+box.width/2)),Math.max(0,Math.min(innerHeight-1,box.y+box.height/2)));
      if (!top || !(el.contains(top) || top.contains(el))) return [];
      let bg;
      for(let parent=el;parent;parent=parent.parentElement) {
        const value=rgb(getComputedStyle(parent).backgroundColor);
        if(value.length===3 || value[3]===1) {bg=value.slice(0,3);break;}
      }
      const size=parseFloat(style.fontSize), weight=parseInt(style.fontWeight)||400;
      return [{text:(el.textContent||el.placeholder).trim().slice(0,60),fg:rgb(style.color).slice(0,3),
        bg:bg||[255,255,255],minimum:size>=24||size>=18.66&&weight>=700?3:4.5}];
    });
  });
  const lum=rgb=>rgb.reduce((sum,c,i)=>{const v=c/255;return sum+[.2126,.7152,.0722][i]*(v<=.04045?v/12.92:((v+.055)/1.055)**2.4);},0);
  for(const row of rows) {
    const a=lum(row.fg),b=lum(row.bg),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    assert(ratio>=row.minimum,row.text+': contrast '+ratio.toFixed(2));
  }
}
(async () => {
  let browser;
  try { browser = await chromium.launch({executablePath:'/opt/pw-browsers/chromium',headless:true}); }
  catch (error) { console.log('Chromium unavailable: ' + error.message.split('\n')[0]); process.exitCode=77; return; }
  try {
    const built = spawnSync('.venv/bin/python',['-c',BUILD,scratch],{encoding:'utf8'});
    assert.equal(built.status,0,built.stderr);
    const root=path.join(scratch,'site');
    for (const [width,height] of [[390,844],[768,1024],[1280,800],[1920,1080]]) {
      for (const colorScheme of ['light','dark']) {
        const context=await browser.newContext({viewport:{width,height},colorScheme});
        const errors=[], calls=[]; let grade='A', hold=false, releases=[];
        await context.addInitScript(() => {
          Math.random=()=>0;
          window.layoutShift=0;
          new PerformanceObserver(list=>{for(const e of list.getEntries()) if(!e.hadRecentInput) window.layoutShift+=e.value;}).observe({type:'layout-shift',buffered:true});
        });
        await context.route('**/*',async route=>{
          const url=new URL(route.request().url());
          if(url.hostname==='m.media-amazon.com') return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300" viewBox="0 0 300 300"><rect width="300" height="300" rx="20" fill="#f3f7ff"/><path d="M70 170v-40a80 80 0 0 1 160 0v40" stroke="#345d94" stroke-width="18" fill="none"/><rect x="56" y="135" width="40" height="95" rx="20" fill="#172b4e"/><rect x="204" y="135" width="40" height="95" rx="20" fill="#172b4e"/></svg>'});
          if(url.origin!=='https://wowpriced.test') return route.fulfill({status:204,body:''});
          if(url.pathname==='/api/search') {
            calls.push(url.href); assert.equal(url.searchParams.get('grade'),'deal');
            if(hold) await new Promise(resolve=>releases.push(resolve));
            return route.fulfill({contentType:'application/json',body:JSON.stringify({items:[{asin:'B0D8KZHN1Q',
              title:'Wireless over-ear headphones with a comfortable folding design',image:'https://m.media-amazon.com/images/I/fixture.jpg',
              price_cents:grade==='A'?3999:grade==='B'?4699:4999,...(grade==='B'?{shipping_cents:2515}:{}),list_price_cents:5999,monthlySold:1000,
              as_of:new Date(NOW.getTime()-4*60000).toISOString()}],deal_grade:{grade,label:{A:'Great price',B:'Good price',C:'Fair price'}[grade],
              facts:['Current Amazon price checked',...(grade==='A'?['Verified deal on wowpriced']:[])]}})});
          }
          let file=path.join(root,url.pathname);
          if(fs.existsSync(file)&&fs.statSync(file).isDirectory()) file=path.join(file,'index.html');
          if(!fs.existsSync(file)) return route.fulfill({status:404,body:''});
          return route.fulfill({contentType:{'.html':'text/html','.js':'application/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml'}[path.extname(file)]||'application/octet-stream',body:fs.readFileSync(file)});
        });
        const page=await context.newPage(); page.on('pageerror',e=>errors.push(e.message));
        await page.clock.install({time:NOW});
        const shot=async name=>{
          await contrastAudit(page);
          return page.screenshot({path:path.join(scratch,`${width}-${colorScheme}-${name}.png`),animations:'disabled'});
        };
        const geometry=async()=>{
          const sizes=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,width:innerWidth,cls:window.layoutShift,
            badImages:[...document.querySelectorAll('img')].filter(img=>!img.hasAttribute('width')||!img.hasAttribute('height')).length}));
          assert(sizes.scroll<=sizes.width,JSON.stringify(sizes));assert.equal(sizes.badImages,0);
          for (const selector of ['.spotter-header','.result-grid','.hero-box']) {
            const boxes=await page.locator(selector+'>*').evaluateAll(nodes=>nodes.filter(n=>n.checkVisibility()).map(n=>{
              const b=n.getBoundingClientRect();return {left:b.left,right:b.right,top:b.top,bottom:b.bottom};
            }));
            for(let i=0;i<boxes.length;i++) for(let j=i+1;j<boxes.length;j++) {
              const a=boxes[i],b=boxes[j];
              assert(Math.min(a.right,b.right)-Math.max(a.left,b.left)<=1 || Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)<=1,selector+' overlap');
            }
          }
        };
        await page.goto('https://wowpriced.test/deal-spotter/');
        await page.clock.runFor(500); await geometry();
        assert((await page.evaluate(()=>window.layoutShift))<.01);
        await shot('start'); await shot('cookie');
        await page.locator('#cookie-ok').click();
        await page.reload(); assert(await page.locator('#cookie-bar').isHidden());
        await page.locator('#q').fill('https://not-amazon.example/product');
        await page.locator('.hero-box button').click(); assert(await page.locator('#input-error').textContent());
        await geometry(); await shot('error');
        for(const next of ['A','B','C']) {
          grade=next;
          if(next==='A') {
            await page.locator('#q').fill('B0D8KZHN1Q');
            await Promise.all([page.waitForResponse(r=>r.url().includes('/api/search')),page.locator('.hero-box button').click()]);
            await page.clock.runFor(5000);
            assert(await page.locator('#analyze-modal').isVisible());
            assert.equal(await page.locator('[role="progressbar"]').getAttribute('aria-valuenow'),'50');
            assert.equal(await page.locator('#modal-product').textContent(),'Wireless over-ear headphones with a comfortable folding design');
            await page.keyboard.press('Shift+Tab');assert.equal(await page.evaluate(()=>document.activeElement.id),'modal-deals');
            await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.id),'cancel-analysis');
            await geometry(); await shot('loading');
            await page.keyboard.press('Escape');assert(await page.locator('#analyze-modal').isHidden());
            await Promise.all([page.waitForResponse(r=>r.url().includes('/api/search')),page.locator('.hero-box button').click()]);
          } else await Promise.all([page.waitForResponse(r=>r.url().includes('/api/search')),page.locator('#reanalyze').click()]);
          await page.clock.runFor(10100);
          assert.equal(await page.locator('#grade-letter').textContent(),next);
          // Amazon's item price first; shipping only when it is charged (operator 2026-10-22).
          assert.equal(await page.locator('#result-price').textContent(),{A:'$39.99',B:'$46.99+ $25.15 shipping',C:'$49.99'}[next]);
          assert(await page.locator('#analyze-modal').isHidden());
          assert.equal(await page.evaluate(()=>document.activeElement.id),'result-title');
          assert.match(page.url(),/\?asin=B0D8KZHN1Q$/);
          assert.match(await page.locator('#result-buy').getAttribute('href'),/tag=wowpriced-20/);
          assert.match(await page.locator('#review-link').getAttribute('href'),/asin=B0D8KZHN1Q/);
          assert(!/avg90|lowest|average|overpriced|fake/i.test(await page.locator('#spotter-result').textContent()));
          await geometry(); await shot('result-'+next);
        }
        // Share URLs rerun the lookup; a slow response cannot be outrun by progress.
        hold=true;
        await Promise.all([page.waitForRequest(r=>r.url().includes('/api/search')),page.goto('https://wowpriced.test/deal-spotter/?asin=B0D8KZHN1Q')]);
        await page.clock.runFor(11000);
        assert(await page.locator('#analyze-modal').isVisible());
        assert.equal(await page.locator('[role="progressbar"]').getAttribute('aria-valuenow'),'99');
        hold=false;releases.splice(0).forEach(resolve=>resolve());
        await page.waitForTimeout(100);await page.clock.runFor(300);
        // Return to the start to verify carousel, steps, menu and reduced motion.
        await page.goto('https://wowpriced.test/deal-spotter/');
        await page.locator('#phone-next').click();assert.equal(await page.locator('#slide-title').textContent(),"Today's Deals");
        await page.locator('#phone-prev').click();assert.equal(await page.locator('#slide-title').textContent(),'Fake Deal Spotter');
        await page.locator('.how-step').nth(2).click();assert.equal(await page.locator('.how-step').nth(2).getAttribute('aria-pressed'),'true');
        assert.equal(await page.locator('.illustration').getAttribute('data-illustration'),'2');
        assert.equal((await page.locator('.panel-title span:visible').textContent()).trim(),'Look past the “was” price');
        // Guide band: list picks a screen; tabs frame it as a computer or a phone.
        await page.locator('.guide-item').nth(2).click();assert.equal(await page.locator('#slide-title').textContent(),'Discount Finder');
        assert.deepEqual(await page.locator('.guide-item').evaluateAll(els=>els.map(el=>el.getAttribute('aria-pressed'))),['false','false','true']);
        assert.equal(await page.locator('[data-slide="2"]').isVisible(),true);
        await page.locator('.screen-tab[data-view="phone"]').click();assert.equal(await page.locator('#screen-stage').getAttribute('data-view'),'phone');
        assert.deepEqual(await page.locator('.screen-tab').evaluateAll(els=>els.map(el=>el.getAttribute('aria-pressed'))),['false','true']);
        await geometry();
        await page.locator('.screen-tab[data-view="desktop"]').click();assert.equal(await page.locator('#screen-stage').getAttribute('data-view'),'desktop');
        // Demo band: pause keeps its name and reports its state; replay restarts without errors.
        await page.locator('#pause-demo').click();assert.equal(await page.locator('#pause-demo').getAttribute('aria-pressed'),'true');
        assert(await page.locator('.demo-frame').evaluate(el=>el.classList.contains('paused')));
        assert.equal(await page.locator('#pause-demo').getAttribute('aria-label'),'Pause demo');
        await page.locator('#replay-demo').click();await page.locator('#pause-demo').click();
        assert.equal(await page.locator('#pause-demo').getAttribute('aria-pressed'),'false');
        assert(!/as mentioned|as seen on|trusted by|million|testimonial|★/i.test(await page.locator('main').textContent()));
        await page.locator('.spotter-menu summary').click();assert(await page.locator('.spotter-menu nav').isVisible());
        await page.keyboard.press('Escape');assert(!(await page.locator('.spotter-menu').evaluate(el=>el.open)));
        await page.emulateMedia({reducedMotion:'reduce'});
        assert.equal(await page.locator('.demo-grade').evaluate(el=>getComputedStyle(el).animationName),'none');
        await page.screenshot({path:path.join(scratch,`${width}-${colorScheme}-full.png`),fullPage:true,animations:'disabled'});
        await page.goto('https://wowpriced.test/fakespot-alternative/');await geometry();await shot('alternative');
        assert.deepEqual(errors,[]);await context.close();
      }
    }
    console.log('All 4 sizes, light/dark, input/modal/A/B/C/cookie/share/menu/motion/CLS checks passed; screenshots: '+scratch);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
