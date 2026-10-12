// Run the actual published support script with an offline DOM/API harness.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync(process.argv[2], 'utf8');
const home = fs.readFileSync(process.argv[3], 'utf8');
function harness({blocked = false, stored = '', now = 1000, panelPresent = true} = {}) {
  const handlers = {}, storage = new Map([['wp.support.dismissed', stored]]);
  let boundary = 200, count = 12, response = {status: 200, json: async () => ({ok:true})};
  let request, event, openFocus = false;
  const input = {value: ' user@example.com ', focus() { document.activeElement = input; }};
  const btn = {disabled: false, focus() { document.activeElement = btn; }};
  const closer = {disabled: false, offsetParent: {}, focus() { document.activeElement = closer; }};
  input.offsetParent = btn.offsetParent = {};
  const msg = {hidden: true, setAttribute(k,v) {this[k] = v;}};
  const form = {matches: s => s === '.joinform', querySelector(s) {
    return s.includes('input') ? input : s.includes('button') ? btn : msg;
  }};
  const opener = {offsetParent: {}, focus() { openFocus = true; document.activeElement = opener; }};
  const modal = {hidden: true, querySelector: () => input, querySelectorAll: () => [closer, input, btn]};
  const panel = {hidden: true}, bar = {}, support = {}, note = {};
  const document = {
    activeElement: opener, body: {style: {}, contains: () => true},
    getElementById(id) { return {authmodal: modal, paywall: panelPresent ? panel : null, subscribe: opener, subbar:bar, content:{}}[id]; },
    querySelector: () => ({getBoundingClientRect: () => ({bottom: 100})}),
    querySelectorAll: selector => selector === "[data-support]" ? [support] : selector === "[data-support-note]" ? [note] : Array.from({length: count}, () => ({getBoundingClientRect: () => ({bottom: boundary})})),
    addEventListener: (type, cb) => { handlers[type] = cb; }
  };
  const ctx = {
    document, location:{hash:'', pathname:'/', search:''}, history:{replaceState(){}},
    localStorage:{getItem(k) {if (blocked) throw Error('blocked'); return storage.get(k);},
      setItem(k,v) {if (blocked) throw Error('blocked'); storage.set(k,v);}},
    Date:{now: () => now}, requestAnimationFrame: f => f(),
    fetch: async (url, opts) => {request = {url, ...opts}; if (response instanceof Error) throw response; return response;},
    window:{wpTrack: (name, params) => {event = {name, params};}, addEventListener:(type, cb) => {handlers[type] = cb;}}
  };
  vm.runInNewContext(code, ctx);
  ctx.window.wpSupport.refresh({api_url:'https://api.test/', support:{link:'https://buy.stripe.com/test_support'}});
  const click = selector => handlers.click({target:{closest: s => s === selector}, preventDefault(){}});
  const key = (key, shiftKey = false) => {
    let prevented = false; handlers.keydown({key, shiftKey, preventDefault(){prevented = true;}}); return prevented;
  };
  async function submit(res) {
    event = null; response = res;
    handlers.submit({target:form, preventDefault(){}});
    assert.equal(btn.disabled, true);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(btn.disabled, false);
  }
  return {ctx, modal, panel, bar, support, note, input, btn, closer, msg, opener, storage, handlers, click, key, submit,
    boundary: n => boundary = n, count: n => count = n, request: () => request, event: () => event,
    openerFocused: () => openFocus};
}
(async () => {
  let h = harness();
  assert.equal(h.panel.hidden, true); // before the eighth card
  assert.equal(h.support.href, "https://buy.stripe.com/test_support");
  assert.equal(h.support.hidden, false);
  h.ctx.window.wpSupport.refresh({api_url:"https://api.test", support:{link:"https://evil.test"}});
  assert.equal(h.support.hidden, true); assert.equal(h.note.hidden, true);
  h.ctx.window.wpSupport.refresh({api_url:"https://api.test/", support:{link:"https://buy.stripe.com/test_support"}});
  h.boundary(90); h.ctx.window.wpSupport.refresh();
  assert.equal(h.panel.hidden, false);
  assert.equal(h.bar.hidden, true);
  h.count(8); h.ctx.window.wpSupport.refresh();
  assert.equal(h.panel.hidden, true); // a short wall has no obstructing banner
  h.count(12); h.ctx.window.wpSupport.refresh();
  h.click('[data-join]');
  assert.equal(h.modal.hidden, false);
  assert.equal(h.ctx.document.activeElement, h.input);
  h.ctx.document.activeElement = h.btn; h.key('Tab');
  assert.equal(h.ctx.document.activeElement, h.closer);
  h.key('Tab', true); assert.equal(h.ctx.document.activeElement, h.btn);
  h.key('Escape'); assert.equal(h.modal.hidden, true); assert.equal(h.openerFocused(), true);
  h.click('[data-join]'); h.click('[data-join-close]'); assert.equal(h.modal.hidden, true);
  h.ctx.location.hash = '#join'; h.handlers.hashchange(); assert.equal(h.modal.hidden, false);
  await h.submit({status:200,json:async()=>({ok:true})});
  assert.equal(h.request().url, 'https://api.test/subscribe');
  assert.equal(h.request().method, 'POST');
  assert.deepEqual(JSON.parse(h.request().body), {email:'user@example.com'});
  assert.equal(h.request().headers['Content-Type'], 'application/json');
  assert.equal(h.request().headers.Authorization, undefined);
  assert.equal(h.msg.textContent, "You're on the list. When our deals email starts, we'll send you one email to confirm first.");
  assert.equal(h.msg.role, 'status');
  assert.equal(h.event().name, 'sign_up'); assert.equal(h.event().params.method, 'email');
  for (const [status, message] of [[400,'valid email'], [429,'Too many tries'], [503,'unavailable']]) {
    await h.submit({status,json:async()=>({error:'failed'})});
    assert.ok(h.msg.textContent.includes(message)); assert.equal(h.msg.role,'alert');
    assert.equal(h.event(), null); assert.equal(h.modal.hidden, false);
  }
  await h.submit(Error('network')); assert.ok(h.msg.textContent.includes("Couldn't reach"));
  await h.submit({status:200,json:async()=>({})}); assert.ok(h.msg.textContent.includes('unavailable'));
  h.click('[data-support]'); assert.equal(h.event().name, 'support_click');
  h.click('[data-support-close]'); assert.equal(h.panel.hidden, true); assert.equal(h.bar.hidden, false);
  const expiry = h.storage.get('wp.support.dismissed');
  assert.equal(Number(expiry), 1000 + 7*24*60*60*1000);
  h.boundary(90); h.ctx.window.wpSupport.refresh(); assert.equal(h.panel.hidden, true);
  h = harness({stored:expiry, now:1001}); h.boundary(90); h.ctx.window.wpSupport.refresh(); assert.equal(h.panel.hidden,true);
  h = harness({stored:expiry, now:Number(expiry)+1}); h.boundary(90); h.ctx.window.wpSupport.refresh(); assert.equal(h.panel.hidden,false);
  h = harness({blocked:true}); h.boundary(90); h.ctx.window.wpSupport.refresh(); assert.equal(h.panel.hidden,false);
  h.click('[data-support-close]'); assert.equal(h.panel.hidden,true);
  await h.submit({status:200,json:async()=>({ok:true})}); assert.equal(h.event().name,'sign_up');
  // Exercise the actual grid function: the inline offer follows eight
  // cards even when the alert-floor split puts fewer than eight on top.
  const start = home.indexOf('    var beforeBand = 8;'), end = home.indexOf('    if (headliners.length)', start);
  const renderer = {remaining:60, banded:false, gridHtml:rows=>rows.join(','), openBand:()=> '[JOIN]'};
  vm.runInNewContext(home.slice(start,end), renderer);
  assert.equal(renderer.grid([1,2,3,4,5]), '1,2,3,4,5');
  assert.equal(renderer.grid([6,7,8,9,10]), '6,7,8[JOIN]9,10');
  assert.equal(renderer.grid([11,12]), '11,12');
  console.log('support logic passed');
})().catch(e => { console.error(e); process.exitCode = 1; });
