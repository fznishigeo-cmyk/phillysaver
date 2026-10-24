// Real Chromium, offline: /best-deals-today/ wears the home header (best_today_header, operator
// Nishi 2026-10-24 ~02:00Z). node tests/best_today_header_browser.cjs <scratch> [<shots dir>]
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');

// on: the shipped config; off: the same with the switch off.
const BUILD = `
import sys
from pathlib import Path
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.publish import site
from tests.test_publish_site import make_deal
from tests.test_support import NOW
site.util_now=lambda:NOW
assert load_config().best_today_header is True
items=[("kitchen","Stainless steel electric kettle 1.7L"),("beauty","Vitamin C face serum 1 oz"),
       ("electronics","Wireless earbuds with charging case"),("home","Cotton bath towel set of 6"),
       ("sports","Non-slip yoga mat 6mm"),("toys_games","Building blocks set 500 pieces")]
rows=[make_deal(dedupe_key=f"best-header-{i}", item=f"{items[i%6][1]}, model {i}", store="Amazon",
    category=items[i%6][0], verify_tier="verified", codes=[], conditions=["new"],
    final_price=19.99+i, regular_price=49.99, canonical_url=f"https://amazon.com/dp/B{i:09d}", asin=f"B{i:09d}",
    regular_price_basis="List price on Amazon", basis="amazon_list_price",
    keepa_list_price={"asin":f"B{i:09d}","price":49.99,"observed_at":NOW.isoformat()},
    first_seen_at=NOW.isoformat(), price_rechecked_at=NOW.isoformat(),
    click_price_check={"asin":f"B{i:09d}","final":19.99+i,"buybox":19.99+i,"at":NOW.isoformat()},
    critic={"plausible":True,"confidence":.9,"reason":"Backed basis"},
    image_url="https://m.media-amazon.com/images/I/photo.jpg") for i in range(24)]
for name,on in (("on",True),("off",False)):
    cfg=load_config(); cfg.tracking={}; cfg.best_today_header=on
    db=DB(":memory:")
    try: site.render_site(db,cfg,NOW,out_dir=Path(sys.argv[1])/name,deals=rows)
    finally: db.close()
`;

// Every rendered element under root, in order: its computed style and box. The tab row is
// read on its own (its current pill differs by design). In the logo the inherited weight
// differs (an h1 on the home, a paragraph here; every text in it sets its own). Below the
// header (.wrap, which itself now contains its title's margin), boxes are measured from the
// title: the resolved offsets of absolutely placed elements and the home-only variables the
// page now inherits (no rule there reads them) are left out.
function dump(selector) {
  const root = document.querySelector(selector), content = selector !== 'header';
  const origin = content ? document.querySelector('.wrap h1').getBoundingClientRect() : {left: 0, top: 0};
  const skip = k => (content && (k.startsWith('--') || /^(top|right|bottom|left|inset-)/.test(k)));
  const out = [];
  (function walk(el, at) {
    if (!el.checkVisibility() || el.id === 'cattabs') return;
    if (!content || el !== root) {
      const b = el.getBoundingClientRect(), props = {};
      for (const pseudo of ['', '::before', '::after']) {
        const style = getComputedStyle(el, pseudo || null);
        if (pseudo && style.content === 'none') continue;
        for (const k of style) if (!skip(k) && !(k === 'font-weight' && el.closest('.brand'))) props[pseudo + k] = style.getPropertyValue(k);
      }
      out.push({at, el: (el.matches('.brand') ? 'logo' : el.tagName) + (el.id ? '#' + el.id : ''), props,
        box: [b.left - origin.left, b.top - origin.top, b.width, b.height].map(Math.round).join()});
    }
    [...el.children].forEach((c, i) => walk(c, at + '/' + i));
  })(root, '');
  return out;
}

function tabs() {
  const nav = document.getElementById('cattabs'), box = nav.getBoundingClientRect();
  const props = ['background-color', 'color', 'border-top-color', 'border-top-width', 'border-top-left-radius',
    'padding-left', 'font-size', 'font-weight', 'height', 'min-height'];
  const look = el => el && Object.fromEntries(props.map(p => [p, getComputedStyle(el).getPropertyValue(p)]));
  const current = nav.querySelector('.active');
  return {labels: [...nav.children].map(a => a.textContent), box: [box.top, box.height].map(Math.round).join(),
    current: current.textContent, currentLook: look(current), plainLook: look(nav.querySelector('[data-i="1"]')),
    aria: current.getAttribute('aria-current'), href: current.getAttribute('href'),
    lines: new Set([...nav.children].map(a => Math.round(a.getBoundingClientRect().top))).size};
}

