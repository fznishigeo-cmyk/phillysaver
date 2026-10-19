// Execute the actual composed functions: only the top button gets bestseller sort.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8'), footer = process.argv[3] === 'true';
const small = '<small>Amazon links may earn us a commission.</small>';
const source = name => {
  const match = new RegExp('  function ' + name + '\\([^\\n]*\\) \\{\\n[\\s\\S]*?\\n  \\}').exec(html);
  assert(match, name); return match[0];
};
let input = '', opened = [], tracked = [];
const ctx = {searchText:() => input.trim(), document:{getElementById:() => ({value:input})},
  esc:s => s.replaceAll('&', '&amp;').replaceAll('"', '&quot;'),
  amazonResultsSettings:{tag:'example-20'}, track:(...args) => tracked.push(args),
  window:{open:(...args) => opened.push(args)}};
vm.createContext(ctx);
for (const name of ['searchAmazonQuery', 'searchAmazonHtml', 'searchAmazonClick', 'searchMoreTopHtml'])
  vm.runInContext(source(name), ctx);
function target(top, discounts = false) {
  return {closest:selector => selector === '.all-discounts' ? (discounts ? {} : null)
    : selector === '.search-amazon-top' ? (top ? {} : null) : selector === '[data-search-amazon]' ? {} : null};
}
function click(raw, expected, top, type = 'click', button = 0, discounts = false) {
  input = raw; const before = opened.length;
  let prevented = false;
  ctx.searchAmazonClick({type, button, target:target(top, discounts), preventDefault(){prevented = true;}});
  if (expected === null) {assert.equal(opened.length, before); return;}
  assert(prevented); assert.equal(opened.length, before + 1);
  assert.deepEqual(opened.at(-1), [expected, '_blank', 'noopener']);
}
for (const raw of [' mouse  pad ', 'café & "tea"', '抹茶 茶碗', '🫖'.repeat(85)]) {
  const q = Array.from(raw.trim().replace(/\s+/g, ' ')).slice(0, 80).join('');
  const url = 'https://www.amazon.com/s?k=' + encodeURIComponent(q);
  for (const [type, button] of [['click', 0], ['auxclick', 1]]) {
    click(raw, url + '&s=exact-aware-popularity-rank&tag=example-20', true, type, button);
    click(raw, url + '&tag=example-20', false, type, button);
    if (html.includes('id="search-discounts"'))
      click(raw, url + '&rh=p_n_deal_type%3A23566065011&tag=example-20', false, type, button, true);
  }
  input = raw;
  assert.equal(ctx.searchMoreTopHtml().includes(small), !footer);
  assert.equal(ctx.searchAmazonHtml(true).includes(small), !footer);
  assert(ctx.searchAmazonHtml(false).includes(small), 'secondary inline link retains its small disclosure');
}
for (const raw of ['', 'name@example.com', 'order １２３４５６', 'https://amazon.com/private',
  'www.example.com', '127.0.0.1', '\ud800']) for (const top of [true, false]) click(raw, null, top);
ctx.wpAndroidLimited = () => true;
click('mouse', null, true); ctx.wpAndroidLimited = () => false;
for (const [type, button] of [['click', 1], ['click', 2], ['auxclick', 0], ['auxclick', 2]])
  click('mouse', null, true, type, button);
assert(!JSON.stringify(tracked).includes('mouse'), 'query stays out of analytics');

const modulePath = path.join(path.dirname(process.argv[2]), 'search-analyze.js');
const analyze = fs.existsSync(modulePath) ? fs.readFileSync(modulePath, 'utf8') : html;
const extract = name => {
  const match = new RegExp('  function ' + name + '\\([^\\n]*\\) \\{\\n[\\s\\S]*?\\n  \\}').exec(analyze);
  assert(match, name); return match[0];
};
ctx.analyzeTask = {asin:'B000000901'};
ctx.analyzeSearchText = () => 'mouse';
ctx.analyzeInput = () => ({kind:'query'});
ctx.host = ctx;
vm.runInContext(extract('analyzeFallbackClick') + '\n' + extract('analyzeFallback'), ctx);
assert.equal(ctx.analyzeFallback(ctx.analyzeTask).includes(small), !footer);
for (const top of [true, false]) for (const [type, button] of [['click', 0], ['auxclick', 1]]) {
  const el = {closest:selector => selector === '[data-analyze-fallback]' || (selector === '.search-amazon-top' && top) ? {} : null};
  ctx.analyzeFallbackClick({type, button, target:el, preventDefault(){}});
  assert.deepEqual(opened.at(-1), ['https://www.amazon.com/s?k=B000000901&' +
    (top ? 's=exact-aware-popularity-rank&' : '') + 'tag=example-20', '_blank', 'noopener']);
}
// Mount the real lazy module while search has focus and the hint is absent.
if (fs.existsSync(modulePath)) {
  const {setup, turn} = require('./search_more_logic.cjs');
  (async () => {
    const s = setup(); delete s.elements['search-hint'];
    s.ctx.document.activeElement = s.elements.q;
    s.emit('q', 'focus'); await turn(); await turn();
    assert.equal(s.analyzeModules.length, 1);
    assert.equal(s.ctx.analyzeFailed, false, 'missing hint must not break a focused module mount');
    assert.equal(typeof s.ctx.analyzeUI.input, 'function');
    console.log('Search polish logic passed: top/secondary/discounts, privacy, disclosures and focused lazy mount');
  })().catch(error => {console.error(error); process.exitCode = 1;});
} else console.log('Search polish inline logic passed');
