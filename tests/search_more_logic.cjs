// Execute the emitted publisher code with a deterministic clock, DOM and observer.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const between = (start, end) => html.slice(html.indexOf(start), html.indexOf(end, html.indexOf(start)));
const modulePath = path.join(path.dirname(process.argv[2]), 'search-more.js');
const moduleSource = fs.existsSync(modulePath) ? fs.readFileSync(modulePath, 'utf8') : 'export default function () {}';
const analyzePath = path.join(path.dirname(process.argv[2]), 'search-analyze.js');
const analyzeSource = fs.existsSync(analyzePath) ? fs.readFileSync(analyzePath, 'utf8') : '';
const code = (analyzeSource ? 'var searchAnalyzeModule = ' + analyzeSource.replace('export default ', '') + ';\n' : '') +
  'var searchMoreModule = ' + moduleSource.replace('export default ', '') + ';\n' +
  (between('  function searchText()', '  function renderTabs(') +
  between('  function filtered(', '  function searchRecoveryOptions(') +
  between('  function searchRecoveryHtml()', '  function recoverSearch(') +
  between('  function render() {', '  function showMore()') +
  between('  var searchTimer, appliedSearch', '  document.getElementById("storesel").addEventListener'))
    .replace(/import\("\/search-more\.js\?v=[a-f0-9]+"\)/, 'loadSearchMore()')
    .replace(/import\("\/search-analyze\.js\?v=[a-f0-9]+"\)/, 'loadSearchAnalyze()');
const turn = () => new Promise(resolve => setImmediate(resolve));
const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const asin = n => 'B0' + String(n).padStart(8, '0');
const deal = (n, title = 'Wireless mouse', extra = {}) => ({key:String(n), dedupe_key:String(n), item:title,
  status:'alive', url:'https://www.amazon.com/dp/' + asin(n) + '?tag=example-20', ...extra});

