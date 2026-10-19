const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const handlers = {}, opened = [], tracked = [];
const input = {value:''};
const ctx = {document:{getElementById:id => {assert.equal(id, 'q'); return input;},
  addEventListener:(name, fn) => handlers[name] = fn},
  window:{open:(...args) => opened.push(args), wpTrack:(...args) => tracked.push(args)},
  searchText:() => input.value.trim(), fetch:() => {throw Error('click must spend zero API tokens');}};
vm.createContext(ctx);
function code(start, end) {
  const at = html.indexOf(start); assert(at >= 0, start);
  return html.slice(at, html.indexOf(end, at));
}
const trackAt = html.indexOf('  function track(name, params)');
vm.runInContext(html.slice(trackAt, html.indexOf('\n  }', trackAt) + 4), ctx);
const queryAt = html.indexOf('  function searchAmazonQuery()');
vm.runInContext(html.slice(queryAt, html.indexOf('\n  }', queryAt) + 4), ctx);
vm.runInContext(code('  function searchAmazonClick(', '  document.addEventListener("click", searchAmazonClick);') +
  'document.addEventListener("click", searchAmazonClick);document.addEventListener("auxclick", searchAmazonClick);', ctx);
const href = 'https://www.amazon.com/';
const target = {href, closest:selector => selector === '.all-discounts' ? target : null};
function click(value, query, type='click', button=0) {
  input.value = value;
  let prevented = false;
  const before = opened.length;
  handlers[type]({type, button, target, preventDefault(){prevented = true;}});
  assert(prevented); assert.equal(opened.length, before + 1);
  const expected = query ? 'https://www.amazon.com/s?k=' + encodeURIComponent(query) + '&rh=p_n_deal_type%3A23566065011&tag=example-20'
    : 'https://www.amazon.com/deals?tag=example-20';
  assert.deepEqual(opened.at(-1), [expected, '_blank', 'noopener']);
  assert.deepEqual(tracked.at(-1), ['search_discounts', undefined]);
  assert.equal(target.href, href, 'automatic outbound analytics sees only a plain domain');
}
for (const [raw, query] of [['kettle', 'kettle'], [' café  & "tea" ', 'café & "tea"'], ['抹茶 茶碗', '抹茶 茶碗'],
  ['🫖'.repeat(85), '🫖'.repeat(80)], ['a'.repeat(90), 'a'.repeat(80)], ['model 12345', 'model 12345'],
  ['adulting book', 'adulting book'], ['adult coloring book', 'adult coloring book']]) {
  click(raw, query); click(raw, query, 'auxclick', 1);
}
for (const raw of ['', '  ', 'name@example.test', 'order 123456', 'order １２３４５６', 'order ١٢٣٤٥٦',
  'https://amazon.com/dp/B000000001', '//example.com/private', 'www.example.com', 'example.com/private',
  '例子。测试/private', '127.0.0.1/private', 'ftp://example.test', '\ud800', 'adult toys', 'VIBRATOR',
  'sex lube', 'butt-plug', 'butt plug', 'kettle ' + 'x'.repeat(90) + ' vibrator']) {
  click(raw, ''); click(raw, '', 'auxclick', 1);
}
ctx.wpAndroidLimited = () => true;
click('kettle', ''); click('kettle', '', 'auxclick', 1);
ctx.wpAndroidLimited = () => false;
const count = opened.length;
for (const [type, button] of [['click', 1], ['click', 2], ['auxclick', 0], ['auxclick', 2]]) {
  handlers[type]({type, button, target, preventDefault(){throw Error('unexpected interception');}});
}
handlers.click({type:'click', button:0, target:{closest:() => null}});
assert.equal(opened.length, count);
assert.equal(tracked.length, count);
assert(!JSON.stringify(tracked).includes('kettle'));
// Preserve the old Amazon exit when a page has one; the discounts filter is opt-in per anchor.
input.value = 'kettle';
handlers.click({type:'click', button:0, target:{closest:selector => selector === '[data-search-amazon]'}, preventDefault(){}});
assert.deepEqual(opened.at(-1), ['https://www.amazon.com/s?k=kettle&tag=example-20', '_blank', 'noopener']);
assert.deepEqual(tracked.at(-1), ['search_amazon', undefined]);
// The phone eligibility class is added only by the emitted input listener.
assert.equal((html.match(/class="searchfield discounts-typed"/g) || []).length, 0);
assert(html.includes('this.parentNode.classList.add("discounts-typed")'));
for (const script of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) {
  if (!script[0].includes('application/ld+json')) new vm.Script(script[1]);
}
console.log('Discount exits passed: encoding, private/adult fallback, Android, left/middle click, tracking, no API');
