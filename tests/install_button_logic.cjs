// Execute the published script with browser events, storage and a native-dialog stand-in.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync(process.argv[2], 'utf8');
const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1';
const week = 7 * 24 * 60 * 60 * 1000;
const flush = () => new Promise(resolve => setImmediate(resolve));
function target() {
  const listeners = {};
  return {hidden:true, open:false,
    addEventListener(name, fn) { (listeners[name] ||= []).push(fn); },
    emit(name, event = {}) {
      event.currentTarget = this;
      event.target ||= this;
      event.preventDefault ||= () => {event.prevented = true;};
      for (const fn of listeners[name] || []) fn(event);
      return event;
    },
    showModal() {this.open = true; this.modal = true;},
    close() {this.open = false;}
  };
}
function page(options = {}) {
  const elements = Object.fromEntries(['pwa-actions','pwa-install','pwa-ios','pwa-header-install','pwa-help','pwa-help-close']
    .map(id => [id, target()]));
  if (options.noHeader) delete elements['pwa-header-install'];
  const store = options.store || {}, session = options.session || {}, events = [];
  let now = options.now || 1000000000;
  function storage(data) {return {
    getItem(k) {if (options.blockedStorage) throw Error('blocked'); return data[k] || null;},
    setItem(k,v) {if (options.blockedStorage) throw Error('blocked'); data[k] = String(v);}
  };}
  const mode = Object.assign(target(), {matches:!!options.standalone});
  const document = Object.assign(target(), {
    readyState:options.loading ? 'loading' : 'complete', referrer:options.referrer || '',
    activeElement:null,
    documentElement:{classList:{contains:() => !!options.android}},
    getElementById:id => elements[id] || null
  });
  for (const element of Object.values(elements)) {
    element.focus = () => {document.activeElement = element;};
  }
  const win = Object.assign(target(), {document,
    navigator:{userAgent:options.ua || 'Chrome', platform:options.platform || '',
      maxTouchPoints:options.touch || 0, standalone:!!options.appleStandalone},
    matchMedia:() => mode, localStorage:storage(store), sessionStorage:storage(session),
    location:{search:options.search || ''}, URLSearchParams, Promise,
    Date:{now:() => now},
    wpTrack: options.noTracking ? undefined : (name, params) => {
      if (options.trackThrows) throw Error('analytics blocked');
      events.push([name, JSON.parse(JSON.stringify(params))]);
    }
  });
  win.window = win;
  vm.runInNewContext(code, win);
  return {win, document, elements, mode, events, store, session, advance:n => {now += n;}};
}
function offer(p, outcome = 'accepted', failure) {
  const event = {prompts:0, preventDefault() {this.prevented = true;},
    prompt() {
      this.prompts++;
      if (failure === 'sync') throw Error('unavailable');
      return failure === 'async' ? Promise.reject(Error('unavailable')) : Promise.resolve();
    }, userChoice:Promise.resolve({outcome})};
  p.win.emit('beforeinstallprompt', event);
  return event;
}
function click(p, id = 'pwa-header-install') {return p.elements[id].emit('click');}
function hidden(p) {
  for (const id of ['pwa-actions','pwa-install','pwa-ios','pwa-header-install']) {
    if (p.elements[id]) assert.equal(p.elements[id].hidden, true, id);
  }
}
const count = (p, name) => p.events.filter(e => e[0] === name).length;

