const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const {EventEmitter} = require('node:events');
const {QUERY, NAMES, BLOCK, rulesFromConfig, utilityMatch, checksForScene, assess, summary,
  textFindings, dealLinkProblem, opticalProblems, health, photoIssue, imageHostsOf} =
  require('../scripts/visitor_watch_logic.cjs');
const {pixels, glare, blankBands} = require('../scripts/browser_pixels.cjs');
const {headLinks, internalLinks, photoState, departmentProbe, inventorySearchProbe, allUsedProbe, firstScreenLinks,
  dealPageProbe, visibleText, collectText, boundedScreenshot} =
  require('../scripts/visitor_watch_health.cjs');
const {readConfig, offlineRoute, pacer, guardedRoute, searchProbe, firstScreenGlare, watchContextErrors, MODES, UA} =
  require('../scripts/visitor_watch.js');

const rules = {rotationEnabled: true, amazonTag: 'wowpriced-20', utilityGroups: {
  plumbing: {phrases: ['valve', 'pipe fitting', 'o-ring'], exclude: ['shower head', 'faucet']},
  construction: ['caulk', 'drywall']}};
const scene = (change = {}) => ({name: 'phone-dark', scheme: 'dark', rotationEnabled: true,
  takenAt: '2026-10-17T03:31:00Z', publishedAt: '2026-10-17T03:25:00Z',
  nextDropAt: '2026-10-17T03:40:00Z', countdown: '9:00', errors: [], glare: .01,
  photosDeep: {home: [{key: 'card-0', photo: 'loaded'}], category: [], deal: []},
  optical: {overflow: 0, boxes: [{key: 'card-0', left: 0, right: 300, top: 0, bottom: 200, height: 200}], bands: []},
  working: {department: {before: ['card-0'], after: ['beauty'], expected: ['beauty'], active: true,
    categories: ['beauty'], allowed: ['beauty']},
    inventorySearch: {query: 'socks', matches: 1}, deal: {status: 200, price: true, getDeal: true}},
  text: {invalid: [], history: [], used: []}, links: [{path: '/about.html', status: 200}],
  cards: Array.from({length: 24}, (_, i) => ({key: 'card-' + i, title: 'Kitchen set ' + i,
    store: 'Amazon', price: 50, hasPhoto: true, href: 'https://www.amazon.com/dp/B012345678?tag=wowpriced-20',
    hrefHost: 'amazon.com', target: '_blank'})),
  search: {placeholder: 'Search deals or Amazon', visible: true, label: 'Search Amazon', target: '_blank',
    clickedHref: 'https://www.amazon.com/s?k=' + encodeURIComponent(QUERY) + '&tag=wowpriced-20'}, ...change});
const check = (name, current, previous = null, options = rules) =>
  checksForScene(current, previous, options).find(row => row.name === name);
const snapshot = (current = scene(), time = current.takenAt) => ({schema: 1, takenAt: time, scenes: [current]});
const previous = () => scene({takenAt: '2026-10-17T03:16:00Z', publishedAt: '2026-10-17T03:10:00Z', nextDropAt: '2026-10-17T03:25:00Z'});

test('clean snapshot covers every named check', () => {
  const checks = checksForScene(scene(), null, rules);
  assert.deepEqual(checks.map(row => row.name), NAMES);
  assert(checks.every(row => !row.flag));
});

test('rotation only flags identical first six across a passed drop mark after task 269', () => {
  assert(check('top_rotation', scene(), previous()).flag);
  assert(!check('top_rotation', scene(), scene()).flag);
  assert(!check('top_rotation', scene(), previous(), {...rules, rotationEnabled: false}).flag);
  assert.match(check('top_rotation', scene(), previous(), {...rules, rotationEnabled: false}).detail, /report only/);
  assert(!check('top_rotation', scene(), {...previous(), rotationEnabled: false}).flag);
  assert(!check('top_rotation', scene(), {...previous(), nextDropAt: 'invalid'}).flag);
  assert(!check('top_rotation', scene(), {...previous(), cards: []}).flag);
  const changed = scene(); changed.cards[5].key = 'new';
  assert(!check('top_rotation', changed, previous()).flag);
  const seventh = scene(); seventh.cards[6].key = 'new';
  assert(check('top_rotation', seventh, previous()).flag);
  assert(check('top_rotation', scene({takenAt: previous().nextDropAt}), previous()).flag);
  assert(!check('top_rotation', scene({takenAt: previous().takenAt}), previous()).flag);
});

test('fresh publishes cannot postpone rotation forever; a changed top or disabled rotation resets the mark', () => {
  let prior;
  for (let minute = 10; minute <= 70; minute += 10) {
    const time = offset => new Date(Date.UTC(2026, 9, 17, 3, minute + offset)).toISOString();
    const current = snapshot(scene({takenAt: time(0), publishedAt: time(-1), nextDropAt: time(14)}));
    assert.equal(assess(current, prior, rules), minute >= 40 ? 1 : 0);
    assert.equal(current.scenes[0].pendingDropAt, '2026-10-17T03:24:00.000Z');
    assert.equal(current.checks.find(row => row.name === 'top_rotation').flag, minute >= 30);
    // Persist exactly as the workflow's JSON artifact does between runs.
    prior = JSON.parse(JSON.stringify(current));
  }
  const changed = snapshot(scene({takenAt: '2026-10-17T04:20:00Z', nextDropAt: '2026-10-17T04:34:00Z'}));
  changed.scenes[0].cards[0].key = 'new arrival';
  assess(changed, prior, rules);
  assert.equal(changed.scenes[0].pendingDropAt, changed.scenes[0].nextDropAt);
  assert(!changed.checks.find(row => row.name === 'top_rotation').flag);
  const disabled = snapshot(scene({takenAt: '2026-10-17T04:20:00Z', rotationEnabled: false}));
  assess(disabled, prior, {...rules, rotationEnabled: false});
  assert.equal(disabled.scenes[0].pendingDropAt, disabled.scenes[0].nextDropAt);
  const enabled = snapshot(scene({takenAt: '2026-10-17T04:30:00Z', nextDropAt: '2026-10-17T04:44:00Z'}));
  assess(enabled, disabled, rules);
  assert.equal(enabled.scenes[0].pendingDropAt, enabled.scenes[0].nextDropAt);
  assert(!enabled.checks.find(row => row.name === 'top_rotation').flag);
});

test('price mix includes $50, excludes $100 from over-$100, and requires 24 known prices', () => {
  const s = scene();
  for (let i = 19; i < 24; i++) s.cards[i].price = 100;
  assert(!check('price_mix', s).flag);
  s.cards[23].price = 101; assert(!check('price_mix', s).flag);
  s.cards[22].price = 101; assert(check('price_mix', s).flag);
  s.cards[22].price = 100; s.cards[18].price = 50.01;
  assert(check('price_mix', s).flag);
  for (const price of [null, undefined, NaN, Infinity, -1, '10']) {
    const bad = scene(); bad.cards[0].price = price; assert(check('price_mix', bad).flag);
  }
  assert(check('price_mix', scene({cards: scene().cards.slice(0, 23)})).flag);
});

test('utility matches whole normalized phrases and group exclusions, prints titles', () => {
  for (const title of ['Pressure reducing valve', '3/4 inch pipe-fitting', 'O-Ring seal', 'Caulk', 'Drywall panel'])
    assert(utilityMatch(title, rules.utilityGroups), title);
  for (const title of ['Water bottle', 'Valve shower head', 'Faucet valve', 'Caulking tool', 'Drywalling book'])
    assert(!utilityMatch(title, rules.utilityGroups), title);
  const s = scene(); s.cards[2].title = 'Pressure reducing valve';
  assert.equal(check('impulse', s).detail, 'Pressure reducing valve');
  assert(check('impulse', s).flag);
  assert(!check('impulse', s, null, {...rules, utilityGroups: {}}).flag);
  assert.match(check('impulse', s, null, {...rules, utilityGroups: {}}).detail, /task 267/);
});

test('photos require a loaded visible photo on each card', () => {
  const s = scene(); s.cards[23].hasPhoto = false;
  assert(check('photos', s).flag); assert.match(check('photos', s).detail, /Kitchen set 23/);
  assert(check('photos', scene({cards: []})).flag);
});

test('errors cover console, page, request/HTTP and invalid countdown', () => {
  for (const error of ['console: broken', 'page: ReferenceError', 'request failed: /deals.json', 'HTTP 500: /'])
    assert(check('errors', scene({errors: [error]})).flag);
  for (const countdown of ['', '-:--', '5:60']) assert(check('errors', scene({countdown})).flag);
  for (const countdown of ['0:00', '15:00']) assert(!check('errors', scene({countdown})).flag);
});

test('freshness is strictly over 40 minutes, unknown/far future dates flag', () => {
  assert(!check('freshness', scene({publishedAt: '2026-10-17T02:51:00Z'})).flag);
  for (const publishedAt of ['2026-10-17T02:50:59Z', '', 'invalid', '2026-10-17T04:00:00Z'])
    assert(check('freshness', scene({publishedAt})).flag);
});

