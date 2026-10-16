// Exercise the generated transport and existing adoption/clock paths without sockets.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto, createHash} = require('node:crypto');
const {gzipSync} = require('node:zlib');
const {performance} = require('node:perf_hooks');
const home = fs.readFileSync(process.argv[2], 'utf8');
const original = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const NOW = Date.parse(original.meta.published_at);
const clone = v => JSON.parse(JSON.stringify(v));
const raw = v => typeof v === 'string' ? v : JSON.stringify(v);
const hash = v => createHash('sha256').update(raw(v)).digest('hex');
const manifest = doc => ({schema:1, sha256:hash(doc), bytes:Buffer.byteLength(raw(doc)),
  published_at:doc.meta.published_at, site_version:doc.meta.site_version});
const snippet = (start,end) => {
  const a=home.indexOf(start), b=home.indexOf(end,a);
  assert(a>=0 && b>a, start); return home.slice(a,b);
};
const publication = (seconds=0, modify=()=>{}) => {
  const doc=clone(original);
  doc.meta.published_at=new Date(NOW+seconds*1000).toISOString();
  doc.meta.generated_at=doc.meta.published_at;
  modify(doc); return doc;
};
const deferred = () => {let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const flush = async () => {await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r));};
async function until(predicate) {
  for(let i=0;i<1000;i++) {
    if(predicate())return;
    await new Promise(r=>setTimeout(r,1));
  }
  throw Error('Fixture request did not reach its gate');
}

