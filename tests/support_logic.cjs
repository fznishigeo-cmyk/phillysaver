// Run the actual published support script with an offline DOM/API harness.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync(process.argv[2], 'utf8');
const home = fs.readFileSync(process.argv[3], 'utf8');
function harness({blocked = false, stored = '', now = 1000, panelPresent = true, contentPresent = true, hash = ''} = {}) {
  const handlers = {}, storage = new Map([['wp.support.dismissed', stored]]);
  let boundary = 200, count = 12, response = {status: 200, json: async () => ({ok:true})};
  let request, event, redirected;
  const input = {value: ' user@example.com '};
  const btn = {disabled: false};
  const msg = {hidden: true, setAttribute(k,v) {this[k] = v;}};
  const form = {matches: s => s === '.joinform', querySelector(s) {
    return s.includes('input') ? input : s.includes('button') ? btn : msg;
  }};
  const panel = {hidden: true}, bar = {}, support = {}, note = {};
  const document = {
    getElementById(id) { return {paywall: panelPresent ? panel : null, subbar:bar, content:contentPresent ? {} : null}[id]; },
    querySelector: () => ({getBoundingClientRect: () => ({bottom: 100})}),
    querySelectorAll: selector => selector === "[data-support]" ? [support] : selector === "[data-support-note]" ? [note] : Array.from({length: count}, () => ({getBoundingClientRect: () => ({bottom: boundary})})),
    addEventListener: (type, cb) => { handlers[type] = cb; }
  };
  const ctx = {
    document, location:{hash, replace(url) {redirected = url;}, pathname:'/', search:''},
    localStorage:{getItem(k) {if (blocked) throw Error('blocked'); return storage.get(k);},
      setItem(k,v) {if (blocked) throw Error('blocked'); storage.set(k,v);}},
    Date:{now: () => now}, requestAnimationFrame: f => f(),
    fetch: async (url, opts) => {request = {url, ...opts}; if (!opts || opts.method !== 'POST') return {json:async()=>({meta:{api_url:'https://api.test/'}})}; if (response instanceof Error) throw response; return response;},
    window:{wpTrack: (name, params) => {event = {name, params};}, addEventListener:(type, cb) => {handlers[type] = cb;}}
  };
  vm.runInNewContext(code, ctx);
  if (contentPresent) ctx.window.wpSupport.refresh({api_url:'https://api.test/', support:{link:'https://buy.stripe.com/test_support'}});
  const click = selector => handlers.click({target:{closest: s => s === selector}, preventDefault(){}});
  async function submit(res) {
    event = null; response = res;
    handlers.submit({target:form, preventDefault(){}});
    assert.equal(btn.disabled, true);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(btn.disabled, false);
  }
  return {ctx, panel, bar, support, note, input, btn, msg, storage, handlers, click, submit,
    boundary: n => boundary = n, count: n => count = n, request: () => request, event: () => event,
    redirected: () => redirected};
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
  assert.equal(h.handlers.keydown, undefined); // no dialog keyboard trap
  const joinClick = {target:{closest: s => s === '[data-join]'}, preventDefault(){throw Error('Join links must navigate normally');}};
  h.handlers.click(joinClick);
  assert.equal(h.redirected(), undefined);
  for (const hash of ['#join', '#signup', '#JOIN', '#login']) {
    const legacy = harness({hash});
    assert.equal(legacy.redirected(), '/join');
  }
  h.ctx.location.hash = '#signup'; h.handlers.hashchange(); assert.equal(h.redirected(), '/join');
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
    assert.equal(h.event(), null);
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
  // The dedicated Join page loads its endpoint from the public metadata.
  h = harness({panelPresent:false, contentPresent:false});
  assert.ok(h.request().url.startsWith('/deals.json?t='));
  await new Promise(resolve => setImmediate(resolve));
  await h.submit({status:200,json:async()=>({ok:true})});
  assert.equal(h.request().url, 'https://api.test/subscribe');
  assert.deepEqual(JSON.parse(h.request().body), {email:'user@example.com'});
  assert.equal(h.event().name, 'sign_up');
  const bandStart = home.indexOf('  function openBand()'), bandEnd = home.indexOf('  function trialBand()', bandStart);
  const band = {};
  vm.runInNewContext(home.slice(bandStart, bandEnd), band);
  assert.ok(band.openBand().includes('<a class="pwbtn alt" href="/join">Join free</a>'));
  assert.equal(band.openBand().includes('<form'), false);
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
