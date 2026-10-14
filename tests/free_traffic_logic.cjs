const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('dealradar/publish/templates/traffic.js', 'utf8');

function setup(options = {}) {
  const events = [], handlers = {}, writes = [], shares = [];
  const element = () => ({hidden: true, disabled: false, textContent: '', listeners: {},
    addEventListener(name, fn) { this.listeners[name] = fn; }});
  const native = element(), copy = element(), manual = element(), status = element();
  const input = {value: 'https://wowpriced.com/deal/a-12345678/?utm_source=share&utm_medium=referral&utm_campaign=deal',
    focus() { this.focused = true; }, select() { this.selected = true; }};
  const elements = {'[data-share-native]': native, '[data-share-copy]': copy,
    '[data-share-manual]': manual, '[data-share-status]': status, input};
  const box = {querySelector: key => elements[key], getAttribute: () => 'Item at Store'};
  const navigator = {};
  if (options.clipboard !== false) navigator.clipboard = {writeText(value) {
    writes.push(value);
    if (options.copyThrow) throw Error('denied');
    return options.copyFail ? Promise.reject(Error('denied')) : Promise.resolve();
  }};
  if (options.share) navigator.share = data => {
    shares.push(data);
    if (options.shareThrow) throw Error('blocked');
    return options.shareFail ? Promise.reject({name: options.shareFail}) : Promise.resolve();
  };
  const window = options.noAnalytics ? {} : {wpTrack(name, params) {
    if (options.analyticsThrows) throw Error('offline');
    events.push([name, params.method]);
  }};
  vm.runInNewContext(source, {window, navigator, URLSearchParams,
    location: {pathname: options.path || '/deal/a-12345678/', search: options.query || '?session_id=PRIVATE'},
    document: {querySelectorAll: () => options.noBox ? [] : [box],
      addEventListener: (name, fn) => { handlers[name] = fn; }}});
  return {events, handlers, native, copy, manual, status, input, writes, shares};
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('copy counts only successful writes, never inherits the visitor query', async () => {
  const x = setup();
  assert.equal(x.native.hidden, true);
  assert.equal(x.copy.hidden, false);
  x.copy.listeners.click();
  assert.equal(x.copy.disabled, true);
  await flush();
  assert.deepEqual(x.writes, [x.input.value]);
  assert.deepEqual(x.events, [['deal_share', 'copy']]);
  assert.equal(x.copy.disabled, false);
  assert.equal(x.status.textContent, 'Deal link copied.');
  assert.ok(!x.writes[0].includes('PRIVATE'));
});
test('denied or throwing clipboard exposes and focuses selectable link, without a share event', async () => {
  for (const options of [{copyFail: true}, {copyThrow: true}]) {
    const x = setup(options); x.copy.listeners.click(); await flush();
    assert.equal(x.manual.hidden, false);
    assert.equal(x.input.focused && x.input.selected, true);
    assert.equal(x.copy.disabled, false);
    assert.deepEqual(x.events, []);
  }
});
test('unsupported APIs keep manual sharing usable', () => {
  const x = setup({clipboard: false});
  assert.equal(x.native.hidden, true); assert.equal(x.copy.hidden, true);
  assert.equal(x.manual.hidden, false);
});
test('native success is a handoff; cancellation counts nothing and preserves focus', async () => {
  const x = setup({share: true}); x.native.listeners.click();
  assert.equal(x.shares.length, 1); // synchronously within the gesture
  await flush();
  assert.deepEqual(x.events, [['deal_share', 'native']]);
  for (const options of [{shareFail: 'AbortError'}, {shareFail: 'NotAllowedError'}, {shareThrow: true}]) {
    const y = setup({share: true, ...options}); y.native.listeners.click(); await flush();
    assert.deepEqual(y.events, []); assert.equal(y.native.disabled, false);
    assert.equal(!!y.input.focused, options.shareFail !== 'AbortError');
  }
});
test('missing or broken analytics does not prevent successful copying', async () => {
  for (const options of [{noAnalytics: true}, {analyticsThrows: true}]) {
    const x = setup(options); x.copy.listeners.click(); await flush();
    assert.equal(x.status.textContent, 'Deal link copied.');
    assert.equal(x.copy.disabled, false);
  }
});
test('arrivals require exact bounded campaigns on deal pages; empty/no-JS markup causes no errors', () => {
  for (const [source, medium] of [['share', 'referral'], ['feed', 'rss']]) {
    const query = `?utm_source=${source}&utm_medium=${medium}&utm_campaign=deal`;
    assert.deepEqual(setup({query, noBox: true}).events, [[source + '_arrival', 'deal']]);
    assert.deepEqual(setup({query, path: '/'}).events, []);
    assert.deepEqual(setup({query: query.replace('deal', 'PRIVATE')}).events, []);
    assert.deepEqual(setup({query: query.replace(medium, 'PRIVATE')}).events, []);
  }
});
test('feed and hub actions keep keyboard, modifier, and middle navigation; right clicks ignored', () => {
  const x = setup();
  const target = {closest: selector => selector === 'a[data-related-hub]' ?
    {getAttribute: () => 'store'} : null};
  x.handlers.click({target, type: 'click', button: 0, ctrlKey: true});
  x.handlers.auxclick({target, type: 'auxclick', button: 1});
  x.handlers.auxclick({target, type: 'auxclick', button: 2});
  x.handlers.click({type: 'click', target: {closest: selector => selector === 'a[data-feed-link]' ? {} : null}});
  assert.deepEqual(x.events, [['related_hub_click', 'store'], ['related_hub_click', 'store'], ['feed_open', 'footer']]);
});
