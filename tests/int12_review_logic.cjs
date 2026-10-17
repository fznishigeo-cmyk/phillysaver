// Execute published ordering/ticker code offline, including stripped output.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const root = process.argv[2], scenario = process.argv[3], enabled = process.argv[4] === '1';
const home = fs.readFileSync(root + '/index.html', 'utf8');
const payload = JSON.parse(fs.readFileSync(root + '/deals.json', 'utf8'));
function fragment(start, end) {
  const a = home.indexOf(start), b = home.indexOf(end, a);
  assert(a >= 0 && b > a, start);
  return home.slice(a, b);
}
const controls = {q:{value:'', addEventListener(){}}, storesel:{value:''}, minpct:{value:''}};
const ctx = {Set, Map, URLSearchParams, location:{search:''}, state:{meta:payload.meta, deals:payload.deals, spotlight:payload.spotlight || [],
  catTab:3, sortKey:'top', sortDir:-1, seed:42, newKeys:{}},
  document:{getElementById:id=>controls[id] || null, addEventListener(){}},
  CATGROUPS:JSON.parse(home.match(/var CATGROUPS = (.*);/)[1]),
  num:x=>x == null ? null : Number(x), dealKey:d=>d.key, isLocked:d=>!!d.locked,
  JITTER_H:3, NO_PHOTO_H:12, offer:()=>({trial:false}), esc:String,
  eligibilityActive:()=>false, matchesEligibility:()=>true};
vm.createContext(ctx);
vm.runInContext(fragment('  function cmp(', '  function storeHue('), ctx);
vm.runInContext(fragment('  function frontPageView(', '  function frontPageMix('), ctx);
vm.runInContext(fragment('  function activeSpotlight(', '  function paintSpotlightTime('), ctx);
vm.runInContext(fragment('  var TICKER_MAX', '  var tickerPending'), ctx);
if (scenario === 'scores') {
  const scores = JSON.parse(fs.readFileSync(root + '/private-scores.json', 'utf8'));
  // Score consumers compare ordering only. Preserve both directions, ties,
  // missing scores, search/department ordering and homepage pick ordering.
  const numeric = payload.deals.map(d=>({...d, ps:scores[d.dedupe_key]}));
  for (const dir of [-1, 1]) for (const tab of [0, 3]) {
    ctx.state.sortDir = dir; ctx.state.catTab = tab;
    assert.deepEqual(Array.from(ctx.topOrder(payload.deals), d=>d.key),
                     Array.from(ctx.topOrder(numeric), d=>d.key));
  }
  assert.deepEqual(Array.from(ctx.tickerItems(), d=>d.key),
    (ctx.state.deals = numeric, Array.from(ctx.tickerItems(), d=>d.key)));
} else {
  const used = d=>(d.conditions || []).some(c=>c === 'used' || c === 'open_box');
  assert(payload.deals.some(used), 'nonempty used/open-box inventory');
  const ticker = Array.from(ctx.tickerItems(), d=>d.key);
  assert(ticker.length > 0, 'nonempty ticker');
  for (const d of payload.deals) assert.equal(ticker.includes(d.key), !enabled || !used(d));
  vm.runInContext(fragment('  function storeKey(', '  function searchInventory('), ctx);
  ctx.state.catTab = ctx.CATGROUPS.findIndex(g=>g[1] === 'USED');
  assert(Array.from(ctx.filtered()).some(used), 'Used tab retains the inventory');
}
console.log('int12 emitted ' + scenario + ': passed');
