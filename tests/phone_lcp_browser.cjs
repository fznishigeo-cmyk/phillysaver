// Offline first-paint and DOM identity probe; uses only installed Chromium.
// Local image placeholders isolate startup work; this is not a Lighthouse
// reproduction or a measurement of production image/network latency.
// node tests/phone_lcp_browser.cjs <site-directory> [chromium-path]
//   [--assert-preserved] [--deals-delay=1200] [--trace=/tmp/phone-lcp.json]
//   [--now=2026-10-16T12:20:00Z]  (the visitor's clock; later than the publication = stale mark)
// Exit 77: no browser here (not a pass). tests/test_keep_first_cards.py runs it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

function observeStartup(now) {
  const NativeDate = Date;
  window.Date = class extends NativeDate {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  };
  const result = window.phoneLcp = {paint: {}, lcp: [], longTasks: [], mutations: [],
    firstCardVisible: null, hydratedAt: null, originals: [], firstScreen: []};
  const originals = [], parents = [], firstScreen = [], observed = new Set(), parsed = new WeakSet();
  let content, parsing = true;
  const key = node => node.dataset.key || node.querySelector('[data-click-key]')?.dataset.clickKey || '';
  const describe = node => node ? {tag: node.tagName, class: node.className,
    text: (node.textContent || '').trim().slice(0, 140), key: key(node.closest('.card') || node)} : null;
  const cards = node => node.nodeType === 1 ? [...(node.matches('.card') ? [node] : []), ...node.querySelectorAll('.card')] : [];
  const remember = () => {
    if (!content) return;
    if (parsing) for (const node of content.querySelectorAll('.card')) {
      if (!observed.has(node)) { observed.add(node); originals.push(node); parents.push(node.parentNode); }
    }
  };
  new MutationObserver(records => {
    if (performance.now() > 10000) return;
    if (!content) content = document.getElementById('content');
    if (!content) return;
    const detached = new Set();
    const belongs = target => target === content || content.contains(target) ||
      [...detached].some(root => root === target || root.contains(target));
    // Mutation records can target a grid already detached in this delivery.
    // Follow removed subtrees even when their cards have since moved elsewhere.
    let previous;
    do {
      previous = detached.size;
      for (const record of records) if (belongs(record.target))
        for (const node of record.removedNodes) detached.add(node);
    } while (previous !== detached.size);
    const removed = new Set(), added = new Set();
    for (const record of records) {
      if (!belongs(record.target)) continue;
      for (const node of [...record.removedNodes].flatMap(cards)) removed.add(node);
      for (const node of [...record.addedNodes].flatMap(cards)) added.add(node);
    }
    // A removed grid and its cards can appear in the same observer delivery.
    // Count each node once, and never adopt a replacement as an original.
    if (removed.size) parsing = false;
    remember();
    if (removed.size || added.size) result.mutations.push({at: performance.now(),
      readyState: document.readyState, afterLoad: result.load !== undefined,
      added: added.size, removed: removed.size,
      parserAdded: [...added].filter(node => observed.has(node) && parsing).length,
      removedOriginal: [...removed].filter(node => observed.has(node)).length,
      removedFirstScreen: [...removed].filter(node => firstScreen.some(item => item.node === node)).length});
  }).observe(document, {childList: true, subtree: true});
  document.addEventListener('DOMContentLoaded', () => {
    remember(); parsing = false; result.domContentLoaded = performance.now();
    for (const node of document.querySelectorAll('#content *')) parsed.add(node);
  });
  addEventListener('load', () => { result.load = performance.now(); });
  new PerformanceObserver(list => {
    for (const entry of list.getEntries()) result.paint[entry.name] = entry.startTime;
  }).observe({type: 'paint', buffered: true});
  new PerformanceObserver(list => {
    // A candidate the page script created inside the wall (not the server's markup;
    // until DOMContentLoaded every wall node is the parser's).
    for (const entry of list.getEntries()) result.lcp.push({at: entry.startTime,
      renderTime: entry.renderTime, loadTime: entry.loadTime, size: entry.size, element: describe(entry.element),
      scripted: document.readyState !== 'loading' && !!entry.element?.closest('#content') && !parsed.has(entry.element)});
  }).observe({type: 'largest-contentful-paint', buffered: true});
  new PerformanceObserver(list => {
    for (const entry of list.getEntries()) result.longTasks.push({at: entry.startTime, duration: entry.duration});
  }).observe({type: 'longtask', buffered: true});
  function visible(node) {
    if (!node?.isConnected) return false;
    const box = node.getBoundingClientRect(), style = getComputedStyle(node);
    if (!(box.width > 0 && box.height > 0 && box.top < innerHeight && box.bottom > 0) ||
        style.visibility === 'hidden' || style.display === 'none') return false;
    for (let parent = node; parent; parent = parent.parentElement)
      if (getComputedStyle(parent).opacity === '0') return false;
    return true;
  }
  function identity(node) {
    const textNodes = [], walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) textNodes.push(walker.currentNode);
    return {node, elements: [...node.querySelectorAll('*')], textNodes,
      critical: [...node.querySelectorAll('.title, .pricerow')],
      criticalText: textNodes.filter(text => text.parentElement.closest('.title, .pricerow')),
      images: [...node.querySelectorAll('img')].map(image => ({node: image, src: image.getAttribute('src')}))};
  }
  function frame() {
    const current = document.querySelector('#content .card');
    if (result.firstCardVisible === null && visible(current)) {
      result.firstCardVisible = performance.now();
      result.firstCard = describe(current);
      result.firstCardTop = current.getBoundingClientRect().top;
      firstScreen.push(...originals.filter(visible).map(identity));
    }
    // The module's first render: client cards carry data-key, server cards do not.
    if (result.hydratedAt === null && document.querySelector('#content .card[data-key]'))
      result.hydratedAt = performance.now();
    if (performance.now() < 10000) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
  window.finishPhoneLcp = () => {
    result.originals = originals.map((node, i) => ({key: key(node), connected: node.isConnected,
      sameParent: node.parentNode === parents[i]}));
    result.firstScreen = firstScreen.map(item => ({key: key(item.node), connected: item.node.isConnected,
      elements: item.elements.length, removedElements: item.elements.filter(node => !node.isConnected).length,
      textNodes: item.textNodes.length, removedTextNodes: item.textNodes.filter(node => !node.isConnected).length,
      removedTitlePrice: item.critical.concat(item.criticalText).filter(node => !node.isConnected).length,
      images: item.images.map(image => ({src: image.src, connected: image.node.isConnected,
        sameSource: image.node.getAttribute('src') === image.src}))}));
    result.finalKeys = [...document.querySelectorAll('#content .card')].map(key);
    const panel = document.getElementById('paywall'), content = document.getElementById('content');
    result.contentHtml = [...content.childNodes].filter(node => node !== panel)
      .map(node => node.outerHTML ?? node.textContent).join('');
    result.resources = performance.getEntriesByType('resource').map(entry => ({
      name: new URL(entry.name).pathname, initiator: entry.initiatorType,
      at: entry.startTime, duration: entry.duration, bytes: entry.transferSize}));
    return result;
  };
}

