// Native Chromium; every navigation, API and image request is fulfilled offline.
// node tests/header_polish_browser.cjs /path/to/chromium [docs/research/header]
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {assertAnalyzeGeometry, assertDiscounts} = require('./search_box_glow_browser.cjs');

function buildFixture(scratch, polish = true, discounts = true, phoneTagline = true, timerRing = true) {
  fs.mkdirSync(scratch, {recursive:true});
  const result = spawnSync('.venv/bin/python', ['-c', `
import json, sys
from pathlib import Path
from dealradar.config import load_config
from dealradar.publish import site
from tests.test_support import NOW, render
from tests.test_publish_site import make_deal
site.util_now = lambda: NOW
cfg = load_config()
cfg.header_polish = sys.argv[2] == "true"
cfg.all_discounts_button = sys.argv[3] == "true"
cfg.search_polish_1019 = cfg.all_discounts_button
cfg.phone_tagline_count = sys.argv[4] == "true"
cfg.timer_pill_ring = sys.argv[5] == "true"
cfg.tracking = {}
cfg.affiliate_amazon_tag = "example-20"
deals = [make_deal(dedupe_key=f"task305-{i}", item=f"Kitchen kettle {i}", store="Amazon", category="kitchen",
    codes=[], conditions=["new"], first_seen_at=NOW.isoformat(), last_seen_at=NOW.isoformat(),
    canonical_url=f"https://www.amazon.com/dp/B0{i:08d}", asin=f"B0{i:08d}", price_rechecked_at=NOW.isoformat(),
    click_price_check=dict(asin=f"B0{i:08d}", final=10, buybox=10, at=NOW.isoformat()),
    image_url="https://m.media-amazon.com/images/I/kettle.jpg") for i in range(1, 25)]
out, public, full, _ = render(Path(sys.argv[1]), cfg, deals)
assert len(full["deals"]) == 24, "all fixture offers must survive publication"
pages = ["/" + str(p.parent.relative_to(out)) + "/" for p in sorted((out / "deal").rglob("index.html"))][:1]
assert pages, "fixture must include a current deal page"
pages += ["/" + cfg.seo_event_pages_events[0]["slug"] + "/"]
(Path(sys.argv[1]) / "fixture.json").write_text(json.dumps({"now":NOW.isoformat(), "pages":pages}))
`, scratch, String(polish), String(discounts), String(phoneTagline), String(timerRing)], {encoding:'utf8'});
  assert.equal(result.status, 0, result.stdout + result.stderr);
  return path.join(scratch, 'site');
}

function geometry() {
  const rect = el => {
    const r = el.getBoundingClientRect();
    return {x:r.x, y:r.y, width:r.width, height:r.height, right:r.right, bottom:r.bottom, cy:r.y + r.height / 2};
  };
  const field = document.querySelector('.searchfield'), button = document.querySelector('#analyze-search');
  const ring = getComputedStyle(field, '::before'), pill = getComputedStyle(button);
  const nav = document.querySelector('#cattabs');
  const joinText = document.createRange();
  joinText.selectNodeContents(document.querySelector('#subscribe').firstChild);
  return {
    header:rect(document.querySelector('header')), field:rect(field), nav:rect(nav),
    firstCardTop:(card => card ? card.getBoundingClientRect().top + scrollY : null)(document.querySelector('#content .card')),
    row:['.brand', '#drop', '#pwa-header-install', '#subscribe'].map(selector => ({selector,
      ...rect(document.querySelector(selector))})),
    tabs:[...nav.children].map(el => {const css = getComputedStyle(el); return {...rect(el),
      font:css.fontSize, border:css.borderTopWidth, radius:css.borderRadius, padding:css.padding,
      background:css.backgroundColor, active:el.classList.contains('active')};}), status:rect(document.querySelector('#tagline')),
    scrollWidth:document.documentElement.scrollWidth,
    style:{rainbow:ring.backgroundImage, ringWidth:ring.borderTopWidth, duration:ring.animationDuration,
      pill:pill.backgroundImage, color:pill.color, border:pill.borderTopWidth, radius:pill.borderRadius,
      padding:pill.padding, height:rect(button).height, top:rect(button).y - rect(field).y},
    why:getComputedStyle(document.querySelector('.join-why')).display,
    whyRect:rect(document.querySelector('.join-why')), joinText:rect(joinText),
    fade:getComputedStyle(nav).maskImage, overflow:getComputedStyle(nav).overflowX,
    wrap:getComputedStyle(nav).flexWrap, justify:getComputedStyle(nav).justifyContent,
    tabScroll:nav.scrollWidth, tabClient:nav.clientWidth,
  };
}

