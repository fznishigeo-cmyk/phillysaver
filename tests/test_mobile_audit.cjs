const assert = require('node:assert/strict');
const {test} = require('node:test');
const Module = require('node:module');
// Importing the audit for its math must not load Playwright or run the CLI.
const load = Module._load;
Module._load = function (name, ...args) {
  assert.notEqual(name, 'playwright');
  return load.call(this, name, ...args);
};
const {rect, pseudoRect, union, effectiveRect} = require('../scripts/mobile_audit.cjs');
Module._load = load;

test('audit intercepts requests before navigation using the site-check block pattern', async () => {
  const fs = require('node:fs');
  const site = fs.readFileSync(require.resolve('../scripts/site_check.js'), 'utf8');
  const audit = fs.readFileSync(require.resolve('../scripts/mobile_audit.cjs'), 'utf8');
  assert.equal(site.match(/const BLOCK = (.*);/)[1], audit.match(/const BLOCK = (.*);/)[1]);
  assert(audit.indexOf('await guardPage(page, base)') < audit.indexOf('await page.goto('));
  let handler, searchHandler;
  const aborted = await require('../scripts/mobile_audit.cjs').guardPage({route: async (pattern, fn) => {
    if (pattern === '**/api/search*') {searchHandler = fn; return;}
    assert.equal(pattern, '**/*'); handler = fn;
  }}, 'https://wowpriced.com/');
  let fixture;
  await searchHandler({request: () => ({url: () => 'https://wowpriced.com/api/search?q=tea&page=2'}),
    fulfill: response => {fixture = JSON.parse(response.body);}});
  assert.deepEqual(fixture, {items: [], reason: 'robot'});
  for (const host of ['www.googletagmanager.com', 'www.google-analytics.com', 'region1.google-analytics.com',
    'www.googleadservices.com', 'stats.g.doubleclick.net', 'pagead2.googlesyndication.com',
    'connect.facebook.net', 'www.facebook.com/tr', 'analytics.tiktok.com', 's.pinimg.com/ct',
    'bat.bing.com', 'www.clarity.ms']) {
    for (const type of ['script', 'image', 'fetch']) {
      let action;
      await handler({request: () => ({url: () => 'https://' + host + '/tag',
        method: () => 'GET', resourceType: () => type}), abort: () => {action = 'abort';},
        continue: () => {action = 'continue';}});
      assert.equal(action, 'abort', host + ' ' + type);
      assert(aborted.has('https://' + host + '/tag'));
    }
  }
  for (const [url, method, type, expected] of [
    ['https://wowpriced.com/deals.json', 'GET', 'fetch', 'continue'],
    ['https://m.media-amazon.com/image.jpg', 'GET', 'image', 'continue'],
    ['https://unknown.test/tag.js', 'GET', 'script', 'abort'],
    ['https://wowpriced.com/collect', 'POST', 'fetch', 'abort']]) {
    let action;
    await handler({request: () => ({url: () => url, method: () => method, resourceType: () => type}),
      abort: () => {action = 'abort';}, continue: () => {action = 'continue';}});
    assert.equal(action, expected);
    assert.equal(aborted.has(url), expected === 'abort');
  }
  const errors = [], {recordConsoleError} = require('../scripts/mobile_audit.cjs');
  for (const url of aborted) recordConsoleError({type: () => 'error', text: () => 'net::ERR_FAILED',
    location: () => ({url})}, aborted, errors);
  assert.deepEqual(errors, []);
  for (const [type, text, url] of [['error', 'app failed', 'https://wowpriced.com/app.js'],
    ['error', 'image failed', 'https://m.media-amazon.com/image.jpg'],
    ['error', 'unknown origin', ''], ['warning', 'ignore', 'https://wowpriced.com/']]) {
    recordConsoleError({type: () => type, text: () => text, location: () => ({url})}, aborted, errors);
  }
  assert.deepEqual(errors, [{text: 'app failed', url: 'https://wowpriced.com/app.js'},
    {text: 'image failed', url: 'https://m.media-amazon.com/image.jpg'}, {text: 'unknown origin', url: ''}]);
});

function style(overrides = {}) {
  return {content: '""', position: 'absolute', pointerEvents: 'auto',
    left: '50%', right: 'auto', top: '50%', bottom: 'auto',
    width: '100%', height: '100%', minWidth: '44px', minHeight: '44px',
    maxWidth: 'none', maxHeight: 'none', transform: 'matrix(1, 0, 0, 1, -22, -22)', ...overrides};
}
const inside = (b, x, y) => x >= b.left && x < b.right && y >= b.top && y < b.bottom;
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < .02, `${actual} != ${expected}`);

