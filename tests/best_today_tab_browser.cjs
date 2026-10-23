// Real Chromium, offline: the home tab row's "Best today" link pill (best_today_tab,
// operator 2026-10-23 ~20:00Z, A48). node tests/best_today_tab_browser.cjs <scratch> [<shots dir>]
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
assert load_config().best_today_tab is True
items=[("kitchen","Stainless steel electric kettle 1.7L"),("beauty","Vitamin C face serum 1 oz"),
       ("electronics","Wireless earbuds with charging case"),("home","Cotton bath towel set of 6"),
       ("sports","Non-slip yoga mat 6mm"),("toys_games","Building blocks set 500 pieces")]
rows=[make_deal(dedupe_key=f"best-tab-{i}", item=f"{items[i%6][1]}, model {i}", store="Amazon",
    category=items[i%6][0], verify_tier="verified", codes=[], conditions=["new"],
    final_price=19.99+i, regular_price=49.99, canonical_url=f"https://amazon.com/dp/B{i:09d}", asin=f"B{i:09d}",
    regular_price_basis="List price on Amazon", basis="amazon_list_price",
    keepa_list_price={"asin":f"B{i:09d}","price":49.99,"observed_at":NOW.isoformat()},
    first_seen_at=NOW.isoformat(), price_rechecked_at=NOW.isoformat(),
    click_price_check={"asin":f"B{i:09d}","final":19.99+i,"buybox":19.99+i,"at":NOW.isoformat()},
    critic={"plausible":True,"confidence":.9,"reason":"Backed basis"},
    image_url="https://m.media-amazon.com/images/I/photo.jpg") for i in range(24)]
for name,on in (("on",True),("off",False)):
    cfg=load_config(); cfg.tracking={}; cfg.best_today_tab=on
    db=DB(":memory:")
    try: site.render_site(db,cfg,NOW,out_dir=Path(sys.argv[1])/name,deals=rows)
    finally: db.close()
