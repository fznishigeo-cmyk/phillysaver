// Execute the emitted renderer and all ordering modes without a browser.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const html = fs.readFileSync(process.argv[2] + '/index.html', 'utf8');
const rows = JSON.parse(fs.readFileSync(process.argv[2] + '/deals.json', 'utf8')).deals;
const aged = rows.find(d => d.price_aged), fresh = rows.find(d => !d.price_aged);
function fragment(start, end) {
  const a = html.indexOf(start), b = html.indexOf(end, a);
  assert(a >= 0 && b > a, start);
  return html.slice(a, b);
}
const ctx = {offer:()=>({}),TICKER_MAX:16,CATGROUPS:[['Trending',null,'']],state:{deals:[aged,fresh], catTab:0, newKeys:{}, spotlight:[], sortKey:'top', sortDir:-1,
  meta:{front_page:{}, floors:{min_pct:0}}}, TIER_LABEL:{},
  document:{querySelectorAll:()=>[],getElementById:()=>({value:""})}, isLocked:d=>!!d.locked, lockedHtml:()=>'', num:v=>v == null ? null : Number(v),
  dealKey:d=>d.key, mediaHtml:()=>'', isNew:()=>false, lit:()=>false, isFreePick:()=>false,
  traceSvg:()=>'', newSinceVisit:()=>false, savedButtonHtml:()=>'', savedWatchHtml:()=>'', hotHtml:()=>'',
  topOrder:r=>r, freshOrder:r=>r, cmp:(a,b)=>a.final_price-b.final_price,
  frontPageView:()=>true, frontPageMix:r=>r.slice().reverse(), filtered:()=>[aged,fresh],
  watchPrice:p=>p, watchCondition:c=>c, engagementOn:()=>true};
vm.createContext(ctx);
vm.runInContext(fragment('  function esc(s)', '  function sortVal'), ctx);
vm.runInContext(fragment('  function ageText(h)', '  function usedTag(d)'), ctx);
// Function/declaration boundaries survive the shipped comment-stripping pass.
vm.runInContext(fragment('  function cardHtml(d)', '  var TICKER_MAX'), ctx);
if (html.includes('  function usedOrOpenBox(d)')) {
  vm.runInContext(fragment('  function usedOrOpenBox(d)', '  function searchNewFirst(rows)'), ctx);
}
vm.runInContext(fragment('  function spotlightTime(endsAt, now)', '  function frontPageView()'), ctx);
vm.runInContext(fragment(html.includes('  function photosFirst(rows)') ? '  function photosFirst(rows)' : '  function confirmedFirst(rows)', '  function storeHue(s)'), ctx);
for (const sort of ['top','fresh','final_price','pct_at_checkout','store']) {
  ctx.state.sortKey = sort;
  for (const defer of [false,true]) assert.equal(ctx.ordered([aged,fresh],defer)[0].key, fresh.key);
}
assert(ctx.cardHtml(aged).includes('<div class="price-aged">Posted 1 day ago</div>'));
assert(!ctx.cardHtml(fresh).includes('Posted 1 day ago'));
vm.runInContext(fragment('  function tickerItems()', '  var tickerPending'), ctx);
assert(ctx.tickerItems().every(d=>!d.price_aged));
vm.runInContext(fragment('  function surpriseRows()', '  document.getElementById("surprise")'), ctx);
assert(ctx.surpriseRows().every(d=>!d.price_aged));
vm.runInContext(fragment('  function savedWatchMatch(entry, current)', '  function checkSavedWatches('), ctx);
assert.equal(ctx.savedWatchMatch({key:aged.key,final_price:100,condition:aged.conditions,target_price:99},aged),null);
