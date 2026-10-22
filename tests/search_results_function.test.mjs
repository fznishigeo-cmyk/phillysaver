import test, {beforeEach, afterEach, mock} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DatabaseSync} from 'node:sqlite';

const productsSource = fs.readFileSync(new URL('../dealradar/publish/templates/search-products.js', import.meta.url), 'utf8');
const productsURL = 'data:text/javascript;base64,' + Buffer.from(productsSource).toString('base64');
const gradeSource = fs.readFileSync(new URL('../functions/api/deal-grade.js', import.meta.url), 'utf8');
const gradeURL = 'data:text/javascript;base64,' + Buffer.from(gradeSource).toString('base64');
const {gradeDeal, publishedDealPrice} = await import(gradeURL);
const reviewSource = fs.readFileSync(new URL('../functions/api/review-grade.js', import.meta.url), 'utf8');
const reviewURL = 'data:text/javascript;base64,' + Buffer.from(reviewSource).toString('base64');
const source = fs.readFileSync(new URL('../functions/api/search.js', import.meta.url), 'utf8')
  .replace('./deal-grade.js', gradeURL)
  .replace('../../dealradar/publish/templates/search-products.js', productsURL)
  .replace('./review-grade.js', reviewURL);
const {onRequest, normalizeQuery, currentItems, settle, topSellerItems, shippingTotal} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const {monthlyBoughtLabel} = await import(productsURL);
const realFetch = globalThis.fetch, realCaches = globalThis.caches, realNow = Date.now;
const NOW = Date.parse('2026-10-17T18:00:00Z'), KEY = 'PRIVATE-test-key';
let now, calls, config, values, kv, cache, context, database;
const minute = time => Math.floor((time - Date.UTC(2011, 0, 1)) / 60000);
function product(i = 0) {
  const current = Array(35).fill(-1); current[1] = 1234; current[18] = 1399;
  return {asin:'B0' + String(i).padStart(8, '0'), title:'Cupping set <b>one</b>', imagesCSV:'photo.jpg,other.jpg',
    lastUpdate:minute(now), stats:{current, lastBuyBoxUpdate:minute(now), avg90:[999999], salesRankDrops90:50},
    csv:[[1,2]], rating:50, reviewCount:100, sales:999, history:'PRIVATE-history', key:KEY};
}
const run = (query = 'cupping', options = {}) => onRequest({...context,
  request:new Request('https://wowpriced.test/api/search?q=' + encodeURIComponent(query), options)});
const body = response => response.json();
const DEAL_SETTINGS = {enabled:true, a_max_avg_ratio:.85, b_max_avg_ratio:.97, b_min_list_pct:20, verified_max_ratio:1.02};

beforeEach(() => {
  now = NOW; Date.now = () => now; calls = [];
  config = {amazon_results:'preview', cache_hours:6, tokens_per_day:3000, max_per_minute:20};
  values = new Map(); cache = new Map();
  kv = {get:async key => values.has(key) ? JSON.parse(values.get(key)) : null,
    put:async (key, value, options) => {assert(options.expirationTtl >= 86400); values.set(key, value);}};
  database = new DatabaseSync(':memory:');
  function statement(sql, args = []) {
    return {sql, args, bind:(...bound) => statement(sql, bound),
      run:async () => ({results:database.prepare(sql).all(...args)})};
  }
  const limits = {prepare:sql => statement(sql), batch:async statements => {
    database.exec('BEGIN');
    try {
      const result = statements.map(s => ({results:database.prepare(s.sql).all(...s.args)}));
      database.exec('COMMIT'); return result;
    } catch (error) {database.exec('ROLLBACK'); throw error;}
  }};
  globalThis.caches = {default:{match:async req => cache.get(req.url)?.clone(),
    put:async (req, response) => {assert.equal(response.headers.get('Cache-Control'), 'public, max-age=21600');
      assert(!req.url.includes(KEY)); cache.set(req.url, response.clone());}}};
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]); assert.equal(url.origin, 'https://api.keepa.com');
    assert.equal(url.pathname, '/search'); assert.equal(url.searchParams.get('key'), KEY);
    assert.equal(url.searchParams.get('page'), '0'); assert.equal(url.searchParams.get('history'), '0');
    assert.equal(url.searchParams.get('domain'), '1'); assert.equal(url.searchParams.get('type'), 'product');
    assert(!url.searchParams.has('update'));
    assert.equal(options.redirect, 'manual');
    assert(options.headers['User-Agent'].includes('contact: hello@wowpriced.com'));
    return Response.json({products:Array.from({length:15}, (_, i) => product(i)), tokensConsumed:20,
      private:KEY, errorDetail:KEY});
  };
  context = {env:{KEEPA_KEY:KEY, SEARCH_BUDGET:kv, SEARCH_LIMITS:limits, ASSETS:{fetch:async req => {
    assert.equal(new URL(req.url).pathname, '/search-config.json'); return Response.json(config);
  }}}};
});
afterEach(() => {database.close(); globalThis.fetch = realFetch; globalThis.caches = realCaches; Date.now = realNow;});

