const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const {routeRobotSearch, FIXTURE_TITLE} = require('../scripts/robot_search.cjs');
const read = name => fs.readFileSync(require.resolve('../scripts/' + name), 'utf8');
function target() {
  const routes = [];
  return {routes, route: async (pattern, handler) => routes.push({pattern, handler}),
    setDefaultTimeout() {}, addInitScript: async () => {}};
}
async function verify(target, products = false) {
  const handler = target.routes.find(r => r.pattern === '**/api/search*')?.handler;
  assert(handler, 'search route must be installed before navigation');
  for (const query of ['q=cupping%20set', 'q=tea&page=1', 'q=tea&page=2', 'asin=B000000298']) {
    let response;
    await handler({request: () => ({url: () => 'https://wowpriced.com/api/search?' + query}),
      fulfill: value => {response = value;}, continue: () => assert.fail('live search'), fetch: () => assert.fail('live search')});
    const result = JSON.parse(response.body);
    assert.equal(result.reason, result.items.length ? undefined : 'robot');
    assert.equal(result.items.length, products && query === 'q=cupping%20set' ? 1 : 0);
    if (result.items.length) assert.equal(result.items[0].title, FIXTURE_TITLE);
  }
}
test('site-check installs local search before any walk navigation', async () => {
  const source = read('site_check.js');
  const code = source.slice(source.indexOf('async function guardPage('), source.indexOf('function check('));
  const page = target();
  const guard = vm.runInNewContext(code + '\nguardPage', {routeRobotSearch, STEP_TIMEOUT_MS:10000});
  await guard(page); await verify(page);
  assert(source.indexOf('await guardPage(page)') < source.indexOf('await walk(page'));
});
test('mobile audit installs local search', async () => {
  const page = target(); await require('../scripts/mobile_audit.cjs').guardPage(page, 'https://wowpriced.com');
  await verify(page);
});
test('visitor watch installs local search on the context, covering sampled pages and popups', async () => {
  const source = read('visitor_watch.js'), context = target();
  const code = source.slice(source.indexOf('    await routeRobotSearch(context);'), source.indexOf('    watchContextErrors(context,'));
  await vm.runInNewContext('(async () => {' + code + '})()', {routeRobotSearch, context});
  await verify(context);
  assert(source.indexOf('await routeRobotSearch(context)') < source.indexOf('const page = await context.newPage()'));
});
test('first-screens installs empty and product fixtures before both navigation paths', async () => {
  const source = read('live_first_screens.js');
  for (const [start, end, products] of [['      await page.route(', '      await page.goto(', false],
    ['        await context.route(', '        const page = await context.newPage()', true]]) {
    const page = target(), context = target();
    await vm.runInNewContext('(async () => {' + source.slice(source.indexOf(start), source.indexOf(end)) + '})()',
      {page, context, routeRobotSearch});
    await verify(products ? context : page, products);
  }
  assert(source.includes('for (const q of ["cupping"])'));
  assert.equal((source.match(/await fetch\(`/g) || []).length, 1);
  assert(source.includes('.includes(FIXTURE_TITLE)'));
});
test('browser probe intercepts search before init scripts and navigation', async () => {
  const source = read('browser_probe.js'), page = target();
  const code = source.slice(source.indexOf('    await page.route('), source.indexOf('    await page.addInitScript('));
  await vm.runInNewContext('(async () => {' + code + '})()', {page, routeRobotSearch, BLOCK:/analytics/});
  await verify(page);
});


test('fixture photo is also local and decodable without an image host request', async () => {
  const context = target(); await routeRobotSearch(context, true);
  const photo = context.routes.find(r => r.pattern.includes('robot-search-fixture.png'));
  let response; await photo.handler({fulfill: value => {response = value;}});
  assert.equal(response.contentType, 'image/svg+xml');
  assert(response.body.includes('<svg'));
});
