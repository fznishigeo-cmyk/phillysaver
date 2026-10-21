// Real Chromium regressions, entirely route-fulfilled; no socket or retailer hit.
const assert = require('node:assert/strict');
const {searchProbe, firstScreenGlare} = require('../scripts/visitor_watch.js');
const {pixels, glare} = require('../scripts/browser_pixels.cjs');
const {QUERY, searchProblems} = require('../scripts/visitor_watch_logic.cjs');
const {opticalProblems} = require('../scripts/visitor_watch_logic.cjs');
const {scanScreens, relookPending, markNewPhotoless, photoState, firstScreenLinks, visibleText, dealPageProbe, boundedScreenshot} =
  require('../scripts/visitor_watch_health.cjs');

(async () => {
  let browser;
  try {
    const {chromium} = require(require.resolve('playwright', {paths: ['/opt/node-tools', __dirname]}));
    browser = await chromium.launch({headless: true, executablePath: '/opt/pw-browsers/chromium',
      args: ['--no-sandbox', '--disable-dev-shm-usage']});
  } catch (error) {
    console.log('Chromium unavailable (exit 77): ' + error.message.split('\n')[0]);
    process.exitCode = 77; return;
  }
  const context = await browser.newContext({viewport: {width: 390, height: 844}, serviceWorkers: 'block'});
  const href = 'https://www.amazon.com/s?k=zzqx+cupping+test&tag=wowpriced-20';
  let releaseImage, releaseQueued, imageRequested = false;
  const released = new Promise(resolve => {releaseImage = resolve;});
  const queued = new Promise(resolve => {releaseQueued = resolve;});
  const outside = [];
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== 'wowpriced.test') {outside.push(url.href); return route.abort();}
    if (url.pathname === '/health-image.svg') return route.fulfill({contentType: 'image/svg+xml', body:
      '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="#728b9b"/></svg>'});
    if (url.pathname === '/broken.svg') return route.fulfill({status: 404, body: ''});
    if (url.pathname === '/deal-probe') {
      const bug = url.searchParams.get('bug');
      const product = 'https://www.amazon.com/dp/B012345678?tag=wowpriced-20';
      const target = bug === 'wrong-tag' ? product.replace('wowpriced-20', 'wrong-20') :
        bug === 'wrong-item' ? product.replace('B012345678', 'B099999999') : product;
      return route.fulfill({contentType: 'text/html', body: `<main><div data-deal-detail>
        <p class="detail-price">Sale price: $10.00 USD</p>
        ${bug === 'missing' ? '' : `<a class="getdeal" href="${target}">Get deal</a>`}
        <div class="grid">${Array.from({length: 6}, () =>
          `<article class="card"><a class="getdeal" href="${product}">Get deal</a></article>`).join('')}</div>
        </div></main>`});
    }
    if (url.pathname === '/health') {
      const bug = url.searchParams.get('bug');
      return route.fulfill({contentType: 'text/html', body: `<!doctype html><style>
        body{margin:0;background:#111;color:#ccc}main{width:100%}.card{box-sizing:border-box;width:300px;height:200px;
        border:1px solid #666;display:flex;background:#222}img{width:100px;height:100px}
        ${bug === 'overflow' ? 'main{width:600px}' : ''}
        ${bug === 'overlap' ? '.card:nth-child(2){position:absolute;top:10px}' : ''}
        ${bug === 'stretch' ? '.card:nth-child(2){height:501px}' : ''}</style>
        <a href="/about.html">About</a><a class="getdeal" href="/affiliate">Get deal</a>
        <main id="content">${bug === 'blank' ? '<div style="height:450px"></div>' : ''}
        ${Array.from({length: 5}, (_, i) => `<article class="card" data-key="health-${i}">
          ${bug === 'placeholder' && i === 0 ? '<span>Store tile</span>' :
            `<img src="/${bug === 'broken' && i === 0 ? 'broken' : 'health-image'}.svg">`}
          <p>${bug === 'text' && i === 0 ? '$NaN undefined null [object Object] price history' :
            'Cotton socks with 90-day warranty $10, 80% off the usual $50.00 <s>List Price $50.00</s> <span>Price drop</span>'}</p>
          <div hidden>Price history undefined</div><span style="display:none">price history $NaN</span>
          <span style="visibility:hidden">price history null</span><span style="opacity:0">highest we</span>
          <script>/* price history $NaN */</script>
        </article>`).join('')}</main>
        <div hidden>Price history undefined</div><script>/* price history $NaN */</script>
        <div data-deal-detail><div class="grid"><img src="/health-image.svg"></div></div>`});
    }
    if (url.pathname === '/held.svg') return;  // never answered: keeps the page loading
    if (url.pathname === '/queued.svg') {
      await queued;  // answered once the test releases it, like a photo behind our pacing
      return route.fulfill({contentType: 'image/svg+xml', body:
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="#728b9b"/></svg>'});
    }
    if (url.pathname === '/slow.svg') {
      await new Promise(resolve => setTimeout(resolve, 2500));  // past the 1.5 s first look
      return route.fulfill({contentType: 'image/svg+xml', body:
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="#728b9b"/></svg>'});
    }
    if (url.pathname === '/loading') return route.fulfill({contentType: 'text/html', body:
      '<!doctype html><style>body{margin:0;background:#111}@keyframes up{from{background:#000}to{background:#fff}}' +
      '.fade{height:100vh;animation:up 60s forwards}</style><div class="fade"></div><img src="/held.svg">'});
    if (url.pathname === '/category-new') return route.fulfill({contentType: 'text/html', body:
      '<!doctype html><style>body{margin:0}.card{height:150px}</style><main id="content">' +
      '<article class="card"><div class="media"></div><h3 class="title"><a href="/deal/new-aaaa1111/">New &amp; no photo</a></h3></article>' +
      '<article class="card"><div class="media"></div><h3 class="title"><a href="https://www.walmart.com/ip/1">Store-linked new</a></h3></article>' +
      '<article class="card"><div class="media"></div><h3 class="title"><a href="/deal/old-bbbb2222/">Old no photo</a></h3></article>' +
      '</main>'});
    if (url.pathname === '/paced') return route.fulfill({contentType: 'text/html', body:
      '<!doctype html><style>body{margin:0}main{width:100%}.card{height:200px}img{width:100px;height:100px}</style>' +
      '<main id="content"><article class="card" data-key="slow"><img src="/slow.svg"></article>' +
      '<article class="card" data-key="held"><img src="/held.svg"></article>' +
      '<article class="card" data-key="queued"><img src="/queued.svg"></article></main>'});
    if (url.pathname === '/white.svg') {
      imageRequested = true; await released;
      return route.fulfill({contentType: 'image/svg+xml', body:
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"><rect width="200" height="200" fill="white"/></svg>'});
    }
    const action = url.pathname.slice(1), handler = action === 'cancelled' ? 'event.preventDefault()' :
      action === 'open' ? `event.preventDefault();window.open(${JSON.stringify(href)}, '_blank')` :
      action === 'self' ? `event.preventDefault();location.href=${JSON.stringify(href)}` : '';
    return route.fulfill({contentType: 'text/html', body: '<!doctype html><style>body{margin:0;background:#111}</style>' +
      (action === 'glare' ? '<img width="200" height="200">' :
        `<input id="q" placeholder="Search deals or Amazon"><a target="_blank" href="${href}">Search Amazon</a>` +
        `<script>document.querySelector('a').addEventListener('click', event => {${handler}});</script>`)});
  });
  try {
    for (const action of ['anchor', 'cancelled', 'open', 'self']) {
      const page = await context.newPage();
      await page.goto('https://wowpriced.test/' + action);
      const result = await searchProbe(page);
      if (action !== 'self') assert.equal(await page.locator('#q').inputValue(), QUERY);
      assert.equal(searchProblems(result, 'wowpriced-20').length === 0, ['anchor', 'open'].includes(action), action);
      if (action === 'cancelled') assert.equal(result.clickedHref, undefined);
      if (action === 'self') assert.equal(result.target, '_self');
      await page.close();
    }
    assert.deepEqual(outside, [], 'search navigation must be aborted before the network guard sees it');
    // Live 2026-10-17..21: page.screenshot waited for document.fonts.ready,
    // which stays pending while a paced photo keeps the page loading.
    const loading = await context.newPage();
    await loading.goto('https://wowpriced.test/loading', {waitUntil: 'domcontentloaded'});
    const started = Date.now();
    const still = pixels(await boundedScreenshot(loading, Date.now() + 3000));
    assert(Date.now() - started < 3000, 'capture must not wait for the page to finish loading');
    assert.equal(await loading.evaluate(() => document.readyState), 'interactive', 'captured while still loading');
    assert(glare(still) > .9, 'finite animations are captured finished, as with animations: disabled');
    assert.equal(await loading.evaluate(() => document.getAnimations()[0].playState), 'finished');
    await loading.close();
    // A paced photo that arrives after the first look is loaded; one still queued is
    // pending (reported, never a flag); a failed photo is broken (the bug= cases below).
    const paced = await context.newPage();
    await paced.goto('https://wowpriced.test/paced', {waitUntil: 'domcontentloaded'});
    const pacedStarted = Date.now();
    const pacedScan = await scanScreens(paced, '.card', Date.now() + 30000);
    assert.deepEqual(Object.fromEntries(pacedScan.photos.map(card => [card.key, card.photo])),
      {slow: 'loaded', held: 'pending', queued: 'pending'});
    assert(Date.now() - pacedStarted < 12000, 'one shared 8 s wait, not one per card or screen');
    // Live 2026-10-21: the desktop home scan saw "0 loaded; 37 still downloading";
    // after the first-24 pass the queued photos have arrived and a short look sees them.
    releaseQueued();
    const relookStarted = Date.now();
    await relookPending(paced, '.card', pacedScan.photos, Date.now() + 5000, 250);
    assert.deepEqual(Object.fromEntries(pacedScan.photos.map(card => [card.key, card.photo])),
      {slow: 'loaded', held: 'pending', queued: 'loaded'});
    assert(Date.now() - relookStarted < 3000, 'a short look per card, not the whole wait on one held photo');
    await paced.close();
    // Live 2026-10-21: category placeholders for deals still inside max_photoless_h
    // are by design; matched by deal-page link or exact title, never by position.
    const category = await context.newPage();
    await category.goto('https://wowpriced.test/category-new', {waitUntil: 'domcontentloaded'});
    const categoryScan = await scanScreens(category, '.card', Date.now() + 30000);
    assert.deepEqual(categoryScan.photos.map(card => card.photo), ['placeholder', 'placeholder', 'placeholder']);
    await markNewPhotoless(category, '.card', categoryScan.photos,
      {paths: new Set(['/deal/new-aaaa1111/']), titles: new Set(['Store-linked new'])});
    assert.deepEqual(categoryScan.photos.map(card => Boolean(card.newPhotoless)), [true, true, false]);
    await category.close();
    const page = await context.newPage();
    await page.goto('https://wowpriced.test/glare', {waitUntil: 'domcontentloaded'});
    await page.waitForFunction(() => document.images.length === 1);
    assert.equal(glare(pixels(await boundedScreenshot(page, Date.now() + 3000))),
      0, 'capture the baseline before starting the held image response');
    await page.locator('img').evaluate(img => {img.src = '/white.svg';});
    await assert.rejects(firstScreenGlare(page, Date.now() + 100), /not decoded/);
    const timer = setTimeout(releaseImage, 200);
    try {assert(await firstScreenGlare(page, Date.now() + 5000) > .01, 'delayed white image must flag dark glare');}
    finally {clearTimeout(timer); releaseImage();}
    assert(imageRequested);
    await page.close();
    for (const bug of ['', 'placeholder', 'broken', 'overflow', 'overlap', 'stretch', 'blank', 'text']) {
      const page = await context.newPage();
      await page.goto('https://wowpriced.test/health?bug=' + bug);
      const links = await firstScreenLinks(page);
      assert(links.includes('https://wowpriced.test/about.html'));
      assert(!links.includes('https://wowpriced.test/affiliate'), 'Get deal must never enter HEAD probes');
      const text = await visibleText(page.locator('main'));
      assert.equal(text.invalid.length > 0, bug === 'text');
      assert.equal(text.history.length > 0, bug === 'text');
      const scan = await scanScreens(page, '.card', Date.now() + 15000);
      const first = scan.photos.find(card => card.key === 'health-0'), photo = first.photo;
      assert.equal(photo, bug === 'placeholder' ? 'placeholder' : bug === 'broken' ? 'broken' : 'loaded', bug);
      if (bug === 'broken') assert.equal(first.host, 'wowpriced.test', 'a broken photo names its host');
      const problems = opticalProblems(scan.optical);
      assert.equal(problems.length > 0, ['overflow', 'overlap', 'stretch', 'blank'].includes(bug), bug + ': ' + problems);
      const detail = page.locator('[data-deal-detail]');
      await detail.scrollIntoViewIfNeeded();
      assert.equal(await photoState(detail, 100, 'img:not(.grid img)'), 'placeholder', 'related image cannot mask missing deal photo');
      await page.close();
    }
    for (const bug of ['', 'missing', 'wrong-tag', 'wrong-item']) {
      const page = await context.newPage();
      await page.goto('https://wowpriced.test/deal-probe?bug=' + bug);
      assert.equal(await page.locator('.grid a.getdeal').count(), 6);
      const result = await dealPageProbe(page, 200, {store: 'Amazon',
        href: 'https://www.amazon.com/dp/B012345678?tag=wowpriced-20'}, {amazonTag: 'wowpriced-20'});
      assert.equal(result.getDeal, bug === '', bug + ': related buttons cannot mask a broken main button');
      assert.equal(result.price, true);
      await page.close();
    }
    assert.deepEqual(outside, [], 'health probes must stay offline');
    console.log('PASS: search navigation, bounded delayed glare, deep photos, layout faults, visible text, main deal buttons and affiliate exclusion.');
  } finally {releaseImage(); await context.close(); await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