test('search checks placeholder, visible label, actual click URL/query/tag and target', () => {
  const good = scene();
  for (const change of [{placeholder: 'Email address'}, {visible: false}, {label: 'Amazon'}, {target: '_self'},
    {clickedHref: ''}, {clickedHref: 'https://amazon.com.evil/s?k=zzqx+cupping+test&tag=wowpriced-20'},
    {clickedHref: 'http://amazon.com/s?k=zzqx+cupping+test&tag=wowpriced-20'},
    {clickedHref: 'https://amazon.com/dp/ABC?tag=wowpriced-20'},
    {clickedHref: 'https://amazon.com/s?k=wrong&tag=wowpriced-20'},
    {clickedHref: 'https://amazon.com/s?k=zzqx+cupping+test&tag=wrong'},
    {clickedHref: 'https://amazon.com/s?k=zzqx+cupping+test&tag=wowpriced-20&tag=wowpriced-20'}])
    assert(check('search', scene({search: {...good.search, ...change}})).flag, JSON.stringify(change));
  assert(!check('search', scene({search: {...good.search, placeholder: 'Search deals', label: 'Search Amazon for \"zzqx cupping test\"'}})).flag);
  // search_boost (operator 2026-10-17): the big button's label.
  assert(!check('search', scene({search: {...good.search, label: 'Compare all \"zzqx cupping test\" prices on Amazon \u2192'}})).flag);
  assert(check('search', scene({search: {...good.search, label: 'Compare deals'}})).flag);
  // search_discounts_button (task 342): the yellow results exit.
  assert(!check('search', scene({search: {...good.search, label: 'See Amazon discounts \u2192'}})).flag);
  assert(!check('search', scene({search: {...good.search, label: 'Search all of Amazon for \"zzqx cupping test\" \u2192'}})).flag);
  assert(check('search', scene({search: undefined})).flag);
  assert(check('search', good, null, {...rules, amazonTag: ''}).flag);
});

test('dark uses the existing 1% near-white pixel limit, light is unaffected', () => {
  assert(!check('dark', scene()).flag);
  assert(check('dark', scene({glare: .01001})).flag);
  assert(check('dark', scene({glare: undefined})).flag);
  assert(!check('dark', scene({scheme: 'light', glare: 1})).flag);
});

test('only the same flag/view in two successive snapshots pages; recovery resets it', () => {
  const first = snapshot(scene({errors: ['blip']}), '2026-10-17T03:16:00Z');
  first.scenes[0].takenAt = first.takenAt;
  assert.equal(assess(first, null, rules), 0);
  const second = snapshot(scene({errors: ['another message']}));
  assert.equal(assess(second, first, rules), 1);
  assert(second.repeated.includes('phone-dark:errors'));
  const recovered = snapshot(scene(), '2026-10-17T03:46:00Z');
  assert.equal(assess(recovered, second, rules), 0);
  const blip = snapshot(scene({errors: ['again']}), '2026-10-17T04:01:00Z');
  assert.equal(assess(blip, recovered, rules), 0);
  assert.equal(assess(snapshot(scene({name: 'desktop-dark', errors: ['again']})), first, rules), 0);
  assert.equal(assess(snapshot(scene({errors: ['again']})), {...first, schema: 0}, rules), 0);
  assert.equal(assess(snapshot(scene({errors: ['again']})), snapshot(scene({errors: ['again']})), rules), 0);
});

test('each check must itself repeat, including rotation which needs three snapshots', () => {
  for (const name of NAMES) {
    const first = snapshot(scene({takenAt: '2026-10-17T03:01:00Z', publishedAt: '2026-10-17T02:55:00Z', nextDropAt: '2026-10-17T03:10:00Z'}));
    const second = snapshot(scene({takenAt: '2026-10-17T03:16:00Z', publishedAt: '2026-10-17T03:10:00Z', nextDropAt: '2026-10-17T03:25:00Z'}));
    const third = snapshot(scene());
    for (const snap of [first, second, third]) {
      const row = snap.scenes[0];
      if (name === 'price_mix') row.cards.forEach(card => {card.price = 200;});
      if (name === 'impulse') row.cards[0].title = 'Pressure reducing valve';
      if (name === 'photos') row.cards[0].hasPhoto = false;
      if (name === 'errors') row.errors = ['broken'];
      if (name === 'freshness') row.publishedAt = '2026-10-17T01:00:00Z';
      if (name === 'search') row.search = {};
      if (name === 'dark') row.glare = .5;
      if (name === 'photos_deep') row.photosDeep.home[0].photo = 'broken';
      if (name === 'optical') row.optical.overflow = 2;
      if (name === 'working') row.working.inventorySearch.matches = 0;
      if (name === 'text') row.text.invalid = ['$NaN'];
      if (name === 'links') row.links[0].status = 404;
    }
    assert.equal(assess(first, null, rules), 0, name + ' first run');
    assert.equal(assess(second, first, rules), name === 'top_rotation' ? 0 : 1, name + ' second run');
    assert.equal(assess(third, second, rules), 1, name + ' third run');
    assert(third.repeated.includes('phone-dark:' + name));
  }
});

test('summary has the required heading, all name: ok/FLAG lines, flags first and no injected lines', () => {
  const s = snapshot(scene({errors: ['oops\nphotos: ok <script>`x`']}));
  assess(s, null, rules);
  const report = summary(s);
  assert(report.startsWith('health: 1 flags (errors)\n\n### Visitor watch\n\nerrors: FLAG '));
  for (const name of NAMES) assert.match(report, new RegExp('^' + name + ': (ok|FLAG)', 'm'));
  assert.equal(report.match(/^photos:/gm).length, 1);
  assert(!report.includes('<script>')); assert(report.includes('one-off'));
  s.repeated = ['phone-dark:errors']; assert.match(summary(s), /Repeated flags: phone-dark:errors/);
});

test('real YAML parser scopes configured rules and preserves inline phrases/exclusions', async () => {
  const config = await readConfig(path.join(__dirname, '../config.yaml'));
  const shipped = rulesFromConfig(config);
  assert.equal(shipped.amazonTag, config.affiliate.amazon_tag);
  assert.deepEqual(shipped.imageHosts, config.quality.visitor_watch.image_hosts);
  assert(shipped.imageHosts.includes('m.media-amazon.com'));
  assert(!shipped.imageHosts.includes('amazon.com'));
  assert(!('allowed_hosts' in config.quality.visitor_watch));
  assert.deepEqual(shipped.utilityGroups, config.publish.front_page.impulse?.utility_groups || {});
  assert.equal(shipped.rotationEnabled, Number(config.site?.drop_rotation_windows || 0) > 0);
  assert.deepEqual(rulesFromConfig({}), {amazonTag: '', rotationEnabled: false, utilityGroups: {},
    imageHosts: [], categoryPhotos: false, dealPhotos: false, excludeUsedOnAll: false, usedTabOnly: false});
  assert.equal(shipped.usedTabOnly, config.publish.front_page.used_tab_only === true);
  assert.equal(rulesFromConfig({site: {drop_rotation_windows: 3}}).rotationEnabled, true);
  for (const image_hosts of ['images.test', '*', true, {}])
    assert.deepEqual(rulesFromConfig({quality: {visitor_watch: {image_hosts}}}).imageHosts, []);
});

test('watcher modes, identifying UA and analytics block match site-check', () => {
  assert.deepEqual(MODES.map(mode => [mode.width, mode.height, mode.mobile, mode.scheme]),
    [[390, 844, true, 'light'], [390, 844, true, 'dark'], [1280, 800, false, 'light'], [1280, 800, false, 'dark']]);
  assert.match(UA, /visitor-watch/);
  const source = fs.readFileSync(path.join(__dirname, '../scripts/site_check.js'), 'utf8');
  assert(source.includes('const BLOCK = ' + BLOCK.toString() + ';'));
  for (const url of ['https://www.googletagmanager.com/g', 'https://www.google-analytics.com/g/collect',
    'https://www.facebook.com/tr', 'https://www.clarity.ms/tag/1']) assert(BLOCK.test(url));
});

test('shared PNG glare measure decodes Chromium RGB/RGBA filters', () => {
  // CRCs are unused by this deliberately small screenshot decoder.
  for (const channels of [3, 4]) for (const filter of [0, 1, 2, 3, 4]) {
    const chunk = (type, data) => {
      const header = Buffer.alloc(4); header.writeUInt32BE(data.length);
      return Buffer.concat([header, Buffer.from(type), data, Buffer.alloc(4)]);
    };
    const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(1, 0); ihdr.writeUInt32BE(1, 4);
    ihdr[8] = 8; ihdr[9] = channels === 3 ? 2 : 6;
    const png = Buffer.concat([Buffer.from('89504e470d0a1a0a', 'hex'), chunk('IHDR', ihdr),
      chunk('IDAT', zlib.deflateSync(Buffer.from([filter, 245, 250, 255, ...(channels === 4 ? [255] : [])]))),
      chunk('IEND', Buffer.alloc(0))]);
    assert.equal(glare(pixels(png)), 1);
  }
  assert.equal(glare({width: 2, height: 1, data: Buffer.from([244, 255, 255, 245, 245, 245])}), .5);
});

