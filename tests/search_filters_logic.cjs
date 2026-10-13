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
    return {headers:{get:()=>''}, json:async()=>response};}
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
  console.log('search, filters and cache refresh passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