test('normalizes case/space, accepts letters/numbers/hyphens only', () => {
  assert.equal(normalizeQuery('  Cupping   SET-2 '), 'cupping set-2');
  assert.equal(normalizeQuery('café 茶碗'), 'café 茶碗');
  for (const q of [null, '', 'a', 'x'.repeat(61), 'one@two.com', '<script>', 'https://shop.test', 'tea & cup', '🫖'])
    assert.equal(normalizeQuery(q), '', String(q));
});
test('bad queries and repeated q return 400 before assets/storage/upstream', async () => {
  context.env.ASSETS.fetch = () => {throw new Error('must not fetch');};
  for (const q of ['', 'a', 'tea/cup', 'name@example.test', 'x'.repeat(61)]) {
    const response = await run(q); assert.equal(response.status, 400); assert.deepEqual((await body(response)).items, []);
  }
  assert.equal((await onRequest({...context, request:new Request('https://wowpriced.test/api/search?q=tea&q=cup')})).status, 400);
  assert.equal((await run('tea', {method:'POST'})).status, 405); assert.equal(calls.length, 0);
});
test('first-page facts have an exact public allowlist, current Buy Box price and real clock', async () => {
  const response = await run(' Cupping  SET '), result = await body(response);
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  assert.equal(result.items.length, 10);
  assert.deepEqual(Object.keys(result.items[0]), ['asin','title','image','price_cents','as_of']);
  assert.equal(result.items[0].price_cents, 1399); assert.equal(result.items[0].as_of, new Date(now).toISOString());
  assert.equal(calls[0][0].searchParams.get('term'), 'cupping set');
  const output = JSON.stringify(result); assert(!output.includes(KEY));
  for (const word of ['history', 'avg90', 'rating', 'reviewCount', 'sales']) assert(!output.includes(word));
  assert.equal(JSON.parse([...values.values()][0]).tokens, 20);
});
test('items carry Amazon\'s most specific category name when Keepa has one', () => {
  const p = product();
  assert.equal(currentItems([p], now)[0].category, undefined);
  p.categoryTree = [{catId:1, name:'Electronics'}, {catId:2, name:'Over-Ear Headphones'}];
  assert.equal(currentItems([p], now)[0].category, 'Over-Ear Headphones');
  p.categoryTree = [{catId:3, name:'x'.repeat(81)}];
  assert.equal(currentItems([p], now)[0].category, undefined);
  p.categoryTree = [{catId:4}];
  assert.equal(currentItems([p], now)[0].category, undefined);
});
test('Buy Box price reads as Amazon\'s item price plus shipping only when Keepa\'s parts add up', () => {
  // B07RFNZYJZ, 2026-10-22: Amazon showed $153.84 + $25.15 shipping; Keepa's Buy Box held $178.99.
  const p = product(1); p.stats.current[18] = 17899; p.stats.current[4] = 24000;
  Object.assign(p.stats, {buyBoxPrice:15384, buyBoxShipping:2515});
  let item = currentItems([p], now)[0];
  assert.deepEqual([item.price_cents, item.shipping_cents, item.list_price_cents], [15384, 2515, 24000]);
  assert.deepEqual(shippingTotal(item), {price_cents:17899, item_price_cents:15384});
  const grade = gradeDeal({...item, ...shippingTotal(item), avg90:20000}, DEAL_SETTINGS);
  assert.deepEqual([grade.grade, grade.facts], ['B', ['35% below the list price']]);
  Object.assign(p.stats, {buyBoxPrice:17899, buyBoxShipping:0});
  item = currentItems([p], now)[0];
  assert.deepEqual([item.price_cents, 'shipping_cents' in item], [17899, false]);
  assert.deepEqual(shippingTotal(item), {price_cents:17899, item_price_cents:17899});
  for (const [price, shipping] of [[15000, 2515], [undefined, 2515], [15384, undefined], [15384.5, 2514.5],
    [0, 17899], [18000, -101], ['15384', 2515]]) {
    Object.assign(p.stats, {buyBoxPrice:price, buyBoxShipping:shipping});
    item = currentItems([p], now)[0];
    assert.deepEqual([item.price_cents, 'shipping_cents' in item], [17899, false]);
  }
  Object.assign(p.stats, {buyBoxPrice:15384, buyBoxShipping:2515}); p.stats.current[18] = -1; p.stats.current[1] = 17899;
  item = currentItems([p], now)[0];
  assert.deepEqual([item.price_cents, 'shipping_cents' in item], [17899, false]);  // no Buy Box, no split
  for (const bad of [undefined, null, {}, {price_cents:'1'}]) assert.deepEqual(shippingTotal(bad), {});
});
test('new current fallback skips absent/invalid/stale prices and never falls back to averages', () => {
  const p = product(); p.stats.current[18] = -1;
  assert.equal(currentItems([p], now)[0].price_cents, 1234);
  p.stats.current[18] = 1399; delete p.stats.lastBuyBoxUpdate;
  assert.equal(currentItems([p], now)[0].price_cents, 1234);
  p.stats.current[18] = -1;
  p.stats.current[1] = -1; assert.deepEqual(currentItems([p], now), []);
  p.stats.current[1] = 0; assert.deepEqual(currentItems([p], now), []);
  p.stats.current[1] = 10.5; assert.deepEqual(currentItems([p], now), []);
  p.stats.current[1] = 1234; p.lastUpdate = minute(now) - 1441; assert.deepEqual(currentItems([p], now), []);
  p.lastUpdate = minute(now) + 1; assert.deepEqual(currentItems([p], now), []);
});
test('List Price copies only a safe current LISTPRICE greater than the selected price', () => {
  const p = product();
  for (const list of [undefined, null, -1, 0, 1398, 1399, 1999.5, '1999', true, NaN, Infinity,
    Number.MAX_SAFE_INTEGER + 1]) {
    p.stats.current[4] = list;
    assert(!Object.hasOwn(currentItems([p], now)[0], 'list_price_cents'), String(list));
  }
  p.stats.current[4] = 1999;
  assert.equal(currentItems([p], now)[0].list_price_cents, 1999);
  p.stats.current[4] = 1300;
  assert(!Object.hasOwn(currentItems([p], now)[0], 'list_price_cents'), 'compare against the Buy Box price');
  delete p.stats.lastBuyBoxUpdate;
  assert.equal(currentItems([p], now)[0].price_cents, 1234);
  assert.equal(currentItems([p], now)[0].list_price_cents, 1300, 'compare against the fallback when selected');
  delete p.stats.current[4];
  p.listPrice = 9999; p.stats.avg90[4] = 9999;
  assert(!Object.hasOwn(currentItems([p], now)[0], 'list_price_cents'), 'no inferred or historical List Price');
});
test('unqualified Buy Box never publishes its price or borrows its clock', () => {
  const p = product(); p.stats.buyBoxIsUnqualified = true; p.stats.current[18] = 999;
  p.lastUpdate = minute(now) - 60;
  assert.equal(currentItems([p], now)[0].price_cents, 1234);
  assert.equal(currentItems([p], now)[0].as_of, new Date(now - 3600000).toISOString());
  p.stats.current[1] = -1; assert.deepEqual(currentItems([p], now), []);
  p.stats.current[1] = 1234; p.lastUpdate = minute(now) - 1441;
  assert.deepEqual(currentItems([p], now), []);
  p.stats.buyBoxIsUnqualified = false;
  assert.equal(currentItems([p], now)[0].price_cents, 999);
});
test('projection rejects malformed products/images and deduplicates ASINs', () => {
  const p = product(); p.imagesCSV = 'https://evil.test/photo.jpg';
  const result = currentItems([null, {}, {...p, asin:'"bad'}, {...p, title:null}, p, p], now);
  assert.equal(result.length, 1); assert.equal(result[0].image, '');
  p.images = [{l:'https://evil.test/a.jpg'}, {l:'large.jpg', m:'medium.jpg'}];
  assert.equal(currentItems([p], now)[0].image, 'https://m.media-amazon.com/images/I/large.jpg');
  p.images = [{m:'medium.jpg'}];
  assert.equal(currentItems([p], now)[0].image, 'https://m.media-amazon.com/images/I/medium.jpg');
});
test('normalized cache hit needs neither quota write nor Keepa', async () => {
  const first = await body(await run()); context.env.SEARCH_BUDGET = null;
  assert.deepEqual(await body(await run('  CUPPING ')), first); assert.equal(calls.length, 1);
});
test('no key/KV or failed storage fails closed', async () => {
  for (const change of [{KEEPA_KEY:''}, {SEARCH_BUDGET:null}, {SEARCH_BUDGET:{}},
    {SEARCH_LIMITS:null}, {SEARCH_LIMITS:{}},
    {SEARCH_LIMITS:{prepare:sql => ({sql}), batch:async () => {throw new Error(KEY);}}},
    {SEARCH_BUDGET:{get:async () => {throw new Error(KEY);}, put(){}}},
    {SEARCH_BUDGET:{get:async () => null, put:async () => {throw new Error(KEY);}}}]) {
    const original = context.env; context.env = {...original, ...change};
    const result = await body(await run()); assert.deepEqual(result.items, []); assert(!JSON.stringify(result).includes(KEY));
    context.env = original;
  }
  assert.equal(calls.length, 0);
});
test('exhausted daily/minute limits make no upstream call', async () => {
  config.tokens_per_day = 109; assert.equal((await body(await run())).reason, 'budget_exhausted');
  config.tokens_per_day = 3000; config.max_per_minute = 0;
  assert.equal((await body(await run())).reason, 'rate_limited');
  config.max_per_minute = 20;
  values.set('search:v1:2026-10-17', JSON.stringify({tokens:2999, count:0, minute:0, last:0}));
  assert.equal((await body(await run())).reason, 'budget_exhausted');
  values.set('search:v1:2026-10-17', JSON.stringify({tokens:110, count:20, minute:Math.floor(now / 60000), last:now - 2000}));
  assert.equal((await body(await run())).reason, 'rate_limited'); assert.equal(calls.length, 0);
});
test('atomic daily cap covers independent isolates and stale KV reads', async () => {
  config.tokens_per_day = 110; kv.get = async () => null;
  const responses = await Promise.all(['cup','tea','mug'].map(q => onRequest({
    ...context, env:{...context.env, SEARCH_BUDGET:{...kv}},
    request:new Request('https://wowpriced.test/api/search?q=' + q)})));
  const results = await Promise.all(responses.map(body));
  assert.equal(results.filter(r => r.items.length).length, 1); assert.equal(calls.length, 1);
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 20);
});
test('atomic minute cap and host spacing survive empty KV mirrors', async () => {
  config.max_per_minute = 2; kv.get = async () => null;
  await run('first'); now += 1000; await run('second'); now += 1000;
  assert.equal((await body(await run('third'))).reason, 'limit_reached');
  now += 60000; await run('fourth');
  // The cooldown gate answers before any debit.
  assert.equal((await body(await run('fifth'))).reason, 'rate_limited');
  assert.equal(calls.length, 3); assert.equal(database.prepare('SELECT count FROM search_budget').get().count, 1);
});
test('delayed KV reservation cannot dispatch alongside a later request', async () => {
  const dispatches = [], put = kv.put;
  let unblock, written;
  const waiting = new Promise(resolve => {written = resolve;});
  kv.get = async () => null;
  kv.put = async (...args) => {
    if (!unblock) {await new Promise(resolve => {unblock = resolve; written();});}
    return put(...args);
  };
  globalThis.fetch = async () => {dispatches.push(now); return Response.json({products:[product()]});};
  const first = run('first'); await waiting;
  now += 1000;
  assert.equal((await body(await run('second'))).items.length, 1);
  unblock();
  assert.equal((await body(await first)).reason, 'rate_limited');
  assert.deepEqual(dispatches, [NOW + 1000]);
  now += 1000;
  assert.equal((await body(await run('third'))).items.length, 1);
  assert.deepEqual(dispatches, [NOW + 1000, NOW + 2000]);
});
test('dispatch lease spaces holders, lapses for a cancelled holder, and a stale release frees nothing', async () => {
  kv.get = async () => null;
  let finish;
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]); return new Promise(resolve => {finish = resolve;});
  };
  const first = run('first');
  while (!finish) await new Promise(resolve => setImmediate(resolve));
  now += 1000;
  assert.equal((await body(await run('second'))).reason, 'rate_limited');
  // The runtime cancels a holder whose browser aborted: its lease lapses after 20 s.
  now += 15000;
  assert.equal((await body(await run('still held'))).reason, 'rate_limited');
  now += 4000;
  const stale = finish; finish = null;
  const third = run('third');
  while (!finish) await new Promise(resolve => setImmediate(resolve));
  stale(Response.json({products:[product()]})); await first;
  now += 1000;
  assert.equal((await body(await run('fourth'))).reason, 'rate_limited');
  finish(Response.json({products:[product()]}));
  assert.equal((await body(await third)).items.length, 1);
  globalThis.fetch = async (url, options) => {calls.push([url, options]); return Response.json({products:[product()]});};
  now += 1000;
  assert.equal((await body(await run('fifth'))).items.length, 1);
  assert.equal(calls.length, 3);
});
test('a reservation that waits into the next minute or UTC day is spent, never dispatched', async () => {
  kv.get = async () => null;
  for (const [start, limits] of [[Date.parse('2026-10-17T23:59:59.500Z'), {tokens_per_day:110}],
    [Date.parse('2026-10-17T18:00:59.500Z'), {max_per_minute:1}]]) {
    database.exec('DROP TABLE IF EXISTS search_budget; DROP TABLE IF EXISTS search_dispatch');
    Object.assign(config, {tokens_per_day:3000, max_per_minute:20}, limits); calls = []; now = start;
    const batch = context.env.SEARCH_LIMITS.batch;
    let unblock, reached; const waiting = new Promise(resolve => {reached = resolve;});
    context.env.SEARCH_LIMITS.batch = async statements => {
      if (statements.some(s => s.sql.startsWith('INSERT INTO search_dispatch')) && !unblock)
        await new Promise(resolve => {unblock = resolve; reached();});
      return batch(statements);
    };
    const first = run('first'); await waiting;
    now += 1000; unblock();
    assert.equal((await body(await first)).reason, 'rate_limited'); assert.equal(calls.length, 0);
    context.env.SEARCH_LIMITS.batch = batch; now += 1000;
    assert.equal((await body(await run('second'))).items.length, 10);
    now += 1000; assert.equal((await body(await run('third'))).items.length, 0);
    assert.equal(calls.length, 1);
  }
});
test('upstream 429 honors Retry-After (60 s without one) and later searches debit nothing meanwhile', async () => {
  kv.get = async () => null;
  for (const [headers, wait, q] of [[{'Retry-After':'120'}, 120000, 'mug'], [{}, 60000, 'tea']]) {
    globalThis.fetch = async (url, options) => {calls.push([url, options]); return new Response(KEY, {status:429, headers});};
    now += 120000; calls = [];
    assert.deepEqual(await body(await run('first')), {items:[], reason:'price_error', detail:'http_429'});
    const debited = database.prepare('SELECT tokens FROM search_budget').get().tokens;
    now += wait - 1000;
    assert.equal((await body(await run('second'))).reason, 'rate_limited');
    assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, debited);
    globalThis.fetch = async (url, options) => {calls.push([url, options]); return Response.json({products:[product()]});};
    now += 1000; assert.equal((await body(await run(q))).items.length, 1);
    assert.equal(calls.length, 2);
  }
});
test('failed KV write keeps its atomic debit, and lowering a cap cannot reset spending', async () => {
  kv.put = async () => {throw new Error(KEY);}; config.tokens_per_day = 110;
  assert.equal((await body(await run('cup'))).reason, 'budget_unavailable');
  kv.put = async () => {}; now += 1000;
  assert.equal((await body(await run('tea'))).reason, 'limit_reached');
  assert.equal(calls.length, 0); config.tokens_per_day = 0;
  assert.equal((await body(await run('mug'))).reason, 'budget_exhausted');
});
test('an oversized receipt durably halts the original UTC day', async () => {
  globalThis.fetch = async () => Response.json({products:[product()], tokensConsumed:111});
  assert.equal((await body(await run())).reason, 'budget_exhausted');
  kv.get = async () => null; now += 1000;
  assert.equal((await body(await run('next'))).reason, 'limit_reached');
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, Number.MAX_SAFE_INTEGER);
});
test('delayed oversized-receipt accounting cannot admit another dispatch', async () => {
  kv.get = async () => null;
  const prepare = context.env.SEARCH_LIMITS.prepare;
  let unblock, accounting;
  const waiting = new Promise(resolve => {accounting = resolve;});
  context.env.SEARCH_LIMITS.prepare = sql => {
    const stmt = prepare(sql);
    if (!sql.startsWith('UPDATE search_budget SET tokens')) return stmt;
    return {bind:(...args) => ({run:async () => {
      await new Promise(resolve => {unblock = resolve; accounting();});
      return stmt.bind(...args).run();
    }})};
  };
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]); return Response.json({products:[product()], tokensConsumed:111});
  };
  const first = run('first'); await waiting;
  now += 2000;
  assert.equal((await body(await run('second'))).reason, 'rate_limited');
  assert.equal(calls.length, 1);
  unblock(); assert.equal((await body(await first)).reason, 'budget_exhausted');
  now += 2000;
  assert.equal((await body(await run('third'))).reason, 'limit_reached');
  assert.equal(calls.length, 1);
});
test('UTC rollover resets both limits, retries and errors keep conservative debits', async () => {
  config.tokens_per_day = 110; await run();
  now += 2000; assert.equal((await body(await run('hijabs'))).reason, 'budget_exhausted');
  now += 86400000; await run('hijabs'); assert.equal(calls.length, 2);
});
test('upstream HTTP, JSON, error bodies and thrown secret URLs are reduced to safe reasons', async () => {
  const replies = [() => new Response(KEY, {status:500}), () => new Response(KEY),
    () => Response.json({error:KEY}), () => {throw new Error('https://api.keepa.com/?key=' + KEY);}];
  const details = ['http_500', 'fetch_SyntaxError', 'body_error', 'fetch_Error'];
  for (let i = 0; i < replies.length; i++) {
    now += 2000; globalThis.fetch = replies[i];
    const result = await body(await run('failure-' + i));
    assert.deepEqual(result, {items:[], reason:'price_error', detail:details[i]}); assert(!JSON.stringify(result).includes(KEY));
  }
  now += 2000; globalThis.fetch = () => Response.json({error:{type:'INVALID_PARAMETER', message:KEY}});
  assert.deepEqual(await body(await run('typed')), {items:[], reason:'price_error', detail:'body_error_INVALID_PARAMETER'});
  now += 2000; globalThis.fetch = () => Response.json({error:{type:'bad <type> ' + KEY}});
  assert.deepEqual(await body(await run('untyped')), {items:[], reason:'price_error', detail:'body_error'});
  assert.equal(JSON.parse([...values.values()][0]).tokens, 660);
});
test('4s timeout covers a hung body and retains its reservation', async () => {
  let signal; globalThis.fetch = async (url, options) => {
    signal = options.signal; return {ok:true, json:() => new Promise(() => {}), arrayBuffer:() => new Promise(() => {})};
  };
  const result = await body(await run()); assert.deepEqual(result, {items:[], reason:'timeout'});
  assert(signal.aborted); assert.equal(JSON.parse([...values.values()][0]).tokens, 110);
});
test('disabled/missing/unreadable published config never calls Keepa', async () => {
  config.amazon_results = 'off'; assert.deepEqual((await body(await run())).items, []);
  config.amazon_results = 'on'; config.tokens_per_day = '3000'; await run();
  context.env.ASSETS = null; await run(); assert.equal(calls.length, 0);
});
test('a gzip body the runtime did not decode is decoded before parsing', async () => {
  const zlib = await import('node:zlib');
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]);
    return new Response(zlib.gzipSync(JSON.stringify({products:[product()], tokensConsumed:20})),
      {headers:{'Content-Type':'application/json'}});
  };
  const result = await body(await run('gzipped'));
  assert.equal(result.items.length, 1); assert.equal(result.items[0].price_cents, 1399);
});
test('a redirect is never followed and counts as a Keepa error', async () => {
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]); assert.equal(options.redirect, 'manual');
    return new Response(null, {status:302, headers:{Location:'https://elsewhere.example/'}});
  };
  assert.deepEqual(await body(await run('moved')), {items:[], reason:'price_error', detail:'http_302'});
});

