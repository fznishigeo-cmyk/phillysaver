const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const code = html.slice(html.indexOf('  function searchAmazonQuery()'), html.indexOf('  function renderTabs('));
const recovery = html.slice(html.indexOf('  function searchRecoveryHtml()'), html.indexOf('  function recoverSearch('));
const turn = () => new Promise(resolve => setImmediate(resolve));
function setup(search) {
  const timers = new Map(), calls = [], events = [], input = {}, elements = {
    q:{value:'', addEventListener:(type, fn) => input[type] = fn}, clearsearch:{}, searchcount:{},
    'search-amazon-result':{}, 'amazon-results':{innerHTML:''}
  };
  let id = 0;
  const ctx = {Map, URLSearchParams, AbortController, Date, Number, location:{search},
    document:{getElementById:key => elements[key], addEventListener(){}},
    window:{wpTrack:(...args) => events.push(args)},
    setTimeout:fn => {timers.set(++id, fn); return id;}, clearTimeout:key => timers.delete(key),
    searchText:() => elements.q.value.trim(), searchRecoveryOptions:() => [],
    plural:(n,s) => n + ' ' + s,
    esc:s => String(s).replace(/[&<>"']/g,c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
    fetch:(url, options) => new Promise(resolve => calls.push({url, options, resolve}))};
  vm.createContext(ctx); vm.runInContext(code + recovery, ctx);
  return {ctx, calls, events, elements,
    async flush() {const queue = [...timers.values()]; timers.clear(); queue.forEach(fn => fn()); await turn();},
    query(q, rows = []) {elements.q.value = q; input.input(); ctx.renderSearchCount(rows);}};
}
const item = {asin:'B012345678', title:'Cupping <script>bad</script>', image:'https://m.media-amazon.com/images/I/photo.jpg',
  price_cents:1599, as_of:new Date(Date.now() - 60000).toISOString()};
async function respond(call, items) {call.resolve({ok:true, json:async () => ({items})}); await turn(); await turn();}

(async () => {
  if (html.includes('"mode": "preview"')) {
    for (const flag of ['', '?preview=other', '?search=preview']) {
      const s = setup(flag); s.query('cupping'); await s.flush(); assert.equal(s.calls.length, 0);
      assert.equal(s.elements['amazon-results'].innerHTML, '');
    }
  }
  const s = setup('?preview=search');
  // Browse restoration replaces the address before the first search render.
  s.ctx.location.search = '';
  assert.equal(s.ctx.amazonResultsEnabled(), true, 'preview survives browse URL replacement');
  for (const q of ['', 'x', 'tea/cup', 'person@example.test', 'order 123456', 'a'.repeat(61)]) {
    s.query(q); await s.flush(); assert.equal(s.calls.length, 0, q);
  }
  s.query('kettle', [{status:'alive'}]); await s.flush(); assert.equal(s.calls.length, 0);
  s.query('cu'); s.query('cupp'); s.query('  CUPPING  '); await s.flush();
  assert.equal(s.calls.length, 1); assert.equal(s.calls[0].url, '/api/search?q=cupping');
  for (const query of ['cupping ', '  CUPPING  ', 'cupping']) {
    s.query(query); await s.flush();
    assert.equal(s.calls.length, 1, 'same normalized input keeps the pending request');
    assert.equal(s.calls[0].options.signal.aborted, false);
  }
  await respond(s.calls[0], [item]);
  const markup = s.elements['amazon-results'].innerHTML;
  for (const text of ['From Amazon right now', '$15.99', 'Amazon price as of ', 'width="160"',
    'https://www.amazon.com/dp/B012345678?tag=example-20', 'rel="sponsored nofollow noopener"', 'target="_blank"',
    'Cupping &lt;script&gt;bad&lt;/script&gt;']) assert(markup.includes(text), text);
  assert(!markup.includes('<script>bad'));
  s.query('  CUPPING  '); await s.flush();
  assert.equal(s.elements['amazon-results'].innerHTML, markup);
  assert.equal(s.calls.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(s.events)), [['search_amazon_results', {count:1}]]);
  const recoveryMarkup = s.ctx.searchRecoveryHtml();
  assert(recoveryMarkup.indexOf('id="amazon-results"') < recoveryMarkup.indexOf('data-search-amazon'));
  s.query('other'); await s.flush(); const pending = s.calls[1];
  s.query('kettle', [{status:'alive'}]); assert(pending.options.signal.aborted);
  await respond(pending, [item]); assert.equal(s.elements['amazon-results'].innerHTML, '');
  s.query('cupping'); await s.flush(); assert.equal(s.calls.length, 2);
  assert.equal(s.elements['amazon-results'].innerHTML, markup);
  s.query('hijabs'); await s.flush(); await respond(s.calls[2], []);
  assert.equal(s.elements['amazon-results'].innerHTML, '');
  assert(s.ctx.searchRecoveryHtml().includes('Search Amazon for'));
  s.ctx.renderSearchCount([]); await s.flush(); assert.equal(s.calls.length, 3, 'rerenders cannot refetch');
  assert(!JSON.stringify(s.events).includes('cupping'));
  console.log('Amazon result page logic passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
