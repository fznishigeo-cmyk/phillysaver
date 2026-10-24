// short_cards + best_today_phone_cards (operator 2026-10-24): native Chromium, every request fulfilled offline.
// node tests/short_cards_browser.cjs <off site> <short_cards site> <best_today_phone_cards site> <both site>
// Prints one JSON line (first-screen counts of /best-deals-today/ per phone size, Get deal sizes); exit 77 = no browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pixels} = require('../scripts/browser_pixels.cjs');

const ORIGIN = 'https://wowpriced.test';
const NOW = Date.parse('2026-10-16T12:00:00Z');  // tests.phone_budget_fixture.NOW
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1cAAAAASUVORK5CYII=', 'base64');
const TYPES = {'.html': 'text/html', '.json': 'application/json', '.js': 'application/javascript', '.css': 'text/css',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.webmanifest': 'application/manifest+json'};
const BEST = '/best-deals-today/';

async function measure(plainOnly) {
  const doc = await (await fetch('/deals.json')).json();
  const byKey = new Map(), byPath = new Map();
  for (const d of doc.deals) { byKey.set(d.key, d); if (d.deal_path) byPath.set(d.deal_path, d); }
  // Lay out every card (site.py OFFSCREEN_CARDS_STYLE skips the phone wall's 9th card onward until it nears the screen).
  document.head.insertAdjacentHTML('beforeend', '<style>.card{content-visibility:visible!important}</style>');
  const vh = innerHeight;
  const shown = el => !!el && el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) && el.getBoundingClientRect().height > 0;
  const inView = r => r.top >= 0 && r.bottom <= vh + .5;
  const visibleText = card => {
    let text = '';
    const walk = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
    for (let node; (node = walk.nextNode());)
      if (!node.parentElement.closest('.sr-only,[hidden]') && shown(node.parentElement)) text += ' ' + node.nodeValue;
    return text;
  };
  const deal = card => [...document.querySelectorAll('.card')].filter(c => c.querySelector('.body .now'));
  // "When none is a step deal": the plain cards alone, in order (no steps, code or How box).
  if (plainOnly) deal().forEach(card => card.querySelector('.how,.how-line,.code-line,.deal-steps') && card.remove());
  const lines = el => Math.round(el.getBoundingClientRect().height / parseFloat(getComputedStyle(el).lineHeight) * 10) / 10;
  return {scrollWidth: document.documentElement.scrollWidth, cards: deal().map((card, i) => {
    const title = card.querySelector('.title'), link = title.querySelector('a') || title;
    const d = byKey.get(card.getAttribute('data-key')) || byPath.get(link.getAttribute('href')) || {};
    const box = card.getBoundingClientRect(), buy = card.querySelector('.getdeal');
    const media = card.querySelector('.media').getBoundingClientRect();
    const body = card.querySelector('.body').getBoundingClientRect(), t = title.getBoundingClientRect();
    const hit = (x, y) => { const el = document.elementFromPoint(x, y); return el && el.closest('.card') === card ? el : null; };
    const on = r => r.top >= 4 && r.bottom <= vh - 4;  // that part of the card is on screen
    const b = buy && buy.getBoundingClientRect();
    // Get deal's tap target: the height around its middle where a tap still lands on it (30px each way at most).
    const reach = dir => { let n = 0; while (n < 30 && hit(b.left + 6, b.top + b.height / 2 + dir * (n + .25))?.closest('.getdeal') === buy) n += .25; return n; };
    const codeLine = card.querySelector('.code-line'), howLine = card.querySelector('.how-line'), text = visibleText(card);
    const codes = (d.codes || []).filter(Boolean);
    return {i, fully: inView(box), buyFully: shown(buy) && inView(b),
      headline: shown(card.querySelector('.headline')), badge: !!card.querySelector('.badge'),
      titleLines: lines(title), fullTitleLines: Math.round(link.scrollHeight / parseFloat(getComputedStyle(link).lineHeight) * 10) / 10,
      // Kept on every card: price, store, credit, Get deal; the badge, How box (disclosures), "Price not confirmed",
      // price age and checkout-price line wherever the card has them.
      missing: ['.now', '.storechip', '.credit', '.getdeal', '.badge', '.how', '.price-unconfirmed', '.price-aged',
        '.checkout-price'].filter(s => card.querySelector(s) && !shown(card.querySelector(s)) &&
        !(s === '.how' && shown(card.querySelector('.deal-steps')))),
      codes, codeLine: shown(codeLine) ? codeLine.textContent : null,
      codeOnce: codes.every(c => text.split(c).length === 2), codeButtons: codeLine ? codeLine.querySelectorAll('button.code').length : 0,
      howLine: shown(howLine) ? lines(howLine) : null,
      needsSteps: !!(d.needs_steps || (d.checkout || {}).card) && (d.how || []).some(s => !/\bcode\b/i.test(s)),
      dealSteps: shown(card.querySelector('.deal-steps')),
      left: Math.round(box.left), width: Math.round(box.width), mediaWidth: Math.round(media.width),
      mediaLeftOfBody: media.right <= body.left + .5,
      buyHeight: b ? Math.round(b.height * 10) / 10 : 0,
      buyTap: b && b.top >= 30 && b.bottom <= vh - 30 ? Math.round(reach(-1) + reach(1) + .25) : null,
      // Taps: on screen, the card's photo goes through Get deal (card_click_1020); the title link
      // takes its own 44px target, even just outside its clamped text.
      photoTapsBuy: media.top >= 0 && media.top + 80 <= vh
        ? !!hit(media.left + media.width / 2, media.top + Math.min(media.height / 2, 40))?.closest('.getdeal') : null,
      titleTap: on(t) ? hit(t.left + Math.min(t.width / 2, 40), t.top + t.height / 2) === link : null,
      titleTapAbove: on(t) && innerWidth <= 640 ? hit(t.left + Math.min(t.width / 2, 40), t.top - 2) === link : null,
      titleTarget: innerWidth <= 640 && link !== title ? getComputedStyle(link, '::after').minHeight : null};
  })};
}

