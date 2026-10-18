// Check native inline/lazy fixtures without trying to launch Chromium.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {buildFixture} = require('./search_analyze_browser.cjs');

for (const lazy of [false, true]) {
  const root = buildFixture(path.join(process.argv[2], String(lazy)), lazy);
  const page = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  assert.equal(fs.existsSync(path.join(root, 'search-analyze.js')), lazy);
  assert.equal(page.includes('function analyzeLookup'), !lazy);
  assert.equal(page.includes('import("/search-analyze.js?v='), lazy);
  assert.equal(JSON.parse(fs.readFileSync(path.join(root, 'deals.json'), 'utf8')).deals[0].item, 'Steel kitchen kettle');
}
console.log('Analyze browser harness passed: inline and lazy native fixtures');
