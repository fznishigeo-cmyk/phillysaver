// Execute the published component with a fake clock, storage, DOM and Google SDK.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync(process.argv[2], 'utf8');
const WEEK = 7 * 24 * 60 * 60 * 1000;
const flush = () => new Promise(resolve => setImmediate(resolve));
function harness({pathname = '/', now = 1000, stored = {}, blocked = '', enabled = true,
                  delay = 15, google = false, api = 'https://api.test', width = 1000, card = true,
                  support = {}, footerOnJoin = false} = {}) {
  const handlers = {}, events = [], requests = [], scripts = [], timers = [];
  let nextTimer = 0;
  const storage = new Map(Object.entries(stored));
  let boundary = 200, googleCallback, googleOptions, redirected;
  let authResult = {status:200, body:{me:{email:'verified@example.com', email_verified:true}, token:'unused'}};
  let subResult = {status:200, body:{ok:true}};
  const document = {hidden:false, addEventListener(type, cb) {handlers[type] = cb;}};
  const rect = () => ({top:10, bottom:50, width:100, height:40, left:0, right:100});
  function node(id, hidden = false) {
    return {id, hidden, isConnected:true, disabled:false, parentNode:{clientWidth:400}, children:[],
      focus() {document.activeElement = this;}, setAttribute(k,v) {this[k] = v;},
      removeAttribute(k) {delete this[k];}, hasAttribute(k) {return Object.hasOwn(this,k);},
      appendChild(el) {el.parentNode=this; this.children.push(el);},
      remove() {this.isConnected=false; this.parentNode.children=this.parentNode.children.filter(el=>el!==this);},
      contains(el) {return el===this || this.children.some(child=>child.contains(el));},
      style:{setProperty(k,v) {this[k] = v;}},
      getClientRects() {
        for(let el=this;el;el=el.parentNode) if(el.hidden) return [];
        return (['join-email','submit','join-back'].includes(id) && fields.hidden) ||
        (id === 'join-email-choice' && one.hidden) || (id === 'legal' && legal.hidden) ? [] : [rect()];}, getBoundingClientRect:rect,
      closest(s) {return s.split(',').some(part=>part.trim() === '#' + id ||
        (id === 'close' && part.trim() === '[data-join-close]') ||
        (part.trim().startsWith('[data-') && this.hasAttribute(part.trim().slice(1,-1)))) ? this : null;}};
  }
  const prompt = node('join-prompt', true), choice = node('join-email-choice'), fields = node('join-email-fields', true);
  const input = node('join-email'), submit = node('submit'), message = node('message', true);
  input.value = ' email@example.com ';
  const close = node('close'), legal = node('legal'), opener = node('opener');
  const one = node('join-step-one'), back = node('join-back'), success = node('join-success', true);
  const successTitle = node('join-title'), deals = node('join-deals', true), subscribe = node('subscribe');
  const provider = node('join-google', true), providerMessage = node('join-google-message', true);
  const googleOr = node('join-google-or', true), description = node('join-description'), label = node('label');
  const footer = node('footer-signup', true), footerSuccess = node('footer-signup-success', true);
  const footerTitle = node('footer-signup-title');
  const footerInput = node('footer-email'), footerSubmit = node('footer-submit'), footerMessage = node('footer-message', true);
  footerInput.value = ' footer@example.com ';
  const dialog = node('dialog'), panel = node('paywall', true), bar = node('subbar', true);
  const nodes = Object.fromEntries([prompt, choice, fields, input, provider, providerMessage, panel, bar, one, back, success, successTitle, deals, subscribe, googleOr, description, footer, footerSuccess, footerTitle].map(n => [n.id,n]));
  if (!card) for (const n of [prompt, choice, fields, input, provider, providerMessage, one, back, success, successTitle, deals, googleOr, description]) delete nodes[n.id];
  if (pathname.startsWith('/join') && !footerOnJoin) {delete nodes[footer.id]; delete nodes[footerSuccess.id];}
  nodes['join-legal'] = legal;
  const form = {matches:s=>s === '.joinform', hasAttribute:()=>false, querySelector:s=>s.includes('input') ? input : s.includes('button') ? submit : message};
  const footerForm = {hidden:false, matches:s=>s === '.joinform', hasAttribute:s=>s === 'data-join-footer', querySelector:s=>s.includes('input') ? footerInput : s.includes('button') ? footerSubmit : footerMessage};
  fields.querySelector = s => s === 'label' ? label : s.includes('button') ? submit : message;
  const focusable = [close, choice, input, submit, back, legal, deals];
  const askControls = () => success.children.flatMap(ask=>ask.children);
  prompt.querySelectorAll = () => [...focusable, ...askControls()];
  prompt.querySelector = () => dialog;
  prompt.contains = dialog.contains = el => [...focusable, ...askControls(), dialog, successTitle].includes(el);
  document.activeElement = opener;
  document.getElementById = id => id === 'content' ? {} : nodes[id];
  document.querySelector = () => ({getBoundingClientRect:()=>({bottom:100})});
  document.querySelectorAll = s => s.startsWith('#content .card')
    ? Array.from({length:12},()=>({getBoundingClientRect:()=>({bottom:boundary})})) : [];
  document.createElement = tag => {
    const el=node(tag);
    Object.defineProperty(el,'innerHTML',{get(){return this.html;}, set(html){
      this.html=html;
      for (const match of html.matchAll(/<(a|button)\b([^>]*)>(.*?)<\/(?:a|button)>/g)) {
        const child=node(match[1]); child.textContent=match[3];
        for (const attr of match[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) child.setAttribute(attr[1],attr[2]||'');
        this.appendChild(child);
      }
    }});
    return el;
  };
  document.head = {appendChild:s=>scripts.push(s)};
  const meta = {api_url:api, google_client_id:google ? 'configured-client' : '', join:{prompt:enabled,prompt_delay_s:delay}, support:{panel_delay_s:0, ...support}};
  const ctx = {document, location:{pathname,hash:'',replace(url){redirected=url;}}, Date:{now:()=>now},
    localStorage:{getItem(k) {if (blocked === 'read') throw Error('blocked'); return storage.get(k) || null;},
      setItem(k,v) {if (blocked === 'write') throw Error('quota'); storage.set(k,v);}},
    setTimeout(cb,ms) {const id=++nextTimer; timers.push({id,cb,at:now+ms}); return id;}, clearTimeout(id) {const t=timers.find(t=>t.id===id); if(t) t.cancelled=true;}, requestAnimationFrame:cb=>cb(),
    fetch:async(url,options)=>{requests.push({url,...options}); const r = await (url.endsWith('/api/google') ? authResult : subResult);
      if (r instanceof Error) throw r; return {status:r.status, json:async()=>r.body};},
    window:{innerHeight:800,innerWidth:width,scrollY:0,wpTrack:(name,params)=>events.push({name,params}),
      addEventListener(type,cb) {handlers[type] = cb;}}};
  vm.runInNewContext(code,ctx);
  ctx.window.wpSupport.refresh(meta);
  function click(target, modifiers={}) {let prevented=false; handlers.click({target, ...modifiers, preventDefault(){prevented=true;}}); return prevented;}
  function advance(ms) {now += ms; for (const t of timers.splice(0)) {if (t.cancelled) continue; if (t.at <= now) t.cb(); else timers.push(t);}}
  function engage() {click({closest:s=>s === '#cattabs a, #controls select, #controls input'});}
  function key(key,shiftKey=false) {let prevented=false; if(handlers.keydown) handlers.keydown({key,shiftKey,preventDefault(){prevented=true;}}); return prevented;}
  return {ctx, document, prompt, choice, fields, input, submit, message, close, legal, opener, panel, bar, storage, events, requests, scripts,
    provider, providerMessage, meta, handlers, advance, engage, click, key, one, back, success, successTitle, deals, subscribe, dialog,
    googleOr, description, label, footer, footerSuccess, footerTitle, footerForm, footerInput, footerSubmit, footerMessage,
    redirected:()=>redirected, type(value) {input.value=value; handlers.input({target:input});},
    refresh:()=>ctx.window.wpSupport.refresh(meta), boundary:n=>boundary=n,
    scroll(n) {ctx.window.scrollY=n; handlers.scroll();},
    submitEmail() {handlers.submit({target:form,preventDefault(){}});},
    submitFooter() {handlers.submit({target:footerForm,preventDefault(){}});},
    auth:r=>authResult=r, sub:r=>subResult=r,
    loadGoogle(fail=false) {
      ctx.window.google = {accounts:{id:{initialize(o) {googleCallback=o.callback;}, renderButton(el,o) {if(fail) throw Error('SDK'); googleOptions=o;}}}};
      scripts[0].onload();
    }, googleOptions:()=>googleOptions, google:()=>googleCallback({credential:'google-id-token'})};
}
(async()=>{
  if (code.includes('panelWaiting')) {
    const h = harness({delay:35, support:{panel_delay_s:120}});
    h.boundary(90); h.refresh(); h.engage();
    h.advance(34999); assert.equal(h.prompt.hidden,true); assert.equal(h.panel.hidden,true);
    h.advance(1); assert.equal(h.prompt.hidden,false); assert.equal(h.panel.hidden,true);
    assert.equal(h.storage.get('wp.join.promptUntil'),String(36000+WEEK));
    h.key('Escape'); h.advance(85000); h.refresh();
    assert.equal(h.prompt.hidden,true); assert.equal(h.panel.hidden,true); // one ask per view
    for (const options of [{enabled:false}, {stored:{'wp.join.promptUntil':String(1000+WEEK)}}]) {
      const waiting = harness({...options, delay:35, support:{panel_delay_s:120}});
      waiting.boundary(90); waiting.engage(); waiting.advance(120000);
      assert.equal(waiting.prompt.hidden,true); assert.equal(waiting.panel.hidden,false);
    }
  }
  let h = harness();
  h.engage(); h.advance(14999); assert.equal(h.prompt.hidden,true);
  h.advance(1); assert.equal(h.prompt.hidden,false); assert.equal(h.document.activeElement,h.input);
  assert.equal(h.prompt.className,'joinoverlay joinpopup'); assert.equal(h.dialog['aria-modal'],'true');
  assert.equal(h.successTitle.innerHTML,'Save up to <span>90% OFF</span>');
  assert.equal(h.description.textContent,"The day's best verified deals in one free email. Unsubscribe anytime.");
  assert.equal(h.label.textContent,'Email address'); assert.equal(h.input.placeholder,'Email address');
  assert.equal(h.submit.textContent,'Get the deals'); assert.equal(h.back.hidden,true); assert.equal(h.choice.hidden,true);
  assert.equal(h.fields.hidden,false); assert.equal(h.googleOr.hidden,true);
  assert.equal(h.legal.innerHTML,'We use your email only to send deals. Unsubscribe anytime. See our <a href="/privacy.html">privacy policy</a>.');
  assert.equal(h.storage.get('wp.join.promptUntil'),String(16000+WEEK));
  assert.equal(h.events[0].name,'signup_prompt_shown'); assert.equal(h.scripts.length,0);
  h.legal.focus(); assert.equal(h.key('Tab'),true); assert.equal(h.document.activeElement,h.close);
  assert.equal(h.key('Tab',true),true); assert.equal(h.document.activeElement,h.legal);
  h.handlers.focusin({target:h.opener}); assert.equal(h.document.activeElement,h.input);
  h.key('Escape'); h.click(h.subscribe); // Header retains the two-step card.
  assert.equal(h.prompt.className,'joinoverlay'); assert.equal(h.choice.hidden,false); assert.equal(h.back.hidden,false);
  assert.equal(h.submit.textContent,'Continue'); assert.equal(h.input.placeholder,'Email');
  assert.equal(h.description.textContent,"The day's best deals in one email. Free.");
  assert.equal(h.googleOr.hidden,true);
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
    [503, 'Sign-up is temporarily unavailable. Please try again.']]) {
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
  assert.equal(h.googleOr.hidden,false);
  h.google(); await flush();
  assert.deepEqual(h.requests.map(r=>r.url),['https://api.test/api/google','https://api.test/subscribe']);
  assert.deepEqual(JSON.parse(h.requests[1].body),{email:'verified@example.com'});
  assert.equal(h.requests[1].headers.Authorization,undefined); assert.equal(h.storage.get('wp.join.joined'),'1');
  assert.equal(h.events.at(-1).params.method,'google'); assert.equal(h.storage.size,2); // no session token persisted
  assert.equal(h.footer.hidden,true);
  for (const auth of [{status:401,body:{}},{status:200,body:{me:{email:'unverified@example.com',email_verified:false}}},Error('network')]) {
    h=harness({pathname:'/join',google:true}); h.loadGoogle(); h.auth(auth); h.google(); await flush();
    assert.equal(h.requests.length,1); assert.equal(h.storage.has('wp.join.joined'),false);
    assert.equal(h.providerMessage.role,'alert'); assert.equal(h.events.length,0);
  }
  h=harness({pathname:'/join',google:true}); h.loadGoogle(); h.sub({status:429,body:{}}); h.google(); await flush();
  assert.equal(h.storage.has('wp.join.joined'),false); assert.equal(h.providerMessage.role,'alert');
  h=harness({pathname:'/join',google:true}); h.loadGoogle(true); assert.equal(h.provider.hidden,true);
  assert.equal(h.googleOr.hidden,true);
  h=harness({pathname:'/join',google:false}); assert.equal(h.scripts.length,0); assert.equal(h.provider.hidden,true);
  assert.equal(h.googleOr.hidden,true);

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
  h=harness(); h.engage(); h.advance(15000); h.submitEmail(); await flush();
  assert.equal(h.success.hidden,false); assert.equal(h.successTitle.textContent,"You're on the list.");
  assert.equal(h.description.hidden,true); assert.equal(h.footer.hidden,true);
  h.advance(1799); assert.equal(h.prompt.hidden,false); h.advance(1); assert.equal(h.prompt.hidden,true);
  h=harness(); h.click(h.subscribe); h.click(h.choice); h.submitEmail(); await flush();
  h.key('Escape'); h.click(h.subscribe); h.advance(1800); assert.equal(h.prompt.hidden,false);

  // Footer submits its own field, even while the card's email is invalid.
  for (const card of [true, false]) {
    h=harness({card}); assert.equal(h.footer.hidden,false);
    h.type('invalid'); h.submitFooter(); h.submitFooter();
    assert.equal(h.footerSubmit.disabled,true); assert.equal(h.requests.length,1);
    await flush();
    assert.equal(h.requests[0].url,'https://api.test/subscribe');
    assert.deepEqual(JSON.parse(h.requests[0].body),{email:'footer@example.com'});
    assert.equal(h.requests[0].headers.Authorization,undefined);
    assert.equal(h.footerForm.hidden,true); assert.equal(h.footerSuccess.hidden,false);
    assert.equal(h.footer.hidden,false); assert.equal(h.footerSubmit.disabled,false);
    assert.equal(h.storage.get('wp.join.joined'),'1');
    assert.equal(h.events.at(-1).name,'sign_up'); assert.equal(h.events.at(-1).params.method,'footer');
    h.engage(); h.advance(15000); assert.equal(h.prompt.hidden,true);
    h.refresh(); assert.equal(h.footer.hidden,false); // Thank-you remains for this visit.
  }
  for (const card of [true, false]) {
    h=harness({card,stored:{'wp.join.joined':'1'}}); assert.equal(h.footer.hidden,true);
    h=harness({card}); h.handlers.storage({key:'wp.join.joined',newValue:'1'});
    assert.equal(h.footer.hidden,true);
  }
  for (const [response,text] of [
    [{status:400,body:{}},'Please enter a valid email address.'],
    [{status:429,body:{}},'Too many tries. Please try again in an hour.'],
    [{status:503,body:{}},'Sign-up is temporarily unavailable. Please try again.'],
    [{status:200,body:{}},'Sign-up is temporarily unavailable. Please try again.'],
    [Error('network'),"Couldn't reach the server. Please check your connection and try again."]
  ]) {
    h=harness({card:false}); h.sub(response); h.submitFooter(); await flush();
    assert.equal(h.footerMessage.textContent,text); assert.equal(h.footerMessage.role,'alert');
    assert.equal(h.footerForm.hidden,false); assert.equal(h.footerSuccess.hidden,true);
    assert.equal(h.footerSubmit.disabled,false); assert.equal(h.storage.has('wp.join.joined'),false);
    assert.equal(h.events.length,0);
    h.sub({status:200,body:{ok:true}}); h.submitFooter(); await flush();
    assert.equal(h.footerSuccess.hidden,false); assert.equal(h.events.at(-1).params.method,'footer');
  }
  h=harness(); h.footerInput.value='invalid'; h.submitFooter();
  assert.equal(h.requests.length,0); assert.equal(h.footerMessage.role,'alert');
  h=harness({api:''}); h.submitFooter(); assert.equal(h.requests.length,0);
  assert.equal(h.footerMessage.textContent,'Sign-up is temporarily unavailable. Please try again.');
  h=harness({blocked:'write'}); h.submitFooter(); await flush();
  assert.equal(h.footerSuccess.hidden,false); assert.equal(h.events.at(-1).params.method,'footer');
  h.engage(); h.advance(15000); assert.equal(h.prompt.hidden,true);
  h=harness(); h.engage(); h.advance(15000); h.submitFooter(); await flush();
  assert.equal(h.prompt.hidden,true); assert.equal(h.footerSuccess.hidden,false);
  if (code.includes('function showSupportAsk')) {
    const link='https://buy.stripe.com/test_support', monthly='https://buy.stripe.com/test_monthly';
    const support={enabled:true,link,monthly_link:code.includes('Support monthly</a>') ? monthly : '',
      panel_delay_s:code.includes('panelWaiting') ? 120 : 0};
    const ask=h=>[...h.success.children,...h.footerSuccess.children].find(n=>n.className==='supportask'&&!n.hidden);
    const actions=h=>h.events.filter(e=>e.name.startsWith('support_ask_'));
    async function join(h,path,method='email') {
      if (path==='prompt') {h.engage();h.advance(15000);}
      else if(path==='header') h.click(h.subscribe);
      if(path.startsWith('footer')) h.submitFooter();
      else if(method==='google') {h.loadGoogle();h.google();}
      else {if(path!=='prompt') h.click(h.choice);h.submitEmail();}
      await flush();
    }
    // The only entry is a successful join, for both identities and every surface.
    for(const path of ['prompt','header','/join','/join/','/join.html','footer','footer-page','footer-join']) {
      for(const method of path.startsWith('footer') ? ['footer'] : ['email','google']) {
        h=harness({support,google:method==='google',width:path==='prompt' ? 360 : 1000,
          pathname:path.startsWith('/join') || path==='footer-join' ? '/join' : path==='footer-page' ? '/about.html' : '/',
          card:path!=='footer-page',footerOnJoin:path==='footer-join'});
        h.advance(5000); assert.equal(ask(h),undefined); assert.equal(actions(h).length,0);
        await join(h,path,method);
        const a=ask(h); assert.ok(a,path+' '+method);
        assert.equal(h.storage.get('wp.support.askShown'),'1');
        assert.equal(actions(h).length,1); assert.equal(actions(h)[0].name,'support_ask_shown');
        assert.equal(actions(h)[0].params,undefined);
        assert.ok(a.innerHTML.includes('wowpriced is free for everyone.'));
        assert.equal(a.innerHTML.includes('from $5 a month'),Boolean(support.monthly_link));
        assert.ok(a.innerHTML.includes('not tax-deductible') && a.innerHTML.includes('buys nothing extra'));
        assert.equal(a.parentNode.role,'status');
        const links=a.children.filter(n=>n.id==='a');
        assert.deepEqual(links.map(n=>n.href),support.monthly_link ? [link,monthly] : [link]);
        for(const el of links) {assert.equal(el.target,'_blank');assert.equal(el.rel,'noopener');}
        h.advance(60000); h.refresh();
        assert.equal(ask(h),a); assert.equal(h.redirected(),undefined);
        if(path==='prompt'||path==='header') {
          assert.equal(h.prompt.hidden,false); assert.equal(h.dialog['aria-describedby'],'join-success');
          a.children.at(-1).focus(); h.key('Tab'); assert.equal(h.document.activeElement,h.close);
          h.key('Tab',true); assert.equal(h.document.activeElement,a.children.at(-1));
        }
        if(path.startsWith('/join')) assert.equal(h.deals.textContent,"See today's deals");
        h.click(a.children.at(-1)); // Not now closes once and restores a visible focus target.
        assert.equal(ask(h),undefined); assert.equal(actions(h).at(-1).name,'support_ask_dismiss');
        assert.equal(actions(h).at(-1).params,undefined);
        assert.equal(h.document.activeElement,path.startsWith('footer') ? h.footerTitle : path.startsWith('/join') ? h.deals : h.opener);
        if(path.startsWith('/join')) assert.equal(h.deals.tabindex,undefined); // Keep the deals link in the tab order.
        h.key('Escape'); h.click(a.children.at(-1)); assert.equal(actions(h).length,2);
        h.advance(60000); assert.equal(h.redirected(),undefined);
      }
    }
    // Every closing route counts one dismissal; /join's navigation remains a normal link.
    for(const closeBy of ['escape','close','outside','header']) {
      h=harness({support}); await join(h,'header');
      if(closeBy==='escape') h.key('Escape');
      else h.click(closeBy==='close' ? h.close : closeBy==='outside' ? h.opener : h.subscribe);
      assert.equal(h.prompt.hidden,true); assert.equal(ask(h),undefined);
      assert.deepEqual(actions(h).map(e=>e.name),['support_ask_shown','support_ask_dismiss']);
      assert.equal(h.document.activeElement,h.opener);
    }
    for(const path of ['/join','footer','footer-page']) {
      h=harness({support,pathname:path==='/join' ? '/join' : '/',card:path!=='footer-page'});
      await join(h,path); assert.equal(h.key('Escape'),true); assert.equal(ask(h),undefined);
      assert.equal(actions(h).at(-1).name,'support_ask_dismiss');
    }
    h=harness({support,pathname:'/join'}); await join(h,'/join');
    assert.equal(h.click(h.deals),false); assert.equal(actions(h).at(-1).name,'support_ask_dismiss');
    // Choosing support emits only the new GA4 action, never the ad-pixel support_click.
    for(const method of support.monthly_link ? ['once','monthly'] : ['once']) {
      for(const path of ['header','/join','footer-page']) {
        h=harness({support,pathname:path==='/join' ? '/join' : '/',card:path!=='footer-page'});
        await join(h,path);
        const button=ask(h).children.find(n=>n.hasAttribute(method==='monthly' ? 'data-support-monthly' : 'data-support'));
        h.click(button);
        assert.equal(button.isConnected,true); assert.equal(button.parentNode.hidden,true);
        assert.equal(h.storage.get('wp.support.clicked'),'1'); assert.equal(ask(h),undefined);
        assert.deepEqual(actions(h).map(e=>e.name),['support_ask_shown','support_ask_click']);
        assert.deepEqual(JSON.parse(JSON.stringify(actions(h)[1].params)),{method});
        assert.equal(h.events.some(e=>e.name==='support_click'),false);
        h.key('Escape'); assert.equal(actions(h).length,2);
      }
    }
    // Read errors and quota errors use a page-local limit; repeated successes cannot arm a timer.
    for(const blocked of ['', 'read','write']) {
      h=harness({support,blocked}); await join(h,'header');
      h.submitEmail(); await flush(); h.advance(60000);
      assert.equal(h.prompt.hidden,false); assert.ok(ask(h)); assert.equal(actions(h).length,1);
      h.key('Escape'); await join(h,'header');
      assert.equal(ask(h),undefined); assert.equal(actions(h).length,2);
    }
    // A slow footer response cannot close a card ask; a slow Google response cannot hide a footer ask.
    let release;
    h=harness({support,google:true}); h.click(h.subscribe); h.loadGoogle();
    h.sub(new Promise(resolve=>release=resolve)); h.submitFooter();
    h.sub({status:200,body:{ok:true}}); h.google(); await flush();
    assert.ok(ask(h)); release({status:200,body:{ok:true}}); await flush(); h.advance(60000);
    assert.equal(h.prompt.hidden,false); assert.ok(ask(h)); assert.equal(actions(h).length,1);
    h=harness({support,google:true}); h.click(h.subscribe); h.loadGoogle();
    h.auth(new Promise(resolve=>release=resolve)); h.google(); h.submitFooter(); await flush();
    assert.equal(h.footer.hidden,false); assert.ok(ask(h));
    release({status:200,body:{me:{email:'verified@example.com',email_verified:true}}}); await flush(); h.advance(60000);
    assert.equal(h.footer.hidden,false); assert.ok(ask(h)); assert.equal(actions(h).length,1);
    for(const path of ['header','/join','footer-page']) for(const field of ['wp.support.askShown','wp.support.clicked','wp.join.joined']) {
      h=harness({support,stored:{[field]:'1'},pathname:path==='/join' ? '/join' : '/',card:path!=='footer-page'});
      await join(h,path); assert.equal(ask(h),undefined); assert.equal(actions(h).length,0);
    }
    for(const blocked of ['', 'read','write']) {
      h=harness({support,blocked});
      const button=h.ctx.document.createElement('a');button.setAttribute('data-support','');
      h.click(button); await join(h,'header'); assert.equal(ask(h),undefined);
    }
    for(const field of ['wp.support.askShown','wp.support.clicked']) {
      h=harness({support}); h.handlers.storage({key:field,newValue:'1'});
      await join(h,'header'); assert.equal(ask(h),undefined);
    }
    h=harness({support}); h.footer.appendChild(h.footerInput); h.footerInput.focus();
    await join(h,'prompt'); h.key('Escape'); assert.equal(h.document.activeElement,h.subscribe);
    h=harness({support}); await join(h,'header'); h.opener.isConnected=false;
    h.key('Escape'); assert.equal(h.document.activeElement,h.subscribe);
    for(const s of [{}, {...support,enabled:false}, {...support,link:''}, {...support,link:'https://evil.test/'}]) {
      for(const path of ['header','/join','footer-page']) {
        h=harness({support:s,pathname:path==='/join' ? '/join' : '/',card:path!=='footer-page'});
        await join(h,path); assert.equal(ask(h),undefined); assert.equal(actions(h).length,0);
        if(path==='/join') {h.advance(1500);assert.equal(h.redirected(),'/');}
        if(path==='header') {h.advance(1800);assert.equal(h.prompt.hidden,true);}
      }
    }
    h=harness({support});h.sub({status:429,body:{}});await join(h,'header');
    assert.equal(ask(h),undefined);assert.equal(h.storage.has('wp.support.askShown'),false);
    h=harness({support});h.click(h.subscribe);h.click(h.choice);h.submitEmail();h.key('Escape');await flush();
    assert.equal(ask(h),undefined);assert.equal(h.storage.has('wp.support.askShown'),false);
  }
  console.log('join prompt logic passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
