// Native Chromium only. All navigation, API and image traffic is fulfilled offline.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
function buildFixture(scratch, glow, lazy) {
  fs.mkdirSync(scratch, {recursive:true});
  const result = spawnSync('.venv/bin/python', ['-c', `
import sys
from pathlib import Path
from dealradar.config import load_config
from dealradar.publish import site
from tests.test_support import NOW, render
from tests.test_publish_site import make_deal
site.util_now = lambda: NOW
cfg = load_config()
cfg.search_box_glow = sys.argv[2] == "true"
cfg.search_analyze["lazy"] = sys.argv[3] == "true"
cfg.tracking = {}
cfg.max_photoless_h = None
cfg.non_amazon_freshness_enabled = False
cfg.keepa_click_price_rule = "off"
cfg.affiliate_amazon_tag = "example-20"
deals = [make_deal(dedupe_key=f"task301-{i}", item=f"Wireless mouse {i}", store="Amazon", category="electronics", codes=[], conditions=[],
    canonical_url=f"https://www.amazon.com/dp/B0{i:08d}", asin=f"B0{i:08d}", price_rechecked_at=NOW.isoformat(),
    image_url="https://m.media-amazon.com/images/I/mouse.jpg") for i in range(1, 9)]
out, doc, *_ = render(Path(sys.argv[1]), cfg, deals)
from dealradar.publish.drop_fresh import rotation
assert len(rotation(doc['deals'], cfg.front_page)) == 8, 'all eight mice must be eligible for fresh Trending'
`, scratch, String(glow), String(lazy)], {encoding:'utf8'});
  assert.equal(result.status, 0, result.stderr);
  return path.join(scratch, 'site');
}
async function assertAnalyzeGeometry(page) {
  const g = await page.locator('.searchfield').evaluate(field => {
    const button = field.querySelector('#analyze-search'), input = field.querySelector('#q');
    const clear = field.querySelector('#clearsearch');
    const f = field.getBoundingClientRect(), b = button.getBoundingClientRect(), q = input.getBoundingClientRect();
    const css = getComputedStyle(button), ring = getComputedStyle(field, '::before');
    return {gaps:[b.top - f.top, f.bottom - b.bottom, f.right - b.right],
      edge:parseFloat(ring.borderTopWidth) || 0,
      borders:[css.borderTopWidth, css.borderBottomWidth, css.borderRightWidth, css.borderLeftWidth],
      radii:[css.borderTopRightRadius, css.borderBottomRightRadius],
      width:b.width, height:b.height, label:button.innerText,
      textRight:q.right - parseFloat(getComputedStyle(input).paddingRight), buttonLeft:b.left,
      clear:clear && !clear.hidden ? {left:clear.getBoundingClientRect().left, right:clear.getBoundingClientRect().right} : null,
      overflow:document.documentElement.scrollWidth > innerWidth};
  });
  assert.deepEqual(g.borders, ['0px', '0px', '0px', '0px'], 'no transparent border ring');
  // The rainbow occupies the outer 3px; the button meets or slightly overlaps its inner edge.
  for (const gap of g.gaps) {
    assert(gap >= 0 && gap <= 3, 'button stays inside the field and flush with its edge');
    if (g.edge) assert(gap - g.edge <= 2, 'at most a hairline of field background');
  }
  assert.deepEqual(g.radii, ['10px', '10px'], 'right corners follow the 12px field');
  assert(g.width >= 44 && g.height >= 44, '44px tap target');
  assert.equal(g.label, 'Analyze', 'full label on phones too');
  assert(g.textRight <= g.buttonLeft, 'input text clears the pill');
  if (g.clear) {
    assert(g.clear.right <= g.buttonLeft, 'clear button clears the pill');
    assert(g.textRight <= g.clear.left, 'input text clears the clear button');
  }
  assert.equal(g.overflow, false, 'no sideways overflow');
}
async function main() {
  const {chromium} = require('playwright');
  const scratch = fs.mkdtempSync('/tmp/task301-browser-');
  const shots = path.resolve(process.argv[3] || 'docs/research/search-box');
  fs.mkdirSync(shots, {recursive:true});
  let browser;
  try {
    const roots = {};
    for (const lazy of [false, true]) for (const glow of [false, true])
      roots[`${lazy}-${glow}`] = buildFixture(path.join(scratch, `${lazy}-${glow}`), glow, lazy);
    browser = await chromium.launch({headless:true, executablePath:process.argv[2]});
    const metrics = [];
    for (const lazy of [false, true]) for (const width of [360, 390, 1440]) for (const theme of ['light', 'dark'])
      for (const motion of ['no-preference', 'reduce']) {
        let before;
        const name = `${lazy ? 'lazy' : 'inline'}-${width}-${theme}-${motion}`;
        for (const glow of [false, true]) {
          const context = await browser.newContext({viewport:{width, height:844}, colorScheme:theme,
            reducedMotion:motion, serviceWorkers:'block'});
          const errors = [], unexpected = [];
          await context.route('**/*', route => {
            const url = new URL(route.request().url());
            if (url.hostname === 'm.media-amazon.com') return route.fulfill({contentType:'image/svg+xml',
              body:'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320"><rect width="320" height="320" fill="#b9c6cd"/></svg>'});
            if (url.origin !== 'https://wowpriced.test') {unexpected.push(url.origin); return route.fulfill({status:204, body:''});}
            if (url.pathname === '/api/search') return route.fulfill({json:url.searchParams.get('q') === 'unavailable'
              ? {items:[], reason:'budget_exhausted'} : {items:[{asin:'B000000901', title:'Wireless mouse from Amazon',
                price_cents:1299, as_of:new Date().toISOString()}]}});
            const root = roots[`${lazy}-${glow}`], file = path.resolve(root, '.' + url.pathname, url.pathname === '/' ? 'index.html' : '');
            if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) return route.fulfill({status:204, body:''});
            return route.fulfill({path:file});
          });
          await context.addInitScript(() => {localStorage.setItem('wp.analyzeHint', '1'); window.opened = [];
            window.open = url => window.opened.push(url);});
          const page = await context.newPage();
          page.on('pageerror', error => errors.push(error.message));
          await page.goto('https://wowpriced.test/');
          await page.locator('#content .card').first().waitFor();
          await assertAnalyzeGeometry(page);
          const geometry = await page.evaluate(() => {
            const rect = selector => {const r = document.querySelector(selector).getBoundingClientRect();
              return {x:r.x, y:r.y, width:r.width, height:r.height};};
            return {headerH:Math.round(document.querySelector('header').getBoundingClientRect().height),
              firstCardTop:Math.round(document.querySelector('.grid .card, .grid article').getBoundingClientRect().top + scrollY),
              field:rect('.searchfield'), input:rect('#q'), button:rect('#analyze-search'),
              color:getComputedStyle(document.querySelector('#q')).color};
          });
          if (!glow) before = geometry;
          else {
            assert.deepEqual(geometry, before, name + ': unchanged geometry and text contrast');
            metrics.push({name, before, after:geometry});
            const style = () => page.locator('.searchfield').evaluate(el => {
              const css = getComputedStyle(el, '::before'); return {name:css.animationName, state:css.animationPlayState,
                duration:css.animationDuration, background:css.backgroundImage, border:css.borderTopWidth};});
            let css = await style();
            assert.equal(css.name, motion === 'reduce' ? 'none' : 'wpspin');
            assert.equal(css.border, '3px'); assert(css.background.includes('conic-gradient'));
            if (motion !== 'reduce') {
              assert.equal(css.duration, '6s'); assert.equal(css.state, 'running');
              const angle = () => page.locator('.searchfield').evaluate(el => getComputedStyle(el, '::before').getPropertyValue('--wpspin'));
              const first = await angle(); await page.waitForTimeout(150); assert.notEqual(await angle(), first);
            }
            await page.screenshot({path:path.join(shots, name + '-idle.png')});
            const button = page.locator('#analyze-search'), bounds = await button.boundingBox();
            await button.hover();
            await button.focus();
            await page.mouse.down();
            try {
              assert.deepEqual(await button.boundingBox(), bounds, 'hover/focus/active do not shift the pill');
              assert.equal(await button.evaluate(el => getComputedStyle(el).animationName),
                motion === 'reduce' ? 'none' : 'wpcharge');
              await assertAnalyzeGeometry(page);
            } finally {
              await page.mouse.move(0, 0);
              await page.mouse.up();
            }
            await page.locator('#q').focus();
            if (motion !== 'reduce') assert.equal((await style()).state, 'paused');
            await page.locator('#q').fill('mouse'); await page.locator('#q').blur();
            await assertAnalyzeGeometry(page);
            if (motion !== 'reduce') assert.equal((await style()).state, 'paused');
            await page.locator('#search-amazon-result a.getdeal').waitFor();
            const checkTop = async analyze => {
              assert.equal(await page.locator('#search-amazon-result:visible, #search-result-top:visible').count(), 1);
              assert.equal(await page.locator('a.getdeal[data-search-amazon]:visible, a.getdeal[data-analyze-fallback]:visible').count(), 1,
                'one big Amazon button');
              const top = page.locator(analyze ? '#search-result-top' : '#search-amazon-result');
              if (analyze) assert(await top.evaluate(el => /^H[12]$/.test(el.previousElementSibling.tagName)));
              assert.equal(await page.locator('#search-more .getdeal[data-search-amazon]').count(), 0);
              assert(await top.locator('a').getAttribute('href').then(href => href.includes('tag=example-20')));
              assert((await top.locator('a').getAttribute('rel')).includes('sponsored'));
              const card = page.locator('#content .card').first();
              if (await card.count()) assert((await top.boundingBox()).y < (await card.boundingBox()).y);
              assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            };
            await checkTop();
            await page.screenshot({path:path.join(shots, name + '-search.png')});
            for (const query of ['mouse', 'https://amazon.com/dp/B000000901?tag=PRIVATE', 'unavailable']) {
              await page.locator('#q').fill(query); await page.locator('#analyze-search').click();
              await page.locator('.search-analysis[aria-busy="false"]').waitFor();
              await checkTop(true);
              await page.locator('#search-result-top a').click();
              const url = new URL(await page.evaluate(() => window.opened.at(-1)));
              assert.equal(url.searchParams.get('tag'), 'example-20');
              assert.equal(url.searchParams.get('k'), query.startsWith('https:') ? 'B000000901' : query);
              assert(!url.href.includes('PRIVATE'));
              await page.screenshot({path:path.join(shots, name + '-analyze-' + (query.startsWith('https:') ? 'asin' : query) + '.png')});
            }
            if (motion === 'reduce') assert.equal(await page.locator('#analyze-search').evaluate(el =>
              getComputedStyle(el, '::before').animationName), 'none');
          }
          assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
          await context.close();
        }
      }
    fs.writeFileSync(path.join(shots, 'metrics.json'), JSON.stringify(metrics, null, 2) + '\n');
    console.log('Search box native checks passed: geometry, motion, early buttons, themes and screenshots');
  } finally {if (browser) await browser.close(); fs.rmSync(scratch, {recursive:true, force:true});}
}
module.exports = {buildFixture, assertAnalyzeGeometry};
if (require.main === module) main().catch(error => {console.error(error); process.exitCode = 1;});