function same(name, a, b) {
  assert.equal(a.length, b.length, name + ': element count');
  a.forEach((x, i) => {
    const y = b[i], keys = Object.keys(x.props).filter(k => x.props[k] !== y.props[k]);
    assert.equal(y.el, x.el, name + ' ' + x.at);
    assert.deepEqual(keys.map(k => [k, y.props[k]]), keys.map(k => [k, x.props[k]]), name + ' ' + x.at + ' ' + x.el);
    assert.equal(y.box, x.box, name + ' ' + x.at + ' ' + x.el + ' box');
  });
}

(async () => {
  const scratch = fs.mkdtempSync(path.join(process.argv[2], 'best-header-'));
  const shots = process.argv[3];
  const built = spawnSync('.venv/bin/python', ['-c', BUILD, scratch], {encoding:'utf8'});
  assert.equal(built.status, 0, built.stderr);
  let browser;
  try {
    browser = await chromium.launch({headless:true, executablePath:'/opt/pw-browsers/chromium'});
  } catch (e) {
    console.log('Chromium cannot launch: ' + e.message.split('\n')[0]);
    process.exitCode = 77; return;
  }
  try {
    async function open(variant, pathname, viewport, colorScheme) {
      const root = path.join(scratch, variant), phone = viewport.width < 900;
      const context = await browser.newContext({viewport, colorScheme, isMobile: phone, hasTouch: phone,
        reducedMotion: 'reduce', serviceWorkers: 'block', deviceScaleFactor: shots ? 2 : 1});
      // The site's events, kept across the search's page change.
      await context.addInitScript(() => {
        const seen = JSON.parse(sessionStorage.getItem('events') || '[]');
        window.wpTrack = (name, params) => { seen.push([location.pathname, name, params]); sessionStorage.setItem('events', JSON.stringify(seen)); };
      });
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://wowpriced.test') {
          if (route.request().resourceType() === 'image') return route.fulfill({contentType: 'image/svg+xml', body:
            '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"><rect width="300" height="300" fill="#ddd"/></svg>'});
          return route.fulfill({status:204, body:''});
        }
        let file = path.join(root, url.pathname);
        if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
        if (!fs.existsSync(file)) return route.fulfill({status:404, body:''});
        const type = {'.html':'text/html','.js':'application/javascript','.css':'text/css','.json':'application/json',
          '.svg':'image/svg+xml','.webmanifest':'application/manifest+json'}[path.extname(file)];
        return route.fulfill({contentType:type || 'application/octet-stream', body:fs.readFileSync(file)});
      });
      const page = await context.newPage(), errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.clock.install({time: new Date('2026-10-12T00:02:00Z')});  // the fixture's publication
      await page.goto('https://wowpriced.test' + pathname);
      // The home draws its tagline once deals.json is in; the page has it from the server.
      await page.waitForFunction(() => !document.getElementById('tagline') || document.querySelector('#tagline .live-dot'));
      return {page, errors, phone};
    }
    async function header(page) {
      return {styles: await page.evaluate(dump, 'header'), tabs: await page.evaluate(tabs),
        words: await page.evaluate(() => [document.getElementById('tagline').innerText, document.getElementById('q').placeholder,
          document.getElementById('q').getAttribute('aria-label')])};
    }
    // Typing words and Enter, then where it lands: the address, the box, the results and the
    // events from Enter on (while typing, only the home has a wall to filter).
    async function search(page, phone) {
      await (phone ? page.locator('#q').tap() : page.locator('#q').click());
      await page.keyboard.type('yoga mat');
      const typed = await page.evaluate(() => [!document.getElementById('clearsearch').hidden,
        ...[...document.querySelectorAll('[data-tool-link]')].map(a => a.getAttribute('href'))]);
      const before = await page.evaluate(() => JSON.parse(sessionStorage.getItem('events') || '[]').length);
      await page.keyboard.press('Enter');
      await page.waitForURL('https://wowpriced.test/');
      await page.waitForFunction(() => document.querySelector('#content .search-analysis[aria-busy="false"]'));
      return {typed, url: page.url(), q: await page.inputValue('#q'),
        // Where the results land first, the home draws their photos eagerly; animations are clocked.
        results: (await page.locator('#content').innerHTML())
          .replace(/ fetchpriority="high" loading="eager"| loading="lazy" decoding="async"/g, '')
          .replace(/animation-delay:-[\d.]+s/g, ''),
        events: (await page.evaluate(() => JSON.parse(sessionStorage.getItem('events') || '[]'))).slice(before)
          .filter(e => e[1] !== 'home_load_attempt')};
    }

    for (const [name, viewport] of [['phone', {width:390, height:844}], ['laptop', {width:1280, height:800}]]) {
      for (const scheme of ['light', 'dark']) {
        const home = await open('on', '/', viewport, scheme), best = await open('on', '/best-deals-today/', viewport, scheme);
        const off = await open('off', '/best-deals-today/', viewport, scheme);
        const a = await header(home.page), b = await header(best.page);
        // The home's header, element for element: same styles and boxes, both color schemes.
        same(name + ' ' + scheme + ' header', a.styles, b.styles);
        assert.deepEqual(b.words, a.words, 'tagline, placeholder and label');
        assert.deepEqual([b.tabs.labels, b.tabs.box, b.tabs.lines], [a.tabs.labels, a.tabs.box, a.tabs.lines], 'tab row');
        // "Best today" is the current page, in the home's selected look; Trending is not.
        assert.deepEqual([a.tabs.current, b.tabs.current, b.tabs.aria, b.tabs.href], ['Trending', 'Best today', 'page', null]);
        assert.deepEqual(b.tabs.currentLook, a.tabs.currentLook, 'selected look');
        assert.deepEqual(b.tabs.plainLook, a.tabs.plainLook, 'other tabs');
        assert.equal(await best.page.locator('h1').count(), 1);
        assert.equal(await best.page.locator('header #q').count(), 1);
        assert.deepEqual(await best.page.locator('header [data-tool-link]').allInnerTexts(), await home.page.locator('header [data-tool-link]').allInnerTexts());
        assert.match(await best.page.locator('header #subscribe').innerText(), /^Join free/);
        // Below the header the page is as it was: same styles, same places relative to its title.
        same(name + ' ' + scheme + ' content', await off.page.evaluate(dump, '.wrap'), await best.page.evaluate(dump, '.wrap'));
        const gap = await best.page.evaluate(() => Math.round(document.querySelector('.wrap h1').getBoundingClientRect().top
          - Math.max(...[...document.querySelectorAll('header, #cattabs')].map(el => el.getBoundingClientRect().bottom))));
        assert.equal(gap, 32, 'the title keeps its own margin below the header and tab row');
        if (shots && scheme === 'light') await best.page.screenshot({path: path.join(shots, `bdt-header-${name}.png`)});
        for (const p of [home, best, off]) assert.deepEqual(p.errors, []);
        console.log(`${name} ${scheme}: home header (${b.styles.length} elements, tab row ${b.tabs.lines} line(s)); ` +
          `Best today selected; content unchanged`);
        for (const p of [home, best, off]) await p.page.context().close();
      }
      // The search works as on the home: typing then Enter lands on the same results and events.
      const home = await open('on', '/', viewport, 'light'), best = await open('on', '/best-deals-today/', viewport, 'light');
      const a = await search(home.page, home.phone), b = await search(best.page, best.phone);
      assert.deepEqual(a.typed, [true, '/deal-spotter/?q=yoga%20mat', '/review-checker/?q=yoga%20mat']);
      assert.deepEqual(b.typed, a.typed, 'clear button and tool links follow the typed words');
      assert.deepEqual([b.url, b.q], [a.url, a.q]);
      assert.equal(b.results, a.results, 'same results');
      assert.deepEqual(b.events, a.events, 'same events');
      assert(a.events.some(e => e[1] === 'search_analyze'), JSON.stringify(a.events));
      assert.deepEqual([...home.errors, ...best.errors], []);
      await home.page.context().close(); await best.page.context().close();
      // Join free opens the same sign-up dialog.
      const join = await open('on', '/best-deals-today/', viewport, 'light');
      await (join.phone ? join.page.locator('#subscribe').tap() : join.page.locator('#subscribe').click());
      await join.page.waitForSelector('#header-join-dialog iframe');
      assert.deepEqual(join.errors, []);
      await join.page.context().close();
      console.log(`${name}: typing then Enter matches the home (${a.events.map(e => e[1]).join(', ')}); Join free opens the dialog`);
    }
  } finally {
    await browser.close();
    fs.rmSync(scratch, {recursive:true, force:true});
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
