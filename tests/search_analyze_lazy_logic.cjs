// Real emitted module and loader, with imports held across user interactions.
const assert = require('node:assert/strict');
const {setup, deal, turn} = require('./search_more_logic.cjs');

function held() {
  const s = setup();
  s.ctx.state.deals = [deal(1, 'Wireless mouse'), deal(2, 'Travel steel kettle')]
    .map(d => Object.assign(d, {badge:'verified', image_url:'https://m.media-amazon.com/images/I/x.jpg'}));  // related cards need a photo (card_tidy)
  let resolve, reject;
  s.ctx.loadSearchAnalyze = () => {
    s.analyzeModules.push('search-analyze.js');
    return new Promise((yes, no) => {resolve = yes; reject = no;});
  };
  s.release = async () => {resolve({default:s.ctx.searchAnalyzeModule}); await turn(); await turn();};
  s.fail = async () => {reject(new Error('offline')); await turn(); await turn();};
  return s;
}

(async () => {
  const s = held();
  s.ctx.render(); await s.advance(10000);
  assert.deepEqual(s.analyzeModules, []);
  assert.deepEqual(s.calls, [], 'no Analyze request or module before interaction');
  s.emit('q', 'focus');
  s.emit('q', 'paste');
  s.elements.q.value = 'https://amazon.com/dp/B000000901?email=PRIVATE';
  s.emit('q', 'input');
  await s.advance(150);
  s.emit('analyze-search', 'click'); s.emit('analyze-search', 'click');
  await s.advance(1000);
  assert.equal(s.analyzeModules.length, 1);
  assert.equal(s.calls.length, 0);
  assert.equal(s.ctx.searchText(), '', 'links stay out of browse snapshots before import');
  await s.release();
  assert.deepEqual(s.calls.map(c => c.url), ['/api/search?asin=B000000901']);
  s.emit('analyze-search', 'click'); await s.advance(150);
  assert.equal(s.calls.length, 1, 'paste/input/click replay only one lookup');
  await s.respond(s.calls[0], {items:[s.item(901, 'Steel kitchen kettle', {list_price_cents:1999})]});
  await s.advance(900);
  assert(s.ctx.analyzeTask.finished);
  assert(s.elements.content.innerHTML.includes('Related verified deals'));
  assert(s.elements['amazon-results'].innerHTML.includes(s.ctx.searchMoreCall ? 'List Price' : '$12.99'));
  assert(!JSON.stringify(s.events).includes('PRIVATE'));
  assert(!JSON.stringify(s.events).includes('B000000901'));

  const adopt = held(); await adopt.query('insulated flask'); await adopt.advance(600);
  assert.equal(adopt.calls.length, 1);
  const request = adopt.calls[0];
  adopt.emit('analyze-search', 'click'); await adopt.release();
  assert.equal(adopt.calls.length, 1, 'the delayed module adopts the already-running text request');
  assert(!request.options.signal.aborted);
  await adopt.respond(request, {items:[adopt.item(904, 'Insulated flask')]}); await adopt.advance(900);
  assert.equal(adopt.ctx.analyzeTask.items[0].asin, 'B000000904');
  assert.equal(adopt.calls.length, 1);

  for (const event of ['focus', 'paste', 'input']) {
    const passive = held(); passive.emit('q', event); await passive.release();
    assert.equal(passive.analyzeModules.length, 1);
    assert.equal(passive.calls.length, 0, event + ' without a submitted value only loads code');
  }
  const clicked = held(); clicked.elements.q.value = 'mouse'; clicked.emit('analyze-search', 'click');
  await clicked.release(); await clicked.advance(900);
  assert.equal(clicked.ctx.analyzeTask.local.key, '1');
  assert.equal(clicked.calls.length, 0, 'a click alone replays against local inventory');

  const changed = held(); changed.elements.q.value = 'https://amazon.com/dp/B000000902';
  changed.emit('analyze-search', 'click'); await changed.query('mouse'); await changed.release();
  assert.equal(changed.ctx.analyzeTask, null);
  assert.equal(changed.calls.length, 0, 'editing while loading cancels the old intent');
  assert(changed.elements.content.innerHTML.includes('Wireless mouse'));
  const cleared = held(); cleared.elements.q.value = 'https://amazon.com/dp/B000000902';
  cleared.emit('analyze-search', 'click'); cleared.ctx.clearSearch(); await cleared.release();
  assert.equal(cleared.ctx.analyzeTask, null); assert.equal(cleared.calls.length, 0);

  const failed = held(); failed.elements.q.value = 'https://amazon.com/dp/B000000903?private=PRIVATE';
  failed.emit('analyze-search', 'click'); await failed.fail(); await failed.advance(1000);
  assert.equal(failed.ctx.searchText(), '');
  assert.equal(failed.calls.length, 0);
  const fallback = failed.elements['search-amazon-result'];
  assert(!fallback.hidden);
  assert(fallback.innerHTML.includes('https://www.amazon.com/?tag=example-20'));
  assert(fallback.innerHTML.includes('rel="sponsored nofollow noopener"'));
  assert(fallback.innerHTML.includes('Search Amazon</a>'));
  assert(!fallback.innerHTML.includes('PRIVATE'));
  await failed.query('mouse'); failed.emit('analyze-search', 'click'); await failed.advance(1000);
  assert(failed.elements.content.innerHTML.includes('Wireless mouse'), 'plain search survives import failure');
  assert(failed.elements['search-amazon-result'].innerHTML.includes('data-search-amazon'));
  assert.equal(failed.analyzeModules.length, 1, 'failure does not create an import loop');
  assert.equal(failed.calls.length, 0);

  // The lazy callback is still the function captured by the other module.
  const canceled = held(); await canceled.query('mouse');
  canceled.emit('analyze-search', 'click'); canceled.ctx.analyzeCancel(); await canceled.release();
  assert.equal(canceled.ctx.analyzeTask, null);
  console.log('Lazy Analyze logic passed: idle, replay, cancellation, privacy and failure fallback');
})().catch(error => {console.error(error); process.exitCode = 1;});
