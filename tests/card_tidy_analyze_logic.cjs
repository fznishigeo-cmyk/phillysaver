// Exercise actual inline/lazy Analyze with the publisher's real card payloads.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {setup, turn} = require('./search_more_logic.cjs');
const cards = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const tidy = process.argv[4] === 'true';
const card = (tier, key, extra = {}) => ({...cards[tier], key, ...extra});

(async () => {
  const s = setup();
  const lead = card('unverified', 'lead');
  s.ctx.state.deals = [lead];
  s.elements.q.value = 'headphones';
  s.emit('analyze-search', 'click');
  await turn(); await turn();
  assert(s.elements.content.innerHTML.includes(tidy ? 'Using our matching deal' : 'Using our verified deal'));
  await s.advance(900);
  assert(s.ctx.analyzeTask.finished);
  assert.equal(s.calls.length, 0);

  function show(local, related = []) {
    s.ctx.analyzeTask.local = local;
    s.ctx.analyzeTask.related = related;
    s.ctx.state.deals = [local, ...related];
    assert(s.ctx.analyzeRender());
    return s.elements.content.innerHTML;
  }
  for (const [tier, verified] of [['unverified', false], ['verified', true],
    ['history-checked', true], ['corroborated', false]]) {
    const html = show(card(tier, 'lead'));
    assert(html.includes('<h2>' + (!tidy || verified ? 'Your verified deal' : 'Your deal') + '</h2>'), tier);
  }
  for (const extra of [{badge:undefined}, {badge:'unknown'}, {price_unconfirmed:true},
    {price_aged:'8h'}, {click_price_unknown:true}, {headline:cards.unverified.headline}]) {
    assert(show(card('verified', 'lead', extra)).includes('<h2>' + (tidy ? 'Your deal' : 'Your verified deal') + '</h2>'));
  }
  const verified = card('verified', 'lead'), related = card('verified', 'related');
  assert(show(verified, [related]).includes('<h2>Related verified deals</h2>'));
  assert(show(lead, [related]).includes('<h2>Related verified deals</h2>'), 'related verification is independent of the lead');
  const unverified = card('unverified', 'unverified');
  assert(show(verified, [related, unverified]).includes('<h2>' + (tidy ? 'Related deals' : 'Related verified deals') + '</h2>'));
  assert(show(verified, [related, {...unverified, image_url:''}]).includes('<h2>Related verified deals</h2>'));
  const noPhotos = show(verified, [{...unverified, image_url:''}]);
  assert.equal(noPhotos.includes('analyze-related'), !tidy);
  assert(!show(verified).includes('analyze-related'));

  // A later publication replaces both cards; headings must follow the new tiers.
  show(verified, [related]);
  s.ctx.state.deals = [card('unverified', 'lead'), card('unverified', 'related')];
  s.ctx.analyzeRender();
  assert(s.elements.content.innerHTML.includes('<h2>' + (tidy ? 'Your deal' : 'Your verified deal') + '</h2>'));
  assert(s.elements.content.innerHTML.includes('<h2>' + (tidy ? 'Related deals' : 'Related verified deals') + '</h2>'));

  // Live API prices retain their separate heading; related inventory is still checked.
  s.elements.q.value = 'https://amazon.com/dp/B000000901';
  s.emit('analyze-search', 'click'); await turn(); await turn();
  await s.respond(s.calls[0], {items:[s.item(901, 'Wireless headphones')]});
  await s.advance(900);
  assert(s.elements.content.innerHTML.includes('<h2>Live Amazon price</h2>'));
  assert(s.elements.content.innerHTML.includes('<h2>' + (tidy ? 'Related deals' : 'Related verified deals') + '</h2>'));
  console.log('Analyze verification headings passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