function harness({crypto=true, initial=publication(), realMember=false}={}) {
  const listeners={}, els={}, timers=new Map(), requests=[], notices=[], events=[], reloads=[], counters={parse:0,hash:0};
  const queues={public:[],manifest:[]};
  let time=NOW, serial=0, parses=0, digestCalls=0, rendered=0, adoptions=0, cancelled=0, memberCalls=0;
  let current=initial, memberGate=null, nextMember=realMember?[{key:'private-current',item:'Member',store:'Store'}]:null;
  const fire=(name,e={})=>(listeners[name]||[]).forEach(fn=>fn(e));
  const listen=(name,fn)=>{(listeners[name]||=[]).push(fn);};
  function el(id) {
    return els[id] ||= {hidden:true,disabled:false,textContent:'',innerHTML:id==='content'?'static credited deals':'',
      addEventListener(type,fn){listen(id+':'+type,fn);},querySelector:()=>id==='content'?{}:null};
  }
  class FixedDate extends Date {constructor(...args){super(...(args.length?args:[time]));}static now(){return time;}}
  const c={Date:FixedDate,TextEncoder,TextDecoder,Uint8Array,Array,Number,JSON:{stringify:JSON.stringify,parse(text){
    const at=performance.now();const result=JSON.parse(text);counters.parse+=performance.now()-at;parses++;return result;}},
    state:{deals:[],teaser:[],meta:{},dataKey:'',newKeys:{},dropKeys:{},seed:0,sortKey:'top'},auth:{token:'',checking:false},
    clock:{tag:'',waiting:0,step:900000,phase:0,lastSeed:null},
    TIMEOUT:{},DROP_FETCH_MS:2500,DROP_MAX_MS:3000,loadedOnce:false,
    document:{hidden:false,getElementById:el,addEventListener(type,fn){listen('document:'+type,fn);}},
    window:{addEventListener(type,fn){listen('window:'+type,fn);}},
    setTimeout(fn,delay){timers.set(++serial,{fn,delay});return serial;},clearTimeout(id){timers.delete(id);},setInterval(){},
    noop(){},track:name=>events.push(name),
    token:()=>c.auth.token,isMember:()=>!!c.state.me?.member,openAccess:()=>!!c.state.meta.open_access,
    apiUrl:m=>(m||c.state.meta).api_url||'',
    dataKey:m=>(m.last_run?.run_key||'')+'|'+(m.generated_at||''),publishTime:m=>Date.parse(m.published_at),
    dealKey:d=>d.key,keySet:ds=>Object.fromEntries(ds.map(d=>[d.key,true])),
    openKeys:ds=>Object.fromEntries(ds.filter(d=>!d.locked).map(d=>[d.key,true])),
    maybeReloadForNewVersion:m=>reloads.push(m.site_version),
    syncClock(){c.clock.phase=Date.parse(c.state.meta.published_at)||0;},
    markIndex:(now,phase,step)=>Math.floor((now-phase)/step),
    nextMarkAfter:(now,phase,step)=>(Math.floor((now-phase)/step)+1)*step+phase,
    populateStores(){adoptions++;},refreshStores(){},sinceLastVisit(){},initBrowse(){},renderTabs(){},
    rememberSeen(){},renderMeta(){},restoreInitialBrowse(){},openFromHash(){},paintClock(){},
    initEngagement(){},reconcileSaved(pub){c.savedPublic=pub;},savedOn:()=>false,savedDeals:{view:false},repaintSaved(){},
    restoreBrowseOnReturn(){return c.refreshOnReturn();},
    render(){rendered++;el('content').innerHTML=JSON.stringify(c.state.deals);},
    reorganize(){c.render();return true;},toast:text=>notices.push(text),plural:(n,w)=>n+' '+w,
    memberDeals:async()=>{memberCalls++;const value=nextMember;if(memberGate) await memberGate.promise;return value;},
    showDeals(full){c.auth.checking=false;c.state.deals=full&&c.token()?full:c.state.teaser;c.render();},
    async fetch(url,options) {
      if(url.includes('/api/')) {
        assert.equal(options.cache,'no-store');requests.push({kind:'member',url,options});
        if(memberGate) await memberGate.promise;
        return {ok:true,status:200,json:async()=>url.endsWith('/api/me')?{member:true}:{deals:nextMember}};
      }
      const kind=url.startsWith('publish-manifest')?'manifest':'public';
      assert.equal(options.cache,kind==='manifest'?'no-cache':'no-store');
      if(kind==='manifest') assert.equal(url,'publish-manifest.json');
      const spec=queues[kind].shift()||{body:kind==='manifest'?manifest(current):current};
      requests.push({kind,url,options});
      if(spec.pending) await spec.pending.promise;
      if(spec.error) throw Error('offline');
      const content=spec.body===undefined?'':raw(spec.body);
      const headers=new Headers(spec.headers===undefined?{'etag':'"test"'}:spec.headers);
      if(kind==='public') return {ok:!spec.status||spec.status<400,headers,
        async text(){if(spec.textPending) await spec.textPending.promise;return content;}};
      const chunks=spec.chunks||[Buffer.from(content)], stream=new ReadableStream({
        start(controller){for(const part of chunks)controller.enqueue(new Uint8Array(part));controller.close();},
        cancel(){cancelled++;}});
      return new Response(stream,{status:spec.status||200,headers});
    }
  };
  if(crypto)c.crypto={subtle:{async digest(...args){const at=performance.now();digestCalls++;
    if(crypto==='broken')throw Error('unavailable');
    const result=await webcrypto.subtle.digest(...args);counters.hash+=performance.now()-at;return result;}}};
  vm.createContext(c);
  if(realMember) {
    c.auth.token='login';
    vm.runInContext(snippet('  function api(path,','  // A JSON POST'),c);
    vm.runInContext(snippet('  function fetchMe(','  function syncSubbar()'),c);
  }
  vm.runInContext(snippet('  var MANIFEST_REFRESH =','  // While a drop waits,'),c);
  vm.runInContext(snippet('  function poll()','  // How old the list'),c);
  vm.runInContext(snippet('  function updatedAgo()','  function loadData()'),c);
  vm.runInContext(snippet('  // A restored or long-hidden page','  // Easy sorting:'),c);
  vm.runInContext(snippet('  function withTimeout(','  // deals.json,'),c);
  // The generated script preserves the original static-fallback status and Retry.
  vm.runInContext(snippet('  function loadData()','})();'),c);
  return {c,requests,notices,events,reloads,counters,el,fire,queues,
    get parsed(){return parses;},get hashed(){return digestCalls;},get rendered(){return rendered;},
    get adoptions(){return adoptions;},get cancelled(){return cancelled;},get memberCalls(){return memberCalls;},
    now(value){time=value;},advance(ms){time+=ms;},set(doc){current=doc;},
    queue(kind,spec){queues[kind].push(spec);},
    member(value,gate=null){nextMember=value;memberGate=gate;},
    async ready(){await c.loadRequest;await flush();},
    async refresh(){const pub=await c.fetchPublish();await c.merge(pub);return pub;},
    count(kind){return requests.filter(r=>r.kind===kind).length;},
    runTimers(){const work=[...timers.values()];timers.clear();work.forEach(t=>t.fn());}
  };
}

