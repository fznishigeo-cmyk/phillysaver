// Offline driver for the actual monitor and emitted tool code; no browser/network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {buildFixture} = require('./tool_pages_browser.cjs');
const {routeRobotSearch, searchResponse} = require('../scripts/robot_search.cjs');
const {checkToolAmazonUrl} = require('../scripts/site_check_logic.cjs');
const SITE = 'https://wowpriced.test/';
const source = fs.readFileSync(path.join(__dirname, '../scripts/site_check.js'), 'utf8');
const toolsCode = source.slice(source.indexOf('async function checkTools('), source.indexOf('async function walk('));
const check = (ok, reason) => { if (!ok) throw Error(reason); };
function monitor() {
  return vm.runInNewContext(toolsCode + '\ncheckTools',
    {SITE, SITE_URL: new URL(SITE), URL, Date, check, checkToolAmazonUrl, routeRobotSearch});
}

function offlinePage(runtime, realtime, fault = '') {
  let url = SITE, ctx, ids, clock = 0, serial = 0;
  const timers = new Map(), visits = [], fills = [], routes = [], calls = [];
  const element = () => ({value:'', dataset:{}, style:{}, hidden:true, innerHTML:'', textContent:'',
    focus() {}, addEventListener() {}, setAttribute() {}});
  const init = () => {
    ids = Object.fromEntries(['q','tool-form','analyzer','analyzer-title','tool-status','tool-progress',
      'tool-result','top-sellers','verified-deals'].map(id => [id, element()]));
    ids['tool-progress'].firstElementChild = element();
    for (const name of ['top-sellers', 'verified-deals']) {
      const grid = element(); ids[name].querySelector = () => grid;
    }
    const math = Object.create(Math); math.random = () => 1; // longest allowed clock: 25s
    ctx = {URL, URLSearchParams, AbortController, console, Math:math, scrollY:fault === 'scroll' ? 12 : 0,
      window:{location:{search:new URL(url).search}, __siteCheckToolJump:fault === 'jump'},
      performance:{now:() => realtime ? performance.now() : clock},
      document:{body:{dataset:{tool:url.includes('finder') ? 'finder' : 'checker'}},
        getElementById:id => ids[id], querySelectorAll:() => Array.from({length:5}, element), addEventListener() {}},
      setInterval() {}, setTimeout:realtime ? setTimeout : (fn, ms) => {
        timers.set(++serial, {fn, at:clock + ms}); return serial;
      }, clearTimeout:realtime ? clearTimeout : id => timers.delete(id),
      fetch:async href => {
        calls.push(href);
        const body = href === '/deals.json' ? {deals:[]} :
          JSON.parse(searchResponse(new URL(href, SITE).href, true).body);
        return {ok:true, json:async () => body};
      }};
    ctx.requestAnimationFrame = fn => ctx.setTimeout(fn, 16);
    ctx.cancelAnimationFrame = ctx.clearTimeout;
    vm.createContext(ctx); vm.runInContext(runtime, ctx);
  };
  const page = {
    visits, fills, routes, calls,
    url:() => url,
    route:async (pattern, handler) => {
      routes.push({pattern, handler});
      if (pattern === '**/*') for (const href of [SITE + 'tool-pages.js', 'https://amazon.com/s?k=cupping',
        'https://m.media-amazon.com/photo.jpg']) {
        let result;
        await handler({request:() => ({url:() => href}),
          fallback:() => {result = 'site';}, abort:() => {result = 'blocked';}});
        assert.equal(result, href.startsWith(SITE) ? 'site' : 'blocked');
      }
    },
    unroute:async (pattern, handler) => routes.splice(routes.findIndex(r => r.handler === handler), 1),
    addInitScript:async () => {},
    goto:async next => { url = next; visits.push(next); init(); return {status:() => 200}; },
    waitForFunction:async (fn, query) => assert.equal(query, '?q=cupping%20set'),
    evaluate:async fn => vm.runInContext('(' + fn.toString() + ')()', ctx),
    locator:selector => {
      if (selector === '#q') return {fill:async value => fills.push(value), inputValue:async () => ids.q.value};
      if (selector === '#tool-result') return {waitFor:async options => {
        assert.equal(options.state, 'visible'); assert.equal(options.timeout, 40000);
        const started = Date.now();
        while (ids['tool-result'].hidden) {
          if (realtime) {
            assert(Date.now() - started < options.timeout); await new Promise(resolve => setTimeout(resolve, 20));
          } else {
            for (let i = 0; i < 30; i++) await Promise.resolve();
            if (!ids['tool-result'].hidden) break;
            const [id, timer] = [...timers].sort((a,b) => a[1].at-b[1].at)[0];
            timers.delete(id); clock = timer.at; timer.fn();
          }
        }
      }};
      if (selector === '#tool-result .amazon-button') return {first:() => ({getAttribute:async () =>
        ids['tool-result'].innerHTML.match(/class="amazon-button" href="([^"]+)"/)[1].replace(/&amp;/g, '&')})};
      assert(['#analyze-search:visible', '#search-discounts:visible, #search-discounts-mobile:visible'].includes(selector));
      const slug = selector.includes('analyze') ? 'amazon-deal-checker' : 'amazon-discount-finder';
      return {getAttribute:async name => name === 'target' ? (fault === 'target' ? '_blank' : null) :
        `/${slug}/?q=${fault === 'href' ? 'wrong' : 'cupping%20set'}`};
    },
  };
  return page;
}

async function run(realtime) {
  const scratch = fs.mkdtempSync('/tmp/task348-offline-');
  try {
    const root = buildFixture(scratch), runtime = fs.readFileSync(path.join(root, 'tool-pages.js'), 'utf8');
    let seconds = 0;
    for (const width of [1440, 390]) {
      const page = offlinePage(runtime, realtime), started = performance.now();
      const note = await monitor()(page, 'example-20');
      const elapsed = (performance.now() - started) / 1000; seconds += elapsed;
      assert.deepEqual(page.visits, ['amazon-deal-checker', 'amazon-discount-finder'].map(s => SITE + s + '/?q=cupping%20set'));
      assert.deepEqual(page.fills, ['cupping set', '']);
      assert.equal(page.calls.filter(url => url.startsWith('/api/')).length, 1);
      assert(!page.routes.some(r => r.pattern === '**/*'), 'temporary guard removed');
      assert(page.routes.some(r => r.pattern.includes('robot-search-fixture.png')), 'fixture image is local');
      console.log(`offline ${width}: ${note} (measured ${elapsed.toFixed(2)}s)`);
    }
    if (!realtime) for (const [fault, reason] of [['href', /home href changed/], ['target', /home link target/],
      ['scroll', /jumped down/], ['jump', /jumped down/]]) {
      const page = offlinePage(runtime, false, fault);
      await assert.rejects(monitor()(page, 'example-20'), reason);
      assert.equal(page.fills.at(-1), '');
      assert(!page.routes.some(r => r.pattern === '**/*'));
    }
    console.log(`Tools ${realtime ? 'real timers, four maximum 25s runs' : 'offline checks'}: ${seconds.toFixed(2)}s total; no network`);
  } finally { fs.rmSync(scratch, {recursive:true, force:true}); }
}
if (require.main === module) run(process.argv.includes('--timing')).catch(error => {console.error(error); process.exitCode = 1;});
module.exports = {monitor, toolsCode, SITE};
