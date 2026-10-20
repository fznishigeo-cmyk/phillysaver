const assert = require('node:assert/strict'), fs = require('node:fs');
const {performance} = require('node:perf_hooks');
const {harness,row,fragment} = require('./browse_logic.cjs');
const keys = rows => Array.from(rows,d=>d.key);
const fixture = JSON.parse(fs.readFileSync(process.argv[4],'utf8'));
const source=fixture.rows, h=harness({rows:source}), {ctx}=h;
const plain=!fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[2]),'drop-fresh.js'),'utf8').includes('Full rotation:');
ctx.document.querySelectorAll=()=>[];
const before=JSON.stringify(source), pool=ctx.freshRotation(source);
assert.deepEqual(keys(pool),fixture.pool,'server/browser full rotation');
assert.equal(pool.length,1200);
let previous, returned=0;
const seen=new Set();
for(let mark=0;mark<21;mark++) {
  const out=ctx.shuffleDropPicks(source,mark), head=keys(out);
  assert.equal(out.length,90);
  assert.deepEqual(head,fixture.pages[mark],'server/browser mark '+mark);
  assert.deepEqual(head,keys(ctx.shuffleDropPicks(source,mark)),'deterministic');
  assert(head.every(k=>k.startsWith('fresh-')),'no ineligible inventory');
  if(previous) {
    assert(head.slice(0,15).every(k=>!previous.includes(k)),'first fifteen are fresh');
    assert(head.filter(k=>!previous.includes(k)).length>=68,'at least 75% new');
    returned+=head.filter(k=>previous.includes(k)).length;
    head.forEach((k,i)=>{if(previous.includes(k)) assert(i>=15 && Math.abs(i-previous.indexOf(k))>=12,'winner moved');});
  }
  if(mark<13) {
    const base=keys(ctx.freshBasePage(pool,mark));
    assert(base.every(k=>!seen.has(k)),'rotation never repeats before wrap');
    if(previous) head.filter(k=>!base.includes(k)).forEach(k=>assert(previous.includes(k),'only previous winners bypass rotation'));
    base.forEach(k=>seen.add(k));
  }
  previous=head;
  assert(out.filter(ctx.freshWomen).length>=63,'women mix');
  assert(out.slice(0,24).filter(ctx.freshCheap).length>=19,'impulse mix mark '+mark);
  for(let i=0;i<24;i+=6) assert(out.slice(i,i+6).filter(d=>d.cl).length<=2,'clothing');
  for(let i=0;i<86;i++) assert(out.slice(i,i+5).filter(d=>d.pick_checkout).length<=1,'checkout');
  for(let i=0;i<87;i++) assert(out.slice(i,i+4).filter(d=>d.allure).length<=1,'allure');
  assert(!out.slice(0,2).some(d=>d.allure));
  for(let i=0;i<88;i++) assert(new Set(out.slice(i,i+3).map(d=>d.category)).size>1,'category');
  assert.equal(new Set(out.map(ctx.freshFamily)).size,90,'family dedup');
}
assert(returned>0,'the test exercises returning winners');assert.equal(seen.size,1170);
assert.equal(ctx.freshCycleText().includes('14 drops'),!plain);
assert.equal(JSON.stringify(source),before,'immutable inventory');
for(let mark=0;mark<20;mark++) {
  const out=ctx.shuffleDropPicks(fixture.stress,mark);
  assert.deepEqual(keys(out),fixture.stress_pages[mark],'server/browser varied mix mark '+mark);
  assert(out.slice(0,24).filter(ctx.freshCheap).length>=19);
  assert(out.filter(ctx.freshWomen).length>=63);
  assert(!out.slice(0,2).some(d=>d.allure));
  for(let i=0;i<87;i++) assert(out.slice(i,i+4).filter(d=>d.allure).length<=1);
  for(let i=0;i<86;i++) assert(out.slice(i,i+5).filter(d=>d.pick_checkout).length<=1);
  for(let i=0;i<24;i+=6) assert(out.slice(i,i+6).filter(d=>d.cl).length<=2);
}
const offTemplate=fs.readFileSync(process.argv[5],'utf8');
for(const tab of [1,3]) for(const sort of ['top','price','fresh']) {
  const on=harness({rows:source}), off=harness({rows:source,template:offTemplate});
  for(const model of [on,off]) {model.ctx.state.catTab=tab;model.ctx.state.sortKey=sort;model.ctx.render();}
  assert.deepEqual(on.keys(),off.keys(),'other views retain their order and pagination');
}
const searched=harness({rows:source});searched.el('q').value='useful';searched.ctx.render();
assert(searched.el('content').innerHTML.includes('id="moremore"'));
// Non-divisible pools and small pools retain the exact maximum new count.
for(const n of [0,1,23,60,90,119,121,127,180,181,1199]) {
  let prev;
  const small=source.slice(0,n);
  for(let mark=-2;mark<23;mark++) {
    const page=keys(ctx.shuffleDropPicks(small,mark));
    assert.equal(page.length,Math.min(n,90));
    if(prev) assert(page.filter(k=>!prev.includes(k)).length>=Math.min(68,Math.max(0,n-90)));
    prev=page;
  }
}
// Title signals are deliberately small; men and adult flags do not gain admission.
for(const item of ["Women's shoes",'Ladies blouse','Girls cardigan','Baby stroller','Kids backpack'])
  assert(ctx.freshWomen(row(item,{item,category:'fashion'})),item);
