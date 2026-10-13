// Exercise the published spotlight and real cards/filters with no network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const home = fs.readFileSync(process.argv[2], 'utf8');
const doc = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
function fragment(start, end) {
  const a = home.indexOf(start), b = home.indexOf(end, a);
  assert(a >= 0 && b > a, start);
  return home.slice(a, b);
}
let now = Date.parse('2026-10-09T14:00:00Z'), html = '', countdowns = [];
const content = {querySelector:()=>null, appendChild(){},
  get innerHTML() {return html;},
  set innerHTML(value) {
    html = value;
    countdowns = [...value.matchAll(/data-ends-at="([^"]+)">([^<]*)/g)].map(m=>({
      getAttribute:()=>m[1], textContent:m[2], hidden:false}));
  }};
const controls = Object.fromEntries(['q','storesel','minpct','clearsearch','searchcount','amazon-strip'].map(id=>[id,{value:''}]));
Object.assign(controls.storesel, {options:[{value:''}], appendChild(o) {this.options.push(o);},
  remove(i) {this.options.splice(i,1);},
  get selectedIndex() {return this.options.findIndex(o=>o.value === this.value);},
  set selectedIndex(i) {this.value = this.options[i].value;}});
class FixedDate extends Date {static now() {return now;}}
const ctx = {Date:FixedDate, state:{meta:doc.meta, deals:doc.deals, spotlight:doc.spotlight,
    shown:60, seed:42, newKeys:{}, catTab:0, sortKey:'top', sortDir:-1},
  CATGROUPS:JSON.parse(home.match(/var CATGROUPS = (.*);/)[1]),
  document:{getElementById:id=>id === 'content' ? content : controls[id],
    querySelectorAll:()=>countdowns, createElement:()=>({})},
  window:{}, panel:{}, moreObserver:null, AUTO_CARDS:300, JITTER_H:3, NO_PHOTO_H:12,
  TIER_LABEL:{corroborated:'✓ 2+ sources', unverified:'single source'},
  num:x=>x == null ? null : Number(x), esc:x=>String(x ?? '').replaceAll('&','&amp;')
    .replaceAll('"','&quot;').replaceAll('<','&lt;'), dealKey:d=>d.key,
  plural:(n,word)=>n + ' ' + word + (n === 1 ? '' : 's'),
  isLocked:d=>!!d.locked, isMember:()=>false, unknownMembership:()=>false,
  auth:{checking:false}, sortVal:(d,k)=>d[k === 'pct' ? 'pct_at_checkout' : k],
  paintTicker(){}, updatePaywall(){}, renderSearchCount(){}, openNudge:()=>false,
  renderMeta(){}, paintClock(){}, clock:{}, loadedOnce:false,
  openAccess:()=>true, canJoin:()=>false, rememberFocus:()=>null, restoreFocus(){}, offer:()=>({})};
vm.createContext(ctx);
for (const [a,b] of [
  ['  function storeKey(', '  function cmp('],
  ['  function cmp(', '  function storeHue('],
  ['  function storeHue(', '  // ---- the deal ticker ----'],
  ['  var TICKER_MAX', '  var tickerPending'],
  ['  function gridHtml(', '  function showMore('],
  ['  function tick()', '  setInterval(tick'],
  ['  function refreshStores()', '  // ---- publishes:']
]) vm.runInContext(fragment(a,b), ctx);
const spot = doc.spotlight[0], unknown = spot.pct_at_checkout == null;
const keys = () => [...html.matchAll(/data-key="([^"]+)"/g)].map(m=>m[1]);
ctx.populateStores();
assert(controls.storesel.options.some(o=>o.value === 'meh'));
for (const catTab of [0,1]) {
  ctx.state.catTab = catTab;
  for (const seed of [1,2]) {
    ctx.state.seed = seed;
    const expected = Array.from(ctx.ordered(ctx.filtered(), true), d=>d.key).filter(k=>k !== spot.key);
    ctx.render();
    assert.equal(keys()[0], spot.key);
    assert.equal(keys().filter(k=>k === spot.key).length,1);
    assert.deepEqual(keys().slice(1), expected); // Grid still uses its existing drop order.
  }
}
assert.match(html,/role="region" aria-label="Today only deal"/);
assert.match(html,/Today only · Meh/);
assert.match(html,/ends in 15h 0m/);
assert.match(html,/class="now">\$10\.00/);
const card = ctx.cardHtml({...spot, regular_price:500, abs_savings:490});
if (unknown) {
  assert(!/%|save \$/.test(card.replace(/<[^>]+>/g,'')));
  assert(!card.includes('<s>'));
  assert(!ctx.tickerItems().some(d=>d.key === spot.key));
} else assert.match(card,/class="flag">-80%/);
ctx.state.catTab = 3; ctx.render();
assert(!html.includes('Today only'));
assert.equal(keys().includes(spot.key),!unknown); // Known deal remains in its category.
ctx.state.catTab = 0;
for (const [id,value] of [['q','missing'], ['storesel','target'], ['minpct','90']]) {
  controls[id].value=value; ctx.render();
  assert(!html.includes('Today only'));
  controls[id].value='';
}
controls.q.value='today offer'; ctx.render();
assert.deepEqual(keys(),[spot.key]);
controls.q.value=''; controls.storesel.value='meh'; ctx.render();
assert.deepEqual(keys(),[spot.key]);
controls.storesel.value=''; controls.minpct.value='70'; ctx.render();
assert.equal(html.includes('Today only'),!unknown);
controls.minpct.value='';
ctx.render(); now += 60000; ctx.tick();
assert.equal(countdowns[0].textContent,'ends in 14h 59m');
now = Date.parse(spot.spotlight.until); ctx.tick();
assert(!html.includes('Today only'));
assert.equal(keys().includes(spot.key),!unknown);
assert.equal(controls.storesel.options.some(o=>o.value === 'meh'),!unknown);
// With no countdown from the source, the card retains its ribbon but no clock.
now = Date.parse('2026-10-09T14:00:00Z');
ctx.state.spotlight = [{...spot, spotlight:{...spot.spotlight, ends_at:''}}]; ctx.render();
assert(!html.includes('spotlight-time'));
assert(html.includes('Today only'));
// Price-only supply can still render when the ordinary grid is empty.
ctx.state.deals = []; ctx.render();
assert.deepEqual(keys(),[spot.key]);
assert(!html.includes('No qualifying deals'));
