// Native Chromium QA. All requests use a local build or explicit fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');

function buildFixture(scratch) {
  fs.mkdirSync(scratch, {recursive:true});
  const result=spawnSync('.venv/bin/python',['-c',`
import sys
from pathlib import Path
from datetime import datetime, timezone
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.publish import site
from tests.test_publish_site import make_deal
now=datetime.now(timezone.utc)
site.util_now=lambda:now
cfg=load_config()
cfg.tool_pages={"enabled":True,"public":False}
cfg.tracking={}
cfg.tool_pages_hero=True
cfg.tool_pages_1020=True
cfg.paywall_api_url="https://account.wowpriced.test"
cfg.paywall_google_client_id="task335-google"
cfg.affiliate_amazon_tag="example-20"
rows=[make_deal(dedupe_key=f"task335-kettle-{i}", item=f"Steel kitchen kettle {i}", store="Amazon", category=["kitchen","beauty","electronics"][i%3],
    verify_tier="verified", codes=[], conditions=["new"], final_price=15, regular_price=30,
    canonical_url=f"https://amazon.com/dp/B{i:09d}", asin=f"B{i:09d}",
    regular_price_basis="List price on Amazon", basis="amazon_list_price",
    keepa_list_price={"asin":f"B{i:09d}","price":30,"observed_at":now.isoformat()},
    first_seen_at=now.isoformat(), price_rechecked_at=now.isoformat(),
    click_price_check={"asin":f"B{i:09d}","final":15,"buybox":15,"at":now.isoformat()},
    critic={"plausible":True,"confidence":.9,"reason":"Backed basis"},
    image_url="https://m.media-amazon.com/images/I/photo.jpg") for i in range(8)]
db=DB(":memory:")
try: site.render_site(db,cfg,now,out_dir=Path(sys.argv[1])/"site",deals=rows)
finally: db.close()
`,scratch],{encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);
  return path.join(scratch,'site');
}


async function waitData(page) {
  for(let i=0;i<300;i++) {
    if(await page.evaluate(()=>toolActive && toolActive.ready)) return;
    await new Promise(resolve=>setTimeout(resolve,10));
  }
  throw Error('fixture data did not finish');
}

async function checkJoin(page,selector,shot) {
  const button=page.locator(selector);
  assert.equal(await button.getAttribute('href'),'/join');
  const before=page.url();
  await button.click();
  const overlay=page.locator('#header-join-dialog');
  await overlay.waitFor({state:'visible'});
  const frame=page.frameLocator('#header-join-dialog iframe');
  await frame.locator('#join-email-choice').waitFor({state:'visible'});
  await frame.locator('#join-google button').waitFor({state:'visible'});
  assert.equal(page.url(),before);
  assert.equal(await overlay.getAttribute('aria-modal'),'true');
  assert.equal(await page.evaluate(() => document.documentElement.style.overflow),'hidden');
  assert.equal(await frame.locator('#join-email-choice').evaluate(el => document.activeElement===el),true);
  const overflow=await frame.locator('body').evaluate(() => document.documentElement.scrollWidth>innerWidth);
  assert.equal(overflow,false,'dialog frame overflow');
  // The last control wraps to the first; Shift+Tab wraps back into the frame.
  await frame.locator('#join-email-choice').focus();
  await page.keyboard.press('Tab');
  assert.equal(await frame.locator('#dialog-close').evaluate(el => document.activeElement===el),true);
  await page.keyboard.press('Shift+Tab');
  assert.equal(await frame.locator('#join-email-choice').evaluate(el => document.activeElement===el),true);
  await page.screenshot({path:shot,fullPage:false});
  await frame.locator('#join-email-choice').click();
  await frame.locator('#join-email').waitFor({state:'visible'});
  assert.equal(await frame.locator('#join-email-fields button[type="submit"]').isDisabled(),true);
  await frame.locator('#join-back').click();
  await page.keyboard.press('Escape');
  await overlay.waitFor({state:'detached'});
  assert.equal(await button.evaluate(el => document.activeElement===el),true);
  assert.notEqual(await page.evaluate(() => document.documentElement.style.overflow),'hidden');
  await button.click();
  const close=page.frameLocator('#header-join-dialog iframe').locator('#dialog-close');
  await close.click();
  await overlay.waitFor({state:'detached'});
  assert.equal(await button.evaluate(el => document.activeElement===el),true);
  // Escape also works while the lazy card is still arriving.
  await button.click();
  await overlay.waitFor({state:'visible'});
  await page.keyboard.press('Escape');
  await overlay.waitFor({state:'detached'});
  assert.equal(await button.evaluate(el => document.activeElement===el),true);
}

async function run(root, executablePath, shots) {
  const {chromium}=require('playwright');
  let browser;
  try {browser=await chromium.launch({headless:true,executablePath});}
  catch(error) {
    if (!/Operation not permitted|Executable doesn't exist|Failed to launch|sandbox|Target .* closed/.test(error.message)) throw error;
    console.log('Chromium unavailable: no screenshots captured');process.exitCode=77;return;
  }
  root=path.resolve(root);
  fs.mkdirSync(shots,{recursive:true});
  let liveOnly=false;
  const fixture=() => ({items:Array.from({length:6},(_,i) => ({asin:'B00000010'+i,
    title:i ? 'Steel kitchen kettle '+i : 'Steel kitchen kettle with temperature control',
    price_cents:1500+i*100,list_price_cents:liveOnly ? null : 3000,monthlySold:[1200,10000,2500,5000,20000,50][i],
    image:'https://m.media-amazon.com/images/I/photo.jpg',as_of:new Date(Date.now()-60000).toISOString()}))});
  try {
    for (const width of [390,1280]) for (const theme of ['light','dark']) for (const reduced of [false,true]) {
      const context=await browser.newContext({viewport:{width,height:1000},colorScheme:theme,
        reducedMotion:reduced ? 'reduce' : 'no-preference'});
      const calls=[],unexpected=[],errors=[];
      let failure=false;
      await context.route('**/*',async route => {
        const url=new URL(route.request().url());
        if (url.origin !== 'https://wowpriced.test') {
          if (url.hostname === 'accounts.google.com') return route.fulfill({contentType:'application/javascript',body:
            `window.google={accounts:{id:{initialize(o){this.options=o},renderButton(box){box.innerHTML='<button style="width:100%;min-height:52px;border:1px solid #dcdfe3;border-radius:999px;background:transparent;color:inherit;font:750 17px system-ui">G　Continue with Google</button>';box.firstChild.onclick=()=>this.options.callback({credential:'offline-fixture'})}}}};`});
          if (url.hostname === 'account.wowpriced.test') return route.fulfill({contentType:'application/json',body:
            JSON.stringify(url.pathname === '/api/google' ? {me:{email_verified:true,email:'fixture@example.invalid'}} : {ok:true})});
          if (url.hostname === 'm.media-amazon.com') return route.fulfill({contentType:'image/svg+xml',body:
            '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"><rect width="300" height="300" fill="#f2f3f5"/><path d="M200 100Q270 80 250 170Q235 200 210 165M75 110L60 85L40 85L70 150L80 230Q150 250 220 230L210 110Z" fill="#b9c6cd" stroke="#263e46" stroke-width="10"/><ellipse cx="143" cy="110" rx="68" ry="14" fill="#5d7681"/></svg>'});
          unexpected.push(url.origin);return route.fulfill({status:204,body:''});
        }
        if (url.pathname === '/api/search') {
          calls.push(url.search);
          assert.deepEqual([...url.searchParams.keys()],['asin']);
          assert.equal(url.searchParams.get('asin'),'B000000100');
          await new Promise(resolve => setTimeout(resolve,5));
          return route.fulfill({contentType:'application/json',body:JSON.stringify(failure ?
            {items:[],reason:'budget_exhausted'} : fixture())});
        }
        const candidate=path.resolve(root,'.'+url.pathname);
        if (candidate !== root && !candidate.startsWith(root+path.sep)) return route.fulfill({status:404,body:''});
        let file=candidate;
        if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file=path.join(file,'index.html');
        if (!fs.existsSync(file) && fs.existsSync(file+'.html')) file+='.html';
        if (!fs.existsSync(file)) return route.fulfill({status:404,body:''});
        return route.fulfill({contentType:({'.html':'text/html','.json':'application/json','.js':'application/javascript',
          '.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)] || 'application/octet-stream',body:fs.readFileSync(file)});
      });
      for (const mode of ['checker','finder']) {
        const slug=mode === 'checker' ? 'amazon-deal-checker' : 'amazon-discount-finder';
        const page=await context.newPage();
        await page.clock.install({time:new Date()});
        page.on('pageerror',error => errors.push(error.message));
        await page.addInitScript(() => {Math.random=()=>.4;window.toolEvents=[];window.wpTrack=(...args) => window.toolEvents.push(args);});
        await page.goto('https://wowpriced.test/'+slug+'/');
        await page.waitForFunction(() => typeof toolSubmit === 'function');
        assert.equal(await page.locator('#tool-result').isVisible(),false);
        await page.waitForTimeout(reduced ? 0 : 800);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),false,'hero overflow');
        assert.equal(await page.locator('.gauge,.meter-card').count(),0);
        assert.equal(await page.locator('.hero .tile').count(),6);
        await page.screenshot({path:path.join(shots,`${mode}-hero-${width}-${theme}${reduced ? '-reduced' : ''}.png`),fullPage:true});
        await checkJoin(page, '.tool-header>.join', path.join(shots,`${mode}-join-${width}-${theme}${reduced ? '-reduced' : ''}.png`));
        const before=calls.length;
        await page.locator('#q').fill('https://amazon.com/Steel-Kitchen-Kettle/dp/B000000100?tag=PRIVATE');
        await page.evaluate(() => {
          window.doneSteps=[];
          const steps=[...document.querySelectorAll('.steps li')];
          new MutationObserver(changes => {
            for (const change of changes) {
              const el=change.target,index=steps.indexOf(el);
              if (el.dataset.state === 'done' && !window.doneSteps.some(s => s.index === index))
                window.doneSteps.push({index,at:performance.now()});
            }
          }).observe(document.querySelector('.steps'),{subtree:true,attributes:true,attributeFilter:['data-state']});
          document.querySelector('#tool-form').addEventListener('submit',() => {window.toolStarted=performance.now();},{capture:true});
        });
        await page.clock.pauseAt(await page.evaluate(()=>Date.now()+1000));
        await page.locator('#tool-submit').scrollIntoViewIfNeeded();
        const origin=await page.evaluate(()=>({scroll:scrollY,hero:['.hero','.hero h1','#tool-form'].map(selector=>{
          const b=document.querySelector(selector).getBoundingClientRect();return {x:b.x,y:b.y+scrollY,width:b.width,height:b.height};
        })}));
        await page.locator('#tool-submit').click();
        await page.locator('#analyzer').waitFor({state:'visible'});
        assert.equal(await page.evaluate(()=>scrollY),origin.scroll,'submit must not scroll');
        await waitData(page);
        const T=await page.evaluate(()=>toolActive.duration);
        let sampled=0;
        for(const fraction of [.25,.5,.9]) {
          await page.clock.runFor(T*(fraction-sampled));sampled=fraction;
          const progress=await page.locator('#tool-progress').evaluate(el=>({
            width:el.firstElementChild.getBoundingClientRect().width/el.getBoundingClientRect().width*100,
            elapsed:(performance.now()-toolActive.started)/toolActive.duration*100}));
          assert(Math.abs(progress.width-fraction*100)<=2,JSON.stringify(progress));
          assert(Math.abs(progress.width-progress.elapsed)<=2,JSON.stringify(progress));
          assert.equal(await page.evaluate(()=>scrollY),origin.scroll,'progress must not scroll');
          assert.equal(await page.locator('#tool-result').isVisible(),false);
        }
        await page.clock.runFor(T*.1+32);
        await page.locator('#tool-result').waitFor({state:'visible',timeout:28000});
        const timings=await page.evaluate(() => ({steps:window.doneSteps,started:window.toolStarted}));
        assert.deepEqual(timings.steps.map(s => s.index),Array.from({length:mode === 'checker' ? 5 : 4},(_,i) => i));
        const duration=timings.steps.at(-1).at-timings.started;
        assert(Math.abs(duration-T)<32,`duration ${duration} ms, target ${T}`);
        assert.equal(await page.evaluate(()=>scrollY),origin.scroll,'result must not scroll');
        assert.deepEqual(await page.evaluate(()=>['.hero','.hero h1','#tool-form'].map(selector=>{
          const b=document.querySelector(selector).getBoundingClientRect();return {x:b.x,y:b.y+scrollY,width:b.width,height:b.height};
        })),origin.hero,'hero CLS 0');
        assert.equal(await page.locator('#analyzer').getAttribute('aria-busy'),'false');
        assert.equal(await page.locator('#tool-progress').getAttribute('aria-valuenow'),'100');
        assert(await page.locator('#verified-deals .product-card').count() > 0);
        if (mode === 'checker') {
          assert.equal(await page.locator('.gauge').count(),0);
          assert.equal(calls.length,before+1);
          assert.equal(await page.locator('#top-sellers .product-card').count(),5);
          assert((await page.locator('#tool-result').innerText()).includes('50% off list price'));
          assert((await page.locator('#tool-result').innerText()).includes('$15.00'));
          assert((await page.locator('#tool-result').innerText()).includes('price checked '));
          assert.equal(await page.locator('#top-sellers .monthly').count(),5);
          assert.equal(await page.locator('#tool-result s').innerText(),'$30.00');
          const similar=page.locator('#tool-result .amazon-button');
          assert.equal(await similar.innerText(),'See similar deals on Amazon →');
          const similarURL=new URL(await similar.getAttribute('href'));
          assert.equal(similarURL.searchParams.get('rh'),'p_n_deal_type:23566065011');
          assert.equal(similarURL.searchParams.get('k'),'Steel kitchen kettle with temperature control');

        } else {
          assert.equal(calls.length,before,'finder cannot call the API');
          assert.equal(await page.locator('#tool-result .amazon-button').getAttribute('href'),
            'https://www.amazon.com/s?k=Steel%20Kitchen%20Kettle&rh=p_n_deal_type%3A210906366011&tag=example-20');
          assert.equal(await page.locator('#tool-result .price').count(),0,'finder cannot claim a live price');
        }
        const audit=await page.evaluate(() => ({overflow:document.documentElement.scrollWidth>innerWidth,
          links:[...document.querySelectorAll('a[href*="amazon.com"]')].map(a => ({href:a.href,target:a.target,rel:a.rel})),
          targets:[...document.querySelectorAll('a,button,input')].filter(el => el.getClientRects().length).map(el => {
            const b=el.getBoundingClientRect();return {label:el.textContent || el.id,width:b.width,height:b.height};
          }),events:window.toolEvents,
          transition:getComputedStyle(document.querySelector('.progress span')).transitionDuration,
          grids:[...document.querySelectorAll('.product-grid')].map((grid,index) => {
            const style=getComputedStyle(grid),template=style.gridTemplateColumns;
            // Hidden grids (the Finder's top sellers) retain repeat() without pixel tracks.
            const repeat=/^repeat\((\d+),\s*minmax\([^()]+\)\)$/.exec(template);
            return {label:'.product-grid['+index+'] in #'+grid.parentElement.id,template,
              columns:repeat ? Number(repeat[1]) : template.split(/\s+/).length,
              width:grid.getBoundingClientRect().width,gap:parseFloat(style.columnGap),
              cards:[...grid.querySelectorAll('.product-card')].map(card => card.getBoundingClientRect().width)};
          })}));
        assert.equal(audit.overflow,false);
        assert.equal(audit.grids.length,2);
        for (const grid of audit.grids) {
          const label=`${slug} ${width}px ${theme}${reduced ? ' reduced motion' : ''} ${grid.label}`;
          assert.equal(grid.columns,width <= 760 ? 2 : 5,`${label}: ${grid.template}`);
          const cardWidth=(grid.width-grid.gap*(grid.columns-1))/grid.columns;
          for (const actual of grid.cards) if (grid.width>0)
            assert(Math.abs(actual-cardWidth)<1,`${label}: card width ${actual}, expected ${cardWidth}`);
        }
        assert(audit.links.length>0);
        for (const a of audit.links) {
          assert.equal(new URL(a.href).searchParams.get('tag'),'example-20');
          assert.equal(a.target,'_blank');assert(a.rel.includes('sponsored') && a.rel.includes('noopener'));
        }
        for (const target of audit.targets) assert(target.width>=44 && target.height>=44,JSON.stringify(target));
        if (reduced) assert.equal(audit.transition,'0s');
        assert(audit.events.some(e => e[0] === 'page_view' && e[1].source === 'tool_page'));
        assert(!JSON.stringify(audit.events).includes('PRIVATE'));
        await page.screenshot({path:path.join(shots,`${mode}-${width}-${theme}${reduced ? '-reduced' : ''}.png`),fullPage:true});
        if (mode === 'finder' && reduced) {
          await page.locator('#q').fill('https://amazon.com/dp/B000000100');
          await page.locator('#tool-submit').click();
          await waitData(page);
          await page.clock.runFor(25032);
          await page.locator('#tool-result').waitFor({state:'visible'});
          assert.equal(await page.locator('#tool-result .amazon-button').getAttribute('href'),
            'https://www.amazon.com/dp/B000000100?tag=example-20');
          assert.equal(calls.length,before);
        }
        // Endpoint failure and delayed real-work gate, once per viewport/theme.
        if (mode === 'checker' && reduced) {
          failure=true;
          await page.reload();
          await page.locator('#q').fill('https://amazon.com/dp/B000000100');
          await page.locator('#tool-submit').click();
          await waitData(page);
          await page.clock.runFor(25032);
          await page.locator('#tool-result').waitFor({state:'visible'});
          assert((await page.locator('#tool-result').innerText()).includes("Today's live price checks have reached their limit."));
          assert.equal(await page.locator('.steps [data-state=failed]').count(),1,'error state at completion');
          failure=false;
          liveOnly=true;
          await page.reload();
          await page.locator('#q').fill('https://amazon.com/dp/B000000100');
          await page.locator('#tool-submit').click();
          await waitData(page);
          await page.clock.runFor(25032);
          await page.locator('#tool-result').waitFor({state:'visible'});
          assert.equal(await page.locator('.gauge').count(),0);
          assert((await page.locator('#tool-result').innerText()).includes('Live Amazon price'));
          liveOnly=false;
        }
        // Playwright's clock belongs to the context: let the next page's timers and frames run.
        await page.clock.resume();
        if (reduced) {
          const raw='https://amazon.com/Steel-Kitchen-Kettle/dp/B000000100';
          for (const reload of [false,true]) {
            if (reload) await page.reload();
            else await page.goto('https://wowpriced.test/'+slug+'/?q='+encodeURIComponent(raw));
            await waitData(page);
            assert.equal(await page.locator('#q').inputValue(),raw);
            assert.equal(await page.evaluate(()=>scrollY),0,'auto-run must not scroll');
            assert.equal(await page.evaluate(mode=>window.toolEvents.filter(e=>e[0]===(mode==='checker'?'analyze':'search_discounts')).length,mode),1,
              'one automatic run per load, including reload');
          }
        }
        await page.close();
      }
      const home=await context.newPage();
      home.on('pageerror',error => errors.push(error.message));
      await home.goto('https://wowpriced.test/');
      await checkJoin(home, '#subscribe', path.join(shots,`home-join-${width}-${theme}${reduced ? '-reduced' : ''}.png`));
      await home.close();
      const noJS=await browser.newContext({viewport:{width,height:1000},javaScriptEnabled:false});
      await noJS.route('**/*', async route => {
        const url=new URL(route.request().url());
        if (url.origin !== 'https://wowpriced.test') return route.fulfill({status:204,body:''});
        let file=path.join(root,url.pathname);
        if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file=path.join(file,'index.html');
        if (!fs.existsSync(file) && fs.existsSync(file+'.html')) file+='.html';
        return route.fulfill({status:fs.existsSync(file) ? 200 : 404,contentType:path.extname(file)==='.css' ? 'text/css' : 'text/html',body:fs.existsSync(file) ? fs.readFileSync(file) : ''});
      });
      for (const slug of ['','amazon-deal-checker/','amazon-discount-finder/']) {
        const plain=await noJS.newPage();
        await plain.goto('https://wowpriced.test/'+slug);
        await plain.locator(slug ? '.tool-header>.join' : '#subscribe').click();
        await plain.waitForURL('**/join');
        assert.equal(await plain.locator('#join-email').count(),1,'no JS still reaches /join');
        await plain.close();
      }
      await noJS.close();
      assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[]);
      await context.close();
    }
    console.log('Tool pages Chromium: 390/1280, light/dark, photo clouds/no gauges, no scroll/hero CLS, linear 25/50/90% progress and lazy Join focus/escape/no-JS, ordered real steps, timing/reduced motion, cards, tags, targets and overflow passed');
  } finally {await browser.close();}
}

// node tests/tool_pages_browser.cjs /path/to/chromium [screenshots]
// Or: node tests/tool_pages_browser.cjs /local/build /path/to/chromium screenshots
async function main() {
  if (process.argv.length>=5) return run(process.argv[2],process.argv[3],process.argv[4]);
  const scratch=fs.mkdtempSync('/tmp/task328-browser-');
  try {await run(buildFixture(scratch),process.argv[2],process.argv[3] || 'artifacts/tool-pages');}
  finally {fs.rmSync(scratch,{recursive:true,force:true});}
}
module.exports={buildFixture};
if (require.main === module) main().catch(error => {console.error(error);process.exitCode=1;});