async function open(browser, root, url, width, height) {
  const context = await browser.newContext({viewport: {width, height}, isMobile: width < 900, hasTouch: width < 900,
    deviceScaleFactor: 1, colorScheme: 'light', reducedMotion: 'reduce', serviceWorkers: 'block'});
  await context.clock.setFixedTime(NOW);
  const errors = [];
  await context.route('**/*', route => {
    const request = route.request(), u = new URL(request.url());
    if (request.resourceType() === 'image' && u.origin !== ORIGIN) return route.fulfill({contentType: 'image/png', body: PNG});
    if (u.origin !== ORIGIN || request.method() !== 'GET') return route.abort();
    let file = path.resolve(root, '.' + decodeURIComponent(u.pathname));
    if (!file.startsWith(root + path.sep) && file !== root) return route.abort();
    if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
    if (!fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
    return route.fulfill({contentType: TYPES[path.extname(file)] || 'application/octet-stream', body: fs.readFileSync(file)});
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(ORIGIN + url, {waitUntil: 'load'});
  if (url === '/') await page.waitForFunction(() => document.querySelector('#content .card[data-key]'), null, {timeout: 30000});
  await page.waitForTimeout(400);
  return {context, page, errors};
}

// Two equal captures in a row: a transition still running is not a difference.
async function still(page) {
  let last = await page.screenshot({animations: 'disabled', caret: 'hide'});
  for (let i = 0; i < 20; i++) {
    await page.waitForTimeout(250);
    const next = await page.screenshot({animations: 'disabled', caret: 'hide'});
    if (next.equals(last)) return pixels(next);
    last = next;
  }
  throw new Error('the page never settled');
}

const median = values => values.slice().sort((a, b) => a - b)[Math.floor(values.length / 2)];

(async () => {
  let chromium;
  try { ({chromium} = require('playwright')); }
  catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; console.log('Playwright is not installed'); process.exitCode = 77; return; }
  const [off, short, phone, both] = process.argv.slice(2, 6).map(dir => path.resolve(dir));
  let browser;
  try { browser = await chromium.launch(); }
  catch (error) {
    if (!/Operation not permitted|Executable doesn't exist/.test(error.message)) throw error;
    console.log('Chromium sandbox launch denied or no installed browser'); process.exitCode = 77; return;
  }
  const report = {short_cards: {}, first_screen: {}};
  try {
    // 1. short_cards: no headline sentence, two-line titles, the steps line, "Enter Code: X at checkout",
    //    a smaller Get deal with its tap target; everything else stays.
    for (const url of ['/', BEST]) {
      for (const [width, height] of [[1440, 900], [390, 844]]) {
        const label = `${url} ${width}x${height}`, seen = {};
        for (const [mode, root] of [['off', off], ['short', short], ['both', both]]) {
          const {context, page, errors} = await open(browser, root, url, width, height);
          seen[mode] = await page.evaluate(measure, false);
          if (mode === 'short' && width < 900) {
            // Without card_click_1020's whole-card target, Get deal's own ::after keeps 44px.
            await page.evaluate(() => {
              document.querySelectorAll('link[href^="/card-click.css"]').forEach(l => l.remove());
              document.querySelector('.card .getdeal').scrollIntoView({block: 'center'});
            });
            seen.ownTap = (await page.evaluate(measure, false)).cards.filter(c => c.buyTap !== null).map(c => c.buyTap);
          }
          await context.close();
          assert.deepEqual(errors, [], label + ' ' + mode + ': no page errors');
          assert.equal(seen[mode].scrollWidth, width, label + ' ' + mode + ': no sideways scroll');
        }
        const {off: o, short: s, both: b} = seen;
        assert(o.cards.length >= 20 && s.cards.length === o.cards.length && b.cards.length === o.cards.length, label + ': same cards');
        assert(o.cards.some(c => c.headline), label + ' off: the fixture shows headlines');
        if (url === BEST && width > 900) assert(o.cards.some(c => c.titleLines > 2), label + ' off: the fixture has long titles');
        for (const m of [s, b]) {
          assert(m.cards.some(c => c.codes.length > 1) && m.cards.some(c => c.needsSteps && !c.codes.length), label + ': fixture covers codes and steps');
          for (const c of m.cards) {
            const at = `${label}: card ${c.i}`;
            assert(!c.headline, at + ' shows no headline');
            assert(c.titleLines <= 2.05, `${at} title in ${c.titleLines} lines`);
            assert.deepEqual(c.missing, [], at + ' keeps price, store, badge, How box, disclosures, Get deal and credit');
            assert.equal(c.codeLine, c.codes.length ? `Enter Code: ${c.codes.join(', ')} at checkout` : null, at + ': the code line');
            assert(c.codeOnce, at + ': each code once');
            if (url === '/' && c.codes.length) assert.equal(c.codeButtons, c.codes.length, at + ': codes copy on tap');
            if (c.needsSteps) assert(c.howLine !== null && c.howLine <= 2.05, at + ': steps visible, two lines at most');
            assert(!c.dealSteps, at + ': the steps line replaces the "How to get this price" link');
            assert(c.buyHeight < 34, `${at}: smaller Get deal (${c.buyHeight}px)`);
            if (width < 900) {
              if (c.buyTap !== null) assert(c.buyTap >= 44, `${at}: Get deal tap target ${c.buyTap}px`);
              if (c.photoTapsBuy !== null) assert(c.photoTapsBuy, at + ': photo tap goes through Get deal');
              if (c.titleTap !== null) assert(c.titleTap && c.titleTapAbove, at + ': title link takes its 44px target');
              if (c.titleTarget !== null) assert.equal(c.titleTarget, '44px');
            }
          }
        }
        assert(o.cards.every(c => c.buyHeight >= 35), label + ' off: the bigger Get deal before');
        if (width < 900) {
          assert(seen.ownTap.length >= 1 && seen.ownTap.every(h => h >= 44), label + ': Get deal ::after keeps 44px ' + seen.ownTap);
          assert(s.cards.some(c => c.titleTapAbove) && b.cards.some(c => c.titleTapAbove), label + ': taps checked');
        }
        if (url === BEST) assert(s.cards.some(c => c.fullTitleLines > 2.05), label + ': a long title is clamped');
        report.short_cards[label] = {cards: s.cards.length, headlines: {off: o.cards.filter(c => c.headline).length, on: 0},
          maxTitleLines: {off: Math.max(...o.cards.map(c => c.titleLines)), on: Math.max(...s.cards.map(c => c.titleLines))},
          codeLines: s.cards.filter(c => c.codeLine).length, stepLines: s.cards.filter(c => c.howLine !== null).length,
          getDeal: {off: median(o.cards.map(c => c.buyHeight)), on: median(s.cards.map(c => c.buyHeight)),
            ...(width < 900 ? {tapOn: Math.min(...s.cards.filter(c => c.buyTap !== null).map(c => c.buyTap)),
              tapWithoutCardClick: Math.min(...seen.ownTap)} : {})}};
      }
    }
    // 2. best_today_phone_cards: the home page's phone cards on /best-deals-today/, first-screen counts.
    for (const [width, height] of [[390, 844], [375, 667], [430, 932]]) {
      const label = `${width}x${height}`, seen = {};
      for (const [mode, root] of [['off', off], ['phone', phone], ['both', both], ['plain', both]]) {
        const {context, page, errors} = await open(browser, root, BEST, width, height);
        seen[mode] = await page.evaluate(measure, mode === 'plain');
        await context.close();
        assert.deepEqual(errors, [], label + ' ' + mode + ': no page errors');
        assert.equal(seen[mode].scrollWidth, width, label + ' ' + mode + ': no sideways scroll');
      }
      const count = m => m.cards.filter(c => c.fully && c.buyFully).length;
      report.first_screen[label] = Object.fromEntries(Object.entries(seen).map(([k, m]) => [k, count(m)]));
      for (const mode of ['phone', 'both']) {
        const m = seen[mode], first = m.cards.slice(0, 6);
        assert(first.every(c => c.left === first[0].left && c.width === first[0].width), `${label} ${mode}: one deal per row`);
        assert(first.every(c => Math.abs(c.mediaWidth - 96) <= 1 && c.mediaLeftOfBody), `${label} ${mode}: photo on the left, 96px`);
        assert(m.cards.every(c => c.titleLines <= 2.05), `${label} ${mode}: titles in two lines`);
        assert(m.cards.every(c => !c.headline || !c.badge), `${label} ${mode}: no headline where the badge says how it was verified`);
        if (mode === 'phone') assert(m.cards.some(c => c.headline && !c.badge), label + ': cards without a badge keep it');
        for (const c of m.cards) {
          assert(!c.missing.length, `${label} ${mode}: card ${c.i} keeps price, store, How box, Get deal and credit`);
          if (c.buyTap !== null) assert(c.buyTap >= 44, `${label} ${mode}: card ${c.i} Get deal tap target`);
          if (c.photoTapsBuy !== null) assert(c.photoTapsBuy, `${label} ${mode}: card ${c.i} photo tap goes through Get deal`);
          if (c.titleTap !== null) assert(c.titleTap && c.titleTapAbove, `${label} ${mode}: card ${c.i} title link takes its 44px target`);
        }
        assert(count(m) >= count(seen.off), `${label} ${mode}: no fewer whole cards on the first screen`);
        assert(m.cards.filter(c => c.buyTap !== null).length >= 1, `${label} ${mode}: the taps were checked on the first screen`);
      }
      assert(seen.off.cards[0].width < width / 2, label + ' off: two columns before');
    }
    // The operator's goal: 3 whole cards with their Get deal at 390x844 when none is a step deal.
    assert(report.first_screen['390x844'].plain >= 3, '390x844: 3 plain cards on the first screen');
    assert(report.first_screen['390x844'].phone > report.first_screen['390x844'].off);
    // 3. Laptops: best_today_phone_cards changes no pixel of /best-deals-today/ (short_cards off and on).
    for (const [a, b, name] of [[off, phone, 'short_cards off'], [short, both, 'short_cards on']]) {
      const images = [];
      for (const root of [a, b]) {
        const {context, page, errors} = await open(browser, root, BEST, 1440, 900);
        await page.mouse.move(1439, 899);
        images.push(await still(page));
        assert.deepEqual(errors, []);
        await context.close();
      }
      const [x, y] = images, channels = x.data.length / (x.width * x.height);
      assert.equal(x.data.length, y.data.length);
      const changed = new Set();
      for (let i = 0; i < x.data.length; i++) if (Math.abs(x.data[i] - y.data[i]) > 2) changed.add(Math.floor(i / channels));
      // Two loads can antialias a rounded corner (the % flag) differently in a pixel or two;
      // any real change of the laptop layout moves thousands.
      assert(changed.size <= 4, '1440x900 ' + name + ': best_today_phone_cards leaves the laptop page unchanged (' + changed.size + ' px)');
    }
    report.laptop = 'best_today_phone_cards: 1440x900 unchanged (short_cards off and on)';
  } finally { await browser.close(); }
  console.log(JSON.stringify(report));
})().catch(error => { console.error(error); process.exitCode = 1; });
