const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(process.argv[2],'utf8');
function setup(mode,query='') {
  const calls=[],events=[],focus=[],timers=new Map();let serial=0,now=0;
  const element=()=>({value:'',dataset:{},style:{},hidden:true,innerHTML:'',attributes:{},
    focus:options=>focus.push(options),addEventListener(){},setAttribute(k,v){this.attributes[k]=v;}});
  const ids=Object.fromEntries(['q','tool-form','analyzer','analyzer-title','tool-status','tool-progress','tool-result',
    'top-sellers','verified-deals'].map(id=>[id,element()]));
  ids['tool-progress'].firstElementChild=element();
  for(const name of ['top-sellers','verified-deals']) {const grid=element();ids[name].querySelector=()=>grid;}
  const steps=Array.from({length:mode==='finder'?4:5},element);
  const item={asin:'B012345678',title:'Wireless [2026 upgraded model] noise (all day battery) cancelling headphones with microphone, very long extra marketing title - over ear | black',
    price_cents:1500,list_price_cents:3000,as_of:new Date().toISOString()};
  const ctx={URL,URLSearchParams,AbortController,console,performance:{now:()=>now},
    window:{location:{search:query},wpTrack:(...args)=>events.push(args)},
    document:{body:{dataset:{tool:mode}},getElementById:id=>ids[id],querySelectorAll:()=>steps,addEventListener(){}},
    setInterval(){},setTimeout(fn,ms){timers.set(++serial,{fn,at:now+ms});return serial;},clearTimeout:id=>timers.delete(id),
    requestAnimationFrame(fn){return ctx.setTimeout(fn,16);},cancelAnimationFrame:id=>timers.delete(id),
    fetch:async url=>{calls.push(url);return {ok:true,json:async()=>url==='/deals.json'?{deals:[]}:{items:[item]}};}};
  vm.createContext(ctx);vm.runInContext(source,ctx);
  return {ctx,ids,calls,events,focus,item,async finish(){
    for(let n=0;n<50;n++)await Promise.resolve();
    const target=ctx.toolActive?.duration || 25000;
    while(true) {
      const entry=[...timers].filter(([,t])=>t.at<=target+32).sort((a,b)=>a[1].at-b[1].at)[0];
      if(!entry)break;
      timers.delete(entry[0]);now=entry[1].at;entry[1].fn();
      for(let n=0;n<30;n++)await Promise.resolve();
    }
  }};
}
(async()=>{
  for(const mode of ['checker','finder']) {
    for(const raw of ['wireless earbuds',' café & tea ','https://amazon.com/Headphones-Coupons/dp/B012345678?tag=PRIVATE','x'.repeat(300)]) {
      const h=setup(mode,'?q='+encodeURIComponent(raw)+'&other=keep');
      assert.equal(h.ids.q.value,raw);assert(h.ctx.toolActive);
      const task=h.ctx.toolActive;
      assert(task.duration>=10000 && task.duration<=25000);
      h.ctx.toolSubmit({preventDefault(){}});assert.equal(h.ctx.toolActive,task,'duplicate in-flight submit ignored');
      await h.finish();
      assert.equal(h.events.filter(e=>e[0]===(mode==='checker'?'analyze':'search_discounts')).length,1,'one mount run');
      assert.equal(h.focus.length,1);assert.equal(h.focus[0].preventScroll,true);
      assert.equal(h.calls.filter(url=>url.startsWith('/api/')).length,mode==='checker'?1:0);
      if(mode==='finder') assert(h.ids['tool-result'].innerHTML.includes('210906366011'));
    }
    for(const query of ['','?q=','?q=%20%20','?q='+('x'.repeat(301))]) {
      const h=setup(mode,query);assert.equal(h.ctx.toolActive,undefined);assert.equal(h.calls.length,0);
    }
  }
  const h=setup('finder');
  for(const [raw,words,path] of [['wireless earbuds','wireless earbuds','/s'],
    ['https://amazon.com/Headphones-With-Coupons/dp/B012345678?tag=PRIVATE','Headphones With Coupons','/s'],
    ['https://amazon.com/dp/B012345678','','/dp/B012345678'],['vibrator','','/deals'],
    ['https://amazon.com/Vibrator/dp/B012345678','','/deals']]) {
    h.ids.q.value=raw;
    const discount=h.ctx.toolDiscount(raw),url=new URL(discount.url);
    assert.equal(url.pathname,path);
    assert.equal(url.searchParams.get('k')||'',words);
    if(words) assert.equal(url.searchParams.get('rh'),'p_n_deal_type:210906366011');
    assert.equal(url.searchParams.get('tag'),'example-20');assert(!url.href.includes('PRIVATE'));
    h.ctx.toolFailure({input:h.ctx.analyzeInput(raw),discountUrl:discount.url},'unavailable');
    assert(h.ids['tool-result'].innerHTML.includes(discount.url.replace(/&/g,'&amp;')));
  }
  const c=setup('checker');
  for(const [raw,title,words] of [['wireless earbuds',c.item.title,'wireless earbuds'],
    ['https://amazon.com/dp/B012345678',c.item.title,'Wireless noise cancelling headphones with microphone'],
    ['https://amazon.com/Fallback-Headphones/dp/B012345678','[empty] (empty)','Fallback Headphones'],
    ['https://amazon.com/dp/B012345678','Acme (battery (upgrade) included) [pack [two]] wireless earbuds - extra','Acme wireless earbuds'],
    ['https://amazon.com/dp/B012345678','[empty]',''],['vibrator','Ordinary headphones',''],
    ['headphones','Vibrator secret','']]) {
    c.ids.q.value=raw;c.ctx.toolActive={raw,input:c.ctx.analyzeInput(raw)};
    const url=new URL(c.ctx.toolSimilar({title}));
    assert.equal(url.searchParams.get('k')||'',words);
    if(words)assert.equal(url.searchParams.get('rh'),'p_n_deal_type:23566065011');
    assert.equal(url.searchParams.get('tag'),'example-20');
  }
  c.ids.q.value='wireless earbuds';c.ctx.toolActive={raw:c.ids.q.value,input:{kind:'query'}};
  const lead=c.ctx.toolLiveCard(c.item,true),seller=c.ctx.toolLiveCard(c.item,false);
  assert(lead.includes('See similar deals on Amazon →') && lead.includes('data-tool-event="search_similar_deals"'));
  assert(lead.includes('target="_blank" rel="sponsored noopener"'));
  assert(seller.includes('/dp/B012345678?') && seller.includes('View at Amazon'));
  c.ctx.toolFailure({input:{kind:'query'},words:'wireless earbuds'},'unavailable');
  assert(c.ids['tool-result'].innerHTML.includes('Search Amazon'));
  assert(!c.ids['tool-result'].innerHTML.includes('rh='));
  console.log('Tool auto-run once, query bounds, coupon/failure/adult URLs, similar/title shortening, unchanged seller/failure exits passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