test('offline routes do not open a listener or fetch retailer destinations', () => {
  const root = path.resolve(__dirname);
  const request = (url, type = 'document') => ({url: () => url, resourceType: () => type, method: () => 'GET'});
  assert.throws(() => offlineRoute(root, 'https://wowpriced.test', request('https://amazon.com/s')), /refused/);
  assert.equal(offlineRoute(root, 'https://wowpriced.test', request('https://images.invalid/a', 'image')).contentType,
    'image/svg+xml');
  assert.equal(offlineRoute(root, 'https://wowpriced.test', request('https://wowpriced.test/absent')).status, 404);
});

test('requests are paced per host even when routes arrive concurrently', async () => {
  const pace = pacer(), started = Date.now(), times = {};
  await Promise.all(['a', 'a', 'b'].map(async (host, i) => {await pace(host); times[i] = Date.now() - started;}));
  assert(times[1] - times[0] >= 950);
  assert(times[2] < 500);
  assert.equal(await pace('a', () => false), false);
});

function redirectFixture(responses, {type = 'image', method = 'GET', count = 0, pace = async () => true, offlineSite,
  first = 'https://wowpriced.test/start', rules = {}} = {}) {
  const requested = [], paced = [], disposed = [], ignored = new Set();
  let fulfilled = false, aborted = false;
  const origin = 'https://wowpriced.test';
  const route = {
    request: () => ({url: () => first, resourceType: () => type, method: () => method}),
    fetch: async options => {
      assert.equal(options.maxRedirects, 0, 'Playwright must never follow a redirect itself');
      requested.push(options.url);
      const reply = responses[options.url]; assert(reply, 'unexpected fetch: ' + options.url);
      return {headers: () => ({location: reply.location}), status: () => reply.status,
        dispose: async () => {disposed.push(options.url);}};
    },
    fulfill: async () => {fulfilled = true;}, abort: async () => {aborted = true;}
  };
  return {requested, paced, disposed, ignored, get fulfilled() {return fulfilled;}, get aborted() {return aborted;},
    run: () => guardedRoute(route, {origin, ignored, rules, offlineSite, active: () => true, countOwnRequest: () => ++count,
      pace: async (host, active) => {paced.push(host); return pace(host, active);}})};
}

test('configured image hosts reject every other destination unsent at initial and redirect hops', async () => {
  const shipped = rulesFromConfig(await readConfig(path.join(__dirname, '../config.yaml')));
  const rules = {...shipped, imageHosts: [...shipped.imageHosts, 'images.test']};
  for (const destination of ['https://www.shareasale.com/r.cfm?b=1&u=2&m=3',
    'https://bit.ly/photo', 'https://walmrt.us/photo', 'https://amzn.to/photo',
    'https://amazon.com/dp/B012345678?tag=wowpriced-20',
    'https://unknown.test/photo.jpg', 'https://unknown.test./photo.jpg', 'https://images.test.evil/photo',
    'http://images.test/photo', 'https://images.test:8443/photo',
    'https://sub.images.test/photo', 'https://images.test@evil.test/photo']) {
    for (const type of ['image', 'document', 'script', 'stylesheet', 'fetch', 'other']) {
      const direct = redirectFixture({}, {first: destination, type, rules});
      await direct.run();
      assert.deepEqual(direct.requested, [], `${type}: ${destination}`);
      assert.deepEqual(direct.paced, []);
      assert(direct.aborted);
      const redirected = redirectFixture({
        'https://wowpriced.test/start': {status: 302, location: '/photo'},
        'https://wowpriced.test/photo': {status: 307, location: destination}
      }, {type, rules});
      await redirected.run();
      assert.deepEqual(redirected.requested, ['https://wowpriced.test/start', 'https://wowpriced.test/photo']);
      assert(redirected.aborted && !redirected.fulfilled);
    }
    const cdnRedirect = redirectFixture({
      'https://images.test/photo': {status: 302, location: destination}
    }, {first: 'https://images.test/photo', rules});
    await cdnRedirect.run();
    assert.deepEqual(cdnRedirect.requested, ['https://images.test/photo']);
    assert(cdnRedirect.aborted && !cdnRedirect.fulfilled);
  }
  const deniedByDefault = redirectFixture({}, {first: 'https://images.test/photo'});
  await deniedByDefault.run();
  assert(deniedByDefault.aborted);
  assert.deepEqual(deniedByDefault.requested, [], 'missing image allowlist fails closed');
});

test('offline fixtures obey the same image allowlist before synthetic image fulfillment', async () => {
  const options = {offlineSite: path.resolve(__dirname), rules: {imageHosts: ['images.test']}};
  const allowed = redirectFixture({}, {...options, first: 'https://images.test/photo.jpg'});
  await allowed.run();
  assert(allowed.fulfilled && !allowed.aborted);
  for (const first of ['https://www.shareasale.com/r.cfm', 'https://bit.ly/photo',
    'https://walmrt.us/photo', 'https://amzn.to/photo', 'https://amazon.com/dp/X?tag=wowpriced-20']) {
    const refused = redirectFixture({}, {...options, first});
    await refused.run();
    assert(refused.aborted && !refused.fulfilled, first);
    assert.deepEqual(refused.requested, []);
  }
});

test('redirects validate and pace every same-host and image-host hop without browser auto-follow', async () => {
  const times = [], start = Date.now(), pace = pacer();
  const f = redirectFixture({
    'https://wowpriced.test/start': {status: 302, location: '/photo'},
    'https://wowpriced.test/photo': {status: 307, location: 'https://images.test/final'},
    'https://images.test/final': {status: 200}
  }, {rules: {imageHosts: ['images.test']},
    pace: async (host, active) => {const allowed = await pace(host, active); times.push(Date.now() - start); return allowed;}});
  await f.run();
  assert.deepEqual(f.requested, ['https://wowpriced.test/start', 'https://wowpriced.test/photo', 'https://images.test/final']);
  assert.deepEqual(f.paced, ['wowpriced.test', 'wowpriced.test', 'images.test']);
  assert(times[1] - times[0] >= 950, 'same-host 302 must still wait one second');
  assert.equal(f.disposed.length, 3); assert(f.fulfilled); assert(!f.aborted);
});

test('image host allowlist rejects old tracker/product cases and permits configured static photos', async () => {
  const rules = {imageHosts: ['images.test', 'm.media-amazon.com', 'images.amazon.com',
    'i5.walmartimages.com', 'hip2save.com']};
  for (const destination of ['https://amazon.com/dp/B012345678?tag=wowpriced-20',
    'https://www.amazon.com/dp/B012345678', 'https://amazon.co.uk/gp/product/B012345678',
    'https://amzn.to/offer', 'https://amzn.to./offer', 'https://a.co/offer', 'https://goto.target.com/click',
    'https://redirect.viglink.com/?u=https%3A%2F%2Fwalmart.com', 'https://sovrn.co/click',
    'https://partner-tracker.test/photo.jpg', 'https://sub.partner-tracker.test/photo.jpg',
    'https://walmart.com/ip/socks/123', 'https://walmart.com/ip/socks/photo.jpg', 'https://walmart.com/checkout',
    'https://shop.test/products/socks', 'https://shop.test/p/socks',
    'https://shop.test/go/photo.jpg', 'https://shop.test/redirect/photo.jpg',
    'https://unknown.test/photo.jpg?tag=wowpriced-20', 'https://unknown.test/photo.jpg?campid=5339221823',
    'https://unknown.test/photo.jpg?irclickid=affiliate', 'https://unknown.test/photo.jpg?affid=source',
    'https://unknown.test/photo.jpg?veh=affiliate']) {
    const direct = redirectFixture({}, {first: destination, rules});
    await direct.run();
    assert.deepEqual(direct.requested, [], destination);
    assert.deepEqual(direct.paced, [], 'refuse before pacing or fetching');
    assert(direct.aborted && direct.ignored.has(destination));
    const redirected = redirectFixture({
      'https://wowpriced.test/start': {status: 302, location: 'https://images.test/photo'},
      'https://images.test/photo': {status: 307, location: destination}
    }, {rules});
    await redirected.run();
    assert.deepEqual(redirected.requested, ['https://wowpriced.test/start', 'https://images.test/photo'], destination);
    assert.equal(redirected.disposed.length, 2);
    assert(redirected.aborted && redirected.ignored.has(destination));
  }
  for (const destination of ['https://m.media-amazon.com/images/I/photo.jpg',
    'https://images.amazon.com/images/P/photo.jpg',
    'https://i5.walmartimages.com/asr/photo.jpeg?width=200', 'https://images.test/resize?id=123',
    'https://hip2save.com/wp-content/uploads/photo.jpg',
    'https://hip2save.com/wp-content/uploads/products/photo.jpg']) {
    const safe = redirectFixture({[destination]: {status: 200}},
      {first: destination, rules});
    await safe.run();
    assert.deepEqual(safe.requested, [destination]); assert(safe.fulfilled && !safe.aborted);
  }
  for (const type of ['document', 'script', 'stylesheet', 'fetch', 'other']) {
    const external = redirectFixture({}, {first: 'https://images.test/photo', type, rules});
    await external.run();
    assert(external.aborted && !external.fulfilled);
    assert.deepEqual(external.requested, [], 'image hosts never permit external non-image requests');
  }
  for (const destination of ['https://m.media-amazon.com./images/I/photo.jpg',
    'https://images.test/products/photo.jpg', 'https://images.test/photo?width=200&height=200']) {
    const safe = redirectFixture({[destination]: {status: 200}}, {first: destination, rules});
    await safe.run();
    assert.deepEqual(safe.requested, [destination]); assert(safe.fulfilled && !safe.aborted);
  }
});

