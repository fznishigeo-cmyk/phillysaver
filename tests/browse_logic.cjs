// Exercise the published filters, history, storage and wall together, offline.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const home = fs.readFileSync(process.argv[2], 'utf8');
const doc = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const fragment = (start, end, template=home) => {
  const a = template.indexOf(start), b = template.indexOf(end, a);
  assert(a >= 0 && b > a, start);
  return template.slice(a, b);
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

function harness({rows=mixed, spotlight=[], search='', stored=null, navigation='', historyState=null, blocked=false, saved=false,
                  rotationStartup=false, realCards=false, template=home}={}) {
  const elements={}, listeners={}, timers=new Map(), frames=new Map(), observers=new Set(), events=[], entries=[{url:'/'+search,state:historyState}];
  let serial=0, historyIndex=0, renders=0, refreshes=0, nextRows=null, pendingRefresh=null, scrolls=[];
  let cards=[], clock=NOW, wallShift=0, cardHeight=220, frameCount=0;
  const session = stored || new Map();
  function listen(key, fn) { (listeners[key] ||= []).push(fn); }
  function unlisten(key, fn) { listeners[key] = (listeners[key] || []).filter(listener=>listener!==fn); }
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
      querySelector:()=>null, setAttribute(k,v) {this.attributes[k]=v;}, removeAttribute(k){delete this.attributes[k];},
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
      getAttribute:()=>m[1], getBoundingClientRect:()=>({top:400+wallShift+i*cardHeight-ctx.window.scrollY,
        bottom:400+wallShift+(i+1)*cardHeight-ctx.window.scrollY}),
      querySelector:()=>el('link-'+m[1])}));
    for (const observer of observers) observer.callback();
  }});
  const initialURL=new URL('/'+search,'https://wowpriced.com');
  const location={search:initialURL.search,hash:initialURL.hash,pathname:'/'};
  const updateLocation = url => {const u=new URL(url,'https://wowpriced.com'); location.search=u.search;location.hash=u.hash;};
  const history={state:historyState,scrollRestoration:'auto',
    replaceState(state, _, url) {this.state=state;entries[historyIndex]={state,url};updateLocation(url);},
    pushState(state, _, url) {this.state=state;entries.splice(++historyIndex);entries.push({state,url});updateLocation(url);}};
  class FixedDate extends Date {constructor(...args){super(...(args.length ? args : [clock]));} static now(){return clock;}}
  const ctx={Date:FixedDate,URL,URLSearchParams,Set,Map,PAGE_CARDS:60,AUTO_CARDS:300,JITTER_H:3,NO_PHOTO_H:12,
    CATGROUPS:JSON.parse(template.match(/var CATGROUPS = (.*);/)[1]), loadedOnce:true,manifestEpoch:0,moreObserver:null,panel:el('paywall'),
    state:{deals:rows,spotlight,meta:{floors:{min_pct:70,site_show_from_pct:50},front_page:doc.meta.front_page || {}},
      shown:60,seed:42,catTab:0,sortKey:'top',sortDir:-1,newKeys:{}},
    document:{hidden:false,body:{contains:()=>true},activeElement:null,
      getElementById:id=>['moremore','totop'].includes(id) && !el('content').innerHTML.includes('id="'+id+'"') ? null : el(id),createElement:()=>({}),
      addEventListener(type,fn){listen('document:'+type,fn);}},
    window:{innerWidth:390,innerHeight:844,scrollY:0,wpSupport:{refresh(){}},wpTrack:(name,params)=>events.push({name,params}),
      scrollTo(options){this.scrollY=options.top;scrolls.push(options);},
      addEventListener(type,fn){listen('window:'+type,fn);},
      removeEventListener(type,fn){unlisten('window:'+type,fn);}},
    history,location,performance:{getEntriesByType:()=>[{type:navigation}]},
    localStorage:{getItem:()=>null,setItem(){}},
    sessionStorage:{getItem(k){if(blocked) throw Error('blocked');return session.get(k) || null;},
      setItem(k,v){if(blocked) throw Error('blocked');session.set(k,v);}},
    setTimeout(fn,delay){timers.set(++serial,{fn,delay,at:clock+delay});return serial;},clearTimeout:id=>timers.delete(id),
    requestAnimationFrame(fn){frames.set(++serial,fn);return serial;},cancelAnimationFrame:id=>frames.delete(id),
    MutationObserver:class {
      constructor(callback){this.callback=callback;}
      observe(){observers.add(this);}
      disconnect(){observers.delete(this);}
    },
    track:(name,params)=>events.push({name,params}),
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
    ['  function esc(s)', '  function dataKey('],
    ['  function storeKey(', '  function storeHue('],
    ['  function gridHtml(', '  function reduceMotion('],
    ['  function resetWall()', '  var sortsel ='],
    ['  var sortsel =', '  var TIMEOUT =']
  ]) vm.runInContext(fragment(a,b,template),ctx);
  if (realCards) {
    Object.assign(ctx, {TIER_LABEL:{corroborated:'2+ sources'}, isMember:()=>false,
      auth:{checking:false},unknownMembership:()=>false,newSinceVisit:()=>false,hotHtml:()=>'',
      savedPublicMatch:key=>rows.concat(spotlight).find(d=>d.key===key),isSaved:()=>false});
    vm.runInContext(fragment('  function savedButtonHtml(', '  function renderSavedControls(',template),ctx);
    vm.runInContext(fragment('  function storeHue(', '  var TICKER_MAX =',template),ctx);
  }
  if (rotationStartup) {
    Object.assign(ctx, {loadedOnce:false, clock:{step:1000,phase:NOW-1000},
      initEngagement(){},readSaved(){},checkSavedWatches(){},savedLimit:()=>100,
      syncClock(){},sinceLastVisit(){},rememberSeen(){},renderMeta(){},openFromHash(){},isMember:()=>false});
    Object.assign(ctx.savedDeals, {entries:[],matches:new Map(),persistent:true});
    for (const [a,b] of [
      ['  function keySet(', '  function liveNotIn('],
      ['  function openKeys(', '\n  }'],
      ['  function reconcileSaved(', '  function isSaved('],
      ['  function renderSavedControls(', '  function setSavedView('],
      ['  function surpriseRows(', '  document.getElementById("surprise").addEventListener'],
      ['  function markIndex(', '  function mmss('],
      ['  function applyPublish(', '  function landed(']
    ]) vm.runInContext(fragment(a,b,template) + (b === '\n  }' ? '\n  }' : ''),ctx);
    ctx.state.deals=[];ctx.state.seed=0;
    ctx.applyPublish({meta:{...doc.meta,front_page:doc.meta.front_page},deals:rows,spotlight,key:'startup'},rows);
  } else {
    ctx.populateStores();
    ctx.initBrowse();
  }
  const renderWall=ctx.render;
  ctx.render=()=>{renders++;renderWall();};
  ctx.render();ctx.restoreInitialBrowse();
  function nextFrame() {
    clock+=16;frameCount++;
    const work=[...frames.values()];frames.clear();for(const fn of work) fn(clock);
  }
  function flushFrames() { for(let i=0;frames.size && i<10;i++) nextFrame();assert.equal(frames.size,0); }
  flushFrames();
  const apply=(changes={})=>{
    for(const [id,value] of Object.entries(changes)) {
      el(id).value=value;
    }
    fire('filterform:submit',{preventDefault(){}});
  };
  return {ctx,el,fire,apply,events,session,entries,scrolls,nextFrame,flushFrames,
    keys:()=>cards.map(c=>c.key),get renders(){return renders;},get refreshes(){return refreshes;},
    get frameCount(){return frameCount;},get pendingFrames(){return frames.size;},get observers(){return observers.size;},
    shift(top=wallShift,height=cardHeight){wallShift=top;cardHeight=height;},
    top(key){return cards.find(c=>c.key===key).getBoundingClientRect().top;},
    advance(ms){clock+=ms;for(const [id,t] of [...timers]) if(t.at<=clock){timers.delete(id);t.fn();}},
    listenerCount(name){return (listeners['window:'+name] || []).length;},
    get pushCount(){return entries.length-1;},
    navigation(delta){historyIndex+=delta;history.state=entries[historyIndex].state;updateLocation(entries[historyIndex].url);fire('window:popstate');},
    refresh(rows,pending=null){nextRows=rows;pendingRefresh=pending;},
    flushTimers(){const work=[...timers.values()];timers.clear();for(const t of work)t.fn();},
    savedView(on){ctx.savedDeals.view=on;ctx.renderSavedControls();ctx.render();}
  };
}
const keys = rows=>Array.from(rows,d=>d.key);
const flush=async()=>{await new Promise(r=>setImmediate(r));};
// Run the emitted row filler too: it moves DOM nodes after render() returns.
function fillGridRows(markup, cols) {
  const parent={}, parts=markup.split(/<div class="(grid(?: spotlight-grid)?)">/).slice(1), grids=[];
  for(let i=0;i<parts.length;i+=2) {
    const grid={spotlight:parts[i].includes('spotlight-grid'),parentNode:parent,children:[],
      querySelectorAll(selector) {
        assert.equal(selector,':scope > .card');
        return this.children.filter(node=>node.key);
      },
      querySelector(selector) {
        assert.equal(selector,':scope > .gridfill');
        return this.children.find(node=>node.className==='gridfill') || null;
      },
      appendChild(node) {
        node.remove();node.parentNode=this;this.children.push(node);
      },
      get lastElementChild(){return this.children.at(-1);}};
    for(const match of parts[i+1].matchAll(/<article class="card[^"\n]*" data-key="([^"]+)"/g)) {
      grid.children.push({key:match[1],parentNode:grid,remove(){
        this.parentNode.children.splice(this.parentNode.children.indexOf(this),1);
      }});
    }
    grids.push(grid);
  }
  const ctx={scope:{querySelectorAll(selector) {
    assert(['.grid','.grid:not(.spotlight-grid)'].includes(selector));
    return grids.filter(grid=>selector==='.grid' || !grid.spotlight);
  }},getComputedStyle:()=>({gridTemplateColumns:Array(cols).fill('180px').join(' ')}),
    document:{createElement:()=>({style:{},remove(){
      if(this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this),1);
    }})}};
  vm.createContext(ctx);
  vm.runInContext(fragment('  function columns(grid)', '  var pending = false;'),ctx);
  const snapshot=()=>grids.map(grid=>grid.children.map(node=>node.key || 'gridfill'));
  const before=snapshot(), ordinary=()=>grids.filter(grid=>!grid.spotlight).flatMap(grid=>grid.children.filter(node=>node.key).map(node=>node.key));
  const beforeOrdinary=ordinary();
  for(let pass=0;pass<3;pass++) {
    ctx.fillRows();
    grids.forEach((grid,i)=>{
      if(grid.spotlight) assert.deepEqual(snapshot()[i],before[i], 'row filling must never borrow, lend or pad spotlight cards');
    });
    assert.deepEqual(ordinary(),beforeOrdinary,'ordinary cards retain their complete order');
  }
  return grids;
}
(async()=>{
  if(process.argv[4]==='pagination-restore') {
    const source=Array.from({length:180},(_,i)=>row('page-'+i));
    const h=harness({rows:source});
    assert.equal(h.keys().length,60);
    // Observer loading and button loading share showMore; an observer can
    // remove the last button before a queued action runs.
    h.ctx.showMore();assert.equal(h.keys().length,120);
    h.ctx.showMore();assert.equal(h.keys().length,180);
    const shown=h.ctx.state.shown;h.ctx.showMore();assert.equal(h.ctx.state.shown,shown);
    const key=h.keys()[119];h.ctx.window.scrollY=400+119*220+37;h.ctx.rememberBrowse(false);
    const snapshot=h.ctx.browseMemory.entries[h.ctx.browseId];assert.equal(snapshot.anchor.key,key);
    for(const navigation of ['reload','back_forward']) {
      const restored=harness({rows:source,stored:h.session,navigation,historyState:h.ctx.history.state});
      assert.equal(restored.keys().length,180);
      assert(Math.abs(restored.top(key)-snapshot.anchor.offset)<=2);
    }
    return;
  }
  if (['rotation-sparse-promos','rotation-repair-windows'].includes(process.argv[4])) {
    const sparse=process.argv[4]==='rotation-sparse-promos';
    const source=Array.from({length:sparse?24:120},(_,i)=>row(String(i),{
      pick:i+1,category:'cat'+i%7,pick_checkout:i%5===0,
      allure:sparse ? i%5!==0 : i===24 || i===119,
      popular:i%24<6,staple:i%24<4,final_price:i%24===0?120:i%24<5?60:20
    }));
    const banner=row('banner',{store:'Walmart',spotlight:{until:new Date(NOW+86400000).toISOString()}});
    const h=harness({rows:source,spotlight:[banner]});
    let before=[];
    const repair=h.ctx.spaceRotatedPromos;
    h.ctx.spaceRotatedPromos=input=>{before=Array.from(input);return repair(input);};
    for(const seed of [0,1,2,3]) {
      h.ctx.state.seed=seed;h.ctx.state.shown=1000;h.ctx.render();
      assert.equal(h.keys()[0],'banner');
      const visible=h.keys().slice(1).map(key=>source.find(d=>d.key===key));
      for(let i=0;i<visible.length;i++) {
        assert(visible.slice(i,i+5).filter(d=>d.pick_checkout).length<=1,'sparse fallback must validate promo spacing');
        if(visible[i].allure) assert(i>=6 && !visible.slice(Math.max(0,i-5),i).some(d=>d.allure));
      }
      if(sparse) {
        assert.equal(visible.length,1,'impossible promos stay available in search instead of consecutive homepage cards');
        assert.equal(h.ctx.state.deals.length,24);
        const compressed=Array.from({length:120},(_,i)=>row('compressed-'+i,{pick:i+1,pick_checkout:i<24}));
        const fallback=Array.from(repair(compressed));
        assert.deepEqual(keys(fallback),['compressed-0'],'shortened windows must not pull a later window onto the first screen');
      } else {
        assert.deepEqual(keys(visible).sort(),keys(before).sort(),`seed ${seed}: admitted inventory`);
        for(let start=0;start<visible.length;start+=24) {
          const part=visible.slice(start,start+24), original=before.slice(start,start+24);
          assert.deepEqual(keys(part).sort(),keys(original).sort(),'repair preserves membership at every protected window and the rotation boundary');
          for(const field of ['popular','staple'])
            assert.equal(part.filter(d=>d[field]).length,original.filter(d=>d[field]).length);
        }
        assert(!visible.slice(0,24).some(d=>Number(d.key)>=72),'only the first three windows can lead');
      }
      const repeat=h.keys();h.ctx.render();assert.deepEqual(h.keys(),repeat);
    }
    h.el('q').value='Gadget';h.ctx.render();assert.deepEqual(h.keys().filter(key=>key!=='banner').sort(),keys(source).sort());
    return;
  }
  if (process.argv[4]==='shipped-rotation') {
    const rows=JSON.parse(fs.readFileSync(process.argv[5],'utf8'));
    const scenarios=[[24],[0],[25],[...Array(12).keys()],Array.from({length:12},(_,i)=>24+i),
      Array.from({length:12},(_,i)=>i*5),Array.from({length:12},(_,i)=>108+i)];
    let random=13;
    for(let n=0;n<100;n++) {
      const chosen=new Set();
      while(chosen.size<12) {random=(Math.imul(random,1664525)+1013904223)>>>0;chosen.add(random%120);}
      scenarios.push([...chosen]);
    }
    for (const allures of scenarios) {
      const source=rows.map((d,i)=>({...d,allure:d.key==='tail-allure' || allures.includes(i)}));
      const spotlight=[row('banner',{store:'Walmart',item:'Banner',spotlight:{until:new Date(NOW+86400000).toISOString()}})];
      const h=harness({rows:source,spotlight,saved:true});
      let spaced=[];
      const spaceAllure=h.ctx.spaceAllure;
      h.ctx.spaceAllure=input=>{const out=spaceAllure(input);spaced=keys(out);return out;};
      const starts=[];
      for(const seed of [0,1,2,3]) {
        h.ctx.state.seed=seed;h.ctx.state.shown=1000;h.ctx.render();
        assert.equal(h.keys()[0],'banner');
        assert.equal(h.keys().filter(key=>key==='banner').length,1);
        const visible=h.keys().slice(1).map(key=>source.find(d=>d.key===key));
        assert(!visible.some(d=>d.conditions.includes('used') || d.conditions.includes('open_box')));
        assert(!visible.slice(0,120).some(d=>d.click_price_unknown));
        const allure=visible.flatMap((d,i)=>d.allure?[i]:[]);
        assert(allure.every((at,i)=>at>=6 && (!i || at-allure[i-1]>=6)));
        for(let i=0;i<visible.length;i++)
          assert(visible.slice(i,i+5).filter(d=>d.pick_checkout).length<=1,
            `seed ${seed}, slot ${i}, Allure ${allures}: checkout spacing after Allure`);
        const admitted=spaced.map(key=>source.find(d=>d.key===key));
        if(visible.length===spaced.length) {
          assert.deepEqual(keys(visible).sort(),spaced.slice().sort(),`seed ${seed}, Allure ${allures}: repair retains every admitted offer`);
          const end=spaced.findLastIndex(key=>source.find(d=>d.key===key).pick!=null)+1;
          assert.deepEqual(keys(visible.slice(end)),spaced.slice(end),'repair leaves the unpicked tail untouched');
          const unpicked=list=>list.flatMap((key,i)=>source.find(d=>d.key===key).pick==null?[[key,i]]:[]);
          assert.deepEqual(unpicked(keys(visible)),unpicked(spaced),'repair moves only ordinary picks');
          for(const flag of ['gone','click_price_unknown']) {
            const slots=list=>list.flatMap((key,i)=>source.find(d=>d.key===key)[flag]?[i]:[]);
            assert.deepEqual(slots(keys(visible)),slots(spaced),`repair retains ${flag} slots`);
          }
          for(let start=0;start<end;start+=24) {
            assert.deepEqual(keys(visible.slice(start,Math.min(start+24,end))).sort(),
              spaced.slice(start,Math.min(start+24,end)).sort(),'repair never crosses a protected window');
          }
        } else {
          // A policy-filtered window can have more promos than its slots
          // permit. The validated fallback defers unsafe rows, in order.
          assert.deepEqual(keys(visible),spaced.filter(key=>visible.some(d=>d.key===key)));
          const firstDeferred=admitted.findIndex(d=>!visible.includes(d));
          const boundary=Math.ceil((firstDeferred+1)/24)*24;
          assert(visible.every(d=>admitted.indexOf(d)<boundary),'deferral never promotes a later window');
          for(const d of admitted.slice(0,boundary).filter(d=>!visible.includes(d)))
            assert(d.pick_checkout || d.allure || d.gone || d.click_price_unknown,'fallback keeps ordinary inventory within the final window');
          assert.equal(h.ctx.state.deals.length,source.length,'deferred deals remain in the inventory');
        }
        if(visible.length===spaced.length) assert(source.slice(0,120).filter(d=>!d.allure && !d.pick_checkout).every(d=>h.keys().includes(d.key)));
        starts.push(visible[0].key);
        const before=h.keys();h.ctx.render();assert.deepEqual(h.keys(),before);
      }
      assert.equal(new Set(starts.slice(0,3)).size,3);
      assert.equal(starts[0],starts[3]);
      h.ctx.state.catTab=2;h.ctx.render();assert.deepEqual(h.keys().sort(),['open-box','used']);
      h.ctx.state.catTab=0;h.el('q').value='Storage set';h.ctx.render();
      assert.deepEqual(h.keys().sort(),rows.map(d=>d.key).sort());
    }
    console.log('shipped rotation ordering: passed');
    return;
  }
  if (['spotlight-dom','spotlight-grid-fill'].includes(process.argv[4])) {
    const h=harness({rows:doc.deals,spotlight:doc.spotlight,saved:true,realCards:true,rotationStartup:true});
    Object.assign(h.ctx,{toast(){},updatedAgo:()=>'',reorganize(){h.ctx.render();return true;}});
    vm.runInContext(fragment('  function present(', '  function drop('),h.ctx);
    const step=doc.meta.cadence_minutes*60000, start=Date.parse(doc.meta.published_at);
    h.advance(start-NOW+1000);
    h.ctx.clock.lastSeed=null;
    const snapshots=[];
    for(const seed of [0,1,2,3]) {
      const pub=structuredClone(doc), at=new Date(start+seed*step).toISOString();
      pub.meta.published_at=pub.meta.generated_at=at;pub.key='publication-'+seed;
      if(seed) h.advance(step);
      h.ctx.present(pub,pub.deals,seed);
      assert.equal(JSON.stringify(h.ctx.state.spotlight),JSON.stringify(doc.spotlight));
      h.ctx.cardImageIndex=0;
      const banner='<div class="grid spotlight-grid">'+doc.spotlight.map(h.ctx.cardHtml).join('')+'</div>';
      const markup=h.el('content').innerHTML;
      assert(markup.startsWith(banner+'<h2'), 'the spotlight grid contains exactly the published spotlight cards');
      if(process.argv[4]==='spotlight-grid-fill') {
        const cols=Number(process.argv[5]);
        fillGridRows(markup,cols);
        assert.equal(JSON.stringify(h.ctx.state.spotlight),JSON.stringify(doc.spotlight));
        const grid=(rows,spotlight=false)=>'<div class="grid'+(spotlight?' spotlight-grid':'')+'">'+rows.map(h.ctx.cardHtml).join('')+'</div>';
        // Spotlights must not be lenders either, even with multiple banners.
        const spots=Array.from({length:cols+1},(_,i)=>({...doc.spotlight[0],key:'spotlight-'+i}));
        const grids=fillGridRows(grid(doc.deals.slice(0,cols-1))+grid(spots,true),cols);
        if(cols>1) assert(grids[0].children.some(node=>node.className==='gridfill'),'ordinary final-row padding still runs');
        if(cols>1) {
          const ordinary=fillGridRows(grid(doc.deals.slice(0,cols-1))+grid(doc.deals.slice(cols-1,2*cols)),cols);
          assert.deepEqual(ordinary.map(grid=>grid.children.length),[cols,cols],'ordinary grids still borrow to complete rows');
        }
        const reload=harness({rows:pub.deals,spotlight:pub.spotlight,saved:true,realCards:true,rotationStartup:true,stored:h.session});
        reload.advance(start-NOW+seed*step+1000);
        reload.ctx.state.seed=seed;reload.ctx.render();
        assert.equal(JSON.stringify(reload.ctx.state.spotlight),JSON.stringify(doc.spotlight));
        fillGridRows(reload.el('content').innerHTML,cols);
      }
      snapshots.push(markup);
    }
    console.log(JSON.stringify(snapshots));
    return;
  }
  if (process.argv[4]==='spotlight-unplaced') {
    const rows=doc.deals, spotlight=doc.spotlight, spotKey=spotlight[0].key;
    const picks=rows.slice().sort((a,b)=>a.pick-b.pick);
    const h=harness({rows,spotlight,saved:true});
    for(const seed of [0,1,2,3]) {
      h.ctx.state.seed=seed;h.ctx.render();
      assert.equal(h.keys().filter(key=>key===spotKey).length,1,'active spotlight stays visible once');
      assert.equal(h.keys()[0],spotKey,'standalone spotlight keeps its separate banner');
      assert(h.el('content').innerHTML.startsWith('<div class="grid spotlight-grid">'));
      assert.deepEqual(h.keys().slice(1),keys(picks),'a single ordinary window preserves server pick order');
      // Six checkout offers cannot share 25 slots at width 5/cap 1. Keep the
      // standalone banner separate and every sliding pick window checkout-safe.
      for(let i=0;i<picks.length;i++) assert(picks.slice(i,i+5).filter(d=>d.pick_checkout).length<=1);
      assert.deepEqual(Array.from(h.ctx.browseRows,d=>d.key),h.keys());
      const before=h.keys();h.ctx.render();assert.deepEqual(h.keys(),before);
    }
    assert.equal(spotlight.length,1,'rendering does not mutate the active payload');
    console.log('standalone checkout spotlight separate banner: passed');
    return;
  }
  if (process.argv[4]?.startsWith('spotlight-')) {
    const source=JSON.parse(fs.readFileSync(process.argv[5],'utf8'));
    const rows=source.map(d=>row(d.key,d));
    const spotlight=[{...rows[rows.length===24?19:24],spotlight:{until:new Date(NOW+86400000).toISOString()}}];
    delete spotlight[0].pick;delete spotlight[0].pick_checkout; // Separate server serialization.
    const extra=process.argv[4].startsWith('spotlight-extra');
    if(extra) Object.assign(spotlight[0],{key:'extra',pct_at_checkout:null,pick_checkout:true});
    if(['spotlight-extra-other','spotlight-extra-auto'].includes(process.argv[4])) {
      spotlight[0].category=process.argv[4].split('-').at(-1);
    }
    const before=JSON.stringify({rows,spotlight});
    const h=harness({rows,spotlight,saved:true});
    if(process.argv[4]==='spotlight-paginated') {
      h.ctx.state.seed=2;h.ctx.render();
      assert(h.keys().includes(spotlight[0].key),'spotlight pick 25 stays visible at the default 60-card limit, seed 2');
    }
    const off=harness({rows,spotlight,saved:true,template:fs.readFileSync(process.argv[6],'utf8')});
    const ordinary=rows.filter(d=>d.key!==spotlight[0].key);
    const plain=harness({rows:ordinary,saved:true});
    const arrivals=process.argv[4].startsWith('spotlight-new');
    if(arrivals) h.ctx.state.newKeys=plain.ctx.state.newKeys={[spotlight[0].key]:true,'31':true};
    const banner=wall=>wall.el('content').innerHTML.match(/<div class="grid spotlight-grid">[\s\S]*?<\/div>/g) || [];
    const starts=[];
    for(const seed of [0,1,2,3]) {
      for(const shown of [60,200]) {
        for(const wall of [h,off,plain]) {wall.ctx.state.shown=shown;wall.ctx.state.seed=seed;wall.ctx.render();}
        const rendered=h.keys(), picks=rendered.slice(1);
        assert.deepEqual(banner(h),banner(off),'rotation preserves the no-rotation spotlight banner exactly');
        assert.equal(banner(h).length,1);
        assert.equal(rendered[0],spotlight[0].key,'spotlight keeps its original separate slot before pagination');
        assert.equal(rendered.filter(key=>key===spotlight[0].key).length,1,'active spotlight is rendered once');
        assert.deepEqual(picks,plain.keys(),'only ordinary picks enter rotation and spacing repair');
        assert.equal(picks.length,Math.min(shown,ordinary.length));
        assert.deepEqual(Array.from(h.ctx.browseRows,d=>d.key).sort(),[spotlight[0].key,...keys(ordinary)].sort());
        for(let i=0;i<picks.length;i++) {
          assert(picks.slice(i,i+5).filter(key=>ordinary.find(d=>d.key===key).pick_checkout).length<=1,
            `seed ${seed}, ordinary slot ${i}: checkout spacing`);
        }
        if(shown===60) starts.push(picks[0]);
        else {
          assert.deepEqual([...rendered].sort(),[spotlight[0].key,...keys(ordinary)].sort());
          for(let i=0;i<ordinary.length;i+=24) {
            assert(ordinary.some((_,j)=>j%24===0 &&
              JSON.stringify(keys(ordinary.slice(j,j+24)).sort())===JSON.stringify(picks.slice(i,i+24).sort())),
              'ordinary aligned window membership stays intact');
          }
        }
        const stable=h.keys();h.ctx.render();assert.deepEqual(h.keys(),stable);
      }
    }
    if(process.argv[4]==='spotlight-off' || process.argv[4]==='spotlight-single') {
      assert.equal(new Set(starts).size,1);
    } else {
      assert.equal(new Set(starts.slice(0,3)).size,3,'the first ordinary card rotates even with an active spotlight');
      assert.equal(starts[0],starts[3]);
    }
    if(arrivals) {
      h.ctx.state.seed=1;h.ctx.state.newKeys={};h.ctx.render();
      assert.equal(h.keys()[1],'31','ordinary arrivals retain priority after newKeys resets');
      h.savedView(true);h.savedView(false);
      assert.equal(h.keys()[1],'31','Saved return retains ordinary arrival priority');
      assert.deepEqual(banner(h),banner(off));
    }
    if(process.argv[4]==='spotlight-views') {
      for(const view of [{catTab:1},{catTab:3},{sortKey:'fresh'},{sortKey:'price'},{sortDir:1}]) {
        for(const wall of [h,off]) {
          Object.assign(wall.ctx.state,{catTab:0,sortKey:'top',sortDir:-1},view);wall.ctx.render();
        }
        assert.deepEqual(h.keys(),off.keys());assert.deepEqual(banner(h),banner(off));
      }
      for(const controls of [{storesel:'store4'},{q:'Rotation product 24'}]) {
        for(const wall of [h,off]) {
          Object.assign(wall.ctx.state,{catTab:0,sortKey:'top',sortDir:-1});
          wall.el('storesel').value='';wall.el('q').value='';
          for(const [id,value] of Object.entries(controls)) wall.el(id).value=value;
          wall.ctx.render();
        }
        assert.deepEqual(h.keys(),off.keys());assert.deepEqual(banner(h),banner(off));
      }
      h.el('q').value='';h.savedView(true);assert.deepEqual(h.keys(),['0','1']);h.savedView(false);
      h.ctx.state.spotlight=[];h.ctx.render();assert.equal(h.keys().length,rows.length);
    }
    assert.equal(JSON.stringify({rows,spotlight}),before,'rotation never mutates spotlight or deal payloads');
    console.log(process.argv[4]+': passed');
    return;
  }
  if (process.argv[4]==='rotation') {
    const rows=Array.from({length:120},(_,i)=>row(String(i),{pick:i+1,category:'cat'+i%5}));
    const first=harness({rows,saved:true});
    first.ctx.state.seed=1;first.ctx.state.newKeys={'31':true};
    first.ctx.render();first.ctx.rememberBrowse(false);
    const before=first.keys();assert.equal(before[0],'31');
    const refresh=harness({rows,saved:true,rotationStartup:true,stored:first.session,
      historyState:first.ctx.history.state,navigation:'reload'});
    assert.deepEqual(refresh.keys(),before);
    assert.equal(refresh.ctx.state.seed,1);
    refresh.ctx.state.seed=2;refresh.ctx.state.newKeys={};refresh.ctx.render();
    refresh.ctx.rememberBrowse(true);
    assert.equal(refresh.keys()[0],'48');
    refresh.navigation(-1);
    assert.deepEqual(refresh.keys(),before);
    for(let seed=3;seed<30;seed++) {refresh.ctx.state.seed=seed;refresh.ctx.render();}
    const records=JSON.parse(refresh.session.get('wp.drop-picks'));
    assert.equal(records.length,20);
    assert.equal(new Set(records.map(entry=>entry.signature)).size,20);
    console.log('drop rotation Saved startup, refresh, Back and bounded cache: passed');
    return;
  }
  // Price limits use known prices, independent of condition or checkout steps.
  for (const ceiling of ['', '25']) {
    const h=harness();h.apply({pricepreset:ceiling});
    const expected=mixed.filter(d=>!ceiling || (typeof d.final_price==='number' && d.final_price<=25));
    assert.deepEqual(keys(h.ctx.filtered()),keys(expected));
    assert.equal(h.ctx.state.meta.floors.min_pct,70);assert.equal(h.ctx.state.meta.floors.site_show_from_pct,50);
  }
  const h=harness();
  h.el('storesel').value='walmart';h.fire('storesel:change');
  h.apply({pricepreset:'25'});assert.deepEqual(keys(h.ctx.filtered()),['steps','other']);
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
  spacing.apply({pricepreset:'25'});
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
  // URL validation uses the actual counted store list, categories, sorts
  // and configured presets. Neither custom input nor queries can enter it.
  const valid=harness({search:'?store=amazon&category=home-kitchen&sort=price&price=25&new=1&steps=none'});
  assert.equal(valid.ctx.state.sortKey,'price');assert.equal(valid.ctx.state.catTab,3);
  assert.deepEqual(keys(valid.ctx.filtered()),['new','used','box','renewed','unknown','plain-unknown','gone']);
  assert(!/[?&](new|steps)=/.test(valid.ctx.browseURL()));
  const legacy=harness({search:'?new=1&steps=none'});
  assert.deepEqual(keys(legacy.ctx.filtered()),keys(mixed));
  assert.equal(legacy.ctx.browseURL(),'/');
  const oldSnapshot={filters:{preset:'25',max:'',newOnly:true,plainOnly:true}};
  legacy.ctx.applyBrowseSnapshot(oldSnapshot,false);
  assert.deepEqual(Object.keys(legacy.ctx.browseFilters).sort(),['max','preset']);
  assert(legacy.ctx.filtered().some(d=>d.key==='used'));
  assert(legacy.ctx.filtered().some(d=>d.key==='steps'));
  const amazon=harness({search:'?store=amazon&sort=top#content'});
  assert.equal(amazon.el('storesel').value,'amazon');assert.equal(amazon.ctx.state.sortKey,'top');
  const bad=harness({search:'?store=ghost&category=ghost&sort=constructor&price=12&new=yes&steps=false&q=PRIVATE&max=123'});
  assert.equal(bad.ctx.browseURL(),'/');assert.equal(bad.el('q').value,'');assert.equal(bad.ctx.eligibilityActive(),false);
  const repeated=harness({search:'?store=amazon&store=walmart&price=25&price=10&sort=price&sort=top&new=1&new=0&steps=none&steps=none'});
  assert.equal(repeated.ctx.browseURL(),'/');
  // Private text replaces the current history state, with no entry per
  // keystroke, and never appears in history.state or event parameters.
  valid.el('q').value='Gadget';valid.ctx.updateSearch();
  valid.apply({pricepreset:'',maxprice:'23.45'});
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
  // A scroll measured before layout settles is only the first step. Both
  // the desktop's -448px shift and +7px per-card placeholders must converge.
  function correctionHarness() {
    const h=harness({rows:many});h.ctx.state.shown=180;h.ctx.render();
    const key=h.keys()[120];
    h.ctx.window.scrollY=400+120*220+37;h.ctx.rememberBrowse(false);
    const snapshot=h.ctx.browseMemory.entries[h.ctx.browseId];
    assert.equal(snapshot.anchor.key,key);
    h.ctx.restoreBrowsePosition(snapshot,'reload');
    return {h,key,snapshot};
  }
  const correction=correctionHarness();
  correction.h.shift(-448);correction.h.nextFrame();
  assert(Math.abs(correction.h.top(correction.key)-correction.snapshot.anchor.offset)<=2);
  correction.h.shift(-448,227);correction.h.nextFrame();correction.h.flushFrames();
  assert(Math.abs(correction.h.top(correction.key)-correction.snapshot.anchor.offset)<=2);
  assert(correction.h.frameCount<=6);assert.equal(correction.h.pendingFrames,0);
  // Our queued scroll event is not user input. A late Saved rebuild replaces
  // card nodes after the stable frames; support's hidden changes also notify.
  correction.h.fire('window:scroll');assert.equal(correction.h.observers,1);
  correction.h.advance(100);correction.h.shift(-900,227);correction.h.ctx.render();
  correction.h.flushFrames();
  assert(Math.abs(correction.h.top(correction.key)-correction.snapshot.anchor.offset)<=2);
  assert(correction.h.frameCount<=7);
  assert.equal(correction.h.events.filter(e=>e.name==='back_restore').length,1);
  correction.h.advance(500);assert.equal(correction.h.observers,0);
  for(const name of ['wheel','touchstart','touchmove','pointerdown','keydown']) assert.equal(correction.h.listenerCount(name),0);
  // An unsettled wall cannot spin forever, even if every frame moves it.
  const bounded=correctionHarness();
  for(let i=1;i<=6;i++){bounded.h.shift(-100*i);bounded.h.nextFrame();}
  assert.equal(bounded.h.pendingFrames,0);assert.equal(bounded.h.frameCount,6);
  bounded.h.shift(-900);bounded.h.ctx.render();bounded.h.flushFrames();
  assert.equal(bounded.h.frameCount,7);
  assert(Math.abs(bounded.h.top(bounded.key)-bounded.snapshot.anchor.offset)<=2);
  bounded.h.shift(-1200);bounded.h.ctx.render();assert.equal(bounded.h.pendingFrames,0);
  assert.equal(bounded.h.events.filter(e=>e.name==='back_restore').length,1);
  const clamped=correctionHarness();
  clamped.h.shift(-448);clamped.h.ctx.render();clamped.h.ctx.window.scrollY-=80;
  clamped.h.fire('window:scroll');clamped.h.flushFrames();
  assert(Math.abs(clamped.h.top(clamped.key)-clamped.snapshot.anchor.offset)<=2);
  const interruptedRedraw=correctionHarness();
  interruptedRedraw.h.ctx.render();interruptedRedraw.h.fire('window:wheel');
  interruptedRedraw.h.shift(-448);interruptedRedraw.h.flushFrames();
  assert.equal(interruptedRedraw.h.scrolls.length,1);
  // Input cancels before the next correction and also disconnects the late
  // redraw watch. Scrollbar/assistive scroll works without a preceding wheel.
  for(const input of ['scroll','wheel','touchstart','touchmove','pointerdown','keydown','pagehide']) {
    const {h,key}=correctionHarness();h.shift(-448);h.nextFrame();
    if(input==='scroll') h.ctx.window.scrollY+=1;
    h.fire('window:'+input);
    const count=h.scrolls.length, userY=h.ctx.window.scrollY;
    h.shift(-448,227);h.ctx.render();h.flushFrames();h.advance(600);
    assert.equal(h.ctx.window.scrollY,userY,input);assert.equal(h.scrolls.length,count,input);
    assert.equal(h.observers,0,input);assert.equal(h.ctx.browseCorrection,null,input);
    assert.equal(h.events.filter(e=>e.name==='back_restore').length,1,input);
    assert(Number.isFinite(h.top(key)));
  }
  const expired=correctionHarness();expired.h.advance(501);expired.h.shift(-448);expired.h.flushFrames();
  assert.equal(expired.h.frameCount,0);assert.equal(expired.h.observers,0);
  const superseded=correctionHarness();
  superseded.h.ctx.restoreBrowsePosition(superseded.snapshot,'history');
  assert.equal(superseded.h.observers,1);assert.equal(superseded.h.listenerCount('wheel'),1);
  superseded.h.shift(-448);superseded.h.flushFrames();
  assert(Math.abs(superseded.h.top(superseded.key)-superseded.snapshot.anchor.offset)<=2);
  // Placeholder sizing uses the content box of a fully rendered card.
  // Turning the CSS off starts no observers; redraw and responsive resize
  // replace the observed node/height without touching any card markup.
  const sizing=harness();let resize, observedCard, measured;
  sizing.ctx.ResizeObserver=class {
    constructor(callback){resize=callback;}
    observe(card){observedCard=card;}
    disconnect(){observedCard=null;}
  };
  sizing.el('content').style={setProperty(name,value){assert.equal(name,'--offscreen-card-height');measured=value;}};
  sizing.el('content').querySelector=selector=>selector==='.grid:not(.spotlight-grid) > .card' ? sizing.ctx.cardEls()[0] : null;
  sizing.ctx.getComputedStyle=()=>({getPropertyValue:()=>''});
  vm.runInContext(fragment('  function observeOffscreenCards()', '  document.getElementById("load-retry")'),sizing.ctx);
  sizing.ctx.observeOffscreenCards();assert.equal(sizing.observers,0);assert.equal(resize,undefined);
  sizing.ctx.getComputedStyle=()=>({getPropertyValue:()=>'auto'});
  sizing.ctx.observeOffscreenCards();assert.equal(sizing.observers,1);
  const originalCard=observedCard;
  for(const height of [373,405,419.5]) {
    resize([{contentRect:{height}}]);assert.equal(measured,height+'px');
  }
  resize([{contentRect:{height:0}}]);assert.equal(measured,'419.5px');
  sizing.ctx.render();assert.notEqual(observedCard,originalCard);
  assert.equal(observedCard,sizing.ctx.cardEls()[0]);
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
  const denied=harness({blocked:true});denied.apply({maxprice:'15'});
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
  spot.apply({maxprice:''});assert(spot.keys().includes('today'));
  spot.ctx.state.spotlight[0].conditions=null;
  spot.ctx.state.spotlight[0].needs_steps=null;
  spot.ctx.render();assert(spot.keys().includes('today'));
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
  // A Pinterest OAuth return keeps ?code= in the address bar for copying.
  const oauth=harness({search:'?code=abc123&state=demo'});
  oauth.apply({maxprice:'12'});oauth.fire('window:pagehide');
  assert.equal(oauth.ctx.location.search,'?code=abc123&state=demo');
  console.log('price/eligibility intersections, private browsing history, current-fact restoration and sheet controls passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
