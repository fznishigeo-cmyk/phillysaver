// Offline real-browser parity: no server, downloads or external requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ORIGIN = 'https://wowpriced.test';

(async () => {
  const {chromium} = require(require.resolve('playwright', {paths: ['/opt/node-tools', __dirname]}));
  let browser;
  try {
    browser = await chromium.launch({headless: true, executablePath: '/opt/pw-browsers/chromium',
      args: ['--no-sandbox', '--disable-dev-shm-usage']});
  } catch (error) {
    if (!/Operation not permitted|Executable doesn't exist|Failed to launch|Target page, context or browser has been closed/.test(error.message)) throw error;
    console.log('Chromium cannot launch (exit 77): ' + error.message.split('\n')[0]);
    process.exitCode = 77; return;
  }
  try {
    for (const [width, height] of [[390, 844], [1280, 800]]) for (const colorScheme of ['light', 'dark']) {
      const snapshots = [];
      for (const rootArg of process.argv.slice(2)) {
        const root = path.resolve(rootArg), errors = [], escaped = [];
        const context = await browser.newContext({viewport: {width, height}, colorScheme,
          isMobile: width === 390, hasTouch: width === 390, serviceWorkers: 'block'});
        try {
          await context.route('**/*', async route => {
            const request = route.request(), url = new URL(request.url());
            if (request.resourceType() === 'image') return route.fulfill({contentType: 'image/svg+xml',
              body: '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"><rect width="200" height="200" fill="#889999"/></svg>'});
            if (url.origin !== ORIGIN || request.method() !== 'GET') {
              escaped.push(url.href); return route.abort();
            }
            let file = path.resolve(root, '.' + url.pathname);
            assert(file === root || file.startsWith(root + path.sep));
            if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
            assert(fs.existsSync(file), 'missing fixture ' + url.pathname);
            await route.fulfill({contentType: ({'.html': 'text/html', '.json': 'application/json',
              '.js': 'application/javascript', '.css': 'text/css'})[path.extname(file)] || 'application/octet-stream',
              body: fs.readFileSync(file)});
          });
          const page = await context.newPage();
          page.on('pageerror', error => errors.push(error.message));
          page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
          // Keep auto-load out of this button-specific smoke, in both variants.
          await page.addInitScript(() => {delete window.IntersectionObserver;});
          await page.clock.setFixedTime(new Date('2026-10-16T12:00:00Z'));
          await page.goto(ORIGIN + '/', {waitUntil: 'networkidle'});
          await page.waitForFunction(() => document.querySelectorAll('#content .card').length === 60);
          const snapshot = async () => page.evaluate(() => ({
            cards: [...document.querySelectorAll('#content .card')].map(card => ({
              title: card.querySelector('.title').textContent,
              href: card.querySelector('a.getdeal').getAttribute('href')})),
            count: document.getElementById('searchcount').textContent,
            background: getComputedStyle(document.body).backgroundColor
          }));
          const states = [await snapshot()];
          assert(states[0].cards.every(card => /^https:\/\//.test(card.href)), 'Get deal hrefs');
          await page.locator('#moremore').click();
          await page.waitForFunction(() => document.querySelectorAll('#content .card').length === 120);
          states.push(await snapshot());
          await page.locator('#q').fill('Kitchen kettle');
          await page.waitForFunction(() => document.getElementById('searchcount').textContent.includes('90 deals'));
          states.push(await snapshot());
          assert(states[2].cards.every(card => card.title.includes('Kitchen kettle')));
          await page.locator('#q').fill('');
          await page.waitForFunction(() => document.getElementById('searchcount').textContent === '');
          await page.locator('#cattabs .cattab').filter({hasText: 'Beauty'}).click();
          await page.waitForFunction(() => [...document.querySelectorAll('#content .card .title')]
            .every(title => title.textContent.includes('Beauty serum')) && document.querySelector('#content .card'));
          states.push(await snapshot());
          await page.locator('#cattabs .cattab[data-i="0"]').click();
          await page.waitForFunction(() => document.querySelectorAll('#content .card').length === 60);
          states.push(await snapshot());
          assert.deepEqual(errors, [], 'console/page errors');
          assert.deepEqual(escaped, [], 'network outside fixtures');
          snapshots.push(states);
        } finally {await context.close();}
      }
      assert.equal(snapshots.length, 2);
      assert.deepEqual(snapshots[1], snapshots[0], `ON/OFF parity ${width}×${height} ${colorScheme}`);
    }
    console.log('PASS: 390×844 + 1280×800, light + dark; ON/OFF home, search, tabs, Load more, Get deal hrefs; no console errors.');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