function assertDesktop(g, width, label) {
  assert(g.field.width >= 320 && g.field.width <= 940, label + ': usable flexible search with 40px more room');
  if (width < 1200) assert(g.field.width >= 320, label + ': compact desktop reserves a usable search track');
  for (const item of g.row) {
    assert(Math.abs(item.cy - g.field.cy) <= 2, label + ': centred ' + item.selector);
    assert(item.x >= 0 && item.right <= width, label + ': bounded ' + item.selector);
  }
  const ordered = [g.row[0], g.field, ...g.row.slice(1)];
  for (let i = 1; i < ordered.length; i++) {
    const gap = ordered[i].x - ordered[i - 1].right;
    assert(gap >= 15.9 && gap <= 24.1, `${label}: row gap ${gap}`);
  }
  assert.equal(g.row[1].height, g.field.height, label + ': timer matches search height');
  assert(g.nav.y >= g.field.bottom && g.nav.bottom <= g.header.bottom, label + ': second row inside header');
  assert(g.status.y >= g.nav.bottom && g.status.bottom <= g.header.bottom, label + ': status on its own visible line');
  assert.equal(g.fade, 'none', label + ': no hidden-scroll fade');
  assert.equal(g.overflow, 'visible', label + ': no scroll container');
  assert.equal(g.wrap, 'wrap', label + ': categories wrap');
  assert.equal(g.justify, 'center', label + ': centred categories');
  assert(g.tabScroll <= g.tabClient, label + ': no horizontal scroll, including classic Windows scrollbars');
  for (const tab of g.tabs) assert(tab.x >= g.nav.x - 1 && tab.right <= g.nav.right + 1 &&
    tab.y >= g.nav.y && tab.bottom <= g.nav.bottom + 1, label + ': every category visible');
  if (width <= 1280) assert(new Set(g.tabs.map(t => Math.round(t.y))).size >= 2, label + ': narrower desktop wraps');
  if (width >= 1500) {
    assert.notEqual(g.why, 'none', label + ': wide Join label');
    assert(g.whyRect.width > 0, label + ': wide Join label has width');
    assert(Math.abs(g.whyRect.y - g.joinText.y) <= 2, label + ': wide Join label on same line');
  } else {
    assert.equal(g.why, 'none', label + ': wide Join label');
  }
}

function assertTabs(g, label) {
  for (const tab of g.tabs) {
    assert.equal(tab.font, '14px', label + ': slightly larger tabs');
    assert.equal(tab.border, '1px', label + ': outlined tabs');
    assert.equal(tab.radius, '999px', label + ': rounded buttons');
    assert.equal(tab.padding, '8px 15px', label + ': room around tab labels');
    assert(tab.height >= 44, label + ': tab tap target');
  }
  const active = g.tabs.find(tab => tab.active), idle = g.tabs.find(tab => !tab.active);
  assert(active && idle && active.background !== idle.background, label + ': clearly filled active tab');
}

async function offerInstall(page) {
  await page.evaluate(() => {
    const event = new Event('beforeinstallprompt', {cancelable:true});
    event.prompt = () => Promise.resolve();
    event.userChoice = Promise.resolve({outcome:'dismissed'});
    dispatchEvent(event);
  });
  await page.locator('#pwa-header-install').waitFor({state:'visible'});
}

