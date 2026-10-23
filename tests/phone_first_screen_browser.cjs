// phone_first_screen (operator 2026-10-23): native Chromium, every request fulfilled offline.
// node tests/phone_first_screen_browser.cjs <off site> <on site> [chromium] [screenshot dir]
// Prints one JSON line (first-screen counts per size, OFF and ON); exit 77 = no browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pixels} = require('../scripts/browser_pixels.cjs');

const ORIGIN = 'https://wowpriced.test';
const NOW = Date.parse('2026-10-16T12:00:00Z');  // tests.phone_budget_fixture.NOW: the publication's own drop
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1cAAAAASUVORK5CYII=', 'base64');
const TYPES = {'.html': 'text/html', '.json': 'application/json', '.js': 'application/javascript', '.css': 'text/css',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.webmanifest': 'application/manifest+json'};

function watchStartup() {
  window.__cls = 0;
  new PerformanceObserver(list => {
    for (const entry of list.getEntries()) if (!entry.hadRecentInput) window.__cls += entry.value;
  }).observe({type: 'layout-shift', buffered: true});
  document.addEventListener('DOMContentLoaded', () => {
    window.__first = [...document.querySelectorAll('#content .card')].slice(0, 3);
  });
}

function measure() {
  const vh = innerHeight;
  const box = el => { const r = el.getBoundingClientRect(); return {top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width, height: r.height}; };
  const shown = el => !!el && el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) && el.getBoundingClientRect().height > 0;
  const inView = r => r.top >= 0 && r.bottom <= vh + .5;
  const luminance = color => {
    const rgb = color.match(/[\d.]+/g).slice(0, 3).map(v => v / 255);
    return rgb.reduce((sum, v, i) => sum + [.2126, .7152, .0722][i] * (v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4), 0);
  };
  const contrast = (a, b) => { const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p); return (x + .05) / (y + .05); };
  const words = card => {
    let text = '';
    const walk = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
    for (let node; (node = walk.nextNode());)
      if (!node.parentElement.closest('.sr-only,[hidden]') && shown(node.parentElement)) text += ' ' + node.nodeValue;
    return text.trim().split(/\s+/).filter(Boolean).length;
  };
  const cards = [...document.querySelectorAll('#content .card')].slice(0, 4).map(card => {
    const buy = card.querySelector('.getdeal'), steps = card.querySelector('.deal-steps'), how = card.querySelector('.how');
    const media = card.querySelector('.media').getBoundingClientRect();
    const hit = document.elementFromPoint(media.left + media.width / 2, media.top + Math.min(media.height / 2, 40));
    const stepRect = shown(steps) ? box(steps) : null;
    const bg = getComputedStyle(card).backgroundColor;
    return {card: box(card), fully: inView(box(card)), buy: shown(buy) ? box(buy) : null,
      buyFully: shown(buy) && inView(box(buy)), words: words(card),
      price: shown(card.querySelector('.now')), store: shown(card.querySelector('.storechip')),
      credit: shown(card.querySelector('.credit')), headline: shown(card.querySelector('.headline')),
      badge: shown(card.querySelector('.badge')), how: shown(how), hasHow: !!how, steps: stepRect,
      stepsHref: steps && steps.getAttribute('href'), title: card.querySelector('.title a')?.getAttribute('href'),
      stepsTap: steps ? getComputedStyle(steps, '::after').minHeight : null,
      stepsHit: stepRect ? document.elementFromPoint(stepRect.left + stepRect.width / 2, stepRect.top + stepRect.height / 2) === steps : null,
      photoTapsBuy: !!hit && !!hit.closest('.getdeal') && hit.closest('.card') === card,
      bg, dark: luminance(bg) < .2,
      titleContrast: contrast(getComputedStyle(card.querySelector('.title')).color, bg),
      priceContrast: contrast(getComputedStyle(card.querySelector('.now')).color, bg),
      stepsContrast: stepRect ? contrast(getComputedStyle(steps).color, bg) : null};
  });
  const tools = [...document.querySelectorAll('header .tool-buttons a')].map(a => ({...box(a), clipped: a.scrollWidth > a.clientWidth + 1}));
  const nav = document.querySelector('#cattabs'), css = getComputedStyle(nav);
  return {cards, cardsFully: cards.filter(c => c.fully).length, buysFully: cards.filter(c => c.buyFully).length,
    firstCardTop: cards[0].card.top, scrollWidth: document.documentElement.scrollWidth, tools,
    search: box(document.querySelector('.searchfield')), q: box(document.querySelector('#q')),
    tabs: {...box(nav), wrap: css.flexWrap, overflow: css.overflowX,
      minTab: Math.min(...[...nav.querySelectorAll('.cattab')].map(t => t.getBoundingClientRect().height))},
    cls: window.__cls, kept: (window.__first || []).length === 3 && window.__first.every((node, i) =>
      node.isConnected && node === document.querySelectorAll('#content .card')[i])};
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

