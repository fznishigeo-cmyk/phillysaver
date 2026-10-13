// Run the published renderer and scheduling code with an offline DOM.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const home = fs.readFileSync(process.argv[2], 'utf8');
function fragment(start, end) {
  const a = home.indexOf(start), b = home.indexOf(end, a);
  assert(a >= 0 && b > a, start);
  return home.slice(a, b);
}
const rows = Array.from({length:18}, (_, i) => ({key:String(i), status:'alive',
  image_url:`https://img.example/${i}.jpg`, store:'Target', item:`Product ${i}`,
  pct_at_checkout:80, final_price:9, credits:[], conditions:[]}));
const content = {innerHTML:'', querySelector:()=>null, appendChild(){}};
const controls = Object.fromEntries(['q','storesel','minpct'].map(id=>[id,{value:''}]));
const ctx = {
  loadedOnce:true,
  state:{meta:{floors:{min_pct:70}}, deals:rows, shown:60, newKeys:{}, sortKey:'fresh',
    sortDir:-1, catTab:0}, moreObserver:null, panel:{}, cardImageIndex:0, traceFree:false,
  TIER_LABEL:{}, num:x=>x == null ? null : Number(x), esc:x=>String(x ?? ''),
  document:{getElementById:id=>id === 'content' ? content : controls[id] || null},
  window:{wpSupport:{refresh(){}}}, paintTicker(){},
  engagementLineHtml:()=>'', filtered:()=>rows, ordered:rows=>rows,
  renderSearchCount(){}, updatePaywall(){}, openNudge:()=>false, canJoin:()=>false,
  openAccess:()=>false, isLocked:d=>!!d.locked, storeHue:()=>0, usedTag:()=>'',
  isNew:()=>false, newSinceVisit:()=>false, isFreePick:()=>false, lit:()=>false,
  dealKey:d=>d.key, ageText:()=>'', hotHtml:()=>'', traceSvg:()=>'',
  openBand:()=>'<p>Join free</p>'
};
vm.createContext(ctx);
vm.runInContext(fragment('  function mediaHtml(', '  // ---- the deal ticker ----'), ctx);
vm.runInContext(fragment('  function gridHtml(', '  function showMore('), ctx);
ctx.render();
const wall = content.innerHTML;
const creditCard = ctx.cardHtml({...rows[0], credits:[
  {name:'Amazon price drop', url:''}, {name:'Slickdeals', url:'https://slickdeals.net/f/1'}]});
assert(creditCard.includes('via Amazon price drop, via <a href="https://slickdeals.net/f/1" rel="noopener">Slickdeals</a>'));
assert(!creditCard.includes('href=""'));
// The support banner splits the grids after eight; it must not reset priority.
assert.equal((wall.match(/fetchpriority="high"/g) || []).length, 4);
ctx.render();
const secondRenderHigh = (content.innerHTML.match(/fetchpriority="high"/g) || []).length;
// Photo-less cards consume no image priority slots.
ctx.cardImageIndex = 0;
ctx.mediaHtml({store:'Target'});
assert(ctx.mediaHtml(rows[0]).includes('fetchpriority="high"'));
function ticker(idle) {
  const events = [], frames = [], timers = [], idles = [];
  const t = {window:{}, requestAnimationFrame:cb=>frames.push(cb),
    setTimeout:cb=>timers.push(cb)};
  if (idle) t.window.requestIdleCallback = cb=>idles.push(cb);
  vm.createContext(t);
  vm.runInContext(fragment('  var tickerPending = false;', '  function paintTickerNow()'), t);
  t.paintTickerNow = ()=>events.push('paint');
  t.paintTicker(); t.paintTicker();
  assert.equal(frames.length,1); // coalesced while waiting for a paint
  assert.deepEqual(events,[]);
  events.push('frame'); frames.shift()();
  assert.deepEqual(events,['frame']);
  events.push('timeout'); timers.shift()();
  if (idle) {events.push('idle'); idles.shift()();}
  else {events.push('timeout'); timers.shift()();}
  return events;
}
process.stdout.write(JSON.stringify({wall,secondRenderHigh,idle:ticker(true),fallback:ticker(false)}));
