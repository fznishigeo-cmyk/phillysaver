// Emitted runtime, injected random source and clock; no browser or network.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(process.argv[2],'utf8').split('// TOOL_PAGE_MOUNT')[0];
const item={asin:'B000000100',title:'Kitchen kettle',price_cents:1500,list_price_cents:3000,
  as_of:new Date().toISOString(),image:'https://m.media-amazon.com/photo.jpg'};
const deal={key:'one',item:'Kitchen kettle',status:'alive',badge:'verified',final_price:15,regular_price:30,
  regular_price_basis:'List price',url:'https://amazon.com/dp/B000000101',store:'Amazon'};
function harness(mode='checker',random=0,reduced=false) {
  let now=0,serial=0;
  const timers=new Map(),ticks=[],calls=[],events=[],focus=[];
  const element=()=>({dataset:{},style:{},hidden:true,innerHTML:'',textContent:'',attributes:{},
    focus(options){focus.push(options);},setAttribute(k,v){this.attributes[k]=v;},
    scrollIntoView(){throw Error('must not scroll');}});
  const ids=Object.fromEntries(['q','analyzer','analyzer-title','tool-status','tool-progress','tool-result',
    'top-sellers','verified-deals'].map(id=>[id,element()]));
  ids.q.value='kettle'; ids['tool-progress'].firstElementChild=element();
  for(const id of ['top-sellers','verified-deals']) {
    const grid=element(); ids[id].querySelector=s=>s==='.product-grid' ? grid : grid.innerHTML.includes('product-card') ? {} : null;
  }
  const steps=Array.from({length:mode==='checker'?5:4},(_,i)=>{
    const el=element();el.textContent='step '+i;
    Object.defineProperty(el.dataset,'state',{get(){return this.value;},set(value){
      this.value=value;if(value==='done') ticks.push({index:i,at:now});}});return el;
  });
  const math=Object.create(Math);math.random=()=>random;
  const schedule=(fn,ms,raf=false)=>{timers.set(++serial,{fn,at:now+ms,raf});return serial;};
  const ctx={URL,AbortController,Math:math,console,performance:{now:()=>now},
    document:{body:{dataset:{tool:mode}},getElementById:id=>ids[id],querySelectorAll:()=>steps},
    window:{wpTrack:(...args)=>events.push(args)},matchMedia:()=>({matches:reduced}),
    setTimeout:(fn,ms)=>schedule(fn,ms),clearTimeout:id=>timers.delete(id),
    requestAnimationFrame:fn=>schedule(fn,16,true),cancelAnimationFrame:id=>timers.delete(id),
    fetch:async url=>{calls.push(url);return {ok:true,json:async()=>url==='/deals.json'?{deals:[deal]}:{items:[item]}};}};
  vm.createContext(ctx);vm.runInContext(source,ctx);
  const flush=async()=>{for(let i=0;i<50;i++) await Promise.resolve();};
  return {ctx,ids,steps,ticks,calls,events,focus,timers,now:()=>now,
    width:()=>parseFloat(ids['tool-progress'].firstElementChild.style.width),
    async advance(target) {
      await flush();
      while(true) {
        const entry=[...timers].filter(([,t])=>t.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];
        if(!entry) break;
        timers.delete(entry[0]);now=entry[1].at;entry[1].fn(now);await flush();
      }
      now=target;
      // A real RAF at each exact sample, independent of 16ms frame quantization.
      const raf=[...timers].find(([,t])=>t.raf);
      if(raf){timers.delete(raf[0]);raf[1].fn(now);await flush();}
    }};
}
(async()=>{
  for(const mode of ['checker','finder']) for(const reduced of [false,true]) for(const random of [0,.5,.999999,1]) {
    const h=harness(mode,random,reduced),run=h.ctx.toolSubmit({preventDefault(){}}),T=h.ctx.toolActive.duration;
    assert.equal(T,(10+15*random)*1000);assert(T>=10000 && T<=25000);
    for(const fraction of [.1,.25,.5,.9]) {
      await h.advance(T*fraction);assert(Math.abs(h.width()-100*fraction)<1e-9,'elapsed/T exactly');
      assert(h.ids['tool-result'].hidden,'result waits for 100%');
      for(let i=0;i<h.steps.length;i++) assert.equal(h.steps[i].dataset.state==='done',(i+1)/h.steps.length<=fraction);
    }
    await h.advance(T);await run;
    assert.equal(h.width(),100);assert.equal(h.ids['tool-result'].hidden,false);
    assert.equal(h.ids.analyzer.attributes['aria-busy'],'false');
    assert.deepEqual(h.ticks.map(t=>t.index),h.steps.map((_,i)=>i));
    h.ticks.forEach((t,i)=>assert(Math.abs(t.at-T*(i+1)/h.steps.length)<=16,'evenly spaced steps'));
    assert.deepEqual(JSON.parse(JSON.stringify(h.focus)),[{preventScroll:true}]);
    assert.equal(h.calls.filter(url=>url.startsWith('/api/')).length,mode==='checker'?1:0);
    assert(!JSON.stringify(h.events).includes('kettle'),'private input excluded');
    if(mode==='checker') assert(h.ids['tool-result'].innerHTML.includes('50% off list price'));
  }
  const badge=harness().ctx.toolBadge;
  for(const [price,list,label] of [[15,30,'50% off list price'],[85,100,'15% off list price'],
    [99,100,'1% off list price'],[100,100,'Live Amazon price'],[10,null,'Live Amazon price'],[10,'100','Live Amazon price']])
    assert.equal(badge(price,list).label,label);
  const held=harness();
  held.ctx.toolLookup=()=>new Promise(resolve=>held.ctx.setTimeout(()=>resolve({items:[item]}),13000));
  const heldRun=held.ctx.toolSubmit({preventDefault(){}});
  await held.advance(10000);assert.equal(held.width(),99);assert.equal(held.steps.at(-1).dataset.state,'pending');
  assert(held.ids['tool-result'].hidden);
  await held.advance(12900);assert.equal(held.width(),99);
  await held.advance(13016);await heldRun;assert.equal(held.width(),100);
  const cap=harness();cap.ctx.toolLookup=()=>new Promise(()=>{});
  const capRun=cap.ctx.toolSubmit({preventDefault(){}});
  await cap.advance(20000);await capRun;
  assert.equal(cap.width(),100);assert(!cap.ids['tool-result'].hidden);
  assert(cap.ids['tool-result'].innerHTML.includes('took too long'));
  const partial=harness();partial.ctx.toolInventory=()=>new Promise(()=>{});
  const partialRun=partial.ctx.toolSubmit({preventDefault(){}});
  await partial.advance(20000);await partialRun;
  assert(partial.ids['tool-result'].innerHTML.includes('$15.00'),'show the real price we have at the cap');
  const cancel=harness(),cancelRun=cancel.ctx.toolSubmit({preventDefault(){}});
  await cancel.advance(1000);cancel.ctx.toolCancel();await cancelRun;await cancel.advance(40000);
  assert(cancel.ids['tool-result'].hidden,'cancelled run cannot paint later');
  const limited=harness();limited.ctx.toolLookup=async()=>({items:[],reason:'budget_exhausted'});
  const limitedRun=limited.ctx.toolSubmit({preventDefault(){}});await limited.advance(10000);await limitedRun;
  assert(limited.ids['tool-result'].innerHTML.includes('reached their limit'));
  assert.equal(limited.steps.at(-1).dataset.state,'failed');
  const timeout=harness(),fetch=timeout.ctx.fetch;
  timeout.ctx.fetch=url=>url.startsWith('/api/')?new Promise(()=>{}):fetch(url);
  const timeoutRun=timeout.ctx.toolSubmit({preventDefault(){}});await timeout.advance(20000);await timeoutRun;
  assert.equal(timeout.now(),20000);assert(!timeout.ids['tool-result'].hidden,'retry path still observes the cap');
  for(const raw of ['vibrator','https://a.co/private','name@example.com']) {
    const invalid=harness('finder');invalid.ids.q.value=raw;
    const run=invalid.ctx.toolSubmit({preventDefault(){}});await invalid.advance(10000);await run;
    assert(!invalid.calls.some(url=>url.startsWith('/api/')));
    assert(invalid.ids['tool-result'].innerHTML.includes('https://www.amazon.com/deals?tag=example-20'));
  }
  console.log('Tool v2: uniform duration, exact linear width, ordered milestones, reduced-motion parity, 99% hold/cap/partial results, cancellation, safe badges passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
