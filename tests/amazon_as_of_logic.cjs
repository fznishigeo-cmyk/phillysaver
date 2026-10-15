// Execute emitted cards and both existing clocks without a browser or requests.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const root = process.argv[2], html = fs.readFileSync(root + '/index.html', 'utf8');
const deals = JSON.parse(fs.readFileSync(root + '/deals.json', 'utf8')).deals;
const deal = deals.find(d => d.amazon_as_of), now = Date.parse('2026-10-15T20:15:00Z');
function fragment(start, end) {
  const a = html.indexOf(start), b = html.indexOf(end, a);
  assert(a >= 0 && b > a, start);
  return html.slice(a, b);
}
class Clock extends Date { static now() { return now; } }
const hidden = {textContent:' (Price as of ' + deal.amazon_as_of + ') '};
const age = {nodeType:3, nodeValue:'2h ago'};
const label = {nodeType:1};
const stamp = {firstChild:label, lastChild:age, children:[label, hidden, age],
  title:'Price as of ' + deal.amazon_as_of, getAttribute:() => deal.checked_at};
const ctx = {Date:Clock, state:{newKeys:{}, spotlight:[]}, TIER_LABEL:{},
  document:{querySelectorAll:() => [stamp]}, isLocked:d=>!!d.locked,
  lockedHtml:()=>'<article class="card locked">locked</article>', num:v=>v == null ? null : Number(v),
  dealKey:d=>d.key, mediaHtml:()=>'', isNew:()=>false, lit:()=>false, isFreePick:()=>false,
  traceSvg:()=>'', newSinceVisit:()=>false, savedButtonHtml:()=>'', savedWatchHtml:()=>'', hotHtml:()=>''};
vm.createContext(ctx);
vm.runInContext(fragment('  function esc(s)', '  function sortVal'), ctx);
vm.runInContext(fragment('  function ageText(h)', '  function usedTag(d)'), ctx);
vm.runInContext(fragment('  function cardHtml(d)', '  // ---- the deal ticker ----'), ctx);
vm.runInContext(fragment('  function spotlightTime(endsAt, now)', '  function frontPageView()'), ctx);
const card = ctx.cardHtml(deal);
assert(card.includes('title="Price as of Oct 15, 2:15 PM EDT"'));
assert(card.includes('<span class="sr-only"> (Price as of Oct 15, 2:15 PM EDT) </span>2h ago'));
assert(!ctx.cardHtml({...deal, checked_at:undefined}).includes('Price as of'));
assert(!ctx.cardHtml({...deal, locked:true}).includes('Price as of'));
assert(!ctx.cardHtml(deals.find(d => d.store === 'Walmart')).includes('Price as of'));
assert(ctx.cardHtml({...deal, amazon_as_of:'<img src=x>"'}).includes('&lt;img src=x&gt;&quot;'));
ctx.paintSpotlightTime(now + 3600000);
assert.equal(age.nodeValue, '3h ago');
assert.equal(stamp.title, 'Price as of Oct 15, 2:15 PM EDT');
assert.equal(hidden.textContent, ' (Price as of Oct 15, 2:15 PM EDT) ');
const scope = {querySelector:s => s === '[data-checked-at]' ? stamp : null,
  querySelectorAll:s => s === '[data-checked-at]' ? [stamp] : [], addEventListener(){}};
let update;
const listing = {Date:Clock, location:{pathname:'/stores/amazon/'}, window:{}, navigator:{},
  document:{getElementById:()=>null, querySelector:()=>scope}, setInterval:fn=>{update=fn;}};
vm.createContext(listing);
const actions = Array.from(html.matchAll(/<script>([\s\S]*?)<\/script>/g), m=>m[1])
  .find(s=>s.includes('Static deals have explicit labels'));
assert(actions);
vm.runInContext(actions, listing);
class Later extends Date { static now() { return now + 7200000; } }
listing.Date = Later;
update();
assert.equal(age.nodeValue, '4h ago');
assert.equal(stamp.children[1], hidden);
assert.equal(hidden.textContent, ' (Price as of Oct 15, 2:15 PM EDT) ');
