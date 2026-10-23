// Front mix: the browser's fresh drops match the server's for a labelled inventory.
const assert = require('node:assert/strict'), fs = require('node:fs');
const {harness} = require('./browse_logic.cjs');
const fixture = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));
const {ctx} = harness({rows: fixture.rows});
ctx.document.querySelectorAll = () => [];
const keys = rows => Array.from(rows, d => d.key);
assert.deepEqual(keys(ctx.freshRotation(fixture.rows)), fixture.pool, 'server/browser rotation');
fixture.pages.forEach((page, mark) =>
  assert.deepEqual(keys(ctx.shuffleDropPicks(fixture.rows, mark)), page, 'server/browser mark ' + mark));
console.log('Front mix passed');
