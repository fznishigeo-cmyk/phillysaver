import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {createClickHandler, ADD_CLICK} from './clicks.js';
import worker from './worker.js';
const key = '0123456789abcdef';
const hour = () => Math.floor(Date.now() / 3600000);
const payload = (overrides = {}) => ({key, hour:hour(), surface:'wall', position:'1-8', ...overrides});
function request(body = payload(), headers = {}, method = 'POST', path = '/c') {
  return new Request('https://test.workers.dev' + path, {method, headers:{Origin:'https://wowpriced.com', 'User-Agent':'Mozilla/5.0', ...headers},
    ...(method === 'POST' ? {body: typeof body === 'string' ? body : JSON.stringify(body)} : {})});
}
function fixture(extra = {}) {
  const statements = [];
  const env = {CLICK_SIGNAL_ENABLED:'true', CLICK_SIGNAL_HOURLY_CAP:'1000', CLICK_SIGNAL_ISOLATE_PER_MINUTE:'120',
    CLICK_SIGNAL_DEAL_PER_MINUTE:'12', CLICK_SIGNAL_ORIGINS:'https://wowpriced.com,https://www.wowpriced.com', CLICK_SIGNAL_TOKEN:'fixture-token',
    CLICKS:{prepare(sql) {return {bind(...args) {
      statements.push({sql,args}); return {run:async () => ({}), all:async () => ({results:[{hour:hour()-1,total:1000,quota:1000,counts:JSON.stringify({[key]:1000})}]})};
    }}; }}, ...extra};
  return {env, statements, handle:createClickHandler()};
}
test('aggregate SQL enforces the global hourly quota with an atomic row guard (real SQLite)', () => {
  const py = `import json,sqlite3,sys\nc=sqlite3.connect(':memory:')\na=json.loads(sys.argv[1])\nc.executescript(a['schema'])\nfor i in range(100):\n c.execute(a['sql'],(100,'0123456789abcdef','$."0123456789abcdef"',3))\nc.execute(a['sql'],(100,'fedcba9876543210','$."fedcba9876543210"',3))\nc.execute(a['sql'],(101,'fedcba9876543210','$."fedcba9876543210"',3))\nprint(json.dumps(c.execute('SELECT * FROM click_hours ORDER BY hour').fetchall()))`;
  const result = spawnSync('python3', ['-c',py,JSON.stringify({sql:ADD_CLICK,schema:readFileSync(new URL('./clicks-schema.sql',import.meta.url),'utf8')})], {encoding:'utf8',timeout:5000});
  assert.equal(result.status, 0, result.stderr);
  const rows = JSON.parse(result.stdout); assert.equal(rows.length,2); assert.equal(rows[0][1],3);
  assert.deepEqual(JSON.parse(rows[0][2]),{[key]:3}); assert.equal(rows[1][1],1);
});
test('router, disabled/missing bindings and store failure are silent and never use KV', async () => {
  const f = fixture(); assert.equal((await worker.fetch(request(),f.env)).status,204); assert.equal(f.statements.length,1);
  for (const extra of [{CLICK_SIGNAL_ENABLED:'false'},{CLICKS:null},{CLICK_SIGNAL_HOURLY_CAP:'99999'},
    {CLICKS:{prepare(){throw Error('private failure')}}}]) {
    const g=fixture(extra); const r=await g.handle(request(),g.env);
    assert.equal(r.status,204); assert.equal(await r.text(),''); assert.equal(g.statements.length,0);
    assert.equal(r.headers.get('Set-Cookie'),null);
  }
});
test('privacy, bots, foreign origins and invalid/oversized bodies never write', async () => {
  const requests = [request(payload(),{'Cookie':'tracking=private'}),request(payload(),{'Sec-GPC':'1'}),request(payload(),{'X-WP-Opt-Out':'1'}),
    request(payload(),{Origin:'https://evil.test'}),request(payload(),{'User-Agent':'Googlebot'}),
    request(payload(),{'User-Agent':'HeadlessChrome'}),request(payload(),{'User-Agent':''}),
    request(payload({key:'private text'})),request(payload({surface:'email'})),request(payload({position:'999'})),
    request(payload({hour:hour()-1})),request(payload({hour:hour()+1})),request(payload({email:'private'})),
    request('not json'),request('x'.repeat(300)),request(payload(),{'Content-Length':'300'})];
  for (const req of requests) {const f=fixture(); assert.equal((await f.handle(req,f.env)).status,204); assert.equal(f.statements.length,0);}
});
test('best effort anonymous per-deal and isolate limits before database calls', async () => {
  const f=fixture({CLICK_SIGNAL_DEAL_PER_MINUTE:'2'});
  for(let i=0;i<9;i++) await f.handle(request(),f.env);
  assert.equal(f.statements.length,2);
  const g=fixture({CLICK_SIGNAL_ISOLATE_PER_MINUTE:'2'});
  for(let i=0;i<9;i++) await g.handle(request(payload({key:String(i).padStart(16,'0')})),g.env);
  assert.equal(g.statements.length,2);
});
test('bounded authenticated read has complete closed hours, absolute counts and capped status', async () => {
  const f=fixture();
  const read = token => request(null,{Authorization:token},'GET','/c/counts?since='+(hour()-2));
  assert.equal((await f.handle(read('wrong'),f.env)).status,401); assert.equal(f.statements.length,0);
  const res=await f.handle(read('Bearer fixture-token'),f.env), body=await res.json();
  assert.equal(res.headers.get('Cache-Control'),'no-store');
  assert.equal(body.hours.length,2); assert.deepEqual(body.hours[0].counts,{});
  assert.deepEqual(body.hours[1].counts,{[key]:1000}); assert.equal(body.hours[1].capped,true);
  assert(f.statements[0].sql.includes('INSERT OR IGNORE')); assert(f.statements[1].sql.includes('hour<?')); assert(f.statements[2].sql.includes('LIMIT 24'));
  assert.equal(body.hours[0].available,false); assert.equal(body.hours[1].available,true);
  await f.handle(read('Bearer fixture-token'),f.env);
  assert.equal((await f.handle(read('Bearer fixture-token'),f.env)).status,429);
  const down=fixture({CLICKS:{prepare(){throw Error('private failure')}}});
  assert.equal((await down.handle(read('Bearer fixture-token'),down.env)).status,503);
});
test('slow request bodies cannot spend an earlier hour quota after the clock changes', async () => {
  const original = Date.now;
  const start = Math.floor(original() / 3600000) * 3600000 + 3599999;
  Date.now = () => start;
  try {
    const f = fixture(), req = request(), bytes = new TextEncoder().encode(JSON.stringify(payload()));
    let read = false;
    const slow = {url:req.url, method:req.method, headers:req.headers, body:{getReader:() => ({read:async () => {
      Date.now = () => start + 2;
      if (read) return {done:true};
      read = true; return {done:false,value:bytes};
    }})}};
    assert.equal((await f.handle(slow,f.env)).status,204);
    assert.equal(f.statements.length,0);
  } finally { Date.now = original; }
});