// Compare the feature with its own OFF render, keeping every other shipped flag ON.
async function assertPhoneTagline(browser, roots, fixture, inspect) {
  for (const width of [360, 390, 412, 430, 900, 1280]) for (const theme of ['light', 'dark']) {
    let baseline;
    for (const mode of ['taglineOff', 'on']) {
      const name = `tagline-${width}-${theme}-${mode}`;
      const context = await browser.newContext({viewport:{width, height:width === 360 ? 740 : 844},
        colorScheme:theme, isMobile:width < 900, hasTouch:width < 900, reducedMotion:'reduce', serviceWorkers:'block'});
      const errors = [], unexpected = [];
      await routeOffline(context, roots[mode], unexpected);
      await context.addInitScript(() => {
        localStorage.setItem('wp.install.engagedMs', '180000');
        addEventListener('beforeinstallprompt', e => {
          if (e.isTrusted) { e.preventDefault(); e.stopImmediatePropagation(); }
        });
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.clock.install({time:new Date(Date.parse(fixture.now) + 120000)});
      await page.goto('https://wowpriced.test/');
      await page.waitForFunction(() => document.querySelector('#content .card') &&
        /24 (?:live|verified) deals/i.test(document.querySelector('#tagline').innerText));
      await offerInstall(page);
      const g = await page.evaluate(geometry);
      const tag = page.locator('#tagline');
      const text = await tag.innerText();
      if (mode === 'taglineOff') baseline = {g, text};
      else {
        if (width >= 900) {
          assert.deepEqual(g, baseline.g, name + ': all desktop/tablet geometry unchanged');
          assert.equal(text, baseline.text, name + ': desktop/tablet wording unchanged');
          assert.equal(await page.locator('.tag-short').evaluate(el => getComputedStyle(el).display), 'none');
        } else {
          // Only the tagline can extend into the spare space before the timer.
          const {status, ...rest} = g, {status:oldStatus, ...oldRest} = baseline.g;
          assert.deepEqual(rest, oldRest, name + ': header/card/timer geometry unchanged');
          for (const key of ['x', 'y', 'height']) assert.equal(status[key], oldStatus[key], name + ': tagline ' + key);
          assert.equal(text.trim().toLowerCase(), '24 verified deals', name + ': only one visible count');
          for (const selector of ['.tag-lead', '.tag-count'])
            assert.equal(await page.locator(selector).evaluate(el => getComputedStyle(el).display), 'none');
          // roughCount currently caps at 1,000+; stress the approved 1,200+ fit example too.
          await page.locator('.tag-short').evaluate(el => {el.textContent = '1,200+ verified deals';});
          const fits = await tag.evaluate(el => ({scroll:el.scrollWidth, client:el.clientWidth,
            text:el.innerText, dot:el.querySelector('.live-dot').getAttribute('aria-hidden'),
            since:el.lastElementChild.id}));
          assert(fits.scroll <= fits.client, name + ': full four-digit count fits without ellipsis ' + JSON.stringify(fits));
          assert.equal(fits.text.trim().toLowerCase(), '1,200+ verified deals');
          assert.equal(fits.dot, 'true'); assert.equal(fits.since, 'tagsince');
          const timer = await page.locator('#drop').boundingBox();
          const textRight = await page.locator('.tag-short').evaluate(el => el.getBoundingClientRect().right);
          assert(textRight < timer.x, name + ': count stays clear of timer');
          assert(g.scrollWidth <= width, name + ': no document overflow');
          if ([360, 390, 412].includes(width)) {
            const audit = await page.evaluate(`(() => { ${inspect}\nreturn inspectPage(hitAreaMath()); })()`);
            assert.equal(audit.headerH, 182, name + ': mobile-audit headerH');
            assert.equal(audit.firstCardTop, 388, name + ': mobile-audit firstCardTop');
            assert.deepEqual(audit.overflow, [], name + ': mobile-audit overflow');
          }
        }
      }
      assert.deepEqual(errors, [], name + ': page errors');
      assert.deepEqual(unexpected, [], name + ': unexpected requests');
      await context.close();
    }
  }
}

async function routeOffline(context, root, unexpected) {
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.hostname === 'm.media-amazon.com') return route.fulfill({contentType:'image/svg+xml',
      body:'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320"><rect width="320" height="320" fill="#b9c6cd"/></svg>'});
    if (url.origin !== 'https://wowpriced.test') {
      unexpected.push(url.origin); return route.fulfill({status:204, body:''});
    }
    if (url.pathname === '/api/search') return route.fulfill({json:{items:[]}});
    const file = path.resolve(root, '.' + url.pathname, url.pathname.endsWith('/') ? 'index.html' : '');
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) {
      unexpected.push(url.pathname); return route.fulfill({status:404, body:''});
    }
    const types = {'.html':'text/html', '.js':'application/javascript', '.css':'text/css',
      '.json':'application/json', '.webmanifest':'application/manifest+json', '.svg':'image/svg+xml', '.png':'image/png'};
    return route.fulfill({path:file, contentType:types[path.extname(file)] || 'application/octet-stream'});
  });
}

