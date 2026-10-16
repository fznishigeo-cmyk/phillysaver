// Actual shipped app/controller/worker code under Node; all requests are stubs.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const root = process.argv[2];
const home = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const scripts = html => Array.from(html.matchAll(/<script>([\s\S]*?)<\/script>/g), m => m[1]);
const appScript = scripts(home).find(s => s.includes('function wpAndroidContext()'));
const trackingScript = scripts(home).find(s => s.includes('window.wpTrack = function'));
const asSiteScript = fs.readFileSync(process.argv[3], 'utf8');

class Element {
  constructor(tag='div') {this.tag=tag;this.children=[];this.listeners={};this.value='';this.content='';}
  set textContent(value) {this.content=value;this.children=[];}
  get textContent() {return this.content+this.children.map(c=>c.textContent).join('');}
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.content='';this.children=children;}
  addEventListener(name, handler) {this.listeners[name]=handler;}
  nodes() {return [this,...this.children.flatMap(c=>c.nodes())];}
}
function context(options={}) {
  const classes = new Set(), storage = options.storage || {}, listeners = {}, requests = [], registrations=[];
  const doc = {referrer:options.referrer || '', documentElement:{classList:{add:k=>classes.add(k),contains:k=>classes.has(k)}},
    addEventListener:(name,fn)=>{(listeners[name] ||= []).push(fn);},
    createElement:tag=>new Element(tag), head:{appendChild:s=>requests.push(s.src)}};
  const ctx = vm.createContext({URL, URLSearchParams, TextEncoder, crypto:webcrypto,
    location:{search:options.search || '', href:'https://wowpriced.com/', origin:'https://wowpriced.com', pathname:'/', hash:''},
    navigator:{serviceWorker:{register:url=>{registrations.push(url);return Promise.resolve();}}},
    document:doc, localStorage:{getItem:()=>null},
    history:{replaceState:(s,t,url)=>{ctx.shared=url;}},
    sessionStorage:{getItem:k=>{if(options.blocked)throw Error('blocked');return storage[k]||null;},
      setItem:(k,v)=>{if(options.blocked)throw Error('blocked');storage[k]=v;}}});
  if (options.accessorBlocked) Object.defineProperty(ctx,'sessionStorage',{get(){throw Error('blocked getter');}});
  ctx.window=ctx;
  vm.runInContext(options.asSite ? asSiteScript : appScript,ctx);
  return {ctx,classes,storage,listeners,requests,registrations};
}
(async () => {
  for (const [options,expected] of [
    [{},false], [{search:'?src=other'},false], [{search:'?src=twa'},true],
    [{search:'?src=other&src=twa'},true], [{referrer:'android-app://com.wowpriced.app/'},true],
    [{referrer:'android-app://com.wowpriced.app'},true], [{referrer:'android-app://com.wowpriced.app.evil/'},false],
    [{referrer:'https://wowpriced.com/?src=twa'},false], [{storage:{wp_app:'android'}},true],
    [{search:'?src=twa',blocked:true},true], [{search:'?src=twa',accessorBlocked:true},true],
    [{blocked:true},false], [{accessorBlocked:true},false]
  ]) {
    const h=context(options);
    assert.equal(h.classes.has('in-app'),expected,JSON.stringify(options));
    assert.deepEqual(h.registrations,expected?['/sw.js']:[]);
    if (expected && !options.blocked && !options.accessorBlocked) assert.equal(h.storage.wp_app,'android');
  }
  const first=context({search:'?src=twa'});
  assert(context({storage:first.storage}).classes.has('in-app'));
  assert(context({referrer:'android-app://com.wowpriced.app/'}).classes.has('in-app'));
  for (const app of [false,true]) {
    const h=context({search:app?'?src=twa':''});
    vm.runInContext(trackingScript,h.ctx);
    assert.equal(h.requests.some(s=>s.includes('connect.facebook.net')), !app);
    assert.equal(h.requests.some(s=>s.includes('clarity.ms')), !app);
    assert(h.requests.some(s=>s.includes('googletagmanager.com')));
    const config=Array.from(h.ctx.dataLayer).map(a=>Array.from(a)).find(a=>a[0]==='config')[2];
    assert.equal(config.app_context,app?'android':undefined);
  }
  for (const [value,expected] of [
    ['https://www.amazon.com/dp/B000000000?tag=example-20&keep=1','https://www.amazon.com/dp/B000000000?keep=1'],
    ['https://amazon.com/dp/B000000000?tag=one&tag=two','https://amazon.com/dp/B000000000'],
    ['https://smile.amazon.co.uk/dp/B000000000?tag=example-20','https://smile.amazon.co.uk/dp/B000000000'],
    ['https://www.amazon.com.evil/dp/B000000000?tag=example-20','https://www.amazon.com.evil/dp/B000000000?tag=example-20'],
    ['https://www.bestbuy.com/site/example/6505727.p?tag=original','https://www.bestbuy.com/site/example/6505727.p?tag=original']
  ]) {
    const h=context({search:'?src=twa'}), link={href:value};
    assert.equal(h.listeners.click.length,1);
    h.listeners.click[0]({target:{closest:()=>link}});
    assert.equal(link.href,expected);
    const full=context({search:'?src=twa',asSite:true}), unchanged={href:value};
    assert(!full.classes.has('app-amazon-limited'));
    full.listeners.click[0]({target:{closest:()=>unchanged}});
    assert.equal(unchanged.href,value);
  }
  const html=fs.readFileSync(path.join(root,'check/index.html'),'utf8');
  const reader=scripts(html).find(s=>s.includes('function show(entry, parts)'));
  const json=id=>html.match(new RegExp('<script id="'+id+'" type="application/json">(.*?)</script>'))[1];
  function harness(search='',app=true,asSite=false) {
    const h=context({search:search+(app?(search?'&':'?')+'src=twa':''),asSite});
    const nodes={};
    for (const id of ['check-rules','check-options','check-form','product-url','check-result']) nodes[id]=new Element();
    nodes['check-rules'].textContent=json('check-rules');nodes['check-options'].textContent=json('check-options');
    h.ctx.document.getElementById=id=>nodes[id];
    h.fetches=[];
    h.ctx.fetch=async (url,options)=>{
      assert.match(url,/^\/check\/data\/[0-9a-f]{2}\.json$/);
      assert.equal(options.referrerPolicy,'no-referrer');
      h.fetches.push(url);
      return {ok:true,json:async()=>JSON.parse(fs.readFileSync(path.join(root,url)))};
    };
    vm.runInContext(reader,h.ctx);
    return {...h,nodes,result:nodes['check-result']};
  }
  async function done(h) {
    for(let i=0;i<100;i++) {
      await new Promise(resolve=>setTimeout(resolve,5));
      if (!h.result.textContent.includes('Checking our verified records')) return;
    }
    throw Error('lookup did not complete');
  }
  const best='https://www.bestbuy.com/site/example/6505727.p';
  for(const search of [
    '?text='+encodeURIComponent('Look '+best+' https://example.invalid/second'),
    '?url='+encodeURIComponent(best), '?title='+encodeURIComponent('Buy '+best),
    '?text=no-link&url='+encodeURIComponent(best), '?u='+encodeURIComponent(best)+'&text=https://example.invalid/ignore'
  ]) {
    const h=harness(search);await done(h);
    assert.equal(h.nodes['product-url'].value,best);
    assert(h.result.textContent.includes('Widget bestbuy'));
    assert(h.result.textContent.includes('$10.00'));
    assert(h.fetches.length>0);
  }
  const title=harness('?title=Headphones');await done(title);
  assert.match(title.result.textContent,/We haven't verified a price/);assert.equal(title.fetches.length,0);
  for(const app of [true,false]) {
    const short=harness('?text='+encodeURIComponent('Look https://a.co/example'),app);await done(short);
    assert.match(short.result.textContent,/Open the link first/);
    assert.equal(short.result.textContent.includes('Open it in Chrome, then share the product page from there.'),app);
    assert.equal(short.fetches.length,0);
  }
  for(const [app,asSite,hasPrice] of [[true,false,false],[false,false,true],[true,true,true]]) {
    const h=harness('?u=https://www.amazon.com/dp/B000000000',app,asSite);await done(h);
    assert.equal(h.result.textContent.includes('$10.00'),hasPrice);
    assert.equal(h.result.nodes().some(n=>n.textContent==='Price history'),hasPrice);
    assert(h.result.nodes().some(n=>n.textContent==='See the deal'));
    if (!hasPrice) assert.equal(h.result.textContent,'Kitchen Widget 0See the deal');
  }

  // Run the actual worker lifecycle: no other response ever enters a cache.
  const worker=fs.readFileSync(path.join(root,'sw.js'),'utf8');
  const handlers={},added=[],deleted=[],fetched=[];
  let offline=false;
  const cache={add:async url=>added.push(url),match:async url=>{assert.equal(url,'/offline.html');return 'offline-page';}};
  const w=vm.createContext({self:{addEventListener:(name,fn)=>{assert(!handlers[name]);handlers[name]=fn;},
    skipWaiting:async()=>{},clients:{claim:async()=>{}}},
    caches:{open:async()=>cache,keys:async()=>['unrelated-cache','wp-android-old'],delete:async key=>deleted.push(key)},
    fetch:async request=>{fetched.push(request);if(offline)throw Error('offline');return 'network-response';}});
  vm.runInContext(worker,w);
  for(const name of ['install','activate']) {let work;handlers[name]({waitUntil:p=>{work=p;}});await work;}
  assert.deepEqual(added,['/offline.html']);assert.deepEqual(deleted,['wp-android-old']);
  let response;
  handlers.fetch({request:{mode:'cors'},respondWith(){throw Error('non-navigation intercepted');}});
  assert.equal(fetched.length,0);
  for(const failed of [false,true]) {
    offline=failed;
    handlers.fetch({request:{mode:'navigate'},respondWith:p=>{response=p;}});
    assert.equal(await response,failed?'offline-page':'network-response');
  }
  assert.deepEqual(added,['/offline.html']);
  console.log('Android context, tracking, delegated links, share controller and worker passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
