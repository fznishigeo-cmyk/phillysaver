// Exercise emitted home ordering, including adversarial circular joins, offline.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const home = fs.readFileSync(process.argv[2], 'utf8'), test = process.argv[3];
const a = home.indexOf('  function cmp('), b = home.indexOf('  function storeHue(', a);
assert(a > 0 && b > a);
const options = {max_same_category_run:2, checkout_price_deals:{top_pick_window:5,top_pick_max_in_window:1}};
const ctx = {state:{meta:{front_page:options},newKeys:{},seed:0,catTab:0,sortKey:'top',sortDir:-1},
  document:{getElementById:()=>({value:''})},frontPageView:()=>true,activeSpotlight:()=>[],
  num:Number, dealKey:d=>d.key, isLocked:d=>!!d.locked, JITTER_H:3,NO_PHOTO_H:12};
let logic=home.slice(a,b);
if(test==='promo-pruning' || test==='category-budget') {
  const entry='function slots(pos, recent, last, run, pool) {';
  assert(logic.includes(entry));
  logic=logic.replace(entry,entry+` exploredStates++;
    if (exploredStates > 1024) throw Error('search exceeded 1024 states');`);
  if(test==='category-budget') {
    // Force exhaustive category choices independently of the feasibility heuristic.
    // The hard stop must unwind every branch and return the untouched server order.
    const prune='function fits(pos, recent, left) {';
    assert(logic.includes(prune));
    logic=logic.replace(prune,prune+' return true;');
  }
}
vm.createContext(ctx);vm.runInContext(logic,ctx);
const rows = Array.from({length:120}, (_,i)=>({key:String(i),pick:i+1,category:'cat'+i%5,
  store:'store'+i%5,brand:'brand'+Math.floor(i/3),final_price:i%24===0?120:i%24<5?60:20,
  popular:i%24<6,staple:i%24<4,stapleGroup:i%24<4?'group'+i%24:'',
  pick_checkout:i%6===0,image_url:'photo',first_seen_at:'2026-10-16T12:00:00Z'}));