// All existing header DOM boxes must match the ring-OFF run exactly. The ring
// is a decorative pseudo-element, so it adds no node or accessible content.
function headerBoxes() {
  return [...document.querySelectorAll('header,header *')].map(el => {
    const r = el.getBoundingClientRect();
    return {tag:el.tagName, id:el.id, cls:el.getAttribute('class'),
      x:r.x, y:r.y, width:r.width, height:r.height};
  });
}

function timerRingState() {
  const box = document.querySelector('#drop'), ring = getComputedStyle(box, '::before');
  const dial = document.querySelector('#dropdial');
  return {content:ring.content, background:ring.backgroundImage, mask:ring.maskComposite,
    padding:ring.padding, transition:ring.transitionDuration,
    countdown:document.querySelector('#countdown').textContent,
    fraction:Number(box.style.getPropertyValue('--pill-progress')),
    dialVisible:getComputedStyle(dial).display !== 'none',
    dialTransform:document.querySelector('#dhead').style.transform,
    dialColor:getComputedStyle(document.querySelector('#dhead')).backgroundColor,
    good:getComputedStyle(document.querySelector('.dtick path')).stroke};
}

async function timerRingAfterTick(page, first, enabled, name) {
  // tick's 1000ms interval calls paintClock for the digits, dial and pill.
  // Advance on every poll so timers and queued animation frames can run;
  // waiting in Node alone leaves them frozen at the previous clock step.
  for (let elapsed = 0; elapsed < 5000; elapsed += 250) {
    await page.clock.runFor(250);
    const state = await page.evaluate(timerRingState);
    if (state.countdown !== first.countdown && (!enabled || state.fraction > first.fraction)) return state;
  }
  assert.fail(name + ': digits and ring did not reflect the advanced clock');
}

