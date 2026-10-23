// Real Chromium, offline: the phone deal page's sticky Get deal bar (switch on).
// node tests/sticky_buy_bar_browser.cjs <scratch> [<shots dir>]
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');

const BUILD = `
import sys
from pathlib import Path
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.publish import site
from tests.test_publish_site import make_deal
from tests.test_support import NOW
site.util_now=lambda:NOW
cfg=load_config()
cfg.sticky_buy_bar=True
cfg.tracking={}
cfg.affiliate_amazon_tag="example-20"
rows=[make_deal(dedupe_key=f"sticky-kettle-{i}", item=f"Stainless steel electric kettle 1.7L, auto shut-off, model {i}",
    store="Amazon", category="kitchen", verify_tier="verified", codes=[], conditions=["new"],
    final_price=24.99+i, regular_price=49.99, canonical_url=f"https://amazon.com/dp/B{i:09d}", asin=f"B{i:09d}",
    regular_price_basis="List price on Amazon", basis="amazon_list_price",
    keepa_list_price={"asin":f"B{i:09d}","price":49.99,"observed_at":NOW.isoformat()},
    first_seen_at=NOW.isoformat(), price_rechecked_at=NOW.isoformat(),
    click_price_check={"asin":f"B{i:09d}","final":24.99+i,"buybox":24.99+i,"at":NOW.isoformat()},
    critic={"plausible":True,"confidence":.9,"reason":"Backed basis"},
    image_url="https://m.media-amazon.com/images/I/photo.jpg") for i in range(8)]
db=DB(":memory:")
try: site.render_site(db,cfg,NOW,out_dir=Path(sys.argv[1])/"site",deals=rows)
finally: db.close()
`;

const state = () => {
  const bar = document.querySelector('[data-buybar]'), main = document.querySelector('.deal-detail>.getdeal');
  const style = getComputedStyle(bar), box = bar.getBoundingClientRect();
  return {display: style.display, visibility: style.visibility, opacity: style.opacity, top: box.top,
    height: box.height, background: style.backgroundColor, pad: getComputedStyle(document.body).paddingBottom,
    mainTop: main.getBoundingClientRect().top + scrollY, mainBottom: main.getBoundingClientRect().bottom};
};

