// Offline driver for the live monitor's tools_merge path (scripts/site_check.js checkMergedTools):
// the homepage buttons, then the spotter and both keyword pages with typed words. No browser or network.
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {checkToolAmazonUrl} = require('../scripts/site_check_logic.cjs');
const SITE = 'https://wowpriced.test/';
const source = fs.readFileSync(path.join(__dirname, '../scripts/site_check.js'), 'utf8');
const code = source.slice(source.indexOf('async function checkMergedTools('), source.indexOf('async function walk('));
const check = (ok, reason) => { if (!ok) throw Error(reason); };
const DISCOUNTS = 'https://www.amazon.com/s?k=cupping%20set&rh=p_n_deal_type%3A210906366011%2Cp_36%3A-5000&tag=example-20';
const RATED = 'https://www.amazon.com/s?k=cupping%20set&s=review-rank&tag=example-20';
function fakePage(fault = '') {
  let url = SITE;
  const visits = [], fills = [];
  const attrs = {'#search-discounts:visible': {href:'/deal-spotter/?q=cupping%20set', target:'_blank', rel:'noopener'},
    '#analyze-search:visible': {href:fault === 'href' ? '/amazon-deal-checker/?q=cupping%20set' : '/review-checker/?q=cupping%20set',
      target:'_blank', rel:'noopener'},
    '#name-results .words-discounts': {href:fault === 'filter' ? RATED : DISCOUNTS}, '#name-results .name-button': {href:RATED}};
  return {visits, fills, url:() => url, route:async () => {}, unroute:async () => {},
    goto:async next => { url = next; visits.push(next); return {status:() => 200}; },
    waitForFunction:async (fn, query) => assert.equal(query, '?q=cupping%20set'),
    locator:selector => selector === '#q' ? {fill:async value => fills.push(value), inputValue:async () => 'cupping set'} :
      {getAttribute:async name => (attrs[selector] || {})[name] ?? null, waitFor:async options => assert.equal(options.state, 'visible')}};
}
(async () => {
  const run = vm.runInNewContext(code + '\ncheckMergedTools', {SITE, SITE_URL:new URL(SITE), URL, Date, check, checkToolAmazonUrl});
  const page = fakePage();
  const note = await run(page, 'example-20');
  assert.deepEqual(page.visits, ['deal-spotter', 'amazon-deal-checker', 'amazon-discount-finder'].map(s => SITE + s + '/?q=cupping%20set'));
  assert.deepEqual(page.fills, ['cupping set', '']);
  assert.match(note, /no lookup/);
  await assert.rejects(run(fakePage('href'), 'example-20'), /review-checker home href changed/);
  await assert.rejects(run(fakePage('filter'), 'example-20'), /missing discounts filter/);
  await assert.rejects(run(fakePage(), 'other-20'), /wrong or duplicate tag/);
  assert.match(source, /const merged = \/\^tools_merge:\[ \\t\]\*true\\b\/m\.test\(config\);/);
  console.log('merged tools monitor: ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