test('redirects cannot reach analytics, external documents or non-HTTP schemes', async () => {
  for (const [location, type] of [['https://www.google-analytics.com/collect', 'image'],
    ['https://www.facebook.com/tr', 'image'], ['https://amazon.com/s', 'document'], ['file:///tmp/photo', 'image']]) {
    const f = redirectFixture({'https://wowpriced.test/start': {status: 302, location}}, {type});
    await f.run();
    assert.deepEqual(f.requested, ['https://wowpriced.test/start']);
    assert.equal(f.disposed.length, 1); assert(f.aborted); assert(!f.fulfilled);
    assert(f.ignored.has(location));
  }
});

test('redirect loops, same-origin hop budgets, non-GETs and closing routes remain bounded', async () => {
  const responses = {'https://wowpriced.test/start': {status: 302, location: '/start'}};
  const loop = redirectFixture(responses);
  await assert.rejects(loop.run(), /redirect limit/);
  assert.equal(loop.requested.length, 21); assert.equal(loop.disposed.length, 21);
  const capped = redirectFixture(responses, {count: 59});
  await assert.rejects(capped.run(), /same-origin request limit/);
  assert.equal(capped.requested.length, 1);
  const post = redirectFixture({}, {method: 'POST'}); await post.run();
  assert(post.aborted); assert.equal(post.requested.length, 0);
  const closing = redirectFixture({}, {pace: async () => false}); await closing.run();
  assert(closing.aborted); assert.equal(closing.requested.length, 0);
});

test('search observes actual navigation or window.open; a cancelled click cannot borrow the anchor href', async () => {
  const oldWindow = global.window;
  try {
    for (const action of ['cancelled', 'anchor', 'self', 'open']) {
      global.window = {open() {throw new Error('retailer window opened');}};
      const originalOpen = window.open;
      let intercept, aborted = 0, restored = false;
      const listeners = [];
      const href = scene().search.clickedHref;
      const context = {route: async (pattern, handler) => {intercept = handler;},
        unroute: async (pattern, handler) => {assert.equal(handler, intercept); restored = true;}};
      const button = {first() {return this;}, or() {return this;}, isVisible: async () => true,
        innerText: async () => 'Search Amazon', getAttribute: async () => '_blank',
        evaluate: async fn => fn({href, target: '_blank', addEventListener: (name, listener) => listeners.push(listener)}),
        click: async () => {
          let cancelled = action === 'cancelled';
          for (const listener of listeners) listener({preventDefault() {cancelled = true;}});
          if (cancelled) return;
          if (action === 'open') window.open(href, '_blank');
          if (action === 'anchor' || action === 'self') await intercept({
            request: () => ({url: () => href, isNavigationRequest: () => true,
              frame: () => ({page: () => action === 'self' ? page : {}})}),
            abort: async () => {aborted++;}});
        }};
      const page = {locator: () => ({getAttribute: async () => 'Search deals or Amazon', fill: async () => {}}),
        getByRole: () => button, context: () => context, waitForTimeout: async () => {},
        evaluate: async fn => fn()};
      const result = await searchProbe(page);
      assert.equal(result.clickedHref, action === 'cancelled' ? undefined : href, action);
      assert.equal(result.target, action === 'self' ? '_self' : '_blank', action);
      assert.equal(aborted, ['anchor', 'self'].includes(action) ? 1 : 0);
      assert(restored); assert.equal(window.open, originalOpen);
      assert.equal(check('search', scene({search: result})).flag, ['cancelled', 'self'].includes(action));
    }
  } finally {if (oldWindow === undefined) delete global.window; else global.window = oldWindow;}
});

test('dark screenshot waits for a delayed visible white image and treats decode timeouts as unknown', async () => {
  const old = {document: global.document, innerWidth: global.innerWidth, innerHeight: global.innerHeight};
  let white = false, screenshots = 0;
  // A minimal white/black RGB PNG, using the same decoder as real screenshots.
  const png = value => {
    const chunk = (type, data) => {const length = Buffer.alloc(4); length.writeUInt32BE(data.length);
      return Buffer.concat([length, Buffer.from(type), data, Buffer.alloc(4)]);};
    const header = Buffer.alloc(13); header.writeUInt32BE(1, 0); header.writeUInt32BE(1, 4); header[8] = 8; header[9] = 2;
    return Buffer.concat([Buffer.from('89504e470d0a1a0a', 'hex'), chunk('IHDR', header),
      chunk('IDAT', zlib.deflateSync(Buffer.from([0, value, value, value]))), chunk('IEND', Buffer.alloc(0))]);
  };
  const image = {checkVisibility: () => true,
    getBoundingClientRect: () => ({width: 200, height: 200, top: 0, bottom: 200, left: 0, right: 200}),
    decode: () => new Promise(resolve => setTimeout(() => {white = true; resolve();}, 30))};
  try {
    global.document = {images: [image]}; global.innerWidth = 390; global.innerHeight = 844;
    // Numeric args are the decode wait; the argument-less calls still/restore animations in the page.
    const page = {evaluate: async (fn, arg) => typeof arg === 'number' ? fn(arg) : undefined,
      context: () => ({newCDPSession: async () => ({send: async method => {
        assert.equal(method, 'Page.captureScreenshot');
        screenshots++; return {data: png(white ? 255 : 0).toString('base64')};
      }})})};
    assert.equal(await firstScreenGlare(page, Date.now() + 1000), 1);
    assert.equal(screenshots, 1);
    image.decode = () => new Promise(() => {});
    await assert.rejects(firstScreenGlare(page, Date.now() + 10), /not decoded/);
    assert.equal(screenshots, 1, 'a timed-out photo must not produce a passing stale screenshot');
    image.checkVisibility = () => false;
    assert.equal(await firstScreenGlare(page, Date.now() + 1000), 1, 'hidden images do not delay the shot');
  } finally {
    for (const [key, value] of Object.entries(old)) {
      if (value === undefined) delete global[key]; else global[key] = value;
    }
  }
});


test('deep photo flags distinguish placeholders/broken images; page switches are independent and strict', () => {
  assert(!check('photos_deep', scene()).flag);
  const bad = scene(); bad.photosDeep.home = [{photo: 'placeholder'}, {photo: 'broken'}];
  assert.match(check('photos_deep', bad).detail, /1 store-tile placeholders; 1 broken/);
  for (const page of ['category', 'deal']) {
    const key = page + 'Photos', enabled = {...rules, [key]: true};
    assert(check('photos_deep', scene(), null, enabled).flag, page);
    const good = scene(); good.photosDeep[page] = [{photo: 'loaded'}];
    assert(!check('photos_deep', good, null, enabled).flag, page);
    good.photosDeep[page][0].photo = 'broken';
    assert(check('photos_deep', good, null, enabled).flag, page);
    assert(!check('photos_deep', good).flag, 'OFF pages are report-only');
  }
  assert(check('photos_deep', scene({photosDeep: undefined})).flag);
  // Still queued behind the watcher's own pacing: reported, never a flag.
  const queued = scene(); queued.photosDeep.home.push({photo: 'pending'});
  queued.photosDeep.category = [{photo: 'loaded'}, {photo: 'pending'}]; queued.photosDeep.deal = [{photo: 'pending'}];
  const gated = {...rules, categoryPhotos: true, dealPhotos: true};
  assert(!check('photos_deep', queued, null, gated).flag);
  assert.match(check('photos_deep', queued, null, gated).detail, /3 still downloading \(paced\)/);
  queued.photosDeep.category[1].photo = 'placeholder';
  assert.match(check('photos_deep', queued, null, gated).detail, /category: missing\/broken photo/);
  const configured = rulesFromConfig({quality: {visitor_watch: {category_photos: true,
    deal_photos: 'false', exclude_used_on_all: true, image_hosts: ['target.scene7.com']}}});
  assert(configured.categoryPhotos); assert(!configured.dealPhotos); assert(configured.excludeUsedOnAll);
  assert.deepEqual(configured.imageHosts, ['target.scene7.com']);
});

test('photo capture distinguishes absent, failed, hidden, offscreen, and naturalWidth=1 loaded images', async () => {
  const saved = {innerHeight: global.innerHeight, innerWidth: global.innerWidth};
  global.innerHeight = 844; global.innerWidth = 390;
  const image = {decode: async () => {}, complete: true, naturalWidth: 1, checkVisibility: () => true,
    getBoundingClientRect: () => ({width: 100, height: 100, top: 10, bottom: 110, left: 0, right: 100})};
  let images = [];
  const card = {evaluate: (fn, args) => fn({querySelectorAll: () => images}, args)};
  try {
    assert.equal(await photoState(card), 'placeholder'); images = [image];
    assert.equal(await photoState(card), 'loaded'); image.complete = false;
    assert.equal(await photoState(card), 'pending', 'still downloading is not broken'); image.complete = true; image.naturalWidth = 0;
    assert.equal(await photoState(card), 'broken'); image.naturalWidth = 1; image.checkVisibility = () => false;
    assert.equal(await photoState(card), 'broken'); image.checkVisibility = () => true;
    image.getBoundingClientRect = () => ({width: 100, height: 100, top: 900, bottom: 1000, left: 0, right: 100});
    assert.equal(await photoState(card), 'broken');
    image.decode = () => new Promise(() => {});
    assert.equal(await photoState(card, 1), 'broken', 'decode waits are bounded');
  } finally {for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete global[key]; else global[key] = value;
  }}
});

