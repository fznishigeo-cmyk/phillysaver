const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync(process.argv[2], 'utf8');
const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1';
const flush = () => new Promise(resolve => setImmediate(resolve));

function target() {
  const listeners = {};
  return {
    hidden: true, focused: false,
    addEventListener(name, fn) { (listeners[name] ||= []).push(fn); },
    emit(name, event = {}) { for (const fn of listeners[name] || []) fn(event); },
    focus() { this.focused = true; }
  };
}
function page(options = {}) {
  const events = [], store = options.store || {};
  const elements = Object.fromEntries(['pwa-actions', 'pwa-install', 'pwa-ios', 'pwa-help', 'pwa-help-close'].map(id => [id, target()]));
  const win = target(), document = target(), mode = target();
  mode.matches = !!options.standalone;
  document.readyState = options.loading ? 'loading' : 'complete';
  document.getElementById = id => elements[id];
  Object.assign(win, {
    document, matchMedia: () => mode,
    navigator: {userAgent: options.ua || 'Android Chrome/131', standalone: !!options.appleStandalone,
      platform: options.platform || '', maxTouchPoints: options.touch || 0},
    sessionStorage: {
      getItem(key) { if (options.blocked) throw Error('blocked'); return store[key] || null; },
      setItem(key, value) { if (options.blocked) throw Error('blocked'); store[key] = value; }
    },
    wpTrack: options.noTracking ? undefined : (name, params) => {
      if (options.trackThrows) throw Error('analytics blocked');
      events.push([name, JSON.parse(JSON.stringify(params))]);
    }
  });
  win.window = win;
  vm.runInNewContext(code, win);
  return {win, document, mode, elements, events, store};
}
function offer(p, outcome = 'accepted', failure) {
  const event = {
    prevented: 0, prompts: 0,
    preventDefault() { this.prevented++; },
    prompt() {
      this.prompts++;
      if (failure === 'sync') throw Error('prompt unavailable');
      return failure === 'async' ? Promise.reject(Error('prompt unavailable')) : Promise.resolve();
    },
    userChoice: failure === 'choice' ? Promise.reject(Error('choice unavailable')) : Promise.resolve({outcome})
  };
  p.win.emit('beforeinstallprompt', event);
  return event;
}
function count(p, name) { return p.events.filter(event => event[0] === name).length; }
function hidden(p) {
  assert.equal(p.elements['pwa-actions'].hidden, true);
  assert.equal(p.elements['pwa-install'].hidden, true);
  assert.equal(p.elements['pwa-ios'].hidden, true);
}

(async () => {
  const early = page({loading: true});
  hidden(early);
  const deferred = offer(early);
  hidden(early);
  assert.equal(deferred.prevented, 1);
  early.document.emit('DOMContentLoaded');
  assert.equal(early.elements['pwa-install'].hidden, false);
  assert.equal(deferred.prompts, 0);
  const duplicate = offer(early);
  assert.equal(duplicate.prevented, 1);
  assert.equal(count(early, 'install_offer_shown'), 1);
  early.elements['pwa-install'].emit('click');
  early.elements['pwa-install'].emit('click');
  assert.equal(deferred.prompts, 1);
  assert.equal(duplicate.prompts, 0);
  hidden(early);
  early.win.emit('appinstalled');
  await flush();
  early.win.emit('appinstalled');
  offer(early);
  hidden(early);
  assert.equal(count(early, 'install_accepted'), 1);
  assert.deepEqual(early.events, [['install_offer_shown', {}], ['install_accepted', {}]]);

  const choiceFirst = page();
  hidden(choiceFirst);
  offer(choiceFirst);
  choiceFirst.elements['pwa-install'].emit('click');
  await flush();
  hidden(choiceFirst);
  assert.equal(count(choiceFirst, 'install_accepted'), 1);
  choiceFirst.win.emit('appinstalled');
  assert.equal(count(choiceFirst, 'install_accepted'), 1);

  for (const failure of [undefined, 'sync', 'async', 'choice']) {
    const dismissed = page();
    offer(dismissed, 'dismissed', failure);
    dismissed.elements['pwa-install'].emit('click');
    await flush();
    hidden(dismissed);
    offer(dismissed);
    hidden(dismissed);
    assert.equal(count(dismissed, 'install_accepted'), 0);
    assert.equal(count(dismissed, 'install_offer_shown'), 1);
  }

  const safari = page({ua: iphone});
  assert.equal(safari.elements['pwa-ios'].hidden, false);
  assert.equal(safari.elements['pwa-install'].hidden, true);
  assert.equal(safari.elements['pwa-help'].hidden, true);
  safari.elements['pwa-ios'].emit('click');
  assert.equal(safari.elements['pwa-help'].hidden, false);
  assert.equal(safari.elements['pwa-help'].focused, true);
  safari.elements['pwa-help-close'].emit('click');
  assert.equal(safari.elements['pwa-help'].hidden, true);
  assert.equal(safari.elements['pwa-ios'].focused, true);
  assert.equal(count(safari, 'install_offer_shown'), 1);
  const ipad = page({ua: iphone.replaceAll('iPhone', 'Macintosh'), platform: 'MacIntel', touch: 5});
  assert.equal(ipad.elements['pwa-ios'].hidden, false);
  for (const ua of [iphone.replace('Version/18.0', 'CriOS/131.0'), iphone + ' FxiOS/18.0',
    iphone + ' EdgiOS/18.0', iphone + ' OPiOS/18.0', iphone + ' GSA/18.0', iphone + ' Instagram',
    iphone.replaceAll('iPhone', 'Android'), iphone.replaceAll('iPhone', 'Macintosh')]) {
    hidden(page({ua}));
  }

  const session = {};
  for (const flags of [{standalone: true}, {appleStandalone: true}, {standalone: true, blocked: true}]) {
    const installed = page({...flags, ua: iphone, store: session});
    hidden(installed);
    assert.equal(offer(installed).prompts, 0);
    hidden(installed);
    installed.mode.emit('change');
    assert.equal(count(installed, 'standalone_session'), flags.appleStandalone ? 0 : 1);
  }
  const sameSession = page({standalone: true, store: session});
  assert.equal(count(sameSession, 'standalone_session'), 0);
  assert.equal(count(page({standalone: true}), 'standalone_session'), 1);

  safari.elements['pwa-ios'].emit('click');
  safari.mode.matches = true;
  safari.mode.emit('change');
  safari.mode.emit('change');
  hidden(safari);
  assert.equal(safari.elements['pwa-help'].hidden, true);
  assert.equal(count(safari, 'standalone_session'), 1);
  assert.equal(count(safari, 'install_accepted'), 0);
  for (const options of [{noTracking: true}, {trackThrows: true}]) {
    const untracked = page(options);
    offer(untracked);
    assert.equal(untracked.elements['pwa-install'].hidden, false);
    untracked.elements['pwa-install'].emit('click');
    await flush();
    hidden(untracked);
  }
  console.log('Install prompt, iOS help, standalone sessions and event deduplication passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