// ASIN requests take the same accounting/dispatch path, including mixed q/asin traffic.
const runAsin = (asin = 'B000000000') => onRequest({...context,
  request:new Request('https://wowpriced.test/api/search?asin=' + encodeURIComponent(asin))});
function productTransport() {
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]);
    assert.equal(url.origin + url.pathname, 'https://api.keepa.com/product');
    assert.deepEqual(Object.fromEntries(url.searchParams), {key:KEY, domain:'1', stats:'1', history:'0',
      asin:url.searchParams.get('asin'), update:'0'});
    assert.equal(options.redirect, 'manual');
    assert(options.headers['User-Agent'].includes('contact: hello@wowpriced.com'));
    return Response.json({products:[{...product(), asin:url.searchParams.get('asin')}, product(99)], tokensConsumed:1});
  };
}
test('asin success is one current item, cached by ASIN/policy without secrets or unrelated products', async () => {
  productTransport();
  const first = await body(await runAsin());
  assert.deepEqual(first, {items:currentItems([product()], now)});
  assert.equal(JSON.parse([...values.values()][0]).tokens, 1);
  assert.equal(calls[0][0].searchParams.get('asin'), 'B000000000');
  context.env.SEARCH_BUDGET = null;
  assert.deepEqual(await body(await runAsin()), first);
  assert.equal(calls.length, 1);
  assert([...cache.keys()].every(key => key.includes('asin=B000000000') && !key.includes(KEY) && !key.includes('q=')));
  assert(!JSON.stringify(first).includes(KEY));
  config.amazon_results = 'off'; assert.equal((await body(await runAsin())).reason, 'disabled');
});
test('malformed/mixed/duplicate ASIN input fails before assets, accounting or Keepa', async () => {
  context.env.ASSETS.fetch = () => {throw new Error('must not read assets');};
  for (const asin of ['', 'B00000000', 'B0000000000', 'b000000000', 'https://amazon.com/dp/B000000000',
    'B00000000!', ' B000000000', 'B000000000\n']) {
    const response = await runAsin(asin);
    assert.equal(response.status, 400); assert.deepEqual(await body(response), {items:[], reason:'invalid_query'});
  }
  for (const query of ['asin=B000000000&asin=B000000001', 'asin=B000000000&q=cup', 'asin=&q=cup']) {
    assert.equal((await onRequest({...context, request:new Request('https://wowpriced.test/api/search?' + query)})).status, 400);
  }
  assert.equal(calls.length, 0); assert.equal(values.size, 0);
});
test('asin fails closed for missing bindings and malformed budget/config', async () => {
  productTransport();
  const original = context.env;
  for (const change of [{KEEPA_KEY:''}, {SEARCH_BUDGET:null}, {SEARCH_LIMITS:null}, {SEARCH_LIMITS:{}},
    {SEARCH_BUDGET:{get:async () => ({tokens:'110'}), put(){}}},
    {SEARCH_BUDGET:{get:async () => {throw new Error(KEY);}, put(){}}}]) {
    context.env = {...original, ...change};
    const result = await body(await runAsin());
    assert.equal(result.items.length, 0); assert(!JSON.stringify(result).includes(KEY));
  }
  context.env = original;
  config.cache_hours = 25; assert.equal((await body(await runAsin())).reason, 'disabled');
  assert.equal(calls.length, 0);
});
test('asin obeys KV daily/minute ceilings and the same atomic cap as q with stale KV', async () => {
  productTransport();
  config.tokens_per_day = 109;
  assert.equal((await body(await runAsin())).reason, 'budget_exhausted');
  config.tokens_per_day = 3000; config.max_per_minute = 0;
  assert.equal((await body(await runAsin())).reason, 'rate_limited');
  config.max_per_minute = 20;
  values.set('search:v1:2026-10-17', JSON.stringify({tokens:110, count:20, minute:Math.floor(now / 60000), last:now - 2000}));
  assert.equal((await body(await runAsin())).reason, 'rate_limited'); assert.equal(calls.length, 0);
  kv.get = async () => null; config.tokens_per_day = 110;
  assert.equal((await body(await runAsin())).items.length, 1);
  now += 1000;
  assert.equal((await body(await run('cupping'))).reason, 'limit_reached');
  assert.equal((await body(await runAsin('B000000001'))).reason, 'limit_reached');
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 1);
  assert.equal(calls.length, 1);
});
test('asin atomic minute window and cooldown cannot be bypassed with fresh ASINs', async () => {
  productTransport(); kv.get = async () => null; config.max_per_minute = 1;
  await runAsin();
  assert.equal((await body(await runAsin('B000000001'))).reason, 'rate_limited');
  now += 1000;
  assert.equal((await body(await runAsin('B000000001'))).reason, 'limit_reached');
  now += 60000;
  assert.equal((await body(await runAsin('B000000001'))).items.length, 1);
  assert.equal(calls.length, 2);
});
test('asin holds the shared lease through the body and completion cooldown', async () => {
  let finish;
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]); return new Promise(resolve => {finish = resolve;});
  };
  const first = runAsin();
  while (!finish) await new Promise(resolve => setImmediate(resolve));
  now += 2000;
  const tokens = database.prepare('SELECT tokens FROM search_budget').get().tokens;
  assert.equal((await body(await run('words'))).reason, 'rate_limited');
  assert.equal((await body(await runAsin('B000000001'))).reason, 'rate_limited');
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, tokens);
  finish(Response.json({products:[product()]})); assert.equal((await body(await first)).items.length, 1);
  assert.equal((await body(await runAsin('B000000001'))).reason, 'rate_limited');
  now += 1000; productTransport();
  assert.equal((await body(await runAsin('B000000001'))).items.length, 1);
});
test('asin reservation crossing a minute never dispatches, including UTC rollover', async () => {
  productTransport();
  const put = kv.put;
  kv.put = async (...args) => {await put(...args); now += 60000;};
  assert.equal((await body(await runAsin())).reason, 'rate_limited');
  assert.equal(calls.length, 0);
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 110);
});
test('asin 429 Retry-After blocks both lookup types without additional debits', async () => {
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]); return new Response(null, {status:429, headers:{'Retry-After':'90'}});
  };
  assert.deepEqual(await body(await runAsin()), {items:[], reason:'price_error', detail:'http_429'});
  now += 89000;
  assert.equal((await body(await runAsin())).reason, 'rate_limited');
  assert.equal((await body(await run('words'))).reason, 'rate_limited');
  assert.equal(calls.length, 1); assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 110);
  now += 1000; productTransport();
  assert.equal((await body(await runAsin())).items.length, 1);
});
test('asin errors, gzip and oversized receipts retain the same privacy and budget guards', async () => {
  const zlib = await import('node:zlib');
  globalThis.fetch = async () => new Response(zlib.gzipSync(JSON.stringify({products:[product()]})));
  assert.equal((await body(await runAsin())).items.length, 1);
  now += 1000;
  globalThis.fetch = async () => {throw new Error('https://api.keepa.com/product?key=' + KEY);};
  assert.deepEqual(await body(await runAsin('B000000001')), {items:[], reason:'price_error', detail:'fetch_Error'});
  now += 1000;
  globalThis.fetch = async () => Response.json({products:[product()], tokensConsumed:111});
  assert.equal((await body(await runAsin('B000000002'))).reason, 'budget_exhausted');
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, Number.MAX_SAFE_INTEGER);
  assert([...cache.values()].every(response => !response.url.includes(KEY)));
});
test('asin hung response body times out without a refund', async () => {
  let signal;
  globalThis.fetch = async (_, options) => {
    signal = options.signal; return {ok:true, arrayBuffer:() => new Promise(() => {})};
  };
  assert.deepEqual(await body(await runAsin()), {items:[], reason:'timeout'});
  assert(signal.aborted);
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 110);
});

