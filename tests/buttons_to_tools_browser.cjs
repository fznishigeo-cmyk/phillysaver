// Native navigation and query handoff; local fixtures only. Operator runs Chromium.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {buildFixture}=require('./tool_pages_browser.cjs');
async function run(root,executablePath) {
  const {chromium}=require('playwright');
  const browser=await chromium.launch({headless:true,executablePath});
  try {
    for(const width of [390,1280]) {
      const context=await browser.newContext({viewport:{width,height:1000}}),errors=[];
      await context.route('**/*',async route=>{
        const url=new URL(route.request().url());
        if(url.origin!=='https://wowpriced.test') return route.fulfill({status:204,body:''});
        if(url.pathname==='/api/search') return route.fulfill({contentType:'application/json',body:JSON.stringify({items:[{
          asin:'B000000100',title:'Wireless earbuds with charging case',price_cents:1500,list_price_cents:3000,
          as_of:new Date().toISOString()}]})});
        let file=path.join(root,url.pathname);
        if(fs.existsSync(file)&&fs.statSync(file).isDirectory())file=path.join(file,'index.html');
        if(!fs.existsSync(file))return route.fulfill({status:404,body:''});
        return route.fulfill({contentType:({'.html':'text/html','.js':'application/javascript','.css':'text/css',
          '.json':'application/json'})[path.extname(file)]||'application/octet-stream',body:fs.readFileSync(file)});
      });
      const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
      await page.clock.install();
      for(const [selector,slug,raw] of [['#analyze-search','amazon-deal-checker','wireless earbuds'],
        ['#analyze-search','amazon-deal-checker','https://amazon.com/dp/B000000100'],
        [width<900?'#search-discounts-mobile':'#search-discounts','amazon-discount-finder','wireless earbuds']]) {
        await page.goto('https://wowpriced.test/');
        await page.locator('#q').fill(raw);
        const button=page.locator(selector);
        assert.equal(await button.getAttribute('href'),'/'+slug+'/?q='+encodeURIComponent(raw));
        assert.equal(await button.getAttribute('target'),null);assert.equal(await button.getAttribute('rel'),null);
        await button.click();await page.waitForURL('**/'+slug+'/?q=*');
        await page.waitForFunction(()=>toolActive && toolActive.ready);
        assert.equal(await page.locator('#q').inputValue(),raw);
        assert.equal(await page.locator('#analyzer').getAttribute('aria-busy'),'true');
        assert.equal(await page.evaluate(()=>scrollY),0,'query handoff must not scroll');
        await page.clock.runFor(25032);
        assert.equal(await page.evaluate(()=>scrollY),0,'result must not scroll');
        assert.equal(await page.locator('#tool-result').isVisible(),true);
        if(slug==='amazon-discount-finder') {
          const url=new URL(await page.locator('#tool-result .amazon-button').getAttribute('href'));
          assert.equal(url.searchParams.get('rh'),'p_n_deal_type:210906366011');
        }
      }
      assert.deepEqual(errors,[]);await context.close();
    }
    console.log('344 Chromium: native checker/finder navigation at 390/1280, query fill/start/no scroll, coupon filter passed');
  } finally {await browser.close();}
}
async function main() {
  if(process.argv.length>=4)return run(process.argv[2],process.argv[3]);
  const scratch=fs.mkdtempSync('/tmp/task344-browser-');
  try {await run(buildFixture(scratch),process.argv[2]);}
  finally {fs.rmSync(scratch,{recursive:true,force:true});}
}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