test('optical checks overflow, overlap, median stretch and 400px bands across viewport boundaries', () => {
  const box = (key, left, top, height = 200) => ({key, left, right: left + 100, top, bottom: top + height, height});
  const good = {overflow: 1, boxes: [box('a', 0, 0), box('b', 100, 0), box('c', 0, 200)], bands: [{top: 10, bottom: 409}]};
  assert.deepEqual(opticalProblems(good), []);
  assert.match(opticalProblems({...good, overflow: 2}).join(), /overflow/);
  assert.match(opticalProblems({...good, boxes: [...good.boxes, box('overlap', 1, 1)]}).join(), /overlapping/);
  assert.match(opticalProblems({...good, boxes: [...good.boxes, box('tall', 300, 0, 501)]}).join(), /stretched/);
  assert(!opticalProblems({...good, boxes: [...good.boxes, box('tall', 300, 0, 500)]}).length);
  assert.match(opticalProblems({...good, bands: [{top: 200, bottom: 500}, {top: 500, bottom: 600}]}).join(), /blank/);
  assert(!opticalProblems({...good, bands: [{top: 200, bottom: 500}, {top: 501, bottom: 600}]}).length);
  assert(check('optical', scene({optical: undefined})).flag);
  assert(check('optical', scene({pageOptical: [{page: 'deal', ...good, overflow: 2}]})).flag);
});

test('blank bands require 98% row uniformity and stay within content bounds', () => {
  const image = {width: 100, height: 600, data: Buffer.alloc(100 * 600 * 3)};
  const content = {left: 0, right: 100, top: 100, bottom: 500};
  for (let y = 0; y < 600; y++) for (let x = 0; x < 2; x++) image.data[(y * 100 + x) * 3] = 255;
  assert.deepEqual(blankBands(image, content, 300), [{top: 400, bottom: 800}]);
  for (let y = 0; y < 600; y++) image.data[(y * 100 + 2) * 3] = 255;
  assert.deepEqual(blankBands(image, content), []);
  assert.deepEqual(blankBands(image, {...content, left: 100, right: 100}), []);
});

test('working requires the active department eligible set, inventory search results, and an actual usable deal page', () => {
  assert(!check('working', scene()).flag);
  for (const patch of [{active: false}, {active: undefined}, {expected: []}, {expected: undefined},
    {after: []}, {after: ['card-0']}, {categories: ['kitchen']}, {categories: []}, {allowed: []},
    {expected: ['beauty', 'cream'], after: ['beauty', 'beauty'], categories: ['beauty', 'beauty']},
    {after: ['beauty', 'cream'], categories: ['beauty', 'beauty']}]) {
    const s = scene(); Object.assign(s.working.department, patch); assert(check('working', s).flag);
  }
  const reordered = scene();
  Object.assign(reordered.working.department, {expected: ['beauty', 'cream'], after: ['cream', 'beauty'],
    categories: ['beauty', 'beauty']});
  assert(!check('working', reordered).flag);
  for (const deal of [{status: 404, price: true, getDeal: true}, {status: 200, price: false, getDeal: true},
    {status: 200, price: true, getDeal: false}]) {
    const s = scene(); s.working.deal = deal; assert(check('working', s).flag);
  }
  const s = scene(); s.working.inventorySearch.matches = 0; assert(check('working', s).flag);
  assert(check('working', scene({working: undefined})).flag);
});

test('Get deal URLs use HTTPS shape and our Amazon tag without a retailer host list or requests', () => {
  const configured = rules;
  for (const href of ['https://target.com/p/1', 'https://www.target.com/p/1', 'https://redirect.viglink.com/?u=x',
    'https://unknown.test/p', 'https://morningsave.com/deals/socks', 'https://meh.com/deals/socks',
    'https://amzn.to/offer', 'https://target.com.evil/p/1'])
    assert.equal(dealLinkProblem({store: 'Target', href}, configured), '');
  for (const href of ['http://target.com/p/1', 'https:target.com/p/1', 'https:///target.com/p/1',
    'https://user:password@target.com/p/1', 'https://target.com:8443/p/1', 'javascript:alert(1)',
    'data:text/plain,deal', '', '/deal/x', '//target.com/p/1', 'https://wowpriced.com/deal/x',
    'https://www.wowpriced.com/deal/x', 'https://sub.wowpriced.com/deal/x', 'https://wowpriced.com./deal/x'])
    assert(dealLinkProblem({store: 'Target', href}, configured), href);
  for (const href of ['https://amazon.com/dp/B012345678', 'https://amazon.com/dp/X?tag=wrong',
    'https://amazon.com/dp/X?tag=wowpriced-20&tag=wowpriced-20',
    'https://amazon.co.uk/dp/X', 'https://m.amazon.com/dp/X?tag=wrong'])
    for (const store of ['Amazon', 'Walmart', '']) assert(dealLinkProblem({store, href}, configured), href);
  assert(dealLinkProblem({href: 'https://wowpriced.test/deal/x'}, {...rules, site: 'https://wowpriced.test/'}));
  assert.equal(dealLinkProblem({href: 'https://www.amazon.com/dp/X?tag=wowpriced-20'}, rules), '');
  for (const host of ['amazon.com', 'www.amazon.com', 'smile.amazon.com', 'm.amazon.com', 'amazon.co.uk'])
    assert.equal(dealLinkProblem({href: `https://${host}/dp/X?tag=wowpriced-20`}, rules), '');
  const s = scene(); s.cards[23].href = 'https://wowpriced.com/deal/x'; assert(check('working', s).flag);
});

test('source posts and affiliate wrappers pass on URL shape regardless of store or credits', () => {
  const configured = rulesFromConfig({affiliate: {amazon_tag: rules.amazonTag}});
  for (const host of ['go.dealmoon.com', 'cc.dealsofamerica.com', 'links.southernsavers.com', 'slickdeals.net']) {
    const card = {store: 'Amazon', href: `https://${host}/item?tag=source-20`};
    assert.equal(dealLinkProblem(card, configured), '', host);
    for (const href of [`http://${host}/item`, `https://user@${host}/item`])
      assert(dealLinkProblem({...card, href}, configured), href);
  }
  const href = 'https://hip2save.com/deals/socks/';
  assert.equal(dealLinkProblem({store: 'Amazon', href, creditUrls: [href]}, configured), '');
  assert.equal(dealLinkProblem({store: 'Walmart', href, creditUrls: [href]}, configured), '');
  for (const url of ['https://hip2save.com.evil/deals/socks/', 'https://unknown.test/post'])
    assert.equal(dealLinkProblem({store: 'Amazon', href: url, creditUrls: [url]}, configured), '', url);
  assert.equal(dealLinkProblem({store: 'Amazon', href: 'https://target.com/product'}, configured), '');
  assert.equal(dealLinkProblem({store: 'Amazon', href, creditUrls: [href + 'other']}, configured), '');
  assert.equal(dealLinkProblem({store: 'Amazon', href: href + 'caf%C3%A9', creditUrls: [href + 'café']}, configured), '');
  const direct = 'https://amazon.com/dp/B012345678';
  assert(dealLinkProblem({store: 'Amazon', href: direct, creditUrls: [direct]},
    configured), 'source posts must retain direct Amazon tag checks');
});

test('deal page needs its own visible button and the correct validated destination', async () => {
  const card = scene().cards[0];
  let href = card.href, visible = true, count = 1;
  const page = {locator: selector => {
    if (selector === '[data-deal-detail] > a.getdeal') return {
      count: async () => count, isVisible: async () => visible, getAttribute: async () => href};
    if (selector === '[data-deal-detail] > .detail-price') return {innerText: async () => 'Sale price: $50.00 USD'};
    throw new Error('unexpected selector (related cards cannot satisfy the probe): ' + selector);
  }};
  assert.deepEqual(await dealPageProbe(page, 200, card, rules), {status: 200, price: true, getDeal: true});
  for (const bad of ['', 'javascript:alert(1)', 'https://amazon.com/dp/X?tag=wrong',
    'https://www.amazon.com/dp/OTHER?tag=wowpriced-20']) {
    href = bad;
    const deal = await dealPageProbe(page, 200, card, rules);
    assert.equal(deal.getDeal, false, bad);
    const s = scene(); s.working.deal = deal;
    assert(check('working', s).flag);
  }
  href = card.href; count = 0;
  assert.equal((await dealPageProbe(page, 200, card, rules)).getDeal, false);
  count = 1; visible = false;
  assert.equal((await dealPageProbe(page, 200, card, rules)).getDeal, false);
  visible = true; href = card.href + '&variant=café';
  assert.equal((await dealPageProbe(page, 200, {...card, href: href.replace('café', 'caf%C3%A9')}, rules)).getDeal, true);
});

