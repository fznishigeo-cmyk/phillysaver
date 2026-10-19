// Event queries survive both orders of the inventory and fresh-drop imports.
const assert = require('node:assert/strict');
const {harness, row} = require('./browse_logic.cjs');
const rows = [row('mouse', {item:'Wireless mouse'}), row('kettle', {item:'Steel kettle'})];
for (const late of [false, true]) {
  const h = harness({rows, pageQuery:true, search:'?q=mouse&utm_source=event#top'}), {ctx} = h;
  const fresh = ctx.freshUI;
  assert(fresh, 'exercise the emitted drop factory');
  if (late) ctx.freshUI = null;
  assert(ctx.applyPagePolishQuery());
  h.flushTimers(); h.flushFrames();
  assert.equal(h.el('q').value, 'mouse');
  assert.deepEqual(h.keys(), ['mouse']);
  assert.equal(ctx.browsePending, null);
  assert.equal(ctx.freshView(), false, 'event search must not become Trending');
  assert(!ctx.location.search.includes('q='));
  // The lazy loader redraws and tries the normal Back/scroll restore after import.
  ctx.freshUI = fresh;
  ctx.render(); ctx.restoreInitialBrowse(); h.flushFrames();
  assert.equal(h.el('q').value, 'mouse');
  assert.deepEqual(h.keys(), ['mouse']);
  assert.equal(ctx.applyPagePolishQuery(), false, 'never replay the event query');
  assert.equal(h.el('fresh-drop-toast').textContent, undefined, 'no drop moment on initial search');
}
console.log('Event search and delayed fresh-drop loading passed');
