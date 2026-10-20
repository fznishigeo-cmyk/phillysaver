const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const eventForm = html.includes('id="event-search"');
const more = html.includes('function searchMoreCall(');
const input = {value:''}, opened = [], tracked = [];
let submitted = 0, click;
const ctx = {URL, analyzeUI:null, analyzeBox:input, analyzeButton:{addEventListener:(name, fn) => {click = fn;}},
  document:{getElementById:id => {assert.equal(id, 'q'); return input;}},
  analyzeSubmit:() => {submitted++;}, searchText:() => eventForm ? input.value.trim() : ctx.analyzeSearchText(),
  track:(...args) => tracked.push(args), window:{open:(...args) => opened.push(args)},
  fetch:() => {throw Error('typed button must not request the network');}};
vm.createContext(ctx);
for (const name of ['analyzeInput', 'analyzeSearchText', 'searchAmazonQuery', 'discountsLink', 'searchAmazonClick']) {
  const start = html.indexOf('  function ' + name + '(');
  assert(start >= 0, name);
  vm.runInContext(html.slice(start, html.indexOf('\n  }', start) + 4), ctx);
}
const listener = html.match(/if \(analyzeButton\) analyzeButton.addEventListener\("click", function \(\) \{[\s\S]*?\n  \}\);/);
if (!eventForm) {assert(listener, 'actual emitted button listener'); vm.runInContext(listener[0], ctx);}
function press(raw, discounts=false, type='click', button=0) {
  input.value = raw;
  const target = {closest:selector => (discounts ? selector === '.all-discounts' : selector === '#analyze-search') ? target : null};
  let prevented = false;
  if (!discounts && type === 'click' && button === 0 && !eventForm) click();
  ctx.searchAmazonClick({type, button, target, preventDefault(){prevented = true;}});
  return prevented;
}
for (const [raw, words] of [['phone holder', 'phone holder'], [' café  & tea ', 'café & tea'], ['抹茶 茶碗', '抹茶 茶碗']]) {
  for (const [type, button] of [['click', 0], ['auxclick', 1]]) {
    assert(press(raw, false, type, button));
    assert.deepEqual(opened.at(-1), ['https://www.amazon.com/s?k=' + encodeURIComponent(words) +
      '&rh=p_n_deal_type%3A210906366011&s=exact-aware-popularity-rank&tag=fixture-coupons-20', '_blank', 'noopener']);
    assert.deepEqual(tracked.at(-1), ['search_discounts']);
  }
}
assert.equal(submitted, 0, 'typed button never enters product analysis');
const count = opened.length;
for (const raw of ['', '  ', 'https://www.amazon.com/dp/B012345678?tag=PRIVATE', 'B012345678',
  'https://amazon.com/Nulaxy-Phone-Holder/dp/B012345678']) assert.equal(press(raw), false);
assert.equal(submitted, eventForm ? 0 : 5, 'empty, Amazon links and bare ASIN retain Analyze flow');
assert.equal(opened.length, count);
press('phone holder', true);
assert.deepEqual(opened.at(-1), ['https://www.amazon.com/s?k=phone%20holder&rh=p_n_deal_type%3A23566065011&tag=fixture-coupons-20', '_blank', 'noopener']);
press('https://amazon.com/Nulaxy-Phone-Holder/dp/B012345678', true);
assert.match(opened.at(-1)[0], /k=Nulaxy%20Phone%20Holder&rh=p_n_deal_type%3A23566065011/);
assert(!JSON.stringify(tracked).includes('phone'));
for (const script of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g))
  if (!script[0].includes('application/ld+json')) new vm.Script(script[1]);