// Page changes share the same money/host gates as text and ASIN requests.
const runPage = (page, query = 'mouse') => onRequest({...context,
  request:new Request('https://wowpriced.test/api/search?q=' + encodeURIComponent(query) + '&page=' + page)});
test('text, ASIN and every page return optional List Price and accept older cached items', async () => {
  config.search_more = {enabled:true, pages:3, min_query_chars:2};
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]);
    const p = product(); p.stats.current[4] = 1999;
    return Response.json({products:[p], tokensConsumed:20});
  };
  for (const request of [() => run('kettle'), () => runAsin(), ...[0, 1, 2].map(page => () => runPage(page))]) {
    const first = await body(await request());
    assert.equal(first.items[0].list_price_cents, 1999);
    assert.deepEqual(Object.keys(first.items[0]), ['asin', 'title', 'image', 'price_cents', 'as_of', 'list_price_cents']);
    assert.deepEqual(await body(await request()), first);
    now += 1100;
  }
  assert.equal(calls.length, 5);
  // Simulate the same cache keys populated before List Price was optional.
  for (const [key, response] of cache) {
    const old = await response.clone().json();
    old.items.forEach(item => {delete item.list_price_cents;});
    cache.set(key, Response.json(old));
  }
  context.env.SEARCH_BUDGET = null;
  for (const request of [() => run('kettle'), () => runAsin(), ...[0, 1, 2].map(page => () => runPage(page))]) {
    const old = await body(await request());
    assert.equal(old.items[0].price_cents, 1399);
    assert(!Object.hasOwn(old.items[0], 'list_price_cents'));
  }
  assert.equal(calls.length, 5, 'optional field never forces a cache refill');
});
function pagedTransport() {
  config.search_more = {enabled:true, pages:3, min_query_chars:2};
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]);
    const page = Number(url.searchParams.get('page'));
    assert([0, 1, 2].includes(page));
    assert.equal(url.origin + url.pathname, 'https://api.keepa.com/search');
    assert.deepEqual(Object.fromEntries(url.searchParams), {key:KEY, domain:'1', stats:'1', history:'0',
      type:'product', term:url.searchParams.get('term'), page:String(page)});
    assert.equal(options.redirect, 'manual');
    return Response.json({products:[product(page)], tokensConsumed:20});
  };
}
test('page sequence reaches Keepa; each page settles to 20 and has a separate normalized cache key', async () => {
  pagedTransport();
  for (const page of [0, 1, 2]) {
    const result = await body(await runPage(page));
    assert.equal(result.items[0].asin, product(page).asin);
    const tokens = database.prepare('SELECT tokens FROM search_budget').get().tokens;
    assert.equal(tokens, 20 * (page + 1));
    assert.deepEqual(await body(await runPage(page, ' MOUSE ')), result);
    assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, tokens);
    now += 1100;
  }
  assert.deepEqual(calls.map(([url]) => url.searchParams.get('page')), ['0', '1', '2']);
  assert.equal(cache.size, 3);
  assert([...cache.keys()].every(url => !url.includes(KEY) && new URL(url).searchParams.has('page')));
  assert.deepEqual(await body(await run('mouse')), await body(await runPage(0)), 'omitted page means page zero');
  assert.equal(calls.length, 3);
  config.search_more.pages = 2;
  assert.equal((await runPage(2)).status, 400, 'lowering pages overrides an already cached page');
  config.search_more.enabled = false;
  assert.equal((await runPage(1)).status, 400);
});
test('invalid/duplicate/ASIN page values fail before assets, cache or spending', async () => {
  context.env.ASSETS.fetch = () => {throw new Error('must not read assets');};
  for (const page of ['', '-1', '1.0', '01', '+1', '1e0', 'NaN', 'Infinity', '9007199254740992', '1&page=2']) {
    const response = await runPage(page);
    assert.equal(response.status, 400, page);
    assert.equal((await body(response)).reason, 'invalid_page');
  }
  for (const page of [0, 1]) {
    const response = await onRequest({...context,
      request:new Request('https://wowpriced.test/api/search?asin=B000000000&page=' + page)});
    assert.equal(response.status, 400);
  }
  assert.equal(calls.length, 0); assert.equal(values.size, 0);
});
test('absent or OFF paging allows only zero; bad page policy fails closed', async () => {
  for (const more of [undefined, false, {enabled:false, pages:3, min_query_chars:2}]) {
    config.search_more = more;
    assert.equal((await runPage(1)).status, 400);
  }
  config.search_more = {enabled:true, pages:3, min_query_chars:2};
  for (const page of [3, 10, 999]) assert.equal((await runPage(page)).status, 400);
  for (const more of [true, [], {enabled:'true', pages:3, min_query_chars:2},
    {enabled:true, pages:11, min_query_chars:2}, {enabled:true, pages:3, min_query_chars:1}]) {
    config.search_more = more;
    assert.equal((await body(await runPage(0))).reason, 'disabled');
  }
  assert.equal(calls.length, 0); assert.equal(values.size, 0);
});
test('later pages obey missing bindings, KV daily/minute caps and malformed counters', async () => {
  pagedTransport();
  for (const page of [1, 2]) {
    config.tokens_per_day = 109;
    assert.equal((await body(await runPage(page))).reason, 'budget_exhausted');
    config.tokens_per_day = 3000; config.max_per_minute = 0;
    assert.equal((await body(await runPage(page))).reason, 'rate_limited');
    config.max_per_minute = 20;
    const original = context.env;
    for (const change of [{KEEPA_KEY:''}, {SEARCH_BUDGET:null}, {SEARCH_LIMITS:null},
      {SEARCH_BUDGET:{get:async () => ({tokens:'110'}), put(){}}}]) {
      context.env = {...original, ...change};
      assert.equal((await body(await runPage(page))).items.length, 0);
    }
    context.env = original;
  }
  assert.equal(calls.length, 0);
});
test('atomic daily/minute caps and completion cooldown count mixed pages with stale KV', async () => {
  pagedTransport(); kv.get = async () => null; config.tokens_per_day = 130; config.max_per_minute = 1;
  assert.equal((await body(await runPage(0))).items.length, 1);
  assert.equal((await body(await runPage(1))).reason, 'rate_limited');
  now += 1100;
  assert.equal((await body(await runPage(1))).reason, 'limit_reached');
  now += 60000;
  assert.equal((await body(await runPage(1))).items.length, 1);
  now += 60000;
  assert.equal((await body(await runPage(2))).reason, 'limit_reached');
  assert.equal(calls.length, 2);
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 40);
});
test('held page lease blocks other pages and ASINs without another debit', async () => {
  pagedTransport(); let finish;
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]); return new Promise(resolve => {finish = resolve;});
  };
  const first = runPage(1);
  while (!finish) await new Promise(resolve => setImmediate(resolve));
  now += 2000;
  assert.equal((await body(await runPage(2))).reason, 'rate_limited');
  assert.equal((await body(await runAsin())).reason, 'rate_limited');
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 110);
  finish(Response.json({products:[product(1)]})); await first;
  assert.equal((await body(await runPage(2))).reason, 'rate_limited');
  now += 1100; pagedTransport();
  assert.equal((await body(await runPage(2))).items.length, 1);
  assert.equal(calls.length, 2);
});
test('a later page waiting into another minute/day is charged but never dispatched', async () => {
  pagedTransport(); now = Date.parse('2026-10-17T23:59:59.500Z');
  const put = kv.put;
  kv.put = async (...args) => {await put(...args); now += 1000;};
  assert.equal((await body(await runPage(2))).reason, 'rate_limited');
  assert.equal(calls.length, 0);
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 110);
});
test('later-page Retry-After cooldown applies to all pages without spending', async () => {
  pagedTransport();
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]); return new Response(null, {status:429, headers:{'Retry-After':'90'}});
  };
  assert.deepEqual(await body(await runPage(1)), {items:[], reason:'price_error', detail:'http_429'});
  now += 89000;
  assert.equal((await body(await runPage(2))).reason, 'rate_limited');
  assert.equal((await body(await runPage(0))).reason, 'rate_limited');
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 110);
  now += 1000; pagedTransport();
  assert.equal((await body(await runPage(2))).items.length, 1);
});
test('later-page oversized receipts halt the day and hung bodies keep reservations', async () => {
  pagedTransport();
  globalThis.fetch = async () => Response.json({products:[product()], tokensConsumed:111});
  assert.equal((await body(await runPage(1))).reason, 'budget_exhausted');
  now += 1100;
  assert.equal((await body(await runPage(2))).reason, 'budget_exhausted');
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, Number.MAX_SAFE_INTEGER);
  now += 86400000;
  let signal;
  globalThis.fetch = async (_, options) => {
    signal = options.signal; return {ok:true, arrayBuffer:() => new Promise(() => {})};
  };
  assert.deepEqual(await body(await runPage(2)), {items:[], reason:'timeout'});
  assert(signal.aborted);
  assert.equal(JSON.parse(values.get('search:v1:2026-10-18')).tokens, 110);
});