const keys = rows=>Array.from(rows,d=>d.key);
function ordered(seed, source=rows) {ctx.state.seed=seed;return Array.from(ctx.topOrder(source.slice().reverse()));}
function check(source, result, n) {
  assert.deepEqual(keys(result).sort(),keys(source).sort());
  const full = Math.min(n,Math.floor(source.length/24));
  for(let i=0;i<full;i++) {
    const part = result.slice(i*24,i*24+24);
    const original = source.findIndex(d=>d===part[0]);
    const start = Math.floor(original/24)*24;
    assert.deepEqual(keys(part).sort(),keys(source.slice(start,start+24)).sort());
    for (const [count,cap] of [[part.filter(d=>d.popular).length,6],
      [part.filter(d=>d.staple).length,4],[part.filter(d=>d.final_price>50).length,5],
      [part.filter(d=>d.final_price>100).length,1]]) assert(count<=cap);
    for(const field of ['store','brand','stapleGroup']) {
      const counts={};for(const d of part) if(d[field]) counts[d[field]]=(counts[d[field]]||0)+1;
      assert(Math.max(...Object.values(counts)) <= (field==='store'?5:field==='brand'?3:1));
    }
  }
  for(let i=full*24;i<source.length;i+=24)
    assert.deepEqual(keys(result.slice(i,i+24)).sort(),keys(source.slice(i,i+24)).sort());
  const width=Math.max(1,options.checkout_price_deals.top_pick_window);
  const cap=options.checkout_price_deals.top_pick_max_in_window;
  for(let i=0;i<result.length;i++) assert(result.slice(i,i+width).filter(d=>d.pick_checkout).length<=cap);
}
if(test==='cycle') {
  const starts=[];
  for(let seed=0;seed<7;seed++) {const out=ordered(seed);check(rows,out,3);starts.push(out[0].key);}
  assert.deepEqual(starts,['0','24','48','0','24','48','0']);
} else if(test==='caps') {
  rows.forEach((d,i)=>{d.pick_checkout=i%5===0;});
  for(let seed=-10;seed<30;seed++) check(rows,ordered(seed),3);
  assert.notDeepEqual(keys(ordered(1)),keys(rows));
  assert.deepEqual([0,1,2,3].map(seed=>Math.floor(Number(ordered(seed)[0].key)/24)),[0,1,2,0]);
} else if(test==='new') {
  ctx.state.newKeys={'31':true,'3':true,'85':true};
  const out=ordered(1);assert.equal(out[0].key,'31');check(rows,out,3);
  assert(out.findIndex(d=>d.key==='3')>=48);assert.equal(out.findIndex(d=>d.key==='85'),85);
  ctx.state.newKeys={'24':true,'30':true};
  const promos=ordered(1);assert.equal(promos[0].key,'24');check(rows,promos,3);
  assert(promos.findIndex(d=>d.key==='30')>=5);
} else if(test==='stable') {
  ctx.state.newKeys={'31':true};
  for(const seed of [-1,0,1,2,2147483648,Number.MAX_SAFE_INTEGER])
    assert.deepEqual(keys(ordered(seed)),keys(ordered(seed)));
  assert.equal(ordered(2147483648)[0].key,'48');
} else if(test==='refresh') {
  let saved=null;
  ctx.sessionStorage={getItem:()=>saved,setItem:(key,value)=>{saved=value;}};
  ctx.state.newKeys={'31':true};const before=keys(ordered(1));
  assert.equal(before[0],'31');ctx.state.newKeys={};
  assert.deepEqual(keys(ordered(1)),before);
  assert.equal(ordered(2)[0].key,'48');
  ctx.sessionStorage={getItem:()=>{throw Error('denied');}};
  check(rows,ordered(1),3);
  ctx.sessionStorage={getItem:()=>'{corrupt'};
  check(rows,ordered(1),3);
} else if(test==='partial') {
  for(const count of [0,1,8,23,24,25,47,48,49,71,72,73,119])
    for(let seed=0;seed<6;seed++) check(rows.slice(0,count),ordered(seed,rows.slice(0,count)),3);
} else if(test==='off') {
  assert(!home.includes('wp.drop-picks'));
  ctx.state.newKeys={'31':true,'3':true};
  for(let seed=0;seed<9;seed++) assert.deepEqual(keys(ordered(seed)),keys(rows));
} else if(test==='bounds') {
  const n=Number(process.argv[4]);
  for(let seed=0;seed<n+2;seed++) {
    const out=ordered(seed);check(rows,out,n);
    assert.equal(Math.floor(Number(out[0].key)/24),seed%n);
  }
} else if(test==='sliding-options') {
  for(const [rawWidth,cap] of [[0,1],[1,1],[3,1],[7,2],[5,0]]) {
    const width=Math.max(1,rawWidth);
    options.checkout_price_deals={top_pick_window:rawWidth,top_pick_max_in_window:cap};
    const source=rows.map((d,i)=>({...d,pick_checkout:i%width<cap}));
    for(let seed=0;seed<6;seed++) {
      const out=ordered(seed,source);
      for(let i=0;i<out.length;i++) assert(out.slice(i,i+width).filter(d=>d.pick_checkout).length<=cap);
      if(width===7 && cap===2 && seed===1) assert.deepEqual(keys(out),keys(source));
    }
  }
} else if(test==='unlabelled') {
  assert(rows.every(d=>!d.checkout_price));check(rows,ordered(2),3);
} else if(test==='category-windows') {
  const source=JSON.parse(fs.readFileSync(process.argv[4],'utf8')), starts=[];
  for(const seed of [0,1,2,3]) {
    const out=ordered(seed,source);check(source,out,3);starts.push(out[0].key);
    assert.equal(Math.floor(Number(out[0].key)/24),seed%3);
    for(let i=0;i<6;i++) assert(new Set(out.slice(i,i+3).map(d=>d.category)).size>1);
  }
  assert.equal(new Set(starts.slice(0,3)).size,3);assert.equal(starts[0],starts[3]);
} else if(test==='category-backtrack') {
  const original=JSON.parse(fs.readFileSync(process.argv[4],'utf8'));
  for(const [width,cap,promos] of [[5,1,false],[5,1,true],[7,2,true]]) {
    options.checkout_price_deals={top_pick_window:width,top_pick_max_in_window:cap};
    const source=original.map((d,i)=>({...d,pick_checkout:promos && i%width<(width===7?1:cap)}));
    check(source,source,3);  // The added promo flags also satisfy server spacing.
    const out=ordered(1,source);check(source,out,3);
    assert.equal(Math.floor(Number(out[0].key)/24),1,JSON.stringify({width,cap}));
    assert.deepEqual(out.slice(0,8).map(d=>d.category),
      ['home','home','kitchen','home','home','kitchen','home','home']);
  }
  const impossible=original.map((d,i)=>({...d,category:24<=i && i<48?'home':d.category}));
  assert.deepEqual(keys(ordered(1,impossible)),keys(impossible));
} else if(test==='promo-pruning' || test==='category-budget') {
  const source=JSON.parse(fs.readFileSync(process.argv[4],'utf8'));
  const counts=[];
  check(source,source,3);
  for(const arrivals of [{},{'50':true,'52':true}]) {
    ctx.state.newKeys=arrivals;
    for(const seed of (test==='category-budget'?[2]:[2,1,0])) {
      ctx.exploredStates=0;
      const out=ordered(seed,source);check(source,out,3);
      if(test==='category-budget' && seed===2) {
        const explored=ctx.exploredStates;
        if(!Object.keys(arrivals).length) assert.equal(explored,1024);
        if(explored===1024) assert.deepEqual(keys(out),keys(source));
        ctx.exploredStates=0;
        assert.deepEqual(keys(ordered(seed,source)),keys(out));
        assert.equal(ctx.exploredStates,explored);
        counts.push(ctx.exploredStates);
        continue;
      }
      assert.equal(Math.floor(Number(out[0].key)/24),seed);
      for(let i=0;i<6;i++) assert(new Set(out.slice(i,i+3).map(d=>d.category)).size>1);
      assert(ctx.exploredStates<=500,`seed ${seed}: ${ctx.exploredStates} explored states`);
      counts.push(ctx.exploredStates);
    }
  }
  console.log(`max explored states: ${Math.max(...counts)}`);
} else if(test==='fuzz') {
  let random=269;
  for(let n=0;n<100;n++) {
    let last=-5;
    rows.forEach((d,i)=>{
      random=(Math.imul(random,1664525)+1013904223)>>>0;
      d.pick_checkout=i-last>=5 && random%3!==0;if(d.pick_checkout) last=i;
    });
    for(let seed=0;seed<3;seed++) {
      const out=ordered(seed);check(rows,out,3);
      assert.equal(Math.floor(Number(out[0].key)/24),seed,JSON.stringify({n,seed,promos:rows.filter(d=>d.pick_checkout).map(d=>d.pick)}));
    }
  }
} else throw Error(test);
console.log('drop rotation '+test+': passed');
