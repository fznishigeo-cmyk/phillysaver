// Execute published filtering, ordering and rendering without a browser/network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const home = fs.readFileSync(process.argv[2], 'utf8');
const payload = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const enabled = process.argv[4] === '1';
const scenario = process.argv[5] || 'basic';
function fragment(start, end) {
  const a = home.indexOf(start), b = home.indexOf(end, a);
  assert(a >= 0 && b > a, start);
  return home.slice(a, b);
}
const controls = {};
function element(id) {
  if (id === 'moremore' || id === 'totop') return null;
  return controls[id] ||= {value:'', innerHTML:'', hidden:false, addEventListener(){},
    querySelector:()=>null, querySelectorAll:()=>[], appendChild(){}, setAttribute(){}};
}
const used = d => (d.conditions || []).some(c => c === 'used' || c === 'open_box');
const keys = rows => Array.from(rows, d => d.key);
class FixedDate extends Date {static now() {return Date.parse(payload.meta.generated_at);}}
const ctx = {Date:FixedDate, Set, Map, state:{meta:payload.meta, deals:payload.deals, spotlight:payload.spotlight || [],
  catTab:0, sortKey:'top', sortDir:-1, seed:42, newKeys:{}, shown:10000}, loadedOnce:true,
  document:{getElementById:element}, CATGROUPS:JSON.parse(home.match(/var CATGROUPS = (.*);/)[1]),
  num:x=>x == null ? null : Number(x), dealKey:d=>d.key, isLocked:d=>d.locked === true,
  sortVal:(d,k)=>d[k === 'pct' ? 'pct_at_checkout' : k],
  JITTER_H:3, NO_PHOTO_H:12, AUTO_CARDS:300, moreObserver:null, panel:{},
  eligibilityActive:()=>false, matchesEligibility:()=>true, paintTicker(){}, updatePaywall(){},
  openNudge:()=>false, openAccess:()=>false, canJoin:()=>false, track(){},
  esc:s=>String(s), cardHtml:d=>`<i data-key="${d.key}"></i>`};
vm.createContext(ctx);
for (const [start,end] of [
  ['  function plural(', '  // The open (unlocked'],
  ['  function storeKey(', '  function cmp('],
  ['  function cmp(', '  function storeHue('],
  ['  function gridHtml(', '  function showMore(']
]) vm.runInContext(fragment(start,end), ctx);
function rendered() {
  ctx.render();
  return [...element('content').innerHTML.matchAll(/data-key="([^"]+)"/g)].map(m=>m[1]);
}
if (scenario === 'history') {
  Object.assign(ctx, {TIER_LABEL:{verified:'✓ verified', corroborated:'✓ 2+ sources'},
    isMember:()=>false, unknownMembership:()=>false, auth:{checking:false}});
  vm.runInContext(fragment('  function storeHue(', '  // ---- the deal ticker ----'), ctx);
  const deal = payload.deals.find(d=>d.store === 'Amazon');
  assert(deal, 'Amazon fixture required');
  const card = ctx.cardHtml(deal);
  assert(!/<s>|save \$|90.day|usual \$|class="flag"/i.test(card));
  assert(card.includes('class="now">$10.00'));
  console.log('history comparator absent from real browser card');
  process.exit(0);
}
if (scenario !== 'basic') {
  const all = [...payload.deals, ...(payload.spotlight || [])];
  for (const sort of ['top', 'fresh', 'pct']) {
    ctx.state.sortKey = sort;
    element('q').value = 'Fixture';
    const actual = rendered().map(key=>all.find(d=>d.key === key));
    assert.equal(new Set(keys(actual)).size, actual.length, 'deduplicate spotlight');
    const live = actual.filter(d=>d.status !== 'dead');
    const expected = new Set(keys(all.filter(d=>d.status !== 'dead')));
    assert.deepEqual(new Set(keys(live)), expected, `${sort}: retain every live match`);
    if (enabled) {
      const cut = live.findIndex(used);
      assert(cut > 0 && live.slice(cut).every(used), `${sort}: used spotlight follows all new live matches`);
    }
    if (scenario === 'spotlight') {
      assert(payload.spotlight?.some(used), 'nonempty qualifying used spotlight');
      element('q').value = '';
      ctx.state.catTab = ctx.CATGROUPS.findIndex(g=>g[1] === 'USED');
      assert(rendered().includes(payload.spotlight[0].key), 'spotlight remains in Used tab');
      ctx.state.catTab = 0;
    } else {
      const gone = actual.map((d,i)=>d.status === 'dead' ? i : -1).filter(i=>i >= 0);
      assert.equal(gone.length, 1, `${sort}: retain eligible ended match`);
      assert(gone[0] >= 8, `${sort}: ended match stays outside first eight`);
    }
  }
  console.log(`${scenario} search ordering passed for three sorts`);
  process.exit(0);
}
for (const sort of ['top', 'fresh', 'pct']) {
  ctx.state.sortKey = sort;
  for (let tab = 0; tab < ctx.CATGROUPS.length; tab++) {
    ctx.state.catTab = tab;
    const group = ctx.CATGROUPS[tab][1];
    const expected = payload.deals.filter(d => group === 'USED' ? used(d) :
      (!group || group.includes(d.category)) && (!enabled || !used(d)));
    assert.deepEqual(keys(ctx.filtered()), keys(expected), `${sort}, tab ${tab}`);
    if ([0,1,2,3].includes(tab)) {
      assert.deepEqual(new Set(rendered()), new Set(keys(expected)), `render ${sort}, tab ${tab}`);
    }
  }
  ctx.state.catTab = 4; // Search crosses the selected department.
  element('q').value = 'Fixture';
  const found = ctx.filtered();
  assert.deepEqual(new Set(keys(found)), new Set(keys(payload.deals)));
  const ordered = ctx.ordered(found);
  if (enabled) {
    const partitioned = rows => {
      const cut = rows.findIndex(used);
      assert(cut > 0 && rows.slice(cut).every(used), `${sort}: used follows new`);
    };
    partitioned(Array.from(ordered));
    partitioned(rendered().map(key => payload.deals.find(d => d.key === key)));
    // The final partition retains the relative order of each condition group.
    const partition = ctx.searchNewFirst(payload.deals);
    assert.deepEqual(keys(partition.filter(d=>!used(d))), keys(payload.deals.filter(d=>!used(d))));
    assert.deepEqual(keys(partition.filter(used)), keys(payload.deals.filter(used)));
  }
  element('storesel').value = 'missing-store';
  assert.equal(ctx.filtered().length, 0);
  element('storesel').value = '';
  element('q').value = '';
}
assert(payload.deals.some(used), 'used inventory must stay in deals.json');
assert.equal(home.includes('function usedOrOpenBox'), enabled);
console.log('used-tab-only logic passed (All, departments, Used, search, three sorts, render).');