async function transport() {
  const h=harness();await h.ready();
  assert(h.c.loadedOnce);assert.equal(h.count('public'),1);assert.equal(h.count('manifest'),0);
  assert.equal(h.c.manifestSnapshot.hash,hash(publication()));
  assert.equal(h.adoptions,1);assert.equal(h.el('load-status').hidden,true);
  const snapshot=h.c.manifestSnapshot.pub, seed=h.c.state.seed, clock={...h.c.clock}, notices=h.notices.length;
  await h.refresh();await h.refresh();
  assert.equal(h.count('public'),1);assert.equal(h.count('manifest'),2);assert.equal(h.hashed,1);
  assert.equal(h.c.manifestSnapshot.pub,snapshot);assert.equal(h.c.state.seed,seed);
  assert.deepEqual({...h.c.clock},clock);assert.equal(h.notices.length,notices);
  // An unchanged scheduled drop neither presents nor advances the accepted seed.
  h.c.drop(NOW+900000);await flush();
  assert.equal(h.c.state.seed,seed);assert.equal(h.notices.length,notices);
  // Changed facts are complete snapshots: both reprice directions, removals, safety and spotlight.
  const variants=[
    d=>{d.deals[0].final_price=3;},d=>{d.deals[0].final_price=80;},
    d=>{d.deals.splice(0,1);},d=>{d.deals[0].status='dead';d.deals[0].gone=true;d.deals[0].url='';},
    d=>{d.deals[0]={key:d.deals[0].key,store:'Amazon',locked:true};d.meta.open_access=false;},
    d=>{d.deals[0].price_unconfirmed=true;d.deals[0].checkout_price={value:90,kind:'code'};},
    d=>{d.deals=[];},d=>{d.spotlight=[{...d.deals[0],spotlight:{until:new Date(NOW+86400000).toISOString()}}];},
    d=>{d.meta.site_version='new-version';},d=>{d.deals[0].item='Café & Thé';}
  ];
  for(let i=0;i<variants.length;i++) {
    const doc=publication(i+1,variants[i]);h.set(doc);await h.refresh();
    assert.equal(JSON.stringify(h.c.state.deals),JSON.stringify(doc.deals));
    assert.equal(JSON.stringify(h.c.state.spotlight),JSON.stringify(doc.spotlight||[]));
    assert.equal(h.c.manifestSnapshot.hash,hash(doc));
  }
  const n=h.count('public');h.advance(300000);await h.refresh();
  assert.equal(h.count('public'),n+1); // stale matching manifest cannot defeat backstop
  h.advance(-1);await h.refresh();assert.equal(h.count('public'),n+2); // clock rollback forces full
  const empty=harness({initial:publication(0,d=>{d.deals=[];})});await empty.ready();
  assert(empty.c.loadedOnce);assert.equal(empty.c.state.deals.length,0);assert.equal(empty.el('load-status').hidden,true);
  return h;
}

