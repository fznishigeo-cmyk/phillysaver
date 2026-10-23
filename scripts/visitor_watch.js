const {routeRobotSearch, searchResponse} = require('./robot_search.cjs');
// Our own public site only. Never follows a deal link or sends a form.
const fs = require('node:fs');
const path = require('node:path');
const {execFile} = require('node:child_process');
const {QUERY, SEARCH_BUTTON, BLOCK, rulesFromConfig, assess, summary, health, photoIssue, imageHostsOf,
  newPhotoless} =
  require('./visitor_watch_logic.cjs');
const {pixels, glare} = require('./browser_pixels.cjs');
const {photoState, relookPending, markNewPhotoless, scanScreens, headLinks, firstScreenLinks, departmentProbe, inventorySearchProbe, allUsedProbe, visibleText, collectText,
  dealPageProbe, boundedScreenshot} =
  require('./visitor_watch_health.cjs');

const SITE = 'https://wowpriced.com/';
const UA = 'wowpriced-visitor-watch/1.0 (+https://wowpriced.com; contact: hello@wowpriced.com)';
const CARDS = '#content .card[data-key]';
const MODES = [
  {name: 'phone-light', width: 390, height: 844, mobile: true, scheme: 'light'},
  {name: 'phone-dark', width: 390, height: 844, mobile: true, scheme: 'dark'},
  {name: 'desktop-light', width: 1280, height: 800, mobile: false, scheme: 'light'},
  {name: 'desktop-dark', width: 1280, height: 800, mobile: false, scheme: 'dark'}
];

async function readConfig(file) {
  // Use the repository's YAML parser, including inline collections/exclusions.
  const input = fs.readFileSync(file);
  return new Promise((resolve, reject) => {
    const child = execFile(process.env.PYTHON || 'python3', ['-c',
      'import json,sys,yaml\nprint(json.dumps(yaml.safe_load(sys.stdin), default=str))'],
    {cwd: path.join(__dirname, '..'), encoding: 'utf8', timeout: 10000}, (error, output) => {
      if (error) return reject(error);
      try {resolve(JSON.parse(output));} catch (error) {reject(error);}
    });
    child.stdin.on('error', reject);
    child.stdin.end(input);
  });
}

function pacer() {
  const queues = new Map(), last = new Map();
  return async (host, active = () => true) => {
    const pending = (queues.get(host) || Promise.resolve()).then(async () => {
      if (!active()) return false;
      const delay = Math.max(0, 1000 - (Date.now() - (last.get(host) || 0)));
      if (delay) await new Promise(resolve => setTimeout(resolve, delay));
      if (!active()) return false;
      last.set(host, Date.now());
      return true;
    });
    queues.set(host, pending);
    return pending;
  };
}

function offlineRoute(root, origin, request) {
  const url = new URL(request.url());
  if (request.resourceType() === 'image') return {contentType: 'image/svg+xml', body:
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"><rect width="200" height="200" fill="#728b9b"/></svg>'};
  if (url.origin !== origin || !['GET', 'HEAD'].includes(request.method())) throw new Error('offline request refused');
  let file = path.resolve(root, '.' + decodeURIComponent(url.pathname));
  if (file !== root && !file.startsWith(root + path.sep)) throw new Error('offline path refused');
  if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
  if (!fs.existsSync(file)) return {status: 404, body: ''};
  return {body: fs.readFileSync(file), contentType: {'.html': 'text/html', '.js': 'application/javascript',
    '.css': 'text/css', '.json': 'application/json', '.webmanifest': 'application/manifest+json',
    '.svg': 'image/svg+xml', '.png': 'image/png'}[path.extname(file)] || 'application/octet-stream'};
}

async function guardedRoute(route, {origin, offlineSite, pace, active, ignored, countOwnRequest, rules = {}}) {
  const request = route.request();
  let url = new URL(request.url());
  for (let redirects = 0; ; redirects++) {
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
        BLOCK.test(url.href) || request.method() !== 'GET' ||
        (url.origin !== origin && (request.resourceType() !== 'image' || url.protocol !== 'https:' || url.port ||
          !(rules.imageHosts || []).includes(url.hostname.replace(/\.$/, ''))))) {
      ignored.add(request.url()); ignored.add(url.href);
      return route.abort();
    }
    if (url.pathname.startsWith('/api/search')) return route.fulfill(searchResponse(url.href));
    if (offlineSite) return route.fulfill(offlineRoute(offlineSite, origin, request));
    if (url.origin === origin && countOwnRequest() > 60) throw new Error('same-origin request limit (60)');
    if (!await pace(url.hostname, active)) return route.abort();
    // Browser redirects skip Playwright's route guard. Fetch one hop at a time
    // so the host policy, request limit and pacing apply to every destination.
    const response = await route.fetch({url: url.href, maxRedirects: 0, timeout: 20000});
    try {
      const location = response.headers().location;
      if (![301, 302, 303, 307, 308].includes(response.status()) || !location)
        return await route.fulfill({response});
      if (redirects >= 20) throw new Error('redirect limit (20)');
      url = new URL(location, url);
    } finally {await response.dispose();}
  }
}

