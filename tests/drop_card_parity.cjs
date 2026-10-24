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
for (const {row, meta, how, steps, lines, ends} of cases) {
  // Hydration after the hot meter's two-hour boundary still matches publication.
  ctx.Date.now = () => Date.parse(doc.meta.generated_at) + 3 * 3600000;
  const card = ctx.cardHtml(row);
  if (meta !== undefined) assert.equal(normalize(card.match(/<div class="metarow">(.*?)<\/div>/s)[1]), normalize(meta), row.key);
  // keep_first_cards: the first static cards draw the script's How box.
  if (how !== undefined) assert.equal(normalize((card.match(/<div class="how".*?<\/div>/s) || [''])[0]), normalize(how), row.key);
  // phone_first_screen: the static cards draw the script's "How to get this price" link.
  if (steps !== undefined) assert.equal((card.match(/<a class="deal-steps".*?<\/a>/s) || [''])[0], steps, row.key);
  // short_cards: the steps line and the "Enter Code: X at checkout" line.
  if (lines !== undefined) assert.equal(normalize(['how-line', 'code-line'].map(c =>
    (card.match(new RegExp('<div class="' + c + '">.*?</div>', 's')) || [''])[0]).join('')), normalize(lines), row.key);
  // short_cards: the "ends Oct 25" chip (the headline's expiry date).
  if (ends !== undefined) assert.equal((card.match(/<span class="chip">ends [^<]*<\/span>/) || [''])[0], ends, row.key);
}
console.log('Static and hydrated metarows match');
