// Route a built site entirely from disk; only the publication clock changes.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
(async()=>{
  let chromium;
  try {({chromium}=require('playwright'));}
  catch(e) {if(e.code!=='MODULE_NOT_FOUND') throw e;console.log('Playwright unavailable');process.exitCode=77;return;}
  let browser;
  try {browser=await chromium.launch({headless:true,executablePath:'/opt/pw-browsers/chromium'});}
  catch(e) {
    if(!/Operation not permitted|Executable doesn't exist|EACCES|EPERM/.test(e.message)) throw e;
    console.log('Chromium unavailable: sandbox launch denied or browser missing');process.exitCode=77;return;
  }
  try {
    const root=path.resolve(process.argv[2]), doc=JSON.parse(fs.readFileSync(path.join(root,'deals.json'),'utf8'));
    const step=doc.meta.cadence_minutes*60000, start=Date.parse(doc.meta.published_at);
    let now=start+1000, publication=0;
    const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
    const errors=[], outside=[];
    await context.route('**/*',route=>{
      const request=route.request(), url=new URL(request.url());
      if(request.resourceType()==='image') return route.fulfill({contentType:'image/png',body:Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1cAAAAASUVORK5CYII=','base64')});
      if(url.origin!=='https://wowpriced.test') {outside.push(url.origin);return route.abort();}
      if(url.pathname==='/deals.json') {
        const data=structuredClone(doc), at=new Date(start+publication*step).toISOString();
        data.meta.published_at=data.meta.generated_at=at;
        return route.fulfill({contentType:'application/json',headers:{etag:'drop-'+publication},body:JSON.stringify(data)});
      }
      let file=path.resolve(root,'.'+url.pathname);
      if(!file.startsWith(root+path.sep)&&file!==root) return route.abort();
      if(fs.existsSync(file)&&fs.statSync(file).isDirectory()) file=path.join(file,'index.html');
      if(!fs.existsSync(file)) return route.fulfill({status:404,body:''});
      const type={'.html':'text/html','.json':'application/json','.js':'application/javascript',
        '.css':'text/css','.webmanifest':'application/manifest+json'}[path.extname(file)];
      return route.fulfill({contentType:type||'application/octet-stream',body:fs.readFileSync(file)});
    });
    const page=await context.newPage();
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(m.type()==='error') errors.push(m.text());});
    await page.clock.setFixedTime(now);
    await page.goto('https://wowpriced.test/',{waitUntil:'load'});
    await page.waitForFunction(()=>sessionStorage.getItem('wp.browse'));
    await page.waitForTimeout(800);
    const snapshot=()=>page.evaluate(()=>({y:scrollY,top:document.getElementById('content').getBoundingClientRect().top,
      cards:Array.from(document.querySelectorAll('#content .grid:not(.spotlight-grid) .card')).slice(0,8).map(el=>{
        const r=el.getBoundingClientRect();return {key:el.dataset.key,x:r.x,y:r.y,width:r.width,height:r.height};})}));
    const spotlight=await page.locator('#content .spotlight-grid').evaluateAll(grids=>grids.map(el=>Array.from(el.querySelectorAll('.card'),card=>card.dataset.key)));
    assert.equal(spotlight.length,doc.spotlight.length?1:0);
    assert.deepEqual(spotlight,doc.spotlight.length?[doc.spotlight.map(d=>d.key)]:[],
      await page.locator('#content .spotlight-grid').evaluateAll(grids=>grids.map(el=>el.outerHTML)));
    const before=await snapshot(), firsts=[before.cards[0].key];
    assert.equal(before.cards.length,8);
    for(publication=1;publication<=3;publication++) {
      await page.evaluate(()=>{
        window.dropCLS=0;
        new PerformanceObserver(list=>list.getEntries().forEach(e=>{if(!e.hadRecentInput) window.dropCLS+=e.value;}))
          .observe({type:'layout-shift'});
      });
      const previous=firsts.at(-1);now=start+publication*step+1000;
      await page.clock.setFixedTime(now);
      await page.waitForFunction(key=>document.querySelector('#content .grid:not(.spotlight-grid) .card')?.dataset.key!==key,previous);
      await page.waitForTimeout(900);
      const after=await snapshot();firsts.push(after.cards[0].key);
      assert.deepEqual(await page.locator('#content .spotlight-grid').evaluateAll(grids=>grids.map(el=>Array.from(el.querySelectorAll('.card'),card=>card.dataset.key))),spotlight);
      assert.equal(after.y,before.y);assert.equal(after.top,before.top);
      assert.deepEqual(after.cards.map(({key,...geometry})=>geometry),before.cards.map(({key,...geometry})=>geometry));
      assert(await page.evaluate(()=>window.dropCLS)<=0.001);
      const order=await page.locator('#content .grid:not(.spotlight-grid) .card').evaluateAll(cards=>cards.map(el=>el.dataset.key));
      await page.reload({waitUntil:'load'});
      await page.waitForFunction(key=>document.querySelector('#content .grid:not(.spotlight-grid) .card')?.dataset.key===key,order[0]);
      await page.waitForTimeout(800);
      assert.deepEqual(await page.locator('#content .grid:not(.spotlight-grid) .card').evaluateAll(cards=>cards.map(el=>el.dataset.key)),order);
      assert.deepEqual(await page.locator('#content .spotlight-grid').evaluateAll(grids=>grids.map(el=>Array.from(el.querySelectorAll('.card'),card=>card.dataset.key))),spotlight,
        await page.locator('#content .spotlight-grid').evaluateAll(grids=>grids.map(el=>el.outerHTML)));
    }
    assert.equal(new Set(firsts.slice(0,3)).size,3);assert.equal(firsts[3],firsts[0]);
    assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);
    console.log(JSON.stringify({viewport:'390x844',firsts,layout:'unchanged',consoleErrors:errors.length}));
    await context.close();
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
