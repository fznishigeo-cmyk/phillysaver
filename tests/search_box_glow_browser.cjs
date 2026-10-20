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
    const rect = el => {const r = el.getBoundingClientRect();
      return {left:r.left, right:r.right, top:r.top, bottom:r.bottom, width:r.width, height:r.height};};
    const input = field.querySelector('#q'), clear = field.querySelector('#clearsearch');
    const q = rect(input), ic = getComputedStyle(input);
    const pills = [...field.querySelectorAll('#analyze-search,.all-discounts')]
      .filter(el => el.getBoundingClientRect().width).map(el => {
        const css = getComputedStyle(el), range = document.createRange(); range.selectNodeContents(el);
        const label = range.getBoundingClientRect();
        return {...rect(el), label:el.innerText.replace(/\s+/g, ' ').trim(), labelWidth:label.width, labelLeft:label.left, labelRight:label.right,
          contentWidth:el.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight), color:css.color, shadow:css.boxShadow,
          background:css.backgroundImage, textShadow:css.textShadow, fontSize:css.fontSize, fontWeight:css.fontWeight,
          sparkle:getComputedStyle(el, '::before').content,
          borders:[css.borderTopWidth, css.borderBottomWidth, css.borderRightWidth, css.borderLeftWidth],
          radii:[css.borderTopLeftRadius, css.borderBottomLeftRadius, css.borderTopRightRadius, css.borderBottomRightRadius]};
      });
    return {field:rect(field), pills, twins:!!field.querySelector('.all-discounts'), phone:innerWidth < 900,
      polished:!document.getElementById('search-hint'), calm:!!field.querySelector('.analyze-long')?.textContent.includes('AI Analysis'),
      icon:field.querySelector('svg') ? rect(field.querySelector('svg')) : null,
      textLeft:q.left + parseFloat(ic.paddingLeft), textRight:q.right - parseFloat(ic.paddingRight),
      clear:clear && !clear.hidden ? rect(clear) : null,
      overflow:document.documentElement.scrollWidth > innerWidth};
  });
  assert.equal(g.pills.length, g.twins && !g.phone ? 2 : 1, 'only desktop puts both pills in the field');
  for (const [i, pill] of g.pills.entries()) {
    assert.deepEqual(pill.borders, ['0px', '0px', '0px', '0px'], 'no transparent border ring');
    const gaps = [pill.top - g.field.top, g.field.bottom - pill.bottom];
    if (i === g.pills.length - 1) gaps.push(g.field.right - pill.right);
    for (const gap of gaps) assert(gap >= (g.polished ? 5.9 : 1.9) && gap <= (g.polished ? 6.1 : g.twins ? 4 : 3),
      'even field inset around the pill: ' + gap);
    assert(Math.abs(gaps[0] - gaps[1]) <= .1, 'vertically centred oval');
    assert.deepEqual(pill.radii, g.twins || g.polished ? ['999px','999px','999px','999px'] : ['999px','999px','10px','10px']);
    assert(pill.width >= 44 && pill.height >= 44, '44px tap target');
    assert(pill.labelWidth <= pill.contentWidth + .1 && pill.labelLeft >= pill.left && pill.labelRight <= pill.right,
      'label fits without clipping: ' + JSON.stringify({page:page.url(), viewport:page.viewportSize(),
        label:pill.label, labelWidth:pill.labelWidth, buttonWidth:pill.width, contentWidth:pill.contentWidth, fontSize:pill.fontSize}));
    if (g.twins) {
      assert(!/inset|255, 255, 255/.test(pill.shadow), 'no white/light ring at rest or on interaction');
      assert.equal(pill.color, 'rgb(255, 255, 255)');
      if (g.calm) {
        assert.equal(pill.sparkle, 'none', 'task 332: no star on either button');
        assert(Math.abs((pill.labelLeft + pill.labelRight) / 2 - (pill.left + pill.right) / 2) <= 1, 'label centred: ' + pill.label);
      } else assert(pill.sparkle.includes('✦'), 'same sparkle icon');
      if (i) {
        const first = g.pills[0];
        assert(pill.left >= first.right + (g.polished ? 5.9 : 3), 'pills have an even small gap');
        if (g.polished) assert(pill.left - first.right <= 6.1, 'same 6px gap between pills and field edges');
        assert.equal(pill.height, first.height, 'twin heights');
        if (g.calm) {
          assert.equal(pill.width, first.width, 'equal task-332 widths');
          assert.equal(pill.fontSize, first.fontSize, 'equal font size');
          assert.equal(pill.fontWeight, first.fontWeight, 'equal font weight');
          assert.equal(pill.label, 'All Discounts', 'full capitalised desktop label at laptop widths too');
        }
        assert.equal(pill.textShadow, first.textShadow, 'same bold-text shadow');
        assert.notEqual(pill.background, first.background, 'distinct purple and Dropbox Blue families');
      }
    }
  }
  assert.equal(g.pills[0].label, g.calm && !g.phone ? 'AI Analysis' : 'Analyze');
  if (g.calm) {
    assert.equal(await page.locator('#analyze-search').getAttribute('aria-label'), null,
      'task 332 derives the accessible name from the visible responsive span');
    assert.equal(await page.getByRole('button', {name:g.phone ? 'Analyze' : 'AI Analysis', exact:true}).count(), 1);
  }
  assert(g.textRight <= g.pills[0].left, 'typed text clears both pills');
  if (g.icon) assert(Math.abs((g.icon.top + g.icon.bottom) / 2 - (g.field.top + g.field.bottom) / 2) <= .1,
    'search icon stays centred in the field');
  if (g.clear) {
    assert(g.clear.right <= g.pills[0].left, 'clear button clears both pills');
    assert(g.textRight <= g.clear.left, 'typed text clears the clear button');
    if (g.polished) assert(Math.abs((g.clear.top + g.clear.bottom) / 2 - (g.field.top + g.field.bottom) / 2) <= .1,
      'clear button stays centred in the field');
  }
  if (g.twins && page.viewportSize().width === 1280 && await page.locator('header>.hrow>.search').count())
    assert(g.textRight - g.textLeft >= 300, '1280px reserves 300px of visible typed text');
  assert.equal(g.overflow, false, 'no sideways overflow');
  if (g.calm && !g.phone && g.twins) {
    const room = g.textRight - g.textLeft, old = g.field.width - (g.icon ? 48 : 14) - (page.viewportSize().width <= 1100 ? 262 : 292);
    assert(room >= old - .1, `typing room ${room} >= previous ${old}`);
    return {width:page.viewportSize().width, typingRoom:room, previousTypingRoom:old,
      labels:g.pills.map(({label, labelWidth, width, contentWidth, fontSize}) => ({label, labelWidth, buttonWidth:width, contentWidth, fontSize}))};
  }
}

