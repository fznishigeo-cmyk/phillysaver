// Native offline QA: node tests/tool_pages_1020_home_browser.cjs /path/to/chromium [shots]
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {buildFixture}=require('./header_polish_browser.cjs');
const audit=fs.readFileSync('scripts/mobile_audit.cjs','utf8');
const inspect=audit.slice(audit.indexOf('function hitAreaMath()'),audit.indexOf('async function runAudit()'));
async function main() {
  const {chromium}=require('playwright');
  let browser;
  try {browser=await chromium.launch({headless:true,executablePath:process.argv[2]});}
  catch(e) {
    if(!/Operation not permitted|Executable doesn't exist|Failed to launch|sandbox|Target .* closed/.test(e.message)) throw e;
    console.log('Chromium unavailable: no phone audit or screenshots measured');process.exitCode=77;return;
  }
  const scratch=fs.mkdtempSync('/tmp/task339-home-browser-');
  const shots=path.resolve(process.argv[3] || 'artifacts/tool-home');fs.mkdirSync(shots,{recursive:true});
  const metrics={};
  try {
    const roots=Object.fromEntries(['off','chips','line'].map(variant=>[variant,
      buildFixture(path.join(scratch,variant),true,true,true,true,true,variant)]));
    const fixture=JSON.parse(fs.readFileSync(path.join(scratch,'chips','fixture.json')));
    for(const width of [390,1280]) for(const theme of ['light','dark']) {
      let baseline;
      for(const variant of ['off','chips','line']) {
        const errors=[],unexpected=[];
        const context=await browser.newContext({viewport:{width,height:844},colorScheme:theme,
          isMobile:width<900,hasTouch:width<900,reducedMotion:'reduce',serviceWorkers:'block'});
        await context.route('**/*',route=>{
          const url=new URL(route.request().url());
          if(url.origin!=='https://wowpriced.test') {
            if(route.request().resourceType()==='image') return route.fulfill({contentType:'image/svg+xml',body:
              '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"><rect width="300" height="300" fill="#ddd"/></svg>'});
            unexpected.push(url.origin);return route.fulfill({status:204,body:''});
          }
          let file=path.resolve(roots[variant],'.'+url.pathname);
          assert(file===roots[variant] || file.startsWith(roots[variant]+path.sep));
          if(fs.existsSync(file) && fs.statSync(file).isDirectory()) file=path.join(file,'index.html');
          if(!fs.existsSync(file) && fs.existsSync(file+'.html')) file+='.html';
          return route.fulfill({status:fs.existsSync(file)?200:404,body:fs.existsSync(file)?fs.readFileSync(file):'',
            contentType:({'.html':'text/html','.js':'application/javascript','.css':'text/css','.json':'application/json',
              '.svg':'image/svg+xml','.woff2':'font/woff2','.webmanifest':'application/manifest+json'})[path.extname(file)] || 'application/octet-stream'});
        });
        await context.addInitScript(()=>localStorage.setItem('wp.install.engagedMs','180000'));
        const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
        await page.clock.install({time:new Date(Date.parse(fixture.now)+120000)});
        await page.goto('https://wowpriced.test/');
        await page.waitForFunction(()=>document.querySelector('#content .card') &&
          /24 (?:live|verified) deals/i.test(document.querySelector('#tagline').innerText));
        await page.evaluate(()=>{
          const event=new Event('beforeinstallprompt',{cancelable:true});event.prompt=()=>Promise.resolve();
          event.userChoice=Promise.resolve({outcome:'dismissed'});dispatchEvent(event);
        });
        await page.locator('#pwa-header-install').waitFor({state:'visible'});
        await page.locator('#ticker:not(.pending)').waitFor({state:'visible'});
        const report=await page.evaluate(`(()=>{${inspect}\nreturn inspectPage(hitAreaMath());})()`);
        const ticker=await page.locator('#tkband').boundingBox();
        const header=await page.locator('header').boundingBox();
        if(variant==='off') baseline={report,ticker,header};
        else {
          const selector=width>=900?'.ticker-tools':'.phone-tools';
          const links=page.locator(selector+' a');assert.equal(await links.count(),2);
          assert.equal(await page.locator(width>=900?'.phone-tools':'.ticker-tools').isVisible(),false);
          if(width>=900) {
            assert.deepEqual(header,baseline.header,'desktop header unchanged');
            assert.equal(ticker.height,baseline.ticker.height,'same ticker row height');
            const run=await page.locator('.tkrun').boundingBox(),nav=await page.locator('.ticker-tools').boundingBox();
            assert(Math.abs(run.x+run.width-nav.x)<1,'links immediately right of ticker');
            assert.equal(nav.y,ticker.y);assert(nav.x+nav.width<=ticker.x+ticker.width+1);
          } else {
            assert.equal(report.headerH,182,variant+' headerH');
            assert.equal(report.firstCardTop,variant==='chips'?388:416,variant+' firstCardTop');
            assert.equal(report.headerH,baseline.report.headerH);
            if(variant==='chips') {
              assert.equal(report.firstCardTop,baseline.report.firstCardTop);
              assert.equal(await page.locator('#cattabs>*:last-child').getAttribute('class'),'home-tools phone-tools');
              await page.locator('#cattabs').evaluate(el=>{el.scrollLeft=el.scrollWidth;});
            } else {
              const line=await page.locator('.tool-line').boundingBox();assert(line.height<=28);
              assert.equal(line.y,header.y+header.height);
            }
          }
          for(let i=0;i<2;i++) {
            const link=links.nth(i);assert(await link.isVisible());
            const box=await link.boundingBox();assert(box.width>=44);
            assert(box.height>=(variant==='line' && width<900?28:44));
            assert(await link.evaluate(el=>{const b=el.getBoundingClientRect();return el.contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2));}), 'link is tappable');
            const href=await link.getAttribute('href');assert.equal(href,i?'/amazon-discount-finder/':'/amazon-deal-checker/');
            await link.click({trial:true});
          }
          const id=`${variant}-${width}-${theme}`;
          metrics[id]={headerH:report.headerH,firstCardTop:report.firstCardTop};
          assert.deepEqual(report.overflow,[]);
          await page.screenshot({path:path.join(shots,id+'.png'),fullPage:false});
          // Category redraws must leave both chips at the end of the row.
          if(width<900 && variant==='chips') {
            // The page's state lives in a closure: redraw through a real tab tap.
            await page.locator('#cattabs .cattab').nth(2).click();
            assert.equal(await page.locator('#cattabs .phone-tools a').count(),2);
          }
        }
        assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
        await context.close();
      }
    }
    fs.writeFileSync(path.join(shots,'metrics.json'),JSON.stringify(metrics,null,2)+'\n');
    console.log(JSON.stringify(metrics,null,2));
  } finally {await browser.close();fs.rmSync(scratch,{recursive:true,force:true});}
}
if(require.main===module) main().catch(e=>{console.error(e);process.exitCode=1;});
