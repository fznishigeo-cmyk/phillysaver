const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8');
const scope = {URL, document:{getElementById:()=>({value:''})}};
vm.createContext(scope);
vm.runInContext(source.slice(0, source.indexOf('\n(function () {')), scope);
const parse = value => JSON.parse(JSON.stringify(scope.parseSpotterInput(value)));
for(const input of ['B0D8KZHN1Q','b0d8kzhn1q',' https://www.amazon.com/name/dp/B0D8KZHN1Q?tag=foreign-20 ',
 'amazon.com/gp/product/B0D8KZHN1Q','https://smile.amazon.com/gp/aw/d/B0D8KZHN1Q',
 'https://m.amazon.com/exec/obidos/ASIN/B0D8KZHN1Q','https://amazon.com/product?asin=B0D8KZHN1Q']) {
 assert.deepEqual(parse(input),{kind:'amazon',asin:'B0D8KZHN1Q'});
}
for(const input of ['', 'shoes','B0SHORT','https://amazon.com.evil/dp/B0D8KZHN1Q','https://amazon.com@evil.test/dp/B0D8KZHN1Q',
 'javascript:alert(1)','https://amazon.com/dp/B0D8KZHN1Q?asin=B000000000',
 'https://amazon.com/?asin=B0D8KZHN1Q&asin=B0D8KZHN1Q','https://amazon.com/dp/B0D8KZHN1QQ',
 'https://amazon.com:444/dp/B0D8KZHN1Q','https://evil.test/?asin=B0D8KZHN1Q',
 'headphones','smartwatch']) assert.notEqual(parse(input).kind,'amazon',input);
for (const input of ['https://a.co/test','https://amzn.to/test']) assert.equal(parse(input).kind,'short');
assert.equal(scope.toolAmazon('B0D8KZHN1Q'),'https://www.amazon.com/dp/B0D8KZHN1Q?tag=example-20');
scope.toolActive = {raw:'B0D8KZHN1Q',input:parse('B0D8KZHN1Q')};
const similar = new URL(scope.toolSimilar({title:'Wireless Headphones (Blue) with charging case, Foldable'}));
assert.equal(similar.searchParams.get('tag'),'example-20');
assert.equal(similar.searchParams.get('k'),'Wireless Headphones with charging case');
assert.match(similar.searchParams.get('rh'),/23566065011,p_36:-5000/);
// Operator 2026-10-22: the kind of product, best sellers first, still capped and tagged.
const kind = new URL(scope.spotterSimilar({title:'SENNHEISER HD 599SE Open-Back Wired Audiophile Headphones, Black',
  category:'Over-Ear Headphones'}));
assert.equal(kind.searchParams.get('k'),'Over-Ear Headphones');
assert.equal(kind.searchParams.get('s'),'exact-aware-popularity-rank');
assert.equal(kind.searchParams.get('tag'),'example-20');
assert.match(kind.searchParams.get('rh'),/23566065011,p_36:-5000/);
const named = new URL(scope.spotterSimilar({title:'Wireless Headphones (Blue) with charging case, Foldable'}));
assert.equal(named.searchParams.get('k'),'Wireless Headphones with charging case');  // no category: product words
assert.equal(named.searchParams.get('s'),'exact-aware-popularity-rank');
assert.equal(new URL(scope.spotterSimilar({title:'Mug', category:'Kitchen & Dining'})).searchParams.get('k'),'Kitchen Dining');
assert(!source.includes('avg90'));
// Price age reads like the Deal Checker's, never "1084 minutes ago" (operator 2026-10-22).
const ago = ms => scope.spotterAge(new Date(Date.now() - ms).toISOString());
for (const [ms,text] of [[-60000,'less than a minute'],[59000,'less than a minute'],[61000,'1 min'],[59*60000+1000,'59 min'],
  [3600000+1000,'1 hr'],[7200000-1000,'1 hr'],[7200000+1000,'2 hrs'],[1084*60000,'18 hrs'],[86400000-1000,'23 hrs'],
  [86400000+1000,'1 day'],[2*86400000+1000,'2 days']]) assert.equal(ago(ms),'Price checked '+text+' ago',String(ms));
console.log('Deal Spotter parser, shared affiliate links, and private-input guard passed');