async function failures() {
  const valid=manifest(publication());
  const bad=[{status:404},{status:500},{error:true},{body:'{'},{body:null},{body:[]},
    {body:{...valid,schema:2}},{body:{...valid,schema:'1'}},{body:{...valid,sha256:'z'.repeat(64)}},
    {body:{...valid,sha256:'a'.repeat(63)}},{body:{...valid,bytes:0}},{body:{...valid,bytes:1.5}},
    {body:{...valid,bytes:999999999}},{body:{...valid,published_at:'today'}},
    {body:{...valid,published_at:'2026-02-30T20:15:00Z'}},
    {body:{...valid,published_at:new Date(NOW+301000).toISOString()}},
    {body:{...valid,site_version:''}},{body:{...valid,keys:['forbidden']}},
    {body:valid,headers:{}},{body:valid,headers:{etag:'"x"','content-length':'999999'}},
    {body:valid,headers:{etag:'"x"','content-length':'invalid'}},
    {body:' '.repeat(1025)}, // no Content-Length: bounded before parsing
    {chunks:[Buffer.from(' '.repeat(600)),Buffer.from(' '.repeat(600))]},
    {chunks:[Buffer.from([0xff,0xfe])]}];
  for(const failure of bad) {
    const h=harness();await h.ready();h.queue('manifest',failure);
    const parsed=h.parsed;await h.refresh();
    assert.equal(h.count('manifest'),1);assert.equal(h.count('public'),2);
    assert.equal(h.c.manifestSnapshot.hash,valid.sha256);
    if(failure.chunks?.[0]?.length===600 || failure.body?.length===1025)assert.equal(h.parsed,parsed+1);
  }
  // Fallback exhaustion performs one full GET, preserves current facts/digest, then permits retry.
  for(const failure of [{status:500},{error:true},{body:'{'},{body:{meta:{},deals:[]}},
    {body:{meta:[],deals:[]}},{body:{meta:publication().meta,deals:null}}]) {
    const h=harness();await h.ready();const saved=h.c.manifestSnapshot;
    h.queue('manifest',{error:true});h.queue('public',failure);
    await assert.rejects(h.refresh());assert.equal(h.count('public'),2);
    assert.equal(h.c.manifestSnapshot,saved);assert.equal(h.c.manifestFlight,null);
    assert.equal(h.notices.length,0);await h.refresh();assert.equal(h.count('public'),2);
  }
  for(const available of [false,'broken']) {
    const h=harness({crypto:available});await h.ready();assert(h.c.loadedOnce);
    await h.refresh();await h.refresh(); // ordinary identical full loads must remain usable
    assert.equal(h.count('public'),3);assert.equal(h.notices.length,0);
    h.set(publication(1));await h.refresh();
    assert.equal(h.count('public'),4);assert.equal(h.count('manifest'),0);
    assert.equal(h.c.manifestSnapshot.hash,null);
    h.set(publication(1,d=>{d.deals=[];}));await assert.rejects(h.refresh(),/Conflicting/);
    h.set(publication());await assert.rejects(h.refresh(),/Regressive/);
  }
  for(const missing of ['TextEncoder','TextDecoder']) {
    const h=harness();h.c[missing]=undefined;await h.ready();await h.refresh();
    assert.equal(h.count('manifest'),0);assert.equal(h.count('public'),2);assert.equal(h.notices.length,0);
  }
  const intermittent=harness();await intermittent.ready();const identity=intermittent.c.state.dataKey;
  const working=intermittent.c.crypto;
  intermittent.c.crypto=undefined;await intermittent.refresh();
  assert.equal(intermittent.c.state.dataKey,identity);assert.equal(intermittent.notices.length,0);
  intermittent.c.crypto=working;await intermittent.refresh();
  assert.equal(intermittent.c.state.dataKey,identity);assert.equal(intermittent.notices.length,0);
  // Validators can be Last-Modified alone; they are never a price-body digest.
  const modified=harness();await modified.ready();
  modified.queue('manifest',{body:valid,headers:{'last-modified':'Thu, 15 Oct 2026 20:15:00 GMT'}});
  await modified.refresh();assert.equal(modified.count('public'),1);
  // First-load validation failure keeps the credited static content and Retry recovers once.
  for(const initial of ['{',{meta:{},deals:null},{meta:{published_at:'wrong'},deals:[]}]) {
    const h=harness({initial});await h.ready();assert.equal(h.c.loadedOnce,false);
    assert.equal(h.el('content').innerHTML,'static credited deals');assert.equal(h.el('load-status').hidden,false);
    assert.equal(h.c.manifestSnapshot,null);h.set(publication());await h.c.loadData();
    assert(h.c.loadedOnce);assert.equal(h.adoptions,1);assert.equal(h.c.manifestSnapshot.hash,valid.sha256);
  }
}