`;
const PILL = '#cattabs a[href="/best-deals-today/"]';

// The row as laid out: positions inside the row (scroll-independent), its size and lines.
function snap() {
  const nav = document.getElementById('cattabs'), box = nav.getBoundingClientRect(), kids = [...nav.children];
  const pill = nav.querySelector('a[href="/best-deals-today/"]'), card = document.querySelector('#content .card');
  const at = el => { const b = el.getBoundingClientRect();
    return {x: Math.round(b.left - box.left + nav.scrollLeft), y: Math.round(b.top - box.top), w: Math.round(b.width), h: Math.round(b.height)}; };
  return {labels: kids.map(k => k.textContent), active: [...nav.querySelectorAll('.active')].map(a => a.textContent),
    pill: pill && Object.assign(at(pill), {index: kids.indexOf(pill), dataI: pill.getAttribute('data-i'),
      cls: pill.className, prev: pill.previousElementSibling.getAttribute('data-i'), right: Math.round(pill.getBoundingClientRect().right),
      gap: Math.round(pill.getBoundingClientRect().left - pill.previousElementSibling.getBoundingClientRect().right)}),
    navH: Math.round(box.height), navTop: Math.round(box.top + scrollY), overflow: nav.scrollWidth > nav.clientWidth + 1,
    lines: new Set(kids.map(k => Math.round(k.getBoundingClientRect().top))).size,
    cardTop: card ? Math.round(card.getBoundingClientRect().top + scrollY) : null,
    cards: [...document.querySelectorAll('#content .card')].map(c => c.dataset.key).join()};
}

// The pill's look against its neighbour "All deals" (a plain, inactive filter tab).
function look() {
  const props = ['display', 'background-color', 'color', 'border-top-width', 'border-top-style', 'border-top-color',
    'border-bottom-width', 'border-bottom-color', 'border-top-left-radius', 'padding-top', 'padding-left',
    'font-size', 'font-weight', 'min-height', 'line-height'];
  const read = el => { const s = getComputedStyle(el), dot = getComputedStyle(el, '::before');
    return Object.fromEntries(props.map(p => [p, s.getPropertyValue(p)]).concat(
      [['height', el.getBoundingClientRect().height], ['dot', dot.display + ' ' + dot.backgroundColor]])); };
  return [read(document.querySelector('a[href="/best-deals-today/"]')), read(document.querySelector('#cattabs [data-i="1"]'))];
}

(async () => {
  const scratch = fs.mkdtempSync(path.join(process.argv[2], 'best-tab-'));
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
    async function open(variant, viewport, colorScheme) {
      const root = path.join(scratch, variant), phone = viewport.width < 900;
      const context = await browser.newContext({viewport, colorScheme, isMobile: phone, hasTouch: phone,
        reducedMotion: 'reduce', serviceWorkers: 'block', deviceScaleFactor: shots ? 2 : 1});
      // The server's row, as first painted, before the page draws its own.
      await context.addInitScript(`window.__snap = ${snap}; document.addEventListener('DOMContentLoaded', () => { if (location.pathname !== '/') return;
        window.__server = window.__snap(); document.getElementById('cattabs').firstElementChild.__server = true; });`);
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
        if (url.pathname === '/deals.json') await new Promise(r => setTimeout(r, 400));  // first paint is the server's row
        const type = {'.html':'text/html','.js':'application/javascript','.css':'text/css','.json':'application/json',
          '.svg':'image/svg+xml','.webmanifest':'application/manifest+json'}[path.extname(file)];
        return route.fulfill({contentType:type || 'application/octet-stream', body:fs.readFileSync(file)});
      });
      const page = await context.newPage(), errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.clock.install({time: new Date('2026-10-12T00:02:00Z')});  // the fixture's publication
      await page.goto('https://wowpriced.test/');
      // The page has drawn the row itself (renderTabs replaced the server's nodes).
      await page.waitForFunction(() => window.__server && document.querySelector('#content .card') &&
        !document.getElementById('cattabs').firstElementChild.__server);
      return {page, errors, phone};
    }

    // Phones: one sideways row. Desktop: the row wraps (two lines at 1280).
    for (const [name, viewport] of [['phone', {width:390, height:844}], ['desktop', {width:1280, height:800}]]) {
      const seen = {};
      for (const scheme of ['light', 'dark']) {
        const {page, errors, phone} = await open('on', viewport, scheme);
        const server = await page.evaluate(() => window.__server), now = await page.evaluate(snap);
        // Right after Trending, a link and not a filter tab.
        assert.equal(now.labels[0], 'Trending');
        assert.deepEqual(now.pill && [now.pill.index, now.pill.prev, now.pill.dataI, now.pill.cls], [1, '0', null, 'cattab']);
        assert.equal(now.labels[1], 'Best today');
        assert(now.pill.gap >= 0 && now.pill.gap <= 16, 'on Trending\'s line, next to it: gap ' + now.pill.gap);
        assert.equal(now.labels.filter(l => l === 'Best today').length, 1);
        assert.deepEqual(now.active, ['Trending']);
        // No layout shift: the page draws the pill where the server did.
        assert.deepEqual(now.labels, server.labels);
        assert.deepEqual(now.pill, server.pill, name + ': pill moved when the page took over');
        assert.equal(now.navH, server.navH); assert.equal(now.navTop, server.navTop);
        // Same pill look as its neighbours.
        const [pill, neighbour] = await page.evaluate(look);
        assert.deepEqual(pill, neighbour, name + ' ' + scheme + ': pill looks different from All deals');
        assert(pill.height >= 44, 'tap target ' + pill.height);
        if (phone) {
          assert.equal(now.lines, 1, 'phones keep one sideways row');
          assert(now.pill.right <= viewport.width, 'pill on the first phone screen');
        } else assert(now.lines >= 2, 'the desktop row wraps');
        if (shots && scheme === 'light') await page.screenshot({path: path.join(shots, `best-tab-${name}.png`)});
        // A tap is not a filter: no tab handler takes it (default not prevented), nothing toggles.
        await page.evaluate(() => {
          window.__taps = [];
          window.__hold = e => { if (e.target.closest('a[href="/best-deals-today/"]')) { __taps.push(e.defaultPrevented); e.preventDefault(); } };
          document.addEventListener('click', window.__hold);
        });
        await (phone ? page.locator(PILL).tap() : page.locator(PILL).click());
        await page.waitForTimeout(200);
        let after = await page.evaluate(snap);
        assert.deepEqual(await page.evaluate(() => window.__taps), [false], 'the tab script handled the pill');
        assert.deepEqual(after.active, ['Trending']); assert.equal(after.pill.cls, 'cattab');
        assert.equal(after.cards, now.cards, 'the wall was not filtered');
        // A category tap redraws the row: the pill stays right after Trending, on the same line
        // (Trending drops its bold active weight, so later tabs move by those pixels alone).
        await page.locator('#cattabs [data-i="3"]').click();
        await page.waitForFunction(() => document.querySelector('#cattabs [data-i="3"].active'));
        after = await page.evaluate(snap);
        assert.deepEqual(after.active, ['Home & Kitchen']);
        assert.deepEqual([after.pill.index, after.pill.gap, after.pill.y, after.pill.w, after.pill.cls], [1, now.pill.gap, now.pill.y, now.pill.w, 'cattab']);
        assert.equal(after.navH, now.navH);
        await page.locator('#cattabs [data-i="0"]').click();
        await page.waitForFunction(() => document.querySelector('#cattabs [data-i="0"].active'));
        // Keyboard: Tab from Trending reaches the pill (focus ring shown), Enter opens the page.
        await page.evaluate(() => document.removeEventListener('click', window.__hold));
        await page.focus('#cattabs [data-i="0"]');
        await page.keyboard.press('Tab');
        const focus = await page.evaluate(() => { const el = document.activeElement;
          return [el.getAttribute('href'), el.matches(':focus-visible'), getComputedStyle(el).outlineStyle]; });
        assert.deepEqual(focus, ['/best-deals-today/', true, 'solid']);
        await page.keyboard.press('Enter');
        await page.waitForURL('https://wowpriced.test/best-deals-today/');
        assert.equal(await page.locator('h1').first().innerText(), 'Best deals today');
        assert.deepEqual(errors, []);
        seen[scheme] = now;
        console.log(`${name} ${scheme}: pill after Trending at x=${now.pill.x} (${now.pill.w}x${now.pill.h}); ` +
          `row ${now.navH}px, ${now.lines} line(s), scrolls sideways: ${now.overflow}; no shift`);
        await page.context().close();
      }
      // Off: no pill; at these widths the row and the wall below sit where they do with it
      // (measured: windows about 920-1030px wide wrap to a third line with the pill).
      const {page, errors} = await open('off', viewport, 'light');
      const off = await page.evaluate(snap);
      assert.equal(off.pill, null);
      assert.deepEqual(off.labels, seen.light.labels.filter(l => l !== 'Best today'));
      assert.deepEqual([off.navH, off.navTop, off.cardTop], [seen.light.navH, seen.light.navTop, seen.light.cardTop]);
      assert.deepEqual(errors, []);
      console.log(`${name} off: no pill; same row height and first card top (${off.cardTop})`);
      await page.context().close();
    }
  } finally {
    await browser.close();
    fs.rmSync(scratch, {recursive:true, force:true});
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