for (const consumed of [0, 1, 20, 110]) test(`trusted receipt ${consumed} settles the same row and mirror once`, async () => {
  globalThis.fetch = async () => Response.json({products:[product()], tokensConsumed:consumed});
  await run();
  const expected = {day:'2026-10-17', tokens:consumed, minute:Math.floor(NOW / 60000), count:1, last:NOW};
  assert.deepEqual({...database.prepare('SELECT * FROM search_budget').get()}, expected);
  assert.deepEqual(JSON.parse(values.get('search:v1:2026-10-17')), expected);
  await Promise.all([settle(kv, context.env.SEARCH_LIMITS, NOW, consumed), settle(kv, context.env.SEARCH_LIMITS, NOW, 0)]);
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, consumed);
});
for (const receipt of [undefined, null, '20', true, -1, 1.5, {}, [], Infinity, NaN])
  test(`untrusted receipt ${JSON.stringify(receipt)} never refunds`, async () => {
    globalThis.fetch = async () => Response.json({products:[product()], tokensConsumed:receipt});
    await run();
    assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 110);
  });
test('failed mirror write after settlement and repeated settlement never refund again', async () => {
  const put = kv.put; let writes = 0;
  kv.put = async (...args) => {if (++writes === 2) throw new Error('mirror unavailable'); await put(...args);};
  assert.equal((await body(await run())).reason, 'budget_unavailable');
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 20);
  await settle(kv, context.env.SEARCH_LIMITS, NOW, 20);
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 20);
});
test('settlement rolls back the refund if consuming the reservation fails', async () => {
  globalThis.fetch = async () => Response.json({products:[product()]}); await run();
  database.exec("CREATE TRIGGER refuse_settle BEFORE DELETE ON search_reservations BEGIN SELECT RAISE(ABORT, 'test'); END");
  await assert.rejects(settle(kv, context.env.SEARCH_LIMITS, NOW, 20));
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 110);
  database.exec('DROP TRIGGER refuse_settle');
  await settle(kv, context.env.SEARCH_LIMITS, NOW, 20);
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 20);
});
test('late concurrent settlements preserve other debits and an oversized halt', async () => {
  kv.get = async () => null;
  globalThis.fetch = async () => Response.json({products:[product()]});
  await run('first'); now += 1000; await run('second');
  await Promise.all([settle(kv, context.env.SEARCH_LIMITS, NOW, 20),
    settle(kv, context.env.SEARCH_LIMITS, NOW + 1000, 1), settle(kv, context.env.SEARCH_LIMITS, NOW, 20)]);
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 21);
  now += 1000; await run('third');
  now += 1000;
  globalThis.fetch = async () => Response.json({products:[product()], tokensConsumed:111});
  await run('halt');
  await settle(kv, context.env.SEARCH_LIMITS, NOW + 2000, 0);
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, Number.MAX_SAFE_INTEGER);
});
test('settlement after midnight belongs to the reservation day', async () => {
  now = Date.parse('2026-10-17T23:59:59Z'); const started = now;
  globalThis.fetch = async () => {now += 2000; return Response.json({products:[product()], tokensConsumed:20});};
  await run();
  assert.equal(database.prepare('SELECT tokens FROM search_budget WHERE day = ?').get('2026-10-17').tokens, 20);
  assert.equal(database.prepare('SELECT count(*) AS n FROM search_budget').get().n, 1);
  await settle(kv, context.env.SEARCH_LIMITS, started, 20);
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 20);
});
test('refunded capacity permits more requests while retaining the full next reservation', async () => {
  config.tokens_per_day = 150; kv.get = async () => null;
  for (const q of ['one', 'two', 'three']) {await run(q); now += 1100;}
  assert.equal(calls.length, 3);
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 60);
  assert.equal((await body(await run('four'))).reason, 'limit_reached');
});

