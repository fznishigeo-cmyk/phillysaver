// Exercise the actual emitted card renderer without a browser or requests.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const root = process.argv[2], home = fs.readFileSync(root + '/index.html', 'utf8');
const deals = JSON.parse(fs.readFileSync(root + '/deals.json', 'utf8')).deals;
function fragment(start, end) {
  const a = home.indexOf(start), b = home.indexOf(end, a);
  assert(a >= 0 && b > a, start);
  return home.slice(a, b);
}
const ctx = {cardImageIndex:0, state:{newKeys:{}}, TIER_LABEL:{verified:'✓ verified'},
  num:v=>v == null ? null : Number(v), esc:v=>String(v ?? ''),
  isLocked:d=>!!d.locked, storeHue:()=>0, usedTag:()=>'', goneText:()=>'',
  isNew:()=>false, newSinceVisit:()=>false, isFreePick:()=>false, lit:()=>false,
  dealKey:d=>d.key, ageText:()=>'', priceCheckedText:()=>'', hotHtml:()=>'', traceSvg:()=>''};
vm.createContext(ctx);
vm.runInContext(fragment('  function mediaHtml(', '  // ---- the deal ticker ----'), ctx);
for (const d of deals) {
  const card = ctx.cardHtml(d);
  assert(!/90-day|average|usual|lowest|highest|history/i.test(card));
  assert(!card.includes('$50.00'));
  if (d.price_drop) {
    assert(card.includes('>Price drop</span>'));
    assert(!card.includes('<s>') && !card.replace(/<[^>]*>/g, '').includes('%') && !card.includes('save $'));
  } else {
    assert(card.includes('List price <s>$25.00</s>'));
    assert(card.includes('-60%') && card.includes('save $15.00'));
  }
}
ctx.state.deals = deals;
ctx.offer = () => ({trial:false});
if (home.includes('  function usedOrOpenBox(')) {
  vm.runInContext(fragment('  function usedOrOpenBox(', '  function searchNewFirst('), ctx);
}
vm.runInContext(fragment('  var TICKER_MAX', '  var tickerPending'), ctx);
for (const item of ctx.tickerItems()) {
  const d = deals.find(d => d.key === item.key);
  if (d.price_drop) {
    assert.equal(item.stamp, 'Price drop');
    assert(!item.plain.includes('%'));
  } else {
    assert.equal(item.stamp, '-60%');
  }
}
// The published floor split keeps privately qualified chips in their tier.
const split = home.match(/var split = rows.findIndex\(function \(d, i\) \{([\s\S]*?)\n    \}\);/);
assert(split);
ctx.top = false; ctx.mixed = false; ctx.alertFloor = 70;
const below = vm.runInContext('(function(d,i){' + split[1] + '})', ctx);
assert.equal(below({pct_at_checkout:null, primary_offer:true}, 30), false);
assert.equal(below({pct_at_checkout:60, primary_offer:true}, 30), false);
assert.equal(below({pct_at_checkout:null, primary_offer:false}, 30), true);
assert.equal(below({pct_at_checkout:75}, 30), false);
process.stdout.write('Amazon reference renderer and hidden tier: passed\n');