(async () => {
  const early = page({loading:true});
  hidden(early);
  const prompt = offer(early), duplicate = offer(early);
  assert(prompt.prevented && duplicate.prevented);
  hidden(early);
  early.document.emit('DOMContentLoaded');
  assert.equal(early.elements['pwa-header-install'].hidden, false);
  assert.equal(prompt.prompts, 0);
  click(early); click(early, 'pwa-install');
  assert.equal(prompt.prompts, 1); assert.equal(duplicate.prompts, 0);
  hidden(early);
  early.win.emit('appinstalled'); await flush(); early.win.emit('appinstalled');
  offer(early); hidden(early);
  assert.deepEqual(early.events, [['install_offer_shown',{}],['install_click',{}],['install_accepted',{}],['installed',{}]]);
  for (const noHeader of [false,true]) {
    const p = page({noHeader});
    offer(p); click(p,'pwa-install'); await flush(); hidden(p);
    assert.equal(count(p,'install_accepted'),1);
    assert.equal(count(p,'installed'),0);
    p.win.emit('appinstalled'); assert.equal(count(p,'installed'),1);
    assert.equal(count(p,'install_accepted'),1);
  }
  for (const failure of [undefined,'sync','async']) {
    const p = page(); offer(p,'dismissed',failure); click(p); await flush(); hidden(p);
    assert.equal(count(p,'install_dismissed'),1);
    assert.equal(count(p,'install_accepted'),0);
    assert.equal(Number(p.store['wp.install.dismissedUntil']),1000000000+week);
    const reloaded = page({store:p.store,now:1000000000+week-1});
    offer(reloaded); hidden(reloaded);
    reloaded.advance(1); offer(reloaded);
    assert.equal(reloaded.elements['pwa-header-install'].hidden,false);
  }
  const crossTab = page(); offer(crossTab);
  crossTab.win.emit('storage',{key:'wp.install.dismissedUntil',newValue:String(1000000000+week)});
  hidden(crossTab);
  for (const ua of [iphone, iphone.replaceAll('iPhone','Macintosh')]) {
    const p = page({ua,platform:'MacIntel',touch:5});
    assert.equal(p.elements['pwa-header-install'].hidden,false);
    assert.equal(p.elements['pwa-install'].hidden,true);
    for (const close of ['x','escape','backdrop']) {
      assert(click(p).prevented);
      assert(p.elements['pwa-help'].open && p.elements['pwa-help'].modal);
      assert.equal(p.document.activeElement,p.elements['pwa-help-close']);
      for (const shiftKey of [false,false,true,true]) {
        assert(p.elements['pwa-help'].emit('keydown',{key:'Tab',shiftKey}).prevented);
        assert.equal(p.document.activeElement,p.elements['pwa-help-close']);
      }
      assert(!p.elements['pwa-help'].emit('keydown',{key:'Escape'}).prevented);
      if (close === 'x') click(p,'pwa-help-close');
      if (close === 'escape') assert(p.elements['pwa-help'].emit('cancel').prevented);
      if (close === 'backdrop') p.elements['pwa-help'].emit('click');
      assert(p.elements['pwa-help'].hidden && !p.elements['pwa-help'].open);
      assert.equal(p.document.activeElement,p.elements['pwa-header-install']);
      assert(!p.elements['pwa-help'].emit('keydown',{key:'Tab'}).prevented);
    }
    click(p,'pwa-ios');
    p.elements['pwa-help'].emit('click',{target:p.elements['pwa-help-close']});
    assert(p.elements['pwa-help'].open);
    click(p,'pwa-help-close'); assert.equal(p.document.activeElement,p.elements['pwa-ios']);
    assert.equal(count(p,'install_click'),4);
    assert.equal(count(p,'install_dismissed'),0);
    click(p); p.mode.matches = true; p.mode.emit('change');
    assert(p.elements['pwa-help'].hidden); hidden(p);
  }
  for (const options of [{standalone:true},{appleStandalone:true},{android:true},
    {search:'?src=twa'},{referrer:'android-app://com.wowpriced.app/'},{session:{wp_app:'android'}},
    ...['FBAN','FBAV','Instagram','instagram'].map(ua => ({ua:iphone+' '+ua}))]) {
    const p = page({ua:iphone,...options}); hidden(p);
    const event = offer(p); click(p); hidden(p); assert.equal(event.prompts,0);
    assert.equal(count(p,'install_click'),0);
  }
  for (const ua of [iphone.replace('Version/18.0','CriOS/131.0'),iphone+' FxiOS/18.0',
    iphone+' EdgiOS/18.0',iphone.replaceAll('iPhone','Android'),iphone.replaceAll('iPhone','Macintosh')]) {
    hidden(page({ua}));
  }
  for (const options of [{blockedStorage:true},{noTracking:true},{trackThrows:true}]) {
    const p = page(options); offer(p,'dismissed'); click(p); await flush(); hidden(p);
    offer(p); hidden(p);
    const safari = page({...options,ua:iphone}); click(safari); assert(safari.elements['pwa-help'].open);
    click(safari,'pwa-help-close');
  }
  const session = {};
  assert.equal(count(page({standalone:true,session}),'standalone_session'),1);
  assert.equal(count(page({standalone:true,session}),'standalone_session'),0);
  console.log('PASS: prompt, footer, iPhone/iPad sheet, focus, suppression, seven days, storage failures and analytics deduplication');
})().catch(error => {console.error(error); process.exitCode = 1;});
