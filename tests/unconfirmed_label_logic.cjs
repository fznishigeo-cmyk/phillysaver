// Execute the emitted renderer/order and promotion gates without a browser or network.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const html = fs.readFileSync(process.argv[2] + '/index.html', 'utf8');
const rows = JSON.parse(fs.readFileSync(process.argv[2] + '/deals.json', 'utf8')).deals;
const label = rows.find(d => d.price_unconfirmed), confirmed = rows.find(d => !d.price_unconfirmed);
function fragment(start, end) {
  const a = html.indexOf(start), b = html.indexOf(end, a);
  assert(a >= 0 && b > a, start);
  return html.slice(a, b);
}
const ctx = {offer:()=>({}),TICKER_MAX:16,state:{deals:[label, confirmed], newKeys:{}, spotlight:[], sortKey:'top', sortDir:-1,
  meta:{front_page:{}, floors:{min_pct:0}}}, TIER_LABEL:{},
  document:{querySelectorAll:()=>[],getElementById:()=>({value:""})},
  isLocked:d=>!!d.locked, lockedHtml:()=>'', num:v=>v == null ? null : Number(v),
  dealKey:d=>d.key, mediaHtml:()=>'', isNew:()=>false, lit:()=>false, isFreePick:()=>false,
  traceSvg:()=>'', newSinceVisit:()=>false, savedButtonHtml:()=>'', savedWatchHtml:()=>'', hotHtml:()=>'',
  topOrder:r=>r, freshOrder:r=>r, cmp:(a,b)=>a.final_price-b.final_price,
  frontPageView:()=>true, frontPageMix:r=>r.slice().reverse(), filtered:()=>[label,confirmed],
  watchPrice:p=>p, watchCondition:c=>c, engagementOn:()=>true};
vm.createContext(ctx);
vm.runInContext(fragment('  function esc(s)', '  function sortVal'), ctx);
vm.runInContext(fragment('  function ageText(h)', '  function usedTag(d)'), ctx);
vm.runInContext(fragment('  function cardHtml(d)', '  var TICKER_MAX'), ctx);
vm.runInContext(fragment('  function spotlightTime(endsAt, now)', '  function frontPageView()'), ctx);
vm.runInContext(fragment(html.includes('  function photosFirst(rows)') ? '  function photosFirst(rows)' : '  function confirmedFirst(rows)', '  function storeHue(s)'), ctx);
for (const sort of ['top', 'fresh', 'final_price', 'pct_at_checkout', 'store']) {
  ctx.state.sortKey = sort;
  for (const defer of [false,true]) {
    const ordered = ctx.ordered([label,confirmed],defer);
    assert.equal(ordered[0].key, confirmed.key, sort);
    assert.equal(ordered[1].key, label.key, sort);
  }
}
const card = ctx.cardHtml(label);
assert.match(card, /class="pricerow">[\s\S]*?<\/div><div class="price-unconfirmed">Price not confirmed<\/div><h3/);
assert(!card.includes('aria-hidden="true">Price not confirmed'));
assert(!ctx.cardHtml(confirmed).includes('Price not confirmed'));
vm.runInContext(fragment('  function tickerItems()', '  var tickerPending'), ctx);
assert(ctx.tickerItems().every(d => d.key !== label.key));
vm.runInContext(fragment('  function surpriseRows()', '  document.getElementById("surprise")'), ctx);
assert(ctx.surpriseRows().every(d => !d.price_unconfirmed));
vm.runInContext(fragment('  function savedWatchMatch(entry, current)', '  function checkSavedWatches('), ctx);
const entry = {key:label.key,final_price:100,condition:label.conditions,target_price:99};
assert.equal(ctx.savedWatchMatch(entry,label),null);
if (process.argv[3] === 'list_price') {
  for (const row of rows) {
    assert.equal(row.amazon, true); // The public no-watch flag covers every store in this mode.
    const saved = {key:row.key,final_price:100,condition:row.conditions,target_price:99};
    const current = {...row,price_unconfirmed:false,price_aged:false};
    assert.equal(ctx.savedWatchMatch(saved,current),null);
  }
  vm.runInContext(fragment('  function savedWatchHtml(d)', '  function savedWatchEditor('), ctx);
  assert(rows.every(d => ctx.savedWatchHtml(d) === ''));
} else {
  assert(ctx.savedWatchMatch(entry,{...label,price_unconfirmed:false}));
}
assert(html.includes('rows = confirmedFirst(rows);'));
console.log('Label markup, every sort, ticker, Surprise and price watches passed.');