async function searchProbe(page, ignored = new Set()) {
  const result = {placeholder: await page.locator('#q').getAttribute('placeholder')};
  await page.locator('#q').fill(QUERY);
  // Give the site's search debounce and empty-state renderer time to finish.
  await page.waitForTimeout(700);
  const button = page.getByRole('link', {name: SEARCH_BUTTON})
    .or(page.getByRole('button', {name: SEARCH_BUTTON})).first();
  result.visible = await button.isVisible();
  if (!result.visible) return result;
  result.label = (await button.innerText()).trim();
  result.target = await button.getAttribute('target');
  const context = page.context();
  let navigation;
  const intercept = async route => {
    const request = route.request();
    if (!request.isNavigationRequest()) return route.fallback();
    let target = '_blank';
    // The first request of a popup may not have a frame yet.
    try {if (request.frame().page() === page) target = '_self';} catch { /* popup */ }
    navigation = {href: request.url(), target};
    ignored.add(request.url());
    await route.abort('aborted');
  };
  await page.evaluate(() => {
    window.__visitorWatchOpen = null;
    window.__visitorWatchOriginalOpen = window.open;
    window.open = (href, target) => {window.__visitorWatchOpen = {href: String(href), target}; return null;};
  });
  await context.route('**/*', intercept);
  try {
    // Let the site's handlers and anchor default run. Only a real navigation
    // request or window.open call counts; neither reaches a retailer.
    await button.click({noWaitAfter: true});
    await page.waitForTimeout(1000);
    const opened = await page.evaluate(() => window.__visitorWatchOpen).catch(() => null);
    const clicked = navigation || opened;
    result.clickedHref = clicked?.href;
    if (clicked) result.target = clicked.target;
  } finally {
    await context.unroute('**/*', intercept);
    await page.evaluate(() => {window.open = window.__visitorWatchOriginalOpen;}).catch(() => {});
  }
  return result;
}

async function firstScreenGlare(page, deadline) {
  const decoded = await page.evaluate(async timeout => {
    const visible = [...document.images].filter(img => {
      const box = img.getBoundingClientRect();
      return img.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) &&
        box.width > 1 && box.height > 1 && box.bottom > 0 && box.top < innerHeight &&
        box.right > 0 && box.left < innerWidth;
    });
    let timer;
    try {
      return await Promise.race([
        Promise.all(visible.map(img => img.decode().catch(() => {}))).then(() => true),
        new Promise(resolve => {timer = setTimeout(() => resolve(false), timeout);})]);
    } finally {clearTimeout(timer);}
  }, Math.max(1, Math.min(3000, deadline - Date.now())));
  if (!decoded) throw new Error('visible photos not decoded before dark capture');
  return glare(pixels(await boundedScreenshot(page, deadline)));
}

function watchContextErrors(context, origin, errors, ignored, active) {
  context.on('page', page => {
    page.on('pageerror', error => {if (active()) errors.push('page: ' + error.message);});
    page.on('console', message => {
      if (active() && message.type() === 'error' && !BLOCK.test(message.location().url || '') &&
          !ignored.has(message.location().url)) errors.push('console: ' + message.text());
    });
    page.on('requestfailed', request => {
      if (active() && new URL(request.url()).origin === origin && !ignored.has(request.url()))
        errors.push('request failed: ' + new URL(request.url()).pathname);
    });
    page.on('response', response => {
      const url = new URL(response.url());
      if (active() && url.origin === origin && response.status() >= 400 && !ignored.has(response.url()))
        errors.push(`HTTP ${response.status()}: ${url.pathname}`);
    });
  });
}

