// Real Chromium, synthetic publication and intercepted local files only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

(async () => {
  const root = path.resolve(process.argv[2]);
  const tools = ['amazon-deal-checker','amazon-discount-finder']
    .filter(slug=>fs.existsSync(path.join(root,slug,'index.html'))).map(slug=>'/'+slug+'/');
  let browser;
  try {
    browser = await chromium.launch({headless:true, executablePath:'/opt/pw-browsers/chromium'});
  } catch (e) {
    console.log('Chromium cannot launch: '+e.message.split('\n')[0]);
    process.exitCode=77;return;
  }
  try {
    for (const [width,height] of [[390,844],[1280,800]]) {
      const context = await browser.newContext({viewport:{width,height},isMobile:width===390,hasTouch:width===390});
      await context.route('**/*', route => {
        const request=route.request(), url=new URL(request.url());
        if (request.resourceType()==='image') return route.fulfill({contentType:'image/png',
          body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1cAAAAASUVORK5CYII=','base64')});
        if (url.origin!=='https://wowpriced.test' || request.method()!=='GET') return route.abort();
        let file=path.resolve(root,'.'+url.pathname);
        if (!file.startsWith(root+path.sep) && file!==root) return route.abort();
        if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file=path.join(file,'index.html');
        if (!fs.existsSync(file)) return route.fulfill({status:404,body:''});
        return route.fulfill({contentType:{'.html':'text/html','.json':'application/json','.js':'application/javascript',
          '.css':'text/css','.webmanifest':'application/manifest+json'}[path.extname(file)] || 'application/octet-stream',
          body:fs.readFileSync(file)});
      });
      const page=await context.newPage(), errors=[];
      page.on('pageerror', error=>errors.push(error.message));
      const ready=async()=>{
        await page.waitForFunction(()=>sessionStorage.getItem('wp.browse') && document.querySelector('#cattabs .active'));
        await page.waitForTimeout(700); // Includes Saved's late redraw and exact anchor correction.
      };
      const fresh=async()=>{
        assert.equal(await page.locator('#q').inputValue(),'');
        assert(await page.locator('#clearsearch').isHidden());
        assert.equal(await page.locator('#cattabs .active').getAttribute('data-i'),'0');
        assert(await page.locator('#content .card').count()>0);
        assert.equal(await page.evaluate(()=>scrollY),0);
      };
      const search=async()=>{
        await page.locator('#q').fill('hijab');
        await page.waitForFunction(()=>document.querySelector('#searchcount').textContent.includes('No deals match'));
        assert.equal(await page.locator('#content .card').count(),0);
      };
      for(const [selector,method] of [['h1.brand a','logo'],['.tklabel','ticker'],['#drop','timer']]) {
        await page.goto('https://wowpriced.test/?case='+method);await ready();
        for (const slug of ['amazon-deal-checker','amazon-discount-finder']) {
          const links=page.locator('a[href="/'+slug+'/"]');
          assert.equal(await links.count(),process.argv[3]==='public' ? 1 : 0);
          assert.equal(await page.locator('#footerlinks a[href="/'+slug+'/"]').count(),
            process.argv[3]==='public' ? 1 : 0);
        }
        await search();
        const previous=await page.evaluate(()=>history.state.wpBrowse);
        // DOM click keeps the current scroll position until the real handler runs.
        await page.evaluate(selector=>{scrollTo(0,200);document.querySelector(selector).click();},selector);
        await fresh();
        assert.notEqual(await page.evaluate(()=>history.state.wpBrowse),previous);
        const events=await page.evaluate(()=>Array.from(window.dataLayer || [],args=>Array.from(args))
          .filter(args=>args[0]==='event' && args[1]==='home_reset'));
        assert.deepEqual(events,[['event','home_reset',{method,send_to:'G-ABC123XYZ9'}]]);
        await page.goBack();await ready();
        assert.equal(await page.locator('#q').inputValue(),'hijab');
        assert.equal(await page.locator('#content .card').count(),0);
        await page.reload();await ready();
        assert.equal(await page.locator('#q').inputValue(),'hijab');
        assert.equal(await page.locator('#content .card').count(),0);
        await page.goForward();await ready();await fresh();
      }
      // A plain home link from another page drops the remembered private query.
      const deal=JSON.parse(fs.readFileSync(path.join(root,'deals.json'))).deals.find(d=>d.deal_path);
      assert(deal);
      for (const destination of [deal.deal_path,...tools]) {
        await search();
        await page.goto('https://wowpriced.test'+destination);
        if (tools.includes(destination)) {
          assert.equal(await page.locator('#tool-form #q').count(),1);
          assert.equal(await page.locator('#cattabs').count(),0);
        }
        await page.locator('a[href="/"]').first().click();await ready();await fresh();
      }
      // Back from a real deal page retains the exact card, at both breakpoints.
      while(await page.locator('#content .card').count()<120) {
        await page.evaluate(()=>document.getElementById('moremore')?.click());
      }
      const card=page.locator('#content .card').nth(119);
      await card.evaluate(el=>scrollTo({top:scrollY+el.getBoundingClientRect().top,behavior:'instant'}));
      await page.waitForTimeout(300);
      const snapshot=await page.evaluate(()=>{
        const memory=JSON.parse(sessionStorage.getItem('wp.browse'));
        return memory.entries[history.state.wpBrowse];
      });
      await page.locator('#content .card[data-key="'+snapshot.anchor.key+'"] .title a').evaluate(el=>el.click());
      await page.waitForURL('**/deal/**');
      await page.goBack();await ready();
      await page.waitForFunction(()=>document.querySelectorAll('#content .card').length>=120);
      const offset=await page.locator('#content .card[data-key="'+snapshot.anchor.key+'"]')
        .evaluate(el=>el.getBoundingClientRect().top);
      assert(Math.abs(offset-snapshot.anchor.offset)<=4,`${width}: Back card drift ${offset-snapshot.anchor.offset}px`);
      assert.deepEqual(errors,[]);
      console.log(`${width}x${height}: three Home controls, Back, reload, ordinary Home navigation and exact card: passed`);
      await context.close();
    }
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
