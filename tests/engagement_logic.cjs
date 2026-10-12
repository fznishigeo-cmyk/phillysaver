// Run the published functions and click handler offline with a fixed local clock.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
process.env.TZ = 'America/New_York';
const home = fs.readFileSync(process.argv[2], 'utf8');
const enabled = {new_since_visit:true, weekly_finds:true, visit_streak:true, surprise_me:true, hot_meter:true};
const NOW = Date.parse('2026-10-12T12:00:00-04:00'); // Monday, local time
const row = (key, changes = {}) => ({key, status:'alive', item:'Item', store:'Store', category:'other',
  url:'https://store.test/item', pct_at_checkout:70, abs_savings:10.25, badge:'corroborated',
  src:['one.test','two.test'], first_seen_at:new Date(NOW-3600000).toISOString(), ...changes});
function fragment(start, end) {
  const a = home.indexOf(start), b = home.indexOf(end, a);
  assert.ok(a >= 0 && b > a, start);
  return home.slice(a,b);
}
function harness({stored={}, throwing='', switches={}, now=NOW, deals=[row('a')], reduced=false, shown=60} = {}) {
  let time = now, random = 0, wall = deals, timers = [], renders = 0;
  const storage = new Map(Object.entries(stored)), events = [], handlers = {}, windowHandlers = {};
  const makeClass = () => {
    const names = new Set();
    return {add:k=>names.add(k), remove:k=>names.delete(k), contains:k=>names.has(k),
      toggle(k,on) { if (on) names.add(k); else names.delete(k); }};
  };
  const elements = {};
  const makeElement = () => ({textContent:'', hidden:false, classList:makeClass(), listeners:{}, attributes:{},
    addEventListener(type, cb) {this.listeners[type] = cb;},
    setAttribute(k,v) {this.attributes[k]=v;}});
  for (const id of ['tagline','surprise','content']) elements[id] = makeElement();
  const lineIds = ['engagementline','visitstreak','engagementsep','weeklyfinds'];
  function readLine(html) {
    for (const id of lineIds) delete elements[id];
    if (!html.includes('id="engagementline"')) return;
    elements.engagementline = {remove() {for (const id of lineIds) delete elements[id];}};
    for (const id of lineIds.slice(1)) {
      const match = html.match(new RegExp('<span id="'+id+'"([^>]*)>(.*?)</span>'));
      elements[id] = makeElement();
      elements[id].hidden = /(?:^|\s)hidden(?:\s|$)/.test(match[1]);
      elements[id].textContent = match[2].replace(/&#39;/g,"'").replace(/&amp;/g,'&');
    }
  }
  Object.defineProperty(elements.content, 'innerHTML', {
    get() {return this.html || '';}, set(html) {this.html=html; readLine(html);}
  });
  elements.content.insertAdjacentHTML = (where,html) => {
    assert.equal(where,'afterbegin'); readLine(html);
  };
  elements.content.querySelector = () => null;
  elements.content.appendChild = () => {};
  const cards = () => wall.filter(d=>d.status!=='dead' && d.pct_at_checkout>=60)
    .concat(wall.filter(d=>d.status!=='dead' && d.pct_at_checkout<60))
    .slice(0, ctx.state.shown).map(d => {
    if (!elements[d.key]) elements[d.key] = {classList:makeClass(), getAttribute:()=>d.key,
      setAttribute(k,v) {this[k]=v;}, scrollIntoView(opts) {this.scroll=opts;}, focus(opts) {this.focused=opts;}};
    return elements[d.key];
  });
  class FixedDate extends Date {
    constructor(...args) {super(...(args.length ? args : [time]));}
    static now() {return time;}
  }
  const ctx = {Date:FixedDate, Math:Object.create(Math),
    state:{deals, meta:{engagement:{...enabled,...switches}, floors:{min_pct:60}}, shown}, PAGE_CARDS:60,
    document:{hidden:false, getElementById:id=>elements[id], addEventListener:(type,cb)=>handlers[type]=cb},
    window:{wpSupport:{refresh(){}}, wpTrack:(name,params)=>events.push({name,params}), addEventListener:(type,cb)=>windowHandlers[type]=cb},
    localStorage:{getItem(k) {if (throwing==='read') throw Error('blocked'); return storage.get(k) || null;},
      setItem(k,v) {if (throwing==='write') throw Error('readonly'); storage.set(k,v);},
      removeItem(k) {if (throwing==='write') throw Error('readonly'); storage.delete(k);}},
    ordered:rows=>rows, filtered:()=>wall, isLocked:d=>d.locked===true,
    cardEls:cards, moreObserver:null, renderSearchCount(){}, paintTicker(){}, paintSources(){}, updatePaywall(){},
    openNudge:()=>false, openBand:()=>'', panel:{}, reduceMotion:()=>reduced,
    setTimeout:cb=>timers.push(cb), mediaHtml:()=>'<span>photo</span>', ageText:()=>'',
    isNew:()=>false, isFreePick:()=>false, lit:()=>false, traceSvg:()=>'', TIER_LABEL:{corroborated:'✓ 2+ sources'},
    lockLabel:()=>'', toast(){}, navigator:{clipboard:{writeText:()=>Promise.resolve()}}
  };
  ctx.Math.random = () => random;
  vm.createContext(ctx);
  vm.runInContext(fragment('  function esc(s)', '  function sortVal'), ctx);
  vm.runInContext(fragment('  function dealKey(d)', '  function keySet'), ctx);
  vm.runInContext(fragment('  // Browser-only visit state.', '  function renderMeta()'), ctx);
  vm.runInContext(fragment('  function lockedHtml(d)', '  function cardHtml(d)'), ctx);
  vm.runInContext(fragment('  function cardHtml(d)', '  // ---- the sources wall'), ctx);
  vm.runInContext(fragment('  function gridHtml(deals)', '  function showMore()'), ctx);
  const renderWall = ctx.render;
  ctx.render = () => {renders++; renderWall(); cards();};
  const contentStart = home.lastIndexOf('  document.getElementById("content").addEventListener("click"');
  const contentEnd = home.indexOf('  // The ticker:', contentStart);
  assert.ok(contentStart >= 0 && contentEnd > contentStart);
  vm.runInContext(home.slice(contentStart,contentEnd), ctx);
  ctx.initEngagement();
  function paint() {elements.tagline.textContent='verified deals · 1 live deal'; ctx.renderEngagement(); ctx.render();}
  paint();
  function click(d, getDeal=true) {
    const card = {getAttribute:()=>d.key};
    const go = {closest:()=>card, classList:{contains:k=>getDeal && k==='getdeal'}};
    elements.content.listeners.click({target:{closest:s=>s.includes('sponsored') ? go : null}});
  }
  return {ctx, storage, events, elements, windowHandlers, handlers, paint, click,
    finds:()=>elements.weeklyfinds?.textContent || '', streak:()=>elements.visitstreak?.textContent || '',
    line:()=>elements.engagementline ? ['visitstreak','engagementsep','weeklyfinds']
      .filter(id=>!elements[id].hidden).map(id=>elements[id].textContent).join('') : '',
    time:n=>time=n, random:n=>random=n, wall:rows=>wall=rows,
    surprise:()=>elements.surprise.listeners.click(), timers:()=>timers.forEach(cb=>cb()), renders:()=>renders};
}
// The header keeps the original layout; private engagement belongs to the wall.
const header = home.match(/<header>([\s\S]*?)<\/header>/)[1];
assert.ok(!/engagement|weeklyfinds|visitstreak/.test(header));
for (const removed of ['engagement-slot','engagement-clock','visit-header','engagement-chip']) {
  assert.ok(!home.includes(removed));
}
assert.ok(home.includes('.sr-only{position:absolute;width:1px;height:1px;'));
assert.ok(home.includes('.engagement-line{color:var(--muted);font-size:12.5px;'));
// NEW stays below the two price/condition flags and opposite the drop's NEW.
assert.ok(home.includes('.visit-new{position:absolute;bottom:8px;right:8px;'));
assert.ok(home.includes('.flag{position:absolute;top:8px;left:8px;'));
assert.ok(home.includes('.condtag{position:absolute;top:8px;right:8px;'));
// Timestamp, not legacy wp.seen; count is stable until this visitor leaves.
let h = harness({stored:{'wp.lastVisit':String(NOW-2*3600000), 'wp.seen':'["a"]'}, deals:[
  row('a'), row('old',{first_seen_at:new Date(NOW-3*3600000).toISOString()}),
  row('edge',{first_seen_at:new Date(NOW-2*3600000).toISOString()}),
  row('dead',{status:'dead'}), row('future',{first_seen_at:new Date(NOW+1000).toISOString()}),
  row('unknown',{first_seen_at:'bad'}), row('a')
]});
assert.equal(h.ctx.engagement.newCount,1);
assert.ok(h.elements.tagline.textContent.endsWith(' · 1 new since your last visit'));
assert.ok(h.ctx.cardHtml(row('a')).includes('NEW<span class="sr-only"> since your last visit'));
assert.ok(!h.ctx.cardHtml(row('dead',{status:'dead'})).includes('visit-new'));
assert.equal(h.storage.get('wp.lastVisit'),String(NOW-2*3600000));
h.time(NOW+3600000); h.ctx.state.deals.push(row('later'));
h.ctx.initEngagement(); h.paint(); assert.equal(h.ctx.engagement.newCount,1);
h.windowHandlers.pagehide(); assert.equal(h.storage.get('wp.lastVisit'),String(NOW+3600000));
for (const stored of [{}, {'wp.lastVisit':'bad'}, {'wp.lastVisit':String(NOW+1000)}]) {
  h=harness({stored}); assert.equal(h.ctx.engagement.newCount,0);
  assert.ok(!h.elements.tagline.textContent.includes('new since'));
}
// Initial render builds the quiet line with the grid, with no live announcement.
const visitDay = h.ctx.localDay(new Date(NOW));
h=harness({stored:{
  'wp.visitStreak':JSON.stringify({day:visitDay-1,count:2}),
  'wp.weeklyFinds':JSON.stringify({week:'2026-10-12',finds:[{key:'a',cents:8600}]})
}});
assert.equal(h.line(), "🔥 3-day streak · You've found $86.00 in savings this week");
assert.ok(h.elements.content.innerHTML.startsWith('<p id="engagementline"'));
assert.ok(h.elements.content.innerHTML.indexOf('</p>') < h.elements.content.innerHTML.indexOf('<div class="grid">'));
assert.ok(!h.elements.content.innerHTML.includes('aria-live="polite"'));
h.click(row('a')); assert.equal(h.elements.weeklyfinds.attributes['aria-live'],'polite');
h.windowHandlers.pageshow(); assert.equal(h.elements.weeklyfinds.attributes['aria-live'],'off');
for (const options of [{}, {switches:{weekly_finds:false,visit_streak:false}, stored:{
  'wp.visitStreak':JSON.stringify({day:visitDay,count:3}),
  'wp.weeklyFinds':JSON.stringify({week:'2026-10-12',finds:[{key:'a',cents:8600}]})
}}]) {
  h=harness(options); assert.equal(h.line(),''); assert.equal(h.elements.engagementline,undefined);
  assert.ok(!h.elements.content.innerHTML.includes('engagementline'));
}
// Weekly tally: only Get deal, exact cents, dedupe across reloads; no private analytics.
h=harness(); h.click(row('a'),false); assert.equal(h.storage.has('wp.weeklyFinds'),false);
h.click(row('a')); h.click(row('a'));
assert.equal(h.finds(),"You've found $10.25 in savings this week");
assert.ok(!h.finds().includes('saved'));
let saved=JSON.parse(h.storage.get('wp.weeklyFinds'));
assert.equal(saved.week,'2026-10-12'); assert.equal(saved.finds.length,1);
assert.ok(h.events.every(e=>e.name==='deal_click' && Object.keys(e.params).sort().join(',')==='category,store'));
h=harness({stored:Object.fromEntries(h.storage)}); h.click(row('a'));
assert.equal(JSON.parse(h.storage.get('wp.weeklyFinds')).finds.length,1);
h.ctx.recordFind(row('b',{abs_savings:2.10})); assert.ok(h.finds().includes('$12.35'));
for (const abs_savings of [null, -1, 0, 'bad', Infinity]) h.ctx.recordFind(row('invalid',{abs_savings}));
assert.equal(h.ctx.engagement.finds.length,2);
h=harness(); h.ctx.recordFind(row('small',{abs_savings:9.99}));
assert.equal(h.finds(),''); h.ctx.recordFind(row('penny',{abs_savings:0.01}));
assert.ok(h.finds().includes('$10.00'));
const SUNDAY=Date.parse('2026-10-18T23:59:00-04:00'), MONDAY=SUNDAY+120000;
h=harness({now:SUNDAY}); h.ctx.recordFind(row('a')); h.time(MONDAY);
h.windowHandlers.pageshow(); assert.equal(h.finds(),'');
h.ctx.recordFind(row('a')); assert.equal(h.ctx.engagement.finds.length,1);
assert.equal(JSON.parse(h.storage.get('wp.weeklyFinds')).week,'2026-10-19');
h=harness({stored:{'wp.weeklyFinds':JSON.stringify({week:'2026-10-05',finds:[{key:'old',cents:100000}]})}});
assert.equal(h.finds(),'');
for (const broken of ['bad', '{}', 'null']) {
  h=harness({stored:{'wp.weeklyFinds':broken,'wp.visitStreak':broken}});
  assert.equal(h.finds(),'');
  assert.equal(h.ctx.engagement.streak,1);
}
// Local calendar streak: same-day reload, consecutive day, missed day, DST, UTC boundary.
const day=h.ctx.localDay(new Date(NOW));
for (const [previous,count,expected] of [[day,4,4],[day-1,4,5],[day-2,4,1],[day+1,4,1]]) {
  h=harness({stored:{'wp.visitStreak':JSON.stringify({day:previous,count})}});
  assert.equal(h.ctx.engagement.streak,expected);
  assert.equal(h.streak(),expected>1 ? `🔥 ${expected}-day streak` : '');
}
h=harness({now:Date.parse('2026-11-01T23:30:00-05:00')});
const dstDay=h.ctx.localDay(new Date('2026-11-01T23:30:00-05:00'));
h=harness({now:Date.parse('2026-11-02T00:30:00-05:00'), stored:{'wp.visitStreak':JSON.stringify({day:dstDay,count:2})}});
assert.equal(h.ctx.engagement.streak,3);
h=harness({now:Date.parse('2026-10-12T02:00:00Z')}); // still Sunday locally
assert.equal(h.ctx.mondayKey(h.ctx.Date.now()),'2026-10-05');
// Real hot signals, exact boundaries, accessible explanations, no dead/locked flames.
h=harness();
const cold=row('cold',{pct_at_checkout:59,badge:'history-checked',first_seen_at:new Date(NOW-7200000).toISOString()});
assert.equal(h.ctx.hotSignals(cold).length,0); assert.equal(h.ctx.hotHtml(cold),'');
for (const changes of [{pct_at_checkout:60},{badge:'corroborated'},{first_seen_at:new Date(NOW-7199999).toISOString()}]) {
  assert.equal(h.ctx.hotSignals({...cold,...changes}).length,1);
  assert.equal(h.ctx.hotHtml({...cold,...changes}),'');
  assert.ok(!h.ctx.cardHtml({...cold,...changes}).includes('hotmeter'));
}
for (const changes of [
  {pct_at_checkout:60,badge:'corroborated'},
  {pct_at_checkout:60,first_seen_at:new Date(NOW-7199999).toISOString()},
  {badge:'corroborated',first_seen_at:new Date(NOW-7199999).toISOString()}
]) {
  const deal = {...cold,...changes}, signals = h.ctx.hotSignals(deal);
  assert.equal(signals.length,2);
  assert.ok(h.ctx.hotHtml(deal).includes('<span aria-hidden="true">🔥🔥</span>'));
  assert.ok(h.ctx.hotHtml(deal).includes('aria-label="Hot: '+signals.join(', ')+'"'));
}
assert.equal(h.ctx.hotSignals(row('a')).length,3);
assert.equal(h.ctx.hotSignals(row('future',{pct_at_checkout:0,badge:'',first_seen_at:new Date(NOW+1).toISOString()})).length,0);
assert.equal(h.ctx.hotHtml(row('dead',{status:'dead'})),'');
assert.equal(h.ctx.hotHtml(row('locked',{locked:true})),'');
const hot=h.ctx.cardHtml(row('a'));
assert.ok(hot.includes('aria-label="Hot: 70% off, confirmed by 2 sources, first seen less than 2 hours ago"'));
assert.ok(hot.includes('🔥🔥🔥'));
// Surprise never leaves the first 50 of the current wall, including its floor split.
const rows=Array.from({length:80},(_,i)=>row(String(i),{pct_at_checkout:i<20 ? 50 : 70}));
h=harness({deals:rows,shown:10,reduced:true});
h.random(0); h.surprise(); assert.equal(h.elements['20'].scroll.behavior,'auto');
h.random(0.999999); h.surprise(); assert.equal(h.elements['69'].focused.preventScroll,true);
assert.equal(h.ctx.state.shown,60); assert.equal(h.renders(),2);
assert.equal(h.elements['69'].classList.contains('flash'),true); h.timers();
assert.equal(h.elements['69'].classList.contains('flash'),false);
assert.ok(h.events.every(e=>e.name==='surprise_click' && e.params===undefined));
h.wall(rows.slice(3,7)); h.paint(); h.random(0.999); h.surprise(); assert.ok(h.elements['6'].scroll);
h=harness({deals:[row('dead',{status:'dead'}),row('locked',{locked:true})]});
h.surprise(); assert.equal(h.events.length,0); assert.equal(h.elements.surprise.disabled,true);
// Every switch is independent, including the legacy since-visit replacement.
for (const name of Object.keys(enabled)) {
  h=harness({switches:{[name]:false},stored:{'wp.lastVisit':String(NOW-7200000),
    'wp.weeklyFinds':JSON.stringify({week:'2026-10-12',finds:[{key:'a',cents:1000}]}),
    'wp.visitStreak':JSON.stringify({day:day-1,count:2})}});
  if(name==='new_since_visit') {
    assert.equal(h.ctx.newSinceVisit(row('a')),false); assert.ok(!h.elements.tagline.textContent.includes('new since'));
    const before=h.storage.get('wp.lastVisit'); h.windowHandlers.pagehide(); assert.equal(h.storage.get('wp.lastVisit'),before);
  }
  if(name==='weekly_finds') {h.ctx.recordFind(row('b')); assert.equal(h.finds(),''); assert.equal(h.line(),'🔥 3-day streak');}
  if(name==='visit_streak') {assert.equal(h.streak(),''); assert.equal(h.line(),"You've found $10.00 in savings this week");}
  if(name==='surprise_me') {h.surprise(); assert.equal(h.events.length,0); assert.equal(h.elements.surprise.hidden,true);}
  if(name==='hot_meter') assert.equal(h.ctx.hotHtml(row('a')),'');
}
const off=Object.fromEntries(Object.keys(enabled).map(k=>[k,false]));
h=harness({switches:off}); h.click(row('a')); h.surprise(); h.windowHandlers.pagehide();
assert.equal(h.storage.size,0); assert.ok(h.events.every(e=>e.name==='deal_click'));
for (const throwing of ['read','write']) {
  h=harness({throwing,stored:{'wp.lastVisit':String(NOW-7200000)}});
  assert.equal(h.ctx.engagement.storage,false);
  assert.equal(h.finds(),''); assert.equal(h.streak(),'');
  assert.ok(!h.elements.tagline.textContent.includes('new since')); h.click(row('a')); h.windowHandlers.pagehide();
  h.surprise(); assert.equal(h.events.at(-1).name,'surprise_click'); // non-storage features still work
  assert.equal(h.elements.a.scroll.behavior,'smooth');
}
console.log('engagement logic passed');
