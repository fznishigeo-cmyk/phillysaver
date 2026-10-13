// Run the emitted handlers and card renderer offline. No retailer requests.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const root = process.argv[2];
const home = fs.readFileSync(root + '/index.html', 'utf8');
const payload = JSON.parse(fs.readFileSync(root + '/deals.json', 'utf8'));
const decode = text => text.replace(/&(?:amp|lt|gt|quot|#x27|#39);/g,
  c => ({'&amp;':'&','&lt;':'<','&gt;':'>','&quot;':'"','&#x27;':"'",'&#39;':"'"})[c]);
function attributes(tag) {
  return Object.fromEntries(Array.from(tag.matchAll(/([\w-]+)(?:="([^"]*)")?/g),
    m => [m[1], decode(m[2] || '')]));
}
function fragment(text, start, end) {
  const a = text.indexOf(start), b = text.indexOf(end, a);
  assert(a >= 0 && b > a, start);
  return text.slice(a, b);
}
function element(attrs, scope, card) {
  return {attrs, hidden:'hidden' in attrs,
    getAttribute:name => attrs[name] ?? null,
    hasAttribute:name => name in attrs,
    classList:{contains:name => (attrs.class || '').split(' ').includes(name)},
    closest(selector) {
      if (selector === '.card') return card;
      if (selector.includes('sponsored')) return (attrs.rel || '').split(' ').includes('sponsored') &&
        (!selector.includes('data-deal-outbound') || 'data-deal-outbound' in attrs) ? this : null;
      if (selector.includes('data-copy-code') || selector.includes('data-code'))
        return 'data-copy-code' in attrs || 'data-code' in attrs ? this : null;
      return null;
    }, scope};
}
function harness(html, path, {clipboard='ok', dynamic=false, tracking=true}={}) {
  const handlers = {}, events = [], writes = [], status = {textContent:'',hidden:true};
  const scope = {
    contains:el => el.scope === scope,
    addEventListener(type, fn) {(handlers[type] ||= []).push(fn);},
    querySelector(selector) {return selector === '[data-deal-detail]' && html.includes('data-deal-detail>') ? {}
      : selector === '.code-status' && html.includes('class="code-status"') ? status : null;},
    querySelectorAll:() => buttons
  };
  const anchors = Array.from(html.matchAll(/<a\b([^>]*)>/g), m => element(attributes(m[1]), scope));
  const buttons = Array.from(html.matchAll(/<button\b([^>]*)>/g), m => element(attributes(m[1]), scope))
    .filter(b => b.hasAttribute('data-copy-code'));
  const ctx = {location:{pathname:path}, navigator:{},
    document:{getElementById:id => id === 'content' && path === '/' ? scope : id === 'toast' ? status : null,
      querySelector:() => path === '/' ? null : scope},
    window:tracking ? {wpTrack:(name,params)=>events.push({name,params:JSON.parse(JSON.stringify(params))})} : {},
    state:{deals:payload.deals,meta:payload.meta,newKeys:{}},
    dealKey:d=>d.key, track:(name,params)=>events.push({name,params:JSON.parse(JSON.stringify(params))}),
    savedButtonClick:()=>false, savedOutbound(){}, recordFind(){}, nextStep(){},
    panel:{querySelector:()=>({addEventListener(){}})},
    isLocked:d=>!!d.locked, lockedHtml:()=>'<article class="card locked">locked</article>',
    mediaHtml:()=>'', num:v=>v==null?null:Number(v), newSinceVisit:()=>false,
    hotHtml:()=>'', ageText:()=>'', isNew:()=>false, lit:()=>false, isFreePick:()=>false,
    traceSvg:()=>'', TIER_LABEL:{}, spotlightTime:()=>'',
    clock:{tag:''}, dataKey:m=>m.generated_at, maybeReloadForNewVersion(){},
    fetch:async()=>({ok:true,headers:{get:()=>''},json:async()=>ctx.nextPayload})
  };
  ctx.nextPayload = payload;
  if (clipboard !== 'none') ctx.navigator.clipboard = {writeText(code) {
    writes.push(code);
    if (clipboard === 'throw') throw Error('clipboard denied');
    return clipboard === 'failed' ? Promise.reject(Error('clipboard denied')) : Promise.resolve();
  }};
  vm.createContext(ctx);
  if (dynamic) {
    vm.runInContext(fragment(home, '  function esc(s)', '  function sortVal'), ctx);
    vm.runInContext(fragment(home, '  function cardHtml(d)', '  // ---- the deal ticker ----'), ctx);
    const a = home.lastIndexOf('  document.getElementById("content").addEventListener("click"');
    vm.runInContext(home.slice(a, home.indexOf('  // The ticker:', a)), ctx);
    vm.runInContext(fragment(home, '  function fetchPublish()', '  function isNewPublish('), ctx);
  }
  const actions = Array.from(html.matchAll(/<script>([\s\S]*?)<\/script>/g), m=>m[1])
    .find(s=>s.includes('Static deals have explicit labels'));
  assert(actions);
  vm.runInContext(actions, ctx);
  function click(el, options={}) {
    const e = {type:'click',button:0,target:el,...options,
      preventDefault(){throw Error('navigation prevented');}, stopPropagation(){throw Error('bubbling stopped');}};
    for (const fn of handlers[e.type] || []) fn(e);
  }
  return {ctx,scope,anchors,buttons,events,writes,status,click};
}
const flush = () => new Promise(resolve=>setImmediate(resolve));
(async () => {
  for (const [path, kind] of [[payload.deals[0].deal_path,'deal'],['/best-deals-today/','best_today'],
    ['/deals/home-kitchen/','category'], ['/stores/walmart/','store'],['/deals/under-25/','price']]) {
    const html = fs.readFileSync(root + path + 'index.html', 'utf8');
    const h = harness(html,path);
    assert.equal(h.events.filter(e=>e.name==='deal_detail_view').length,kind==='deal'?1:0);
    const links = h.anchors.filter(a=>a.hasAttribute('data-deal-outbound'));
    assert(links.length >= (kind === 'deal' ? 2 : 1)); // primary and related on detail
    for (const link of links) {
      const before = h.events.length;
      h.click(link,{ctrlKey:true});
      assert.equal(h.events.length,before+1);
      assert.deepEqual(h.events.at(-1),{name:'deal_click',params:{store:link.attrs['data-store'],
        category:link.attrs['data-category'],method:kind}});
      for (const options of [{metaKey:true},{shiftKey:true},{altKey:true},{type:'click',button:1},
                            {type:'auxclick',button:1},{type:'auxclick',button:2},{button:0,detail:0}]) {
        const count = h.events.length;
        h.click(link,options);
        assert.equal(h.events.length-count,(options.button===2 || options.type==='click' && options.button===1)?0:1);
      }
    }
    for (const link of h.anchors.filter(a=>!a.hasAttribute('data-deal-outbound'))) {
      const before = h.events.length; h.click(link); assert.equal(h.events.length,before);
    }
    assert(h.events.every(e=>Object.keys(e.params).every(k=>['store','category','method'].includes(k))));
  }
  const detailPath=payload.deals[0].deal_path, detail=fs.readFileSync(root+detailPath+'index.html','utf8');
  for (const clipboard of ['ok','failed','throw','none']) {
    const h=harness(detail,detailPath,{clipboard});
    assert(h.buttons.length);
    assert.equal(h.buttons[0].hidden,clipboard==='none');
    h.click(h.buttons[0]); await flush();
    assert.equal(h.events.filter(e=>e.name==='deal_code_copy').length,clipboard==='ok'?1:0);
    assert.match(h.status.textContent,clipboard==='ok'?/Code copied:/:/Select the code/);
    assert.equal(h.status.hidden,false);
    assert(!JSON.stringify(h.events).includes(h.buttons[0].attrs['data-copy-code']));
  }
  const h=harness(home,'/',{dynamic:true});
  const staticGet=h.anchors.find(a=>a.hasAttribute('data-deal-outbound'));
  h.click(staticGet); assert.equal(h.events.length,1); // both real handlers registered
  h.click(staticGet,{type:'auxclick',button:1}); assert.equal(h.events.length,2);
  for (const phase of ['first','refresh']) {
    if(phase==='refresh') h.ctx.nextPayload={...payload,deals:payload.deals.map(d=>({...d,item:'Renamed title'}))};
    const pub=await h.ctx.fetchPublish(); h.ctx.state.deals=pub.deals;
    for (const d of pub.deals) {
      const html=h.ctx.cardHtml(d), title=html.match(/<h3 class="title">([\s\S]*?)<\/h3>/)[1];
      assert.match(title,new RegExp('href="'+(d.deal_path || d.url).replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'"'));
      if(d.deal_path) assert(!title.includes('sponsored'));
      assert.equal(attributes(html.match(/<a class="getdeal"([^>]*)>/)[1]).href,d.url);
      const card={getAttribute:()=>d.key};
      const outbound=element({href:d.url,rel:'sponsored noopener',class:'getdeal'},h.scope,card);
      const before=h.events.length; h.click(outbound,{metaKey:true});
      assert.equal(h.events.length,before+1); assert.equal(h.events.at(-1).params.method,'home');
      h.click(outbound,{type:'click',button:1}); assert.equal(h.events.length,before+1);
      h.click(outbound,{type:'auxclick',button:1}); assert.equal(h.events.length,before+2);
      if(d.deal_path) {
        const before=h.events.length; h.click(element({href:d.deal_path},h.scope,card));
        assert.equal(h.events.length,before);
      }
      const fallback=h.ctx.cardHtml({...d,deal_path:undefined});
      assert.equal(attributes(fallback.match(/<h3 class="title"><a([^>]*)>/)[1]).href,d.url);
      assert.match(fallback,/<h3 class="title"><a[^>]*rel="sponsored/);
      const unsafe=h.ctx.cardHtml({...d,deal_path:'//attacker.test/'}); assert(!unsafe.includes('attacker.test'));
      const dead=h.ctx.cardHtml({...d,status:'dead'}); assert(!dead.includes('rel="sponsored') && !dead.includes('data-code='));
      const locked=h.ctx.cardHtml({...d,locked:true}); assert(!locked.includes('href='));
    }
  }
  const noTags=harness(detail,detailPath,{tracking:false});
  noTags.click(noTags.anchors.find(a=>a.hasAttribute('data-deal-outbound')));
  noTags.click(noTags.buttons[0]); await flush(); assert.equal(noTags.events.length,0);
  console.log('Static detail/listing/related, copy, first/refresh titles and dynamic clicks passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
