const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('dealradar/publish/templates/click-signal.js', 'utf8');
function harness(options = {}) {
  const calls = [], listeners = {};
  let privacy = options.optOut;
  const card = {classList: {contains: s => options[s]}, getAttribute: () => options.badKey ? 'private item' : '0123456789abcdef'};
  const link = {getAttribute: name => name === 'href' ? 'https://shop.test' : options.static ? 'fedcba9876543210' : null,
    closest: () => options.detail ? null : card};
  const document = {
    querySelector: () => options.off ? null : {getAttribute: () => 'https://test.workers.dev/c'},
    querySelectorAll: () => [...Array(options.position || 1)].map((_, i) => i === (options.position || 1) - 1 ? card : {}),
    getElementById: () => options.search ? {value: 'private search words'} : null,
    addEventListener: (name, fn, opts) => {assert.equal(opts.passive, true); listeners[name] = fn;}
  };
  const context = {document, location: {pathname: options.path || '/'},
    navigator: {globalPrivacyControl: options.gpc, sendBeacon: options.noBeacon ? undefined : (url, body) => {
      if (options.failure) throw Error('down');
      calls.push({url, body: JSON.parse(body)}); return !options.full;
    }}, localStorage: {getItem: key => {assert.equal(key, 'wp.adsOptOut'); if (options.blocked) throw Error('blocked'); return privacy ? '1' : null;}}};
  vm.runInNewContext(source, context);
  return {calls, setPrivacy: v => privacy = v, click: (type = 'click', overrides = {}) => {
    const e = {type, button: type === 'auxclick' ? 1 : 0, isTrusted: true,
      target: {closest: selector => {assert.equal(selector, 'a.getdeal'); return options.notDeal ? null : link;}},
      preventDefault: () => assert.fail('navigation was blocked'), stopPropagation: () => assert.fail('propagation changed'), ...overrides};
    if (listeners[type]) listeners[type](e);
    assert.equal(e.defaultPrevented, overrides.defaultPrevented);
  }};
}
test('only four coarse fields; no names, queries, URL, IDs, cookies or browser writes', () => {
  const h = harness({search: true, position: 31}); h.click();
  const data = h.calls[0].body;
  assert.deepEqual(Object.keys(data).sort(), ['hour', 'key', 'position', 'surface']);
  assert.equal(data.surface, 'search'); assert.equal(data.position, '31-60');
  assert.equal(data.hour, Math.floor(Date.now() / 3600000));
  assert(!JSON.stringify(h.calls).includes('private'));
});
test('off, GPC, opt-out and unavailable storage send nothing; privacy checked at click time', () => {
  for (const key of ['off', 'gpc', 'optOut', 'blocked', 'noBeacon', 'gone', 'locked', 'badKey', 'notDeal']) {
    const h = harness({[key]: true}); h.click(); assert.equal(h.calls.length, 0, key);
  }
  const h = harness(); h.click(); h.setPrivacy(true); h.click(); assert.equal(h.calls.length, 1);
});
test('normal, keyboard, modified and middle clicks navigate unchanged; right/synthetic/cancelled clicks ignored', () => {
  const h = harness(); h.click(); h.click('click', {detail: 0}); h.click('click', {ctrlKey: true}); h.click('auxclick');
  assert.equal(h.calls.length, 4);
  h.click('auxclick', {button: 2}); h.click('click', {isTrusted: false});
  h.click('click', {defaultPrevented: true}); assert.equal(h.calls.length, 4);
});
test('beacon failures or a full queue never trigger a retry or prevent navigation', () => {
  for (const opts of [{failure: true}, {full: true}]) { const h = harness(opts); h.click(); }
});
test('static details, related cards, hubs, wall and all position boundaries', () => {
  for (const [n, bucket] of [[1,'1-8'],[8,'1-8'],[9,'9-30'],[30,'9-30'],[60,'31-60'],[61,'61-120'],[120,'61-120'],[121,'121+']]) {
    const h = harness({position:n}); h.click(); assert.equal(h.calls[0].body.position, bucket);
  }
  for (const [path, surface] of [['/deal/item/', 'deal_page'], ['/deals/kitchen/', 'hub'], ['/', 'wall']]) {
    const h = harness({path, static:true, detail:surface === 'deal_page'}); h.click();
    assert.equal(h.calls[0].body.surface, surface); assert.equal(h.calls[0].body.key, 'fedcba9876543210');
    if (surface === 'deal_page') assert.equal(h.calls[0].body.position, 'detail');
  }
});