async function races() {
  // Manifest older than body, then newer than body: remember actual bytes only.
  const h=harness();await h.ready();const a=publication(),b=publication(1),c=publication(2);
  h.queue('manifest',{body:manifest(b)});h.queue('public',{body:c});await h.refresh();
  assert.equal(h.c.manifestSnapshot.hash,hash(c));assert.equal(h.c.state.meta.published_at,c.meta.published_at);
  h.queue('manifest',{body:manifest(publication(3))});h.queue('public',{body:b});
  await assert.rejects(h.refresh(),/Regressive/);assert.equal(h.c.manifestSnapshot.hash,hash(c));
  h.queue('manifest',{body:manifest(b)});h.queue('public',{body:c});await h.refresh();
  assert.equal(h.c.manifestSnapshot.hash,hash(c));
  h.queue('manifest',{body:manifest(publication(3))});h.queue('public',{body:publication(2,d=>d.deals=[])});
  await assert.rejects(h.refresh(),/Conflicting/);assert.equal(h.c.manifestSnapshot.hash,hash(c));
  assert.equal(JSON.stringify(h.c.state.deals),JSON.stringify(c.deals));
  // Hash the exact response text, including formatting and UTF-8, rather
  // than the parsed or filtered snapshot or the manifest's claimed body.
  const formatted=publication(4,d=>{d.deals[0].item='Café & Thé';d.deals.push({key:'malformed'});});
  const text=JSON.stringify(formatted,null,2)+'\n';
  h.queue('manifest',{body:manifest(publication(5))});h.queue('public',{body:text});await h.refresh();
  assert.equal(h.c.manifestSnapshot.hash,hash(text));
  assert.equal(h.c.manifestSnapshot.bytes,Buffer.byteLength(text));
  assert.equal(h.c.state.deals.length,formatted.deals.length-1);
  h.queue('manifest',{body:{...manifest(formatted),sha256:hash(text),bytes:Buffer.byteLength(text)}});
  const count=h.count('public');await h.refresh();assert.equal(h.count('public'),count);
  // Coalesced refreshes and existing drop/return/poll callers present once.
  const j=harness();await j.ready();j.set(b);const pending=deferred();j.queue('manifest',{body:manifest(b),pending});
  const p=j.c.fetchPublish(),q=j.c.fetchPublish();assert.equal(p,q);
  const merges=[p.then(j.c.merge),q.then(j.c.merge)];
  j.c.drop(NOW+900000);j.c.poll();const returned=j.c.refreshOnReturn();
  pending.resolve();await Promise.all([...merges,returned]);await flush();
  assert.equal(j.count('public'),2);assert.equal(j.count('manifest'),1);
  assert.equal(j.notices.length,1);assert.equal(j.c.state.dataKey,j.c.manifestSnapshot.pub.key);
  const seed=j.c.state.seed;await j.refresh();assert.equal(j.notices.length,1);assert.equal(j.c.state.seed,seed);
  // Reverse completion across pagehide: the invalidated older body cannot adopt or poison the digest.
  const k=harness();await k.ready();k.c.invalidateManifest();const wait=deferred();
  k.queue('public',{body:b,pending:wait});const older=k.refresh();const rejected=assert.rejects(older,/context/);
  await flush();
  k.fire('window:pagehide');k.queue('public',{body:c});await k.refresh();wait.resolve();await rejected;
  assert.equal(k.c.manifestSnapshot.hash,hash(c));assert.equal(k.c.manifestFlight,null);
  // A response which crosses the wall-time backstop forces full even though its hash matches.
  const slow=harness();await slow.ready();const pause=deferred();slow.queue('manifest',{body:manifest(a),pending:pause});
  const underway=slow.refresh();slow.advance(300001);pause.resolve();await underway;
  assert.equal(slow.count('public'),2);
  // A stale matching manifest can suppress a changed body only until the
  // wall-time backstop, which fetches and adopts the actual current body.
  const stale=harness();await stale.ready();stale.set(b);
  stale.queue('manifest',{body:manifest(a)});await stale.refresh();
  assert.equal(stale.count('public'),1);assert.equal(stale.c.state.meta.published_at,a.meta.published_at);
  stale.advance(300000);await stale.refresh();
  assert.equal(stale.count('public'),2);assert.equal(stale.c.manifestSnapshot.hash,hash(b));
  // A new manifest served before its body never attaches its claimed hash
  // to the still-current body; the next trigger can retry the new publish.
  const ahead=harness();await ahead.ready();ahead.queue('manifest',{body:manifest(b)});
  ahead.queue('public',{body:a});await ahead.refresh();assert.equal(ahead.c.manifestSnapshot.hash,hash(a));
  ahead.set(b);await ahead.refresh();assert.equal(ahead.c.manifestSnapshot.hash,hash(b));
  // A delayed obsolete merge must not reconcile Saved against older prices,
  // even when the main wall correctly rejects that publication as not new.
  const saved=harness();await saved.ready();const obsolete=saved.c.manifestSnapshot.pub;
  saved.set(b);await saved.refresh();const currentSaved=saved.c.savedPublic;
  await saved.c.merge(obsolete);assert.equal(saved.c.savedPublic,currentSaved);
}