test('monthlySold is optional, safe, positive and only passed with the feature on', () => {
  for (const value of [undefined, null, true, false, '1000', -1, 0, 1.5, Infinity, NaN, Number.MAX_SAFE_INTEGER + 1]) {
    const p = {...product(), monthlySold:value};
    assert(!('monthlySold' in currentItems([p], now)[0]), String(value));
  }
  for (const value of [1, 49, 50, 1000, Number.MAX_SAFE_INTEGER]) {
    const p = {...product(), monthlySold:value};
    assert.equal(currentItems([p], now)[0].monthlySold, value);
    assert(!('monthlySold' in currentItems([p], now, false)[0]), 'OFF preserves the previous response');
  }
});

test('monthly proof rounds down through Amazon-style buckets and omits unknown/sub-50 values', () => {
  for (const value of [undefined, null, true, '1000', 0, -1, 49, 50.5, Infinity, NaN, Number.MAX_SAFE_INTEGER + 1])
    assert.equal(monthlyBoughtLabel(value), '', String(value));
  for (const [value, label] of [[50,'50'],[99,'50'],[100,'100'],[199,'100'],[200,'200'],[499,'200'],
    [500,'500'],[999,'500'],[1000,'1K'],[1999,'1K'],[2000,'2K'],[4999,'2K'],[5000,'5K'],[9999,'5K'],
    [10000,'10K'],[19999,'10K'],[20000,'20K'],[50000,'50K'],[100000,'100K'],[999999,'500K'],[1000000,'1M']])
    assert.equal(monthlyBoughtLabel(value), label + '+ bought in past month', String(value));
});

test('best sellers rank the whole page before the five limit, with current rank as the tie-breaker', () => {
  const products = Array.from({length:20}, (_, i) => {
    const p = {...product(i), monthlySold:i < 10 ? 100 : 2000};
    p.stats.current[3] = 100 - i;
    return p;
  });
  assert.deepEqual(topSellerItems(products, 'cupping', '', now).map(p => p.asin),
    [19,18,17,16,15].map(i => product(i).asin));
  assert.deepEqual(topSellerItems(products.reverse(), 'cupping', '', now).map(p => p.asin),
    [19,18,17,16,15].map(i => product(i).asin));
});

test('invalid sold/rank values sort last without inventing facts; invalid prices and duplicate ASINs cannot take slots', () => {
  const products = [product(0), product(1), product(2), product(3), product(4), product(5), product(6)];
  products[0].monthlySold = '999999'; products[0].stats.current[3] = 1;
  products[1].monthlySold = 100; products[1].stats.current[3] = -1;
  products[2].monthlySold = 100; products[2].stats.current[3] = 20;
  products[3].monthlySold = 100; products[3].stats.current[3] = 10;
  products[4].monthlySold = 10000; products[4].stats.current[18] = products[4].stats.current[1] = -1;
  products[5].monthlySold = 100; products[5].stats.current[3] = '1';
  products[6].monthlySold = 0; products[6].stats.current[3] = 2;
  assert.deepEqual(topSellerItems([products[3], ...products], 'cupping', '', now).map(p => p.asin),
    [3,2,1,5,0].map(i => product(i).asin));
});

test('best sellers reuse search relevance (all words, exact models, accents, prefixes and typos) and adult filters', () => {
  const make = (i, title, sold = 100) => ({...product(i), title, monthlySold:sold});
  const products = [make(0, 'Sony WH-1000XM5 wireless headphones'), make(1, 'Wireless headphones Sony WH-1000XM4'),
    make(2, 'Replacement ear pads for Sony WH-1000XM4', 99999), make(3, 'Travel kettle', 99999),
    make(4, 'Sony WH-1000XM5 wireless headphones sex toy', 99999)];
  const exclude = '(?:^|[^\\p{L}\\p{N}])(?:sex[ -]+toy)(?![\\p{L}\\p{N}])';
  assert.deepEqual(topSellerItems(products, 'sony 1000xm5 headphnes', exclude, now).map(p => p.asin), [product(0).asin]);
  assert.deepEqual(topSellerItems([make(5, 'Café electric kettles')], 'cafe kett', '', now).map(p => p.asin), [product(5).asin]);
  assert.deepEqual(topSellerItems(products, 'sex toy', exclude, now), []);
});

test('published flag enables five results, a separate cache version and just one paid page with the same six-hour cache', async () => {
  config.analyze_top_sellers = true; config.analyze_exclude = ''; config.tokens_per_day = 10000;
  const transport = globalThis.fetch;
  globalThis.fetch = async (...args) => {
    const data = await (await transport(...args)).json();
    data.products.forEach((p, i) => { p.monthlySold = (i + 1) * 100; });
    return Response.json(data);
  };
  const first = await body(await run());
  assert.equal(first.items.length, 5); assert.equal(calls.length, 1);
  assert.deepEqual(first.items.map(p => p.monthlySold), [1500,1400,1300,1200,1100]);
  assert.equal(calls[0][0].searchParams.get('page'), '0');
  assert([...cache.keys()].every(url => new URL(url).searchParams.get('v') === '2'));
  assert.deepEqual(await body(await run()), first); assert.equal(calls.length, 1, 'cache does not spend again');
  config.analyze_top_sellers = false;
  now += 1001;
  const off = await body(await run());
  assert.equal(off.items.length, 10); assert.equal(calls.length, 2, 'OFF cannot reuse ON body');
  assert(off.items.every(p => !('monthlySold' in p)), 'OFF omits the new field even when Keepa supplies it');
});

