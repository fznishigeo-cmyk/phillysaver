// Exercise the published filters, history, storage and wall together, offline.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const home = fs.readFileSync(process.argv[2], 'utf8');
const doc = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const fragment = (start, end) => {
  const a = home.indexOf(start), b = home.indexOf(end, a);
  assert(a >= 0 && b > a, start);
  return home.slice(a, b);
};
const NOW = Date.parse('2026-10-13T18:00:00Z');
const row = (key, changes={}) => ({key, item:'Gadget '+key, store:'Amazon', category:'home', conditions:[],
  needs_steps:false, final_price:10, pct_at_checkout:80, abs_savings:40, status:'alive',
  first_seen_at:new Date(NOW-1000).toISOString(), age_hours:1, image_url:'https://image.test/photo', ...changes});
const mixed = [row('new'), row('used',{conditions:['Used - Like New']}), row('box',{conditions:['Open-box']}),
  row('renewed',{conditions:['Condition: Renewed']}), row('steps',{store:'Walmart',needs_steps:true}),
  row('expensive',{final_price:40}), row('null',{final_price:null}), row('unknown',{conditions:null}),
  row('plain-unknown',{needs_steps:null}), row('gone',{status:'dead',gone:true}),
  row('locked',{locked:true,final_price:null,needs_steps:undefined}), row('other',{store:'Walmart',category:'kitchen',final_price:25})];

