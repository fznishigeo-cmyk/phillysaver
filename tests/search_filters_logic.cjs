// Exercise the shipped functions and event registrations, without a network or browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const payload = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const snippet = (start, end) => {
  const a = html.indexOf(start), b = html.indexOf(end, a);
  assert.ok(a >= 0 && b > a, start);
  return html.slice(a, b);
};
const handlers = {}, timers = new Map(), elements = {};
let timerID = 0, now = 0, renders = 0, reloads = 0, requests = [], response = payload, fail = false;
function element(id) {
  return elements[id] ||= {value:'', hidden:false, textContent:'', innerHTML:'', options:[],
    addEventListener(type, fn) { handlers[id + ':' + type] = fn; },
    appendChild(opt) {this.options.push(opt);}, remove(i) {this.options.splice(i, 1);},
    focus() {this.focused = true;}, querySelectorAll() {return [];}, scrollIntoView(options) {this.scrolled = options;}};
}
element('storesel').options.push({value:''});
const session = new Map(), saved = new Map();
const ctx = {URL, URLSearchParams, Set, state:{deals:payload.deals, catTab:2, shown:600, dataKey:'old'}, PAGE_CARDS:60,
  CATGROUPS:JSON.parse(html.match(/var CATGROUPS = (.*);/)[1]),
  document:{hidden:false, getElementById:element, createElement:()=>({}),
    addEventListener(type, fn) {handlers['document:'+type] = fn;}},
  window:{addEventListener(type, fn) {handlers['window:'+type] = fn;}},
  Date:class extends Date {constructor(){super(now);} static now(){return now;}},
  setTimeout(fn, delay) {timers.set(++timerID, {fn, delay}); return timerID;},
  clearTimeout(id) {timers.delete(id);},
  num:v=>v == null ? null : Number(v), esc:s=>s, isLocked:d=>!!d.locked, noop:()=>{},
  render() {renders++; ctx.renderSearchCount(ctx.filtered());},
  clock:{tag:''}, dataKey:meta=>meta.published_at,
  isNewPublish:pub=>pub.key !== ctx.state.dataKey,
  dealsFor:async pub=>pub.deals,
  landed(pub, deals) {ctx.state.deals = deals; ctx.state.dataKey = pub.key;},
  sessionStorage:{getItem:k=>session.get(k), setItem:(k,v)=>session.set(k,v)},
  location:{search:'', reload(){reloads++;}}, loadedOnce:true, reduceMotion:()=>true,
  SORT_KEY:'wp.sort', lsGet:()=>null, lsSet:(k,v)=>saved.set(k,v),
  fetch:async (url, options)=>{requests.push({url, options}); if(fail) throw Error('offline');
    return {ok:true, headers:{get:()=>''}, json:async()=>response};}
};
vm.createContext(ctx);
for (const [a,b] of [
  ['  function plural(', '  // The open (unlocked'],
  ['  var SITE_VERSION =', '  // Every published deal'],
  ['  function storeKey(', '  function cmp('],
  ['  function resetWall()', '  // Easy sorting:'],
  ['  var sortsel =', '  function refreshStores()'],
  ['  function refreshStores()', '  // ---- publishes:'],
  ['  function fetchPublish()', '  function isNewPublish('],
  ['  function merge(pub)', '  function poll()']
]) vm.runInContext(snippet(a,b),ctx);
const query = value => {element('q').value=value; ctx.updateSearch(); return ctx.filtered();};
const items = rows => Array.from(rows, d=>d.item);
const flush = async()=>{await new Promise(resolve=>setImmediate(resolve));};
const staticWall = html.match(/<div id="content"[^>]*>([\s\S]*?)<\/div>\s*<div id="paywall"/)[1];
const support = payload.meta.support.enabled;
function deferred() {
  let resolve;
  const promise = new Promise(r=>{resolve=r;});
  return {promise, resolve};
}
function loadHarness(next, {staticDeals=true, signedIn=false, memberFails=false, memberPending=null}={}) {
  const els = {}, listeners = {}, intervals = [], events = [], requests = [], notices = [];
  let renders=0, firstAdoptions=0, memberRequests=0;
  function el(id) {
    if (['moremore','totop'].includes(id)) return null;
    return els[id] ||= {value:'', innerHTML:'', textContent:'', hidden:false, disabled:false,
      addEventListener(type, fn) {listeners[id+':'+type]=fn;},
      querySelectorAll:()=>[], appendChild(){}, focus(){},
      querySelector(selector) {return selector === '.card' && /<article class="card/.test(this.innerHTML) ? {} : null;}};
  }
  el('content').innerHTML=staticDeals ? staticWall : '<p class="empty">No qualifying deals right now</p>';
  el('load-status').hidden=true;
  el('cattabs').innerHTML=html.match(/<nav id="cattabs"[^>]*>(.*?)<\/nav>/)[1];
  el('toast').textContent='unrelated notice';
  const c = {next, URLSearchParams, CATGROUPS:ctx.CATGROUPS, PAGE_CARDS:60, AUTO_CARDS:300,
    state:{shown:60, deals:[], teaser:[], meta:{}, dataKey:'', newKeys:{}, dropKeys:{}, seed:0, sortKey:'top', catTab:0},
    clock:{tag:'old-tag', waiting:0, step:900000, phase:0, lastSeed:null}, auth:{checking:false}, moreObserver:null,
    document:{hidden:false, getElementById:el,
      addEventListener(type, fn) {listeners['document:'+type]=fn;}},
    window:{wpTrack:(...args)=>events.push(args), wpSupport:{refresh(){}},
      addEventListener(type, fn) {listeners['window:'+type]=fn;}},
    localStorage:{getItem:()=>null, setItem(){}}, location:{search:''},
    setTimeout:()=>1, clearTimeout(){}, setInterval(fn, delay) {intervals.push({fn,delay});},
    dataKey:m=>m.generated_at || '', dealKey:d=>d.key, keySet:ds=>Object.fromEntries(ds.map(d=>[d.key,true])),
    openKeys:ds=>Object.fromEntries(ds.filter(d=>!d.locked).map(d=>[d.key,true])),
    maybeReloadForNewVersion(){}, syncClock(){}, markIndex:()=>1,
    populateStores(){firstAdoptions++;}, refreshStores(){}, renderMeta(){}, sinceLastVisit(){}, rememberSeen(){},
    selectAmazonDeals(){}, openFromHash(){}, initEngagement(){}, engagementLineHtml:()=>'',
    paintTicker(){renders++;}, renderSearchCount(){}, filtered:()=>c.state.deals, ordered:rows=>rows,
    searchText:()=>el('q').value.trim(),
    isLocked:d=>d.locked===true, openNudge:()=>false,
    updatePaywall(){notices.push(c.auth.checking);}, openAccess:()=>!!c.state.meta.open_access,
    canJoin:()=>false, placeGone:rows=>rows, frontPageView:()=>false, openBand:()=>'', activeSpotlight:()=>[],
    gridHtml:rows=>'<div class="grid">'+rows.map(d=>'<article class="card" data-key="'+d.key+'">'+d.item+'</article>').join('')+'</div>',
    apiUrl:m=>(m || c.state.meta).api_url || '', token:()=>support || !signedIn ? '' : 'session',
    isMember:()=>!!(c.state.me && c.state.me.member),
    async fetchMe() {memberRequests++; return c.state.me={member:true};},
    async fetchFull() {if(memberPending) await memberPending.promise;
      if(memberFails) throw Error('private member error'); return [{...payload.deals[0], key:'member-only'}];},
    memberDeals:async()=>null, showDeals(){},
    fetch(url, options) {requests.push({url,options}); const sc=c.next;
      return (sc.pending ? sc.pending.promise : Promise.resolve()).then(()=>{
        if (sc.error) throw sc.error;
        return {ok:!sc.status || sc.status<400, headers:{get:()=>sc.tag || 'new-tag'},
          json:async()=>{if(sc.invalidJSON) throw Error('JSON at https://private.example/item');
            if(sc.jsonPending) await sc.jsonPending.promise; return sc.body;}};
      });
    }
  };
  c.panel=el('paywall');
  vm.createContext(c);
  for (const [a,b] of [
    ['  function esc(s)', '  function sortVal'],
    ['  function renderTabs()', '  function filtered('],
    ['  function render()', '  function showMore()'],
    ['  function resetWall()', '  // Easy sorting:'],
    ['  function fetchPublish()', '  // While a drop waits,']
  ]) vm.runInContext(snippet(a,b),c);
  if (!support) vm.runInContext(snippet('  function memberDeals(fresh)', '  function syncSubbar()'),c);
  vm.runInContext(snippet('  function loadData()', '})();'),c);
  return {c, el, listeners, intervals, events, requests, notices,
    get renders(){return renders;}, get firstAdoptions(){return firstAdoptions;}, get memberRequests(){return memberRequests;},
    retry:()=>listeners['load-retry:click'](), timer:()=>intervals.find(i=>i.delay===300000).fn(),
    count:name=>events.filter(e=>e[0]===name).length};
}
const goodPublish = () => ({meta:{...payload.meta,api_url:'https://api.test'},deals:payload.deals});
async function checkFirstLoad() {
  // Only a broken envelope rejects a publish (review of task 064).
  const badShapes = [null, [], {}, {meta:{}, deals:null}, {meta:null,deals:[]}, {meta:[],deals:[]},
    {meta:{},deals:{}}, {meta:{floors:[]},deals:[]},
    {meta:{front_page:{broad_categories:{}}},deals:payload.deals}];
  // A malformed deal is skipped; the rest of the publish still goes up.
  const badDeals = [[null], [[]], [{}],
    ...['store','conditions','credits','codes','how','final_price','locked','deal_path'].map(k=>
      [{...payload.deals[1],[k]:{invalid:true}}]),
    [{...payload.deals[1],credits:[null]}]];
  for (const bad of badDeals) {
    const h=loadHarness({body:{...goodPublish(),deals:[payload.deals[0], ...bad]}}); await flush();
    assert.equal(h.c.loadedOnce,true); assert.equal(h.el('load-status').hidden,true);
    assert.deepEqual(h.c.state.deals.map(d=>d.key).filter(k=>k===payload.deals[0].key),[payload.deals[0].key]);
    assert.ok(h.c.state.deals.every(d=>d && typeof d.store==='string' && typeof d.key==='string'));
  }
  for (const failure of [{error:Error('fetch failed https://private.example/product')},
    {status:404,body:goodPublish()}, {status:503,body:goodPublish()}, {invalidJSON:true},
    ...badShapes.map(body=>({body}))]) {
    const h=loadHarness(failure), before=h.el('content').innerHTML, tabs=h.el('cattabs').innerHTML;
    const oldMeta=h.c.state.meta, oldDeals=h.c.state.deals;
    await flush();
    assert.equal(h.c.loadedOnce,false); assert.equal(h.c.loadRequest,null); assert.equal(h.renders,0);
    assert.equal(h.c.state.meta,oldMeta); assert.equal(h.c.state.deals,oldDeals); assert.equal(h.c.clock.tag,'old-tag');
    assert.equal(h.el('content').innerHTML,before); assert.match(before,/<a class="getdeal" href="https:\/\//);
    assert.match(before,/<span class="credit">via <a href="https:\/\//);
    assert.equal(h.el('load-status').hidden,false); assert.equal(h.el('load-retry').disabled,false);
    assert.equal(h.el('load-message').textContent,'Live updates are unavailable. These are the last published deals.');
    assert.equal(h.count('home_load_attempt'),1); assert.equal(h.count('first_load_failed'),1);
    assert.equal(h.count('static_fallback_shown'),1); assert.equal(h.count('first_load_recovered'),0);
    assert.ok(h.events.every(e=>e.length===2 && e[1]===undefined));
    assert.doesNotMatch(h.el('load-message').textContent,/private|https:|JSON|fetch failed/);
    h.el('q').value='search while unavailable'; h.listeners['q:keydown']({key:'Escape',preventDefault(){}});
    h.listeners['storesel:change'](); h.c.render();
    assert.equal(h.el('content').innerHTML,before); assert.equal(h.el('cattabs').innerHTML,tabs);
    await h.retry(); assert.equal(h.requests.length,2); assert.equal(h.count('first_load_failed'),1);
    assert.equal(h.count('static_fallback_shown'),1); assert.equal(h.el('content').innerHTML,before);
    h.c.next={body:goodPublish()}; await h.retry();
    assert.equal(h.firstAdoptions,1); assert.equal(h.renders,1); assert.equal(h.c.loadedOnce,true);
    assert.equal(h.el('load-status').hidden,true); assert.equal(h.el('load-message').textContent,'');
    assert.match(h.el('content').innerHTML,/<article class="card"/);
    assert.equal(h.el('toast').textContent,'unrelated notice'); assert.equal(h.count('first_load_recovered'),1);
  }
  const missing=loadHarness({status:503}, {staticDeals:false}); await flush();
  assert.equal(missing.el('load-message').textContent,'Live updates are unavailable. Please try again.');
  assert.doesNotMatch(missing.el('content').innerHTML,/class="card"/); assert.equal(missing.count('static_fallback_shown'),0);
  missing.c.next={body:goodPublish()}; await missing.retry(); assert.equal(missing.count('first_load_recovered'),1);
  const empty=loadHarness({body:{meta:{},deals:[]}}); await flush();
  assert.equal(empty.c.loadedOnce,true); assert.equal(empty.firstAdoptions,1);
  assert.equal(empty.el('load-status').hidden,true); assert.match(empty.el('content').innerHTML,/No qualifying deals right now/);
  assert.deepEqual(empty.events.map(e=>e[0]),['home_load_attempt']);
  for(const timerFirst of [false,true]) {
    const h=loadHarness({error:Error('unavailable')}); await flush();
    const pending=deferred(); h.c.next={body:goodPublish(),pending};
    const tasks=timerFirst ? [h.timer(),h.retry(),h.retry()] : [h.retry(),h.timer(),h.retry()];
    h.listeners['window:pageshow']({persisted:true});
    assert.equal(h.requests.length,2); assert.ok(tasks.every(t=>t===tasks[0]));
    assert.equal(h.el('load-retry').disabled,true); assert.equal(h.el('load-status').hidden,false);
    assert.equal(h.el('content').innerHTML,staticWall);
    pending.resolve(); await Promise.all(tasks); await flush();
    assert.equal(h.firstAdoptions,1); assert.equal(h.renders,1); assert.equal(h.count('first_load_recovered'),1);
    await h.retry(); h.timer(); assert.equal(h.requests.length,2);
    const {meta,deals,dataKey}=h.c.state, clock={...h.c.clock}, content=h.el('content').innerHTML;
    h.c.clock.waiting=1; clock.waiting=1; h.c.next={status:503,body:goodPublish()}; await h.c.loadData();
    assert.equal(h.c.state.meta,meta); assert.equal(h.c.state.deals,deals); assert.equal(h.c.state.dataKey,dataKey);
    assert.deepEqual({...h.c.clock},clock); assert.equal(h.el('content').innerHTML,content);
    assert.equal(h.el('load-status').hidden,true); assert.equal(h.count('first_load_failed'),1);
  }
  const pending=deferred(), delayed=loadHarness({body:goodPublish(),jsonPending:pending}); await flush();
  delayed.c.render(); delayed.timer(); delayed.retry();
  assert.equal(delayed.c.loadedOnce,false); assert.equal(delayed.el('content').innerHTML,staticWall);
  assert.equal(delayed.el('load-status').hidden,true); assert.equal(delayed.requests.length,1);
  assert.equal(delayed.c.clock.tag,'old-tag');
  pending.resolve(); await flush(); assert.equal(delayed.firstAdoptions,1);
  const returnLoad=loadHarness({error:Error('unavailable')}); await flush();
  returnLoad.c.next={body:goodPublish()}; returnLoad.listeners['window:pageshow']({persisted:true}); await flush();
  assert.equal(returnLoad.firstAdoptions,1); assert.equal(returnLoad.count('first_load_recovered'),1);
  const normal=loadHarness({body:goodPublish()}); await flush();
  assert.equal(normal.firstAdoptions,1); assert.deepEqual(normal.events.map(e=>e[0]),['home_load_attempt']);
  normal.c.clock.waiting=1; normal.c.next={error:Error('later failure')}; await normal.c.loadData();
  assert.deepEqual(normal.events.map(e=>e[0]),['home_load_attempt']); assert.equal(normal.el('load-status').hidden,true);
  for (const memberFails of [false,true]) {
    const h=loadHarness({error:Error('unavailable')},{signedIn:true,memberFails}); await flush();
    h.c.next={body:goodPublish()};
    await h.retry(); await flush();
    assert.equal(h.firstAdoptions,1); assert.equal(h.count('first_load_recovered'),1); assert.equal(h.c.auth.checking,false);
    assert.equal(h.memberRequests,support ? 0 : 1);
    assert.equal(h.c.state.deals[0].key,!support && !payload.meta.open_access && !memberFails ? 'member-only' : payload.deals[0].key);
    assert.equal(h.notices[0],!support);
  }
  if (!support && !payload.meta.open_access) {
    const pending=deferred(), h=loadHarness({body:goodPublish()},{signedIn:true,memberPending:pending}); await flush();
    assert.equal(h.firstAdoptions,1); assert.equal(h.c.auth.checking,true); assert.deepEqual(h.c.state.deals,payload.deals);
    h.c.clock.waiting=1; h.c.next={error:Error('refresh while membership waits')}; await h.c.loadData();
    assert.equal(h.c.auth.checking,true); assert.equal(h.el('load-status').hidden,true);
    pending.resolve(); await flush(); assert.equal(h.c.auth.checking,false); assert.equal(h.c.state.deals[0].key,'member-only');
  }
}
(async()=>{
  for(const q of ['best office', 'bestoffice', 'best offi', 'BEST-OFFICE'])
    assert.deepEqual(items(query(q)), ['BestOffice 4-Shelf Bookcase']);
  assert.deepEqual(items(query('ninja blender')), ['Ninja Professional Plus Blender']);
  assert.deepEqual(items(query('cafe and the')), ['Café & Thé Espresso']);
  assert.deepEqual(items(query('CAFÉ & THÉ')), ['Café & Thé Espresso']);
  assert.equal(query('ninja missing').length,0);
  assert.equal(element('searchcount').textContent,'No deals match "ninja missing" : try fewer words');
  assert.equal(query('lego').length,5); // brand, even when absent from the item title
  assert.equal(query('online only').length,5);
  assert.equal(element('searchcount').textContent,'5 deals match "online only"');
  assert.equal(query('walmart').length,2);
  assert.equal(query('kitchen').length,5); // category labels/vocabulary
  assert.equal(query('best office').length,1);
  assert.equal(element('searchcount').textContent,'1 deal matches "best office"');
  assert.match(element('cattabs').innerHTML, /class="cattab active" href="\/" data-i="0"/);
  assert.equal(ctx.state.catTab,2);
  let prevented = false;
  handlers['q:keydown']({key:'Enter',preventDefault(){prevented=true;}});
  assert.ok(prevented); assert.equal(element('q').value,'best office');
  handlers['q:keydown']({key:'Escape',preventDefault(){}});
  assert.equal(element('q').value,''); assert.equal(element('searchcount').textContent,'');
  assert.equal(element('clearsearch').hidden,true); assert.ok(element('q').focused);
  assert.match(element('cattabs').innerHTML,/class="cattab active"[^>]+data-i="2"/);
  assert.equal(ctx.state.catTab,2);
  assert.equal(ctx.filtered().length,0); // original Used tab is effective again
  const before=renders;
  for (const value of ['n','ni','ninja']) {
    element('q').value=value; handlers['q:input'].call(element('q'));
  }
  assert.equal(renders,before); assert.equal(timers.size,1);
  const timer=[...timers.values()][0]; assert.equal(timer.delay,150); timer.fn();
  assert.equal(renders,before+1); assert.equal(ctx.state.shown,60);
  handlers['clearsearch:click'](); assert.equal(element('q').value,'');
  ctx.populateStores();
  assert.deepEqual(element('storesel').options.map(o=>o.textContent).slice(1),
    ["Macy's (2)",'Walmart (2)','Amazon (1)']);
  for(const d of payload.deals) {
    assert.ok(element('storesel').options.some(o=>o.value===ctx.storeKey(d.store)));
  }
  element('storesel').value='walmart'; assert.equal(query('ninja').length,1);
  element('storesel').value='amazon'; assert.equal(query('ninja').length,0);
  element('storesel').value=''; element('minpct').value='90'; assert.equal(query('ninja').length,0);
  element('minpct').value='0';
  const ninja=payload.deals.find(d=>d.item.startsWith('Ninja'));
  ctx.state.deals.push({store:'Ghost',status:'dead'});
  ctx.refreshStores(); assert.ok(!element('storesel').options.some(o=>o.value==='ghost'));
  ctx.state.deals.pop();
  element('storesel').value='walmart'; ctx.refreshStores(); assert.equal(element('storesel').value,'walmart');
  // The Amazon band counts live deals across categories, using the same
  // normalized store key as the existing filter, and follows later drops.
  ctx.state.deals = Array.from({length:9}, (_,i)=>({...payload.deals[2], key:`a${i}`,
    store:['Amazon',' amazon.COM ','AMAZON'][i%3], status:'alive'}));
  ctx.state.deals.push({...payload.deals[2], store:'Amazon', status:'dead'},
    {...payload.deals[2], store:'Amazon Fresh', status:'alive'});
  ctx.refreshStores(); assert.equal(element('amazon-strip').hidden,true);
  ctx.state.deals.push({...payload.deals[2], key:'tenth', store:'Amazon', status:'alive'});
  ctx.refreshStores(); assert.equal(element('amazon-strip').hidden,false);
  element('q').value='missing'; element('minpct').value='99'; element('storesel').value='walmart';
  ctx.state.catTab=3; ctx.state.shown=600; ctx.state.sortKey='price'; ctx.state.sortDir=1;
  for (const modifier of ['ctrlKey','metaKey','shiftKey','altKey','button']) {
    handlers['amazon-deals:click']({[modifier]:1, preventDefault(){assert.fail('modified navigation intercepted');}});
    assert.equal(ctx.state.sortKey,'price'); assert.equal(ctx.state.shown,600);
  }
  ctx.loadedOnce=false;
  handlers['amazon-deals:click']({preventDefault(){assert.fail('navigation before data was intercepted');}});
  ctx.loadedOnce=true;
  let amazonPrevented=false;
  handlers['amazon-deals:click']({preventDefault(){amazonPrevented=true;}});
  assert.ok(amazonPrevented); assert.equal(ctx.state.catTab,0); assert.equal(ctx.state.shown,60);
  assert.equal(element('q').value,''); assert.equal(element('clearsearch').hidden,true);
  assert.equal(element('minpct').value,'0'); assert.equal(element('storesel').value,'amazon');
  assert.equal(element('sortsel').value,'top:-1'); assert.equal(saved.get('wp.sort'),'top:-1');
  assert.equal(ctx.state.sortKey,'top'); assert.equal(ctx.state.sortDir,-1);
  assert.equal(ctx.filtered().filter(d=>d.status!=='dead').length,10);
  assert.ok(ctx.filtered().every(d=>ctx.storeKey(d.store)==='amazon'));
  assert.equal(element('controls').scrolled.behavior,'auto');
  // Opening the actual link in another tab overrides a remembered sort.
  element('storesel').value=''; ctx.state.sortKey='price';
  ctx.location.search='?store=amazon&sort=top';
  vm.runInContext(snippet('      var browse =', '      renderTabs();'),ctx);
  assert.equal(element('storesel').value,'amazon'); assert.equal(ctx.state.sortKey,'top');
  ctx.state.deals.pop(); ctx.refreshStores(); assert.equal(element('amazon-strip').hidden,true);
  element('storesel').value='';
  ctx.state.deals=Array.from({length:1637},()=>ninja);
  // The published metadata renderer sets the rounded live count placeholder.
  const countCode = snippet('    var priceOnly =', '    if (floor !=') +
    snippet('    var live = state.deals.filter', '    document.getElementById("tagline").textContent');
  vm.runInContext(countCode,ctx);
  assert.equal(element('q').placeholder,'Search 1,000+ deals');
  ctx.state.deals=[];
  ctx.state.spotlight=[{pct_at_checkout:null, spotlight:{until:new Date(now+3600000).toISOString()}}];
  vm.runInContext(countCode,ctx);
  assert.equal(element('q').placeholder,'Search 1 deals');
  ctx.state.spotlight=[];
  ctx.state.deals=payload.deals;
  handlers['window:pageshow']({persisted:false}); await flush(); assert.equal(requests.length,0);
  handlers['window:pageshow']({persisted:true}); await flush();
  assert.equal(requests.length,1); assert.match(requests[0].url,/^deals.json\?t=/);
  assert.equal(requests[0].options.cache,'no-store'); assert.equal(reloads,0);
  assert.equal(ctx.state.dataKey,payload.meta.published_at);
  const hidden = value=>{ctx.document.hidden=value; handlers['document:visibilitychange']();};
  hidden(true); now=299999; hidden(false); await flush(); assert.equal(requests.length,1);
  hidden(true); now+=300000; hidden(false); await flush(); assert.equal(requests.length,2);
  const renderBefore=renders;
  response={meta:{...payload.meta,published_at:'new-publish'},deals:[ninja]};
  handlers['window:pageshow']({persisted:true}); await flush();
  assert.equal(ctx.state.deals.length,1); assert.ok(renders>renderBefore); assert.equal(reloads,0);
  response={...response,meta:{...response.meta,site_version:'new-template'}};
  handlers['window:pageshow']({persisted:true}); await flush(); assert.equal(reloads,1);
  handlers['window:pageshow']({persisted:true}); await flush(); assert.equal(reloads,1);
  fail=true; handlers['window:pageshow']({persisted:true}); await flush();
  assert.equal(ctx.returnRefresh,null); assert.equal(ctx.state.deals.length,1);
  fail=false; const reqBefore=requests.length;
  handlers['window:pageshow']({persisted:true}); handlers['window:pageshow']({persisted:true});
  await flush(); assert.equal(requests.length,reqBefore+1);
  await checkFirstLoad();
  console.log('search, filters, cache refresh and static fallback passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
