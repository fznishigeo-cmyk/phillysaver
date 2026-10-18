const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const {harness, row, fragment} = require('./browse_logic.cjs');
const doc = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const keys = rows => Array.from(rows, d => d.key);
const turns = async () => {for (let i=0;i<6;i++) await new Promise(resolve => setImmediate(resolve));};

(async () => {
  // Unequal clothing totals caused the old whole-window rotation to bail out.
  const rows = Array.from({length:144}, (_, i) => row(String(i), {
    pick:i < 120 ? i+1 : null, category:['home','kitchen','electronics','beauty'][i%4],
    store:'Store '+i%5, brand:'Brand '+i%12, cl:i%24 < (i<24 ? 2 : 1),
    pick_checkout:i%5===0, allure:i%12===7, boost:i%4===0
  }));
  const h = harness({rows});
  const before = JSON.stringify(rows);
  assert.deepEqual(keys(h.ctx.rotatePicks(rows,1)),keys(rows),'reproduce the old incompatible-clothing freeze');
  let last;
  for (let seed=-5;seed<100;seed++) {
    const out = h.ctx.shuffleDropPicks(rows, seed);
    assert.equal(JSON.stringify(rows), before, 'do not mutate inventory');
    assert.deepEqual(keys(out), keys(h.ctx.shuffleDropPicks(rows, seed)));
    assert.deepEqual(keys(out.slice(120)), keys(rows.slice(120)));
    const head = keys(out.slice(0,15));
    if (last) assert.notDeepEqual(head, last, 'every adjacent drop changes the first 15');
    last = head;
    for (let i=0;i<120;i+=24) assert.deepEqual(keys(out.slice(i,i+24)).sort(),keys(rows.slice(i,i+24)).sort(),
      'window membership retains all store/brand/category/boost shares');
    for (let i=0;i<out.length;i++) {
      for (const flag of ['cl','pick_checkout','allure']) assert.equal(!!out[i][flag],!!rows[i][flag]);
      assert(out.slice(i,i+5).filter(d=>d.pick_checkout).length<=1);
      if (i<8 && i>=2) assert(new Set(out.slice(i-2,i+1).map(d=>d.category)).size>1);
    }
    for (let i=0;i<24;i+=6) assert(out.slice(i,i+6).filter(d=>d.cl).length<=2);
  }
  // Unsafe/non-picked cards never become shuffle candidates, even in a partial pool.
  const unsafe = ['niche','click_price_unknown','price_unconfirmed','price_aged'].map((flag,i)=>row('unsafe-'+i,{pick:i+1,[flag]:true}));
  unsafe.push(row('gone',{pick:5,status:'dead'}),row('photo',{pick:6,image_url:''}),
    row('box',{pick:7,conditions:['open_box']}),row('adult',{pick:null}),row('locked',{pick:9,locked:true}));
  for (let seed=0;seed<30;seed++) {
    const out = h.ctx.shuffleDropPicks(unsafe.concat(rows),seed);
    assert.deepEqual(keys(out.slice(0,unsafe.length)),keys(unsafe));
  }
  for (const n of [0,1,2,8,15,23,24,25,60,119]) {
    const source=rows.slice(0,n);
    assert.deepEqual(keys(h.ctx.shuffleDropPicks(source,9)).sort(),keys(source).sort());
  }

  // Exercise the real rendered final ordering, all mix rules and current-mark restore.
  const wall = rows.concat([
    row('team',{pick:121,niche:true,item:'NFL licensed jersey'}),
    row('niche',{pick:122,category:'auto'}), row('no-photo',{pick:123,image_url:''}),
    row('open-box',{conditions:['open_box']}), row('used',{conditions:['used']})
  ], Array.from({length:15},(_,i)=>row('gone-'+i,{status:'dead',gone:true})));
  const published = {meta:doc.meta,deals:wall,spotlight:[],key:'unchanged'};
  const live = harness({rows:wall,rotationStartup:true});
  const {ctx} = live;
  ctx.state.shown=240;ctx.state.meta=doc.meta;ctx.state.dataKey=published.key;
  const now = ctx.Date.now(), step=ctx.dropStepMs(doc.meta), phase=ctx.dropPhase(doc.meta,step);
  const target=ctx.nextMarkAfter(now,phase,step);
  Object.assign(ctx, {DROP_FETCH_MS:2500,DROP_MAX_MS:3000,POLL_MS:15000,POLL_SLOW_MS:30000,SLOW_AFTER_MS:360000,
    noop(){}, paintClock(){},paintSpotlightTime(){},toast(){},updatedAgo:()=>'',reorganize(){ctx.render();},
    async fetchPublish(){return published;}, isNewPublish:pub=>pub && pub.key!==ctx.state.dataKey,
    dealsFor:async pub=>pub.deals, poll(){}});
  vm.runInContext(fragment('  var TIMEOUT =', '  function fetchPublish('),ctx);
  vm.runInContext(fragment('  function present(', '  function loadData('),ctx);
  // Real clock helpers were loaded by harness; syncClock there is a startup stub.
  ctx.clock.phase=phase;ctx.clock.step=step;ctx.clock.target=target;
  ctx.render();
  assert(live.keys().length>=15,'the clock fixture must retain a full first 15 after all policy passes');
  let previous=live.keys().slice(0,15);
  for (const mode of ['unchanged','failure','timeout','unchanged']) {
    if (mode==='failure') ctx.fetchPublish=async()=>{throw Error('offline');};
    else if (mode==='timeout') ctx.fetchPublish=()=>new Promise(()=>{});
    else ctx.fetchPublish=async()=>published;
    live.advance(ctx.clock.target-ctx.Date.now());
    ctx.tick(); // Countdown reaches its mark, without a newer publication.
    await turns();live.advance(3000);await turns();
    const after=live.keys().slice(0,15);
    assert.notDeepEqual(after,previous,mode);previous=after;
    assert.equal(ctx.state.dataKey,'unchanged');
    assert.equal(ctx.clock.dropping,0);
    assert(ctx.clock.target>ctx.Date.now());
    const visible=live.keys().map(key=>wall.find(d=>d.key===key));
    assert(visible.slice(0,15).every(d=>d.image_url && !d.niche && d.category!=='auto'));
    assert(!visible.some(d=>['used','open-box'].includes(d.key)));
    for(let i=0;i<visible.length;i++) assert(visible.slice(i,i+10).filter(d=>d.status==='dead').length<=1);
    for(let i=0;i<24;i+=6) assert(visible.slice(i,i+6).filter(d=>d.cl).length<=2);
    ctx.rememberBrowse(false);
    const stored=live.session, historyState=ctx.history.state;
    const reloaded=harness({rows:wall,rotationStartup:true,stored,navigation:'reload',historyState});
    reloaded.advance(ctx.Date.now()-reloaded.ctx.Date.now());
    reloaded.ctx.render();
    assert.deepEqual(reloaded.keys(),live.keys(),'reload restores the current mark exactly');
    ctx.applyBrowseSnapshot({seed:-100,sort:'top'},false);ctx.state.shown=240;ctx.render();
    assert.deepEqual(live.keys().slice(0,15),after,'Back never restores an expired mark');
  }
  const same=live.keys();ctx.state.newKeys={'5':true,'6':true};ctx.render();assert.deepEqual(live.keys(),same);
  const late = {...published, key:'new-publish-same-facts', meta:{...published.meta,
    published_at:new Date(ctx.Date.now()-60000).toISOString()}};
  assert.equal(ctx.dropPhase(late.meta,step),0,'late publications cannot move UTC cadence marks');
  ctx.present(late,wall,null);
  assert.deepEqual(live.keys(),same,'a late publish cannot advance the mark a second time');
  // Missing or corrupt storage cannot affect deterministic choices.
  const blocked=harness({rows:wall,rotationStartup:true,blocked:true});
  blocked.advance(ctx.Date.now()-blocked.ctx.Date.now());blocked.ctx.state.shown=240;blocked.ctx.render();
  assert.deepEqual(blocked.keys(),live.keys());
  ctx.window.scrollY=400+10*220+17;ctx.rememberBrowse(false);
  const anchor=ctx.browseMemory.entries[ctx.browseId].anchor;
  assert(anchor,'the scroll probe must target a rendered card');
  live.advance(step);ctx.refreshDropPicks();
  assert.equal(live.top(anchor.key),anchor.offset,'a scrolled reader keeps the same visible card position');

  const banner=row('banner',{spotlight:{until:'2050-01-01T00:00:00Z'}});
  const withBanner=harness({rows,spotlight:[banner]});
  const bannerFacts=JSON.stringify(withBanner.ctx.state.spotlight);
  let bannerHead=withBanner.keys().slice(1,16);
  for(let n=0;n<4;n++) {
    withBanner.advance(step);withBanner.ctx.render();
    assert.equal(withBanner.keys()[0],'banner');
    assert.equal(withBanner.keys().filter(key=>key==='banner').length,1);
    assert.equal(JSON.stringify(withBanner.ctx.state.spotlight),bannerFacts);
    assert.notDeepEqual(withBanner.keys().slice(1,16),bannerHead);
    bannerHead=withBanner.keys().slice(1,16);
  }
  ctx.state.sortKey='price';ctx.clock.lastSeed=10;
  ctx.present({...published,key:'other-sort'},wall,10);
  assert.equal(ctx.state.seed,11,'other sorts retain their existing publication seeds');
  console.log('Clock-only drops, mix slots, current-mark reload/Back and blocked storage passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
