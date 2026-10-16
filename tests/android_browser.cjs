'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs'), path=require('node:path'), http=require('node:http');
const {once}=require('node:events');
(async()=>{
  const {chromium}=require('playwright');
  let browser;
  try {browser=await chromium.launch({headless:true,executablePath:'/opt/pw-browsers/chromium',args:['--no-sandbox']});}
  catch(e) {
    if(!/Operation not permitted|Executable doesn't exist/.test(e.message))throw e;
    console.log('Chromium blocked before launch by sandbox sockets; native app QA remains pending.');process.exitCode=77;return;
  }
  const root=path.resolve(process.argv[2]), deals=JSON.parse(fs.readFileSync(process.argv[3]));
  // Playwright's offline emulation does not reach service-worker fetches, so the
  // test makes the origin unreachable instead (sockets dropped while `down`).
  let down=false;
  const server=http.createServer((req,res)=>{
    if(down){req.socket.destroy();return;}
    const url=new URL(req.url,'http://localhost');
    let file=path.resolve(root,'.'+decodeURIComponent(url.pathname));
    if(file!==root&&!file.startsWith(root+path.sep)){res.writeHead(403).end();return;}
    if(fs.existsSync(file)&&fs.statSync(file).isDirectory())file=path.join(file,'index.html');
    if(!fs.existsSync(file)){res.writeHead(404).end();return;}
    res.setHeader('Content-Type',{'.html':'text/html; charset=utf-8','.json':'application/json','.js':'application/javascript','.webmanifest':'application/manifest+json','.png':'image/png'}[path.extname(file)]||'text/plain');
    res.end(fs.readFileSync(file));
  });
  try {
    server.listen(0,'127.0.0.1');await once(server,'listening');
    const origin='http://127.0.0.1:'+server.address().port, external=[], errors=[];
    const makeContext=async()=>{
      const c=await browser.newContext({viewport:{width:390,height:844}});
      await c.route('**/*',route=>{
        const url=route.request().url();
        if(new URL(url).origin!==origin){external.push(url);return route.abort();}
        return route.continue();
      });
      return c;
    };
    const ordinary=await makeContext(), plain=await ordinary.newPage();
    await plain.goto(origin+'/');
    assert.equal(await plain.locator('html').evaluate(e=>e.classList.contains('in-app')),false);
    assert.equal(await plain.evaluate(async()=>(await navigator.serviceWorker.getRegistrations()).length),0);
    await ordinary.close();external.length=0;

    const context=await makeContext(), page=await context.newPage();
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(origin+'/?src=twa');
    await page.waitForFunction(()=>document.documentElement.classList.contains('in-app'));
    await page.waitForFunction(()=>!!navigator.serviceWorker.controller);
    await page.evaluate(()=>window.dispatchEvent(new Event('beforeinstallprompt')));
    for(const selector of ['#paywall','#pwa-actions','#pwa-install','#pwa-ios','a[href^="https://buy.stripe.com/"]','#loginlink','#account']) {
      for(const el of await page.locator(selector).all())assert.equal(await el.isVisible(),false,selector);
    }
    assert(await page.locator('#subscribe').isVisible());assert.match(await page.locator('#subscribe').innerText(),/Join free/);
    const cfg=await page.evaluate(()=>Array.from(window.dataLayer||[]).map(a=>Array.from(a)).find(a=>a[0]==='config'));
    assert.equal(cfg[2].app_context,'android');
    await page.goto(origin+'/join.html');
    assert.equal(await page.locator('#join-google').isVisible(),false);
    await page.getByRole('button',{name:'Continue with email',exact:false}).click();
    assert(await page.locator('#join-email').isVisible());
    await page.goto(origin+'/best-deals-today/');assert(await page.locator('html').evaluate(e=>e.classList.contains('in-app')));
    const amazon=deals.find(d=>d.store==='Amazon');
    await page.goto(origin+amazon.deal_path);
    assert.equal(await page.locator('.deal-detail .price-history').isVisible(),false);
    assert(await page.locator('.deal-detail .amazon-as-of').isVisible());
    await page.goto(origin+amazon.product_path);
    assert.equal(await page.locator('.product-facts').isVisible(),false);
    assert.equal(await page.locator('.price-verdict').isVisible(),false);
    assert.equal(await page.locator('.product-detail .price-history').isVisible(),false);
    assert(await page.getByText('Amazon price history is on wowpriced.com in your browser.',{exact:true}).isVisible());
    assert(await page.getByRole('link',{name:'See the deal',exact:true}).isVisible());
    await page.goto(origin+amazon.deal_path);
    const link=page.locator('.deal-detail > a.getdeal');
    assert((await link.getAttribute('href')).includes('tag=example-20'));
    const nav=context.waitForEvent('request',r=>new URL(r.url()).hostname.endsWith('amazon.com')&&r.isNavigationRequest());
    await link.click();
    const request=await nav;assert.equal(new URL(request.url()).searchParams.has('tag'),false);

    await page.goto(origin+'/check/?text='+encodeURIComponent('Look https://www.bestbuy.com/site/example/6505727.p'));
    const result=page.locator('#check-result');
    await result.getByRole('link',{name:'See the deal',exact:true}).waitFor();
    assert.match(await result.innerText(),/Widget bestbuy/);
    await page.goto(origin+'/check/?title=Headphones');await result.getByText("We haven't verified a price for this product yet.",{exact:true}).waitFor();
    await page.goto(origin+'/check/?text='+encodeURIComponent('Look https://a.co/example'));
    await result.getByText(/Open it in Chrome, then share the product page from there/).waitFor();
    await page.goto(origin+'/check/?u=https://www.amazon.com/dp/B000000000');
    await result.getByRole('link',{name:'See the deal',exact:true}).waitFor();
    assert.equal((await result.innerText()).replace(/\s+/g,' ').trim(),'Kitchen Widget 0 See the deal');
    assert.equal(await result.locator('time,.detail-price').count(),0);
    assert(!external.some(u=>/facebook\.net|facebook\.com\/tr|clarity\.ms/.test(u)),external.join('\n'));
    const keys=await page.evaluate(async()=>{
      const cache=await caches.open((await caches.keys()).find(k=>k.startsWith('wp-android-')));
      return (await cache.keys()).map(r=>new URL(r.url).pathname);
    });
    assert.deepEqual(keys,['/offline.html']);
    down=true;
    await page.goto(origin+'/check/?offline-test=1');
    await page.getByRole('heading',{name:"You're offline.",exact:true}).waitFor();
    assert(!await page.locator('body').innerText().then(t=>t.includes('$')));
    down=false;await page.getByRole('button',{name:'Retry',exact:true}).click();
    await page.getByRole('heading',{name:'Is this a good deal?',exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    await context.close();
  } finally {server.close();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
