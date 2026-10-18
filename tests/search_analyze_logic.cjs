// Execute the emitted code, with held promises and a deterministic clock.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const code = html.slice(html.indexOf('  function searchText()'), html.indexOf('  function renderTabs('));
const inputCode = html.slice(html.indexOf('  var searchTimer, appliedSearch'), html.indexOf('  document.getElementById("storesel").addEventListener'));
const inventoryCode = html.slice(html.indexOf('  function searchInventory()'), html.indexOf('  function searchRecoveryOptions()'));
const turn = () => new Promise(resolve => setImmediate(resolve));
const events = [], calls = [], opened = [], timers = new Map(), listeners = {};
let now = Date.parse('2026-10-18T00:00:00Z'), serial = 0, resets = 0;
const element = () => ({value:'', innerHTML:'', hidden:false, textContent:'', handlers:{},
  addEventListener(name, fn) { (this.handlers[name] ||= []).push(fn); }, focus() {}});
const elements = Object.fromEntries(['q','content','analyze-search','search-hint','clearsearch','searchcount',
  'search-amazon-result','amazon-results'].map(key => [key, element()]));
const escape = value => String(value).replace(/[&<>"']/g,c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ctx = {URL, URLSearchParams, Map, Set, WeakMap, AbortController, Number, RegExp, Promise,
  Date:class extends Date {static now() {return now;}}, location:{search:'?preview=search'},
  document:{getElementById:key => elements[key], addEventListener:(name, fn) => (listeners[name] ||= []).push(fn)},
  window:{wpTrack:(...args) => events.push(args), open:(...args) => opened.push(args)},
  localStorage:{getItem:() => null, setItem(){}},
  setTimeout:(fn, ms = 0) => {timers.set(++serial, {fn, at:now + ms}); return serial;}, clearTimeout:id => timers.delete(id),
  track:(...args) => events.push(args), esc:escape, CATGROUPS:[], loadedOnce:true,
  state:{deals:[], spotlight:[], catTab:0}, savedDeals:{view:false}, renderSavedControls(){}, renderTabs(){},
  isLocked:d => !!d.locked, dealKey:d => d.key, plural:(n,s) => n + ' ' + s,
  cardHtml:d => '<article class="card" data-key="' + d.key + '"><a href="' + escape(d.url) + '">' + escape(d.item) + '</a></article>',
  filtered:rows => rows.filter(d => ctx.matchesSearch(d, ctx.searchWords(ctx.searchText()))),
  resetWall:() => {resets++; ctx.analyzeRender();}, rememberBrowse(){}, actionSignature:() => '',
  fetch:(url, options) => new Promise((resolve, reject) => calls.push({url, options, resolve, reject}))};
vm.createContext(ctx); vm.runInContext(code + inventoryCode + inputCode, ctx);
const advance = async ms => {
  now += ms;
  const due = [...timers].filter(([,v]) => v.at <= now);
  for (const [id, value] of due) {timers.delete(id); value.fn();}
  await turn(); await turn();
};
const emit = (name, type, event = {}) => {for (const fn of elements[name].handlers[type] || []) fn.call(elements[name], event);};
const submit = async value => {elements.q.value = value; emit('q', 'input'); emit('analyze-search', 'click'); await turn();};
const item = (asin, title = 'Steel kitchen kettle') => ({asin, title, image:'https://m.media-amazon.com/images/I/photo.jpg',
  price_cents:1599, as_of:new Date(now - 60000).toISOString()});
const deal = (key, asin, item = 'Steel kitchen kettle', extra = {}) => ({key, item, status:'alive',
  url:'https://www.amazon.com/dp/' + asin + '?tag=example-20', ...extra});
const finish = async (call, data) => {call.resolve({ok:true, json:async () => data}); await turn(); await turn();};

(async () => {
  const asin = 'B012345678';
  for (const host of ['amazon.com', 'www.amazon.com', 'smile.amazon.com', 'm.amazon.com']) {
    for (const path of ['/dp/', '/gp/product/', '/gp/aw/d/', '/exec/obidos/', '/exec/obidos/ASIN/', '/Long-product-name/dp/']) {
      for (const prefix of ['https://', 'http://', '', '//']) {
        assert.equal(ctx.analyzeInput(prefix + host + path + asin + '/ref=something?tag=PRIVATE&email=PRIVATE#PRIVATE').asin, asin);
      }
    }
    assert.equal(ctx.analyzeInput('https://' + host + '/anything?asin=b012345678').asin, asin);
  }
  for (const [url, kind] of [
    ['https://amzn.to/PRIVATE', 'short'], ['a.co/PRIVATE', 'short'], ['https://www.a.co/x', 'short'],
    ['https://walmart.com/ip/12345', 'other'], ['https://amazon.com.evil.test/dp/' + asin, 'other'],
    ['https://evilamazon.com/dp/' + asin, 'other'], ['https://amazon.co.uk/dp/' + asin, 'other'],
    ['https://www.amazon.com@evil.test/dp/' + asin, 'invalid'], ['https://amazon.com:8080/dp/' + asin, 'invalid'],
    ['https://amazon.com/dp/B01234567', 'invalid'], ['https://amazon.com/dp/B0123456789', 'invalid'],
    ['https://amazon.com/dp/B01234567!', 'invalid'], ['https://amazon.com/?asin=bad', 'invalid'],
    ['https://amazon.com/?asin=' + asin + '&asin=' + asin, 'invalid'],
    ['https://amazon.com/dp/' + asin + '?asin=B000000001', 'invalid'], ['https://amazon.com/', 'invalid'],
    ['javascript:alert(1)', 'invalid'], ['https://amazon.com\\@evil.test/dp/' + asin, 'invalid'],
    ['See https://amazon.com/dp/' + asin, 'invalid'], ['Copy amazon.com/dp/' + asin, 'invalid'],
    ['garlic press', 'query'], ['', 'query']
  ]) assert.equal(ctx.analyzeInput(url).kind, kind, url);
  for (const reason of ['PRIVATE', 'constructor', '__proto__', 'toString']) {
    assert.equal(ctx.analyzeReason(reason), 'Live price checks are unavailable right now.');
  }

  const live = deal('ours', asin), related = deal('related', 'B000000002', 'Travel steel kettle');
  ctx.state.deals = [live, related, deal('dead', 'B000000003', 'Steel kettle', {status:'dead'}),
    deal('locked', 'B000000004', 'Steel kettle', {locked:true})];
  // The inventory promise is a real prerequisite, including the exact displayed N.
  ctx.loadedOnce = false;
  await submit('https://amazon.com/dp/' + asin + '?tag=PRIVATE&email=PRIVATE');
  assert.equal(ctx.searchText(), '');
  assert.equal(calls.length, 0);
  assert.equal(ctx.analyzeTask.steps[0], 'done');
  assert.equal(ctx.analyzeTask.steps[1], 'pending');
  assert(!elements.content.innerHTML.includes('Comparing with 0'));
  ctx.loadedOnce = true; await advance(50);
  assert.equal(calls.length, 0, 'our deal comes first without a live lookup');
  assert.equal(ctx.analyzeTask.total, 2, 'dead and locked rows never inflate the live count');
  assert(elements.content.innerHTML.includes('Using our verified deal'));
  await advance(849); assert.equal(ctx.analyzeTask.finished, false);
  await advance(1); assert.equal(ctx.analyzeTask.finished, true);
  assert(elements.content.innerHTML.indexOf('data-key="ours"') < elements.content.innerHTML.indexOf('data-key="related"'));
  assert(elements.content.innerHTML.includes(escape(live.url)), 'our card keeps our link');
  assert.equal(ctx.analyzeDealAsin({url:'https://source.test/how-to', dedupe_key:'url|https://amazon.com/dp/' + asin}), asin);
  live.status = 'dead'; ctx.analyzeRender();
  assert(!elements.content.innerHTML.includes('data-key="ours"'), 'a later publication cannot revive a gone deal');
  live.status = 'alive';

  const originalCompare = ctx.analyzeCompare;
  let compared;
  ctx.analyzeCompare = (...args) => new Promise(resolve => {compared = () => originalCompare(...args).then(resolve);});
  await submit('https://smile.amazon.com/gp/aw/d/B000000009?private=PRIVATE');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/search?asin=B000000009');
  assert.equal(calls[0].options.credentials, 'omit');
  assert.equal(calls[0].options.referrerPolicy, 'no-referrer');
  assert.deepEqual(Array.from(ctx.analyzeTask.steps), ['done', 'working', 'pending']);
  let bodyReady;
  calls[0].resolve({ok:true, json:() => new Promise(resolve => {bodyReady = resolve;})});
  await turn(); assert.equal(ctx.analyzeTask.steps[1], 'working', 'headers are not a price result');
  ctx.state.deals.push(deal('arrival', 'B000000008', 'Wool scarf'));
  bodyReady({items:[item('B000000009')]}); await turn(); await turn();
  assert.deepEqual(Array.from(ctx.analyzeTask.steps), ['done','done','working']);
  assert(elements.content.innerHTML.includes('Comparing with 3 verified deals'), 'comparison uses the latest real live count');
  assert.equal(ctx.analyzeTask.finished, false);
  await compared(); await turn();
  assert.equal(ctx.analyzeTask.steps[2], 'done');
  await advance(899); assert.equal(ctx.analyzeTask.finished, false);
  await advance(1); assert.equal(ctx.analyzeTask.finished, true);
  ctx.analyzeCompare = originalCompare;
  assert(elements.content.innerHTML.includes('Live Amazon price'));
  assert(elements['amazon-results'].innerHTML.includes('$15.99'));
  assert(elements['amazon-results'].innerHTML.includes('Amazon price as of '));
  assert(elements['amazon-results'].innerHTML.includes('/dp/B000000009?tag=example-20'));
  assert(elements['amazon-results'].innerHTML.includes('rel="sponsored nofollow noopener"'));
  for (const word of ['rating', 'review', 'sales', 'history']) assert(!elements['amazon-results'].innerHTML.includes(word));
  assert(!JSON.stringify([...events, ...calls.map(c => [c.url, c.options]), ctx.analyzeTask]).includes('PRIVATE'));
  emit('analyze-search', 'click'); await turn(); assert.equal(calls.length, 1, 'same result is idempotent');

  // Enter submits, and switching away aborts; late results cannot overwrite the new query.
  elements.q.value = 'https://amazon.com/dp/B000000010'; emit('q', 'input');
  let prevented = false;
  emit('q', 'keydown', {key:'Enter', preventDefault() {prevented = true;}}); await turn();
  assert(prevented); assert.equal(calls.length, 2);
  await submit('https://amazon.com/dp/B000000011');
  assert(calls[1].options.signal.aborted);
  await finish(calls[1], {items:[item('B000000010', 'STALE')]});
  assert.equal(ctx.analyzeTask.asin, 'B000000011');
  await finish(calls[2], {items:[], reason:'budget_exhausted', detail:'PRIVATE'}); await advance(900);
  assert(elements.content.innerHTML.includes(escape("Today's live price checks have reached their limit.")));
  assert(elements.content.innerHTML.includes('Search Amazon</a>'));
  assert(elements.content.innerHTML.includes('href="https://www.amazon.com/?tag=example-20"'));
  const target = {closest:selector => selector === '[data-analyze-fallback]'};
  for (const fn of listeners.click) fn({type:'click', button:0, target, preventDefault(){}});
  assert.equal(opened.at(-1)[0], 'https://www.amazon.com/s?k=B000000011&tag=example-20');
  assert(!JSON.stringify(events).includes('B000000011'));

  for (const [url, message] of [['https://a.co/PRIVATE', 'Paste the full Amazon link (open it, then copy from the address bar)'],
    ['https://target.com/PRIVATE', 'We check Amazon links for now'], ['https://amazon.com/', 'Paste a full Amazon product link']]) {
    await submit(url); await advance(900);
    assert(elements.content.innerHTML.includes(message)); assert.equal(calls.length, 3);
  }
  elements.q.value = 'steel kettle'; emit('q', 'input'); await advance(150);
  assert.equal(ctx.analyzeTask, null); assert(resets > 0, 'normal local search still works after another store link');

  // Same words and phrases as search_boost, even when buried in a pasted product's title.
  await submit('https://amazon.com/dp/B000000012');
  await finish(calls[3], {items:[item('B000000012', 'Water Based Lubricant')]}); await advance(900);
  assert(ctx.analyzeTask.reason); assert(!elements.content.innerHTML.includes('Lubricant'));
  assert(elements.content.innerHTML.includes('Search Amazon</a>'));
  ctx.state.deals.push(deal('adult', 'B000000013', 'Silicone Butt-Plug'));
  await submit('https://amazon.com/dp/B000000013'); await advance(900);
  assert.equal(calls.length, 4); assert(!elements.content.innerHTML.includes('Butt-Plug'));
  assert.equal(ctx.analyzeTask.reason, 'excluded');

  // The Analyze button also checks typed products; its request uses the existing query guard.
  await submit('travel mug'); assert.equal(calls.at(-1).url, '/api/search?q=travel%20mug');
  await finish(calls.at(-1), {items:[item('B000000014', 'Travel mug')]}); await advance(900);
  assert.equal(ctx.analyzeTask.finished, true);
  await submit('https://amazon.com/dp/B000000015'); calls.at(-1).reject(new Error('PRIVATE url'));
  await turn(); await advance(900);
  assert(elements.content.innerHTML.includes('Live price checks are unavailable right now.'));
  assert(elements.content.innerHTML.includes('Search Amazon</a>'));
  assert(!elements.content.innerHTML.includes('PRIVATE'));

  // Both existing retry attempts remain bounded for a busy phone.
  await submit('https://amazon.com/dp/B000000016');
  const start = calls.length;
  for (let i = 0; i < 3; i++) {
    await finish(calls.at(-1), {items:[], reason:'rate_limited'});
    await advance(1500);
  }
  assert.equal(calls.length, start + 2);
  assert.equal(ctx.analyzeTask.reason, 'rate_limited');
  assert(elements.content.innerHTML.includes('Search Amazon</a>'));

  elements.q.value = 'insulated flask'; emit('q', 'input');
  ctx.queueAmazonResults([]); await advance(600);
  const pending = calls.at(-1), before = calls.length;
  assert.equal(pending.url, '/api/search?q=insulated%20flask');
  emit('analyze-search', 'click'); await turn();
  assert(!pending.options.signal.aborted, 'Analyze adopts the already paid text request');
  assert.equal(calls.length, before);
  await finish(pending, {items:[item('B000000017', 'Insulated flask')]}); await advance(900);
  assert.equal(ctx.analyzeTask.items[0].asin, 'B000000017');
  assert.equal(calls.length, before, 'no extra API call for Enter/Analyze during automatic search');
  await advance(86400001); ctx.analyzeRender();
  assert.equal(ctx.analyzeTask.reason, 'no_price', 'a cached price never outlives its real reading');
  assert(events.some(e => e[0] === 'search_analyze') && events.some(e => e[0] === 'paste_amazon_link'));
  assert(events.some(e => e[0] === 'analyze_result' && e[1].live === true));
  console.log('Analyze search logic passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