async function lifecycle() {
  const h=harness();await h.ready();h.c.document.hidden=true;h.fire('document:visibilitychange');
  h.advance(300001);h.set(publication(1));h.c.document.hidden=false;h.fire('document:visibilitychange');
  await h.c.returnRefresh;assert.equal(h.count('public'),2);
  h.fire('window:pagehide');h.fire('window:pageshow',{persisted:true});await h.c.returnRefresh;
  assert.equal(h.count('public'),3);assert.equal(h.listeners,undefined);
  // Public access changes use the new full snapshot; signed-in member responses never use the manifest.
  const member=harness();await member.ready();member.c.auth.token='login';member.c.state.me={member:true};
  member.member([{key:'private',item:'Private current',store:'Store'}]);
  const b=publication(1,d=>{d.meta.open_access=false;});member.set(b);await member.refresh();
  assert.equal(member.count('manifest'),0);assert.equal(member.memberCalls,1);
  assert.equal(member.c.state.deals[0].key,'private');
  member.c.auth.token='';member.c.state.me=null;member.c.invalidateManifest();member.c.showDeals(null);await member.refresh();
  assert.equal(member.count('public'),3);assert.equal(JSON.stringify(member.c.state.deals),JSON.stringify(b.deals));
  // Do not apply delayed member facts after a newer publication took ownership.
  const gate=deferred();const m=harness();await m.ready();m.c.auth.token='login';m.c.state.me={member:true};
  m.member([{key:'private-old',item:'Old private',store:'Store'}],gate);m.set(b);
  const waiting=m.refresh();await until(()=>m.memberCalls===1);m.c.auth.token='';m.c.state.me=null;m.c.invalidateManifest();
  m.set(publication(2));await m.refresh();gate.resolve();await waiting;
  assert.notEqual(m.c.state.deals[0]?.key,'private-old');
  // Equal public bytes across a new lifetime cannot revive ownership of
  // an older pending member result, even if the token is unchanged.
  const same=harness();await same.ready();same.c.auth.token='login';same.c.state.me={member:true};
  const oldMember=deferred();same.member([{key:'stale-member',item:'Old',store:'Store'}],oldMember);same.set(b);
  const oldMerge=same.refresh();await until(()=>same.memberCalls===1);same.fire('window:pagehide');
  same.member([{key:'current-member',item:'Current',store:'Store'}]);await same.refresh();
  oldMember.resolve();await oldMerge;assert.equal(same.c.state.deals[0].key,'current-member');
  // Exercise the real account-service path: /me changing membership does
  // not discard the first full member response or leave its UI checking.
  if(home.includes('  function api(path,')) {
    const first=harness({realMember:true,initial:publication(0,d=>{d.meta.open_access=false;d.meta.api_url='https://account.test';})});
    await first.ready();assert.equal(first.c.state.deals[0].key,'private-current');
    assert.equal(first.c.auth.checking,false);assert.equal(first.count('member'),2);
    await first.refresh();assert.equal(first.count('manifest'),0);assert.equal(first.count('public'),2);
  }
  // A pending return refresh must not hold a bfcache restore behind an old response.
  const restored=harness();await restored.ready();const pending=deferred();
  restored.queue('manifest',{body:manifest(publication()),pending});const oldReturn=restored.c.refreshOnReturn();
  restored.fire('window:pagehide');restored.set(publication(2));
  restored.fire('window:pageshow',{persisted:true});await restored.c.returnRefresh;
  assert.equal(restored.c.state.meta.published_at,publication(2).meta.published_at);
  pending.resolve();await oldReturn;
  assert.equal(restored.c.manifestSnapshot.hash,hash(publication(2)));
  // The same applies to a first load that was suspended before any JSON landed.
  const suspended=harness({initial:'{'});await suspended.ready();const bodyWait=deferred();
  suspended.queue('public',{body:publication(1),pending:bodyWait});const oldLoad=suspended.c.loadData();
  await flush();
  suspended.fire('window:pagehide');suspended.set(publication(2));suspended.fire('window:pageshow',{persisted:true});
  await suspended.c.loadRequest;bodyWait.resolve();await oldLoad;
  assert.equal(suspended.c.state.meta.published_at,publication(2).meta.published_at);
  assert.equal(suspended.adoptions,1);assert.equal(suspended.el('load-status').hidden,true);
}