console.log('PASS: actual button listener + shared click handler: typed/link/ASIN/empty, URL, tag, encoding, tracking, zero network');
if (!eventForm) (async () => {
  const {setup, deal, turn} = require('./search_more_logic.cjs');
  for (const reason of [null, 'no_results', 'budget_exhausted', 'timeout']) {
    const recovery = setup();
    Object.assign(recovery.ctx, {freshView:() => false, freshUI:null, spaceSameProducts:rows => rows,
      searchRecoveryOptions:() => [{kind:'clear', value:'query', label:'Clear search'},
        {kind:'suggestion', value:'mouse', label:'Mouse'}]});
    recovery.ctx.state.deals = [deal(1, 'Wireless mouse')];
    await recovery.query('phone holder');
    assert(!/search-amazon-top|class="getdeal" data-search-amazon|Compare all|See all &quot;/.test(recovery.elements.content.innerHTML),
      'no recovery pill while Amazon is loading');
    await recovery.advance(750);
    assert.equal(recovery.calls.length, 1);
    await recovery.respond(recovery.calls[0], reason ? {items:[], reason} :
      {items:[recovery.item(901, 'Phone holder')]});
    await recovery.advance(1500);
    const markup = recovery.elements.content.innerHTML;
    assert(markup.includes('Search Amazon for &quot;phone holder&quot;</a>'), 'plain zero-match exit');
    assert(!/search-amazon-top|class="getdeal" data-search-amazon|Compare all|See all &quot;/.test(markup),
      'no recovery pill before or after Amazon responds: ' + reason);
    assert(markup.includes('Try fewer words') && markup.includes('Clear search') && markup.includes('Browse available deals instead:'));
    if (!reason) {
      assert(recovery.elements['amazon-results'].innerHTML.includes('View on Amazon'));
      assert(recovery.elements['amazon-results'].innerHTML.includes('Phone holder'));
    } else assert.equal(recovery.elements['amazon-results'].innerHTML, '');
    recovery.click('[data-search-amazon]');
    assert.equal(recovery.opened.at(-1)[0],
      'https://www.amazon.com/s?k=phone%20holder&tag=fixture-coupons-20');
  }
  console.log('PASS: zero-local live/empty/error states keep cards, hints and a plain Amazon exit');
  const typed = setup();
  Object.assign(typed.ctx, {freshView:() => false, freshUI:null, spaceSameProducts:rows => rows});
  typed.ctx.state.deals = [deal(1, 'Wireless mouse')];
  typed.elements.q.value = 'phone holder';
  typed.emit('q', 'input');
  await turn(); await turn();
  await typed.advance(150); // Input debounce renders and starts the search debounce.
  await typed.advance(599);
  assert.equal(typed.calls.length, 0, 'typing search has not reached its 600ms debounce');
  await typed.advance(1);
  assert.equal(typed.calls.length, 1, 'typing alone issues the request at 150 + 600ms');
  assert.equal(typed.calls[0].url, '/api/search?q=phone%20holder' + (more ? '&page=0' : ''));
  await typed.respond(typed.calls[0], {items:[typed.item(901, 'Phone holder')]});
  assert(typed.elements['amazon-results'].innerHTML.includes('Phone holder'), 'typing request painted');
  const loaded = typed.analyzeModules.length;
  typed.emit('analyze-search', 'click');
  typed.click('#analyze-search');
  await typed.advance(1000);
  assert.equal(typed.calls.length, 1, 'settled typed-word click adds no API call');
  assert.equal(typed.analyzeModules.length, loaded, 'click adds no lazy Analyze load');
  assert.equal(typed.ctx.analyzeTask, null, 'click never starts Analyze');
  assert.equal(typed.opened.length, 1, 'click opens Amazon');
  console.log('PASS: typing requests /api/search after 150 + 600ms; settled click adds none');
  const s = setup();
  Object.assign(s.ctx, {freshView:() => false, freshUI:null, spaceSameProducts:rows => rows});
  s.ctx.state.deals = [deal(1, 'Wireless mouse', {image_url:'https://m.media-amazon.com/images/I/a.jpg'})];
  await s.query('https://amazon.com/dp/B000000901?tag=PRIVATE');
  s.emit('analyze-search', 'click');
  for (let i = 0; i < 4; i++) await turn();
  assert.equal(s.calls.length, 1);
  assert.equal(s.calls[0].url, '/api/search?asin=B000000901');
  await s.respond(s.calls[0], {items:[s.item(901, 'Phone holder')]});
  await s.advance(900);
  assert(s.ctx.analyzeTask.finished);
  assert(s.elements['amazon-results'].innerHTML.includes('Phone holder'), 'pasted product retains the live analysis card');
  assert(!s.elements.content.innerHTML.includes('search-result-top'), 'no product-variant pill');
  assert.equal(s.ctx.analyzeUI ? s.ctx.analyzeUI.input('B000000901').kind : s.ctx.analyzeInput('B000000901').kind, 'amazon');
  await s.query('phone holder');
  s.emit('q', 'keydown', {key:'Enter', preventDefault(){}});
  for (let i = 0; i < 4; i++) await turn();
  assert.equal(s.calls.length, 2, 'Enter retains text analysis rather than opening Amazon');
  await s.respond(s.calls[1], {items:[s.item(902, 'Phone holder from Amazon')]});
  await s.advance(900);
  assert(s.ctx.analyzeTask.finished);
  assert(s.elements['amazon-results'].innerHTML.includes('Phone holder from Amazon'));
  assert(!s.elements.content.innerHTML.includes('search-result-top'), 'no typed-results pill');
  assert(!JSON.stringify(s.events).includes('PRIVATE'));
  console.log('PASS: real inline/lazy product analysis, live cards and unchanged Enter routing');
})().catch(error => {console.error(error); process.exitCode = 1;});