test('top sellers keep ASIN analysis at one exact product and no extra product reads', async () => {
  config.analyze_top_sellers = true; config.analyze_exclude = '';
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]); assert.equal(url.pathname, '/product');
    assert.equal(url.searchParams.get('asin'), product(7).asin);
    return Response.json({products:[{...product(7), monthlySold:1200}, product(8)], tokensConsumed:1});
  };
  const response = await onRequest({...context, request:new Request('https://wowpriced.test/api/search?asin=' + product(7).asin)});
  const result = await body(response);
  assert.equal(result.items.length, 1); assert.equal(result.items[0].asin, product(7).asin);
  assert.equal(calls.length, 1);
});

test('published top-seller switch fails closed for non-booleans or missing filters before storage/upstream', async () => {
  for (const value of [null, 0, 1, 'true', 'false', [], {}]) {
    config.analyze_top_sellers = value;
    assert.equal((await body(await run())).reason, 'disabled');
  }
  config.analyze_top_sellers = true;
  assert.equal((await body(await run())).reason, 'disabled');
  assert.equal(calls.length, 0); assert.equal(values.size, 0);
});

// Task 366: the public grade contains no historical input, and shares every cap/cache.
test('deal grades include exact A/B/C boundaries, missing values and no D or F', () => {
  for (const [price, expected] of [[8499,'A'],[8500,'A'],[8501,'B'],[9700,'B'],[9701,'C'],[99999,'C']]) {
    const grade = gradeDeal({price_cents:price, avg90:10000}, DEAL_SETTINGS);
    assert.equal(grade.grade, expected); assert.deepEqual(Object.keys(grade), ['grade','label','facts']);
    assert(!JSON.stringify(grade).includes('avg'));
  }
  for (const price of [undefined, null, 0, -1, NaN, Infinity, '100', 10.5, true]) {
    assert.deepEqual(gradeDeal({price_cents:price, avg90:10000, verified_price_cents:1000}, DEAL_SETTINGS),
      {grade:'C', label:'Fair price', facts:[]});
  }
  assert.equal(gradeDeal({}, DEAL_SETTINGS).grade, 'C');
  assert.equal(gradeDeal({price_cents:100, avg90:1000}, {}).grade, 'C');
});
test('list discount is a fallback only, and verified threshold is inclusive', () => {
  assert.equal(gradeDeal({price_cents:8000, list_price_cents:10000}, DEAL_SETTINGS).grade,'B');
  assert.equal(gradeDeal({price_cents:8001, list_price_cents:10000}, DEAL_SETTINGS).grade,'C');
  assert.equal(gradeDeal({price_cents:8000, list_price_cents:10000, avg90:7000}, DEAL_SETTINGS).grade,'C');
  for (const avg90 of [-1, null, 0, '10000']) assert.equal(gradeDeal({price_cents:8000, list_price_cents:10000, avg90}, DEAL_SETTINGS).grade,'B');
  assert.equal(gradeDeal({price_cents:1020, verified_price_cents:1000}, DEAL_SETTINGS).grade,'A');
  assert.equal(gradeDeal({price_cents:1021, verified_price_cents:1000}, DEAL_SETTINGS).grade,'C');
  assert.deepEqual(gradeDeal({price_cents:8000, list_price_cents:10000, verified_price_cents:8000}, DEAL_SETTINGS).facts,
    ['20% below the list price','Verified deal on wowpriced']);
  assert.equal(gradeDeal({price_cents:8000, avg90:10000}, {...DEAL_SETTINGS,a_max_avg_ratio:.7}).grade,'B');
});
const liveDeal = () => ({key:'one', url:'https://www.amazon.com/dp/B000000000?tag=example-20', status:'alive', badge:'verified', final_price:13.99});
test('only live public verified offers can confer verified grade credit', () => {
  assert.equal(publishedDealPrice({deals:[liveDeal()]},'B000000000', NOW),1399);
  // Published = price-checked; the badge only counts agreeing sources (single source = 'unverified').
  for (const badge of ['unverified','corroborated',undefined])
    assert.equal(publishedDealPrice({deals:[{...liveDeal(),badge}]},'B000000000', NOW),1399);
  for (const change of [{status:'dead'},{locked:true},{price_unconfirmed:true},{price_aged:true},{click_price_unknown:true},
    {final_price:0},{final_price:'13.99'},{url:'https://amazon.com.evil/dp/B000000000'},
    {url:'https://www.amazon.com/dp/B000000001'},{url:'javascript:alert(1)'}]) {
    assert.equal(publishedDealPrice({deals:[{...liveDeal(),...change}]},'B000000000', NOW),undefined);
  }
  assert.equal(publishedDealPrice({spotlight:[{...liveDeal(),spotlight:{until:new Date(NOW-1).toISOString()}}]},'B000000000',NOW),undefined);
  assert.equal(publishedDealPrice({spotlight:[{...liveDeal(),spotlight:{until:new Date(NOW+1000).toISOString()}}]},'B000000000',NOW),1399);
  // One legitimate source is enough only when the prices file says so (A30, 2026-10-22).
  const single = {...liveDeal(), headline:'Reported by one source — not yet confirmed.'};
  assert.equal(publishedDealPrice({deals:[single]},'B000000000', NOW),undefined);
  assert.equal(publishedDealPrice({deals:[single],single_source_ok:'yes'},'B000000000', NOW),undefined);
  assert.equal(publishedDealPrice({deals:[single],single_source_ok:true},'B000000000', NOW),1399);
  assert.equal(publishedDealPrice({deals:[{...single,click_price_unknown:true}],single_source_ok:true},'B000000000', NOW),undefined);
});
function enableDealGrade(p = product()) {
  config.deal_spotter = {...DEAL_SETTINGS};
  context.env.ASSETS.fetch = async req => Response.json(new URL(req.url).pathname === '/search-config.json' ? config : {deals:[]});
  globalThis.fetch = async (url, options) => {
    calls.push([url,options]); assert.equal(url.pathname,'/product');
    assert.equal(url.searchParams.get('stats'),'1'); assert.equal(url.searchParams.get('history'),'0');
    assert.equal(url.searchParams.get('update'),'0');
    return Response.json({products:[p], tokensConsumed:1});
  };
  return suffix => onRequest({...context,request:new Request('https://wowpriced.test/api/search?asin='+p.asin+(suffix || ''))});
}
test('checker then deal grade reuses one cache and never exposes private average', async () => {
  const p = product(); p.stats.avg90[18]=2000;
  const request = enableDealGrade(p);
  const checker = await body(await request()); assert(!checker.deal_grade && !checker._deal);
  const result = await body(await request('&grade=deal'));
  assert.equal(result.deal_grade.grade,'A'); assert.deepEqual(result.items,checker.items); assert.equal(calls.length,1);
  assert.equal(JSON.parse(values.get('search:v1:2026-10-17')).tokens,1);
  for (const word of ['avg90','_deal','history',KEY]) assert(!JSON.stringify(result).includes(word));
  const cached = await [...cache.values()][0].clone().json(); assert.equal(cached._deal.avg90,2000);
  assert(!Object.hasOwn(cached,'deal_grade'));
});
test('deal grade then normal checker keeps checker output unchanged and recalculates live membership', async () => {
  const request = enableDealGrade();
  context.env.ASSETS.fetch = async req => Response.json(new URL(req.url).pathname === '/search-config.json' ? config : {deals:[liveDeal()]});
  const result = await body(await request('&grade=deal')); assert.equal(result.deal_grade.grade,'A');
  context.env.ASSETS.fetch = async req => Response.json(new URL(req.url).pathname === '/search-config.json' ? config : {deals:[]});
  assert.equal((await body(await request('&grade=deal'))).deal_grade.grade,'C');
  const checker = await body(await request()); assert.deepEqual(checker,{items:result.items}); assert.equal(calls.length,1);
});
test('deal grade reads the slim prices file, never the full deals.json (10 ms CPU budget)', async () => {
  const request = enableDealGrade(), paths = [];
  context.env.ASSETS.fetch = async req => {const path = new URL(req.url).pathname; paths.push(path);
    return Response.json(path === '/search-config.json' ? config : {deals:[liveDeal()]});};
  assert.equal((await body(await request('&grade=deal'))).deal_grade.grade,'A');
  assert(paths.includes('/deal-spotter-prices.json') && !paths.includes('/deals.json'));
});
test('deal average follows the same qualified fresh price series as the checker', async () => {
  for (const kind of ['new','stale','unqualified','same-price']) {
    cache.clear(); now += 61000;
    const p = product(); p.stats.avg90[1]=1000; p.stats.avg90[18]=10000;
    if (kind==='new') p.stats.current[18]=-1;
    if (kind==='stale') p.stats.lastBuyBoxUpdate=minute(now)-1441;
    if (kind==='unqualified') p.stats.buyBoxIsUnqualified=true;
    if (kind==='same-price') {p.stats.current[18]=p.stats.current[1]; p.stats.buyBoxIsUnqualified=true;}
    const request = enableDealGrade(p);
    assert.equal((await body(await request('&grade=deal'))).deal_grade.grade,'C',kind);
  }
});
test('deal option is gated, ASIN-only and cannot bypass the shared daily or minute caps', async () => {
  const request = enableDealGrade();
  config.tokens_per_day=109;
  assert.equal((await body(await request('&grade=deal'))).reason,'budget_exhausted');
  assert.equal(calls.length,0); config.tokens_per_day=3000; config.max_per_minute=0;
  assert.equal((await body(await request('&grade=deal'))).reason,'rate_limited');
  config.max_per_minute=20; config.deal_spotter.enabled=false;
  assert.equal((await body(await request('&grade=deal'))).reason,'disabled');
  delete config.deal_spotter;
  assert.equal((await body(await request('&grade=deal'))).reason,'disabled');
  assert.equal((await request('&grade=deal&grade=deal')).status,400);
  assert.equal((await onRequest({...context,request:new Request('https://wowpriced.test/api/search?q=tea&grade=deal')})).status,400);
  assert.equal(calls.length,0);
});
test('missing price still gives C and failed inventory cannot award A', async () => {
  const p = product(); p.stats.current[1]=-1; p.stats.current[18]=-1;
  const request=enableDealGrade(p);
  context.env.ASSETS.fetch = async req => {if(new URL(req.url).pathname==='/search-config.json') return Response.json(config); throw Error('offline');};
  const result = await body(await request('&grade=deal'));
  assert.deepEqual(result.items,[]);assert.deepEqual(result.deal_grade,{grade:'C',label:'Fair price',facts:[]});
});
// Task 367: the review route uses these same real SQLite budget fixtures.
const reviewConfig = {enabled:true, min_reviews:100, min_rating:4.3, steady_rating_delta:.2,
  pace_min_reviews:50, pace_max_fraction:.25, popular_monthly_sold:100, grade_a_checks:4, grade_b_checks:2};
