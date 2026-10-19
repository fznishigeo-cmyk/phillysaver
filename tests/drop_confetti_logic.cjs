const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');
function setup({hidden=false, reduced=false, tab=true}={}) {
  let serial=0, mark=1, created=0, removed=0, maxDraws=0, draws=0, now=0;
  const frames=new Map(), listeners={}, nodes=[];
  const context={setTransform(){}, clearRect(){draws=0;}, save(){draws++;maxDraws=Math.max(maxDraws,draws);},
    translate(){},rotate(){},scale(){},restore(){},beginPath(){},arc(){},fill(){},fillRect(){}};
  const host={state:{sortKey:'top',meta:{front_page:{fresh_page_size:90}},deals:[]},
    freshView:()=>tab, savedOn:()=>false, savedDeals:{},dealKey:d=>d.key,dropPickIndex:()=>mark,
    reduceMotion:()=>reduced,cardEls:()=>[],isLocked:d=>d.locked};
  const el={hidden:true};
  const ctx={host,console,document:{hidden,head:{appendChild(){}},
    body:{appendChild(node){created++;nodes.push(node);}},
    createElement(tag){if(tag!=='canvas') return {};return {style:{},getContext:()=>context,setAttribute(){},remove(){removed++;}};},
    getElementById:()=>el,querySelectorAll:()=>[],addEventListener(type,fn){listeners[type]=fn;}},
    window:{innerWidth:390,innerHeight:844,devicePixelRatio:2,scrollY:0},
    requestAnimationFrame(fn){frames.set(++serial,fn);return serial;},cancelAnimationFrame(id){frames.delete(id);},
    setTimeout:()=>1,clearTimeout(){}};
  vm.createContext(ctx);
  vm.runInContext(source.replace('export default function (host)', 'function mount(host)'),ctx);
  const ui=ctx.mount(host);
  const rows=Array.from({length:90},(_,i)=>({key:'first-'+i}));
  ui.updateFreshDrop(rows);ui.paintFreshDrop();
  assert.equal(created,0,'never on initial mount');
  function drop(){mark++;ui.before();ui.updateFreshDrop(rows.map(d=>({key:mark+d.key})));ui.paintFreshDrop();}
  function frame(){now+=16;const work=[...frames.values()];frames.clear();work.forEach(fn=>fn(now));}
  return {ctx,ui,drop,frame,frames,nodes,listeners,setTab(v){tab=v;},setReduced(v){reduced=v;},
    get created(){return created;},get removed(){return removed;},get maxDraws(){return maxDraws;}};
}
for(const mode of [{hidden:true},{reduced:true},{tab:false}]) {
  const t=setup(mode);t.drop();assert.equal(t.created,0);assert.equal(t.frames.size,0);
}
const t=setup();t.drop();assert.equal(t.created,1);assert.equal(t.frames.size,1);
t.frame();assert.equal(t.nodes[0].width,780);assert.equal(t.nodes[0].height,1688);
assert(t.nodes[0].style.cssText.includes('pointer-events:none'));
t.ctx.window.innerWidth=800;t.frame();assert.equal(t.nodes[0].width,1600);
for(let i=0;i<750;i++) t.frame();
assert(t.maxDraws>100 && t.maxDraws<=300,'two layers share a 300 particle cap');
assert.equal(t.removed,1);assert.equal(t.frames.size,0,'no rAF left after twelve seconds');
t.ui.paintFreshDrop();assert.equal(t.created,1,'ordinary renders never replay a burst');
for(const end of ['hidden','tab','motion']) {
  const s=setup();s.drop();s.frame();
  if(end==='hidden') {s.ctx.document.hidden=true;s.listeners.visibilitychange();}
  if(end==='tab') {s.setTab(false);s.ui.hold();}
  if(end==='motion') {s.setReduced(true);s.frame();}
  assert.equal(s.removed,1);assert.equal(s.frames.size,0,end+' cancels');
}
const scrolled=setup();scrolled.ctx.window.scrollY=1000;scrolled.drop();
assert.equal(scrolled.created,1,'viewport celebration also covers a scrolled reader');
console.log('Confetti lifecycle passed');
