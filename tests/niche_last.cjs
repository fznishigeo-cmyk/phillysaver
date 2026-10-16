// Front page: niche categories (car parts) follow every other deal; their tab and search keep the order.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[2], 'utf8');
const start = html.indexOf('  function cmp('), end = html.indexOf('  function storeHue(', start);
let query = '';
const ctx = {state: {sortKey: 'top', sortDir: -1, catTab: 0, newKeys: {}, seed: 1,
    meta: {front_page: {top_picks: true, niche_last: true, niche_categories: ['auto', 'office']}}},
  document: {getElementById: () => ({value: query})}, CATGROUPS: [['All deals', null], ['Auto', ['auto']]],
  dealKey: d => d.key, isLocked: d => !!d.locked, num: x => x, sortVal: (d, k) => d[k], JITTER_H: 3, NO_PHOTO_H: 12,
  eligibilityActive: () => false, frontPageView: () => ctx.state.catTab === 0 && !query, frontPageMix: r => r};
vm.createContext(ctx); vm.runInContext(html.slice(start, end), ctx);
const rows = [
  {key: 'axle', category: 'auto', image_url: 'p', pick: 1},
  {key: 'lamp', category: 'home', image_url: 'p', pick: 2},
  {key: 'labels', category: 'office', image_url: 'p'},
  {key: 'dress', category: 'fashion', image_url: 'p'},
  {key: 'kettle', category: 'kitchen'},
  {key: 'gone', category: 'home', image_url: 'p', price_unconfirmed: true}];
const keys = r => Array.from(r, d => d.key);
for (const sort of ['top', 'fresh']) {
  ctx.state.sortKey = sort; ctx.state.catTab = 0;
  const front = keys(ctx.ordered(rows));
  assert.deepEqual(front.slice(-1), ['gone']);
  assert.deepEqual(front.slice(-3, -1).sort(), ['axle', 'labels']);
  assert(front.indexOf('kettle') < front.indexOf('axle'), 'a photo-less broad deal still leads car parts');
  ctx.state.meta.front_page.niche_last = false;
  const off = keys(ctx.ordered(rows));
  ctx.state.meta.front_page.niche_last = true;
  ctx.state.catTab = 1;
  assert.deepEqual(keys(ctx.ordered(rows)), off, 'category tabs keep their order');
  ctx.state.catTab = 0; query = 'axle';
  assert.deepEqual(keys(ctx.ordered(rows)), off, 'search keeps its order');
  query = '';
}
console.log('ok');
