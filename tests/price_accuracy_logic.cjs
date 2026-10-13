// Execute the emitted card renderer and relative clock, with no network.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const root = process.argv[2];
const html = fs.readFileSync(root + '/index.html', 'utf8');
const deal = JSON.parse(fs.readFileSync(root + '/deals.json', 'utf8')).deals[0];
const now = Date.parse('2026-10-13T22:00:00Z');
function fragment(start, end) {
  const a = html.indexOf(start), b = html.indexOf(end, a);
  assert(a >= 0 && b > a, start);
  return html.slice(a, b);
}
class Clock extends Date { static now() { return now; } }
const stamp = {textContent:'price checked 25m ago', getAttribute:() => deal.checked_at};
const ctx = {Date:Clock, state:{newKeys:{}, spotlight:[]}, TIER_LABEL:{},
  document:{querySelectorAll:selector => selector.includes('price-checked') ? [stamp] : []},
  isLocked:d=>!!d.locked, lockedHtml:()=>'<article class="card locked">locked</article>',
  num:v=>v == null ? null : Number(v), dealKey:d=>d.key, mediaHtml:()=>'',
  isNew:()=>false, lit:()=>false, isFreePick:()=>false, traceSvg:()=>'',
  newSinceVisit:()=>false, savedButtonHtml:()=>'', savedWatchHtml:()=>'', hotHtml:()=>''};
vm.createContext(ctx);
vm.runInContext(fragment('  function esc(s)', '  function sortVal'), ctx);
vm.runInContext(fragment('  function ageText(h)', '  function usedTag(d)'), ctx);
vm.runInContext(fragment('  function cardHtml(d)', '  // ---- the deal ticker ----'), ctx);
vm.runInContext(fragment('  function spotlightTime(endsAt, now)', '  function frontPageView()'), ctx);
assert.equal(ctx.priceCheckedText(deal.checked_at, now), 'price checked 25m ago');
for (const [minutes, label] of [[0,'0m'],[59,'59m'],[60,'1h'],[1440,'1d']]) {
  assert.equal(ctx.priceCheckedText(new Date(now - minutes * 60000).toISOString(), now), 'price checked ' + label + ' ago');
}
for (const invalid of ['garbage', '<img src=x>', '2026-10-13T23:00:00Z'])
  assert.equal(ctx.priceCheckedText(invalid, now), '');
const card = ctx.cardHtml({...deal, age_hours:9});
assert.match(card, /class="chip storechip">[^<]*<\/span><span class="chip price-checked" data-checked-at="[^"]+"><span class="pcl">price checked <\/span>25m ago<\/span>/);
assert(!card.includes('9h ago'));
const old = ctx.cardHtml({...deal, checked_at:undefined, age_hours:9});
assert(old.includes('9h ago') && !old.includes('price checked'));
assert.equal((card.match(/class="chip/g) || []).length, (old.match(/class="chip/g) || []).length);
assert(!ctx.cardHtml({...deal, locked:true}).includes('price checked'));
ctx.paintSpotlightTime(now + 60000);
assert.equal(stamp.textContent, 'price checked 26m ago');
// The shared page script also advances static listing and related-card ages.
const listingStamp = {textContent:'', getAttribute:() => deal.checked_at};
let update;
const scope = {querySelector:selector => selector === '[data-checked-at]' ? listingStamp : null,
  querySelectorAll:selector => selector === '[data-checked-at]' ? [listingStamp] : [], addEventListener(){}};
const listing = {Date:Clock, location:{pathname:'/stores/amazon/'}, window:{}, navigator:{},
  document:{getElementById:()=>null, querySelector:()=>scope}, setInterval:fn=>{update=fn;}};
vm.createContext(listing);
const actions = Array.from(html.matchAll(/<script>([\s\S]*?)<\/script>/g), m=>m[1])
  .find(s=>s.includes('Static deals have explicit labels'));
assert(actions);
vm.runInContext(actions, listing);
assert.equal(listingStamp.textContent, 'price checked 25m ago');
class Later extends Date { static now() { return now + 3600000; } }
listing.Date = Later;
update();
assert.equal(listingStamp.textContent, 'price checked 1h ago');
console.log('Price timestamps replace the age chip and advance with the client clock.');
