// Offline fixture QA: node tests/static_photos_browser.cjs <published-site>.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  let chromium;
  try { ({chromium} = require(require.resolve('playwright', {paths:['/opt/node-tools', __dirname]}))); }
  catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND') throw error;
    console.log('Chromium unavailable: Playwright missing (exit 77).'); process.exitCode=77; return;
  }
  let browser;
  try {
    browser = await chromium.launch({headless:true, executablePath:'/opt/pw-browsers/chromium',
      args:['--no-sandbox', '--disable-dev-shm-usage']});
  } catch (error) {
    if (!/Operation not permitted|Executable doesn't exist|Target page, context or browser has been closed/.test(error.message)) throw error;
    console.log('Chromium cannot launch (exit 77): ' + error.message.split('\n')[0]);
    process.exitCode=77; return;
  }
  const root = path.resolve(process.argv[2]);
  const payload = JSON.parse(fs.readFileSync(path.join(root,'deals.json'), 'utf8'));
  const deal = payload.deals.find(d => d.image_url?.startsWith('https://'));
  assert(deal?.deal_path, 'fixture needs a photographed deal');
  try {
    for (const [width,height] of [[390,844],[1280,800]]) {
      for (const colorScheme of ['light','dark']) {
        const context = await browser.newContext({viewport:{width,height}, colorScheme,
          isMobile:width===390, hasTouch:width===390});
        const outside = [], errors = [];
        await context.route('**/*', async route => {
          const request = route.request(), url = new URL(request.url());
          if (request.resourceType() === 'image') {
            // Deliberate delay exposes any size reservation failure on load.
            await new Promise(resolve=>setTimeout(resolve,80));
            return route.fulfill({contentType:'image/svg+xml', body:
              '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400"><rect width="400" height="400" fill="white"/><rect x="100" y="60" width="200" height="280" rx="30" fill="teal"/></svg>'});
          }
          if (url.origin !== 'https://wowpriced.test' || request.method() !== 'GET') {
            outside.push(url.origin); return route.abort();
          }
          let file = path.resolve(root,'.'+decodeURIComponent(url.pathname));
          if (!file.startsWith(root+path.sep) && file !== root) return route.abort();
          if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file,'index.html');
          assert(fs.existsSync(file), 'missing fixture: '+url.pathname);
          const contentType = {'.html':'text/html', '.json':'application/json', '.js':'application/javascript',
            '.css':'text/css', '.xml':'application/xml'}[path.extname(file)] || 'application/octet-stream';
          return route.fulfill({contentType, body:fs.readFileSync(file)});
        });
        const page = await context.newPage();
        page.on('pageerror', error=>errors.push(error.message));
        page.on('console', message=>{if (message.type()==='error') errors.push(message.text());});
        await page.addInitScript(() => {
          window.task274CLS = 0;
          new PerformanceObserver(list => {
            for (const entry of list.getEntries()) if (!entry.hadRecentInput) window.task274CLS += entry.value;
          }).observe({type:'layout-shift', buffered:true});
        });
        for (const pathname of ['/deals/home-kitchen/', deal.deal_path]) {
          await page.goto('https://wowpriced.test'+pathname,{waitUntil:'load'});
          await page.waitForFunction(() => Array.from(document.querySelectorAll('.media img'))
            .filter(image=>image.loading !== 'lazy').every(image=>image.complete && image.naturalWidth>0));
          await page.waitForTimeout(200);
          const measure = await page.evaluate(() => {
            const hero = document.querySelector('.detail-photo'), related = document.querySelector('.related-grid');
            return {cls:window.task274CLS, overflow:document.documentElement.scrollWidth>innerWidth,
              photos:Array.from(document.querySelectorAll('.media img')).map(image => ({
                width:image.getBoundingClientRect().width,height:image.getBoundingClientRect().height,
                fit:getComputedStyle(image).objectFit})),
              hero:hero && {height:hero.getBoundingClientRect().height, background:getComputedStyle(hero).backgroundColor},
              related:related && {columns:getComputedStyle(related).gridTemplateColumns.split(' ').length,
                count:related.querySelectorAll('.card').length,
                clamp:getComputedStyle(related.querySelector('.title')).webkitLineClamp}};
          });
          assert.equal(measure.cls,0, `${width} ${colorScheme} ${pathname}: CLS`);
          assert.equal(measure.overflow,false, `${width} ${pathname}: overflow`);
          assert(measure.photos.length >= (pathname === deal.deal_path ? 2 : 4));
          assert(measure.photos.every(image=>image.width>0 && image.height>0 && image.fit==='contain'));
          if (pathname === deal.deal_path) {
            assert(measure.hero.height<=320);
            assert.equal(measure.hero.background,colorScheme==='dark' ? 'rgb(217, 222, 228)' : 'rgb(255, 255, 255)');
            assert(measure.related.count<=4);
            assert.equal(measure.related.clamp,'2');
            assert.equal(measure.related.columns,width===390 ? 2 : 3);
          }
          console.log(JSON.stringify({width,height,colorScheme,pathname,cls:measure.cls,photos:measure.photos.length}));
        }
        assert.deepEqual(errors,[]);
        assert.deepEqual(outside,[]);
        await context.close();
      }
    }
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
