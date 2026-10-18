import test, {beforeEach, afterEach} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DatabaseSync} from 'node:sqlite';

const source = fs.readFileSync(new URL('../functions/api/search.js', import.meta.url), 'utf8');
const {onRequest, normalizeQuery, currentItems} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
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
  assert.equal(JSON.parse([...values.values()][0]).tokens, 110);
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
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 110);
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
    assert.deepEqual(await body(await run('first')), {items:[], reason:'keepa_error', detail:'http_429'});
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
    assert.deepEqual(result, {items:[], reason:'keepa_error', detail:details[i]}); assert(!JSON.stringify(result).includes(KEY));
  }
  now += 2000; globalThis.fetch = () => Response.json({error:{type:'INVALID_PARAMETER', message:KEY}});
  assert.deepEqual(await body(await run('typed')), {items:[], reason:'keepa_error', detail:'body_error_INVALID_PARAMETER'});
  now += 2000; globalThis.fetch = () => Response.json({error:{type:'bad <type> ' + KEY}});
  assert.deepEqual(await body(await run('untyped')), {items:[], reason:'keepa_error', detail:'body_error'});
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
  assert.deepEqual(await body(await run('moved')), {items:[], reason:'keepa_error', detail:'http_302'});
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
  assert.equal(JSON.parse([...values.values()][0]).tokens, 110);
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
  assert.equal(database.prepare('SELECT tokens FROM search_budget').get().tokens, 110);
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
  assert.deepEqual(await body(await runAsin()), {items:[], reason:'keepa_error', detail:'http_429'});
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
  assert.deepEqual(await body(await runAsin('B000000001')), {items:[], reason:'keepa_error', detail:'fetch_Error'});
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