async function open(browser, root, width, height, scheme) {
  const context = await browser.newContext({viewport: {width, height}, isMobile: width < 900, hasTouch: width < 900,
    deviceScaleFactor: 1, colorScheme: scheme, reducedMotion: 'reduce', serviceWorkers: 'block'});
  await context.clock.setFixedTime(NOW);
  const errors = [];
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url());
    if (request.resourceType() === 'image' && url.origin !== ORIGIN) return route.fulfill({contentType: 'image/png', body: PNG});
    if (url.origin !== ORIGIN || request.method() !== 'GET') return route.abort();
    let file = path.resolve(root, '.' + decodeURIComponent(url.pathname));
    if (!file.startsWith(root + path.sep) && file !== root) return route.abort();
    if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
    if (!fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
    return route.fulfill({contentType: TYPES[path.extname(file)] || 'application/octet-stream', body: fs.readFileSync(file)});
  });
  await context.addInitScript(watchStartup);
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(ORIGIN + '/', {waitUntil: 'load'});
  // The drop module's first render (keep_first_cards patches the server's cards in place).
  await page.waitForFunction(() => document.querySelector('#content .card[data-key]'), null, {timeout: 30000});
  await page.waitForTimeout(500);
  return {context, page, errors};
}

(async () => {
  let chromium;
  try { ({chromium} = require('playwright')); }
  catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; console.log('Playwright is not installed'); process.exitCode = 77; return; }
  const [off, on] = process.argv.slice(2, 4).map(dir => path.resolve(dir));
  const executablePath = process.argv[4] || '/opt/pw-browsers/chromium', shots = process.argv[5];
  let browser;
  try { browser = await chromium.launch({headless: true, executablePath}); }
  catch (error) {
    if (!/Operation not permitted|Executable doesn't exist/.test(error.message)) throw error;
    console.log('Chromium sandbox launch denied or no installed browser'); process.exitCode = 77; return;
  }
  const report = {};
  try {
    // Phones: [width, height, ON must show at least this many cards with their Get deal button].
    for (const [width, height, least] of [[390, 844, 3], [375, 667, 2], [430, 932, 3], [360, 740, 2]]) {
      for (const scheme of width === 390 ? ['light', 'dark'] : ['light']) {
        const label = `${width}x${height}-${scheme}`, seen = {};
        for (const [mode, root] of [['off', off], ['on', on]]) {
          const {context, page, errors} = await open(browser, root, width, height, scheme);
          const m = await page.evaluate(measure);
          if (shots) await page.screenshot({path: path.join(shots, `${mode}-${label}.png`), animations: 'disabled', caret: 'hide'});
          await context.close();
          assert.deepEqual(errors, [], label + ' ' + mode + ': no page errors');
          assert(m.kept, label + ' ' + mode + ': the first three server cards stay (keep_first_cards)');
          assert(m.cls < .001, `${label} ${mode}: no layout shift (${m.cls})`);
          assert.equal(m.scrollWidth, width, label + ' ' + mode + ': no sideways scroll');
          seen[mode] = m;
        }
        const {off: o, on: n} = seen;
        report[label] = {off: {cards: o.cardsFully, buttons: o.buysFully, firstCardTop: o.firstCardTop, words: o.cards.slice(0, 3).map(c => c.words)},
                         on: {cards: n.cardsFully, buttons: n.buysFully, firstCardTop: n.firstCardTop, words: n.cards.slice(0, 3).map(c => c.words)}};
        // The operator's goal: the first deals and their buttons on the first screen, fewer words.
        assert(n.buysFully >= least && n.cardsFully >= least, `${label}: ${n.buysFully} buttons / ${n.cardsFully} cards on the first screen`);
        assert(n.cards.slice(0, least).every(c => c.fully && c.buyFully), label + ': the FIRST cards, in order');
        assert(n.buysFully > o.buysFully, label + ': more Get deal buttons than before');
        assert(n.cards.slice(0, 3).every((c, i) => c.words < o.cards[i].words), label + ': fewer words on each card');
        // Kept on every card: price, store, credit, Get deal (taps on the card still open it).
        for (const c of n.cards.slice(0, 3)) {
          assert(c.price && c.store && c.credit && c.buy, label + ': price, store, credit and Get deal stay');
          assert(c.photoTapsBuy, label + ': the card tap still goes through Get deal (deal_click)');
          assert(c.buy.height >= 44 && c.buy.width >= 44, label + ': Get deal tap target');
          assert(!c.headline || !c.badge, label + ': the headline goes only where the badge says how it was verified');
          if (c.hasHow && !c.how) {
            assert(c.steps && c.stepsHref === c.title && c.stepsHref.startsWith('/deal/'), label + ': a hidden How box leaves its link to the deal page');
            assert(c.stepsHit && c.stepsTap === '44px', label + ': the link takes the tap, 44px target');
            assert(c.stepsContrast >= 4.5, label + ': link contrast ' + c.stepsContrast);
          }
          assert(c.titleContrast >= 4.5 && c.priceContrast >= 4.5, label + ': text contrast');
          assert.equal(c.dark, scheme === 'dark', label + ': card colours follow the theme');
        }
        assert(n.cards.slice(0, 3).some(c => c.steps), label + ': the fixture covers a step deal');
        assert(o.cards.every(c => !c.steps), label + ' off: no link');
        // Above the deals: the search box and the scrolling tab row stay, tool buttons in one row.
        assert(n.search.bottom <= n.firstCardTop && n.q.height >= 44, label + ': search box above the deals');
        assert(n.tabs.bottom <= n.firstCardTop && n.tabs.minTab >= 44, label + ': category tabs above the deals');
        assert.equal(n.tabs.wrap, 'nowrap'); assert.equal(n.tabs.overflow, 'auto');
        assert.equal(n.tools.length, 2); assert.equal(n.tools[0].top, n.tools[1].top, label + ': tool buttons side by side');
        assert(n.tools.every(t => t.height >= 44 && !t.clipped), label + ': whole tool button labels, 44px');
      }
    }
    // Laptops: the same pixels with the switch on, light and dark.
    for (const scheme of ['light', 'dark']) {
      const images = [];
      for (const root of [off, on]) {
        const {context, page, errors} = await open(browser, root, 1280, 800, scheme);
        await page.mouse.move(1279, 799);
        images.push(await still(page));
        assert.deepEqual(errors, []);
        await context.close();
      }
      // Two loads of the very same page can differ by one colour level in a few
      // rasterized pixels (seen OFF vs OFF); anything more is a real change.
      const [a, b] = images;
      assert.equal(a.data.length, b.data.length);
      let changed = 0;
      for (let i = 0; i < a.data.length; i++) if (Math.abs(a.data[i] - b.data[i]) > 2) changed++;
      assert.equal(changed, 0, '1280x800 ' + scheme + ': laptop pixels unchanged');
    }
    report['1280x800'] = 'unchanged (light and dark)';
  } finally { await browser.close(); }
  console.log(JSON.stringify(report));
})().catch(error => { console.error(error); process.exitCode = 1; });
