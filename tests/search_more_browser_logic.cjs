// Exercise the native harness's publication and timeout diagnostics without a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {buildFixture, waitFor} = require('./search_more_browser.cjs');

(async () => {
  const root = buildFixture(process.argv[2]);
  const deals = JSON.parse(fs.readFileSync(path.join(root, 'deals.json'), 'utf8')).deals;
  assert.equal(deals.length, 25);
  assert.equal(new Set(deals.filter(d => d.store === 'Amazon').map(d => d.url)).size, 24);
  const timeout = Object.assign(new Error('page.waitForFunction: Timeout 30000ms exceeded'), {name:'TimeoutError'});
  const condition = '390 light: at least 20 initial local cards in #content';
  const page = {evaluate:async () => ({cards:1, query:'mouse'})};
  await assert.rejects(waitFor(page, condition, async () => {throw timeout;}, {calls:[], errors:['fixture error']}), error => {
    assert.equal(error.cause, timeout);
    for (const text of [condition, '"cards":1', 'fixture error', '"calls":[]']) assert(error.message.includes(text));
    return true;
  });
  await assert.rejects(waitFor({evaluate:async () => {throw Error('closed');}}, condition,
    async () => {throw timeout;}), /pageUnavailable/);
  const other = new Error('not a timeout');
  await assert.rejects(waitFor(page, condition, async () => {throw other;}), error => error === other);
  assert.equal(await waitFor(page, condition, async () => 42), 42);
  console.log('Search more browser harness passed: 25 published fixtures and named timeout diagnostics');
})().catch(error => {console.error(error); process.exitCode = 1;});