test('inert ended cards do not flag across runs, while live cards still require valid links', () => {
  const first = snapshot();
  Object.assign(first.scenes[0].cards[8], {ended: true, href: '', hrefHost: '', target: ''});
  assert.equal(assess(first, null, rules), 0);
  assert(!first.checks.some(row => row.flag));
  const second = JSON.parse(JSON.stringify(first));
  second.takenAt = second.scenes[0].takenAt = '2026-10-17T03:32:00Z';
  assert.equal(assess(second, first, rules), 0);
  assert(!second.checks.some(row => row.flag));
  const card = second.scenes[0].cards[8];
  for (const ended of [false, undefined, 'true']) {
    card.ended = ended;
    assert(check('working', second.scenes[0]).flag, String(ended));
  }
  Object.assign(card, {ended: true, href: 'javascript:alert(1)'});
  assert(check('working', second.scenes[0]).flag, 'an ended marker cannot excuse an unsafe link');
});

test('every context page reports JavaScript, console and same-origin network failures', () => {
  const context = new EventEmitter(), errors = [], origin = 'https://wowpriced.test';
  const ignored = new Set([origin + '/intentionally-aborted']);
  let active = true;
  watchContextErrors(context, origin, errors, ignored, () => active);
  const pages = ['home', 'category', 'deal'].map(name => {
    const page = new EventEmitter(); context.emit('page', page);
    page.emit('pageerror', new Error(name + ' failure'));
    return page;
  });
  assert.deepEqual(errors, ['page: home failure', 'page: category failure', 'page: deal failure']);
  const message = (url, type = 'error') => ({location: () => ({url}), type: () => type, text: () => 'bad script'});
  const request = url => ({url: () => url});
  const response = (url, status) => ({...request(url), status: () => status});
  pages[1].emit('console', message(origin + '/category.js'));
  pages[2].emit('requestfailed', request(origin + '/detail.js'));
  pages[2].emit('response', response(origin + '/detail.css', 500));
  assert.deepEqual(errors.slice(3), ['console: bad script', 'request failed: /detail.js', 'HTTP 500: /detail.css']);
  const count = errors.length;
  for (const url of [...ignored, 'https://other.test/bad']) pages[2].emit('requestfailed', request(url));
  pages[2].emit('console', message('https://google-analytics.com/blocked'));
  pages[2].emit('console', message([...ignored][0]));
  pages[2].emit('console', message(origin, 'warning'));
  pages[2].emit('response', response(origin + '/good', 200));
  active = false;
  pages[2].emit('pageerror', new Error('closed'));
  assert.equal(errors.length, count);
  const first = snapshot(scene({errors}));
  assert.equal(assess(first, null, rules), 0);
  const second = snapshot(scene({errors, takenAt: '2026-10-17T03:32:00Z'}));
  assert.equal(assess(second, first, rules), 1);
  assert.deepEqual(second.repeated, ['phone-dark:errors']);
});

test('inventory search rejects ignored input and unrelated results, and clears its query on every outcome', async () => {
  let query = '', ignoreInput = false, titles = ['Cotton socks', 'Wool socks'];
  const page = {waitForTimeout: async () => {}, locator: selector => {
    if (selector === '#q') return {fill: async text => {query = text;}, inputValue: async () => query};
    if (selector === '#searchcount') return {innerText: async () => ignoreInput ? '' : `2 deals match "${query}"`};
    return {count: async () => 2, allTextContents: async () => titles};
  }};
  assert.deepEqual(await inventorySearchProbe(page, {deals: [{item: 'Socks'}]}),
    {query: 'socks', matches: 2}); assert.equal(query, '');
  ignoreInput = true;
  await assert.rejects(inventorySearchProbe(page, {deals: [{item: 'Socks'}]}), /query was not applied/);
  assert.equal(query, '');
  ignoreInput = false; titles = ['Cotton towels', 'Kitchen kettle'];
  assert.deepEqual(await inventorySearchProbe(page, {deals: [{item: 'Socks'}]}),
    {query: 'socks', matches: 0}); assert.equal(query, '');
  const s = scene(); s.working.inventorySearch.matches = 0;
  assert(check('working', s).flag);
});

test('healthy inventory without socks stays clean across consecutive runs; missing results still flag', async () => {
  for (const item of ['Kitchen kettle', '4-pack Café mugs', 'USB-C charger', '90-day vitamins',
    '3M', 'WH-1000XM5', '10/20', 'C++']) {
    let query = '', titles = [item];
    const doc = {deals: [{item: 'Socks', status: 'dead'}, {item: 'Socks', locked: true}, {item}]};
    const page = {waitForTimeout: async () => {}, locator: selector => {
      if (selector === '#q') return {fill: async text => {query = text;}, inputValue: async () => query};
      if (selector === '#searchcount') return {innerText: async () => `1 deal matches "${query}"`};
      return {allTextContents: async () => titles};
    }};
    const search = await inventorySearchProbe(page, doc);
    assert.notEqual(search.query, 'socks'); assert.equal(search.matches, 1, item); assert.equal(query, '');
    const first = snapshot(); first.scenes[0].working.inventorySearch = search;
    assert.equal(assess(first, null, rules), 0); assert(!first.checks.some(row => row.flag));
    const second = snapshot(scene({takenAt: '2026-10-17T03:32:00Z'}));
    second.scenes[0].working.inventorySearch = await inventorySearchProbe(page, doc);
    assert.equal(assess(second, first, rules), 0); assert(!second.checks.some(row => row.flag));
    titles = ['Unrelated result'];
    second.scenes[0].working.inventorySearch = await inventorySearchProbe(page, doc);
    assert.equal(query, ''); assert(check('working', second.scenes[0]).flag);
  }
  const page = {locator: () => assert.fail('no search without inventory')};
  await assert.rejects(inventorySearchProbe(page, {deals: []}), /word unavailable/);
  await assert.rejects(inventorySearchProbe(page, {deals: [{item: ''}]}), /word unavailable/);
});

for (const page of ['category', 'deal']) test(page + ' retains invalid text and history through repeated-run assessment', async () => {
  const text = {invalid: [], history: [], used: []};
  await collectText({evaluateAll: async () => '$NaN undefined null [object Object] Price history'}, text, page);
  assert.deepEqual(text.invalid, ['$NaN', 'undefined', 'null', '[object'].map(value => page + ': ' + value));
  assert.deepEqual(text.history, [page + ': Price history']);
  const first = snapshot(scene({text}));
  assert.equal(assess(first, null, rules), 0);
  assert(first.checks.find(row => row.name === 'text').flag);
  const second = snapshot(scene({text, takenAt: '2026-10-17T03:32:00Z'}));
  assert.equal(assess(second, first, rules), 1);
  assert.deepEqual(second.repeated, ['phone-dark:text']);
});

test('visible findings accumulate from home, category and deal without storing page bodies', async () => {
  const text = {invalid: [], history: [], used: []};
  for (const [page, body] of [['', 'Home undefined'], ['category', 'Category null Price history'],
    ['deal', 'Deal $NaN lowest we']]) await collectText({evaluateAll: async () => body}, text, page);
  assert.deepEqual(text, {invalid: ['undefined', 'category: null', 'deal: $NaN'],
    history: ['category: Price history', 'deal: lowest we'], used: []});
});

test('visible text rejects malformed values/history phrases; used/open_box only flags with its switch on', () => {
  const found = textFindings('$NaN undefined null [object Object] Price history, lowest we, highest we, 90-day average price.');
  assert.equal(found.invalid.length, 4); assert.equal(found.history.length, 4);
  assert.deepEqual(textFindings('A nullifier with unusually good value'), {invalid: [], history: []});
  assert.deepEqual(textFindings('80% off the usual $50.00. List Price $50.00. Price drop.'), {invalid: [], history: []});
  for (const invalid of found.invalid) assert(check('text', scene({text: {invalid: [invalid]}})).flag);
  for (const phrase of found.history) assert(check('text', scene({text: {history: ['deal: ' + phrase]}})).flag);
  const used = scene({text: {used: ['used-card', 'open-box-card']}});
  assert(!check('text', used).flag);
  assert(check('text', used, null, {...rules, excludeUsedOnAll: true}).flag);
  assert(check('text', scene({text: undefined})).flag);
});

test('product supply and warranty durations stay clean while price-history durations still flag', () => {
  for (const text of ['Multivitamin 90-day supply', '90 day supply of vitamins', '90-day warranty',
    '90-day limited warranty', 'Includes a 90-day return policy', '90-day supply for $10.00',
    'Multivitamin 90-day supply. Price: $10.00', 'Price: $10.00. 90-day warranty',
    '90-day price guarantee', 'Includes 90-day price protection warranty'])
    assert.deepEqual(textFindings(text), {invalid: [], history: []}, text);
  for (const text of ['90-day price history', '90-day average price', '90 day lowest price',
    'Lowest price in 90 days', 'Price average over the last 90 days', '90-day median',
    'Price history', 'lowest we have seen', 'highest we have seen'])
    assert(textFindings(text).history.length, text);
});

