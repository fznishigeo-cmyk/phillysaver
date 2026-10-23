// Real Chromium, entirely offline: "Add to Amazon cart" on a deal page and the
// Fake Deal Spotter result, switch ON vs OFF. Usage: node add_to_cart_browser.cjs <build dir> [<screenshot dir>]
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');
const scratch = process.argv[2], shots = process.argv[3] || scratch;
fs.mkdirSync(scratch,{recursive:true}); fs.mkdirSync(shots,{recursive:true});
const VARIANT = 'B0D8KZHN1Q';
const BUILD = `
import json, sys
from pathlib import Path
from unittest.mock import patch
from tests.test_support import render, NOW
from tests.test_publish_site import make_deal
from dealradar.config import load_config
from dealradar.publish import site
def row(i, asin, **extra):
    return make_deal(dedupe_key='cart|'+str(i), asin=asin, item='Everyday wireless headphones '+str(i), store='Amazon',
        codes=[], stack_steps=[], conditions=[], canonical_url='https://www.amazon.com/dp/'+asin,
        image_url='https://m.media-amazon.com/images/I/fixture.jpg', final_price=20+i, regular_price=50,
        pct_at_checkout=60, abs_savings=30-i, regular_price_basis='List price', verify_tier='verified',
        price_rechecked_at=NOW.isoformat(), critic={'plausible':True,'confidence':.99,'reason':'Fixture'}, **extra)
# One size/colour child (opens with th=1&psc=1), as on the live site.
rows = [row(i, 'B0'+str(i).zfill(8)) for i in range(3)] + [row(3, '${VARIANT}',
    click_price_check={'asin':'${VARIANT}','final':23,'at':NOW.isoformat(),'buybox':23,'amazon':None,'new':23,'variation_child':True})]
paths = {}
for mode in ('on', 'off'):
    cfg = load_config(); cfg.add_to_cart = {'enabled': mode == 'on'}
    (Path(sys.argv[1]) / mode).mkdir(parents=True, exist_ok=True)
    with patch.object(site, 'util_now', lambda: NOW):
        out, doc, *_ = render(Path(sys.argv[1]) / mode, cfg, rows)
    paths = {d['url']: d['deal_path'] for d in doc['deals'] if d.get('deal_path')}
print(json.dumps(paths))
`;
const NOW = new Date('2026-10-22T01:00:00Z');
const REL = 'sponsored nofollow noopener';
const cartURL = asin => `https://www.amazon.com/gp/aws/cart/add.html?ASIN.1=${asin}&Quantity.1=1&AssociateTag=wowpriced-20`;
function contrast(page, selector) {
  return page.locator(selector).evaluate(el => {
    const rgb = color => (color.match(/[\d.]+/g) || []).map(Number);
    let bg = [255,255,255];
    for (let node = el; node; node = node.parentElement) {
      const value = rgb(getComputedStyle(node).backgroundColor);
      if (value.length === 3 || value[3] === 1) { bg = value.slice(0,3); break; }
    }
    const lum = c => c.reduce((sum,v,i) => { v /= 255; return sum + [.2126,.7152,.0722][i]*(v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4); }, 0);
    const a = lum(rgb(getComputedStyle(el).color).slice(0,3)), b = lum(bg);
    return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
  });
}
async function box(page, selector) {
  return page.locator(selector).evaluate(el => { const b = el.getBoundingClientRect();
    return {top:b.top+scrollY, bottom:b.bottom+scrollY, left:b.left, right:b.right, width:b.width, height:b.height}; });
}
(async () => {
  let browser;
  try { browser = await chromium.launch({executablePath:'/opt/pw-browsers/chromium',headless:true}); }
  catch (error) { console.log('Chromium unavailable: ' + error.message.split('\n')[0]); process.exitCode=77; return; }
  try {
    const built = spawnSync('.venv/bin/python',['-c',BUILD,scratch],{encoding:'utf8'});
    assert.equal(built.status,0,built.stderr);
    const paths = JSON.parse(built.stdout.trim().split('\n').pop());
    const variantURL = Object.keys(paths).find(url => url.includes(VARIANT));
    assert.equal(variantURL, `https://www.amazon.com/dp/${VARIANT}?th=1&psc=1&tag=wowpriced-20`);
    for (const [width,height] of [[390,844],[1280,800]]) {
      for (const colorScheme of ['light','dark']) {
        const positions = {};
        for (const mode of ['off','on']) {
          const root = path.join(scratch,mode,'site');
          const context = await browser.newContext({viewport:{width,height},colorScheme});
          const errors = [];
          await context.addInitScript(() => {
            Math.random = () => 0;
            // Amazon clicks are recorded, never followed (the run stays offline).
            window.amazonClicks = [];
            document.addEventListener('click', e => { const a = e.target.closest && e.target.closest('a[href^="https://www.amazon.com/"]');
              if (a) { window.amazonClicks.push({href:a.href,target:a.target,rel:a.rel}); e.preventDefault(); } }, true);
            try { localStorage.setItem('wp.spotter.cookies','1'); } catch (_) {}
          });
          await context.route('**/*', async route => {
            const url = new URL(route.request().url());
            if (url.hostname === 'm.media-amazon.com') return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300" viewBox="0 0 300 300"><rect width="300" height="300" rx="20" fill="#f3f7ff"/><path d="M70 170v-40a80 80 0 0 1 160 0v40" stroke="#345d94" stroke-width="18" fill="none"/><rect x="56" y="135" width="40" height="95" rx="20" fill="#172b4e"/><rect x="204" y="135" width="40" height="95" rx="20" fill="#172b4e"/></svg>'});
            if (url.origin !== 'https://wowpriced.test') return route.fulfill({status:204,body:''});
            if (url.pathname === '/api/search') return route.fulfill({contentType:'application/json',body:JSON.stringify({items:[{asin:VARIANT,
              title:'Wireless over-ear headphones with a comfortable folding design',image:'https://m.media-amazon.com/images/I/fixture.jpg',
              price_cents:3999,list_price_cents:5999,monthlySold:1000,as_of:new Date(NOW.getTime()-4*60000).toISOString()}],
              deal_grade:{grade:'A',label:'Great price',facts:['Current Amazon price checked','Verified deal on wowpriced']}})});
            let file = path.join(root,url.pathname);
            if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file,'index.html');
            if (!fs.existsSync(file)) return route.fulfill({status:404,body:''});
            return route.fulfill({contentType:{'.html':'text/html','.js':'application/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml'}[path.extname(file)]||'application/octet-stream',body:fs.readFileSync(file)});
          });
          const page = await context.newPage(); page.on('pageerror', e => errors.push(e.message));
          await page.clock.install({time:NOW});
          const label = `${width}-${colorScheme}-${mode}`;
          // Deal page: the size/colour child's own ASIN, under/next to Get deal.
          await page.goto('https://wowpriced.test' + paths[variantURL]);
          const deal = await box(page,'.deal-detail>.getdeal');
          assert.equal(await page.locator('a[href*="/gp/aws/cart/add.html"]').count(), mode === 'on' ? 1 : 0, label);
          assert.equal(await page.locator('form[action*="amazon."]').count(), 0, label);  // a link, never a form
          if (mode === 'on') {
            const cart = page.locator('.deal-detail>.addcart');
            assert.equal(await cart.getAttribute('href'), cartURL(VARIANT));
            assert.equal(await cart.getAttribute('target'), '_blank'); assert.equal(await cart.getAttribute('rel'), REL);
            assert.equal((await cart.innerText()).trim(), 'Add to Amazon cart');
            assert(await cart.evaluate(el => el.previousElementSibling.classList.contains('getdeal')), label);
            const c = await box(page,'.deal-detail>.addcart');
            assert(c.height >= 44 && c.width >= 44, label + ' tap target ' + JSON.stringify(c));
            if (width < 641) assert(c.top >= deal.bottom && c.top - deal.bottom <= 16, label + ' below Get deal');
            else assert(Math.abs(c.top - deal.top) <= 4 && c.left > deal.right, label + ' beside Get deal');
            assert(await contrast(page,'.deal-detail>.addcart') >= 4.5, label + ' contrast');
            if (colorScheme === 'light') {
              await cart.evaluate(el => el.scrollIntoView({block:'center',behavior:'instant'}));
              await page.mouse.move(width - 2, 1); await page.screenshot({path:path.join(shots,`add-to-cart-deal-${width}.png`),animations:'disabled'});
            }
            // The same deal_click as Get deal, told apart by button: "add_to_cart".
            await cart.click(); await page.locator('.deal-detail>.getdeal').click();
            assert.deepEqual(await page.evaluate(() => window.amazonClicks.map(c => c.href)), [cartURL(VARIANT), `https://www.amazon.com/dp/${VARIANT}?th=1&psc=1&tag=wowpriced-20`]);
            const clicks = await page.evaluate(() => window.dataLayer.filter(a => a[0] === 'event' && a[1] === 'deal_click').map(a => a[2]));
            assert.equal(clicks.length, 2, label); assert.equal(clicks[0].button, 'add_to_cart'); assert(!('button' in clicks[1]), label);
            assert.equal(clicks[0].store, clicks[1].store); assert.equal(clicks[0].method, 'deal');
          }
          assert((await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)) <= 0, label + ' horizontal scroll');
          // Fake Deal Spotter: a typed name never shows it; a graded link does.
          await page.goto('https://wowpriced.test/deal-spotter/');
          await page.locator('#q').fill('wireless headphones'); await page.locator('.hero-box button').click();
          await page.clock.runFor(10100);  // tool_words_analyzer: the link's wait first
          assert(await page.locator('#name-results').isVisible());
          assert.equal(await page.locator('a[href*="/gp/aws/cart/add.html"]:visible').count(), 0, label + ' typed name');
          await page.locator('#q').fill(`https://www.amazon.com/dp/${VARIANT}`);
          await Promise.all([page.waitForResponse(r => r.url().includes('/api/search')), page.locator('.hero-box button').click()]);
          await page.clock.runFor(10100);
          assert.equal(await page.locator('#grade-letter').textContent(), 'A');
          const buy = await box(page,'#result-buy');
          positions[mode] = {deal, buy};
          assert.equal(await page.locator('#result-cart').count(), mode === 'on' ? 1 : 0, label);
          if (mode === 'on') {
            const cart = page.locator('#result-cart');
            assert.equal(await cart.getAttribute('href'), cartURL(VARIANT));
            assert.equal(await cart.getAttribute('target'), '_blank'); assert.equal(await cart.getAttribute('rel'), REL);
            assert(await cart.evaluate(el => el.previousElementSibling.id === 'result-buy' && el.classList.contains('secondary')));
            const c = await box(page,'#result-cart');
            assert(c.height >= 44 && c.width >= 44 && c.top >= buy.bottom && c.top - buy.bottom <= 16, label + ' ' + JSON.stringify(c));
            assert(await contrast(page,'#result-cart') >= 4.5, label + ' contrast');
            if (colorScheme === 'light') {
              if (width < 641) await cart.evaluate(el => el.scrollIntoView({block:'center',behavior:'instant'}));
              else await page.evaluate(() => scrollTo({top:0,behavior:'instant'}));
              await page.mouse.move(width - 2, 1); await page.screenshot({path:path.join(shots,`add-to-cart-spotter-${width}.png`),animations:'disabled'});
            }
            const before = await page.evaluate(() => (window.dataLayer || []).length);
            await cart.click();
            assert.deepEqual(await page.evaluate(() => window.amazonClicks.pop()), {href:cartURL(VARIANT),target:'_blank',rel:REL});
            // Tool-page events: every Amazon link in the Spotter's tool area, this one too, sends one deal_click.
            assert.equal(await page.evaluate(() => (window.dataLayer || []).length), before + 1);
          }
          assert((await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)) <= 0, label + ' horizontal scroll');
          assert.deepEqual(errors, [], label); await context.close();
        }
        // The main Amazon buttons stay exactly where they were (nothing pushed down or aside).
        assert.deepEqual(positions.on, positions.off, `${width}-${colorScheme} main button moved`);
      }
    }
    console.log('Add to Amazon cart: deal page and Fake Deal Spotter, 390/1280, light/dark, ON/OFF checks passed; screenshots: ' + shots);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
