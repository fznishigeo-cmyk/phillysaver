// Run the emitted card and top-pick ordering without requests or a browser.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const home = fs.readFileSync(process.argv[2] + '/index.html', 'utf8');
const doc = JSON.parse(fs.readFileSync(process.argv[2] + '/deals.json', 'utf8'));
function fragment(start, end) {
  const a = home.indexOf(start), b = home.indexOf(end, a);
  assert(a >= 0 && b > a, start);
  return home.slice(a, b);
}
const ctx = {state:{meta:doc.meta,newKeys:{},seed:0,catTab:0,sortKey:'top',sortDir:-1}, TIER_LABEL:{},
  document:{getElementById:()=>({value:''})},
  isLocked:d=>!!d.locked, lockedHtml:()=>'', num:v=>v == null ? null : Number(v),
  dealKey:d=>d.key, mediaHtml:()=>'', isNew:()=>false, lit:()=>false, isFreePick:()=>false,
  traceSvg:()=>'', newSinceVisit:()=>false, savedButtonHtml:()=>'', savedWatchHtml:()=>'', hotHtml:()=>'',
  frontPageView:()=>true, JITTER_H:3,NO_PHOTO_H:12};
vm.createContext(ctx);
vm.runInContext(fragment('  function esc(s)', '  function sortVal'), ctx);
vm.runInContext(fragment('  function ageText(h)', '  function usedTag(d)'), ctx);
vm.runInContext(fragment('  function spotlightTime(endsAt, now)', '  function frontPageView()'), ctx);
vm.runInContext(fragment('  function checkoutPriceHtml(d)', '  // ---- the deal ticker ----'), ctx);
const d = doc.deals[0];
const live = ctx.cardHtml(d), expected = ctx.esc(process.argv[3]);
assert(live.includes(expected));
assert(live.indexOf(expected) < live.indexOf('How to get this price:'));
assert(live.includes('>Get deal</a>') && live.includes('price checked '));
assert(!ctx.cardHtml({...d, checkout_price:null}).includes('Amazon shows'));
assert(!ctx.cardHtml({...d, status:'dead'}).includes('Amazon shows'));
assert(!ctx.cardHtml({...d, locked:true}).includes('Amazon shows'));
vm.runInContext(fragment('  function cmp(', '  function storeHue('), ctx);
const rows = Array.from({length:30}, (_,i)=>({...d,key:String(i),pick:i+1}));
for (let seed=0;seed<10;seed++) {
  ctx.state.seed=seed;
  ctx.state.newKeys={'13':true,'24':true};
  assert.deepEqual(Array.from(ctx.topOrder(rows.slice().reverse()),d=>d.pick), rows.map(d=>d.pick));
}
assert(home.includes('.checkout-price{font-size:12.5px;color:var(--muted);line-height:1.4}'));