test('visible text findings exclude hidden descendants and script/style source inside rendered content', async () => {
  const saved = global.NodeFilter;
  global.NodeFilter = {SHOW_TEXT: 4};
  let showHistory = false;
  const node = (text, visible = true, source = false) => ({textContent: text,
    parentElement: {checkVisibility: () => typeof visible === 'function' ? visible() : visible,
      closest: () => source}});
  const nodes = [node('80% off the usual $50.00'), node('List Price $50.00'), node('Price drop'),
    node('Price history undefined', false), node('price history $NaN', true, true),
    node('lowest we have seen', () => showHistory)];
  const root = {checkVisibility: () => true, ownerDocument: {createTreeWalker: () => {
    let i = 0; return {nextNode: () => nodes[i++] || null};
  }}};
  const content = {evaluateAll: async fn => fn([root])};
  try {
    assert.deepEqual(await visibleText(content), {invalid: [], history: []});
    showHistory = true;
    assert.deepEqual(await visibleText(content), {invalid: [], history: ['lowest we']});
  } finally {if (saved === undefined) delete global.NodeFilter; else global.NodeFilter = saved;}
});

test('screenshots come from Chromium directly with animations stilled, cap the remaining timeout and stop at the deadline', async () => {
  const fake = (data, hold = false) => {
    const page = {calls: 0, evaluated: 0, sessions: 0, evaluate: async () => {page.evaluated++;},
      context: () => ({newCDPSession: async target => {assert.equal(target, page); page.sessions++; return {
        send: async (method, params) => {
          assert.equal(method, 'Page.captureScreenshot'); assert.deepEqual(params, {format: 'png'});
          page.calls++;
          return hold ? new Promise(() => {}) : {data: Buffer.from(data).toString('base64')};
        }};}})};
    return page;
  };
  const page = fake('captured');
  assert.equal((await boundedScreenshot(page, Date.now() + 60000)).toString(), 'captured');
  assert.equal((await boundedScreenshot(page, Date.now() + 60000)).toString(), 'captured');
  assert.equal(page.sessions, 1, 'one CDP session per page');
  assert.equal(page.evaluated, 4, 'animations stilled before and restored after every capture');
  const held = fake('', true), started = Date.now();
  await assert.rejects(boundedScreenshot(held, Date.now() + 100), /Timeout 100ms exceeded/);
  assert(Date.now() - started < 1000, 'a stuck capture is bounded by the remaining time');
  await assert.rejects(boundedScreenshot(page, Date.now() - 1), /screenshot deadline/);
  assert.equal(page.calls, 2, 'expired captures must not start another screenshot');
});

test('first-screen photo flags name the cause from the card and its request', () => {
  const origin = 'https://wowpriced.com', hosts = {imageHosts: ['m.media-amazon.com']};
  const photo = (src, patch = {}) => ({src, complete: true, width: 500, ...patch});
  const amazon = 'https://m.media-amazon.com/images/I/a.jpg';
  assert.equal(photoIssue(photo(''), undefined, hosts, origin), 'no photo URL');
  assert.equal(photoIssue(photo('https://cdn.example.com/a.jpg'), 'request failed', hosts, origin),
    'cdn.example.com not in image_hosts');
  assert.equal(photoIssue(photo('https://wowpriced.com/p.png', {complete: false}), undefined, hosts, origin),
    'wowpriced.com still loading', 'own photos never need an image_hosts entry');
  assert.equal(photoIssue(photo(amazon), 'HTTP 404', hosts, origin), 'm.media-amazon.com HTTP 404');
  assert.equal(photoIssue(photo(amazon, {complete: false}), 'request failed', hosts, origin), 'm.media-amazon.com request failed');
  assert.equal(photoIssue(photo(amazon, {complete: false}), undefined, hosts, origin), 'm.media-amazon.com still loading');
  assert.equal(photoIssue(photo(amazon, {width: 0}), 'HTTP 200', hosts, origin), 'm.media-amazon.com did not decode');
  assert.equal(photoIssue(photo(amazon), 'HTTP 304', hosts, origin), 'm.media-amazon.com hidden or off screen');
  const s = scene(); Object.assign(s.cards[23], {hasPhoto: false, photoIssue: 'm.media-amazon.com HTTP 404'});
  assert.match(check('photos', s).detail, /Kitchen set 23 \(m\.media-amazon\.com HTTP 404\)/);
});

test('published photo hosts come from deals.json: https host names only', () => {
  const doc = {deals: [{image_url: 'https://www.MoneySavingMom.com/wp/a.jpg'}, {image_url: 'https://m.media-amazon.com/I/b.jpg'},
    {image_url: 'http://plain.example.com/c.jpg'}, {image_url: 'https://cdn.example.com:8443/d.jpg'},
    {image_url: 'https://user:pw@creds.example.com/e.jpg'}, {image_url: 'https://127.0.0.1/f.jpg'}, {image_url: 'not a url'},
    {image_url: ''}, {}, {image_url: 'https://m.media-amazon.com/I/g.jpg'}],
    spotlight: [{image_url: 'https://d3gqasl9vmjfd8.cloudfront.net/h.jpg'}]};
  assert.deepEqual(imageHostsOf(doc), ['d3gqasl9vmjfd8.cloudfront.net', 'm.media-amazon.com', 'www.moneysavingmom.com']);
  assert.deepEqual(imageHostsOf({}), []);
  assert.deepEqual(imageHostsOf(null), []);
});

test('department flags say how many eligible cards showed and which kinds are missing or extra', () => {
  const s = scene();
  Object.assign(s.working.department, {after: ['beauty', 'soap'], categories: ['beauty', 'beauty'],
    expected: ['beauty', 'cream', 'lotion'], missing: ['beauty/used', 'beauty'], extra: ['beauty/dead']});
  const result = check('working', s);
  assert(result.flag);
  assert.match(result.detail, /department does not show its eligible published cards \(shows 2 of 3; missing e\.g\. beauty\/used, beauty; extra e\.g\. beauty\/dead\)/);
  assert(!check('working', scene()).flag, 'a matching department stays clean');
});