function harness({rows=mixed, search='', stored=null, navigation='', historyState=null, blocked=false, saved=false}={}) {
  const elements={}, listeners={}, timers=new Map(), events=[], entries=[{url:'/'+search,state:historyState}];
  let serial=0, historyIndex=0, renders=0, refreshes=0, nextRows=null, pendingRefresh=null, scrolls=[];
  let cards=[];
  const session = stored || new Map();
  function listen(key, fn) { (listeners[key] ||= []).push(fn); }
  function fire(key, e={}) { for (const fn of [...(listeners[key] || [])]) fn.call(elements[key.split(':')[0]], e); }
  function el(id) {
    return elements[id] ||= {id,value:'',checked:false,disabled:false,hidden:false,options:[],attributes:{},innerHTML:'',
      addEventListener(type, fn) {listen(id+':'+type,fn);},
      appendChild(node) {if(id==='storesel') this.options.push(node); else node.parentElement=this;},
      remove(i) {this.options.splice(i,1);},
      get selectedIndex() {return this.options.findIndex(o=>o.value===this.value);},
      set selectedIndex(i) {this.value=this.options[i]?.value || '';},
      querySelectorAll(selector) {
        if (selector!=='.cattab') return [];
        return [...this.innerHTML.matchAll(/data-i="(\d+)"/g)].map(m=>({getAttribute:()=>m[1],
          addEventListener(type,fn) {listen('category'+m[1]+':'+type,fn);}}));
      },
      querySelector:()=>null, setAttribute(k,v) {this.attributes[k]=v;},
      focus() {ctx.document.activeElement=this;}, scrollIntoView(){}, reportValidity:()=>true,
      getBoundingClientRect:()=>({top:500,left:0,right:390,bottom:844}),
      showModal() {this.open=true; ctx.document.activeElement=this;},
      close() {this.open=false; fire(id+':close');}
    };
  }
  el('storesel').options.push({value:''});
  el('sortsel').options=Object.values({top:'top:-1',fresh:'fresh:-1',pct:'pct:-1',abs:'abs:-1',price:'price:1',age:'age:1'}).map(value=>({value}));
  el('filterfields').disabled=true; el('filtertoggle').disabled=true;
  Object.defineProperty(el('content'),'innerHTML', {get(){return this.html || '';},set(value) {
    this.html=value;
    cards=[...value.matchAll(/data-key="([^"]+)"/g)].map((m,i)=>({key:m[1],
      getAttribute:()=>m[1], getBoundingClientRect:()=>({top:400+i*220-ctx.window.scrollY,bottom:620+i*220-ctx.window.scrollY}),
      querySelector:()=>el('link-'+m[1])}));
  }});
  const initialURL=new URL('/'+search,'https://wowpriced.com');
  const location={search:initialURL.search,hash:initialURL.hash,pathname:'/'};
  const updateLocation = url => {const u=new URL(url,'https://wowpriced.com'); location.search=u.search;location.hash=u.hash;};
  const history={state:historyState,scrollRestoration:'auto',
    replaceState(state, _, url) {this.state=state;entries[historyIndex]={state,url};updateLocation(url);},
    pushState(state, _, url) {this.state=state;entries.splice(++historyIndex);entries.push({state,url});updateLocation(url);}};
  class FixedDate extends Date {constructor(...args){super(...(args.length ? args : [NOW]));} static now(){return NOW;}}
  const ctx={Date:FixedDate,URL,URLSearchParams,Set,Map,PAGE_CARDS:60,AUTO_CARDS:300,JITTER_H:3,NO_PHOTO_H:12,
    CATGROUPS:JSON.parse(home.match(/var CATGROUPS = (.*);/)[1]), loadedOnce:true,moreObserver:null,panel:el('paywall'),
    state:{deals:rows,spotlight:[],meta:{floors:{min_pct:70,site_show_from_pct:50},front_page:doc.meta.front_page || {}},
      shown:60,seed:42,catTab:0,sortKey:'top',sortDir:-1,newKeys:{}},
    document:{hidden:false,body:{contains:()=>true},activeElement:null,
      getElementById:id=>['moremore','totop'].includes(id) && !el('content').innerHTML.includes('id="'+id+'"') ? null : el(id),createElement:()=>({}),
      addEventListener(type,fn){listen('document:'+type,fn);}},
    window:{innerWidth:390,innerHeight:844,scrollY:0,wpSupport:{refresh(){}},wpTrack:(name,params)=>events.push({name,params}),
      scrollTo(options){this.scrollY=options.top;scrolls.push(options);},
      addEventListener(type,fn){listen('window:'+type,fn);}},
    history,location,performance:{getEntriesByType:()=>[{type:navigation}]},
    localStorage:{getItem:()=>null,setItem(){}},
    sessionStorage:{getItem(k){if(blocked) throw Error('blocked');return session.get(k) || null;},
      setItem(k,v){if(blocked) throw Error('blocked');session.set(k,v);}},
    setTimeout(fn,delay){timers.set(++serial,{fn,delay});return serial;},clearTimeout:id=>timers.delete(id),
    requestAnimationFrame:fn=>fn(),track:(name,params)=>events.push({name,params}),
    noop(){},reduceMotion:()=>true,cardEls:()=>cards,
    isLocked:d=>d.locked===true,dealKey:d=>d.key,plural:(n,w)=>n+' '+w+(n===1?'':'s'),
    paintTicker(){},updatePaywall(){},openNudge:()=>false,openAccess:()=>true,canJoin:()=>false,
    cardHtml:d=>'<article class="card" data-key="'+d.key+'"></article>',
    engagementLineHtml:()=>'',SORT_KEY:'wp.sort',savedOn:()=>saved,savedDeals:{view:false},
    renderSavedControls(){el('filterfields').disabled=ctx.savedDeals.view;el('filtertoggle').disabled=ctx.savedDeals.view;},
    savedWatchEditor:()=>null,restoreSavedWatchEditor(){},savedWatchHtml:()=>'',
    savedViewHtml:()=>'<div class="grid">'+ctx.state.deals.slice(0,2).map(ctx.cardHtml).join('')+'</div>',
    async fetchPublish(){refreshes++;if(pendingRefresh) await pendingRefresh;return {deals:nextRows || ctx.state.deals};},
    async merge(pub){ctx.state.deals=pub.deals;ctx.refreshStores();}, loadData:async()=>{},
    rememberFocus:()=>null,restoreFocus(){},openBand:()=>''
  };
  vm.createContext(ctx);
  for (const [a,b] of [
    ['  function esc(s)', '  // One string per published'],
    ['  function storeKey(', '  function storeHue('],
    ['  function gridHtml(', '  // ---- reorganizing the wall'],
    ['  function resetWall()', '  // Easy sorting:'],
    ['  var sortsel =', '  // ---- publishes:']
  ]) vm.runInContext(fragment(a,b),ctx);
  ctx.populateStores();
  ctx.initBrowse();
  const renderWall=ctx.render;
  ctx.render=()=>{renders++;renderWall();};
  ctx.render();ctx.restoreInitialBrowse();
  const apply=(changes={})=>{
    for(const [id,value] of Object.entries(changes)) {
      if(['newonly','plainonly'].includes(id)) el(id).checked=value; else el(id).value=value;
    }
    fire('filterform:submit',{preventDefault(){}});
  };
  return {ctx,el,fire,apply,events,session,entries,scrolls,
    keys:()=>cards.map(c=>c.key),get renders(){return renders;},get refreshes(){return refreshes;},
    get pushCount(){return entries.length-1;},
    navigation(delta){historyIndex+=delta;history.state=entries[historyIndex].state;updateLocation(entries[historyIndex].url);fire('window:popstate');},
    refresh(rows,pending=null){nextRows=rows;pendingRefresh=pending;},
    flushTimers(){const work=[...timers.values()];timers.clear();for(const t of work)t.fn();},
    savedView(on){ctx.savedDeals.view=on;ctx.renderSavedControls();ctx.render();}
  };
}
const keys = rows=>Array.from(rows,d=>d.key);
const flush=async()=>{await new Promise(r=>setImmediate(r));};
(async()=>{
  // Every intersection uses known facts, with nulls/stubs denied only by
  // explicit eligibility filters. Conditions use the ingest vocabulary.
  for (const ceiling of ['', '25']) for(const newOnly of [false,true]) for(const plainOnly of [false,true]) {
    const h=harness();h.apply({pricepreset:ceiling,newonly:newOnly,plainonly:plainOnly});
    const expected=mixed.filter(d=>(!ceiling || (typeof d.final_price==='number' && d.final_price<=25)) &&
      (!newOnly || ['new','steps','expensive','null','plain-unknown','gone','other','locked'].includes(d.key)) &&
      (!plainOnly || !['steps','plain-unknown','locked'].includes(d.key)));
    assert.deepEqual(keys(h.ctx.filtered()),keys(expected));
    assert.equal(h.ctx.state.meta.floors.min_pct,70);assert.equal(h.ctx.state.meta.floors.site_show_from_pct,50);
  }
  const h=harness();
  h.el('storesel').value='walmart';h.fire('storesel:change');
  h.apply({pricepreset:'25',newonly:true,plainonly:true});assert.deepEqual(keys(h.ctx.filtered()),['other']);
  h.el('q').value='other';h.ctx.updateSearch();assert.deepEqual(keys(h.ctx.filtered()),['other']);
  h.el('minpct').value='90';h.fire('minpct:input');h.fire('minpct:change');assert.deepEqual(keys(h.ctx.filtered()),[]);
  const empties=h.events.filter(e=>e.name==='filter_empty').length;
  h.fire('minpct:change');assert.equal(h.events.filter(e=>e.name==='filter_empty').length,empties);
  h.fire('filterclear:click');assert.equal(h.el('q').value,'');assert.equal(h.el('storesel').value,'');
  assert.equal(h.el('minpct').value,'0');assert.equal(h.ctx.state.catTab,0);assert.equal(h.ctx.state.shown,60);
  assert.deepEqual(keys(h.ctx.filtered()),keys(mixed));
  const actions=h.events.length;h.fire('filterclear:click');h.apply();assert.equal(h.events.length,actions);
  h.ctx.state.shown=180;h.ctx.renderTabs();h.fire('category0:click',{preventDefault(){}});
  assert.equal(h.ctx.state.shown,60);assert.equal(h.events.length,actions);
  assert(h.events.some(e=>e.name==='filter_apply'));
  assert(h.events.some(e=>e.name==='filter_empty'));
  const spacing=harness({rows:Array.from({length:32},(_,i)=>row('sp'+i,{status:i%3===0?'dead':'alive'}))});
  spacing.apply({pricepreset:'25',newonly:true,plainonly:true});
  const goneSlots=spacing.keys().map((key,i)=>spacing.ctx.state.deals.find(d=>d.key===key).status==='dead'?i:-1).filter(i=>i>=0);
  assert(goneSlots[0]>=8);assert(goneSlots.every((n,i)=>!i || n-goneSlots[i-1]>=8));
  const floor=harness({rows:[row('high'),row('lower',{pct_at_checkout:55})]});
  floor.apply({pricepreset:'25'});assert.deepEqual(floor.keys().sort(),['high','lower']);
  floor.el('sortsel').value='pct:-1';floor.fire('sortsel:change');assert.deepEqual(floor.keys(),['high','lower']);
  const front=harness();front.ctx.state.meta.front_page={prefer_photos:true};
  assert(front.ctx.frontPageView());front.apply({maxprice:'20'});assert.equal(front.ctx.frontPageView(),false);
  // Draft input does not filter until Apply; presets and custom values
  // replace each other, and form validation is respected.
  h.el('maxprice').value='9.75';h.fire('maxprice:input');assert.deepEqual(keys(h.ctx.filtered()),keys(mixed));
  h.apply();assert.equal(h.ctx.filtered().length,0);assert.equal(h.el('pricepreset').value,'');
  const afterApply=h.events.length;h.apply();assert.equal(h.events.length,afterApply);
  h.el('pricepreset').value='25';h.fire('pricepreset:change');assert.equal(h.el('maxprice').value,'');
  h.apply();assert(h.ctx.filtered().length>0);
  h.el('filterform').reportValidity=()=>false;h.apply({maxprice:'-1'});assert.equal(h.ctx.browseFilters.preset,'25');
  for(const price of [null,undefined,NaN,Infinity,-Infinity,'5']) {
    assert.equal(h.ctx.matchesEligibility(row('odd',{final_price:price})),false);
  }
  for(const condition of ['pre-owned','Refurbished','reconditioned','openbox','Amazon Warehouse','Second hand','unknown']) {
    h.ctx.browseFilters.newOnly=true;assert.equal(h.ctx.matchesEligibility(row('odd',{conditions:[condition]})),false);
  }
  h.ctx.clearEligibility();h.ctx.browseFilters.newOnly=true;
  assert(h.ctx.matchesEligibility(row('locked',{locked:true,conditions:[]})));
  assert.equal(h.ctx.matchesEligibility(row('locked',{locked:true,conditions:undefined})),false);
  assert.equal(h.ctx.matchesEligibility(row('locked',{locked:true,conditions:['used']})),false);
  // URL validation uses the actual counted store list, categories, sorts
  // and configured presets. Neither custom input nor queries can enter it.
  const valid=harness({search:'?store=amazon&category=home-kitchen&sort=price&price=25&new=1&steps=none'});
  assert.equal(valid.ctx.state.sortKey,'price');assert.equal(valid.ctx.state.catTab,3);
  assert.deepEqual(keys(valid.ctx.filtered()),['new','gone']);
  const amazon=harness({search:'?store=amazon&sort=top#content'});
  assert.equal(amazon.el('storesel').value,'amazon');assert.equal(amazon.ctx.state.sortKey,'top');
  const bad=harness({search:'?store=ghost&category=ghost&sort=constructor&price=12&new=yes&steps=false&q=PRIVATE&max=123'});
  assert.equal(bad.ctx.browseURL(),'/');assert.equal(bad.el('q').value,'');assert.equal(bad.ctx.eligibilityActive(),false);
  const repeated=harness({search:'?store=amazon&store=walmart&price=25&price=10&sort=price&sort=top&new=1&new=0&steps=none&steps=none'});
  assert.equal(repeated.ctx.browseURL(),'/');
  // Private text replaces the current history state, with no entry per
  // keystroke, and never appears in history.state or event parameters.
  valid.el('q').value='Gadget';valid.ctx.updateSearch();
  valid.apply({pricepreset:'',maxprice:'23.45',newonly:false,plainonly:false});
  const pushes=valid.pushCount;
  for(const query of ['PRIVATE-a','PRIVATE-ab','PRIVATE-abc']) {
    valid.el('q').value=query;valid.fire('q:input');
  }
  valid.flushTimers();assert.equal(valid.pushCount,pushes);
  assert(!JSON.stringify(valid.entries).includes('PRIVATE'));assert(!JSON.stringify(valid.entries).includes('23.45'));
  assert(!JSON.stringify(valid.events).includes('PRIVATE'));assert(!JSON.stringify(valid.events).includes('23.45'));
  assert(valid.session.get('wp.browse').includes('PRIVATE-abc'));
  // The complete public URL overrides remembered selections. Identical
  // URLs retain their tab-private custom ceiling and query on reload.
  const reload=harness({rows:mixed,search:valid.ctx.location.search,stored:valid.session,navigation:'reload',historyState:valid.ctx.history.state});
  assert.equal(reload.el('q').value,'PRIVATE-abc');assert.equal(reload.ctx.browseFilters.max,'23.45');
  assert.equal(reload.events.filter(e=>e.name==='back_restore').length,1);
  const shared=harness({search:'?store=walmart&price=50&sort=pct',stored:new Map(valid.session)});
  assert.equal(shared.el('q').value,'');assert.equal(shared.ctx.browseFilters.max,'');
  assert.equal(shared.el('storesel').value,'walmart');assert.equal(shared.ctx.browseFilters.preset,'50');
  const defaults=harness({stored:valid.session});assert.equal(defaults.ctx.browseFilters.max,'23.45');
  // Real same-document Back and Forward restore filters, the remembered
  // search/category and shown count without emitting filter actions.
  const many=Array.from({length:420},(_,i)=>row('r'+i,{final_price:5+i%40,ps:i,pick:i<120?i:null}));
  const nav=harness({rows:many});
  nav.ctx.state.catTab=3;nav.ctx.renderTabs();nav.ctx.finishBrowseAction('category',true);
  nav.el('q').value='Gadget';nav.ctx.updateSearch();nav.ctx.state.shown=180;nav.ctx.render();
  nav.ctx.window.scrollY=400+95*220+80;nav.ctx.rememberBrowse(false);
  const anchor=nav.ctx.browseMemory.entries[nav.ctx.browseId].anchor;
  nav.apply({pricepreset:'25'});assert.equal(nav.ctx.state.shown,60);
  const filterEvents=nav.events.filter(e=>e.name==='filter_apply').length;
  nav.navigation(-1);assert.equal(nav.ctx.browseFilters.preset,'');assert.equal(nav.el('q').value,'Gadget');
  assert.equal(nav.ctx.state.catTab,3);assert.equal(nav.ctx.state.shown,180);
  assert.equal(nav.ctx.window.scrollY,400+95*220+80);
  nav.navigation(1);assert.equal(nav.ctx.browseFilters.preset,'25');
  assert.equal(nav.events.filter(e=>e.name==='filter_apply').length,filterEvents);
  assert.equal(nav.events.filter(e=>e.name==='back_restore').length,2);
  // Restore after a new publish: anchor deletion or an increased price
  // cannot resurrect it. The neighboring current card takes its position.
  nav.ctx.window.scrollY=400+20*220+30;nav.ctx.rememberBrowse(false);
  const old=nav.ctx.browseMemory.entries[nav.ctx.browseId].anchor;
  const updated=many.filter(d=>d.key!==old.key).map(d=>d.key===anchor.key?{...d,final_price:999}:d);
  let release;const wait=new Promise(r=>{release=r;});nav.refresh(updated,wait);
  nav.fire('window:pageshow',{persisted:true});nav.fire('window:pageshow',{persisted:true});
  assert.equal(nav.refreshes,1);release();await flush();
  assert(!nav.keys().includes(old.key));assert(!nav.keys().includes(anchor.key));
  assert.equal(nav.events.filter(e=>e.name==='back_restore').length,3);
  assert(nav.scrolls.every(s=>s.behavior==='auto'));
  // A retained anchor that moved beyond the saved page is rendered before
  // restoration; scrolling alone does not exceed Trending's 300-card cap.
  nav.ctx.clearEligibility();nav.ctx.render();nav.ctx.restoreBrowsePosition({anchor:{key:'r400',index:5,offset:20},y:0},'history');
  assert(nav.keys().includes('r400'));
  let observed=0;nav.ctx.window.IntersectionObserver=true;
  nav.ctx.IntersectionObserver=class {observe(){observed++;}disconnect(){}};
  nav.ctx.state.catTab=0;nav.ctx.state.shown=300;nav.ctx.armMore({});assert.equal(observed,0);
  nav.ctx.state.catTab=1;nav.ctx.armMore({});assert.equal(observed,1);
  nav.ctx.render();nav.ctx.showMore();assert.equal(nav.ctx.state.shown,360);
  // No storage, corrupt storage and a store removed by the current
  // publish all retain working filters and safe restoration behavior.
  const denied=harness({blocked:true});denied.apply({maxprice:'15',newonly:true});
  denied.ctx.window.scrollY=800;denied.fire('window:pagehide');denied.refresh(mixed.slice(1));
  denied.fire('window:pageshow',{persisted:true});await flush();assert.equal(denied.ctx.browseFilters.max,'15');
  for(const raw of ['invalid','[]','{"version":1,"entries":null}', '{"version":999,"entries":{}}']) {
    const corrupt=harness({stored:new Map([['wp.browse',raw]])});assert.equal(corrupt.ctx.browseURL(),'/');
  }
  const removed=harness({rows:mixed.filter(d=>d.store==='Walmart'),search:valid.ctx.location.search,
    stored:valid.session,historyState:valid.ctx.history.state,navigation:'reload'});
  assert.equal(removed.el('storesel').value,'');assert.equal(removed.ctx.browseFilters.max,'23.45');
  assert.equal(removed.events.filter(e=>e.name==='back_restore').length,1);
  // The Today-only card shares every new filter, including unknown fields.
  const spot=harness();spot.ctx.state.spotlight=[row('today',{spotlight:{until:new Date(NOW+3600000).toISOString()}})];
  spot.ctx.render();assert(spot.keys().includes('today'));
  spot.apply({maxprice:'5'});assert(!spot.keys().includes('today'));
  spot.apply({maxprice:'',newonly:true});assert(spot.keys().includes('today'));
  spot.ctx.state.spotlight[0].conditions=null;spot.ctx.render();assert(!spot.keys().includes('today'));
  spot.ctx.state.spotlight[0].conditions=[];spot.ctx.state.spotlight[0].needs_steps=null;
  spot.apply({plainonly:true});assert(!spot.keys().includes('today'));
  // Sheet opens with applied values, moves focus, returns focus on
  // close, and discards drafts. Native dialog supplies modal focus/Escape.
  const sheet=harness();sheet.fire('filtertoggle:click');assert(sheet.el('filtersheet').open);
  assert.equal(sheet.ctx.document.activeElement,sheet.el('pricepreset'));
  sheet.el('maxprice').value='12';sheet.fire('filterclose:click');
  assert.equal(sheet.ctx.document.activeElement,sheet.el('filtertoggle'));assert.equal(sheet.el('maxprice').value,'');
  sheet.fire('filtertoggle:click');sheet.apply({maxprice:'12'});
  assert(!sheet.el('filtersheet').open);assert.equal(sheet.events[0].params.method,'sheet');
  const first=harness();first.ctx.loadedOnce=false;
  const before=first.renders;first.apply({maxprice:'1'});first.fire('filterclear:click');first.fire('filtertoggle:click');
  assert.equal(first.renders,before);assert.equal(first.events.length,0);assert.equal(first.ctx.browseFilters.max,'');
  if (home.includes('function savedOn()')) {
    const saved=harness({saved:true});saved.savedView(true);assert(saved.el('filterfields').disabled);
    saved.apply({maxprice:'1'});assert.equal(saved.ctx.browseFilters.max,'');saved.savedView(false);
    assert.equal(saved.el('filterfields').disabled,false);
    saved.savedView(true);saved.fire('window:pagehide');
    const again=harness({saved:true,stored:saved.session,navigation:'reload',historyState:saved.ctx.history.state});
    assert(again.ctx.savedDeals.view);assert(again.el('filterfields').disabled);
    again.ctx.renderTabs();again.fire('category0:click',{preventDefault(){}});
    assert.equal(again.ctx.savedDeals.view,false);assert.equal(again.el('filterfields').disabled,false);
    assert(again.keys().length>2);
  }
  console.log('price/eligibility intersections, private browsing history, current-fact restoration and sheet controls passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