test('centered PAUSE, Amazon and brand extensions measure 44px without changing the element box', () => {
  for (const [width, height] of [[60, 24], [87, 21], [160, 31], [136, 26]]) {
    const box = rect(100, 50, width, height);
    const before = {...box};
    const offset = width === 87 ? 2 : 0;
    const target = pseudoRect(box, style({top: `calc(50% + ${offset}px)`,
      transform: `matrix(1, 0, 0, 1, ${-width / 2}, -22)`}));
    assert.deepEqual(target, rect(100, 50 + (height - 44) / 2 + offset, width, 44));
    const effective = effectiveRect(box, [target], (x, y) => inside(target, x, y));
    assert.equal(effective.width, width);
    assert.equal(effective.height, 44);
    assert.deepEqual(box, before);
  }
  const amazon = rect(0, 10, 87, 21);
  const original = pseudoRect(amazon, style({transform: 'matrix(1, 0, 0, 1, -43.5, -22)'}));
  const shifted = pseudoRect(amazon, style({top: 'calc(50% + 2px)',
    transform: 'matrix(1, 0, 0, 1, -43.5, -22)'}));
  const owned = b => (x, y) => y >= 0 && inside(b, x, y); // header paints above the strip
  close(effectiveRect(amazon, [original], owned(original)).height, 42.5);
  assert.equal(effectiveRect(amazon, [shifted], owned(shifted)).height, 44);
});

test('title extension uses the heading outside the clamped link', () => {
  const heading = rect(0, 100, 150, 36);
  const target = pseudoRect(heading, style({transform: 'matrix(1, 0, 0, 1, -75, -22)'}));
  assert.deepEqual(target, rect(0, 96, 150, 44));
  assert.equal(effectiveRect(heading, [target], (x, y) => inside(target, x, y)).height, 44);
  // A pseudo-element still inside overflow:hidden must fail, even with
  // the exact same nominal 44px dimensions.
  close(effectiveRect(heading, [target], (x, y) => inside(heading, x, y)).height, 36);
});

test('source extension takes only separator space and protects a close neighbor', () => {
  const a = rect(20, 0, 35, 18), b = rect(80, 0, 57, 18);
  const creditStyle = style({left: 'auto', right: '0px', width: '44px', minWidth: '100%',
    maxWidth: 'calc(100% + 24px)', transform: 'matrix(1, 0, 0, 1, 0, -22)'});
  const aHit = pseudoRect(a, creditStyle), bHit = pseudoRect(b, creditStyle);
  assert.deepEqual(aHit, rect(11, -13, 44, 44));
  assert.deepEqual(bHit, rect(80, -13, 57, 44));
  assert.ok(aHit.right <= bHit.left);
  assert.equal(effectiveRect(a, [aHit], (x, y) => inside(aHit, x, y)).width, 44);
  // A tiny name shrinks instead of extending through the preceding link.
  const tiny = rect(80, 0, 7, 18);
  const tinyHit = pseudoRect(tiny, creditStyle);
  assert.equal(tinyHit.width, 31);
  assert.ok(tinyHit.left > a.right);
  assert.ok(effectiveRect(tiny, [tinyHit], (x, y) => inside(tinyHit, x, y)).width < 44);
  const meh = rect(80, 0, 22, 18);
  assert.equal(pseudoRect(meh, creditStyle).width, 44);
  // The first detail source has no preceding link, so a short name can
  // use the free space beside "Sources:" instead of the tight comma gap.
  assert.equal(pseudoRect(meh, {...creditStyle, maxWidth: 'none'}).width, 44);
  const single = rect(20, 0, 22, 18);
  const singleHit = pseudoRect(single, {...creditStyle, maxWidth: 'none'});
  const oldClip = rect(0, -14, 42, 46), paddedClip = rect(-14, -14, 70, 46);
  const clipped = clip => (x, y) => inside(singleHit, x, y) && inside(clip, x, y);
  close(effectiveRect(single, [singleHit], clipped(oldClip)).width, 42);
  assert.equal(effectiveRect(single, [singleHit], clipped(paddedClip)).width, 44);
  // Static links retain their inline font box and ellipsis semantics.
  const inline = rect(20, 2, 22, 13.25);
  const inlineHit = pseudoRect(inline, {...creditStyle, maxWidth: 'none'});
  const inlineClip = rect(-14, -14, 70, 45.25);
  const effective = effectiveRect(inline, [inlineHit], (x, y) => inside(inlineHit, x, y) && inside(inlineClip, x, y));
  assert.equal(effective.width, 44);
  assert.equal(effective.height, 44);
  const lastDetail = rect(80, 0, 32, 44);
  const lastHit = pseudoRect(lastDetail, {...creditStyle, left: '0px', right: 'auto',
    maxWidth: 'calc(100% + 16px)'});
  assert.deepEqual(lastHit, rect(80, 0, 44, 44));
  assert.ok(lastHit.right - lastDetail.right <= 16); // stay inside the page gutter
  const middleDetail = rect(41, 0, 32, 44);
  const middleHit = pseudoRect(middleDetail, {...creditStyle, left: '-8px', right: 'auto',
    maxWidth: 'calc(100% + 16px)'});
  assert.deepEqual(middleHit, rect(33, 0, 44, 44));
  assert.ok(middleHit.left > 32 && middleHit.right < 82); // neighboring source boxes
  // Wrapped credits' real boxes sit above all extensions. A 44px claim
  // must fail where the next line receives the tap.
  const wrapped = rect(20, 18, 57, 18);
  const actual = effectiveRect(a, [aHit], (x, y) => inside(aHit, x, y) && !inside(wrapped, x, y));
  assert.ok(actual.height < 44);
  close(actual.height, 31);
});

