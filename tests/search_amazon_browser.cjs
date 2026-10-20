// Route local files in memory; abort all external requests, including clicks.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async()=>{
  let chromium;
  try { ({chromium}=require('playwright')); }
  catch(e) {
    if(e.code!=='MODULE_NOT_FOUND')throw e;
    console.log('Chromium unavailable: Playwright missing');process.exitCode=77;return;
  }
  let browser;
  try { browser=await chromium.launch({headless:true,executablePath:process.argv[3]}); }
  catch(e) {
    if(!/Operation not permitted|Executable doesn't exist/.test(e.message))throw e;
    console.log('Chromium unavailable: sandbox launch denied or browser missing');process.exitCode=77;return;
  }
  const root=path.resolve(process.argv[2]);
  try {
    for (const width of [390,1280]) {
      const context=await browser.newContext({viewport:{width,height:844}}), external=[], errors=[];
      await context.route('**/*',route=>{
        const url=new URL(route.request().url());
        if(url.origin!=='https://wowpriced.test') {external.push(url.href);return route.abort();}
        let file=path.resolve(root,'.'+url.pathname);
        if(!file.startsWith(root+path.sep)&&file!==root)return route.abort();
        if(fs.existsSync(file)&&fs.statSync(file).isDirectory())file=path.join(file,'index.html');
        if(!fs.existsSync(file))return route.fulfill({status:404,body:''});
        return route.fulfill({contentType:({'.html':'text/html','.json':'application/json','.js':'application/javascript',
          '.css':'text/css'})[path.extname(file)]||'application/octet-stream',body:fs.readFileSync(file)});
      });
      const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
      await page.goto('https://wowpriced.test/');
      await page.waitForFunction(()=>document.querySelector('#content .card'));
      await page.evaluate(()=>{
        window.searchEvents=[];window.wpTrack=(...args)=>window.searchEvents.push(args);
        window.automaticOutbounds=[];
        const collect=e=>{
          const a=e.target.closest('a[href]');
          if(a&&new URL(a.href).origin!==location.origin)window.automaticOutbounds.push({link_url:a.href});
        };
        // Model Enhanced Measurement reading outbound hrefs before/after our handler.
        document.addEventListener('click',collect,true);
        document.addEventListener('click',collect);
        document.addEventListener('auxclick',collect,true);
        document.addEventListener('auxclick',collect);
      });
      assert.equal(await page.locator('[data-search-amazon]').count(),0);
      const calm = await page.locator('.analyze-long').textContent().catch(() => '').then(label => label === 'AI Analysis');
      assert.equal(await page.locator('#q').getAttribute('placeholder'), calm && width < 900
        ? 'Search deals on Amazon' : 'Search deals or any Amazon product');
      const input=page.locator('#q'), link=page.locator('[data-search-amazon]');
      const search=async q=>{await input.fill(q);await page.waitForTimeout(350);};
      await search('cupping');
      await link.waitFor({state:'visible'});
      assert.equal(await link.textContent(),'Search Amazon for "cupping"');
      assert.equal(await link.getAttribute('href'),'https://www.amazon.com/');
      assert.equal(await link.getAttribute('target'),'_blank');
      assert.equal(await link.getAttribute('rel'),'sponsored noopener');
      assert.equal(await link.evaluate(el=>el.classList.contains('getdeal')), !calm);
      if (calm) {
        assert.equal(await page.locator('.search-amazon-top [data-search-amazon], #search-result-top').count(), 0);
        assert.equal(await link.evaluate(el=>getComputedStyle(el).animationName), 'none');
        assert.equal(await link.evaluate(el=>getComputedStyle(el).backgroundImage), 'none');
      }
      assert(await page.locator('.search-recovery').innerText().then(s=>s.includes('Amazon links may earn us a commission.')));
      assert.deepEqual(external,[],'no external request before the click');
      const outboundPromise=context.waitForEvent('request',request=>request.url().startsWith('https://www.amazon.com/s?'));
      const popupPromise=context.waitForEvent('page');await link.click();
      const popup=await popupPromise;
      assert.equal((await outboundPromise).url(),'https://www.amazon.com/s?k=cupping&tag=example-20');
      await page.waitForFunction(()=>window.searchEvents.some(e=>e[0]==='search_amazon'));
      assert.deepEqual(await page.evaluate(()=>window.searchEvents.filter(e=>e[0]==='search_amazon')),[['search_amazon',undefined]]);
      assert(!await page.evaluate(()=>JSON.stringify(window.searchEvents).includes('cupping')));
      assert.deepEqual(await page.evaluate(()=>window.automaticOutbounds),[
        {link_url:'https://www.amazon.com/'},{link_url:'https://www.amazon.com/'}]);
      await popup.close();
      external.length=0;
      await search('  café   & "tea"  ');
      assert.equal(await link.getAttribute('href'),'https://www.amazon.com/');
      assert.equal(await link.textContent(),'Search Amazon for "café & "tea""');
      await search('kettle');
      if (calm) assert.equal(await link.count(),0,'no secondary Amazon pill for local matches');
      else {
        await link.waitFor({state:'visible'});
        assert(await page.locator('#search-amazon-result').innerText().then(s=>s.startsWith("Not what you're looking for? ")));
      }
      assert.equal(await page.locator('.search-recovery').count(),0);
      if (await page.locator('#savedview').count()) {
        // Saved stays hidden until a deal is saved.
        await page.locator('#content .save-deal').first().click();
        await page.locator('#savedview').waitFor({state:'visible'});
        await page.locator('#savedview').click();
        assert.equal(await input.inputValue(),'');
        assert.equal(await link.count(),0);
        assert.equal(await page.locator('#search-amazon-result').innerHTML(),'');
        assert.equal(await page.locator('#search-amazon-result').isHidden(),true);
        await page.locator('#savedall').click();
        assert.equal(await link.count(),0);
        await search('kettle');
        if (!calm) await link.waitFor({state:'visible'});
      }
      for(const q of ['https://example.com/private','example.com','person@example.com','order 123456',
        '例子.测试/private','例子。测试/private','order ١٢٣٤٥٦','order 𝟏𝟐𝟑𝟒𝟓𝟔','']) {
        await search(q);assert.equal(await link.count(),0,q);
      }
      await search('x'.repeat(90));
      assert.equal(await link.textContent(),'Search Amazon for "'+'x'.repeat(80)+'"');
      assert.equal(await link.getAttribute('href'),'https://www.amazon.com/');
      assert.deepEqual(external,[],'searching must not request any external URL');
      assert.deepEqual(errors,[]);
      await context.close();
    }
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