async function assertTimerPillRing(browser, roots, fixture, shots) {
  const widths = [360, 390, 412, 430, 899, 900, 1023, 1024, 1100, 1199, 1200, 1280, 1440, 1512, 1599, 1600, 1920];
  for (const width of widths) for (const theme of ['light', 'dark']) {
    for (const motion of ['reduce', 'no-preference']) {
      let baseline;
      for (const mode of ['ringOff', 'on']) {
        const name = `ring-${width}-${theme}-${motion}-${mode}`;
        const context = await browser.newContext({viewport:{width, height:width < 900 ? 844 : 900},
          deviceScaleFactor:2, colorScheme:theme, isMobile:width < 900, hasTouch:width < 900,
          reducedMotion:motion, serviceWorkers:'block'});
        try {
          const errors = [], unexpected = [];
          await routeOffline(context, roots[mode], unexpected);
          await context.addInitScript(() => {
            localStorage.setItem('wp.install.engagedMs', '180000');
            addEventListener('beforeinstallprompt', e => {
              if (e.isTrusted) { e.preventDefault(); e.stopImmediatePropagation(); }
            });
          });
          const page = await context.newPage();
          page.on('pageerror', error => errors.push(error.message));
          await page.clock.install({time:new Date(Date.parse(fixture.now) + 120000)});
          await page.goto('https://wowpriced.test/');
          await page.locator('#content .card').first().waitFor();
          await offerInstall(page);
          await page.clock.pauseAt(new Date(Date.parse(fixture.now) + 123000));
          const boxes = await page.evaluate(headerBoxes);
          const first = await page.evaluate(timerRingState);
          const second = await timerRingAfterTick(page, first, mode === 'on', name);
          const tickBoxes = await page.evaluate(headerBoxes);
          const compact = width >= 900 && width < 1600;
          if (mode === 'ringOff') baseline = {boxes, first, phases:{}};
          else {
            if (compact) {
              assert.deepEqual(boxes, baseline.boxes, name + ': every header box unchanged (0px)');
              assert.deepEqual(tickBoxes, boxes, name + ': progress tick moves no header element (0px)');
            }
            else {
              // The dial's transformed children can sample a different millisecond;
              // compare all stable header boxes and its existing visibility instead.
              const stable = rows => rows.filter(r => !['dA', 'dB', 'dhead'].includes(r.id));
              assert.deepEqual(stable(boxes), stable(baseline.boxes), name + ': phone/wide header unchanged');
            }
            assert.equal(first.dialVisible, baseline.first.dialVisible, name + ': original dial visibility');
            assert.equal(first.dialVisible, width >= 1600, name + ': original dial breakpoint');
            if (first.dialVisible) assert.notEqual(second.dialTransform, first.dialTransform, name + ': existing dial still ticks');
            if (compact) {
              assert.equal(first.content, '""', name + ': visible pill outline');
              assert(first.mask.split(',').every(value => value.trim() === 'exclude'), name + ': only the rounded outline is painted');
              assert.equal(first.padding, '3px', name + ': stroke inside existing border');
              assert(first.background.startsWith('conic-gradient('), name + ': clockwise conic fill');
              assert(second.fraction > first.fraction, name + ': same fraction grows between ticks');
              assert.notEqual(second.background, first.background, name + ': visible fill grows');
              assert.equal(first.transition, '0s', name + ': per-second jumps, including reduced motion');
            } else assert.equal(first.content, 'none', name + ': ring absent outside compact desktop');
          }
          if (compact) {
            for (const phase of ['calm', 'warm', 'hot', 'final', 'dropping']) {
              await page.locator('#drop').evaluate((el, value) => {
                el.dataset.phase = value === 'dropping' ? 'final' : value;
                el.classList.toggle('dropping', value === 'dropping');
                // Finish the dial's existing finite transitions before comparing
                // its transformed tag/tick descendants in ordinary motion mode.
                for (const animation of el.getAnimations({subtree:true}))
                  if (animation.effect.getComputedTiming().iterations !== Infinity) animation.finish();
              }, phase);
              const state = await page.evaluate(timerRingState), phaseBoxes = await page.evaluate(headerBoxes);
              if (mode === 'ringOff') baseline.phases[phase] = phaseBoxes;
              else {
                assert.deepEqual(phaseBoxes, baseline.phases[phase], name + ': ' + phase + ' moves no header element (0px)');
                assert(state.background.includes(phase === 'dropping' ? state.good : state.dialColor),
                  name + ': ' + phase + ' matches the dial colour');
              }
            }
            await page.locator('#drop').evaluate(el => {el.classList.remove('dropping'); el.dataset.phase = 'calm';});
            if (mode === 'on') {
              const reset = await page.evaluate(timerRingState);
              assert(reset.background.includes(reset.dialColor), name + ': normal colour restored after drop');
            }
          }
          if (mode === 'on' && motion === 'reduce')
            await page.screenshot({path:path.join(shots, `ring-${width}-${theme}.png`)});
          if (mode === 'ringOff') assert.equal(first.content, 'none', name + ': flag OFF has no ring');
          assert.deepEqual(errors, [], name + ': page errors');
          assert.deepEqual(unexpected, [], name + ': unexpected requests');
        } finally { await context.close(); }
      }
    }
  }
}