function reviewRun(suffix='') {
  return onRequest({...context, request:new Request('https://wowpriced.test/api/search?asin=B000000000&grade=reviews'+suffix)});
}
function reviewUpstream(receipt=2) {
  globalThis.fetch = async (url, options) => {
    calls.push([url,options]); assert.equal(url.origin,'https://api.keepa.com');
    assert.equal(url.pathname,'/product'); assert.equal(url.searchParams.get('rating'),'1');
    assert.equal(url.searchParams.get('stats'),'30'); assert.equal(url.searchParams.get('history'),'0');
    assert.equal(url.searchParams.get('update'),'0');
    const p=product(); p.stats.current[16]=45; p.stats.current[17]=100;
    p.stats.avg180=Array(18).fill(-1); p.stats.avg180[16]=44;
    p.stats.atIntervalStart=Array(18).fill(-1); p.stats.atIntervalStart[17]=75;
    p.monthlySold=200; p.stats.current[4]=2999;
    return Response.json({products:[p,product(1)],tokensConsumed:receipt});
  };
}
test('review route settles its extra token, caches only safe data, and isolates ordinary ASIN results', async () => {
  config.review_checker={...reviewConfig}; config.analyze_top_sellers=true; config.analyze_exclude=''; reviewUpstream();
  const result=await body(await reviewRun()); assert.equal(result.review.grade,'A'); assert.equal(calls.length,1);
  assert.deepEqual(Object.keys(result.items[0]),['asin','title','image','price_cents','as_of']);
  assert.equal(result.items.length,1); assert.equal(JSON.parse([...values.values()][0]).tokens,2);
  assert(!JSON.stringify(result).includes('monthlySold')); assert(!JSON.stringify(result).includes('PRIVATE'));
  assert.equal(JSON.stringify(result.review).match(/[0-9]/),null);
  assert.deepEqual(await body(await reviewRun()),result); assert.equal(calls.length,1);
  assert([...cache.keys()].every(url=>new URL(url).searchParams.get('grade')==='reviews'));
  config.review_checker.enabled=false;
  assert.equal((await body(await reviewRun())).reason,'disabled'); assert.equal(calls.length,1);
});
test('review policy changes cannot reuse previous grades', async () => {
  config.review_checker={...reviewConfig}; reviewUpstream(); await reviewRun(); now+=1001;
  config.review_checker.min_rating=5;
  assert.equal((await body(await reviewRun())).review.grade,'B'); assert.equal(calls.length,2);
});
test('review caps, missing bindings, and invalid route fail before paid dispatch', async () => {
  reviewUpstream();
  assert.equal((await body(await reviewRun())).reason,'disabled');
  config.review_checker={...reviewConfig}; config.tokens_per_day=109;
  assert.equal((await body(await reviewRun())).reason,'budget_exhausted'); config.tokens_per_day=3000;
  config.max_per_minute=0; assert.equal((await body(await reviewRun())).reason,'rate_limited'); config.max_per_minute=20;
  assert.equal((await reviewRun('&grade=reviews')).status,400);
  assert.equal((await reviewRun('&q=tea')).status,400);
  delete context.env.SEARCH_LIMITS;
  assert.equal((await body(await reviewRun())).reason,'budget_unavailable'); assert.equal(calls.length,0);
});
test('review untrusted receipts retain the reservation and oversize receipts halt the shared budget', async () => {
  config.review_checker={...reviewConfig}; reviewUpstream(null); await reviewRun();
  assert.equal(JSON.parse([...values.values()][0]).tokens,110);
  cache.clear(); now+=1001; reviewUpstream(111);
  assert.equal((await body(await reviewRun())).reason,'budget_exhausted');
  assert.equal(JSON.parse([...values.values()][0]).tokens,Number.MAX_SAFE_INTEGER);
});
test('review requests share search cooldown and the same daily ledger', async () => {
  config.review_checker={...reviewConfig}; await run(); reviewUpstream();
  assert.equal((await body(await reviewRun())).reason,'rate_limited'); assert.equal(calls.length,1);
  now+=1001; await reviewRun(); assert.equal(JSON.parse([...values.values()][0]).tokens,22);
});
// Fresh single-product prices: update=0 on every ASIN lookup (never a name search),
// a longer wait only where the page shows a 10-25 s progress dialog.
test('a live refresh gets 10 s on Deal Spotter and Review Checker, 4 s elsewhere, and keeps its reservation', async () => {
  config.deal_spotter = {...DEAL_SETTINGS}; config.review_checker = {...reviewConfig};
  let signal, url;
  globalThis.fetch = async (requested, options) => {
    url = requested; signal = options.signal; return {ok:true, arrayBuffer:() => new Promise(() => {})};
  };
  const get = path => onRequest({...context, request:new Request('https://wowpriced.test/api/search?' + path)});
  mock.timers.enable({apis:['setTimeout']});
  try {
    const cases = [['asin=B000000001', 4000, '0'], ['asin=B000000002&grade=deal', 10000, '0'],
      ['asin=B000000003&grade=reviews', 10000, '0'], ['q=kettle', 4000, null]];
    for (const [i, [path, wait, update]] of cases.entries()) {
      signal = null; now += 2000;
      const pending = get(path);
      while (!signal) await new Promise(resolve => setImmediate(resolve));
      assert.equal(url.searchParams.get('update'), update, path);
      mock.timers.tick(wait - 1);
      for (let turn = 0; turn < 5; turn++) await new Promise(resolve => setImmediate(resolve));
      assert(!signal.aborted, path);
      mock.timers.tick(1);
      assert.deepEqual(await body(await pending), {items:[], reason:'timeout'}, path);
      assert(signal.aborted, path);
      assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 110 * (i + 1), path);
    }
  } finally { mock.timers.reset(); }
});
test("a live refresh's extra token settles from Keepa's receipt", async () => {
  const p = product();
  enableDealGrade(p);
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]); assert.equal(url.searchParams.get('update'), '0');
    return Response.json({products:[p], tokensConsumed:2});
  };
  const result = await body(await onRequest({...context,
    request:new Request('https://wowpriced.test/api/search?asin=' + p.asin + '&grade=deal')}));
  assert.equal(result.items[0].as_of, new Date(now).toISOString().slice(0, 17) + '00.000Z');
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 2);
});
