// Execute the published component with a fake clock, storage, DOM and Google SDK.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync(process.argv[2], 'utf8');
const WEEK = 7 * 24 * 60 * 60 * 1000;
const flush = () => new Promise(resolve => setImmediate(resolve));
function harness({pathname = '/', now = 1000, stored = {}, blocked = '', enabled = true,
                  delay = 15, google = false, api = 'https://api.test', width = 1000} = {}) {
  const handlers = {}, events = [], requests = [], scripts = [], timers = [];
  let nextTimer = 0;
  const storage = new Map(Object.entries(stored));
  let boundary = 200, googleCallback, googleOptions, redirected;
  let authResult = {status:200, body:{me:{email:'verified@example.com', email_verified:true}, token:'unused'}};
  let subResult = {status:200, body:{ok:true}};
  const document = {hidden:false, addEventListener(type, cb) {handlers[type] = cb;}};
  const rect = () => ({top:10, bottom:50, width:100, height:40, left:0, right:100});
  function node(id, hidden = false) {
    return {id, hidden, isConnected:true, disabled:false, parentNode:{clientWidth:400},
      focus() {document.activeElement = this;}, setAttribute(k,v) {this[k] = v;},
      style:{setProperty(k,v) {this[k] = v;}},
      getClientRects() {return this.hidden || (['join-email','submit','join-back'].includes(id) && fields.hidden) ||
        (id === 'join-email-choice' && one.hidden) || (id === 'legal' && legal.hidden) ? [] : [rect()];}, getBoundingClientRect:rect,
      closest(s) {return s === '#' + id || (id === 'close' && s === '[data-join-close]') ? this : null;}};
  }
  const prompt = node('join-prompt', true), choice = node('join-email-choice'), fields = node('join-email-fields', true);
  const input = node('join-email'), submit = node('submit'), message = node('message', true);
  input.value = ' email@example.com ';
  const close = node('close'), legal = node('legal'), opener = node('opener');
  const one = node('join-step-one'), back = node('join-back'), success = node('join-success', true);
  const successTitle = node('join-title'), deals = node('join-deals', true), subscribe = node('subscribe');
  const provider = node('join-google', true), providerMessage = node('join-google-message', true);
  const dialog = node('dialog'), panel = node('paywall', true), bar = node('subbar', true);
  const nodes = Object.fromEntries([prompt, choice, fields, input, provider, providerMessage, panel, bar, one, back, success, successTitle, deals, subscribe].map(n => [n.id,n]));
  nodes['join-legal'] = legal;
  const form = {matches:s=>s === '.joinform', querySelector:s=>s.includes('input') ? input : s.includes('button') ? submit : message};
  fields.querySelector = s => s.includes('button') ? submit : message;
  const focusable = [close, choice, input, submit, back, legal, deals];
  prompt.querySelectorAll = () => focusable;
  prompt.querySelector = () => dialog;
  prompt.contains = dialog.contains = el => [...focusable, dialog, successTitle].includes(el);
  document.activeElement = opener;
  document.getElementById = id => id === 'content' ? {} : nodes[id];
  document.querySelector = () => ({getBoundingClientRect:()=>({bottom:100})});
  document.querySelectorAll = s => s.startsWith('#content .card')
    ? Array.from({length:12},()=>({getBoundingClientRect:()=>({bottom:boundary})})) : [];
  document.createElement = () => ({});
  document.head = {appendChild:s=>scripts.push(s)};
  const meta = {api_url:api, google_client_id:google ? 'configured-client' : '', join:{prompt:enabled,prompt_delay_s:delay}};
  const ctx = {document, location:{pathname,hash:'',replace(url){redirected=url;}}, Date:{now:()=>now},
    localStorage:{getItem(k) {if (blocked === 'read') throw Error('blocked'); return storage.get(k) || null;},
      setItem(k,v) {if (blocked === 'write') throw Error('quota'); storage.set(k,v);}},
    setTimeout(cb,ms) {const id=++nextTimer; timers.push({id,cb,at:now+ms}); return id;}, clearTimeout(id) {const t=timers.find(t=>t.id===id); if(t) t.cancelled=true;}, requestAnimationFrame:cb=>cb(),
    fetch:async(url,options)=>{requests.push({url,...options}); const r = url.endsWith('/api/google') ? authResult : subResult;
      if (r instanceof Error) throw r; return {status:r.status, json:async()=>r.body};},
    window:{innerHeight:800,innerWidth:width,scrollY:0,wpTrack:(name,params)=>events.push({name,params}),
      addEventListener(type,cb) {handlers[type] = cb;}}};
  vm.runInNewContext(code,ctx);
  ctx.window.wpSupport.refresh(meta);
  function click(target, modifiers={}) {let prevented=false; handlers.click({target, ...modifiers, preventDefault(){prevented=true;}}); return prevented;}
  function advance(ms) {now += ms; for (const t of timers.splice(0)) {if (t.cancelled) continue; if (t.at <= now) t.cb(); else timers.push(t);}}
  function engage() {click({closest:s=>s === '#cattabs a, #controls select, #controls input'});}
  function key(key,shiftKey=false) {let prevented=false; handlers.keydown({key,shiftKey,preventDefault(){prevented=true;}}); return prevented;}
  return {ctx, document, prompt, choice, fields, input, submit, message, close, legal, opener, panel, bar, storage, events, requests, scripts,
    provider, providerMessage, meta, handlers, advance, engage, click, key, one, back, success, successTitle, deals, subscribe, dialog,
    redirected:()=>redirected, type(value) {input.value=value; handlers.input({target:input});},
    refresh:()=>ctx.window.wpSupport.refresh(meta), boundary:n=>boundary=n,
    scroll(n) {ctx.window.scrollY=n; handlers.scroll();},
    submitEmail() {handlers.submit({target:form,preventDefault(){}});},
    auth:r=>authResult=r, sub:r=>subResult=r,
    loadGoogle(fail=false) {
      ctx.window.google = {accounts:{id:{initialize(o) {googleCallback=o.callback;}, renderButton(el,o) {if(fail) throw Error('SDK'); googleOptions=o;}}}};
      scripts[0].onload();
    }, googleOptions:()=>googleOptions, google:()=>googleCallback({credential:'google-id-token'})};
}
(async()=>{
  let h = harness();
  h.engage(); h.advance(14999); assert.equal(h.prompt.hidden,true);
  h.advance(1); assert.equal(h.prompt.hidden,false); assert.equal(h.document.activeElement,h.choice);
  assert.equal(h.storage.get('wp.join.promptUntil'),String(16000+WEEK));
  assert.equal(h.events[0].name,'signup_prompt_shown'); assert.equal(h.scripts.length,0);
  h.legal.focus(); assert.equal(h.key('Tab'),true); assert.equal(h.document.activeElement,h.close);
  assert.equal(h.key('Tab',true),true); assert.equal(h.document.activeElement,h.legal);
  h.handlers.focusin({target:h.opener}); assert.equal(h.document.activeElement,h.choice);
  h.click(h.choice); assert.equal(h.one.hidden,true); assert.equal(h.fields.hidden,false); assert.equal(h.choice['aria-expanded'],'true');
  assert.equal(h.successTitle.textContent,"What's your email?"); assert.equal(h.successTitle.className,'joinemailtitle');
  assert.equal(h.document.activeElement,h.input);
  assert.equal(h.submit.disabled,false);
  for (const value of ['', 'hello', 'a@', '@example.com', 'a@example', 'a@@example.com', 'a b@example.com']) {
    h.type(value); assert.equal(h.submit.disabled,true,value);
  }
  h.type(' person@example.com '); assert.equal(h.submit.disabled,false);
  h.click(h.back); assert.equal(h.fields.hidden,true); assert.equal(h.one.hidden,false);
  assert.equal(h.successTitle.textContent,'Join wowpriced'); assert.equal(h.successTitle.className,'');
  assert.equal(h.choice['aria-expanded'],'false'); assert.equal(h.document.activeElement,h.choice);
  h.click(h.choice); h.handlers.focusin({target:h.opener}); assert.equal(h.document.activeElement,h.input);
  h.key('Escape'); assert.equal(h.prompt.hidden,true); assert.equal(h.document.activeElement,h.opener);
  assert.equal(h.events.at(-1).name,'signup_prompt_closed');
  h.advance(WEEK+1); h.refresh(); assert.equal(h.prompt.hidden,true); // once in this view
  for (const closeBy of ['outside','close']) {
    h=harness(); h.advance(15000); h.engage(); h.click(closeBy==='outside' ? h.prompt : h.close);
    assert.equal(h.prompt.hidden,true); assert.equal(h.document.activeElement,h.opener);
  }
  h=harness(); h.advance(60000); assert.equal(h.prompt.hidden,true); // delay alone insufficient
  h.scroll(799); assert.equal(h.prompt.hidden,true); h.scroll(800); assert.equal(h.prompt.hidden,false);
  h=harness(); h.advance(15000); h.handlers.change({target:{closest:()=>true}}); assert.equal(h.prompt.hidden,false);
  for (const options of [{blocked:'read'}, {blocked:'write'}, {enabled:false}, {api:''},
    {pathname:'/join'}, {pathname:'/join/'}, {pathname:'/join.html'},
    {stored:{'wp.join.joined':'1'}}, {stored:{'wp.join.promptUntil':String(1000+WEEK)}}]) {
    h=harness(options); h.engage(); h.advance(60000); assert.equal(h.prompt.hidden,true,JSON.stringify(options));
    assert.equal(h.events.length,0);
  }
  h=harness({stored:{'wp.join.promptUntil':'1000'}}); h.engage(); h.advance(15000); assert.equal(h.prompt.hidden,false);
  h=harness({delay:1}); h.engage(); h.advance(14999); assert.equal(h.prompt.hidden,true);
  h.advance(1); assert.equal(h.prompt.hidden,false);
  h=harness({delay:30}); h.engage(); h.advance(15000); assert.equal(h.prompt.hidden,true);
  h.advance(15000); assert.equal(h.prompt.hidden,false);
  // Banner already up: the weekly prompt takes its place, never both, and the
  // banner stays away for the rest of this page view after the prompt closes.
  h=harness(); h.boundary(90); h.refresh(); assert.equal(h.panel.hidden,false);
  h.engage(); h.advance(15000); assert.equal(h.prompt.hidden,false);
  assert.equal(h.panel.hidden,true); assert.equal(h.bar.hidden,true);
  h.key('Escape'); h.refresh(); assert.equal(h.prompt.hidden,true);
  assert.equal(h.panel.hidden,true); assert.equal(h.bar.hidden,true);
  // A later page view (prompt already used this week) keeps the banner.
  h=harness({stored:{'wp.join.promptUntil':String(1000+WEEK)}}); h.boundary(90); h.engage(); h.advance(15000);
  assert.equal(h.prompt.hidden,true); assert.equal(h.panel.hidden,false);
  h=harness(); h.document.hidden=true; h.engage(); h.advance(15000); assert.equal(h.prompt.hidden,true);
  h.document.hidden=false; h.handlers.visibilitychange(); assert.equal(h.prompt.hidden,false);
  h=harness(); h.submitEmail(); await flush(); assert.equal(h.storage.get('wp.join.joined'),'1');
  assert.equal(h.events[0].params.method,'email'); assert.equal(h.requests[0].url,'https://api.test/subscribe');
  assert.deepEqual(JSON.parse(h.requests[0].body),{email:'email@example.com'});
  h.engage(); h.advance(15000); assert.equal(h.prompt.hidden,true);
  h=harness(); h.sub({status:503,body:{}}); h.submitEmail(); await flush(); assert.equal(h.storage.has('wp.join.joined'),false);
  h.engage(); h.advance(15000); assert.equal(h.prompt.hidden,false);
  h.handlers.storage({key:'wp.join.joined',newValue:'1'}); assert.equal(h.prompt.hidden,true);
  for (const [status, text] of [[400, 'Please enter a valid email address.'],
    [429, 'Too many tries. Please try again in an hour.'],
    [503, 'Sign-up is unavailable right now. Please try again soon.']]) {
    h=harness({pathname:'/join'}); h.click(h.choice); h.sub({status,body:{}}); h.submitEmail();
    await flush(); assert.equal(h.message.textContent,text); assert.equal(h.message.role,'alert');
    assert.equal(h.fields.hidden,false); assert.equal(h.success.hidden,true); assert.equal(h.submit.disabled,false);
    h.advance(5000); assert.equal(h.redirected(),undefined);
  }
  h=harness({pathname:'/join'}); h.click(h.choice); h.type('invalid'); h.submitEmail();
  assert.equal(h.requests.length,0); assert.equal(h.message.textContent,'Please enter a valid email address.');
  h.type('person@example.com'); h.sub(Error('network')); h.submitEmail(); await flush();
  assert.equal(h.message.textContent,"Couldn't reach the server. Please check your connection and try again.");
  assert.equal(h.success.hidden,true);
  // Google is absent until a configured SDK loads successfully; no automatic One Tap.
  h=harness({google:true}); assert.equal(h.scripts.length,0);
  h.engage(); h.advance(15000); assert.equal(h.provider.hidden,true); assert.equal(h.scripts.length,1);
  h.loadGoogle(); assert.equal(h.provider.hidden,false); assert.equal(h.googleOptions().theme,'filled_black');
  h.google(); await flush();
  assert.deepEqual(h.requests.map(r=>r.url),['https://api.test/api/google','https://api.test/subscribe']);
  assert.deepEqual(JSON.parse(h.requests[1].body),{email:'verified@example.com'});
  assert.equal(h.requests[1].headers.Authorization,undefined); assert.equal(h.storage.get('wp.join.joined'),'1');
  assert.equal(h.events.at(-1).params.method,'google'); assert.equal(h.storage.size,2); // no session token persisted
  for (const auth of [{status:401,body:{}},{status:200,body:{me:{email:'unverified@example.com',email_verified:false}}},Error('network')]) {
    h=harness({pathname:'/join',google:true}); h.loadGoogle(); h.auth(auth); h.google(); await flush();
    assert.equal(h.requests.length,1); assert.equal(h.storage.has('wp.join.joined'),false);
    assert.equal(h.providerMessage.role,'alert'); assert.equal(h.events.length,0);
  }
  h=harness({pathname:'/join',google:true}); h.loadGoogle(); h.sub({status:429,body:{}}); h.google(); await flush();
  assert.equal(h.storage.has('wp.join.joined'),false); assert.equal(h.providerMessage.role,'alert');
  h=harness({pathname:'/join',google:true}); h.loadGoogle(true); assert.equal(h.provider.hidden,true);
  h=harness({pathname:'/join',google:false}); assert.equal(h.scripts.length,0); assert.equal(h.provider.hidden,true);

  // Header clicks ignore frequency limits and storage; phone and modified clicks navigate.
  for (const options of [{enabled:false}, {blocked:'read'}, {blocked:'write'},
    {stored:{'wp.join.promptUntil':String(1000+WEEK)}}, {stored:{'wp.join.joined':'1'}}]) {
    h=harness(options); h.subscribe.focus(); assert.equal(h.click(h.subscribe),true);
    assert.equal(h.prompt.hidden,false); assert.equal(h.document.activeElement,h.choice);
    assert.equal(h.prompt.style['--join-top'],'58px'); assert.equal(h.prompt.style['--join-right'],'900px');
    assert.equal(h.dialog['aria-modal'],'false');
    h.key('Escape'); assert.equal(h.document.activeElement,h.subscribe);
    assert.equal(h.click(h.subscribe),true); assert.equal(h.prompt.hidden,false);
    h.click(h.opener); assert.equal(h.prompt.hidden,true); // ordinary page click closes
    assert.equal(h.document.activeElement,h.subscribe);
  }
  h=harness({width:640}); assert.equal(h.click(h.subscribe),false); assert.equal(h.prompt.hidden,true);
  h.engage(); h.advance(15000); assert.equal(h.dialog['aria-modal'],'true');
  for (const modifiers of [{ctrlKey:true}, {metaKey:true}, {shiftKey:true}, {altKey:true}, {button:1}]) {
    h=harness(); assert.equal(h.click(h.subscribe,modifiers),false); assert.equal(h.prompt.hidden,true);
  }
  // Both ways to join share success, with only /join redirecting (fake clock).
  for (const pathname of ['/join', '/join/', '/join.html', '/']) for (const method of ['email','google']) {
    h=harness({pathname, google:method==='google'});
    if (pathname === '/') h.click(h.subscribe);
    if (method === 'email') {h.click(h.choice); h.submitEmail();}
    else {h.loadGoogle(); h.google();}
    await flush();
    assert.equal(h.success.hidden,false); assert.equal(h.one.hidden,true); assert.equal(h.fields.hidden,true);
    assert.equal(h.successTitle.textContent,"You're on the list.");
    assert.equal(h.legal.hidden,true); assert.equal(h.document.activeElement,h.successTitle);
    if (pathname === '/') {h.handlers.focusin({target:h.opener}); assert.equal(h.document.activeElement,h.successTitle);}
    assert.equal(h.deals.hidden,pathname==='/'); assert.equal(h.storage.get('wp.join.joined'),'1');
    const delay = pathname==='/' ? 1800 : 1500;
    h.advance(delay-1); assert.equal(h.redirected(),undefined);
    if (pathname==='/') assert.equal(h.prompt.hidden,false);
    h.advance(1);
    if (pathname==='/') {assert.equal(h.prompt.hidden,true); assert.equal(h.redirected(),undefined);}
    else assert.equal(h.redirected(),'/');
  }
  // Timed weekly prompt closes too, and a closed success cannot close a new opening.
  h=harness(); h.engage(); h.advance(15000); h.click(h.choice); h.submitEmail(); await flush();
  h.advance(1799); assert.equal(h.prompt.hidden,false); h.advance(1); assert.equal(h.prompt.hidden,true);
  h=harness(); h.click(h.subscribe); h.click(h.choice); h.submitEmail(); await flush();
  h.key('Escape'); h.click(h.subscribe); h.advance(1800); assert.equal(h.prompt.hidden,false);
  console.log('join prompt logic passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