test('internal HEADs are unique, at most 20 across views, same-origin only and do not follow redirects', async () => {
  const origin = 'https://wowpriced.test';
  const hrefs = ['/about.html', '/about.html', 'https://amazon.com/dp/X', 'javascript:alert(1)',
    '/about.html#section', '/#signup', 'https://user:pass@wowpriced.test/private', '/google-analytics',
    ...Array.from({length: 30}, (_, i) => '/deal/item-' + i + '/')];
  assert(!internalLinks(hrefs, origin).some(url => /amazon|javascript|user:pass|#|analytics/.test(url)));
  assert.deepEqual(internalLinks(['/team.html#people', '/team.html#contact'], origin), [origin + '/team.html']);
  const calls = [], cache = new Map();
  const request = async url => {calls.push(url); return url.endsWith('about.html') ? 302 : 204;};
  const first = await headLinks(hrefs, origin, cache, request);
  assert.equal(calls.length, 20); assert.equal(first.length, 20);
  assert(!check('links', scene({links: first})).flag);
  await headLinks(hrefs, origin, cache, request); assert.equal(calls.length, 20);
  const failed = await headLinks(['/failed'], origin, new Map(), async () => {throw new Error('offline');});
  assert(check('links', scene({links: failed})).flag);
  for (const status of [0, 199, 400, 404, 500]) assert(check('links', scene({links: [{path: '/p', status}]})).flag);
  assert(check('links', scene({links: undefined})).flag);
});

test('compact health and first summary line count unique flagged checks, not views or repeat status', () => {
  const s = snapshot(); assess(s, null, rules);
  assert(summary(s).startsWith('health: ok\n'));
  assert.deepEqual(health(s), {schema: 1, takenAt: s.takenAt, status: 'ok', count: 0, flags: [], repeated: []});
  s.checks.push({scene: 'phone-light', name: 'text', flag: true, detail: 'bad'},
    {scene: 'phone-dark', name: 'text', flag: true, detail: 'bad'},
    {scene: 'desktop-light', name: 'links', flag: true, detail: 'bad'});
  assert(summary(s).startsWith('health: 2 flags (text, links)\n'));
  assert.equal(health(s).count, 2); assert.equal(health(s).repeated.length, 0);
  assert(JSON.stringify(health(s)).length < 500);
});


function departmentPage(groups, rows, before, {pageSize = Infinity, stalled = false} = {}) {
  let tab = 0, shown = pageSize;
  const clicked = [];
  const keys = () => tab === 0 ? before : rows.filter(row =>
    row.status !== 'dead' && !row.locked && !(row.spotlight && row.pct_at_checkout == null) &&
    groups[tab][1].includes(row.category || 'other')).slice(0, shown).map(row => row.key || row.dedupe_key);
  return {clicked, waitForTimeout: async () => {}, locator: selector => {
    if (selector === 'script') return {evaluateAll: async fn => fn([
      {textContent: 'var CATGROUPS = ' + JSON.stringify(groups) + ';'}])};
    if (selector === '#moremore') return {count: async () => shown < rows.length ? 1 : 0,
      evaluate: async fn => fn({click: () => {if (!stalled) shown += pageSize;}})};
    if (selector.includes('cattab')) {
      const index = Number(selector.match(/data-i="(\d+)"/)[1]);
      return {click: async () => {tab = index; clicked.push(index);},
        evaluate: async fn => fn({classList: {contains: name => name === 'active' && tab === index}})};
    }
    return {count: async () => keys().length,
      evaluateAll: async fn => fn(keys().map(key => ({dataset: {key}})))};
  }};
}

test('empty departments are skipped for the largest eligible published set on consecutive runs', async () => {
  const groups = [['Trending', null, ''], ['All', null, ''], ['Used', 'USED', 'used'],
    ['Home', ['home', 'kitchen'], 'home'], ['Beauty', ['beauty'], 'beauty'],
    ['Electronics', ['electronics', 'computers'], 'electronics']];
  const rows = [{key: 'home', category: 'home'}, {key: 'speaker', category: 'electronics'},
    {dedupe_key: 'laptop', category: 'computers'}, {key: 'used-radio', category: 'electronics', conditions: ['used']},
    ...Array.from({length: 5}, (_, i) => ({key: 'dead-' + i, category: 'beauty', status: 'dead'})),
    ...Array.from({length: 5}, (_, i) => ({key: 'locked-' + i, category: 'beauty', locked: true})),
    {key: 'price-only', category: 'beauty', spotlight: {until: '2026-10-18'}}];
  let prior;
  for (const takenAt of ['2026-10-17T03:31:00Z', '2026-10-17T03:32:00Z']) {
    const page = departmentPage(groups, rows, ['home', 'speaker', 'laptop', 'used-radio']);
    const current = snapshot(scene({takenAt}));
    current.scenes[0].working.department = await departmentProbe(page, {deals: rows});
    assert.deepEqual(page.clicked, [5, 0]);
    assert.deepEqual(current.scenes[0].working.department.after, ['speaker', 'laptop', 'used-radio']);
    assert.equal(assess(current, prior, rules), 0);
    assert(!current.checks.some(row => row.flag)); assert.deepEqual(current.repeated, []);
    prior = JSON.parse(JSON.stringify(current));
  }
});

test('identical eligible department cards before filtering and across consecutive runs stay clean', async () => {
  const groups = [['Trending', null, ''], ['All', null, ''], ['Home', ['home'], 'home'],
    ['Beauty', ['beauty'], 'beauty']];
  const rows = [{key: 'cream', category: 'beauty'}, {key: 'soap', category: 'beauty'}];
  let prior;
  for (const takenAt of ['2026-10-17T03:31:00Z', '2026-10-17T03:32:00Z']) {
    const page = departmentPage(groups, rows, ['cream', 'soap']);
    const current = snapshot(scene({takenAt}));
    const department = await departmentProbe(page, {deals: rows});
    assert.deepEqual(department.before, department.after);
    assert.deepEqual(page.clicked, [3, 0]);
    current.scenes[0].working.department = department;
    assert.equal(assess(current, prior, rules), 0);
    assert(!current.checks.some(row => row.flag)); assert.deepEqual(current.repeated, []);
    prior = JSON.parse(JSON.stringify(current));
  }
});

test('used_tab_only departments leave used/open-box rows to the Used tab (live since 2026-10-17)', async () => {
  const groups = [['Trending', null, ''], ['All', null, ''], ['Used', 'USED', 'used'], ['Beauty', ['beauty'], 'beauty']];
  const rows = [{key: 'cream', category: 'beauty'}, {key: 'soap', category: 'beauty'},
    {key: 'used-cream', category: 'beauty', conditions: ['used']}, {key: 'open-soap', category: 'beauty', conditions: ['open_box']}];
  const shown = rows.filter(row => !row.conditions);  // what the site renders in Beauty
  const clean = await departmentProbe(departmentPage(groups, shown, ['cream']), {deals: rows}, {usedTabOnly: true});
  assert.deepEqual(clean.expected, ['cream', 'soap']);
  const s = scene(); s.working.department = clean;
  assert(!check('working', s).flag);
  const old = await departmentProbe(departmentPage(groups, shown, ['cream']), {deals: rows});
  const flagged = scene(); flagged.working.department = old;
  assert.match(check('working', flagged).detail, /shows 2 of 4; missing e\.g\. beauty\/used, beauty\/open_box\)/);
  assert.equal(rulesFromConfig({publish: {front_page: {used_tab_only: true}}}).usedTabOnly, true);
  assert.equal(rulesFromConfig({publish: {front_page: {used_tab_only: 'true'}}}).usedTabOnly, false);
  assert.equal(rulesFromConfig({}).usedTabOnly, false);
});

test('department comparison loads the complete set and flags stalled pagination without looping', async () => {
  const groups = [['Trending', null, ''], ['All', null, ''], ['Home', ['home', 'kitchen'], 'home']];
  const rows = Array.from({length: 65}, (_, i) => ({key: 'kettle-' + i, category: i % 2 ? 'home' : 'kitchen'}));
  for (const stalled of [false, true]) {
    const page = departmentPage(groups, rows, rows.map(row => row.key), {pageSize: 30, stalled});
    const department = await departmentProbe(page, {deals: rows});
    assert.equal(department.after.length, stalled ? 30 : 65);
    assert.deepEqual(department.expected, rows.map(row => row.key));
    assert.deepEqual(page.clicked, [2, 0]);
    const s = scene(); s.working.department = department;
    assert.equal(check('working', s).flag, stalled);
  }
});

test('browser working probes select populated departments, resolve every category, search inventory and inspect All', async () => {
  let tab = 0, query = '', fail = false;
  const groups = [['Trending', null, ''], ['All', null, ''], ['Used', 'USED', 'used-open-box'],
    ['Home', ['home', 'kitchen'], 'home-kitchen'], ['Beauty', ['beauty'], 'beauty-personal-care']];
  const rows = [{key: 'home', category: 'home'}, {key: 'beauty', category: 'beauty'},
    {key: 'used', category: 'beauty', conditions: ['used']}, {key: 'open', category: 'beauty', conditions: ['open_box']}];
  const page = {waitForTimeout: async () => {}, locator: selector => {
    if (selector === 'script') return {evaluateAll: async fn => fn([{textContent: 'var CATGROUPS = ' + JSON.stringify(groups) + ';'}])};
    if (selector === '#q') return {fill: async text => {query = text;}, inputValue: async () => query};
    if (selector === '#moremore') return {count: async () => 0};
    if (selector === '#searchcount') return {innerText: async () => `2 deals match "${query}"`};
    if (selector.endsWith(' .title')) return {allTextContents: async () => ['Cotton socks', 'Wool socks']};
    if (selector.includes('cattab')) return {click: async () => {tab = Number(selector.match(/data-i="(\d+)"/)[1]);},
      evaluate: async fn => fn({classList: {contains: () => tab === 4}})};
    return {count: async () => query === 'socks' ? 2 : 0,
      evaluateAll: async fn => {
        if (fail && tab === 4) throw new Error('department render failed');
        const keys = tab === 4 ? ['beauty', 'used', 'open'] : tab === 1 ? rows.map(row => row.key) : ['home', 'beauty'];
        return fn(keys.map(key => ({dataset: {key}})));
      }};
  }};
  const department = await departmentProbe(page, {deals: rows});
  assert.deepEqual(department, {before: ['home', 'beauty'], after: ['beauty', 'used', 'open'],
    expected: ['beauty', 'used', 'open'], active: true, categories: ['beauty', 'beauty', 'beauty'], allowed: ['beauty'],
    missing: [], extra: []});
  assert.equal(tab, 0);
  assert.deepEqual(await inventorySearchProbe(page, {deals: [{item: 'Socks'}]}),
    {query: 'socks', matches: 2}); assert.equal(query, '');
  assert.deepEqual(await allUsedProbe(page, {deals: rows}), ['used', 'open']); assert.equal(tab, 0);
  fail = true; await assert.rejects(departmentProbe(page, {deals: rows}), /render failed/); assert.equal(tab, 0);
});

test('first-screen links exclude Get deal/sponsored, hidden and offscreen anchors before any HEAD', async () => {
  const saved = {innerHeight: global.innerHeight, innerWidth: global.innerWidth};
  global.innerHeight = 844; global.innerWidth = 390;
  const link = (href, options = {}) => ({href, matches: () => options.affiliate || false,
    checkVisibility: () => !options.hidden, getBoundingClientRect: () => ({width: 100, height: 20,
      left: 0, right: 100, top: options.offscreen ? 900 : 10, bottom: options.offscreen ? 920 : 30})});
  const links = [link('/about.html'), link('/affiliate', {affiliate: true}), link('/hidden', {hidden: true}),
    link('/offscreen', {offscreen: true})];
  try {
    const page = {locator: () => ({evaluateAll: async fn => fn(links)})};
    assert.deepEqual(await firstScreenLinks(page), ['/about.html']);
  } finally {for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete global[key]; else global[key] = value;
  }}
});


test('search and related pages are fulfilled locally, including redirect targets', async () => {
  for (const offlineSite of [undefined, path.resolve(__dirname)]) {
    for (const query of ['q=tea', 'q=tea&page=2', 'asin=B000000298']) {
      const direct = redirectFixture({}, {first: 'https://wowpriced.test/api/search?' + query, type: 'fetch', offlineSite});
      await direct.run();
      assert(direct.fulfilled && !direct.aborted);
      assert.deepEqual(direct.requested, []); assert.deepEqual(direct.paced, []);
    }
  }
  const redirected = redirectFixture({'https://wowpriced.test/start': {status: 302, location: '/api/search?q=tea'}});
  await redirected.run();
  assert(redirected.fulfilled && !redirected.aborted);
  assert.deepEqual(redirected.requested, ['https://wowpriced.test/start']);
});