function setup(observer = true) {
  let now = Date.parse('2026-10-18T00:00:00Z'), serial = 0;
  const calls = [], events = [], opened = [], timers = new Map(), observers = [], listeners = {}, elements = {}, modules = [], analyzeModules = [];
  function node(id) {
    return {id, _html:'', value:'', hidden:false, connected:true, handlers:{}, attributes:{},
      get innerHTML() {return this._html;},
      set innerHTML(text) {
        this._html = text;
        if (id !== 'content') return;
        this.lastChild = {};
        for (const name of ['search-more', 'amazon-results', 'search-recovery-title', 'moremore', 'totop']) {
          if (elements[name]) elements[name].remove();
        }
        for (const name of ['amazon-results', 'search-recovery-title', 'moremore', 'totop']) {
          if (text.includes('id="' + name + '"')) elements[name] = node(name);
        }
        if (elements['search-recovery-title']) elements['search-recovery-title'].textContent =
          text.match(/<h2 id="search-recovery-title">([^<]*)<\/h2>/)[1]
            .replace(/&(amp|lt|gt|quot|#39);/g, (_, key) => ({amp:'&', lt:'<', gt:'>', quot:'"', '#39':"'"}[key]));
      },
      addEventListener(type, fn) {(this.handlers[type] ||= []).push(fn);},
      setAttribute(name, value) {this.attributes[name] = value;},
      querySelector(selector) {return this.children?.[selector] || (selector === '.more' && elements.moremore) || null;},
      appendChild() {this.lastChild = {};}, insertBefore() {}, focus() {},
      remove() {this.connected = false; if (this.children) Object.values(this.children).forEach(child => child.remove()); delete elements[id];},
      insertAdjacentHTML(position, markup) {
        assert.equal(position, 'beforeend');
        const box = elements['search-more'] = node('search-more'); box._html = markup;
        const button = node('load-button'), sentinel = node('load-sentinel');
        sentinel.children = {button}; box.children = {'.grid':node('amazon-grid'), '.search-more-load':sentinel};
      }};
  }
  for (const name of ['q', 'content', 'analyze-search', 'search-hint', 'clearsearch', 'searchcount',
    'search-amazon-result', 'search-spelling', 'storesel', 'minpct']) elements[name] = node(name);
  function links(markup, container) {
    return [...markup.matchAll(/<article\b[^>]*class="[^"]*\bcard\b[^"]*"[^>]*>([\s\S]*?)<\/article>/g)]
      .flatMap(article => [...article[1].matchAll(/<a\b[^>]*href="([^"]+)"/g)].map(match => ({
        href:match[1].replaceAll('&amp;', '&'), closest:selector => selector === container})));
  }
  class Observer {
    constructor(callback, options) {this.callback = callback; this.options = options; this.targets = []; observers.push(this);}
    observe(target) {this.targets.push(target);}
    disconnect() {this.targets = [];}
  }
  const ctx = {URL, URLSearchParams, Map, Set, WeakMap, AbortController, Number, RegExp, Promise,
    Date:class extends Date {static now() {return now;}}, location:{search:''},
    document:{getElementById:id => elements[id], body:{contains:node => node.connected},
      createElement:node, head:{appendChild(){}},
      querySelector:selector => selector === '.analyze-short' ? (elements.short ||= node('short')) : null,
      addEventListener:(name, fn) => (listeners[name] ||= []).push(fn),
      querySelectorAll(selector) {
        assert.equal(selector, '#content .card a[href]');
        return links(elements.content.innerHTML, '')
          .concat(links(elements['amazon-results']?.innerHTML || '', '#amazon-results'))
          .concat(links(elements['search-more']?.children['.grid'].innerHTML || '', '#search-more'));
      }},
    window:{wpTrack:(...args) => events.push(args), open:(...args) => opened.push(args), wpSupport:{refresh(){}}},
    localStorage:{getItem:() => null, setItem(){}},
    setTimeout:(fn, ms = 0) => {timers.set(++serial, {fn, at:now + ms}); return serial;}, clearTimeout:id => timers.delete(id),
    track:(...args) => events.push(args), esc:escape, CATGROUPS:[['All', null]], loadedOnce:true,
    state:{deals:[], spotlight:[], catTab:0, shown:60, sortKey:'top', meta:{}}, savedDeals:{view:false},
    renderSavedControls(){}, renderTabs(){}, isLocked:d => !!d.locked, dealKey:d => d.key,
    plural:(n, word) => n + ' ' + word, num:Number, storeKey:value => value || '', usedOrOpenBox:() => false,
    matchesEligibility:() => true, searchRecoveryOptions:() => [],
    cardHtml:d => '<article class="card" data-key="' + d.key + '"><a href="' + escape(d.url) + '">' + escape(d.item) + '</a></article>',
    gridHtml:rows => '<div class="grid">' + rows.map(ctx.cardHtml).join('') + '</div>',
    resetWall:() => {ctx.state.shown = 60; ctx.render();}, rememberBrowse(){}, actionSignature:() => '',
    moreObserver:null, paintTicker(){}, savedOn:() => false, engagementLineHtml:() => '', activeSpotlight:() => [],
    ordered:rows => rows, updatePaywall(){}, openNudge:() => false, frontPageView:() => false,
    placeGone:rows => rows, confirmedFirst:rows => rows, clickPriceHomeRows:rows => rows, searchNewFirst:rows => rows,
    spaceAllure:rows => rows, spaceRotatedPromos:rows => rows,
    openAccess:() => false, canJoin:() => false, openBand:() => '', panel:{}, PAGE_CARDS:60, AUTO_CARDS:240,
    showMore() {ctx.state.shown += 60; ctx.render();},
    fetch:(url, options) => new Promise((resolve, reject) => calls.push({url, options, resolve, reject})),
    loadSearchMore:() => {modules.push('search-more.js'); return Promise.resolve({default:ctx.searchMoreModule});},
    loadSearchAnalyze:() => {analyzeModules.push('search-analyze.js'); return Promise.resolve({default:ctx.searchAnalyzeModule});}};
  if (observer) ctx.IntersectionObserver = ctx.window.IntersectionObserver = Observer;
  vm.createContext(ctx); vm.runInContext(code, ctx);
  const emit = (name, type, event = {}) => {
    for (const fn of elements[name].handlers[type] || []) fn.call(elements[name], event);
  };
  const advance = async ms => {
    now += ms;
    for (const [id, value] of [...timers].filter(([, value]) => value.at <= now)) {timers.delete(id); value.fn();}
    await turn(); await turn();
  };
  return {ctx, calls, events, opened, elements, observers, advance, emit, modules, analyzeModules,
    async query(q) {elements.q.value = q; emit('q', 'input'); ctx.updateSearch(); await turn();},
    item(n, title = 'Wireless mouse', extra = {}) {return {asin:asin(n), title, price_cents:1299,
      as_of:new Date(now - 60000).toISOString(), image:'https://m.media-amazon.com/images/I/photo.jpg', ...extra};},
    intersect() {
      const observer = observers.findLast(o => o.options.rootMargin === '300px' && o.targets.length);
      if (observer) observer.callback(observer.targets.map(target => ({target, isIntersecting:true})));
    },
    async respond(call, data) {call.resolve({ok:true, json:async () => data}); await turn(); await turn();},
    click(selector) {
      const event = {type:'click', button:0, target:{closest:other => other === selector}, preventDefault(){}};
      for (const fn of listeners.click || []) fn(event);
    },
    markup() {return elements['search-more']?.children['.grid'].innerHTML || '';}
  };
}

