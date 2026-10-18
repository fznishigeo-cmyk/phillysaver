// The same emitted signup controller and fake-clock harness used by the homepage.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {harness, flush} = require('./join_prompt_logic.cjs');
const source = fs.readFileSync(process.argv[2], 'utf8');
const DAY = 24 * 60 * 60 * 1000;
const open = options => harness({brand:true, pathname:'/about.html', ...options});

(async () => {
  for (const width of [390, 1440]) {
    let h = open({width});
    assert.equal(h.requests.length, 0); // Static metadata, no deals.json download.
    h.advance(24999); assert.equal(h.prompt.hidden, true);
    h.advance(1); assert.equal(h.prompt.hidden, false);
    assert.equal(h.storage.get('wp.join.promptUntil'), String(26000 + DAY));
    assert.equal(h.successTitle.innerHTML, 'Save up to <span>90% OFF</span>');
    assert.equal(h.description.textContent, "The day's best verified deals in one free email. Unsubscribe anytime.");
    assert.equal(h.dialog['aria-modal'], 'true');
    assert.equal(h.document.activeElement, h.input);
    assert.equal(h.scripts.length, 0); // No new third-party provider request.
    h.legal.focus(); h.key('Tab'); assert.equal(h.document.activeElement, h.close);
    h.key('Tab', true); assert.equal(h.document.activeElement, h.legal);
    h.key('Escape'); assert.equal(h.prompt.hidden, true);
    assert.equal(h.document.activeElement, h.opener);
    const stored = Object.fromEntries(h.storage);
    for (const pathname of ['/contact', '/careers.html', '/hacks', '/how-it-works.html']) {
      const next = open({width, now:26000 + DAY - 1, stored, pathname});
      next.scroll(800); assert.equal(next.prompt.hidden, true);
    }
    const tomorrow = open({now:26000 + DAY, stored});
    tomorrow.scroll(800); assert.equal(tomorrow.prompt.hidden, false);
    h.advance(2 * DAY); h.refresh(); assert.equal(h.prompt.hidden, true); // Once per page view.
    for (const how of ['close', 'backdrop']) {
      h = open({width});
      h.scroll(799); assert.equal(h.prompt.hidden, true);
      h.scroll(800); assert.equal(h.prompt.hidden, false); // Exactly half the scrollable page.
      h.click(how === 'close' ? h.close : h.prompt);
      assert.equal(h.prompt.hidden, true);
      assert.equal(h.document.activeElement, h.opener);
    }
  }
  for (const options of [
    {blocked:'read'}, {blocked:'write'}, {stored:{'wp.join.joined':'1'}},
    {stored:{'wp.join.promptUntil':String(1000 + 7 * DAY)}},
    ...['/join', '/join/', '/join.html', '/join.html/'].map(pathname=>({pathname})),
    {source:source.replace('"prompt":true', '"prompt":false')},
    {source:source.replace(/"api_url":"[^"]+"/, '"api_url":""')}
  ]) {
    const h = open(options); h.scroll(1600); h.advance(60000);
    assert.equal(h.prompt.hidden, true, JSON.stringify(options));
    assert.equal(h.events.length, 0);
    assert.equal(h.requests.length, 0);
    assert.equal(h.scripts.length, 0);
  }
  let h = open(); h.document.hidden = true; h.advance(25000);
  assert.equal(h.prompt.hidden, true);
  h.document.hidden = false; h.handlers.visibilitychange(); assert.equal(h.prompt.hidden, false);
  h = open(); h.document.documentElement.scrollHeight = 800; h.scroll(0);
  assert.equal(h.prompt.hidden, true); // Short pages wait for time; 0/0 is not a scroll.
  h.advance(25000); assert.equal(h.prompt.hidden, false);
  h.type('not-an-email'); assert.equal(h.submit.disabled, true);
  h.type(' person@example.com '); assert.equal(h.submit.disabled, false);
  h.submitEmail(); await flush(); await flush();
  assert.deepEqual(h.requests.map(r=>({url:r.url, method:r.method, body:r.body})), [{
    url:'https://account.example.invalid/subscribe', method:'POST', body:'{"email":"person@example.com"}'
  }]);
  assert.equal(h.storage.get('wp.join.joined'), '1');
  assert.equal(h.success.hidden, false);
  h.advance(1800); assert.equal(h.prompt.hidden, true);
  h = open(); h.sub({status:429, body:{}}); h.advance(25000); h.submitEmail();
  await flush(); await flush();
  assert.equal(h.message.role, 'alert'); assert.equal(h.storage.has('wp.join.joined'), false);
  h = open(); h.advance(25000); h.handlers.storage({key:'wp.join.joined', newValue:'1'});
  assert.equal(h.prompt.hidden, true);
  // Native refreshes run on the next animation frame, including after the timeout.
  let stored = {};
  for (const pathname of ['/about.html', '/contact.html']) {
    h = open({pathname, stored, queuedFrames:true});
    h.frame(); h.advance(24999); h.frame(); assert.equal(h.prompt.hidden, true);
    h.advance(1); assert.equal(h.prompt.hidden, true);
    h.frame(); assert.equal(h.prompt.hidden, false);
    h.key('Escape');
    stored = Object.fromEntries(h.storage);
    const next = open({pathname:'/careers.html', stored, queuedFrames:true});
    next.frame(); next.advance(30000); next.frame(); assert.equal(next.prompt.hidden, true);
    delete stored['wp.join.promptUntil']; // Independent visitor; daily memory itself is correct.
  }

  const motion = fs.readFileSync(process.argv[3], 'utf8');
  function reveals({reduced = false, supported = true} = {}) {
    const preference = {matches:reduced}, observed = new Set(), classes = new Set();
    const card = {classList:{add:name=>classes.add(name)}};
    let callback;
    function IntersectionObserver(cb) {
      callback = cb;
      this.observe = el=>observed.add(el);
      this.unobserve = el=>observed.delete(el);
    }
    vm.runInNewContext(motion, {
      window:{matchMedia:()=>preference, ...(supported ? {IntersectionObserver} : {})},
      IntersectionObserver,
      document:{querySelectorAll:()=>[card]}
    });
    return {classes, observed, preference, enter(visible) {
      if (observed.has(card)) callback([{target:card, isIntersecting:visible}]);
    }};
  }
  const reveal = reveals();
  reveal.enter(false); assert.equal(reveal.classes.size, 0);
  reveal.enter(true); assert(reveal.classes.has('brand-reveal'));
  assert.equal(reveal.observed.size, 0); // One entrance; scrolling back cannot replay it.
  reveal.classes.clear(); reveal.enter(true); assert.equal(reveal.classes.size, 0);
  for (const options of [{reduced:true}, {supported:false}]) {
    const plain = reveals(options); plain.enter(true);
    assert.equal(plain.observed.size, 0); assert.equal(plain.classes.size, 0);
  }
  const changed = reveals(); changed.preference.matches = true; changed.enter(true);
  assert.equal(changed.classes.size, 0);
  console.log('brand pages: queued frames, timer, scroll, daily memory, focus, shared signup copy and motion passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
