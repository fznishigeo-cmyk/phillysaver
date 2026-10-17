// Execute the published ordering and rendering functions, with no network or DOM engine.
const fs = require('node:fs');
const vm = require('node:vm');
const home = fs.readFileSync(process.argv[2], 'utf8');
const cases = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const defaultSort = home.match(/sortKey: "([^"]+)"/)[1];
function fragment(start, end) {
  const a = home.indexOf(start), b = home.indexOf(end, a);
  if (a < 0 || b < a) throw Error(start);
  return home.slice(a, b);
}
const results = cases.map(c => {
  const controls = Object.fromEntries(['q', 'storesel', 'minpct'].map(id =>
    [id, {value: c.controls?.[id] || ''}]));
  const content = {innerHTML:'', querySelector:()=>null, appendChild(){}};
  class FixedDate extends Date { static now() {return Date.parse('2026-10-09T14:00:00Z');} }
  const ctx = {
    Date:FixedDate, loadedOnce:true, state:{meta:{front_page:c.options, floors:{min_pct:70}},
      catTab:0, sortKey:defaultSort, sortDir:-1, seed:42, newKeys:{}, deals:c.rows, shown:10000, ...c.state},
    document:{getElementById:id => id === 'content' ? content : controls[id] || null},
    num:x => x == null ? null : Number(x), dealKey:d=>d.key,
    isLocked:d=>d.locked === true, sortVal:(d,k)=>d[k === 'pct' ? 'pct_at_checkout' : k],
    JITTER_H:3, NO_PHOTO_H:12, AUTO_CARDS:Number(home.match(/var AUTO_CARDS = (\d+);/)[1]), moreObserver:null, panel:{},
    CATGROUPS:[['Trending', null, ''], ['All deals', null, ''], ['Used & Open-Box', 'USED', 'used-open-box'],
               ['Home & Kitchen', ['home', 'kitchen'], 'home-kitchen']],
    filtered:()=>c.rows, eligibilityActive:()=>false, paintTicker(){}, updatePaywall(){}, openNudge:()=>false,
    searchText:()=>controls.q.value.trim(),
    renderSearchCount(){},
    openAccess:()=>false, canJoin:()=>false,
    renderSavedControls(){}, savedOn:()=>false, engagementLineHtml:()=>'', openBand:()=>'',
    cardHtml:d=>`<i data-key="${d.key}"></i>`
  };
  vm.createContext(ctx);
  vm.runInContext(fragment('  function cmp(', '  function storeHue('), ctx);
  vm.runInContext(fragment('  function gridHtml(', '  function showMore('), ctx);
  let observed = 0;
  ctx.window = {IntersectionObserver:true, wpSupport:{refresh(){}}};
  ctx.IntersectionObserver = class {observe(){observed++;} disconnect(){}};
  ctx.armMore({});
  const base = ctx.ordered(c.rows, true);
  const ordered = ctx.ordered(c.rows);
  ctx.render();
  let dropRenders = 0;
  if (c.drop) {
    Object.assign(ctx, {reduceMotion:()=>true, headKeys:()=> 'same,head',
      cardOrder:()=>content.innerHTML, rememberFocus:()=>null, restoreFocus(){}, clock:{}});
    const render = ctx.render;
    ctx.render = () => {dropRenders++; render();};
    vm.runInContext(fragment('  function reorganize(', '  // ---- paywall'), ctx);
    ctx.reorganize(true, false);
  }
  return {base:base.map(d=>d.key), ordered:ordered.map(d=>d.key),
    mixed:ctx.frontPageMix(c.rows, c.options).map(d=>d.key),
    rendered:[...content.innerHTML.matchAll(/data-key="([^"]+)"/g)].map(m=>m[1]),
    active:!!ctx.frontPageView(), seed:ctx.state.seed, dropRenders, observed, defaultSort,
    strongSection:content.innerHTML.includes('More verified deals'), html:content.innerHTML};
});
process.stdout.write(JSON.stringify(results));
