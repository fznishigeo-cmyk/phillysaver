// Offline checks of emitted code, with the homepage as the discount-URL oracle.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8').split('// TOOL_PAGE_MOUNT')[0];
const home = fs.readFileSync(process.argv[3], 'utf8');
const fixture = () => ({items:Array.from({length:6}, (_, i) => ({asin:'B00000010' + i,
  title:'Steel kitchen kettle ' + i, price_cents:1500 + i * 100, list_price_cents:3000,
  image:'https://m.media-amazon.com/images/I/photo.jpg', monthlySold:1000 * (i + 1), as_of:new Date().toISOString()}))});
const deal = {key:'verified', item:'Steel kitchen kettle', status:'alive', final_price:15, regular_price:30,
  regular_price_basis:'List price', badge:'verified', url:'https://www.amazon.com/dp/B012345678?tag=old',
  image_url:'https://m.media-amazon.com/images/I/photo.jpg', store:'Amazon', category:'kitchen'};

function harness(mode='checker', reduced=false, response=fixture()) {
  let now = 0, serial = 0;
  const timers = new Map(), ticks = [], calls = [], events = [];
  const makeElement = () => ({hidden:true, textContent:'', innerHTML:'', dataset:{}, style:{},
    attributes:{}, setAttribute(key, value){this.attributes[key] = value;}});
  const ids = Object.fromEntries(['q','analyzer','analyzer-title','tool-status','tool-progress','tool-result',
    'top-sellers','verified-deals'].map(id => [id, makeElement()]));
  ids.q.value = 'kettle';
  ids['tool-progress'].firstElementChild = makeElement();
  for (const id of ['top-sellers', 'verified-deals']) {
    const grid = makeElement();
    ids[id].querySelector = selector => selector === '.product-grid' ? grid :
      grid.innerHTML.includes('class="product-card"') ? {} : null;
  }
  const steps = Array.from({length:mode === 'checker' ? 5 : 4}, (_, i) => {
    const el = makeElement(); el.textContent = 'step ' + i;
    Object.defineProperty(el.dataset, 'state', {get(){return this.value;}, set(value){
      this.value = value; if (value === 'done' || value === 'failed') ticks.push({index:i, state:value, at:now});
    }}); return el;
  });
  const ctx = {URL, AbortController, performance:{now:() => now}, console,
    document:{body:{dataset:{tool:mode}}, getElementById:id => ids[id], querySelectorAll:() => steps},
    matchMedia:() => ({matches:reduced}), window:{wpTrack:(...args) => events.push(args)},
    setTimeout:(fn, ms) => {timers.set(++serial, {fn, at:now + ms}); return serial;},
    clearTimeout:id => timers.delete(id),
    fetch:async url => {calls.push(url); return {ok:true, json:async () =>
      url === '/deals.json' ? {deals:[deal, {...deal, key:'dead', status:'dead'}, {...deal, key:'locked', locked:true},
        {...deal, key:'unknown', price_unconfirmed:true}, {...deal, key:'ordinary', badge:'corroborated'},
        {...deal, key:'adult', item:'vibrator kettle'}]} : response};}};
  vm.createContext(ctx); vm.runInContext(source, ctx);
  return {ctx, ids, steps, ticks, calls, events, now:() => now,
    async settle(promise) {
      let finished=false, failure;
      promise.then(() => {finished=true;}, e => {failure=e;finished=true;});
      for (let turns=0; !finished && turns<100; turns++) {
        for (let i=0;i<30;i++) await Promise.resolve();
        if (finished) break;
        const next = [...timers].sort((a,b) => a[1].at-b[1].at)[0];
        assert(next, 'pending operation without a timer');
        timers.delete(next[0]); now=next[1].at; next[1].fn();
      }
      assert(finished, 'analyzer did not settle'); if (failure) throw failure;
    }};
}

const h = harness();
for (const [price,list,kind,pct] of [[60,100,'big',40],[1,100,'big',99],[6001,10000,'good',39],
  [85,100,'good',15],[8501,10000,'live'],[86,100,'live'],[100,100,'live'],[120,100,'live'],
  [10,null,'live'],[10,undefined,'live'],[10,0,'live'],[0,100,'live'],[-1,100,'live'],
  [NaN,100,'live'],[10,Infinity,'live'],[10,'100','live'],[true,100,'live'],[10,100.1,'live']]) {
  const badge=h.ctx.toolBadge(price,list);
  assert.equal(badge.kind,kind);
  assert.equal(badge.label,kind === 'live' ? 'Live Amazon price' :
    `${kind === 'big' ? 'Big discount' : 'Good discount'} · ${pct}% off list price`);
}