assert(!ctx.freshWomen(row('men',{item:"Men's jacket",category:'fashion'})));
const twins=[row('a',{item:'Acme countertop storage container Black',store:'Walmart'}),
  row('b',{item:'Acme countertop storage container Blue',store:'Walmart'})];
assert.equal(ctx.freshRotation(twins).length,1);
ctx.shuffleDropPicks(source,0);
assert.equal(h.keys().length,90);assert(h.el('content').innerHTML.includes(plain ? 'See all 1,000+ deals' : 'See all 1,560+ deals'));
if(plain) assert(!h.el('content').innerHTML.includes('<p class="meta">'));
assert(!h.el('content').innerHTML.includes('id="moremore"'));
assert.equal(ctx.freshDropKeys.size,0);
const animations=[], originalCards=ctx.cardEls;
ctx.cardEls=()=>originalCards().map(el=>{el.animate=(frames,timing)=>animations.push(timing);return el;});
ctx.reduceMotion=()=>false;
const oldKeys=h.keys();
h.advance(900000);ctx.refreshDropPicks();
assert.equal(animations.length,ctx.freshDropKeys.size);assert(animations.length>=68);assert(animations.every(t=>t.duration+t.delay<=600));
ctx.reduceMotion=()=>true;animations.length=0;
assert(ctx.freshDropKeys.size>=68);
for(const key of h.keys()) assert.equal(ctx.freshUI.badge({key}),!oldKeys.includes(key),'winners never get new badges');
assert.equal(h.el('fresh-drop-toast').textContent,ctx.freshDropKeys.size+' new deals just dropped');
assert.equal(h.el('fresh-drop-toast').hidden,false);
const badged=[...ctx.freshDropKeys];ctx.render();assert.deepEqual([...ctx.freshDropKeys],badged);
h.advance(4000);assert(h.el('fresh-drop-toast').hidden);
const reload=harness({rows:source});reload.advance(ctx.Date.now()-reload.ctx.Date.now());reload.ctx.render();
assert.deepEqual(reload.keys(),h.keys());assert.equal(reload.ctx.freshDropKeys.size,0);
ctx.window.scrollY=1200;const held=h.keys();
h.advance(900000);ctx.refreshDropPicks();ctx.render();
assert.deepEqual(h.keys(),held);assert.equal(ctx.window.scrollY,1200);
assert.equal(h.el('fresh-drop-pill').hidden,false);
assert.match(h.el('fresh-drop-pill').textContent,/^(?:[6-8][0-9]|90) new deals just dropped ↑$/);
h.el('fresh-drop-pill').onclick();
assert.equal(animations.length,0,'reduced motion never animates');
assert.equal(ctx.window.scrollY,0);assert(h.keys().filter(k=>!held.includes(k)).length>=68);assert(ctx.freshDropKeys.size>=68);
h.el('fresh-all').onclick();assert.equal(ctx.state.catTab,1);assert(h.el('content').innerHTML.includes('id="moremore"'));
// Cold construction as well as cached marks on 4,000 eligible, unique rows.
const large=Array.from({length:4000},(_,i)=>({...source[i%1200],key:'large-'+i,item:'Unique'+i+' '+source[i%1200].item}));
const start=performance.now();ctx.freshCache=null;ctx.shuffleDropPicks(large,0);
const cold=performance.now()-start;
let worst=0;
for(let mark=1;mark<20;mark++) {const t=performance.now();ctx.shuffleDropPicks(large,mark);worst=Math.max(worst,performance.now()-t);}
assert(cold<50 && worst<50,'under 50 ms; cold '+cold+', cached '+worst);
(async()=>{
  // Count actual DOM writes: identical HTML would still destroy focused links.
  for(const query of ['useful', 'https://www.amazon.com/dp/B000000001']) {
    const model=harness({rows:source}), {ctx:active}=model;
    require('node:vm').runInContext(fragment('  function reorganize(', '\n  }')+'\n  }',active);
    model.el('q').value=query;active.render();
    let writes=0;
    new active.MutationObserver(()=>writes++).observe(model.el('content'));
    model.advance(900000);active.refreshDropPicks();
    active.state.deals=source.slice(1);
    assert.equal(active.reorganize(true,true),false);
    assert.equal(writes,0,'background drops preserve the active search DOM');
    const next=source.slice(2);
    model.refresh(next);await active.refreshOnReturn();
    assert.equal(active.state.deals,next,'return refresh still adopts inventory');
    assert.equal(writes,0,'return refresh preserves the active search DOM');
    assert.equal(model.el('q').value,query);
    model.el('q').value='';active.updateSearch();
    assert(writes>0,'clearing search renders the adopted inventory');
    assert(!model.keys().some(key=>source.slice(0,2).some(d=>d.key===key)));
  }
  console.log('Fresh drops passed; 4,000 rows cold '+cold.toFixed(2)+' ms, worst cached '+worst.toFixed(2)+' ms');
})().catch(error=>{console.error(error);process.exitCode=1;});