// One paced read of deals.json per watch, before any page loads its photos.
async function publishedImageHosts(browser, options, pace) {
  const url = new URL('deals.json', options.site);
  try {
    let text;
    if (options.offlineSite) {
      text = String(offlineRoute(options.offlineSite, url.origin,
        {url: () => url.href, method: () => 'GET', resourceType: () => 'fetch'}).body || '');
    } else {
      if (!await pace(url.hostname)) return [];
      const context = await browser.newContext({userAgent: UA});
      try {
        const response = await context.request.get(url.href, {timeout: 20000, maxRedirects: 0});
        if (!response.ok()) return [];
        text = await response.text();
      } finally {await context.close();}
    }
    return imageHostsOf(JSON.parse(text));
  } catch {return [];}
}

// Each view's first-screen internal links get one paced HEAD per run, after all
// views. Inside a view's 120 s watchdog, the first view to show a link paid for
// it: desktop-light, 13 more header/footer links than the phones at 1 request/
// second, ran past 120 s (live 2026-10-23 #222-#239); desktop-dark reused them.
async function checkLinks(browser, options, pace, scenes, firstLinks) {
  const origin = new URL(options.site).origin, cache = new Map(), deadline = Date.now() + 60000;
  let context;
  try {
    for (const scene of scenes) {
      if (!firstLinks.has(scene.name)) continue;
      scene.links = await headLinks(firstLinks.get(scene.name), origin, cache, async url => {
        if (options.offlineSite) return offlineRoute(options.offlineSite, origin,
          {url: () => url, method: () => 'HEAD', resourceType: () => 'document'}).status || 200;
        if (Date.now() >= deadline) throw new Error('link checks exceeded 60 seconds');
        if (!await pace(new URL(url).hostname)) throw new Error('paced request refused');
        context ||= await browser.newContext({userAgent: UA});
        const response = await context.request.head(url, {maxRedirects: 0, timeout: Math.max(1, Math.min(3000, deadline - Date.now()))});
        try {return response.status();} finally {await response.dispose();}
      });
    }
  } finally {await context?.close().catch(() => {});}
}