test('neighbor at a 44px box edge also prevents a passing measurement', () => {
  const box = rect(10, 10, 35, 18), hit = rect(1, -3, 44, 44);
  const neighbor = rect(1, -3, 5, 5);
  const actual = effectiveRect(box, [hit], (x, y) => inside(hit, x, y) && !inside(neighbor, x, y));
  assert.ok(actual.width < 44 || actual.height < 44);
});

test('code target includes the dashed border but stays out of the next button', () => {
  for (const width of [44, 78]) {
    const box = rect(0, 0, width, 36);
    const paddingBox = rect(1.5, 1.5, width - 3, 33);
    const hit = pseudoRect(paddingBox, style({width: 'calc(100% + 3px)', height: 'calc(100% + 3px)',
      minWidth: '0px', transform: `matrix(1, 0, 0, 1, ${-width / 2}, -22)`}));
    assert.deepEqual(hit, rect(0, -4, width, 44));
    const next = rect(width + 4, -4, 44, 44);
    assert.ok(hit.right < next.left);
    assert.ok(!inside(hit, next.left, next.top + next.height / 2));
    const actual = effectiveRect(box, [hit], (x, y) => inside(hit, x, y));
    assert.equal(actual.width, width);
    assert.equal(actual.height, 44);
  }
});

test('decorations, disabled pseudo hit testing and unsupported transforms cannot inflate a target', () => {
  const box = rect(0, 0, 35, 18);
  for (const overrides of [{content: 'none'}, {content: '"$0 today"'}, {pointerEvents: 'none'},
    {position: 'static'}, {display: 'none'}, {visibility: 'hidden'},
    {transform: 'matrix(0, 1, -1, 0, 0, 0)'}, {transform: 'rotate(8deg)'},
    {left: 'auto', right: 'auto'}]) {
    assert.equal(pseudoRect(box, style(overrides)), null);
  }
  const actual = effectiveRect(box, [null], (x, y) => inside(box, x, y));
  assert.equal(actual.width, 35);
  assert.equal(actual.height, 18);
});

test('clipping, occlusion and a hole between the center and edge remain failures', () => {
  const box = rect(0, 0, 60, 24), hit = rect(0, -10, 60, 44);
  close(effectiveRect(box, [hit], (x, y) => inside(box, x, y)).height, 24);
  assert.equal(effectiveRect(box, [hit], () => false).width, 0);
  const gap = rect(40, -10, 3, 44);
  assert.ok(effectiveRect(box, [hit], (x, y) => inside(hit, x, y) && !inside(gap, x, y)).width < 44);
  assert.deepEqual(union([box, hit]), hit);
});

test('auto dimensions from both insets and asymmetric translation are supported', () => {
  const box = rect(10, 20, 35, 18);
  const target = pseudoRect(box, style({left: '-5px', right: '-5px', top: '-13px', bottom: '-13px',
    width: 'auto', height: 'auto', transform: 'none'}));
  assert.deepEqual(target, rect(5, 7, 45, 44));
});
