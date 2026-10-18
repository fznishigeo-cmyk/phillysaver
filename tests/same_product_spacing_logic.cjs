const assert = require('node:assert/strict'), fs = require('node:fs');
const {performance} = require('node:perf_hooks');
const {harness, row} = require('./browse_logic.cjs');
const fixture = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));
const rows = fixture.rows.map((d, i) => row(d.key, {...d, category: ['home','kitchen','electronics'][i%3]}));
const h = harness({rows}), {ctx} = h;
const keys = rows => Array.from(rows, d => d.key);
function check(list, bestEffort=false) {
  const last = new Map(), counts = new Map();
  list.slice(0,240).forEach((d,i) => {
    const key = ctx.productFamily(d);
    if (!key) return;
    const fits = d => {
      const k = ctx.productFamily(d);
      return !k || (i - (last.has(k) ? last.get(k) : -9) > 8 && (i >= 48 || (counts.get(k) || 0) < 2));
    };
    if (!bestEffort || fits(d)) assert(fits(d), `family too close at ${i}: ${key}`);
    else assert(!list.slice(i, i+60).some(fits), 'fallback only when no candidate fits');
    last.set(key,i);
    counts.set(key,(counts.get(key)||0)+1);
  });
}
assert.deepEqual(Array.from(rows, ctx.productFamily), fixture.keys, 'Python/JS family parity');
assert.deepEqual(keys(ctx.spaceSameProducts(rows)), fixture.order, 'Python/JS ordering parity');
for (const sample of fixture.bounded) {
  const before = JSON.stringify(sample);
  const spaced = ctx.spaceSameProducts(sample.rows, sample.prefix);
  assert.deepEqual(keys(spaced), sample.order, 'bounded Python/JS ordering parity');
  assert.deepEqual(keys(spaced).sort(), keys(sample.rows).sort(), 'keep every pending card');
  assert.equal(JSON.stringify(sample), before, 'do not mutate input or prefix');
}
const large = Array.from({length:4000}, (_,i) => row('perf-'+i, {
  item: i < 3000 ? 'Family product '+Math.floor(i/10) : 'Single product '+i
}));
const timings = [];
for (let run=0; run<5; run++) {
  const start = performance.now();
  const spaced = ctx.spaceSameProducts(large);
  timings.push(performance.now()-start);
  assert.equal(spaced.length,4000);
  assert.deepEqual(keys(spaced).sort(),keys(large).sort());
  const selected = new Set(keys(spaced.slice(0,240)));
  assert.deepEqual(keys(spaced.slice(240)), keys(large.filter(d=>!selected.has(d.key))), 'tail retains order');
}
const median = timings.sort((a,b)=>a-b)[2];
assert(median < 50, `4,000-row spacing median ${median.toFixed(2)} ms exceeds 50 ms`);
console.log(`4,000-row spacing median: ${median.toFixed(2)} ms`);
const original = JSON.stringify(rows);
for (let tab = 0; tab < ctx.CATGROUPS.length; tab++) {
  ctx.state.catTab=tab;ctx.state.shown=240;
  for (const sort of ['top','fresh','price']) {
    ctx.state.sortKey=sort;ctx.render();
    check(h.keys().map(k=>rows.find(d=>d.key===k)), true);
  }
}
ctx.state.catTab=0;ctx.state.sortKey='top';
for (let mark=0;mark<50;mark++) {
  h.advance(900000);ctx.render();
  check(h.keys().map(k=>rows.find(d=>d.key===k)));
  if (ctx.shuffleDropPicks) {
    const shuffled=ctx.shuffleDropPicks(rows,mark);
    check(shuffled);
    assert.deepEqual(keys(shuffled).sort(),keys(rows).sort());
    assert.deepEqual(keys(shuffled.filter(d=>d.key.startsWith('hoodie'))), keys(rows.slice(0,4)), 'best listing first');
    // A swap at the end of the shuffled prefix cannot collide with the tail.
    const boundary=Array.from({length:140},(_,i)=>row('edge-'+i,{pick:i+1,item:'Unique model '+i}));
    boundary[111]={...boundary[111],item:'Same repeated boundary product'};
    boundary[120]={...boundary[120],item:'Same repeated boundary product'};
    check(ctx.shuffleDropPicks(boundary,mark));
  }
}
assert.equal(JSON.stringify(rows),original);
// Searching bypasses spacing and still exposes every hoodie listing.
h.el('q').value='Blue 84';ctx.state.shown=240;ctx.render();
assert.equal(h.keys().filter(k=>k.startsWith('hoodie')).length,4);
// No unrelated supply: every card remains on the wall.
h.el('q').value='';ctx.state.deals=rows.slice(0,4);ctx.render();
assert.equal(h.keys().length,4);assert.equal(ctx.state.deals.length,4);
console.log('Same-product first paint, tabs, sorts, search and clock spacing passed');
const banner = row('banner', {item:rows[0].item,spotlight:{until:'2050-01-01T00:00:00Z'}});
const prefixed = harness({rows,spotlight:[banner]});
prefixed.ctx.state.shown=240;prefixed.ctx.render();
check(prefixed.keys().map(k=>k==='banner'?banner:rows.find(d=>d.key===k)));
assert.equal(prefixed.keys()[0],'banner');
prefixed.el('q').value='Blue 84';prefixed.ctx.render();
assert.equal(prefixed.keys().filter(k=>k==='banner'||k.startsWith('hoodie')).length,5);
