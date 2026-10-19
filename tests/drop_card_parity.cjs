const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const {harness, fragment} = require('./browse_logic.cjs');
const home = fs.readFileSync(process.argv[2], 'utf8');
const doc = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const cases = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));
const {ctx} = harness({rows:doc.deals, realCards:true});
ctx.state.meta = doc.meta;
ctx.engagementOn = name => !!doc.meta.engagement[name];
vm.runInContext('var TIER_LABEL = ' + home.match(/var TIER_LABEL = (\{[\s\S]*?\});/)[1], ctx);
if (home.includes('  function hotSignals('))
  vm.runInContext(fragment('  function hotSignals(', '  function surpriseRows('), ctx);
else ctx.hotHtml = () => '';
const normalize = s => s.replace(/&#x27;/g, '&#39;');
for (const {row, meta} of cases) {
  // Hydration after the hot meter's two-hour boundary still matches publication.
  ctx.Date.now = () => Date.parse(doc.meta.generated_at) + 3 * 3600000;
  const actual = ctx.cardHtml(row).match(/<div class="metarow">(.*?)<\/div>/s)[1];
  assert.equal(normalize(actual), normalize(meta), row.key);
}
console.log('Static and hydrated metarows match');
