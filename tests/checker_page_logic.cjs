const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');

(async () => {
  const html = fs.readFileSync(process.argv[2], 'utf8');
  const data = id => html.match(new RegExp('<script id="' + id + '" type="application/json">(.*?)</script>'))[1];
  const script = html.match(/<script>("use strict";[\s\S]*?)<\/script>/)[1];
  // Parse the whole shipped script, then run its actual pure functions.
  new vm.Script(script);
  const ctx = vm.createContext({URL, URLSearchParams, TextEncoder, crypto:webcrypto,
    document:{getElementById:id => ({textContent:data(id)})}});
  vm.runInContext(script.slice(0, script.indexOf('\n(function () {')), ctx);
  for (const row of JSON.parse(fs.readFileSync(process.argv[3], 'utf8'))) {
    ctx.input = row.url;
    const aliases = JSON.parse(JSON.stringify(vm.runInContext('checkAliases(input)', ctx)));
    assert.deepEqual(aliases, row.aliases, row.url);
    for (const [count, expected] of Object.entries(row.shards)) {
      ctx.count = Number(count);
      const actual = await vm.runInContext('Promise.all(checkAliases(input).map(a => checkShard(a, count)))', ctx);
      assert.deepEqual(Array.from(actual), expected, row.url + ' shards=' + count);
    }
  }

  // Exercise the actual page controller offline even where Chromium cannot
  // launch. The separate browser journey checks native focus/layout/network.
  class Element {
    constructor(tag='div') {this.tag=tag;this.children=[];this.listeners={};this.value='';this.content='';}
    set textContent(value) {this.content=value;this.children=[];}
    get textContent() {return this.content+this.children.map(c=>c.textContent).join('');}
    append(...children) {this.children.push(...children);}
    replaceChildren(...children) {this.content='';this.children=children;}
    addEventListener(name, handler) {this.listeners[name]=handler;}
    nodes() {return [this,...this.children.flatMap(c=>c.nodes())];}
  }
  const entries={};
  const folder=path.join(path.dirname(process.argv[2]),'data');
  for (const file of fs.readdirSync(folder).filter(n=>n!=='meta.json')) Object.assign(entries,JSON.parse(fs.readFileSync(path.join(folder,file))));
  const now=Date.parse(entries['bestbuy:1234567'].at);
  const today=new Date(now).toLocaleDateString('en-US',{year:'numeric',month:'short',day:'numeric',timeZone:'UTC'});
  function harness(prefill='') {
    const elements={};
    for (const id of ['check-rules','check-options','check-form','product-url','check-result']) elements[id]=new Element();
    elements['check-rules'].textContent=data('check-rules');elements['check-options'].textContent=data('check-options');
    const requests=[];
    const storage=new Map();
    const c=vm.createContext({URL,URLSearchParams,TextEncoder,crypto:webcrypto,
      Date:class extends Date {static now(){return now;}},
      location:{search:prefill?'?u='+encodeURIComponent(prefill):''},
      history:{replaceState(s,t,url){c.share=url;}},
      sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
      document:{getElementById:id=>elements[id],createElement:tag=>new Element(tag)},
      fetch:async (url,options)=>{requests.push({url,options});return {ok:true,json:async()=>entries};}});
    vm.runInContext(script,c);
    return {c,elements,requests,storage,result:elements['check-result'],submit(value) {
      elements['product-url'].value=value;
      elements['product-url'].listeners.input();
      elements['check-form'].listeners.submit({preventDefault(){}});
    }};
  }
  async function done(h) {
    for (let i=0;i<100;i++) {
      await new Promise(resolve=>setTimeout(resolve,5));
      if (!h.result.textContent.includes('Checking our verified records')) return;
    }
    throw Error('page controller did not complete');
  }
  const h=harness();
  const run=async value=>{h.submit(value);await done(h);};
  await run('https://www.bestbuy.com/site/widget/1234567.p');
  assert.equal(h.result.children[0].textContent,entries['bestbuy:1234567'].verdict);
  assert(h.result.textContent.includes('checked just now'));
  assert(h.result.textContent.includes('via Slickdeals'));
  const credit=h.result.nodes().find(n=>n.tag==='a'&&n.textContent==='Slickdeals');
  assert.equal(credit.href,'https://slickdeals.net/f/1');assert.equal(credit.rel,'noopener');
  assert(h.result.nodes().some(n=>n.tag==='time'&&n.dateTime&&n.title));
  assert(h.result.nodes().some(n=>n.tag==='a'&&n.textContent==='Price history'));
  assert.equal(new URL(h.c.share,'https://wowpriced.com').searchParams.get('u'),'https://www.bestbuy.com/site/widget/1234567.p');
  await run('https://www.target.com/p/widget/-/A-12345678');
  assert(h.result.textContent.includes('Last verified price $10.00 on '+today+' — this deal has ended'));
  assert(!h.result.nodes().some(n=>n.tag==='a'&&n.textContent==='See the deal'));
  await run('https://home.woot.com/offers/a-good-widget');
  assert.equal(h.result.textContent,'Widget wootSee the deal');
  await run('https://www.amazon.com/dp/B012345678');
  assert(h.result.textContent.includes('Amazon price as of '+entries['amazon:B012345678'].amazon_as_of));
  assert(h.result.textContent.includes(JSON.parse(data('check-options')).disclaimer));
  await run('https://shop.example/products/a-good-widget');
  assert(h.result.nodes().some(n=>n.className==='check-meta'&&n.textContent.startsWith('shop.example · checked')));
  await run('https://shop.example/products/unverified-air-fryer?secret=local-only');
  assert(h.result.textContent.includes("We haven't verified a price for this product yet."));
  assert.equal(h.result.nodes().find(n=>n.tag==='a').href,'/');
  assert.equal(h.storage.size,0);
  for (const [url,words] of [
    ['https://shop.example/products/unverified-air-fryer','unverified air fryer'],
    ['https://www.ebay.com/itm/123456789013?hash=itemabc123',''],
    ['https://www.amazon.com/dp/B000000001',''],
    ['https://www.amazon.com/Echo/dp/B000000001/ref=abc123','Echo'],
    ['https://www.bestbuy.com/site/searchpage.jsp?st=blender',''],
    ['https://shop.example/products/Some-Great-Blender','Some Great Blender'],
    ['https://shop.example/products/widget','widget']
  ]) {
    await run(url);
    const search=h.result.nodes().find(n=>n.tag==='a');
    assert.equal(search.href,'/');
    assert.equal(search.textContent,words?'Search our deals for “'+words+'”':'Search our deals');
    h.storage.clear();
    if (words) {
      search.listeners.click();
      const memory=JSON.parse(h.storage.get('wp.browse'));
      assert.equal(memory.entries[memory.last].query,words);
    } else assert.equal(search.listeners.click,undefined);
  }
  // Invalid memory resets safely; existing entries survive the handoff.
  for (const raw of ['null','{"version":2}','{"version":1,"entries":[]}',
    '{"version":1,"last":"old","entries":{"old":{"query":"previous"}}}']) {
    h.storage.set('wp.browse',raw);
    h.result.nodes().find(n=>n.tag==='a').listeners.click();
    const memory=JSON.parse(h.storage.get('wp.browse'));
    assert.equal(memory.entries[memory.last].query,'widget');
    if (raw.includes('previous')) assert.equal(memory.entries.old.query,'previous');
  }
  h.c.sessionStorage.getItem=()=>{throw Error('blocked');};
  assert.doesNotThrow(()=>h.result.nodes().find(n=>n.tag==='a').listeners.click());
  const before=h.requests.length;
  await run('https://amzn.to/example');
  assert.equal(h.result.textContent,'Open the link first, then paste the full product address');
  assert.equal(h.requests.length,before);
  await run('javascript:alert(1)');assert.equal(h.requests.length,before);
  for (const {url,options} of h.requests) {
    assert.match(url,/^\/check\/data\/[a-f0-9]{2}\.json$/);
    assert.equal(options.referrerPolicy,'no-referrer');assert.equal(options.redirect,'error');assert.equal(options.credentials,'omit');
  }
  const prefill=harness('https://www.amazon.com/dp/B012345678');await done(prefill);
  assert.equal(prefill.elements['product-url'].value,'https://www.amazon.com/dp/B012345678');
  assert(prefill.result.textContent.includes('Amazon price as of'));
  // Old/failed requests cannot become a not-found answer or overwrite a later result.
  let release;
  h.c.fetch=()=>new Promise(resolve=>{release=resolve;});
  h.submit('https://www.amazon.com/dp/B012345678');
  for(let i=0;i<100&&!release;i++) await new Promise(resolve=>setTimeout(resolve,5));
  assert(release);
  h.c.fetch=async()=>({ok:true,json:async()=>entries});
  await run('https://home.woot.com/offers/a-good-widget');
  release({ok:true,json:async()=>entries});await new Promise(resolve=>setTimeout(resolve,5));
  assert.equal(h.result.textContent,'Widget wootSee the deal');
  // Editing an in-flight lookup clears its status even without another submit.
  release=null;
  h.c.fetch=()=>new Promise(resolve=>{release=resolve;});
  h.submit('https://www.amazon.com/dp/B012345678');
  for(let i=0;i<100&&!release;i++) await new Promise(resolve=>setTimeout(resolve,5));
  assert(release);assert(h.result.textContent.includes('Checking our verified records'));
  h.elements['product-url'].value='https://shop.example/new-input';
  h.elements['product-url'].listeners.input();
  assert.equal(h.result.textContent,'');
  release({ok:true,json:async()=>entries});await new Promise(resolve=>setTimeout(resolve,5));
  assert.equal(h.result.textContent,'');
  h.c.fetch=async()=>({ok:false});await run('https://www.amazon.com/dp/B012345678');
  assert.equal(h.result.textContent,"We couldn't load our verified records. Please try Check again.");
  // Absolute hover dates accompany each relative clock, including older checks.
  const sample=entries['bestbuy:1234567'];
  h.c.fetch=async()=>({ok:true,json:async()=>({'bestbuy:1234567':{...sample,credit:[null,{},
    {name:'<b>literal</b>',url:'javascript:alert(1)'},{name:'Plain',url:'/relative'},
    {name:'Safe',url:'https://example.com/credit'}]}})});
  await run('https://www.bestbuy.com/site/widget/1234567.p');
  assert(h.result.textContent.includes('via <b>literal</b>, Plain, Safe'));
  assert(!h.result.nodes().some(n=>n.tag==='a'&&['<b>literal</b>','Plain'].includes(n.textContent)));
  assert.equal(h.result.nodes().find(n=>n.tag==='a'&&n.textContent==='Safe').rel,'noopener');
  for(const [minutes,expected] of [[1,'1 minute ago'],[65,'1 hour ago'],[125,'2 hours ago'],[1500,'1 day ago'],[3000,'2 days ago']]) {
    h.c.fetch=async()=>({ok:true,json:async()=>({'bestbuy:1234567':{...sample,at:new Date(now-minutes*60000).toISOString()}})});
    await run('https://www.bestbuy.com/site/widget/1234567.p');assert(h.result.textContent.includes('checked '+expected));
  }
})().catch(e => {console.error(e);process.exitCode=1;});
