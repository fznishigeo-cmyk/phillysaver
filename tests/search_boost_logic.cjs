// Operator 2026-10-17 (search_boost): the big button leads, adult products never
// paint, a rate-limited final word retries twice at most.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const code = html.slice(html.indexOf('  function searchAmazonQuery()'), html.indexOf('  function renderTabs('));
const recovery = html.slice(html.indexOf('  function searchRecoveryHtml()'), html.indexOf('  function recoverSearch('));
const turn = () => new Promise(resolve => setImmediate(resolve));
const timers = new Map(), calls = [], input = {}, elements = {
  q:{value:'', addEventListener:(type, fn) => input[type] = fn}, clearsearch:{}, searchcount:{},
  'search-amazon-result':{}, 'amazon-results':{innerHTML:''}
};
let id = 0;
const ctx = {Map, URLSearchParams, AbortController, Date, Number, RegExp, location:{search:'?preview=search'},
  document:{getElementById:key => elements[key], addEventListener(){}}, window:{},
  setTimeout:fn => {timers.set(++id, fn); return id;}, clearTimeout:key => timers.delete(key),
  searchText:() => elements.q.value.trim(), searchRecoveryOptions:() => [], plural:(n,s) => n + ' ' + s,
  esc:s => String(s).replace(/[&<>"']/g,c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  fetch:(url, options) => new Promise(resolve => calls.push({url, options, resolve}))};
vm.createContext(ctx); vm.runInContext(code + recovery, ctx);
const flush = async () => {const queue = [...timers.values()]; timers.clear(); queue.forEach(fn => fn()); await turn();};
const query = q => {elements.q.value = q; input.input(); ctx.renderSearchCount([]);};
const answer = async (call, body) => {call.resolve({ok:true, json:async () => body}); await turn(); await turn();};
const item = (asin, title) => ({asin, title, image:'https://m.media-amazon.com/images/I/photo.jpg', price_cents:1599,
  as_of:new Date(Date.now() - 60000).toISOString()});

(async () => {
  query('hijab');
  const markup = ctx.searchRecoveryHtml();
  assert(markup.indexOf('data-search-amazon') < markup.indexOf('id="amazon-results"'), 'button leads');
  assert(markup.includes('<h2 id="search-recovery-title">Live Amazon prices for &quot;hijab&quot;</h2>'));
  assert(!markup.includes('Try a broader search'));
  assert(markup.includes('Compare all &quot;hijab&quot; prices on Amazon →</a>'));
  assert(markup.includes('search-amazon-top') && markup.includes('Amazon links may earn us a commission.'));
  await flush(); assert.equal(calls.length, 1); assert.equal(calls[0].url, '/api/search?q=hijab');
  await answer(calls[0], {items:[item('B000000001', 'LUBELIFE Water Based Lubricant 8 oz'),
    item('B000000002', 'Silicone Butt-Plug set'), item('B000000003', 'Analog kitchen timer'), item('B000000004', 'Chiffon hijab scarf')]});
  const cards = elements['amazon-results'].innerHTML;
  assert(cards.includes('Analog kitchen timer') && cards.includes('Chiffon hijab scarf'));
  assert(!cards.includes('LUBELIFE') && !cards.includes('Butt-Plug'));
  for (const adult of ['anal beads', 'G-Spot toy', 'sex toys']) {
    query(adult); await flush(); assert.equal(calls.length, 1, adult);
    assert.equal(elements['amazon-results'].innerHTML, '');
    assert(ctx.searchRecoveryHtml().includes('data-search-amazon'), 'the button stays for ' + adult);
  }
  query('garlic press'); await flush(); assert.equal(calls.length, 2);
  await answer(calls[1], {items:[], reason:'rate_limited'}); await flush();
  assert.equal(calls.length, 3); assert.equal(calls[2].url, '/api/search?q=garlic%20press');
  await answer(calls[2], {items:[item('B000000005', 'Garlic press')]});
  assert(elements['amazon-results'].innerHTML.includes('Garlic press'));
  query('pepper mill'); await flush();
  for (const n of [3, 4, 5]) { await answer(calls[n], {items:[], reason:'rate_limited'}); await flush(); }
  assert.equal(calls.length, 6, 'two retries at most');
  query('pepper mill'); ctx.renderSearchCount([]); await flush(); assert.equal(calls.length, 6, 'then remembered');
  console.log('Search boost logic passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