(async () => {
  const scratch = fs.mkdtempSync(path.join(process.argv[2], 'sticky-'));
  const shots = process.argv[3];
  const built = spawnSync('.venv/bin/python', ['-c', BUILD, scratch], {encoding:'utf8'});
  assert.equal(built.status, 0, built.stderr);
  const root = path.join(scratch, 'site');
  const deal = '/deal/' + fs.readdirSync(path.join(root, 'deal')).sort()[0] + '/';
  let browser;
  try {
    browser = await chromium.launch({headless:true, executablePath:'/opt/pw-browsers/chromium'});
  } catch (e) {
    console.log('Chromium cannot launch: ' + e.message.split('\n')[0]);
    process.exitCode = 77; return;
  }
  try {
    async function open(viewport, colorScheme) {
      const context = await browser.newContext({viewport, colorScheme, deviceScaleFactor: shots ? 2 : 1});
      await context.addInitScript(() => { window.calls = []; window.wpTrack = (n, p) => calls.push([n, p]); });
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://wowpriced.test') return route.fulfill({status:204, body:''});
        let file = path.join(root, url.pathname);
        if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
        if (!fs.existsSync(file)) return route.fulfill({status:404, body:''});
        const type = {'.html':'text/html','.js':'application/javascript','.css':'text/css','.json':'application/json'}[path.extname(file)];
        return route.fulfill({contentType:type || 'application/octet-stream', body:fs.readFileSync(file)});
      });
      const page = await context.newPage();
      await page.goto('https://wowpriced.test' + deal);
      await page.waitForTimeout(300);
      return page;
    }
    const settle = page => page.waitForTimeout(400);  // past the .2s fade

    for (const scheme of ['light', 'dark']) {
      const page = await open({width:390, height:844}, scheme);
      let s = await page.evaluate(state);
      assert.equal(s.visibility, 'hidden', scheme + ': no bar at the top');
      assert.equal(s.opacity, '0');
      assert.equal(s.pad, '0px');
      if (shots && scheme === 'light') await page.screenshot({path: path.join(shots, 'top.png')});
      const before = s.mainTop;
      // Halfway down the page is past the main button.
      await page.evaluate(() => scrollTo(0, (document.documentElement.scrollHeight - innerHeight) / 2));
      await settle(page);
      s = await page.evaluate(state);
      assert(s.mainBottom < 0, scheme + ': halfway is past the main button');
      assert.equal(s.visibility, 'visible', scheme + ': bar shows past the main button');
      assert.equal(s.opacity, '1');
      assert.equal(s.top + s.height, 844, 'pinned to the bottom');
      assert.equal(s.height, 56, 'slim bar');
      assert.equal(s.pad, s.height + 'px', 'bottom padding equals the bar height');
      assert.equal(s.mainTop, before, 'no layout shift');
      const tap = await page.evaluate(() => document.querySelector('[data-buybar] .getdeal').getBoundingClientRect().height);
      assert(tap >= 44, 'tap target ' + tap);
      assert.equal(s.background, scheme === 'dark' ? 'rgba(17, 24, 39, 0.7)' : 'rgba(255, 255, 255, 0.72)', 'glass');
      if (shots) await page.screenshot({path: path.join(shots, scheme === 'light' ? 'scrolled.png' : 'dark.png')});
      // Back above the main button: hidden again.
      await page.evaluate(() => scrollTo(0, 0));
      await settle(page);
      assert.equal((await page.evaluate(state)).visibility, 'hidden', scheme + ': hidden again at the top');
      // At the very bottom the footer's last line sits above the bar.
      await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight));
      await settle(page);
      const foot = await page.evaluate(() => {
        const bar = document.querySelector('[data-buybar]').getBoundingClientRect();
        const lines = [...document.querySelectorAll('footer *')].filter(el =>
          el.checkVisibility() && el.textContent.trim() && !el.children.length);
        const last = lines.reduce((a, b) => b.getBoundingClientRect().bottom > a.getBoundingClientRect().bottom ? b : a);
        const box = last.getBoundingClientRect();
        const hit = document.elementFromPoint(box.left + 2, box.top + box.height / 2);
        return {text: last.textContent.trim(), bottom: box.bottom, barTop: bar.top, visible: last.contains(hit)};
      });
      assert(foot.bottom <= foot.barTop && foot.visible, scheme + ': footer last line covered ' + JSON.stringify(foot));
      if (scheme === 'light') {
        // The bar's tap is the main button's: same deal_click, no new event.
        await page.click('[data-buybar] .getdeal');
        await page.waitForTimeout(300);
        const calls = await page.evaluate(() => calls.filter(c => c[0] === 'deal_click'));
        assert.deepEqual(calls, [['deal_click', {store:'Amazon', category:'kitchen', method:'deal'}]]);
      }
      console.log(`${scheme}: bar ${s.height}px; footer "${foot.text.slice(0, 40)}" ends at ${foot.bottom}, bar at ${foot.barTop}`);
      await page.context().close();
    }
    // Laptops: never shown.
    const desk = await open({width:1280, height:900}, 'light');
    await desk.evaluate(() => scrollTo(0, document.querySelector('.deal-detail>.getdeal').getBoundingClientRect().bottom + scrollY + 200));
    await settle(desk);
    const d = await desk.evaluate(state);
    assert(d.mainBottom < 0, 'desktop scrolled past the main button');
    assert.equal(d.display, 'none');
    assert.equal(d.pad, '0px');
    console.log('desktop: hidden');
  } finally {
    await browser.close();
    fs.rmSync(scratch, {recursive:true, force:true});
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
