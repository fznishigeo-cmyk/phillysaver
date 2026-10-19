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
cfg.affiliate_amazon_tag="example-20"
rows=[make_deal(dedupe_key="task328-kettle", item="Steel kitchen kettle", store="Amazon", category="kitchen",
    verify_tier="verified", codes=[], conditions=["new"], final_price=15, regular_price=30,
    canonical_url="https://amazon.com/dp/B099999999", asin="B099999999",
    first_seen_at=now.isoformat(), price_rechecked_at=now.isoformat(),
    click_price_check={"asin":"B099999999","final":15,"buybox":15,"at":now.isoformat()},
    critic={"plausible":True,"confidence":.9,"reason":"Backed basis"},
    image_url="https://m.media-amazon.com/images/I/photo.jpg")]
db=DB(":memory:")
try: site.render_site(db,cfg,now,out_dir=Path(sys.argv[1])/"site",deals=rows)
finally: db.close()
`,scratch],{encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);
  return path.join(scratch,'site');
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
  const fixture=() => ({items:Array.from({length:6},(_,i) => ({asin:'B00000010'+i,
    title:i ? 'Steel kitchen kettle '+i : 'Steel kitchen kettle with temperature control',
    price_cents:1500+i*100,list_price_cents:3000,monthlySold:[1200,10000,2500,5000,20000,50][i],
    image:'https://m.media-amazon.com/images/I/photo.jpg',as_of:new Date().toISOString()}))});
  try {
    for (const width of [390,1280]) for (const theme of ['light','dark']) for (const reduced of [false,true]) {
      const context=await browser.newContext({viewport:{width,height:1000},colorScheme:theme,
        reducedMotion:reduced ? 'reduce' : 'no-preference'});
      const calls=[],unexpected=[],errors=[];
      let failure=false;
      await context.route('**/*',async route => {
        const url=new URL(route.request().url());
        if (url.origin !== 'https://wowpriced.test') {
          if (url.hostname === 'm.media-amazon.com') return route.fulfill({contentType:'image/svg+xml',body:
            '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"><rect width="300" height="300" fill="#f2f3f5"/><path d="M200 100Q270 80 250 170Q235 200 210 165M75 110L60 85L40 85L70 150L80 230Q150 250 220 230L210 110Z" fill="#b9c6cd" stroke="#263e46" stroke-width="10"/><ellipse cx="143" cy="110" rx="68" ry="14" fill="#5d7681"/></svg>'});
          unexpected.push(url.origin);return route.fulfill({status:204,body:''});
        }
        if (url.pathname === '/api/search') {
          calls.push(url.search);
          assert.deepEqual([...url.searchParams.keys()],['asin']);
          assert.equal(url.searchParams.get('asin'),'B000000100');
          await new Promise(resolve => setTimeout(resolve,180));
          return route.fulfill({contentType:'application/json',body:JSON.stringify(failure ?
            {items:[],reason:'budget_exhausted'} : fixture())});
        }
        const candidate=path.resolve(root,'.'+url.pathname);
        if (candidate !== root && !candidate.startsWith(root+path.sep)) return route.fulfill({status:404,body:''});
        let file=candidate;
        if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file=path.join(file,'index.html');
        if (!fs.existsSync(file)) return route.fulfill({status:404,body:''});
        return route.fulfill({contentType:({'.html':'text/html','.json':'application/json','.js':'application/javascript',
          '.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)] || 'application/octet-stream',body:fs.readFileSync(file)});
      });
      for (const mode of ['checker','finder']) {
        const slug=mode === 'checker' ? 'amazon-deal-checker' : 'amazon-discount-finder';
        const page=await context.newPage();
        page.on('pageerror',error => errors.push(error.message));
        await page.addInitScript(() => {window.toolEvents=[];window.wpTrack=(...args) => window.toolEvents.push(args);});
        await page.goto('https://wowpriced.test/'+slug+'/');
        await page.waitForFunction(() => typeof toolSubmit === 'function');
        assert.equal(await page.locator('#tool-result').isVisible(),false);
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
        await page.locator('#tool-submit').click();
        await page.locator('#analyzer').waitFor({state:'visible'});
        if (!reduced) {
          await page.waitForTimeout(500);
          assert.equal(await page.locator('#tool-result').isVisible(),false,'minimum step time is real');
        }
        await page.locator('#tool-result').waitFor({state:'visible',timeout:28000});
        const timings=await page.evaluate(() => ({steps:window.doneSteps,started:window.toolStarted}));
        assert.deepEqual(timings.steps.map(s => s.index),Array.from({length:mode === 'checker' ? 5 : 4},(_,i) => i));
        const duration=timings.steps.at(-1).at-timings.started;
        assert(reduced ? duration < 1500 : duration >= 8000 && duration <= 10000,`duration ${duration} ms`);
        assert.equal(await page.locator('#analyzer').getAttribute('aria-busy'),'false');
        assert.equal(await page.locator('#tool-progress').getAttribute('aria-valuenow'),'100');
        assert(await page.locator('#verified-deals .product-card').count() > 0);
        if (mode === 'checker') {
          assert.equal(calls.length,before+1);
          assert.equal(await page.locator('#top-sellers .product-card').count(),5);
          assert((await page.locator('#tool-result').innerText()).includes('Big discount · 50% off list price'));
          assert((await page.locator('#tool-result').innerText()).includes('$15.00'));
          assert((await page.locator('#tool-result').innerText()).includes('price checked '));
          assert.equal(await page.locator('#top-sellers .monthly').count(),5);
          assert.equal(await page.locator('#tool-result s').innerText(),'$30.00');
        } else {
          assert.equal(calls.length,before,'finder cannot call the API');
          assert.equal(await page.locator('#tool-result .amazon-button').getAttribute('href'),
            'https://www.amazon.com/s?k=Steel%20Kitchen%20Kettle&rh=p_n_deal_type%3A23566065011&tag=example-20');
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
          assert.equal(grid.columns,width === 390 ? 2 : 5,`${label}: ${grid.template}`);
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
          await page.locator('#tool-result').waitFor({state:'visible'});
          assert((await page.locator('#tool-result').innerText()).includes("Today's live price checks have reached their limit."));
          assert.equal(await page.locator('.steps [data-state=done]').count(),0,'failed work never ticks');
          failure=false;
        }
        await page.close();
      }
      assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[]);
      await context.close();
    }
    console.log('Tool pages Chromium: 390/1280, light/dark, ordered real steps, timing/reduced motion, cards, tags, targets and overflow passed');
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