function homepageFunction(name) {
  const m=home.match(new RegExp('  function '+name+'\\([^\\n]*\\) \\{\\n[\\s\\S]*?\\n  \\}'));
  assert(m,name); return m[0];
}
let raw, opened=[];
const oracle={URL, document:{getElementById:() => ({value:raw})}, searchText:() => raw.trim(),
  window:{open:url => opened.push(url)}, track:() => {}};
vm.createContext(oracle);
for (const name of ['searchAmazonQuery','discountsLink','searchAmazonClick']) vm.runInContext(homepageFunction(name),oracle);
for (raw of ['mouse pad',' café  & "tea" ','抹茶 茶碗',
  'https://amazon.com/Nulaxy-Adjustable-Anti-Slip-Stand/dp/B012345678?tag=PRIVATE#PRIVATE',
  'amazon.com/Caf%C3%A9-Tea_%E8%8C%B6%E7%A2%97/gp/product/B012345678',
  'https://amazon.com/dp/B012345678','https://amazon.com/gp/product/B012345678',
  'https://amazon.com/one-two-three-four-five-six-seven-eight-nine/dp/B012345678',
  'https://amazon.com/adult-toys/dp/B012345678','https://amazon.com/%ZZ/dp/B012345678',
  'https://amazon.com/name%40example.com/dp/B012345678','https://a.co/secret',
  'https://amzn.to/secret','https://amazon.com.evil.test/Tea/dp/B012345678',
  'https://user@amazon.com/Tea/dp/B012345678','https://example.com','name@example.com']) {
  h.ids.q.value=raw;
  oracle.searchAmazonClick({type:'click',button:0,preventDefault(){},target:{closest:s => s === '.all-discounts'}});
  assert.equal(h.ctx.toolDiscount(raw).url,opened.at(-1),raw);
}
assert.equal(h.calls.length,0,'discount URL calculation must never fetch');
assert.equal(h.ctx.toolLink('https://amazon.com/dp/B012345678?tag=old'),
  'https://www.amazon.com/dp/B012345678?tag=example-20');
assert.equal(h.ctx.toolLink('javascript:alert(1)'), '');
assert.equal(h.ctx.toolLink('https://user@amazon.com/dp/B012345678'), '');
assert(h.ctx.matchesSearch(deal,h.ctx.searchWords('ketle')),'reuse homepage typo matching');
assert(!h.ctx.matchesSearch({...deal,item:'Kettle 6500'},h.ctx.searchWords('65')),'exact number matching');
assert(!h.ctx.toolDealCard({...deal,needs_steps:true}),'never omit required price steps');
assert(!h.ctx.toolDealCard({...deal,regular_price_basis:'previous basis'}).includes('<s>'));
assert(!h.ctx.toolLiveCard({...fixture().items[0],monthlySold:null},false).includes('bought in past month'));
assert(h.ctx.toolLiveCard({...fixture().items[0],title:'<img onerror="x">'},true).includes('&lt;img'));