async function assertCalm(page, button) {
  const paint = () => button.evaluate(el => {
    const css = getComputedStyle(el);
    return Object.fromEntries(['backgroundImage', 'backgroundPosition', 'transform', 'filter', 'boxShadow', 'color']
      .map(key => [key, css[key]]));
  });
  await page.mouse.move(0, 0); await button.blur();
  const before = await paint(), box = await button.boundingBox();
  await button.hover(); await page.waitForTimeout(200);
  assert.deepEqual(await paint(), before, 'hover preserves every painted style');
  await page.keyboard.press('Tab');
  await button.focus();
  assert.deepEqual(await paint(), before, 'focus preserves every painted style');
  assert(await button.evaluate(el => parseFloat(getComputedStyle(el).outlineWidth) >= 2 &&
    getComputedStyle(el).outlineStyle !== 'none'), 'visible keyboard focus outline');
  await page.mouse.down();
  try {
    assert.deepEqual(await paint(), before, 'active preserves every painted style');
    assert.deepEqual(await button.boundingBox(), box);
    for (const pseudo of [null, '::before', '::after'])
      assert.equal(await button.evaluate((el, pseudo) => getComputedStyle(el, pseudo).animationName, pseudo), 'none');
  } finally {await page.mouse.move(0, 0); await page.mouse.up(); await button.blur();}
}

async function assertPhonePlaceholder(page) {
  assert.equal(await page.locator('#q').getAttribute('placeholder'), 'Search deals on Amazon');
  const fit = await page.locator('#q').evaluate(input => {
    const css = getComputedStyle(input), probe = document.createElement('span');
    Object.assign(probe.style, {position:'fixed', left:'-10000px', display:'block', whiteSpace:'nowrap',
      font:css.font, width:(input.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight)) + 'px'});
    probe.textContent = input.placeholder; document.body.appendChild(probe);
    const result = {scrollWidth:probe.scrollWidth, clientWidth:probe.clientWidth,
      inputScrollWidth:input.scrollWidth, inputClientWidth:input.clientWidth};
    probe.remove(); return result;
  });
  assert(fit.scrollWidth <= fit.clientWidth, 'placeholder fits without truncation: ' + JSON.stringify(fit));
  assert(fit.inputScrollWidth <= fit.inputClientWidth, 'input has no internal overflow');
}