async function benchmark() {
  const large=publication(0,d=>{const sample=d.deals.filter(x=>!x.locked);d.deals=Array.from({length:2300},(_,i)=>
    ({...sample[i%sample.length],key:'fixture-'+i,dedupe_key:'fixture|'+i,item:sample[i%sample.length].item+' '+i}));});
  const legacy=harness({initial:large}), h=harness({initial:large});await legacy.ready();await h.ready();
  legacy.c.state.dataKey=legacy.c.dataKey(large.meta);
  legacy.c.manifestSnapshot=null;
  legacy.c.manifestCurrent=pub=>!!pub;
  legacy.c.isNewPublish=pub=>!!pub && pub.key!==legacy.c.state.dataKey;
  legacy.c.fetchPublish=()=>legacy.c.fetchPublicBody().then(pub=>{delete pub.raw;delete pub.tag;return pub;});
  const median=values=>values.sort((a,b)=>a-b)[Math.floor(values.length/2)];
  async function measure(client, changed, ordinary) {
    const runs=[];
    for(let i=1;i<=5;i++) {
      const body=clone(large);
      if(changed){body.meta.published_at=new Date(NOW+i*1000).toISOString();
        body.meta.generated_at=body.meta.published_at;body.deals[0].final_price=8;}
      // Fake origin work (serialization and its hash) stays outside browser timing.
      const text=raw(body), descriptor=manifest(body), tiny=raw(descriptor);
      const publicRaw=Buffer.byteLength(text), publicGzip=gzipSync(text,{level:9}).length;
      const tinyRaw=Buffer.byteLength(tiny), tinyGzip=gzipSync(tiny,{level:9}).length;
      client.queue('public',{body:text});
      if(!ordinary)client.queue('manifest',{body:descriptor});
      if(global.gc)global.gc();
      const heap=process.memoryUsage().heapUsed, cpu=process.cpuUsage(), at=performance.now();
      const parse=client.counters.parse, digest=client.counters.hash, requests=client.requests.length;
      await client.refresh();
      const elapsed=performance.now()-at, work=process.cpuUsage(cpu);
      const allocated=process.memoryUsage().heapUsed-heap;
      if(global.gc)global.gc();
      runs.push({requests:client.requests.length-requests,local_ms:elapsed,cpu_ms:(work.user+work.system)/1000,
        parse_ms:client.counters.parse-parse,hash_ms:client.counters.hash-digest,
        heap_used_delta:allocated,retained_heap_delta:process.memoryUsage().heapUsed-heap,
        raw_bytes:(ordinary||changed?publicRaw:0)+(ordinary?0:tinyRaw),
        gzip_bytes:(ordinary||changed?publicGzip:0)+(ordinary?0:tinyGzip)});
      // Quiet checks leave the queued full body unused.
      client.queues.public.length=0;
    }
    return Object.fromEntries(Object.keys(runs[0]).map(k=>[k,+median(runs.map(r=>r[k])).toFixed(3)]));
  }
  const baselineQuiet=await measure(legacy,false,true), quiet=await measure(h,false,false);
  const baselineChanged=await measure(legacy,true,true), changed=await measure(h,true,false);
  const fullRaw=Buffer.byteLength(raw(large)), fullGzip=gzipSync(raw(large),{level:9}).length;
  const tinyRaw=Buffer.byteLength(raw(manifest(large))), tinyGzip=gzipSync(raw(manifest(large)),{level:9}).length;
  const stats={deals:2300,median_runs:5,full_raw:fullRaw,full_gzip:fullGzip,manifest_raw:tinyRaw,manifest_gzip:tinyGzip,
    baseline_quiet:{...baselineQuiet,round_trips:1},quiet:{...quiet,round_trips:1},
    baseline_changed:{...baselineChanged,round_trips:1},changed:{...changed,round_trips:2}};
  assert.equal(quiet.requests,1);assert.equal(changed.requests,2);assert.equal(quiet.hash_ms,0);
  assert.equal(h.count('public'),6);assert.equal(h.count('manifest'),10);
  assert(tinyGzip<fullGzip);console.log(JSON.stringify(stats));
}
let stage='transport';
const watchdog=setTimeout(()=>{console.error('Unfinished manifest fixture: '+stage);process.exitCode=1;},45000);
(async()=>{
  await transport();stage='failures';await failures();stage='races';await races();
  stage='lifecycle';await lifecycle();stage='benchmark';await benchmark();
})().then(()=>clearTimeout(watchdog),e=>{clearTimeout(watchdog);console.error(e);process.exitCode=1;});