(async () => {
  for (const mode of ['checker','finder']) for (const reduced of [false,true]) {
    const t=harness(mode,reduced);
    await t.settle(t.ctx.toolSubmit({preventDefault(){}}));
    assert.deepEqual(t.ticks.map(t => t.index),Array.from({length:mode === 'checker' ? 5 : 4},(_,i) => i));
    assert(t.ticks.every(t => t.state === 'done'));
    assert.equal(t.now(),reduced ? 0 : 9000);
    assert.equal(t.ids['tool-result'].hidden,false);
    assert.equal(t.ids['analyzer'].attributes['aria-busy'],'false');
    assert.equal(t.calls.filter(url => url.startsWith('/api/')).length,mode === 'checker' ? 1 : 0);
    assert(!t.ids['verified-deals'].hidden,'matching verified deals render');
    assert.equal((t.ids['verified-deals'].querySelector('.product-grid').innerHTML.match(/class="product-card"/g) || []).length,1);
    if (mode === 'checker') {
      assert.equal(t.calls[1],'/api/search?q=kettle');
      assert.equal((t.ids['top-sellers'].querySelector('.product-grid').innerHTML.match(/class="product-card"/g) || []).length,5);
      assert(t.ids['tool-result'].innerHTML.includes('Big discount · 50% off list price'));
      await t.settle(t.ctx.toolSubmit({preventDefault(){}}));
      assert.equal(t.calls.filter(url => url.startsWith('/api/')).length,1,'repeat cannot spend twice');
    }
    assert(!JSON.stringify(t.events).includes('kettle'),'never track typed words');
  }
  const single=harness('checker',true,{items:[{...fixture().items[0],asin:'B012345678'}]});
  single.ids.q.value='https://amazon.com/dp/B012345678?tag=PRIVATE';
  await single.settle(single.ctx.toolSubmit({preventDefault(){}}));
  assert.equal(single.calls[1],'/api/search?asin=B012345678');
  assert(single.ids['top-sellers'].hidden,'ASIN endpoint does not invent related products');
  const limited=harness('checker',true,{items:[],reason:'budget_exhausted'});
  await limited.settle(limited.ctx.toolSubmit({preventDefault(){}}));
  assert(limited.ids['tool-result'].innerHTML.includes("Today&#39;s live price checks have reached their limit."));
  assert.equal(limited.ticks[0].state,'failed');
  assert(!limited.ids['verified-deals'].hidden,'verified matches survive endpoint failures');
  const delayed=harness('checker');
  const fastFetch=delayed.ctx.fetch;
  delayed.ctx.fetch=url => url.startsWith('/api/') ? new Promise(resolve => delayed.ctx.setTimeout(() =>
    resolve({json:async () => fixture()}),3000)) : fastFetch(url);
  await delayed.settle(delayed.ctx.toolSubmit({preventDefault(){}}));
  assert(delayed.ticks[0].at>=3000,'cannot mark found before the response exists');
  assert.equal(delayed.now(),9000,'a slower response still fits the reading time');
  const timeout=harness('checker',true);
  let attempts=0;
  const inventoryFetch=timeout.ctx.fetch;
  timeout.ctx.fetch=url => {if (url.startsWith('/api/')) {attempts++;return new Promise(() => {});} return inventoryFetch(url);};
  await timeout.settle(timeout.ctx.toolSubmit({preventDefault(){}}));
  assert.equal(attempts,3,'same six-second timeout and three attempts as the homepage');
  assert.equal(timeout.now(),21000);
  assert(timeout.ids['tool-result'].innerHTML.includes('The price check took too long. Please try again.'));
  assert(timeout.ticks.every(t => t.state === 'failed'),'no fake completed steps on timeout');
  const noInventory=harness('finder');
  noInventory.ctx.fetch=() => new Promise(() => {});
  await noInventory.settle(noInventory.ctx.toolSubmit({preventDefault(){}}));
  assert.equal(noInventory.now(),9000,'unavailable published data cannot leave an analyzer waiting');
  assert.equal(noInventory.ids['tool-result'].hidden,false);
  assert.equal(noInventory.ids['verified-deals'].hidden,true);
  const adult=harness('checker',true); adult.ids.q.value='vibrator';
  await adult.settle(adult.ctx.toolSubmit({preventDefault(){}}));
  assert(!adult.calls.some(url => url.startsWith('/api/')),'adult queries cannot spend');
  for (const raw of ['vibrator','https://a.co/secret','name@example.com']) {
    const invalid=harness('finder',true);invalid.ids.q.value=raw;
    await invalid.settle(invalid.ctx.toolSubmit({preventDefault(){}}));
    assert(invalid.ticks.every(t => t.state === 'failed'),'unresolved product cannot tick found');
    assert(invalid.ids['tool-result'].innerHTML.includes('https://www.amazon.com/deals?tag=example-20'));
    assert(!invalid.calls.some(url => url.startsWith('/api/')));
  }
  console.log('Tool logic passed: badges, homepage URL parity, real ordered steps, timing, cache, limits, safe cards and matching');
})().catch(error => {console.error(error);process.exitCode=1;});