async function capture(browser, mode, options, rules, pace, firstLinks) {
  const context = await browser.newContext({viewport: {width: mode.width, height: mode.height},
    isMobile: mode.mobile, hasTouch: mode.mobile, colorScheme: mode.scheme, deviceScaleFactor: 1,
    userAgent: UA, serviceWorkers: 'block'});
  const scene = {name: mode.name, scheme: mode.scheme, viewport: {width: mode.width, height: mode.height},
    rotationEnabled: rules.rotationEnabled, cards: [], errors: [], phases: {}};
  const ignored = new Set();
  const origin = new URL(options.site).origin;
  // Live 2026-10-17: category/deal pages with hundreds of cards needed > 10 s on
  // scheduled runners; budgets sized so a healthy page never flags.
  const start = Date.now(), deadline = start + 120000;
  // Looks at photos still queued behind our own pacing are optional ("pending" is
  // report only), so they end 30 s before the watchdog and the checks after them
  // still finish (live 2026-10-23: desktop photo waits pushed its deal page and
  // search past 120 s). A page that truly hangs still meets the watchdog.
  const photoDeadline = deadline - 30000;
  let ownRequests = 0, closing = false, doc, phase = 'home';
  // Seconds into the capture at which each phase began; a timeout names its phase.
  const enter = name => {phase = name; scene.phases[name] = Math.round((Date.now() - start) / 100) / 10;};
  enter('home');
  const timer = setTimeout(() => {
    scene.errors.push(`health capture exceeded 120 seconds (${phase})`); closing = true;
    context.close().catch(() => {});
  }, 120000);
  try {
    await context.addInitScript(() => {
      try { localStorage.setItem('wp.join.promptUntil', String(Date.now() + 7 * 864e5));
        localStorage.setItem('wp.internal', '1'); } catch { /* storage unavailable */ }
    });
    await context.route('**/*', async route => {
      try {
        await guardedRoute(route, {origin, offlineSite: options.offlineSite, pace, ignored, rules,
          active: () => !closing, countOwnRequest: () => ++ownRequests});
      } catch (error) {
        if (!closing) scene.errors.push(error.message.split('\n')[0]);
        await route.abort().catch(() => {});
      }
    });
    await routeRobotSearch(context);
    watchContextErrors(context, origin, scene.errors, ignored, () => !closing);
    // Photo URL -> what its request did, so a photo flag names its cause.
    const photoRequests = new Map();
    context.on('page', page => {
      page.on('response', response => {
        if (response.request().resourceType() === 'image') photoRequests.set(response.url(), `HTTP ${response.status()}`);
      });
      page.on('requestfailed', request => {
        if (request.resourceType() === 'image') photoRequests.set(request.url(), 'request failed');
      });
    });
    const page = await context.newPage();
    page.setDefaultTimeout(8000);
    if (options.now) await page.clock.setFixedTime(new Date(options.now));
    const loaded = page.waitForResponse(response => new URL(response.url()).origin === origin &&
      new URL(response.url()).pathname === '/deals.json', {timeout: 20000})
      .then(response => response.ok() ? response.json() : null).catch(() => null);
    try {
      const response = await page.goto(options.site, {waitUntil: 'load', timeout: 20000});
      if (!response || response.status() !== 200) throw new Error('home did not return 200');
      doc = await loaded;
      if (!doc) throw new Error('deals.json unavailable or invalid');
      await page.waitForFunction(() => document.querySelectorAll('#content .card[data-key]').length >= 24);
      await page.waitForTimeout(500);
      const initial = await page.evaluate(selector => ({takenAt: new Date().toISOString(),
        countdown: document.querySelector('#countdown')?.textContent.trim() || '',
        cards: [...document.querySelectorAll(selector)].slice(0, 24).map(card => {
          const link = card.querySelector('a.getdeal'), price = card.querySelector('.now')?.textContent || '';
          let hrefHost = ''; try {hrefHost = new URL(link.href).host;} catch { /* missing link */ }
          return {key: card.dataset.key, title: card.querySelector('.title')?.textContent.trim() || '',
            ended: card.classList.contains('gone'),
            price: /^\$[\d,.]+$/.test(price) ? Number(price.slice(1).replace(/,/g, '')) : null,
            store: card.querySelector('.storechip')?.textContent.trim() || '',
            hasPhoto: false, href: link?.href || '', hrefHost, target: link?.target || ''};
        })}), CARDS);
      Object.assign(scene, initial);
      scene.publishedAt = doc.meta?.published_at || doc.meta?.generated_at || '';
      const step = Number(doc.meta?.cadence_minutes) * 60000, pub = Date.parse(scene.publishedAt);
      if (step > 0 && Number.isFinite(pub)) scene.nextDropAt = new Date(pub +
        (Math.floor((Date.parse(scene.takenAt) - pub) / step) + 1) * step).toISOString();
      if (mode.scheme === 'dark') {
        try {scene.glare = await firstScreenGlare(page, deadline);}
        catch (error) {scene.errors.push(error.message.split('\n')[0]);}
      }
      firstLinks.set(mode.name, await firstScreenLinks(page));
      const dealHref = await page.locator(CARDS).first().locator('.title a').getAttribute('href');
      scene.text = {invalid: [], history: (await visibleText(page.locator(CARDS))).history, used: []};
      scene.photosDeep = {};
      scene.working = {};
      scene.pageOptical = [];
      enter('home photos');
      try {
        const scan = await scanScreens(page, CARDS, deadline, photoDeadline);
        scene.photosDeep.home = scan.photos; scene.optical = scan.optical;
      } catch (error) {scene.optical = {error: error.message.split('\n')[0]};}
      // Lazy photos count only once scrolled into view and actually decoded.
      const firstPhoto = async (card, wait) => {
        const image = card.locator('img').first();
        if (await image.count()) {
          try { await image.evaluate((img, timeout) => Promise.race([
            img.decode().catch(() => {}), new Promise(resolve => setTimeout(resolve, timeout))]), wait); }
          catch { /* missing photo is a photos FLAG */ }
        }
        return card.evaluate(el => {
          const img = el.querySelector('img');
          return {key: el.dataset.key, src: img?.currentSrc || img?.src || '', complete: Boolean(img?.complete),
            width: img?.naturalWidth || 0, loaded: [...el.querySelectorAll('img')].some(img => {
              const box = img.getBoundingClientRect();
              return img.complete && img.naturalWidth > 1 && img.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) &&
                box.width > 1 && box.height > 1 && box.bottom > 0 && box.top < innerHeight && box.right > 0 && box.left < innerWidth;
            })};
        });
      };
      const record = (row, photo) => {
        row.hasPhoto = photo.loaded;
        if (photo.loaded) delete row.photoIssue;
        else row.photoIssue = photoIssue(photo, photoRequests.get(photo.src), rules, origin);
      };
      for (let i = 0; i < scene.cards.length; i++) {
        const remaining = deadline - Date.now();
        if (remaining <= 0) {scene.errors.push('photo capture exceeded 120 seconds'); break;}
        const card = page.locator(CARDS).nth(i);
        await card.scrollIntoViewIfNeeded({timeout: Math.min(3000, remaining)});
        const photo = await firstPhoto(card, Math.max(1, Math.min(3000, photoDeadline - Date.now())));
        if (photo.key !== scene.cards[i].key) {scene.errors.push('card order changed during capture'); break;}
        record(scene.cards[i], photo);
        await collectText(card, scene.text);
      }
      // Photos still queued behind our own 1 request/second pacing (live #159:
      // 4 desktop Amazon photos "still loading") get one shared 8 s look; any
      // still downloading after it are reported, not flagged.
      const until = Math.min(photoDeadline, Date.now() + 8000);
      for (const [i, row] of scene.cards.entries()) {
        if (row.hasPhoto || !/ still loading$/.test(row.photoIssue || '') || until - Date.now() <= 0) continue;
        const card = page.locator(CARDS).nth(i);
        await card.scrollIntoViewIfNeeded({timeout: Math.min(3000, until - Date.now())}).catch(() => {});
        const photo = await firstPhoto(card, Math.max(1, until - Date.now()));
        if (photo.key !== row.key) break;
        record(row, photo);
      }
      for (const row of scene.cards) if (!row.hasPhoto && / still loading$/.test(row.photoIssue || '')) row.photoPending = true;
      // The home scan ran while our own pacing still queued most desktop photos
      // (live 2026-10-21: "0 loaded; 37 still downloading"); the pass above gave
      // them time to arrive. One more short look each, 5 s in all.
      await relookPending(page, CARDS, scene.photosDeep.home || [], Math.min(photoDeadline, Date.now() + 5000), 250);
      enter('probes');
      if (rules.excludeUsedOnAll) scene.text.used = await allUsedProbe(page, doc);
      // Each probe has its own failure result; one broken control must not hide
      // the other health checks. No Get deal link is ever clicked.
      try {scene.working.department = await departmentProbe(page, doc, rules);}
      catch (error) {scene.working.error = error.message.split('\n')[0];}
      try {scene.working.inventorySearch = await inventorySearchProbe(page, doc);}
      catch (error) {scene.working.error = error.message.split('\n')[0];}
      await page.evaluate(() => window.scrollTo(0, 0));
      for (const [name, href, enabled] of [['category', '/deals/beauty-personal-care/', rules.categoryPhotos],
        ['deal', dealHref, rules.dealPhotos]]) {
        enter(name);
        const sample = await context.newPage();
        sample.setDefaultTimeout(5000);
        try {
          const url = new URL(href, options.site);
          if (!href || url.origin !== origin || !url.pathname.startsWith(name === 'deal' ? '/deal/' : '/deals/'))
            throw new Error('sample internal link unavailable');
          const response = await sample.goto(url.href, {waitUntil: 'domcontentloaded', timeout: 30000});
          await collectText(sample.locator('main'), scene.text, name);
          if (name === 'deal') {
            scene.working.deal = await dealPageProbe(sample, response?.status(), scene.cards[0], rules);
          }
          const scan = await scanScreens(sample, '.card', deadline, photoDeadline);
          scene.pageOptical.push({page: name, ...scan.optical, emptyAllowed: true});
          if (name === 'category') {
            scene.photosDeep.category = scan.photos;
            await markNewPhotoless(sample, '.card', scan.photos, newPhotoless(doc, rules.maxPhotolessH));
          } else {
            // Related-card photos cannot stand in for the main deal's photo.
            const detail = sample.locator('[data-deal-detail]');
            const image = detail.locator('img:not(.grid img)').first();
            if (await image.count()) await image.scrollIntoViewIfNeeded();
            scene.photosDeep.deal = [{photo: await photoState(detail, Math.max(1, Math.min(5000, photoDeadline - Date.now())),
              'img:not(.grid img)')}];
          }
          if (response?.status() !== 200) throw new Error(name + ' page did not return 200');
        } catch (error) {
          scene.pageOptical.push({page: name, error: error.message.split('\n')[0]});
          if (name === 'deal' || enabled) scene.errors.push(name + ': ' + error.message.split('\n')[0]);
        } finally {await sample.close().catch(() => {});}
      }
      enter('search');
      try {scene.search = await searchProbe(page, ignored);}
      catch (error) {scene.search = {error: error.message.split('\n')[0]};}
    } catch (error) {scene.errors.push(error.message.split('\n')[0]);}
    scene.takenAt ||= await page.evaluate(() => new Date().toISOString()).catch(() => new Date().toISOString());
    scene.errors = [...new Set(scene.errors)];
    return scene;
  } finally {clearTimeout(timer); closing = true; await context.close();}
}

