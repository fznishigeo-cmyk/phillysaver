const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const home = fs.readFileSync(process.argv[2], 'utf8');
function fragment(a, b) {
  const start = home.indexOf(a), end = home.indexOf(b, start);
  assert(start >= 0 && end > start, a);
  return home.slice(start, end);
}
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, node());
  return elements.get(id);
}
function node() {
  let text = '';
  const classes = new Set();
  return {children: [], attributes: {}, style: {setProperty() {}},
    set textContent(v) {text = String(v); this.children = [];},
    get textContent() {return text + this.children.map(n=>n.textContent).join('');},
    appendChild(n) {this.children.push(n);},
    setAttribute(k,v) {this.attributes[k] = v;},
    querySelector() {return element('droplbl');},
    classList: {toggle(k, on) {on ? classes.add(k) : classes.delete(k);}, contains:k=>classes.has(k)}
  };
}
let now = 1000000000, reduced = false, drops = 0;
const ctx = {Date: {now:()=>now}, DROP_MIN:15,
  num:v=>typeof v === 'number' ? v : null,
  state:{meta:{floors:{min_pct:40}}, deals:[], spotlight:[]},
  reduceMotion:()=>reduced, loadedOnce:true,
  document:{getElementById:element,createElement:node,createTextNode:v=>({textContent:v})},
  roughCount:n=>n >= 1000 ? '1,000+' : n < 100 ? String(n) : Math.floor(n/100)*100+'+',
  plural:(n,w)=>n+' '+w+(n===1?'':'s'), paintSpotlightTime(){},
  drop(t) {drops++;ctx.clock.dropping=t;ctx.clock.burstAt=t;ctx.clock.target=t+ctx.clock.step;},
};
vm.createContext(ctx);
vm.runInContext(fragment('  var clock =', '  setInterval(tick, 1000);'), ctx);
ctx.clock.target = now + 900000;
for (const [left, phase, label, time] of [
  [900000,'calm','Next drop in','15:00'], [300001,'calm','Next drop in','5:01'],
  [300000,'warm','Next drop in','5:00'], [120001,'warm','Next drop in','2:01'],
  [120000,'hot','New deals in','2:00'], [30001,'hot','New deals in','0:31'],
  [30000,'final','Dropping in','0:30'], [1,'final','Dropping in','0:01'],
  [0,'final','Dropping in','0:00']
]) {
  ctx.clock.target=now+left;ctx.paintClock(now);
  assert.equal(element('drop').attributes['data-phase'],phase);
  assert.equal(element('droplbl').textContent,label);
  assert.equal(element('countdown').textContent,time);
  assert.match(element('countdown').textContent,/^\d+:[0-5]\d$/);
  assert.equal(element('drop').classList.contains('clock-pulse'),['hot','final'].includes(phase));
  reduced=true;ctx.paintClock(now);
  assert(!element('drop').classList.contains('clock-pulse'));
  assert(!element('drop').classList.contains('burst'));
  reduced=false;
}
ctx.state.meta.drop_clock={warm_s:90,hot_s:40,final_s:10};
for(const [left,phase] of [[90001,'calm'],[90000,'warm'],[40000,'hot'],[10000,'final']]) {
  ctx.clock.target=now+left;ctx.paintClock(now);
  assert.equal(element('drop').attributes['data-phase'],phase);
}
ctx.clock.target=now;ctx.tick();
assert.equal(drops,1);assert(element('drop').classList.contains('dropping'));
assert(element('drop').classList.contains('burst'));
assert.equal(element('droplbl').textContent,'Dropping…');
assert.equal(element('countdown').textContent,'0:00');
assert(!element('drop').classList.contains('clock-pulse'));
ctx.clock.dropping=0;ctx.paintClock(now);
assert(element('drop').classList.contains('burst')); // a fast response keeps the short burst
now+=1200;ctx.paintClock(now);assert(!element('drop').classList.contains('burst'));
now-=1200;reduced=true;ctx.paintClock(now);assert(!element('drop').classList.contains('burst'));
const reducedCSS = fragment('@media (prefers-reduced-motion: reduce){', '@media (max-width:640px){');
assert(reducedCSS.includes('.drop .dial,.drop .dropt,.drop .dial::after,.live-dot{animation:none!important;transform:none!important}'));
assert(home.includes('animation:drop-pulse 1.5s ease-in-out infinite'));
assert(home.includes('animation-duration:1s'));
assert(home.includes('transform:scale(1.04)'));
assert(home.includes('animation:drop-burst 1.2s ease-out both'));
// Run the actual tagline builder up to the next unrelated metadata action.
const meta = fragment('  function renderMeta() {','    // The browser tab');
vm.runInContext(meta.slice(meta.indexOf('{')+1),ctx);
for(const [floor,count,priceOnly,expected] of [
  [40,1234,false,'verified deals, 40–90% off · 1,000+ live deals'],
  [70,12,false,'verified deals, 70–90% off · 12 live deals'],
  [90,101,false,'verified deals, 90%+ off · 100+ live deals'],
  [40,3,true,'verified deals · 4 live deals'],
  [null,0,false,'verified deals · Offers checked before publication'],
  [40,1,false,'verified deals, 40–90% off · 1 live deal']
]) {
  ctx.state.meta.floors.min_pct=floor;
  ctx.state.deals=Array.from({length:count},()=>({status:'alive'})).concat({status:'dead'});
  ctx.state.spotlight=priceOnly?[{pct_at_checkout:null,spotlight:{until:new Date(now+1000).toISOString()}}]:[];
  // Date.parse is used only for the price-only spotlight qualifier.
  ctx.Date.parse=Date.parse;
  vm.runInContext(meta.slice(meta.indexOf('{')+1),ctx);
  assert.equal(element('tagline').textContent,expected);
  if(count>1 || priceOnly) assert.match(element('tagline').textContent,/\b[\d,]+\+? live deals\b/);
  if(!count && !priceOnly) assert.equal(element('q').placeholder,'Search deals');
  const dot=element('tagline').children.find(n=>n.className==='live-dot');
  assert.equal(dot.attributes['aria-hidden'],'true');
  const off=element('tagline').children.find(n=>n.className==='tag-off');
  assert.equal(!!off,floor!==null && !priceOnly);
}
console.log('Home clock phases, motion preferences and tagline passed');
