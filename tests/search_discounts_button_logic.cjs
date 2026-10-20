const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const input = {value:''}, opened = [], tracks = [];
const ctx = {URL, analyzeTask:null, analyzeUI:null, searchText:() => input.value.trim(),
  document:{getElementById:() => input}, esc:String,
  window:{open:(...args) => opened.push(args)}, track:(...args) => tracks.push(args)};
vm.createContext(ctx);
for (const name of ['analyzeInput', 'analyzeSearchText', 'searchAmazonQuery', 'discountsLink', 'searchAmazonClick', 'searchAmazonHtml']) {
  const start = html.indexOf('  function ' + name + '(');
  if (start >= 0) vm.runInContext(html.slice(start, html.indexOf('\n  }', start) + 4), ctx);
}
function click(kind, type='click', button=0) {
  const target = {closest:selector => selector.split(',').includes(kind) ? target : null};
  ctx.searchAmazonClick({target, type, button, preventDefault(){}});
  return opened.at(-1);
}
for (const [raw, words] of [['makeup','makeup'], [' café & tea ','café & tea'],
  ['https://www.amazon.com/Nulaxy-Phone-Holder/dp/B012345678?tag=PRIVATE','Nulaxy Phone Holder'],
  ['https://www.amazon.com/dp/B012345678?tag=PRIVATE',null]]) {
  input.value = raw;
  const all = click('.all-discounts');
  for (const [type, button] of [['click',0], ['auxclick',1]]) {
    assert.deepEqual(click('[data-search-discounts]',type,button), all);
    assert.equal(tracks.at(-1)[0], 'search_discounts');
  }
  const url = new URL(all[0]);
  assert.equal(url.searchParams.get('tag'), 'fixture-coupons-20');
  if (words) {
    assert.equal(url.searchParams.get('k'), words);
    assert.equal(url.searchParams.get('rh'), 'p_n_deal_type:23566065011');
  } else assert.equal(url.pathname, '/dp/B012345678');
  assert(!url.href.includes('PRIVATE'));
  assert.deepEqual(all.slice(1), ['_blank','noopener']);
  if (ctx.searchAmazonHtml) {
    const markup = ctx.searchAmazonHtml();
    assert(markup.includes('href="https://www.amazon.com/" target="_blank" rel="sponsored noopener"'));
    assert.equal((markup.match(/See Amazon discounts →/g) || []).length, 1);
    assert(!markup.includes('?k=') && !markup.includes('PRIVATE'));
  }
}
input.value = '';
if (ctx.searchAmazonHtml) assert.equal(ctx.searchAmazonHtml(), '');
if (html.includes('id="event-search"')) process.exit(0);
const {setup, deal, turn} = require('./search_more_logic.cjs');
(async () => {
  for (const reason of [null, 'no_results', 'budget_exhausted', 'timeout']) {
    const s = setup();
    Object.assign(s.ctx, {freshView:() => false, freshUI:null, spaceSameProducts:rows => rows});
    s.ctx.state.deals = [deal(1, 'Makeup brush')];
    await s.query('makeup');
    assert(!s.elements['search-amazon-result'].hidden);
    assert.equal((s.elements['search-amazon-result'].innerHTML.match(/See Amazon discounts →/g) || []).length, 1);
    assert(!s.elements.content.innerHTML.includes('See Amazon discounts →'));
    await s.query('phone holder');
    assert(s.elements['search-amazon-result'].hidden);
    await s.advance(750);
    await s.respond(s.calls.at(-1), reason ? {items:[], reason} : {items:[s.item(901,'Phone holder')]});
    await s.advance(1500);
    assert.equal((s.elements.content.innerHTML.match(/See Amazon discounts →/g) || []).length, 1);
    s.click('.all-discounts,[data-search-discounts]');
    assert.equal(s.opened.at(-1)[0], 'https://www.amazon.com/s?k=phone%20holder&rh=p_n_deal_type%3A23566065011&tag=fixture-coupons-20');
    await s.query('');
    assert(s.elements['search-amazon-result'].hidden);
    assert(!s.elements.content.innerHTML.includes('See Amazon discounts →'));
  }
  for (const raw of ['https://amazon.com/Phone-Holder/dp/B000000901?tag=PRIVATE',
    'https://amazon.com/dp/B000000901?tag=PRIVATE']) {
    const s = setup();
    Object.assign(s.ctx, {freshView:() => false, freshUI:null, spaceSameProducts:rows => rows});
    s.ctx.state.deals = [deal(1, 'Wireless mouse')];
    await s.query(raw); s.emit('analyze-search', 'click');
    for (let n = 0; n < 4; n++) await turn();
    assert.equal(s.calls.length, 1);
    await s.respond(s.calls[0], {items:[s.item(901,'Phone holder')]});
    await s.advance(900);
    assert.equal((s.elements.content.innerHTML.match(/See Amazon discounts →/g) || []).length, 1,
      'inline/lazy pasted-link results get the same single exit');
    s.click('.all-discounts,[data-search-discounts]');
    const url = new URL(s.opened.at(-1)[0]);
    assert.equal(url.searchParams.get('k').toLowerCase(), 'phone holder');
    assert.equal(url.searchParams.get('rh'), 'p_n_deal_type:23566065011');
    assert.equal(url.searchParams.get('tag'), 'fixture-coupons-20');
  }
  console.log('PASS: actual emitted typed/link/empty URLs equal All Discounts; top/recovery/zero-zero keep one yellow exit');
})().catch(error => {console.error(error); process.exitCode = 1;});