(async () => {
  let chromium;
  try { ({chromium} = require('playwright')); }
  catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND') throw error;
    console.log(JSON.stringify({status: 'not_measured', reason: 'Playwright is not installed', timings: null}));
    process.exitCode = 77; return;
  }
  const args = process.argv.slice(2), root = path.resolve(args[0]);
  const executablePath = args[1] && !args[1].startsWith('--') ? args[1] : '/opt/pw-browsers/chromium';
  const delay = Number(args.find(arg => arg.startsWith('--deals-delay='))?.split('=')[1] || 0);
  const tracePath = args.find(arg => arg.startsWith('--trace='))?.slice(8);
  const now = Date.parse(args.find(arg => arg.startsWith('--now='))?.slice(6) || '2026-10-16T12:00:00Z');
  let browser;
  try { browser = await chromium.launch({headless: true, executablePath}); }
  catch (error) {
    if (!/Operation not permitted|Executable doesn't exist/.test(error.message)) throw error;
    console.log(JSON.stringify({status: 'not_measured', reason: 'Chromium sandbox launch denied or no installed browser', timings: null}));
    process.exitCode = 77; return;
  }
  try {
    const context = await browser.newContext({viewport: {width: 390, height: 844},
      isMobile: true, hasTouch: true, deviceScaleFactor: 1, serviceWorkers: 'block'});
    const blocked = [], errors = [];
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (request.resourceType() === 'image') return route.fulfill({contentType: 'image/png',
        body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1cAAAAASUVORK5CYII=', 'base64')});
      if (url.origin !== 'https://wowpriced.test' || request.method() !== 'GET') {
        blocked.push(url.href); return route.abort();
      }
      let file = path.resolve(root, '.' + url.pathname);
      if (!file.startsWith(root + path.sep) && file !== root) return route.abort();
      if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
      if (!fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
      if (url.pathname === '/deals.json' && delay) await new Promise(resolve => setTimeout(resolve, delay));
      const contentType = {'.html': 'text/html', '.json': 'application/json', '.js': 'application/javascript',
        '.css': 'text/css', '.woff2': 'font/woff2', '.webmanifest': 'application/manifest+json'}[path.extname(file)];
      return route.fulfill({contentType: contentType || 'application/octet-stream', body: fs.readFileSync(file)});
    });
    // Default: tests.phone_budget_fixture.NOW; no unintended clock-driven drop.
    await context.addInitScript(observeStartup, now);
    const page = await context.newPage(), session = await context.newCDPSession(page);
    page.on('pageerror', error => errors.push(error.message));
    await session.send('Emulation.setCPUThrottlingRate', {rate: 4});
    const traceEvents = [];
    if (tracePath) {
      session.on('Tracing.dataCollected', event => traceEvents.push(...event.value));
      await session.send('Tracing.start', {categories: 'devtools.timeline,loading,blink.user_timing', transferMode: 'ReportEvents'});
    }
    await page.goto('https://wowpriced.test/', {waitUntil: 'load'});
    await page.waitForFunction(() => performance.now() >= 10100);
    const result = await page.evaluate(() => window.finishPhoneLcp());
    result.status = 'measured';
    result.viewport = {width: 390, height: 844}; result.cpuThrottle = 4;
    result.dealsDelay = delay; result.imageFixture = 'local 1x1 PNG'; result.errors = errors; result.blocked = blocked;
    result.removedOriginal = result.mutations.reduce((total, item) => total + item.removedOriginal, 0);
    result.removedFirstScreen = result.mutations.reduce((total, item) => total + item.removedFirstScreen, 0);
    result.removedCards = result.mutations.reduce((total, item) => total + item.removed, 0);
    result.addedCards = result.mutations.reduce((total, item) => total + item.added, 0);
    result.parserAddedCards = result.mutations.reduce((total, item) => total + item.parserAdded, 0);
    result.postInitialAddedCards = result.addedCards - result.parserAddedCards;
    result.postLoad = result.mutations.filter(item => item.afterLoad).reduce((total, item) => ({
      added: total.added + item.added, removed: total.removed + item.removed}), {added: 0, removed: 0});
    // Removed-then-reinserted (moved) cards keep their identity; lost ones do not.
    result.lostOriginal = result.originals.filter(item => !item.connected).length;
    result.firstEight = result.originals.slice(0, 8);
    result.lcpScripted = result.lcp.filter(item => item.scripted);
    result.contentSha256 = require('node:crypto').createHash('sha256').update(result.contentHtml).digest('hex');
    delete result.contentHtml;
    if (tracePath) {
      const completed = new Promise(resolve => session.once('Tracing.tracingComplete', resolve));
      await session.send('Tracing.end'); await completed;
      fs.writeFileSync(tracePath, JSON.stringify({traceEvents}));
    }
    console.log(JSON.stringify(result));
    if (args.includes('--assert-preserved')) {
      assert(result.originals.length >= 8, 'server-rendered card nodes must be observed before scripts');
      assert(result.firstScreen.length > 0, 'at least one original card must be visible before startup finishes');
      assert.equal(result.removedFirstScreen, 0, 'first-screen card nodes must not be removed');
      assert(result.firstScreen.every(node => node.connected), 'first-screen cards must retain their DOM identity');
      assert(result.firstScreen.every(node => !node.removedTitlePrice),
        'first-screen titles, prices and their text must retain their DOM identity');
      assert(result.firstScreen.every(node => node.images.every(image => image.connected && image.sameSource)),
        'first-screen images must retain their nodes and sources');
      assert(result.hydratedAt !== null, 'the module must render the wall');
      assert(result.firstEight.every(item => item.connected && item.sameParent),
        'the first eight server cards must stay in place');
      assert.equal(result.lostOriginal, 0, 'server cards must not be replaced');
      assert.deepEqual(result.lcpScripted, [], 'the first render must paint no new largest content');
      assert.deepEqual(errors, []);
    }
    await context.close();
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
