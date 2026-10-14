const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('dealradar/publish/templates/inventory.js', 'utf8');

function setup(mode) {
  const events = [], handlers = {};
  const window = mode === 'missing' ? {} : {wpTrack(name, params) {
    if (mode === 'throws') throw Error('unavailable');
    events.push([name, params.method]);
  }};
  vm.runInNewContext(source, {window, document: {addEventListener(name, fn) { handlers[name] = fn; }}});
  return {events, click(type, method, options = {}) {
    const link = {getAttribute: () => method};
    const target = {closest: () => link};
    const event = {type, target: options.textNode ? {parentElement: target} : target, ...options,
      preventDefault() { assert.fail('must preserve browser navigation'); },
      stopPropagation() { assert.fail('must preserve browser navigation'); }};
    handlers[type](event);
  }};
}
test('real pagination links preserve keyboard, modified and middle navigation', () => {
  const x = setup();
  x.click('click', 'category');
  x.click('click', 'store', {button: 0, ctrlKey: true});
  x.click('auxclick', 'price', {button: 1});
  x.click('click', 'product_family', {button: 0, textNode: true});
  assert.deepEqual(x.events, ['category', 'store', 'price', 'product_family'].map(m => ['hub_page_click', m]));
});
test('member links count actions; right clicks and unbounded values count nothing', () => {
  const x = setup();
  x.click('click', null, {button: 0});
  x.click('auxclick', 'store', {button: 2});
  x.click('click', 'store', {button: 2});
  x.click('click', 'PRIVATE');
  assert.deepEqual(x.events, [['product_family_click', 'deal']]);
});
test('missing or throwing analytics leaves native links usable', () => {
  for (const mode of ['missing', 'throws']) {
    const x = setup(mode);
    x.click('click', 'store'); x.click('click', null);
    assert.deepEqual(x.events, []);
  }
});
test('shared deal actions classify generated inventory pages and keep outbound navigation', () => {
  const actions = fs.readFileSync(process.argv[2], 'utf8');
  for (const [path, method] of [['/deals/electronics/page/2/', 'category'],
    ['/stores/walmart/page/10/', 'store'], ['/deals/under-15/page/3/', 'price'],
    ['/products/model-123/', 'product_family'], ['/products/model-123/page/2/', 'product_family']]) {
    const handlers = {}, events = [];
    const scope = {querySelector: () => null, querySelectorAll: () => [], contains: () => true,
      addEventListener(name, fn) { handlers[name] = fn; }};
    vm.runInNewContext(actions, {location: {pathname: path}, navigator: {},
      window: {wpTrack(name, params) { events.push([name, params.method]); }},
      document: {getElementById: () => null, querySelector: () => scope}});
    const outbound = {getAttribute: () => 'store', hasAttribute: () => true};
    handlers.click({type: 'click', button: 0, ctrlKey: true,
      target: {closest: selector => selector.includes('sponsored') ? outbound : null},
      preventDefault() { assert.fail('native navigation'); }});
    handlers.toggle({target: {open: true, hasAttribute: () => true}});
    assert.deepEqual(events, [['deal_click', method], ['requirements_deal_click', method], ['requirements_open', method]]);
  }
});