// Exercise the complete emitted UI with a deterministic clock and network. Native
// layout/focus/contrast are separately checked by the Chromium harness.
function ui() {
  let now=0, serial=0; const timers=new Map(), requests=[], storage=new Map();
  const doc={body:{style:{}},hidden:false};
  function element(id='') {
    const handlers={}, attrs={}, classes=new Set();
    return {id,hidden:false,style:{},dataset:{},value:'',textContent:'',innerHTML:'',handlers,children:[],
      append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=nodes;},
      addEventListener(name,fn){handlers[name]=fn;},setAttribute(k,v){attrs[k]=v;},getAttribute:k=>attrs[k],removeAttribute:k=>delete attrs[k],
      classList:{toggle(k,on){on===false?classes.delete(k):classes.add(k);},contains:k=>classes.has(k)},
      focus(){doc.activeElement=this;},scrollIntoView(){},contains(el){return this===el;},
      showModal(){this.open=true;},close(){this.open=false;},click(){return handlers.click?.({currentTarget:this,preventDefault(){}});}};
  }
  const template=fs.readFileSync('dealradar/publish/templates/deal-spotter.html','utf8');
  const ids=Object.fromEntries([...template.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],element(m[1])]));
  const progress=element(), illustration=element(), steps=Array.from({length:4},()=>element()), slides=Array.from({length:3},(_,i)=>({...element(),dataset:{slide:String(i)}}));
  const hero=element(), button=element('submit');hero.querySelector=()=>ids.q;
  ids['pwa-install'].hidden=ids['pwa-ios'].hidden=true;ids['spotter-result'].hidden=true;
  ids['analyze-modal'].querySelector=()=>progress;
  ids['analyze-modal'].querySelectorAll=()=>[ids['cancel-analysis'],ids['modal-install'],ids['modal-deals']];
  doc.getElementById=id=>ids[id]; doc.activeElement=ids.q; doc.createElement=()=>element();
  doc.querySelectorAll=selector=>({'[data-analyze]':[hero],'[data-analyze] input':[ids.q],'.how-step':steps,'[data-slide]':slides}[selector]||[]);
  const demo=element(), how=element();
  doc.querySelector=selector=>({'.illustration':illustration,'.demo-frame':demo,'.how-steps':how}[selector]);
  doc.addEventListener=()=>{};
  const fixed=Date.parse('2026-10-22T01:00:00Z');
  class FakeDate extends Date {constructor(...args){super(...(args.length?args:[fixed+now]));}static now(){return fixed+now;}}
  const location={origin:'https://wowpriced.test',search:''};
  const ctx={document:doc,URL,URLSearchParams,AbortController,Date:FakeDate,Intl,console,location,
    Math:Object.assign(Object.create(Math),{random:()=>0}),performance:{now:()=>now},navigator:{clipboard:{writeText:async value=>{ctx.copied=value;}}},
    localStorage:{getItem:key=>storage.get(key),setItem:(k,v)=>storage.set(k,v)},
    history:{replaceState:(state,title,url)=>{location.search=url.slice(url.indexOf('?'));}},
    matchMedia:()=>({matches:false}),addEventListener(){},
    MutationObserver:class{observe(){}},IntersectionObserver:class{observe(){}disconnect(){}},
    setTimeout:(fn,ms)=>{timers.set(++serial,{fn,at:now+ms});return serial;},
    clearTimeout:id=>timers.delete(id),
    setInterval:(fn,ms)=>{timers.set(++serial,{fn,at:now+ms,repeat:ms});return serial;},clearInterval:id=>timers.delete(id),
    fetch:(url,options)=>new Promise((resolve,reject)=>{
      requests.push({url,respond:data=>resolve({ok:true,json:async()=>data})});
      options.signal.addEventListener('abort',()=>reject({name:'AbortError'}));
    })};
  ctx.window=ctx;vm.createContext(ctx);vm.runInContext(source,ctx);
  const flush=async()=>{for(let i=0;i<20;i++) await Promise.resolve();};
  return {ids,requests,ctx,progress,storage,flush,
    submit(value){ids.q.value=value;hero.handlers.submit({preventDefault(){},submitter:button});},
    async advance(ms){const target=now+ms;while(true){const next=[...timers].sort((a,b)=>a[1].at-b[1].at)[0];if(!next||next[1].at>target) break;
      now=next[1].at; if(next[1].repeat) next[1].at+=next[1].repeat;else timers.delete(next[0]); next[1].fn();await flush();}now=target;await flush();},
    data(grade='A'){return {items:[{asin:'B0D8KZHN1Q',title:'Wireless headphones',price_cents:2000,list_price_cents:3000,
      as_of:new Date(fixed).toISOString(),monthlySold:1000}],deal_grade:{grade,label:{A:'Great price',B:'Good price',C:'Fair price'}[grade],facts:['33% below the list price']}};}};
}
(async()=>{
  const h=ui();h.submit('https://not-amazon.example/item');assert.equal(h.requests.length,0);assert(h.ids['input-error'].textContent);
  // A typed name never calls /api/search: one tagged Amazon highly rated search link, in a new tab.
  h.submit('wireless  headphones');assert.equal(h.requests.length,0);assert(!h.ids['name-results'].hidden);
  const [named,hint]=h.ids['name-results'].children;
  assert.equal(named.href,'https://www.amazon.com/s?k=wireless%20headphones&s=review-rank&tag=example-20');
  assert.equal(named.target,'_blank');assert.equal(named.rel,'sponsored noopener');assert.equal(h.ctx.document.activeElement,named);
  assert.equal(named.children[0],'See highly rated wireless headphones on Amazon');assert.match(hint.textContent,/paste the product’s Amazon link/);
  h.submit('vibrator');assert.equal(h.requests.length,0);assert(h.ids['name-results'].hidden);
  assert.equal(h.ids['input-error'].textContent,'For an A, B or C grade, paste the product’s Amazon link.');
  h.submit('B0D8KZHN1Q');assert(h.ids['analyze-modal'].open);assert.equal(h.requests.length,1);
  assert.equal(h.requests[0].url,'/api/search?asin=B0D8KZHN1Q&grade=deal');
  h.requests[0].respond(h.data());await h.flush();assert.equal(h.ids['modal-product'].textContent,'Wireless headphones');
  await h.advance(5000);assert.equal(h.progress.getAttribute('aria-valuenow'),'50');assert(h.ids['spotter-result'].hidden);
  await h.advance(5000);assert(!h.ids['analyze-modal'].open);assert.equal(h.ids['grade-letter'].textContent,'A');
  assert.equal(h.ctx.document.activeElement.id,'result-title');assert.equal(h.ctx.location.search,'?asin=B0D8KZHN1Q');
  assert(h.ids['result-time'].textContent.startsWith('Price as of '));assert(h.ids['result-facts'].innerHTML.includes('bought'));
  assert.equal(h.ids['result-list'].textContent,'$30.00 Amazon list price · 33% off list price');
  await h.ids.share.click();assert.equal(h.ctx.copied,'https://wowpriced.test/deal-spotter/?asin=B0D8KZHN1Q');
  h.ids.reanalyze.click();assert.equal(h.requests.length,2);
  h.requests[1].respond(h.data('B'));await h.flush();await h.advance(10000);assert.equal(h.ids['grade-letter'].textContent,'B');
  h.ids.reanalyze.click();h.ids['analyze-modal'].handlers.cancel({preventDefault(){}});
  h.requests.at(-1).respond(h.data('C'));await h.flush();await h.advance(26000);assert.equal(h.ids['grade-letter'].textContent,'B');
  h.ids['cookie-ok'].click();assert(h.ids['cookie-bar'].hidden);assert.equal(h.storage.get('wp.spotter.cookies'),'1');
  // The server waits up to 10 s for Keepa's live refresh: no retry before 14 s.
  const slow=ui();slow.submit('B0D8KZHN1Q');await slow.advance(13900);assert.equal(slow.requests.length,1);await slow.advance(2100);
  assert(slow.ids['analyze-modal'].open);assert.equal(slow.progress.getAttribute('aria-valuenow'),'99');
  assert.equal(slow.requests.length,2);slow.requests[1].respond(slow.data('C'));await slow.flush();
  assert.equal(slow.ids['grade-letter'].textContent,'C');assert(!slow.ids['analyze-modal'].open);
  const failure=ui();failure.submit('B0D8KZHN1Q');failure.requests[0].respond({items:[],reason:'budget_exhausted'});await failure.flush();await failure.advance(10000);
  assert.match(failure.ids['input-error'].textContent,/limit/);assert(failure.ids['spotter-result'].hidden);
  console.log('Complete UI: clock, late data, cancel, grades, reanalysis, limits, share, cookie passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
