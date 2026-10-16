// Run the published functions and click handler offline with a fixed local clock.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
process.env.TZ = 'America/New_York';
const home = fs.readFileSync(process.argv[2], 'utf8');
const enabled = {new_since_visit:true, weekly_finds:true, visit_streak:true, surprise_me:true, hot_meter:true};
const NOW = Date.parse('2026-10-12T12:00:00-04:00'); // Monday, local time
const row = (key, changes = {}) => ({key, status:'alive', item:'Item', store:'Store', category:'other',
  final_price:20, conditions:[],
  url:'https://store.test/item', pct_at_checkout:70, abs_savings:10.25, badge:'corroborated',
  credits:[{name:'One',url:'https://one.test/post'}, {name:'Two',url:'https://two.test/post'}],
  first_seen_at:new Date(NOW-3600000).toISOString(), ...changes});
function fragment(start, end) {
  const a = home.indexOf(start), b = home.indexOf(end, a);
  assert.ok(a >= 0 && b > a, start);
  return home.slice(a,b);
}
function harness({stored={}, throwing='', switches={}, now=NOW, deals=[row('a')], reduced=false, shown=60,
                  savedEnabled=false, savedLimit=100} = {}) {
  let time = now, random = 0, wall = deals, timers = [], renders = 0;
  const storage = new Map(Object.entries(stored)), events = [], toasts = [], handlers = {}, windowHandlers = {};
  function listen(list,type,cb) {
    const previous=list[type];
    list[type]=previous ? (...args)=>{previous(...args); cb(...args);} : cb;
  }
  const makeClass = () => {
    const names = new Set();
    return {add:k=>names.add(k), remove:k=>names.delete(k), contains:k=>names.has(k),
      toggle(k,on) { if (on) names.add(k); else names.delete(k); }};
  };
  const elements = {};
  const makeElement = () => ({textContent:'', hidden:false, classList:makeClass(), listeners:{}, attributes:{},
    get innerHTML() {return this.html || '';},
    set innerHTML(html) {this.html=html; this.textContent=html.replace(/<[^>]*>/g,'');},
    addEventListener(type, cb) {this.listeners[type] = cb;},
    setAttribute(k,v) {this.attributes[k]=v;}, removeAttribute(k) {delete this.attributes[k];}, focus() {this.focused=true;}});
  for (const id of ['tagline','tagsince','surprise','content','savedview','savedtools','savedall','savedclear','savedstatus',
                    'storesel','sortsel','minpct','q','clearsearch','searchcount','filtertoggle','filterfields']) elements[id] = makeElement();
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
  const ctx = {Date:FixedDate, Math:Object.create(Math), loadedOnce:true,
    state:{deals, meta:{engagement:{...enabled,saved_deals:savedEnabled,...switches}, support:{enabled:true},
      saved_deals_limit:savedLimit, floors:{min_pct:60}}, shown, newKeys:{}}, PAGE_CARDS:60,
    document:{hidden:false, getElementById:id=>elements[id], addEventListener:(type,cb)=>listen(handlers,type,cb)},
    window:{wpSupport:{refresh(){}}, wpTrack:(name,params)=>events.push({name,params}), addEventListener:(type,cb)=>listen(windowHandlers,type,cb)},
    localStorage:{getItem(k) {if (throwing==='read') throw Error('blocked'); return storage.get(k) || null;},
      setItem(k,v) {if (throwing==='write') throw Error('readonly'); storage.set(k,v);},
      removeItem(k) {if (throwing==='write') throw Error('readonly'); storage.delete(k);}},
    ordered:rows=>rows, filtered:()=>wall, isLocked:d=>d.locked===true,
    searchInventory:()=>ctx.state.deals, searchRecoveryHtml:()=>'<section class="search-recovery"></section>',
    cardEls:cards, moreObserver:null, renderSearchCount(){}, paintTicker(){}, updatePaywall(){},
    openNudge:()=>false, openBand:()=>'', panel:{}, reduceMotion:()=>reduced,
    setTimeout:cb=>timers.push(cb), cardImageIndex:0, ageText:()=>'',
    isNew:()=>false, isFreePick:()=>false, lit:()=>false, traceSvg:()=>'', TIER_LABEL:{corroborated:'✓ 2+ sources'},
    lockLabel:()=>'', toast:message=>toasts.push(message), navigator:{clipboard:{writeText:()=>Promise.resolve()}},
    rememberFocus:()=>null, restoreFocus(){}
  };
  ctx.Math.random = () => random;
  vm.createContext(ctx);
  vm.runInContext(fragment('  function esc(s)', '  function sortVal'), ctx);
  vm.runInContext(fragment('  function dealKey(d)', '  function keySet'), ctx);
  vm.runInContext(fragment('  // Browser-only visit state.', '  function renderMeta()'), ctx);
  vm.runInContext(fragment('  function storeHue(s)', '  var traceFree'), ctx);
  vm.runInContext(fragment('  function goneText(d)', '  function lockedHtml(d)'), ctx);
  vm.runInContext(fragment('  function lockedHtml(d)', '  function cardHtml(d)'), ctx);
  vm.runInContext(fragment('  function cardHtml(d)', '  // ---- the deal ticker ----'), ctx);
  vm.runInContext(fragment('  function gridHtml(deals)', '  function showMore()'), ctx);
  const renderWall = ctx.render;
  ctx.render = () => {renders++; renderWall(); cards();};
  const contentStart = home.lastIndexOf('  document.getElementById("content").addEventListener("click"');
  const contentEnd = home.indexOf('  // The ticker:', contentStart);
  assert.ok(contentStart >= 0 && contentEnd > contentStart);
  vm.runInContext(home.slice(contentStart,contentEnd), ctx);
  ctx.initEngagement();
  ctx.reconcileSaved({deals});
  function paint() {elements.tagline.textContent='verified deals · 1 live deal'; ctx.renderEngagement(); ctx.render();}
  paint();
  function click(d, getDeal=true, save=false) {
    let prevented=0, stopped=0;
    const card = {getAttribute:()=>d.key};
    const go = {closest:()=>card, hasAttribute:()=>false, classList:{contains:k=>getDeal && k==='getdeal'}};
    const button = {getAttribute:()=>d.key};
    elements.content.listeners.click({target:{closest:s=>s.includes('data-save-key') && save ? button
      : s.includes('sponsored') && !save ? go : null},
      preventDefault(){prevented++;}, stopPropagation(){stopped++;}});
    return {prevented,stopped};
  }
  return {ctx, storage, events, toasts, elements, windowHandlers, handlers, paint, click,
    finds:()=>elements.weeklyfinds?.textContent || '', streak:()=>elements.visitstreak?.textContent || '',
    line:()=>elements.engagementline ? ['visitstreak','engagementsep','weeklyfinds']
      .filter(id=>!elements[id].hidden).map(id=>elements[id].textContent).join('') : '',
    time:n=>time=n, random:n=>random=n, wall:rows=>wall=rows,
    surprise:()=>elements.surprise.listeners.click(), timers:()=>{const due=timers; timers=[]; due.forEach(cb=>cb());}, renders:()=>renders};
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
assert.equal(h.elements.tagsince.textContent, ' · 1 new since your last visit');
assert.ok(h.ctx.cardHtml(row('a')).includes('NEW<span class="sr-only"> since your last visit'));
assert.ok(!h.ctx.cardHtml(row('dead',{status:'dead'})).includes('visit-new'));
assert.equal(h.storage.get('wp.lastVisit'),String(NOW-2*3600000));
h.time(NOW+3600000); h.ctx.state.deals.push(row('later'));
h.ctx.initEngagement(); h.paint(); assert.equal(h.ctx.engagement.newCount,1);
h.windowHandlers.pagehide(); assert.equal(h.storage.get('wp.lastVisit'),String(NOW+3600000));
for (const stored of [{}, {'wp.lastVisit':'bad'}, {'wp.lastVisit':String(NOW+1000)}]) {
  h=harness({stored}); assert.equal(h.ctx.engagement.newCount,0);
  assert.ok(!h.elements.tagsince.textContent.includes('new since'));
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
assert.ok(h.events.every(e=>e.name==='deal_click' && Object.keys(e.params).sort().join(',')==='category,method,store' && e.params.method==='home'));
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
    assert.equal(h.ctx.newSinceVisit(row('a')),false); assert.ok(!h.elements.tagsince.textContent.includes('new since'));
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
  assert.ok(!h.elements.tagsince.textContent.includes('new since')); h.click(row('a')); h.windowHandlers.pagehide();
  h.surprise(); assert.equal(h.events.at(-1).name,'surprise_click'); // non-storage features still work
  assert.equal(h.elements.a.scroll.behavior,'smooth');
}
// Saved offers always reconcile to public data; v1 identities migrate without invented prices.
const SAVED_KEY='wp.savedDeals';
const record=deals=>JSON.stringify({version:1,deals});
const watched=deals=>JSON.stringify({version:2,deals});
const identity=(key, changes={})=>({key,label:'Original '+key,store:'Store',...changes});
const shortlist=options=>harness({savedEnabled:true,switches:off,...options});
h=shortlist({deals:[row('path',{deal_path:'/deal/original-name-12345678/'})]});
assert.equal(h.ctx.saveDeal(row('path')),true);
assert.equal(JSON.parse(h.storage.get(SAVED_KEY)).deals[0].path,'/deal/original-name-12345678/');
assert.match(h.ctx.cardHtml(row('path',{item:'Renamed item',deal_path:'/deal/original-name-12345678/'})),
  /<h3 class="title"><a href="\/deal\/original-name-12345678\/">Renamed item<\/a>/);
h=shortlist({deals:[row('a'),row('b'),row('c')]});
assert.equal(h.elements.savedview.textContent,'Saved (0)');
assert.match(h.elements.savedstatus.textContent,/Saved on this device/);
assert.match(h.ctx.cardHtml(row('a')),/<button type="button" class="save-deal"/);
assert.match(h.ctx.cardHtml(row('a')),/aria-pressed="false" aria-label="Save deal: Item"/);
assert.deepEqual(h.click(row('a'),true,true),{prevented:1,stopped:1});
assert.deepEqual(h.events.map(e=>e.name),['deal_save','watch_create']); // Save never falls through to a deal link.
assert.equal(h.ctx.saveDeal(row('a')),false);
assert.equal(h.ctx.saveDeal(row('b')),true);
assert.deepEqual(JSON.parse(h.storage.get(SAVED_KEY)).deals.map(d=>d.key),['b','a']);
const serialized=h.storage.get(SAVED_KEY);
for(const forbidden of ['tag=','https:','photo','codes','credits','abs_savings','regular_price']) assert.ok(!serialized.includes(forbidden));
assert.ok(JSON.parse(serialized).deals.every(d=>Object.keys(d).sort().join(',')==='condition,final_price,key,label,matched_prices,store'));
assert.equal(JSON.parse(serialized).version,2);
assert.ok(JSON.parse(serialized).deals.every(d=>d.final_price===20 && d.condition.length===0));
assert.equal(h.ctx.removeSaved('missing'),false);
const eventCount=h.events.length;
h.elements.savedview.listeners.click();
assert.equal(h.elements.savedview.attributes['aria-pressed'],'true');
assert.equal(h.elements.savedview.textContent,'Saved (2)');
assert.equal(h.elements.content.attributes['data-clarity-mask'],'true');
assert.equal(h.elements.storesel.disabled,true);
assert.equal(h.elements.filtertoggle.disabled,true);
assert.equal(h.elements.filterfields.disabled,true);
h.ctx.setSavedView(true); h.ctx.render(); h.ctx.render();
assert.equal(h.events.length,eventCount+1); // One view event per user opening, never per render.
h.click(row('a')); assert.equal(h.events.at(-1).name,'saved_deal_click');
assert.equal(h.events.filter(e=>e.name==='saved_deal_click').length,1);
assert.equal(h.events.filter(e=>e.name==='deal_click').length,1);
h.click(row('a'),true,true);
assert.equal(h.elements.savedview.textContent,'Saved (1)');
assert.equal(h.ctx.isSaved('a'),false);
h.elements.savedclear.listeners.click();
assert.equal(h.elements.savedview.textContent,'Saved (0)');
assert.match(h.elements.content.innerHTML,/Save a deal to revisit/);
assert.equal(h.ctx.clearSaved(),false);
assert.deepEqual(JSON.parse(h.storage.get(SAVED_KEY)),{version:2,deals:[]});
assert.equal(h.events.filter(e=>e.name==='deal_save' && e.params.method==='clear').length,1);
assert.ok(h.events.filter(e=>e.name!=='deal_click').every(e=>Object.keys(e.params).sort().join(',')===
  (e.name.startsWith('watch_') ? 'method' : 'count,method')));
h.elements.savedall.listeners.click();
assert.equal(h.elements.content.attributes['data-clarity-unmask'],'true');
assert.equal(h.elements.content.attributes['data-clarity-mask'],undefined);
assert.equal(h.elements.savedview.focused,true);
// Persistence preserves newest-first order and optional safe internal paths; current names/prices win.
h=shortlist({stored:{[SAVED_KEY]:record([identity('b',{path:'/deal/original-b-123abcde/'}),identity('a')])},
  deals:[row('a',{item:'Renamed item',final_price:17.25,regular_price:50}),row('b',{final_price:31})]});
h.ctx.setSavedView(true);
let html=h.elements.content.innerHTML;
assert.ok(html.indexOf('data-key="b"')<html.indexOf('data-key="a"'));
assert.match(html,/Renamed item/); assert.match(html,/\$17\.25/); assert.ok(!html.includes('Original a'));
assert.equal(JSON.parse(h.storage.get(SAVED_KEY)).deals[0].path,'/deal/original-b-123abcde/');
h.ctx.state.deals=[row('a',{item:'Renamed again',final_price:9.99,regular_price:40}),row('b')];
h.ctx.reconcileSaved({deals:h.ctx.state.deals}); h.ctx.render();
assert.match(h.elements.content.innerHTML,/\$9\.99/); assert.ok(!h.elements.content.innerHTML.includes('$17.25'));
// Cap/rotation/absent pages are unknown. A confirmed gone match alone says it ended.
for(const current of [null,row('a',{status:'dead',gone:false}),row('a',{locked:true,item:'LOCKED SECRET',final_price:999}),
                      row('a',{status:'dead',gone:true})]) {
  h=shortlist({stored:{[SAVED_KEY]:record([identity('a',{final_price:88,url:'https://amazon.com/item?tag=old-20',
    codes:['SECRET'],image_url:'https://photos.test/a'})])},deals:current ? [current] : []});
  h.ctx.setSavedView(true); html=h.elements.content.innerHTML;
  assert.ok(!/Get deal|href=|\$|SECRET|photos\.test|amazon\.com/.test(html));
  assert.ok(html.includes(current?.gone===true ? 'This deal ended.' : 'Not currently listed; availability unknown'));
  assert.deepEqual(Object.keys(JSON.parse(h.storage.get(SAVED_KEY)).deals[0]).sort(),
    ['condition','final_price','key','label','matched_prices','store']);
  assert.equal(h.ctx.savedDeals.entries[0].final_price,null);
  const n=h.events.length; h.ctx.savedOutbound(current || row('a')); assert.equal(h.events.length,n);
}
// A member-only list cannot satisfy a saved public identity or make a locked stub saveable.
h=shortlist({deals:[row('a',{locked:true})]});
assert.equal(h.ctx.saveDeal(row('a',{locked:false,final_price:1})),false);
assert.ok(!h.ctx.cardHtml(row('a',{locked:true})).includes('data-save-key'));
h.ctx.state.deals=[row('private')];
assert.equal(h.ctx.saveDeal(row('private')),false);
// Limits reject new saves without evicting an existing one, and restored duplicates never reorder.
h=shortlist({savedLimit:2,deals:[row('a'),row('b'),row('c')]});
h.ctx.saveDeal(row('a')); h.ctx.saveDeal(row('b'));
assert.equal(h.ctx.saveDeal(row('c')),false); assert.equal(h.events.length,4);
assert.deepEqual(JSON.parse(h.storage.get(SAVED_KEY)).deals.map(d=>d.key),['b','a']);
h=shortlist({savedLimit:2,stored:{[SAVED_KEY]:record([identity('a'),identity('a',{label:'Duplicate'}),identity('b'),identity('c')])}});
assert.deepEqual(JSON.parse(h.storage.get(SAVED_KEY)).deals.map(d=>d.key),['a','b']);
assert.equal(h.ctx.savedDeals.entries[0].label,'Original a');
for(const raw of ['broken','[]','null','{}',JSON.stringify({version:3,deals:[identity('a')]}),
                 JSON.stringify({version:1,deals:{key:'a'}}),'x'.repeat(2000001)]) {
  h=shortlist({stored:{[SAVED_KEY]:raw}}); assert.equal(h.ctx.savedDeals.entries.length,0);
  assert.ok(h.elements.content.innerHTML.includes('data-key="a"')); // Corruption never breaks the wall.
}
for(const path of ['https://evil.test/deal/a/','//evil.test/deal/a/','/deal/../a/','/deal/%2e%2e/a/',
                  '/deal/a/?tag=old-20','/deal/a/#code','/deal/a/\\evil','/deal/a/\n','javascript:alert(1)','/stores/amazon/']) {
  h=shortlist({stored:{[SAVED_KEY]:record([identity('a',{path})])}});
  assert.equal(h.ctx.savedDeals.entries[0].path,undefined);
}
for(const changes of [{key:''},{key:{}},{key:'a'.repeat(257)},{label:''},{label:4},{label:'x'.repeat(201)},
                      {label:'bad\u0000label'},{store:[]},{store:'bad\nstore'}]) {
  h=shortlist({stored:{[SAVED_KEY]:record([identity('a',changes)])}}); assert.equal(h.ctx.savedDeals.entries.length,0);
}
h=shortlist({stored:{[SAVED_KEY]:record([identity('absent',{label:'<img src=x onerror="boom">',store:'A & B'})])}});
h.ctx.setSavedView(true); assert.match(h.elements.content.innerHTML,/&lt;img/);
assert.ok(!h.elements.content.innerHTML.includes('<img')); assert.match(h.elements.content.innerHTML,/A &amp; B/);
// Quota/read denial preserves an in-memory shortlist and explains its tab-only lifetime.
for(const throwing of ['read','write']) {
  h=shortlist({throwing}); h.click(row('a'),true,true);
  assert.equal(h.ctx.savedDeals.entries.length,1); assert.equal(h.ctx.savedDeals.persistent,false);
  assert.match(h.elements.savedstatus.textContent,/this tab only.*will not persist/);
  h.windowHandlers.storage({key:SAVED_KEY,newValue:record([identity('other')])});
  assert.equal(h.ctx.savedDeals.entries[0].key,'a');
  h.ctx.setSavedView(true); assert.match(h.elements.content.innerHTML,/Get deal/);
  h.elements.savedclear.listeners.click(); assert.equal(h.ctx.savedDeals.entries.length,0);
}
h=shortlist({throwing:'write',stored:{[SAVED_KEY]:record([identity('b')])},deals:[row('a'),row('b')]});
h.click(row('a'),true,true); assert.equal(h.ctx.savedDeals.entries.length,2);
assert.deepEqual(JSON.parse(h.storage.get(SAVED_KEY)).deals.map(d=>d.key),['b']);
// Cross-tab saves/removals/clear and malformed changes update count/view without analytics or write loops.
h=shortlist(); h.ctx.setSavedView(true);
let n=h.events.length;
h.windowHandlers.storage({key:SAVED_KEY,newValue:record([identity('a')])});
assert.equal(h.elements.savedview.textContent,'Saved (1)'); assert.match(h.elements.content.innerHTML,/Get deal/);
assert.equal(h.events.length,n); assert.equal(h.storage.has(SAVED_KEY),false);
h.windowHandlers.storage({key:'other',newValue:null}); assert.equal(h.ctx.savedDeals.entries.length,1);
h.windowHandlers.storage({key:SAVED_KEY,newValue:'corrupt'}); assert.equal(h.ctx.savedDeals.entries.length,0);
h.windowHandlers.storage({key:SAVED_KEY,newValue:record([identity('a')])});
h.windowHandlers.storage({key:null,newValue:null}); assert.equal(h.ctx.savedDeals.entries.length,0);
assert.equal(h.events.length,n);
// Switch-off never reads or mutates a stored list, and ordinary outbound/source markup stays intact.
h=harness({stored:{[SAVED_KEY]:record([identity('a')])},switches:off});
assert.equal(h.ctx.saveDeal(row('a')),false); assert.equal(h.ctx.clearSaved(),false);
assert.ok(!h.ctx.cardHtml(row('a')).includes('data-save-key'));
assert.equal(h.ctx.savedDeals.started,false);
h=shortlist(); html=h.ctx.cardHtml(row('a'));
assert.match(html,/<a class="getdeal" href="https:\/\/store.test\/item" target="_blank" rel="sponsored noopener"/);
assert.match(html,/via <a href="https:\/\/one.test\/post" rel="noopener">One<\/a>/);
// Migration keeps v1 saves usable, with no baseline or automatic watch match.
h=shortlist({stored:{[SAVED_KEY]:record([identity('a',{final_price:100,condition:[],target_price:90,matched_prices:[20]})])}});
let entry=JSON.parse(h.storage.get(SAVED_KEY)).deals[0];
assert.equal(entry.final_price,null); assert.equal(entry.condition,null);
assert.equal(entry.target_price,undefined); assert.deepEqual(entry.matched_prices,[]);
h.ctx.setSavedView(true);
assert.match(h.elements.content.innerHTML,/No baseline yet/);
assert.ok(!h.elements.content.innerHTML.includes('Price dropped'));
assert.equal(h.ctx.savedDeals.matches.size,0); assert.equal(h.events.filter(e=>e.name.startsWith('watch_')).length,0);
// A migrated save can explicitly acquire a target and exact condition, without inventing a baseline.
assert.equal(h.ctx.setWatchTarget('a',20),true);
assert.equal(h.ctx.savedDeals.entries[0].final_price,null);
h.ctx.reconcileSaved({deals:[row('a')]}); h.ctx.render(); h.timers();
assert.match(h.elements.content.innerHTML,/Target price reached: now \$20\.00/);
assert.equal(h.toasts.length,1);
// New saves copy only the displayed final price and exact conditions from public data.
h=shortlist({deals:[row('a',{final_price:29.95,conditions:['used','open_box'],regular_price:500,
  image_url:'https://photos.test/a',codes:['PRIVATE'],how:['SECRET']})]});
h.ctx.saveDeal(row('a',{final_price:1,conditions:[]}));
entry=JSON.parse(h.storage.get(SAVED_KEY)).deals[0];
assert.equal(entry.final_price,29.95); assert.deepEqual(entry.condition,['open_box','used']);
assert.equal(entry.target_price,undefined); assert.deepEqual(entry.matched_prices,[]);
assert.ok(!/500|PRIVATE|SECRET|photos\.test|https:/.test(h.storage.get(SAVED_KEY)));
assert.equal(h.toasts.length,0);
assert.ok(!h.ctx.cardHtml(row('a')).includes('Watch price')); // No extra card height on the ordinary wall.
// Every price/availability/identity/condition guard, with exact matching and no fuzzy titles.
const watchEntry=(key='a',changes={})=>identity(key,{final_price:20,condition:[],matched_prices:[],...changes});
for (const [changes,expected] of [[{final_price:19.99},true],[{final_price:20},false],[{final_price:21},false],
  [{final_price:0},true],[{final_price:null},false],[{final_price:'1'},false],[{final_price:NaN},false],
  [{final_price:Infinity},false],[{final_price:-1},false],[{final_price:19.999},false],
  [{status:'dead',gone:true,final_price:1},false],[{status:'dead',gone:false,final_price:1},false],
  [{status:'unknown',final_price:1},false],[{gone:true,final_price:1},false],[{alive:false,final_price:1},false],
  [{locked:true,final_price:1},false],[{key:'different',final_price:1},false],
  [{key:null,dedupe_key:'a',final_price:1},false],
  [{conditions:['used'],final_price:1},false],[{conditions:['new_customer'],final_price:1},false],
  [{conditions:null,final_price:1},false],[{conditions:'new',final_price:1},false]]) {
  h=shortlist({stored:{[SAVED_KEY]:watched([watchEntry()])},deals:[]});
  h.ctx.reconcileSaved({deals:[row('a',changes)]});
  assert.equal(h.ctx.savedDeals.matches.size,expected ? 1 : 0,JSON.stringify(changes));
  assert.equal(h.events.filter(e=>e.name==='watch_match').length,expected ? 1 : 0);
  h.timers(); assert.equal(h.toasts.length,expected ? 1 : 0);
}
h=shortlist({stored:{[SAVED_KEY]:watched([watchEntry()])},deals:[]});
assert.equal(h.ctx.savedDeals.matches.size,0); h.timers(); assert.equal(h.toasts.length,0);
h=shortlist({stored:{[SAVED_KEY]:watched([watchEntry('a',{condition:['open_box','used']})])},
  deals:[row('a',{conditions:['used','open_box'],final_price:10})]});
assert.equal(h.ctx.savedDeals.matches.size,1); // Condition order does not change the offer.
// Dropped entries lead the Saved view; badge and readable text also describe matches to screen readers.
h=shortlist({stored:{[SAVED_KEY]:watched([watchEntry('b'),watchEntry('a')])},
  deals:[row('a',{final_price:17.25}),row('b')]});
assert.match(h.elements.savedview.innerHTML,/<span class="watch-count" aria-hidden="true">1<\/span>/);
assert.match(h.elements.savedview.innerHTML,/<span class="sr-only">, 1 price watch match<\/span>/);
h.ctx.setSavedView(true); html=h.elements.content.innerHTML;
assert.ok(html.indexOf('data-key="a"')<html.indexOf('data-key="b"'));
assert.match(html,/<p class="watch-drop">Price dropped since you saved: was \$20\.00, now \$17\.25<\/p>/);
assert.ok(!/%|regular/.test(html.match(/<p class="watch-drop">([^<]+)<\/p>/)[1]));
assert.match(html,/<summary>Watch price<\/summary>/);
assert.match(html,/type="number" name="target" min="0" step="0.01" required inputmode="decimal"/);
h.timers(); assert.equal(h.toasts.length,1); assert.match(h.toasts[0],/Checked when you visit/);
h.click(row('a',{final_price:17.25}));
assert.deepEqual(JSON.parse(JSON.stringify(h.events.at(-1))),{name:'watch_deal_click',params:{method:'outbound'}});
h.ctx.setSavedView(false); h.click(row('a',{final_price:17.25}));
assert.equal(h.events.filter(e=>e.name==='watch_deal_click').length,1);
// A repeated payload, rerender, reload, or rebound to an already observed price never reannounces it.
const firstStorage=Object.fromEntries(h.storage);
h.ctx.reconcileSaved({deals:[row('a',{final_price:17.25}),row('b')]}); h.paint(); h.timers();
assert.equal(h.events.filter(e=>e.name==='watch_match').length,1); assert.equal(h.toasts.length,1);
h=shortlist({stored:firstStorage,deals:[row('a',{final_price:17.25}),row('b')]}); h.timers();
assert.equal(h.ctx.savedDeals.matches.size,1); assert.equal(h.events.length,0); assert.equal(h.toasts.length,0);
h.ctx.reconcileSaved({deals:[row('a',{final_price:16}),row('b')]}); h.timers();
assert.equal(h.events.filter(e=>e.name==='watch_match').length,1); assert.equal(h.toasts.length,1);
h.ctx.reconcileSaved({deals:[row('a',{final_price:17.25}),row('b')]}); h.timers();
assert.equal(h.events.filter(e=>e.name==='watch_match').length,1); assert.equal(h.toasts.length,1);
// A target is inclusive, can be $0, and cannot cross on unknown prices or another condition.
h=shortlist({stored:{[SAVED_KEY]:watched([watchEntry('a',{final_price:null,condition:[],target_price:10})])},
  deals:[row('a',{final_price:10.01})]});
assert.equal(h.ctx.savedDeals.matches.size,0);
for(const price of [10,10,11,10,9]) {h.ctx.reconcileSaved({deals:[row('a',{final_price:price})]}); h.timers();}
assert.deepEqual(h.events.map(e=>[e.name,e.params.method]),[['watch_match','target'],['watch_match','target']]);
assert.equal(h.toasts.length,2);
assert.equal(h.ctx.setWatchTarget('a',0),true);
assert.equal(h.ctx.setWatchTarget('a',0),false);
h.ctx.reconcileSaved({deals:[row('a',{final_price:0})]}); h.timers();
assert.equal(h.events.at(-1).name,'watch_match');
assert.equal(h.ctx.setWatchTarget('a',null),true);
assert.equal(h.ctx.savedDeals.entries[0].target_price,undefined);
for(const target of [-1,'5',NaN,Infinity,1.001]) assert.equal(h.ctx.setWatchTarget('a',target),false);
h.ctx.reconcileSaved({deals:[row('a',{conditions:['used'],final_price:0})]});
assert.equal(h.ctx.setWatchTarget('a',5),false);
assert.equal(h.ctx.savedDeals.matches.size,0);
// A target above the baseline can match, but must never falsely claim that the price dropped.
h=shortlist({stored:{[SAVED_KEY]:watched([watchEntry('a',{target_price:25})])},deals:[row('a',{final_price:22})]});
h.ctx.setSavedView(true);
assert.match(h.elements.content.innerHTML,/Target price reached: now \$22\.00 \(target \$25\.00\)/);
assert.ok(!h.elements.content.innerHTML.includes('Price dropped since you saved'));
// Cross-tab changes recompute badges quietly, and persisted observations suppress duplicate events.
const tab1=shortlist({deals:[row('a')]}); tab1.ctx.saveDeal(row('a')); tab1.ctx.setWatchTarget('a',15);
const tab2=shortlist({stored:Object.fromEntries(tab1.storage),deals:[row('a')]}); tab2.ctx.setSavedView(true);
tab1.ctx.reconcileSaved({deals:[row('a',{final_price:15})]}); tab1.timers();
tab2.ctx.savedDeals.publicDeals=[row('a',{final_price:15})];
let tab2Events=tab2.events.length, tab2Record=tab2.storage.get(SAVED_KEY);
tab2.windowHandlers.storage({key:SAVED_KEY,newValue:tab1.storage.get(SAVED_KEY)});
assert.equal(tab2.ctx.savedDeals.matches.size,1);
assert.match(tab2.elements.content.innerHTML,/now \$15\.00/);
assert.equal(tab2.events.length,tab2Events); assert.equal(tab2.storage.get(SAVED_KEY),tab2Record);
tab2.storage.set(SAVED_KEY,tab1.storage.get(SAVED_KEY));
tab2.ctx.reconcileSaved({deals:[row('a',{final_price:15})]}); tab2.timers();
assert.equal(tab2.events.length,tab2Events); assert.equal(tab2.toasts.length,0);
tab2.windowHandlers.storage({key:null,newValue:null});
assert.equal(tab2.ctx.savedDeals.matches.size,0); assert.ok(!tab2.elements.savedview.innerHTML.includes('watch-count'));
// A quota failure after observing a drop retains prices, targets and deduplication in this tab.
h=shortlist({deals:[row('a')]}); h.ctx.saveDeal(row('a')); h.ctx.setWatchTarget('a',10);
h.ctx.localStorage.setItem=()=>{throw Error('quota');};
h.ctx.reconcileSaved({deals:[row('a',{final_price:10})]}); h.timers();
assert.equal(h.ctx.savedDeals.persistent,false); assert.equal(h.ctx.savedDeals.matches.size,1);
assert.equal(h.ctx.savedDeals.entries[0].target_price,10);
h.ctx.reconcileSaved({deals:[row('a',{final_price:10})]}); h.timers();
assert.equal(h.events.filter(e=>e.name==='watch_match').length,1); assert.equal(h.toasts.length,1);
assert.match(h.elements.savedstatus.textContent,/this tab only.*will not persist/);
// Target form validation and submit do not fall through to navigation or expose a price in analytics.
h=shortlist({deals:[row('a')]}); h.ctx.saveDeal(row('a')); h.ctx.setSavedView(true);
h.ctx.cardEls=()=>[];
const input={value:'7.125'}, feedback={textContent:''};
const form={getAttribute:()=> 'a',querySelector:s=>s.startsWith('input') ? input : feedback,closest(){return this;}};
let submitted=0;
const submit=()=>h.elements.content.listeners.submit({target:form,preventDefault(){submitted++;}});
submit(); assert.match(feedback.textContent,/up to two decimal places/);
input.value='7.12'; submit(); submit();
assert.equal(submitted,3); assert.equal(h.ctx.savedDeals.entries[0].target_price,7.12);
assert.equal(h.events.filter(e=>e.name==='watch_create' && e.params.method==='target').length,1);
assert.ok(h.events.filter(e=>e.name.startsWith('watch_')).every(e=>Object.keys(e.params).join(',')==='method'));
// A public update retains the active target draft and native expanded/focus state.
h=shortlist();
const focusedInput={name:'target',value:'12.34',focus(options){this.focused=options;}};
const summary={focus(options){this.focused=options;}};
const details={open:true,closest:()=>({getAttribute:()=> 'a'}),querySelector:s=>s==='input' ? focusedInput : summary};
focusedInput.closest=()=>details;
h.ctx.document.activeElement=focusedInput;
const editor=h.ctx.savedWatchEditor();
assert.equal(editor.key,'a'); assert.equal(editor.value,'12.34'); assert.equal(editor.open,true);
details.open=false; focusedInput.value='20';
h.ctx.cardEls=()=>[{getAttribute:()=> 'a',querySelector:()=>details}];
h.ctx.restoreSavedWatchEditor(editor);
assert.equal(details.open,true); assert.equal(focusedInput.value,'12.34'); assert.equal(focusedInput.focused.preventScroll,true);
focusedInput.disabled=true;
h.ctx.restoreSavedWatchEditor(editor); assert.equal(summary.focused.preventScroll,true);
// Visitors without saves get no badge, watch UI, storage record or watch toast on their first load.
h=shortlist(); h.timers();
assert.equal(h.elements.savedview.hidden,true); assert.equal(h.storage.has(SAVED_KEY),false);
assert.ok(!h.elements.content.innerHTML.includes('watch-price')); assert.equal(h.toasts.length,0);
assert.equal(h.events.length,0);
(async()=>{
  // An HTTP error, invalid payload or JSON failure cannot replace the last good offers.
  h=shortlist({deals:[row('a',{final_price:17.25})]});
  h.ctx.saveDeal(row('a')); h.ctx.setSavedView(true);
  h.ctx.clock={tag:''}; h.ctx.maybeReloadForNewVersion=()=>{};
  h.ctx.dataKey=meta=>meta.generated_at || '';
  vm.runInContext(fragment('  function fetchPublish()', '  function isNewPublish('),h.ctx);
  const response=(body,ok=true)=>({ok,headers:{get:()=>''},json:async()=>body});
  for(const bad of [response({meta:{},deals:[]},false), response({}), response({meta:{},deals:{}}),
                    {ok:true,headers:{get:()=>''},json:async()=>{throw Error('bad JSON');}}]) {
    h.ctx.fetch=async()=>bad;
    await assert.rejects(h.ctx.fetchPublish()); h.ctx.render();
    assert.match(h.elements.content.innerHTML,/\$17\.25/);
    assert.ok(!h.elements.content.innerHTML.includes('availability unknown'));
  }
  h.ctx.fetch=async()=>response({meta:h.ctx.state.meta,deals:[]});
  const pub=await h.ctx.fetchPublish();
  h.ctx.reconcileSaved(pub); h.ctx.render();
  assert.match(h.elements.content.innerHTML,/Not currently listed; availability unknown/);
  assert.ok(!h.elements.content.innerHTML.includes('$17.25'));
  // Successful loads of the same publish still check a newly created target.
  h=shortlist({deals:[row('a')]}); h.ctx.saveDeal(row('a')); h.ctx.setSavedView(true);
  assert.equal(h.ctx.setWatchTarget('a',20),true);
  assert.equal(h.events.filter(e=>e.name==='watch_match').length,0); // Editing alone is not a new observation.
  h.ctx.isNewPublish=()=>false;
  vm.runInContext(fragment('  function merge(pub)', '  // While a drop waits'),h.ctx);
  await h.ctx.merge({deals:[row('a')]}); h.timers();
  assert.equal(h.events.filter(e=>e.name==='watch_match').length,1); assert.equal(h.toasts.length,1);
  await h.ctx.merge({deals:[row('a')]}); h.timers();
  assert.equal(h.events.filter(e=>e.name==='watch_match').length,1); assert.equal(h.toasts.length,1);
  // Malformed HTTP/JSON/envelopes never reach price comparison, or replace the last good match.
  h.ctx.clock={tag:''}; h.ctx.maybeReloadForNewVersion=()=>{}; h.ctx.dataKey=()=>'';
  vm.runInContext(fragment('  function fetchPublish()', '  function isNewPublish('),h.ctx);
  for(const bad of [response({meta:{},deals:[row('a',{final_price:1})]},false),response({}),
    response({meta:{},deals:{}}),{ok:true,headers:{get:()=>''},json:async()=>{throw Error('bad JSON');}}]) {
    h.ctx.fetch=async()=>bad;
    await assert.rejects(h.ctx.fetchPublish());
    assert.equal(h.events.filter(e=>e.name==='watch_match').length,1);
    assert.equal(h.ctx.savedDeals.matches.get('a').price,20);
  }
  h.ctx.fetch=async()=>response({meta:h.ctx.state.meta,deals:[row('a',{final_price:'1'})]});
  await h.ctx.merge(await h.ctx.fetchPublish()); h.timers();
  assert.equal(h.ctx.savedDeals.matches.size,0); // goodDeal skipped this odd row; missing never triggers.
  assert.equal(h.events.filter(e=>e.name==='watch_match').length,1);
  // Later quota denial and a failing analytics function cannot stop the local action.
  h=shortlist({deals:[row('a'),row('b')]}); h.ctx.saveDeal(row('a'));
  h.ctx.localStorage.setItem=()=>{throw Error('quota');};
  h.ctx.window.wpTrack=()=>{throw Error('tag down');};
  assert.equal(h.ctx.saveDeal(row('b')),true);
  assert.equal(h.ctx.savedDeals.entries.length,2);
  assert.equal(h.ctx.savedDeals.persistent,false);
  h.ctx.renderSavedControls();
  assert.match(h.elements.savedstatus.textContent,/will not persist/);
  console.log('engagement and saved-deals logic passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