async function assertDiscounts(page, typed) {
  const phone = page.viewportSize().width < 900;
  const desktop = page.locator('#search-discounts'), mobile = page.locator('#search-discounts-mobile');
  assert.equal(await page.locator('#search-hint').count(), 0, 'redundant hint removed');
  assert(!((await page.locator('#q').getAttribute('aria-describedby')) || '').split(/\s+/).includes('search-hint'));
  assert.match(await page.locator('footer').innerText(), /As an Amazon Associate,\s*wowpriced earns from qualifying purchases\./);
  assert.equal(await page.locator('.discounts-disclosure, .discounts-mobile small').count(), 0,
    'footer carries the disclosure for both discounts buttons');
  assert.equal(await desktop.isVisible(), !phone);
  assert.equal(await mobile.isVisible(), phone && typed);
  if (phone && !typed) return;
  const button = phone ? mobile : desktop;
  assert.equal(await button.getAttribute('href'), 'https://www.amazon.com/');
  assert.equal(await button.getAttribute('target'), '_blank');
  assert.equal(await button.getAttribute('rel'), 'sponsored noopener');
  assert(await page.locator('footer .footerfine').isVisible(), 'footer disclosure remains visible');
  if (phone) {
    const b = await button.boundingBox(), field = await page.locator('.searchfield').boundingBox();
    const calm = (await page.locator('.analyze-long').first().textContent()).includes('AI Analysis');
    assert.equal(await button.innerText(), calm ? 'All Discounts on Amazon →' : 'All discounts on Amazon →');
    assert(b.height >= 44 && Math.abs(b.width - field.width) < 1 && Math.abs(b.x - field.x) < 1);
    assert(b.y >= field.y + field.height && b.y <= field.y + field.height + 12, 'directly under the field');
  }
  // Check the actual emitted gradient stops, including the gold sheen's brightest blend.
  for (const control of [page.locator('#analyze-search'), button]) {
    const colors = await control.evaluate(el => {
      const css = getComputedStyle(el), sheen = getComputedStyle(el, '::after');
      return {fg:css.color, stops:css.backgroundImage.match(/rgba?\([^)]+\)/g), sheen:sheen.backgroundImage};
    });
    assert.equal(colors.fg, 'rgb(255, 255, 255)');
    if (control === button) assert(colors.stops.includes('rgb(0, 97, 254)'), 'Dropbox Blue gradient centre');
    const rgb = color => color.match(/[\d.]+/g).map(Number).slice(0, 3).map(v => v / 255);
    const luminance = color => color.map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
      .reduce((sum, v, i) => sum + v * [.2126,.7152,.0722][i], 0);
    const sheen = /rgba\(255, 196, 0, ([\d.]+)\)/.exec(colors.sheen);
    for (const stop of colors.stops) for (const alpha of [0, sheen ? Number(sheen[1]) : 0]) {
      const blend = rgb(stop).map((v, i) => v * (1 - alpha) + [1,196/255,0][i] * alpha);
      assert(1.05 / (luminance(blend) + .05) >= 4.5, 'white text AA on every painted stop: ' + stop);
    }
  }
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
    for (const lazy of [false, true]) for (const width of [360, 390, 412, 900, 1023, 1024, 1100, 1199, 1200, 1280, 1440, 1512, 1599, 1600, 1920]) for (const theme of ['light', 'dark'])
      for (const motion of ['no-preference', 'reduce']) {
        let before;
        const name = `${lazy ? 'lazy' : 'inline'}-${width}-${theme}-${motion}`;
        for (const glow of [false, true]) {
          const context = await browser.newContext({viewport:{width, height:844}, colorScheme:theme,
            reducedMotion:motion, serviceWorkers:'block'});
          const errors = [], unexpected = [], calls = [];
          await context.route('**/*', route => {
            const url = new URL(route.request().url());
            if (url.hostname === 'm.media-amazon.com') return route.fulfill({contentType:'image/svg+xml',
              body:'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320"><rect width="320" height="320" fill="#b9c6cd"/></svg>'});
            if (url.origin !== 'https://wowpriced.test') {unexpected.push(url.origin); return route.fulfill({status:204, body:''});}
            if (url.pathname.startsWith('/api/')) calls.push(url.pathname + url.search);
            if (url.pathname === '/api/search') { return route.fulfill({json:url.searchParams.get('q') === 'unavailable'
              ? {items:[], reason:'budget_exhausted'} : {items:[{asin:'B000000901', title:'Wireless mouse from Amazon',
                price_cents:1299, as_of:new Date().toISOString()}]}}); }
            const root = roots[`${lazy}-${glow}`], file = path.resolve(root, '.' + url.pathname, url.pathname === '/' ? 'index.html' : '');
            if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) return route.fulfill({status:204, body:''});
            return route.fulfill({path:file});
          });
          await context.addInitScript(() => {localStorage.setItem('wp.analyzeHint', '1'); window.opened = []; window.openCalls = [];
            window.open = (...args) => {window.opened.push(args[0]); window.openCalls.push(args);};});
          const page = await context.newPage();
          page.on('pageerror', error => errors.push(error.message));
          await page.goto('https://wowpriced.test/');
          await page.locator('#content .card').first().waitFor();
          const typing = await assertAnalyzeGeometry(page);
          const calm = await page.locator('.analyze-long').textContent().then(label => label === 'AI Analysis');
          if (calm && width < 900) await assertPhonePlaceholder(page);
          await assertDiscounts(page, false);
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
            // Task 318: the pill is a full oval with an even 6 px gap, so only its
            // width/x may differ from the glow-OFF pill; the field, input, header and cards may not.
            const {button, ...rest} = geometry, {button: beforeButton, ...beforeRest} = before;
            assert.deepEqual(rest, beforeRest, name + ': unchanged geometry and text contrast');
            assert.equal(button.height, beforeButton.height, name + ': pill height unchanged');
            assert.equal(button.y, beforeButton.y, name + ': pill row unchanged');
            // Inside the field; on phones Analyze is the rightmost pill (All discounts sits right of it on desktop).
            assert(button.x >= geometry.field.x && button.x + button.width <= geometry.field.x + geometry.field.width - 1,
              name + ': pill inside the field');
            if (/-(360|390)-/.test(name)) {
              const gap = geometry.field.x + geometry.field.width - (button.x + button.width);
              assert(gap >= 5.9 && gap <= 6.1,
                name + ': phone pill has a white field gap ' + JSON.stringify({button, field: geometry.field}));
            }
            metrics.push({name, before, after:geometry, typing});
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
            for (const selector of ['#analyze-search', ...(width >= 900 ? ['#search-discounts'] : [])]) {
              const button = page.locator(selector), bounds = await button.boundingBox();
              if (calm) {await assertCalm(page, button); continue;}
              await button.hover();
              await button.focus();
              await page.mouse.down();
              try {
                assert.deepEqual(await button.boundingBox(), bounds, 'hover/focus/active do not shift the pill');
                assert.equal(await button.evaluate(el => getComputedStyle(el).animationName),
                  motion === 'reduce' ? 'none' : 'wpcharge');
                await assertAnalyzeGeometry(page);
                await assertDiscounts(page, false);
              } finally {
                await page.mouse.move(0, 0);
                await page.mouse.up();
              }
            }
            await page.locator('#q').focus();
            if (motion !== 'reduce') assert.equal((await style()).state, 'paused');
            await page.locator('#q').fill('mouse'); await page.locator('#q').blur();
            await assertAnalyzeGeometry(page);
            await assertDiscounts(page, true);
            if (width < 900) {
              const mobile = page.locator('#search-discounts-mobile');
              if (calm) await assertCalm(page, mobile);
              const before = await mobile.boundingBox();
              await mobile.focus();
              assert.equal(await mobile.evaluate(el => getComputedStyle(el).animationName),
                calm || motion === 'reduce' ? 'none' : 'wpcharge', 'phone discounts motion rule');
              assert.deepEqual(await mobile.boundingBox(), before, 'phone focus does not move the button');
              await assertDiscounts(page, true);
            }
            await page.locator('#q').fill('');
            await assertDiscounts(page, false);
            await page.locator('#q').fill('https://www.amazon.com/Nulaxy-Adjustable-Anti-Slip-Smartphone-Essentials/dp/B012345678');
            await page.locator(width < 900 ? '#search-discounts-mobile' : '#search-discounts').click();
            assert.equal(await page.evaluate(() => window.opened.at(-1)),
              'https://www.amazon.com/s?k=Nulaxy%20Adjustable%20Anti%20Slip%20Smartphone%20Essentials&rh=p_n_deal_type%3A23566065011&tag=example-20');
            await page.locator('#q').fill('mouse');
            if (motion !== 'reduce') assert.equal((await style()).state, 'paused');
            if (calm) {
              // The 150ms input debounce, lazy module import and 600ms search
              // debounce exceed 750ms. Wait for this query's response and paint.
              const response = page.waitForResponse(response => {
                const url = new URL(response.url());
                return url.pathname === '/api/search' && url.searchParams.get('q') === 'phone holder' &&
                  url.searchParams.get('page') === '0';
              });
              await page.locator('#q').fill('phone holder');
              await (await response).finished();
              await page.locator('#amazon-results article').first().waitFor();
              const beforeClick = calls.length;
              await page.locator('#analyze-search').click();
              assert.equal(await page.evaluate(() => window.opened.at(-1)),
                'https://www.amazon.com/s?k=phone%20holder&rh=p_n_deal_type%3A210906366011&s=exact-aware-popularity-rank&tag=example-20');
              assert.deepEqual(await page.evaluate(() => window.openCalls.at(-1).slice(1)), ['_blank', 'noopener']);
              await page.waitForTimeout(200);
              assert.equal(calls.length, beforeClick,
                'typed button adds no API call: ' + JSON.stringify(calls.slice(beforeClick)));
              assert.equal(await page.locator('#search-result-top, #search-amazon-result:visible').count(), 0, 'pill absent');
              assert.equal(await page.locator('.search-recovery .search-amazon-top, .search-recovery [data-search-amazon].getdeal').count(), 0,
                'zero-local Amazon recovery pill absent');
              const fallback = page.locator('.search-recovery [data-search-amazon]');
              assert.equal(await fallback.textContent(), 'Search Amazon for "phone holder"');
              assert.equal(await fallback.evaluate(el => getComputedStyle(el).animationName), 'none');
              assert.equal(await fallback.evaluate(el => getComputedStyle(el).backgroundImage), 'none');
              assert.equal(await page.locator('#amazon-results .getdeal').first().textContent(), 'View on Amazon');
              await page.screenshot({path:path.join(shots, name + '-phone-holder.png')});
              const emptyResponse = page.waitForResponse(response => {
                const url = new URL(response.url());
                return url.pathname === '/api/search' && url.searchParams.get('q') === 'unavailable';
              });
              await page.locator('#q').fill('unavailable');
              await (await emptyResponse).finished();
              await page.waitForFunction(() => document.querySelector('#search-recovery-title')?.textContent ===
                'See every "unavailable" price on Amazon');
              assert.equal(await fallback.textContent(), 'Search Amazon for "unavailable"');
              assert.equal(await page.locator('#amazon-results article, .search-recovery .search-amazon-top, .search-recovery .getdeal').count(), 0);
              await page.locator('#q').fill('https://amazon.com/dp/B000000901?tag=PRIVATE');
              await page.locator('#analyze-search').click();
              await page.locator('.search-analysis[aria-busy="false"]').waitFor();
              assert.equal(await page.locator('#search-result-top, .search-analysis .search-amazon-top').count(), 0);
              assert(await page.locator('.search-analysis #amazon-results article').count() > 0, 'product analysis still renders its live card');
              assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
              await context.close();
              continue;
            }
            await page.locator('#search-amazon-result a.getdeal').waitFor();
            const checkTop = async analyze => {
              assert.equal(await page.locator('#search-amazon-result:visible, #search-result-top:visible').count(), 1);
              assert.equal(await page.locator('a.getdeal[data-search-amazon]:visible, a.getdeal[data-analyze-fallback]:visible').count(), 1,
                'one big Amazon button');
              const top = page.locator(analyze ? '#search-result-top' : '#search-amazon-result');
              assert.equal(await top.locator('small').count(), 0, 'top button relies on footer disclosure');
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
              assert.equal(url.searchParams.get('s'), 'exact-aware-popularity-rank');
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
    console.log('Typing room (CSS px): ' + JSON.stringify(metrics.filter(m => m.typing).map(m => ({name:m.name, ...m.typing}))));
    console.log('Search box native checks passed: geometry, motion, early buttons, themes and screenshots');
  } finally {if (browser) await browser.close(); fs.rmSync(scratch, {recursive:true, force:true});}
}
module.exports = {buildFixture, assertAnalyzeGeometry, assertDiscounts};
if (require.main === module) main().catch(error => {console.error(error); process.exitCode = 1;});