async function runWatch(options = {}) {
  options = {site: SITE, config: path.join(__dirname, '../config.yaml'), output: 'visitor-watch/snapshot.json', ...options};
  if (!options.offlineSite && options.site !== SITE) throw new Error('live watcher is restricted to wowpriced.com');
  if (options.offlineSite) options.offlineSite = path.resolve(options.offlineSite);
  let rules = rulesFromConfig({}), setupError = '';
  try {rules = rulesFromConfig(await readConfig(options.config));}
  catch (error) {setupError = 'watcher config: ' + error.message.split('\n')[0];}
  rules.site = options.site;
  let previous = null;
  if (options.previous && fs.existsSync(options.previous)) {
    try { previous = JSON.parse(fs.readFileSync(options.previous, 'utf8')); }
    catch { console.log('Previous snapshot unreadable; starting a new baseline.'); }
  }
  const snapshot = {schema: 1, takenAt: options.now || new Date().toISOString(), scenes: []};
  let browser;
  if (!setupError) try {
    const {chromium} = require(require.resolve('playwright', {paths: [__dirname, '/opt/node-tools']}));
    browser = await chromium.launch({headless: true,
      ...(options.offlineSite ? {executablePath: '/opt/pw-browsers/chromium'} : {}),
      args: ['--no-sandbox', '--disable-dev-shm-usage']});
  } catch (error) {
    if (options.offlineSite) {console.log('Chromium unavailable (exit 77): ' + error.message.split('\n')[0]); return {exitCode: 77};}
    setupError = 'Chromium launch failed: ' + error.message.split('\n')[0];
  }
  if (setupError) snapshot.scenes = MODES.map(mode => ({name: mode.name, scheme: mode.scheme,
    takenAt: snapshot.takenAt, errors: [setupError]}));
  if (browser) {
    try {
      const pace = pacer(), firstLinks = new Map();
      rules.imageHosts = [...new Set([...rules.imageHosts, ...await publishedImageHosts(browser, options, pace)])];
      for (const mode of MODES) {
        try {snapshot.scenes.push(await capture(browser, mode, options, rules, pace, firstLinks));}
        catch (error) {snapshot.scenes.push({name: mode.name, scheme: mode.scheme, takenAt: snapshot.takenAt,
          errors: [error.message.split('\n')[0]]});}
      }
      await checkLinks(browser, options, pace, snapshot.scenes, firstLinks);
    } finally {await browser.close().catch(error => {
      snapshot.scenes.forEach(scene => scene.errors.push('browser close: ' + error.message.split('\n')[0]));
    });}
  }
  const exitCode = assess(snapshot, previous, rules), report = summary(snapshot);
  fs.mkdirSync(path.dirname(options.output), {recursive: true});
  fs.writeFileSync(options.output, JSON.stringify(snapshot, null, 2) + '\n');
  fs.writeFileSync(path.join(path.dirname(options.output), 'health.json'), JSON.stringify(health(snapshot)) + '\n');
  if (options.summary || process.env.GITHUB_STEP_SUMMARY)
    fs.appendFileSync(options.summary || process.env.GITHUB_STEP_SUMMARY, report);
  console.log(report);
  return {exitCode, snapshot};
}

if (require.main === module) {
  const args = process.argv.slice(2), options = {};
  const flags = {'--offline-site': 'offlineSite', '--config': 'config', '--output': 'output',
    '--previous': 'previous', '--summary': 'summary', '--now': 'now', '--site': 'site'};
  for (let i = 0; i < args.length; i += 2) {
    if (!flags[args[i]] || !args[i + 1]) throw new Error('Unknown/incomplete watcher option');
    options[flags[args[i]]] = args[i + 1];
  }
  runWatch(options).then(result => {process.exitCode = result.exitCode;})
    .catch(error => {console.error(error.message); process.exitCode = 1;});
}
module.exports = {runWatch, readConfig, pacer, offlineRoute, guardedRoute, searchProbe, firstScreenGlare, checkLinks,
  watchContextErrors, publishedImageHosts, MODES, UA};
