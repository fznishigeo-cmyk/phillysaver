// Offline real Chromium: four viewports, both themes, all shopper states.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
  const {chromium}=require('playwright');
  const root=path.resolve(process.argv[2]), shots=path.join(path.resolve(process.argv[3]),'screenshots');
  let browser;
  try {browser=await chromium.launch({headless:true,executablePath:'/opt/pw-browsers/chromium'});}
  catch(e){if(!/Operation not permitted|Executable doesn't exist/.test(e.message))throw e;
    console.log('Chromium unavailable: sandbox launch denied or no installed browser; screenshots not produced.');process.exitCode=77;return;}
  fs.mkdirSync(shots,{recursive:true});
  const pure=fs.readFileSync('functions/api/review-grade.js','utf8');
  const {gradeReviews}=await import('data:text/javascript;base64,'+Buffer.from(pure).toString('base64'));
  const settings=JSON.parse(fs.readFileSync(path.join(root,'search-config.json'))).review_checker;
  const errors=[], requests=[], measurements=[];
  const photo='<svg xmlns="http://www.w3.org/2000/svg" width="240" height="180"><rect width="240" height="180" fill="white"/><path d="M65 110V85a55 55 0 01110 0v25" fill="none" stroke="#2a6ebb" stroke-width="16"/><rect x="50" y="90" width="35" height="60" rx="16" fill="#173f71"/><rect x="155" y="90" width="35" height="60" rx="16" fill="#173f71"/></svg>';
  function response(grade,asin='B012345678'){
    const stats={current:Array(18).fill(-1),avg180:Array(18).fill(-1),atIntervalStart:Array(18).fill(-1)};
    if(grade!=='C'){stats.current[16]=45;stats.current[17]=100;}
    if(grade==='A'){stats.avg180[16]=44;stats.atIntervalStart[17]=75;}
    return {items:[{asin,title:'Everyday wireless over-ear headphones with a comfortable fit',image:'https://m.media-amazon.com/images/I/test.jpg',price_cents:2499,as_of:'2026-10-22T01:00:00Z',
      // A34/C16: Keepa's item/shipping split, or a Buy Box total that includes shipping.
      ...(grade==='B'?{shipping_cents:2515}:grade==='C'?{price_includes_shipping:true}:{})}],review:gradeReviews({stats},settings)};
  }
  try {
    for(const [width,height] of [[390,844],[768,1024],[1280,800],[1920,1080]])for(const theme of ['light','dark']){
      const context=await browser.newContext({viewport:{width,height},colorScheme:theme,serviceWorkers:'block'});
      let grade='A', pending=null, hold=false;
      await context.route('**/*',async route=>{
        const url=new URL(route.request().url());requests.push(url.origin+url.pathname);
        if(url.hostname==='m.media-amazon.com')return route.fulfill({contentType:'image/svg+xml',body:photo});
        if(url.pathname==='/subscribe')return route.fulfill({json:{ok:true}});
        if(url.origin!=='https://review.test')return route.fulfill({contentType:'application/javascript',body:''});
        if(url.pathname==='/api/search'){
          assert.match(url.searchParams.get('asin'),/^[A-Z0-9]{10}$/);assert.equal(url.searchParams.get('grade'),'reviews');
          if(hold){pending=route;return;}return route.fulfill({json:response(grade,url.searchParams.get('asin'))});
        }
        let file=path.resolve(root,'.'+url.pathname);
        assert(file.startsWith(root+path.sep));if(fs.existsSync(file)&&fs.statSync(file).isDirectory())file=path.join(file,'index.html');
        if(!fs.existsSync(file))return route.fulfill({status:404,body:''});
        return route.fulfill({contentType:{'.html':'text/html','.css':'text/css','.js':'application/javascript','.json':'application/json','.webmanifest':'application/manifest+json'}[path.extname(file)]||'application/octet-stream',body:fs.readFileSync(file)});
      });
      await context.addInitScript(()=>{
        Math.random=()=>0;window.reviewCLS=0;
        // Amazon clicks are recorded, never followed (the run stays offline).
        window.amazonClicks=[];document.addEventListener('click',e=>{const a=e.target.closest&&e.target.closest('a[href^="https://www.amazon.com/"]');if(a){window.amazonClicks.push({href:a.href,target:a.target,rel:a.rel});e.preventDefault();}},true);
        // The page's own wpTrack is wrapped: each tool event is recorded, then sent as usual.
        let wpTrack;window.tracked=[];Object.defineProperty(window,'wpTrack',{configurable:true,set(fn){wpTrack=fn;},
          get(){return wpTrack&&((name,params)=>{if(['deal_click','tool_grade'].includes(name))window.tracked.push([name,params]);return wpTrack(name,params);});}});
        new PerformanceObserver(list=>{for(const entry of list.getEntries())if(!entry.hadRecentInput)window.reviewCLS+=entry.value;}).observe({type:'layout-shift',buffered:true});
      });
      const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
      await page.clock.install({time:new Date('2026-10-22T02:00:00Z')});
      await page.clock.pauseAt(new Date('2026-10-22T02:00:01Z'));
      await page.goto('https://review.test/review-checker/');
      async function contrast() {
        const failures=await page.evaluate(()=>{
          const rgb=s=>s.match(/[\d.]+/g)?.slice(0,3).map(Number);
          const luminance=c=>c.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0);
          const problems=[];
          for(const el of document.querySelectorAll('h1,.intro h2,.header-label,.header-nav a,.run-report,.hero-box label,.hero-box button,.blue-bar,.independence,.coverage td,.coverage th,.deal-card h3,.card-title,.card-from,.deal-price,.view-report,.watch-bar a,.grade-box h2,.grade-box p,.grade-letter,.checks-table p,.checks-table h3,.checks-table summary,#review-terms h2,#review-terms li,.terms-fine,#cookies-ok,.footer-disclaimer,.footerfine,#footerlinks a,.back-top')) {
            if(!el.checkVisibility())continue;
            const style=getComputedStyle(el);let bgEl=el,bg;
            while(bgEl){const c=getComputedStyle(bgEl).backgroundColor;if(c!=='rgba(0, 0, 0, 0)'&&c!=='transparent'){bg=rgb(c);break;}bgEl=bgEl.parentElement;}
            if(!bg)bg=[255,255,255];const fg=rgb(style.color);if(!fg)continue;
            const a=luminance(fg),b=luminance(bg),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
            const large=parseFloat(style.fontSize)>=24||(parseFloat(style.fontSize)>=18.66&&Number(style.fontWeight)>=700);
            if(ratio<(large?3:4.5))problems.push({element:el.tagName,class:el.className,ratio});
          }
          return problems;
        });
        assert.deepEqual(failures,[],`${width} ${theme}: AA text contrast`);
      }
      const snap=async name=>{
        await contrast();
        const geometry=await page.evaluate(()=>{
          const rect=s=>{const e=document.querySelector(s);return e&&e.checkVisibility()?e.getBoundingClientRect():null;};
          const input=rect('#review-input'),button=rect('#review-form button'),modal=rect('#review-loading');
          const photo=rect('.report-photo'),grade=rect('.grade-box');
          return {header:input.right<=button.left+1&&input.left>=0&&button.right<=innerWidth,
            modal:!modal||(modal.left>=0&&modal.right<=innerWidth&&modal.top>=0&&modal.bottom<=innerHeight),
            report:!photo||!grade||photo.right<=grade.left||grade.bottom<=photo.top||photo.bottom<=grade.top||grade.right<=photo.left};
        });
        assert(Object.values(geometry).every(Boolean),`${width} ${name}: overlapping controls or clipped dialog`);
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${width} ${name}: horizontal scroll`);
        await page.screenshot({path:path.join(shots,`${width}-${theme}-${name}.png`),fullPage:true,animations:'disabled'});
      };
      await snap('start');
      // Placeholders are never cut off: the long one where it fits, the short one on phones.
      for(const field of await page.locator('[data-short-placeholder]').all())assert(await field.evaluate(e=>{
        const s=getComputedStyle(e),c=document.createElement('canvas').getContext('2d');c.font=s.font;
        return !e.checkVisibility()||c.measureText(e.placeholder).width<=e.clientWidth-parseFloat(s.paddingLeft)-parseFloat(s.paddingRight);}),`${width}: placeholder cut off`);
      if(width>=1280)assert.equal(await page.locator('#hero-input').getAttribute('placeholder'),'Paste an Amazon link or type a product');
      if(width===390)assert.equal(await page.locator('#review-input').getAttribute('placeholder'),'Link or product name');
      const watch=await page.locator('.watch-bar').boundingBox();assert(watch.y+watch.height<=height+1);
      // Shoppers never see star ratings or review counts (Amazon's rule), on the page or in the cards.
      assert(!/★|☆|out of 5|\b\d[\d,]*\s+(?:ratings|reviews)\b/i.test(await page.locator('body').innerText()));
      assert.equal(await page.locator('#review-terms').evaluate(e=>e.open),false);
      await page.keyboard.press('Tab');assert(await page.locator('.skip').evaluate(e=>e===document.activeElement));
      await page.keyboard.press('Tab');await page.keyboard.press('Tab');
      const input=page.locator('#review-input');assert(await input.evaluate(e=>e===document.activeElement));
      assert(await input.evaluate(e=>parseFloat(getComputedStyle(e).outlineWidth)>=2));
      // The terms notice opens after about 1.5 s; Escape closes it for this visit only.
      await page.clock.runFor(1600);const terms=page.locator('#review-terms');
      assert.equal(await terms.evaluate(e=>e.open),true);assert(await page.locator('#cookies-ok').evaluate(e=>e===document.activeElement));
      await snap('terms');
      await page.keyboard.press('Escape');assert.equal(await terms.evaluate(e=>e.open),false);
      assert.equal(await page.evaluate(()=>localStorage.getItem('wp.tools.cookies')),null);
      await input.fill('https://example.test/product');await page.locator('#review-form button').click();
      assert.equal(await input.getAttribute('aria-invalid'),'true');await snap('input-error');
      await page.reload();await page.locator('#review-input').waitFor();await page.clock.runFor(1600);
      assert.equal(await terms.evaluate(e=>e.open),true);
      await page.locator('#cookies-ok').click();assert.equal(await terms.evaluate(e=>e.open),false);
      assert.equal(await page.evaluate(()=>localStorage.getItem('wp.tools.cookies')),'1');
      await page.locator('#watch-close').click();
      await page.reload();await page.locator('#review-input').waitFor();await page.clock.runFor(3000);
      assert.equal(await terms.evaluate(e=>e.open),false);assert.equal(await page.locator('.watch-bar').isVisible(),false);
      for(grade of ['A','B','C']){
        await input.fill('https://amazon.com/dp/B012345678');await page.locator('#review-form button').click();
        await page.locator('#loading-product').filter({hasText:'headphones'}).waitFor();
        await page.clock.runFor(5000);
        const percent=Number(await page.locator('#review-progress').getAttribute('aria-valuenow'));
        assert(percent>=49&&percent<=51);assert.equal(await page.locator('#review-loading').evaluate(e=>e.open),true);
        await page.keyboard.press('Tab');assert(await page.locator('#loading-close').evaluate(e=>e===document.activeElement));
        if(grade==='A')await snap('loading-mid-progress');
        await page.clock.runFor(5100);await page.locator('.grade-box').waitFor();
        assert.equal(await page.locator('.grade-letter').textContent(),grade);assert.equal(await page.locator('#review-loading').evaluate(e=>e.open),false);
        assert(await page.locator('#report-title').evaluate(e=>e===document.activeElement));
        assert.equal(new URL(page.url()).searchParams.get('asin'),'B012345678');
        const amazon=await page.getByRole('link',{name:'Read the reviews on Amazon'}).getAttribute('href');
        assert.equal(new URL(amazon).searchParams.get('tag'),'wowpriced-20');
        assert.equal(await page.locator('#review-report a[href*="review-rank"]').count(),0);
        const text=await page.locator('#review-report').innerText();assert(!/\bstars?\b|\bFAIL\b|\bWARN\b|100 reviews|4\.5/.test(text));
        assert(text.includes('Price as of'));await page.locator('.price-note summary').first().click();
        // The Deal Spotter's shipping note under the price (Codex review 2026-10-23 #6).
        assert.equal(await page.locator('.current-price').textContent(),{A:'$24.99',B:'$24.99+ $25.15 shipping',C:'$24.99incl. shipping'}[grade]);
        // Photo, name and grade box each open the button's tagged Amazon page; Price details only toggles.
        assert(await page.locator('.price-note').first().evaluate(e=>e.open));
        assert.equal(await page.evaluate(()=>window.amazonClicks.length),0);assert.equal(new URL(page.url()).searchParams.get('asin'),'B012345678');
        assert.equal(await page.locator('.report-photo').getAttribute('aria-label'),'See Everyday wireless over-ear headphones with a comfortable fit on Amazon');
        assert.equal(await page.locator('#review-report .grade-letter').getAttribute('aria-label'),`Grade ${grade} — see it on Amazon`);
        // GA4 hears each shown grade once, through the shipped dispatch (tool_grade_event: true).
        assert.deepEqual(await page.evaluate(()=>window.tracked.splice(0)),[['tool_grade',{method:'review_'+grade.toLowerCase()}]]);
        assert.equal(await page.evaluate(()=>window.dataLayer.filter(a=>a[0]==='event'&&a[1]==='tool_grade').map(a=>a[2].method).pop()),'review_'+grade.toLowerCase());
        for(const selector of ['.report-photo img','.grade-box h2','.grade-box p','#report-title a','.report-actions .button']){
          // The first line box: a title that wraps has gaps inside its bounding box.
          const spot=page.locator(selector).first();await spot.scrollIntoViewIfNeeded();
          const box=await spot.evaluate(e=>{const r=e.getClientRects()[0];return {x:r.x+r.width/2,y:r.y+r.height/2};});
          await page.mouse.click(box.x,box.y);
          assert.deepEqual(await page.evaluate(()=>window.amazonClicks.pop()),{href:amazon,target:'_blank',rel:'sponsored noopener'},selector);
          assert.deepEqual(await page.evaluate(()=>window.tracked.splice(0)),[['deal_click',{store:'Amazon'}]],selector);
        }
        if(grade!=='C'){await page.locator('.checks-table summary').first().click();assert(await page.locator('.checks-table details').first().evaluate(e=>e.open));}
        await snap('report-'+grade);
        measurements.push({width,height,theme,state:grade,cls:await page.evaluate(()=>window.reviewCLS)});
        assert((await page.evaluate(()=>window.reviewCLS))<.01,`CLS ${width} ${theme}`);
        await page.getByRole('button',{name:'Check another product'}).click();
      }
      // Never finish before data arrives, even after the chosen duration.
      hold=true;await input.fill('B012345678');await page.locator('#review-form button').click();
      await page.locator('#review-loading[open]').waitFor();await page.clock.runFor(11000);
      assert.equal(await page.locator('#review-progress').getAttribute('aria-valuenow'),'99');
      await page.keyboard.press('Escape');assert.equal(await page.locator('#review-loading').evaluate(e=>e.open),false);
      if(pending)await pending.fulfill({json:response('A')}).catch(()=>{});
      await page.clock.runFor(1000);assert.equal(await page.locator('#review-report').isVisible(),false);
      hold=false;
      await page.locator('[data-check-asin]').first().click();
      await page.locator('#loading-product').filter({hasText:'headphones'}).waitFor();
      await page.keyboard.press('Escape');
      assert(await page.locator('[data-check-asin]').first().evaluate(e=>e===document.activeElement));
      // A shared link on a first visit: the notice waits for the check, then opens over the finished report.
      await page.evaluate(()=>localStorage.removeItem('wp.tools.cookies'));
      await page.goto('https://review.test/review-checker/?asin=B012345678');
      await page.locator('#review-loading[open]').waitFor();
      await page.locator('#loading-product').filter({hasText:'headphones'}).waitFor();
      await page.clock.runFor(2000);
      assert.equal(await page.locator('#review-loading').evaluate(e=>e.open),true);assert.equal(await terms.evaluate(e=>e.open),false);
      await page.clock.runFor(8100);assert.equal(await page.locator('.grade-letter').textContent(),'C');
      await page.clock.runFor(3000);assert.equal(await terms.evaluate(e=>e.open),true);
      assert(await page.locator('#review-report').isVisible());
      await page.locator('#cookies-ok').click();
      await page.getByRole('button',{name:'Check another product'}).click();
      // The second (hero) box runs the same check, and errors stay on the box that was used.
      const heroInput=page.locator('#hero-input');
      await heroInput.fill('https://example.test/not-a-product');await page.locator('#hero-form button').click();
      assert.equal(await heroInput.getAttribute('aria-invalid'),'true');assert(await heroInput.evaluate(e=>e===document.activeElement));
      assert.match(await page.locator('#input-message').textContent(),/Paste an Amazon\.com product link, or type a product name/);
      // A typed name spends no lookup: one tagged Amazon highly rated search in a new tab (operator 2026-10-22).
      const searches=requests.filter(u=>u.endsWith('/api/search')).length;
      assert.deepEqual(await page.evaluate(()=>window.tracked.splice(0)),[['tool_grade',{method:'review_c'}]]);  // the shared link's report
      await heroInput.fill('wireless  headphones');await page.locator('#hero-form button').click();
      const named=page.locator('#name-results .name-button');
      assert.equal(await named.getAttribute('href'),'https://www.amazon.com/s?k=wireless%20headphones&s=review-rank&tag=wowpriced-20');
      assert.equal(await named.getAttribute('target'),'_blank');assert.equal(await named.getAttribute('rel'),'sponsored noopener');
      assert.equal((await named.innerText()).trim(),'See highly rated wireless headphones on Amazon →');
      assert(await named.evaluate(e=>e===document.activeElement));assert.equal(await heroInput.getAttribute('aria-invalid'),null);
      assert.equal(await page.locator('#name-results .name-hint').textContent(),'For an A, B or C grade, paste the product’s Amazon link.');
      // A typed name is not a grade; its Amazon button is one deal_click and still opens.
      assert.deepEqual(await page.evaluate(()=>window.tracked),[]);await named.click();
      assert.deepEqual(await page.evaluate(()=>window.tracked.splice(0)),[['deal_click',{store:'Amazon'}]]);
      assert.equal((await page.evaluate(()=>window.amazonClicks.pop())).href,await named.getAttribute('href'));
      assert.equal(await page.locator('#input-message').textContent(),'');
      assert(!/★|☆|out of 5|\b\d[\d,]*\s+(?:ratings|reviews)\b/i.test(await page.locator('body').innerText()));
      await snap('name');
      // The adult filter applies: no button, only the paste-a-link hint.
      await input.fill('vibrator');await page.locator('#review-form button').click();
      assert.equal(await page.locator('#name-results').isVisible(),false);
      assert.equal(await page.locator('#input-message').textContent(),'For an A, B or C grade, paste the product’s Amazon link.');
      assert.equal(requests.filter(u=>u.endsWith('/api/search')).length,searches);
      grade='B';await heroInput.fill('https://www.amazon.com/dp/B012345678');await page.locator('#hero-form button').click();
      await page.locator('#loading-product').filter({hasText:'headphones'}).waitFor();await page.clock.runFor(10100);
      assert.equal(await page.locator('.grade-letter').textContent(),'B');assert.equal(await heroInput.getAttribute('aria-invalid'),null);
      await page.getByRole('button',{name:'Check another product'}).click();
      assert.equal(await page.locator('#name-results').isVisible(),false);
      await page.emulateMedia({reducedMotion:'reduce'});
      assert.equal(await page.locator('.tick').first().evaluate(e=>getComputedStyle(e).animationName),'none');
      await page.locator('#footer-email').fill('reader@example.test');await page.locator('[data-join-footer] button').click();
      await page.locator('#footer-signup-success').waitFor();
      await context.close();
    }
    assert.deepEqual(errors,[]);assert(!requests.some(u=>/^https:\/\/(?:www\.)?amazon\.com/.test(u)));
    fs.writeFileSync(path.join(shots,'measurements.json'),JSON.stringify(measurements,null,2));
    console.log('Chromium: all four sizes, both themes, all states; screenshots: '+shots);
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
