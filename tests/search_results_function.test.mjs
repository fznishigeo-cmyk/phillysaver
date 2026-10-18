import test, {beforeEach, afterEach} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DatabaseSync} from 'node:sqlite';

const source = fs.readFileSync(new URL('../functions/api/search.js', import.meta.url), 'utf8');
const {onRequest, normalizeQuery, currentItems, settle} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
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
  // The runtime cancels a holder whose browser aborted: its lease lapses.
  now += 15000;
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
      asin:url.searchParams.get('asin')});
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