async function main() {
  const {chromium} = require('playwright');
  const scratch = fs.mkdtempSync('/tmp/task305-browser-');
  const shots = path.resolve(process.argv[3] || 'docs/research/header');
  fs.mkdirSync(shots, {recursive:true});
  // Use the actual mobile-audit inspection and hit-area probes, without its live runner.
  const auditSource = fs.readFileSync('scripts/mobile_audit.cjs', 'utf8');
  const inspect = auditSource.slice(auditSource.indexOf('function hitAreaMath()'), auditSource.indexOf('async function runAudit()'));
  let browser;
  try {
    const roots = {off:buildFixture(path.join(scratch, 'off'), true, false), on:buildFixture(path.join(scratch, 'on')),
      taglineOff:buildFixture(path.join(scratch, 'tagline-off'), true, true, false),
      ringOff:buildFixture(path.join(scratch, 'ring-off'), true, true, true, false)};
    const fixture = JSON.parse(fs.readFileSync(path.join(scratch, 'on/fixture.json'), 'utf8'));
    browser = await chromium.launch({headless:true, executablePath:process.argv[2]});
    await assertTimerPillRing(browser, roots, fixture, shots);
    await assertPhoneTagline(browser, roots, fixture, inspect);
    const metrics = [];
    for (const width of [900, 1024, 1100, 1199, 1280, 1440, 1500, 1557, 1920, 2560, 899, 390, 360]) {
      for (const theme of ['light', 'dark']) {
        let original;
        const originalActions = new Map();
        for (const mode of ['off', 'on']) {
          const name = `${width}-${theme}-${mode}`;
          const context = await browser.newContext({viewport:{width, height:width === 360 ? 740 : width < 900 ? 844 : 900}, colorScheme:theme,
            isMobile:width < 900, hasTouch:width < 900, reducedMotion:'reduce', serviceWorkers:'block'});
          const errors = [], unexpected = [];
          await routeOffline(context, roots[mode], unexpected);
          await context.addInitScript(() => {
            localStorage.setItem('wp.install.engagedMs', '180000');
            addEventListener('beforeinstallprompt', e => {
              if (e.isTrusted) { e.preventDefault(); e.stopImmediatePropagation(); }
            });
          });
          const page = await context.newPage();
          page.setDefaultTimeout(10000);
          page.on('pageerror', error => errors.push(error.message));
          page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
          await page.clock.install({time:new Date(Date.parse(fixture.now) + 120000)});
          await page.goto('https://wowpriced.test/');
          await page.waitForFunction(() => !document.querySelector('#filtertoggle').disabled &&
            document.querySelector('#content .card') && /(?:live|verified) deals?/i.test(document.querySelector('#tagline').innerText));
          const beforeInstall = await page.locator('.searchfield').boundingBox();
          await offerInstall(page);
          assert.deepEqual(await page.locator('.searchfield').boundingBox(), beforeInstall, name + ': stable install slot');
          const g = await page.evaluate(geometry);
          await assertAnalyzeGeometry(page);
          if (mode === 'off') original = g;
          else {
            for (const key of ['rainbow', 'ringWidth', 'duration'])
              assert.equal(g.style[key], original.style[key], name + ': rainbow field preserved');
            await assertDiscounts(page, false);
            assert(g.scrollWidth <= width, name + ': no document overflow');
            if (width < 900) assert(Math.max(...g.tabs.map(t => t.y)) - Math.min(...g.tabs.map(t => t.y)) <= 1, name + ': phone tab row unchanged');
            assertTabs(g, name);
            if (width >= 900) assertDesktop(g, width, name);
            const record = {name, geometry:g};
            if (width === 390 || width === 360) {
              const audit = await page.evaluate(`(() => { ${inspect}\nreturn inspectPage(hitAreaMath()); })()`);
              assert(audit.headerH <= 196, name + ': headerH ' + audit.headerH);
              assert(audit.firstCardTop !== null && audit.firstCardTop <= 402, name + ': firstCardTop ' + audit.firstCardTop);
              assert.deepEqual(audit.overflow, [], name + ': overflow');
              assert.deepEqual(audit.smallTaps, [], name + ': 44px hit areas');
              assert.deepEqual(audit.smallInputs, [], name + ': input type size');
              assert(g.header.height <= original.header.height, name + ': removing hint offsets taller field');
              assert.equal(g.field.width, original.field.width, name + ': phone field width unchanged');
              assert.equal(g.field.x, original.field.x, name + ': phone field stays aligned');
              assert.equal(g.field.y, original.field.y, name + ': phone field row unchanged');
              assert.equal(g.field.height, 56, name + ': 44px pill plus two 6px gaps');
              assert(g.firstCardTop <= original.firstCardTop, name + ': first card stays at least as high');
              record.mobile = audit;
            }
            await page.screenshot({path:path.join(shots, `${width}-${theme}.png`)});
            // Every desktop category is visible; phone scroll remains keyboard reachable.
            const last = page.locator('#cattabs .cattab').last();
            await last.focus();
            assert(await last.evaluate(el => {
              const r = el.getBoundingClientRect(), n = el.parentElement.getBoundingClientRect();
              return r.x >= n.x - 1 && r.right <= n.right + 1;
            }), name + ': last tab reachable');
            const all = page.locator('#cattabs .cattab', {hasText:'All deals'});
            await all.click();
            assert(await all.evaluate(el => el.classList.contains('active')), name + ': category still filters');
            await page.locator('#q').fill('kettle');
            await page.waitForFunction(() => document.querySelector('#searchcount').textContent.includes('24'));
            await assertAnalyzeGeometry(page);
            await assertDiscounts(page, true);
            if (width >= 900) assertDesktop(await page.evaluate(geometry), width, name + '-search');
            await page.locator('#clearsearch').click();
            await assertDiscounts(page, false);
            if (width < 900) {
              const cleared = await page.evaluate(geometry);
              assert.equal(cleared.header.height, g.header.height, name + ': clear restores polished phone header');
            }
            if (width >= 900) {
              await page.locator('#q').fill('cofee');
              await page.waitForFunction(() => document.querySelector('#q').value === 'coffee' &&
                !document.querySelector('#search-spelling').hidden);
              assertDesktop(await page.evaluate(geometry), width, name + '-spelling');
              const undo = page.locator('[data-search-original]');
              const beforeBlur = await undo.boundingBox();
              await page.locator('#q').evaluate(el => el.blur());
              assert.deepEqual(await undo.boundingBox(), beforeBlur, name + ': stable spelling undo on blur');
              assertDesktop(await page.evaluate(geometry), width, name + '-spelling-blurred');
              await undo.click();
              assert.equal(await page.locator('#q').inputValue(), 'cofee', name + ': spelling undo remains clickable');
            }
            metrics.push(record);
          }
          // Other page writers retain their own header and install/Join actions.
          if ([390, 1440].includes(width)) for (const url of fixture.pages) {
            await page.goto('https://wowpriced.test' + url);
            await page.locator('header').waitFor();
            assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), name + ': shared page ' + url);
            const actions = {
              subscribe:await page.locator('header #subscribe').count(),
              install:await page.locator('header #pwa-header-install').count(),
            };
            if (mode === 'off') originalActions.set(url, actions);
            else {
              assert.deepEqual(actions, originalActions.get(url), name + ': shared header actions unchanged ' + url);
              await page.screenshot({path:path.join(shots, `${width}-${theme}-${url.startsWith('/deal/') ? 'deal' : 'event'}.png`)});
            }
          }
          assert.deepEqual(errors, [], name + ': browser errors');
          assert.deepEqual(unexpected, [], name + ': unexpected requests');
          await context.close();
        }
      }
    }
    fs.writeFileSync(path.join(shots, 'metrics.json'), JSON.stringify(metrics, null, 2) + '\n');
    console.log('PASS: twin ovals, wrapped desktop tabs, phone load/reveal audit, shared pages and light/dark');
  } finally {
    if (browser) await browser.close();
    fs.rmSync(scratch, {recursive:true, force:true});
  }
}

module.exports = {buildFixture};
if (require.main === module) main().catch(error => {console.error(error); process.exitCode = 1;});