async function main() {
  const s = setup(); s.ctx.state.deals = [deal(1), deal(2)]; s.ctx.render(); await turn();
  assert.equal(s.modules.length, 0, 'the home page does not load optional search code');
  await s.query('mouse'); assert.equal(s.modules.length, 1);
  assert(s.elements['search-more'].innerHTML.includes('More from Amazon for &quot;mouse&quot;'));
  await s.advance(10000); assert.equal(s.calls.length, 0, 'no intersection means no request');
  s.intersect(); await s.advance(0);
  assert.equal(s.calls.length, 1); assert.equal(s.calls[0].url, '/api/search?q=mouse&page=0');
  s.intersect(); await s.advance(0); assert.equal(s.calls.length, 1, 'one in-flight page');
  await s.respond(s.calls[0], {items:[s.item(1), s.item(101, 'Mouse <script>bad</script>'), s.item(102), s.item(102),
    s.item(103, 'Anal lubricant'), s.item(104, 'Old', {as_of:'2020-01-01T00:00:00Z'}), s.item(105, 'Bad', {price_cents:-1})]});
  assert(!s.markup().includes('/dp/' + asin(1)));
  assert(!s.markup().includes('lubricant') && !s.markup().includes('<script>'));
  for (const text of ['Mouse &lt;script&gt;bad&lt;/script&gt;', '$12.99', 'Amazon price as of ',
    'rel="sponsored nofollow noopener"', '?tag=example-20', 'loading="lazy"']) assert(s.markup().includes(text), text);
  assert.equal((s.markup().match(/<article /g) || []).length, 2);
  s.intersect(); await s.advance(1099); assert.equal(s.calls.length, 1, 'respect completion cooldown');
  await s.advance(1); assert.equal(s.calls[1].url, '/api/search?q=mouse&page=1');
  await s.respond(s.calls[1], {items:[s.item(102), s.item(106)]});
  s.intersect(); await s.advance(1100); assert.equal(s.calls[2].url, '/api/search?q=mouse&page=2');
  await s.respond(s.calls[2], {items:[s.item(101), s.item(107)]});
  s.intersect(); await s.advance(10000); assert.equal(s.calls.length, 3);
  assert.equal((s.markup().match(/<article /g) || []).length, 4);
  for (const n of [101, 102, 106, 107]) assert.equal(s.markup().split('/dp/' + asin(n)).length - 1, 1);
  assert(s.elements['search-more'].children['.search-more-load'].hidden);
  assert(s.elements['search-more'].innerHTML.includes('See all &quot;mouse&quot; on Amazon →'));
  assert(s.elements['search-more'].innerHTML.includes('href="https://www.amazon.com/?tag=example-20"'));
  assert(!s.elements['search-more'].innerHTML.includes('k=mouse'), 'automatic outbound tracking cannot collect query');
  s.click('[data-search-amazon]'); assert.equal(s.opened.at(-1)[0], 'https://www.amazon.com/s?k=mouse&tag=example-20');
  s.ctx.render(); s.intersect(); await s.advance(2000); assert.equal(s.calls.length, 3, 'rerenders reuse pages');
  assert.deepEqual(JSON.parse(JSON.stringify(s.events.filter(e => e[0] === 'search_more_page'))),
    [0, 1, 2].map(page => ['search_more_page', {page}]));
  assert(!JSON.stringify(s.events).includes('mouse'));

  const many = setup(); many.ctx.state.deals = Array.from({length:65}, (_, n) => deal(n)); await many.query('mouse');
  assert(!many.elements['search-more'], 'our remaining deals come before Amazon');
  many.ctx.showMore(); await turn(); assert(many.elements['search-more']); await many.advance(1000); assert.equal(many.calls.length, 0);

  const delayed = setup(); let releaseModule;
  delayed.ctx.loadSearchMore = () => new Promise(resolve => {releaseModule = resolve;});
  delayed.ctx.state.deals = [deal(1), deal(2, 'Steel kettle')];
  await delayed.query('mouse'); await delayed.query('kettle');
  releaseModule({default:delayed.ctx.searchMoreModule}); await turn();
  assert(delayed.elements['search-more'].innerHTML.includes('&quot;kettle&quot;'));
  assert.equal(delayed.calls.length, 0, 'a slow code download cannot spend for an abandoned query');
  const unavailable = setup(); unavailable.ctx.loadSearchMore = () => Promise.reject(new Error('offline'));
  unavailable.ctx.state.deals = [deal(1)]; await unavailable.query('mouse'); await unavailable.advance(1000);
  assert.equal(unavailable.calls.length, 0);
  assert(unavailable.elements['search-amazon-result'].innerHTML.includes('data-search-amazon'));

  for (const reason of ['rate_limited', 'budget_exhausted', 'budget_unavailable', 'limit_reached', 'timeout', 'price_error', 'disabled']) {
    const t = setup(); t.ctx.state.deals = [deal(1)]; await t.query('mouse'); t.intersect(); await t.advance(0);
    await t.respond(t.calls[0], {items:[t.item(101)]}); t.intersect(); await t.advance(1100);
    await t.respond(t.calls[1], {items:[], reason});
    for (let n = 0; n < 3; n++) {t.intersect(); await t.advance(10000); t.ctx.render(); await turn();}
    assert.equal(t.calls.length, 2, reason); assert(t.elements['search-more'].innerHTML.includes('See all'));
    assert(!t.elements['search-more'].innerHTML.includes(reason));
    assert.equal((t.markup().match(/<article /g) || []).length, 1, 'keep earlier cards after a later-page failure');
    assert(!t.markup().includes(reason), 'no error text in the grid');
    assert(t.elements['search-more'].children['.search-more-load'].hidden);
  }
  const changed = setup(); changed.ctx.state.deals = [deal(1), deal(2, 'Steel kettle')];
  await changed.query('mouse'); changed.intersect(); await changed.advance(0);
  const old = changed.calls[0]; await changed.query('kettle'); assert(old.options.signal.aborted);
  await changed.respond(old, {items:[changed.item(101, 'STALE')]});
  assert(!changed.markup().includes('STALE')); changed.intersect(); await changed.advance(0);
  assert.equal(changed.calls[1].url, '/api/search?q=kettle&page=0');

  const manual = setup(false); manual.ctx.state.deals = [deal(1)]; await manual.query('mouse');
  const button = manual.elements['search-more'].children['.search-more-load'].children.button;
  assert.equal(button.hidden, false); await manual.advance(1000); assert.equal(manual.calls.length, 0);
  button.onclick(); await manual.advance(0); assert.equal(manual.calls.length, 1);

  const restricted = setup(); restricted.ctx.state.deals = [deal(1)]; await restricted.query('mouse');
  restricted.ctx.searchMoreUI.settings.min_query_chars = 6; await restricted.query('mouse'); assert(!restricted.elements['search-more']);
  restricted.ctx.searchMoreUI.settings.min_query_chars = 2;
  restricted.ctx.amazonResultsSettings.mode = 'preview'; await restricted.query('mouse'); assert(!restricted.elements['search-more']);
  restricted.ctx.amazonResultsSettings.mode = 'on'; restricted.ctx.wpAndroidLimited = () => true;
  await restricted.query('mouse'); assert(!restricted.elements['search-more']);
  restricted.ctx.wpAndroidLimited = () => false;
  for (const q of ['', 'x', 'private@example.test', 'Anal lubricant', 'x'.repeat(61)]) {
    await restricted.query(q); restricted.intersect(); await restricted.advance(2000);
    assert(!restricted.elements['search-more']);
  }
  assert.equal(restricted.calls.length, 0);

  function emptyRecovery(t, q = 'cupping set') {
    assert.equal(t.elements['search-recovery-title'].textContent, 'See every "' + q + '" price on Amazon');
    assert.equal(t.elements['amazon-results'].innerHTML, '', 'no empty grid or Amazon heading');
    const content = t.elements.content.innerHTML;
    assert(!content.includes('Live Amazon prices') && !content.includes('class="grid"'));
    const boosted = typeof t.ctx.amazonResultsRetries === 'number';
    assert(content.includes(boosted ? 'Compare all &quot;' + q + '&quot; prices on Amazon' : 'Search Amazon for &quot;' + q + '&quot;'));
    assert(content.includes('rel="sponsored noopener"'), 'the primary button keeps its existing rel');
    t.click('[data-search-amazon]');
    assert.equal(t.opened.at(-1)[0], 'https://www.amazon.com/s?k=' + encodeURIComponent(q) + '&tag=example-20');
  }
  for (const reason of ['budget_exhausted', 'budget_unavailable', 'rate_limited', 'timeout', 'price_error', 'disabled', '', 'network', 'http']) {
    const t = setup(); await t.query('cupping set'); emptyRecovery(t); await t.advance(600);
    const attempts = ['rate_limited', 'timeout'].includes(reason) && typeof t.ctx.amazonResultsRetries === 'number' ? 3 : 1;
    for (let n = 0; n < attempts; n++) {
      assert.equal(t.calls.length, n + 1, reason);
      if (reason === 'network') {t.calls[n].reject(new Error('offline')); await turn();}
      else if (reason === 'http') {t.calls[n].resolve({ok:false}); await turn(); await turn();}
      else await t.respond(t.calls[n], reason ? {items:[], reason} : {items:[]});
      emptyRecovery(t);
      await t.advance(1500);
    }
    await t.query('x'); await t.query('cupping set'); await t.advance(2000);
    emptyRecovery(t);
    assert.equal(t.calls.length, attempts, 'cached empty responses do not spend again: ' + reason);
  }
  for (const mode of ['off', 'preview']) {
    const t = setup(); t.ctx.amazonResultsSettings.mode = mode;
    await t.query('cupping set'); await t.advance(5000);
    emptyRecovery(t); assert.equal(t.calls.length, 0, 'disabled client needs no response to show the fallback');
  }
  const missingModule = setup(); missingModule.ctx.loadSearchMore = () => Promise.reject(new Error('offline'));
  await missingModule.query('cupping set'); await missingModule.advance(5000);
  emptyRecovery(missingModule); assert.equal(missingModule.calls.length, 0);

  const filteredEmpty = setup(); await filteredEmpty.query('cupping set'); await filteredEmpty.advance(600);
  await filteredEmpty.respond(filteredEmpty.calls[0], {items:[filteredEmpty.item(101, 'Anal lubricant'),
    filteredEmpty.item(102, 'Old price', {as_of:'2020-01-01T00:00:00Z'}), filteredEmpty.item(103, 'No price', {price_cents:0})]});
  emptyRecovery(filteredEmpty);

  for (const retry of [false, true]) {
    const t = setup(); await t.query('cupping set'); await t.advance(600); emptyRecovery(t);
    if (retry) {
      if (typeof t.ctx.amazonResultsRetries !== 'number') continue;
      await t.respond(t.calls[0], {items:[], reason:'rate_limited'}); emptyRecovery(t); await t.advance(1500);
    }
    await t.respond(t.calls.at(-1), {items:[t.item(101, 'Cupping set')]});
    assert.equal(t.elements['search-recovery-title'].textContent, 'Live Amazon prices for "cupping set"');
    const cards = t.elements['amazon-results'].innerHTML, count = t.calls.length;
    assert(cards.includes('<article class="card">'));
    await t.query('x'); await t.query('cupping set'); emptyRecovery(t); await t.advance(600);
    assert.equal(t.elements['search-recovery-title'].textContent, 'Live Amazon prices for "cupping set"');
    assert.equal(t.elements['amazon-results'].innerHTML, cards, 'cached cards restore the heading');
    assert.equal(t.calls.length, count);
    t.ctx.amazonResultsCancel(); emptyRecovery(t);
    await t.advance(86400000); t.ctx.render(); await turn(); await t.advance(600);
    emptyRecovery(t); assert.equal(t.calls.length, count, 'expired cached prices cannot leave a live heading');
  }

  // The shared renderer covers ordinary results, related pages and Analyze.
  for (const [list, price, pct] of [[1999, 1299, 35], [10000, 9500, 5], [10000, 9551, 0],
    [10000, 9549, 5], [undefined, 1299, 0], [null, 1299, 0], [1299, 1299, 0], [1200, 1299, 0],
    [1999.5, 1299, 0], ['1999', 1299, 0], [Number.MAX_SAFE_INTEGER + 1, 1299, 0]]) {
    for (const image of ['https://m.media-amazon.com/images/I/photo.jpg', '']) {
      const p = setup(); await p.query('kettle'); await p.advance(600);
      const item = p.item(600, 'Steel kettle', {list_price_cents:list, price_cents:price, image});
      await p.respond(p.calls[0], {items:[item]});
      const markup = p.elements['amazon-results'].innerHTML;
      assert(markup.includes('$' + (price / 100).toFixed(2)));
      assert.equal(markup.includes('<s>'), pct >= 5, String(list));
      assert.equal(markup.includes('class="flag"'), pct >= 5, String(list));
      assert.equal(markup.includes('List Price'), pct >= 5, String(list));
      if (pct) {
        assert(markup.includes('aria-label="' + pct + '% off List Price">-' + pct + '%</span>'));
        assert(markup.includes('<s><span class="sr-only">List Price </span>$' + (list / 100).toFixed(2) + '</s>'));
      }
      assert(!/price drop|history/i.test(markup));

      p.ctx.state.deals = [deal(1)]; await p.query('mouse'); p.intersect(); await p.advance(0);
      await p.respond(p.calls[1], {items:[item]});
      assert.equal(p.markup(), markup.match(/<article\b[\s\S]*<\/article>/)[0], 'related pages use the same markup');

      if (typeof p.ctx.analyzeSubmit === 'function') {
        await p.query('https://www.amazon.com/dp/' + asin(600));
        const analyzed = p.ctx.analyzeSubmit(); await turn();
        await p.respond(p.calls[2], {items:[item]}); await p.advance(900); await analyzed; await turn();
        assert(p.elements.content.innerHTML.includes('Live Amazon price'));
        assert.equal(p.elements['amazon-results'].innerHTML, markup, 'Analyze uses the same price markup');
      }
    }
  }

  const spelling = setup(); spelling.ctx.state.deals = [deal(1, 'Hair tie organizer'), deal(2, 'Steel kettle'),
    deal(3, 'Tire for a bike'), deal(4, 'Dead qwertyu', {status:'dead'}), deal(5, 'Private zxcvbn', {locked:true})];
  await spelling.query('mouse');
  for (const [query, corrected] of [['hair tire', 'hair tie'], ['mouce', 'mouse'], ['iphone 15', 'iphone 15'],
    ['xbox360', 'xbox360'], ['mouce12', 'mouce12'], ['abc-123', 'abc-123'], ['qwerty', 'qwerty'], ['zxcvb', 'zxcvb']]) {
    assert.equal(spelling.ctx.searchMoreUI.suggest(query), corrected, query);
  }
  await spelling.query('hair tire'); await spelling.advance(600);
  assert.equal(spelling.calls[0].url, '/api/search?q=hair%20tire&page=0');
  await spelling.respond(spelling.calls[0], {items:[]});
  assert.equal(spelling.elements.q.value, 'hair tie');
  assert(spelling.elements['search-spelling'].innerHTML.includes('Showing results for <b>hair tie</b>'));
  assert(spelling.elements['search-spelling'].innerHTML.includes('Search instead for &quot;hair tire&quot;'));
  await spelling.advance(5000); assert.equal(spelling.calls.length, 1, 'corrected local results need no lookup');
  spelling.click('[data-search-original]'); await spelling.advance(1000);
  assert.equal(spelling.elements.q.value, 'hair tire'); assert(spelling.elements['search-spelling'].hidden);
  assert.equal(spelling.calls.length, 1, 'Search instead restores cached original without correcting again');
  for (let n = 0; n < 3; n++) {
    spelling.emit('q', 'input'); spelling.ctx.render(); await spelling.advance(1300);
    assert.equal(spelling.elements.q.value, 'hair tire', 'unchanged input and rerenders retain the original');
  }
  await spelling.query('hair tir'); await spelling.query('hair tire'); await spelling.advance(600);
  assert.equal(spelling.elements.q.value, 'hair tie', 'editing re-enables correction using the successful empty cache');
  assert.equal(spelling.calls.length, 1, 'editing does not repeat the original Amazon request');
  spelling.click('[data-search-original]'); await spelling.advance(1300);

  // Continue the native script's sequence in the same mounted module after undo.
  await spelling.query('cofee'); await spelling.advance(600);
  assert.equal(spelling.calls[1].url, '/api/search?q=cofee&page=0');
  await spelling.respond(spelling.calls[1], {items:[]});
  assert.equal(spelling.elements.q.value, 'coffee');
  await spelling.advance(1100);
  assert.equal(spelling.calls[2].url, '/api/search?q=coffee&page=0');
  await spelling.respond(spelling.calls[2], {items:[spelling.item(900, 'Coffee maker', {list_price_cents:1999})]});
  assert(spelling.elements['amazon-results'].innerHTML.includes('35% off List Price'));
  if (typeof spelling.ctx.analyzeSubmit === 'function') {
    await spelling.query('https://www.amazon.com/dp/' + asin(901));
    const analyzed = spelling.ctx.analyzeSubmit(); await turn();
    assert.equal(spelling.calls[3].url, '/api/search?asin=' + asin(901));
    await spelling.respond(spelling.calls[3], {items:[spelling.item(901, 'Coffee grinder', {list_price_cents:1999})]});
    await spelling.advance(900); await analyzed; await turn();
    assert(spelling.elements.content.innerHTML.includes('Live Amazon price'));
    assert(spelling.elements['amazon-results'].innerHTML.includes('35% off List Price'));
  }
  spelling.ctx.state.deals.push(deal(6));
  const previous = spelling.calls.length;
  await spelling.query('wireless'); spelling.intersect(); await spelling.advance(0);
  assert.equal(spelling.calls[previous].url, '/api/search?q=wireless&page=0');
  await spelling.respond(spelling.calls[previous], {items:[spelling.item(101)]});
  spelling.intersect(); await spelling.advance(1100);
  assert.equal(spelling.calls[previous + 1].url, '/api/search?q=wireless&page=1');
  await spelling.respond(spelling.calls[previous + 1], {items:[], reason:'rate_limited'});
  spelling.ctx.render(); await spelling.advance(1300); spelling.intersect(); await spelling.advance(1300);
  assert.equal(spelling.calls.length, previous + 2);
  assert(spelling.elements['search-more'].innerHTML.includes('See all'));
  assert(!JSON.stringify(spelling.events).match(/hair|cofee|coffee|wireless/));

  const literal = setup(); literal.ctx.state.deals = [deal(1, 'Hair tie organizer')];
  await literal.query(' Hair   Tire '); await literal.advance(600);
  await literal.respond(literal.calls[0], {items:[]});
  literal.click('[data-search-original]'); await literal.advance(1300);
  assert.equal(literal.elements.q.value, ' Hair   Tire ', 'undo restores the exact typed text');
  assert.equal(literal.calls.length, 1);

  const correctedLocal = setup(); correctedLocal.ctx.state.deals = [deal(1, 'Hair tie organizer')];
  await correctedLocal.query('hair tire'); await correctedLocal.advance(600);
  await correctedLocal.respond(correctedLocal.calls[0], {items:[]});
  correctedLocal.intersect(); await correctedLocal.advance(1099); assert.equal(correctedLocal.calls.length, 1);
  await correctedLocal.advance(1);
  assert.equal(correctedLocal.calls[1].url, '/api/search?q=hair%20tie&page=0', 'corrected local matches also respect the cooldown');
  correctedLocal.click('[data-search-original]');
  assert(correctedLocal.calls[1].options.signal.aborted);
  await correctedLocal.respond(correctedLocal.calls[1], {items:[correctedLocal.item(101, 'STALE')]});
  await correctedLocal.advance(1300);
  assert.equal(correctedLocal.elements.q.value, 'hair tire');
  assert(!correctedLocal.markup().includes('STALE'));
  assert.equal(correctedLocal.calls.length, 2);

  for (const reply of [{items:[]}, {items:[], reason:'rate_limited'}]) {
    const t = setup(); t.ctx.state.deals = [deal(1, 'Steel kettle')]; await t.query('mouce'); await t.advance(600);
    await t.respond(t.calls[0], {items:[]}); assert.equal(t.elements.q.value, 'mouse');
    await t.advance(1099); assert.equal(t.calls.length, 1);
    await t.advance(1); assert.equal(t.calls[1].url, '/api/search?q=mouse&page=0');
    await t.respond(t.calls[1], reply);
    for (let i = 0; i < 5; i++) {await t.advance(5000); t.ctx.render();}
    assert.equal(t.calls.length, 2, 'at most one corrected request, including rate limiting');
    assert(!JSON.stringify(t.events).includes('mouce') && !JSON.stringify(t.events).includes('mouse'));
  }
  for (const data of [{items:[], reason:'budget_exhausted'}, {items:[{title:'Anal lubricant'}]}, {}]) {
    const t = setup(); t.ctx.state.deals = []; await t.query('mouce'); await t.advance(600);
    await t.respond(t.calls[0], data); assert.equal(t.elements.q.value, 'mouce', 'only successful raw empty pages correct');
    await t.query('x'); await t.query('mouce'); await t.advance(600);
    assert.equal(t.elements.q.value, 'mouce', 'cached failures and filtered items are not successful empty pages');
    assert.equal(t.calls.length, 1);
  }

  if (typeof s.ctx.analyzeSubmit === 'function') {
    const a = setup(); a.ctx.state.deals = [deal(1), deal(2, 'Hair tie organizer')];
    a.elements.q.value = 'mouse'; const analyzed = a.ctx.analyzeSubmit(); await turn(); await a.advance(900); await analyzed;
    assert(a.elements['search-more']); assert.equal(a.calls.length, 0);
    a.intersect(); await a.advance(0); assert.equal(a.calls[0].url, '/api/search?q=mouse&page=0');
    await a.respond(a.calls[0], {items:[a.item(101)]});
    await a.query('hair tire'); const corrected = a.ctx.analyzeSubmit(); await turn();
    assert.equal(a.calls[1].url, '/api/search?q=hair%20tire&page=0');
    await a.respond(a.calls[1], {items:[]}); await corrected;
    assert.equal(a.elements.q.value, 'hair tie'); assert.equal(a.ctx.analyzeTask, null);

    const adopt = setup(); await adopt.query('mouce'); await adopt.advance(600);
    await adopt.respond(adopt.calls[0], {items:[]}); await adopt.advance(1100);
    const request = adopt.calls[1], pending = adopt.ctx.analyzeSubmit(); await turn();
    assert.equal(adopt.calls.length, 2); assert(!request.options.signal.aborted);
    await adopt.respond(request, {items:[adopt.item(102)]}); await adopt.advance(900); await pending;
    assert.equal(adopt.ctx.analyzeTask.items[0].asin, asin(102));
    assert.equal(adopt.calls.length, 2, 'Analyze adopts the pending corrected request');
  }
  console.log('Search more logic passed: lazy pages 0..2, empty reasons, later cards, guards, dedupe, tagged button, spelling, List Price and Analyze');
}
module.exports = {setup, deal, turn};
if (require.main === module) main().catch(error => {console.error(error); process.exitCode = 1;});
