const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const html = fs.readFileSync(process.argv[2] + '/index.html', 'utf8');
const start = html.indexOf('  function clickPriceHomeRows(rows)'), end = html.indexOf('  function nicheLast(', start);
assert(start >= 0 && end > start);
let query = '';
const ctx = {state:{catTab:0}, CATGROUPS:[['Trending',null],['All deals',null],['Used','USED'],['Fashion',['fashion']]],
  document:{getElementById:()=>({value:query})}};
vm.createContext(ctx);
vm.runInContext(html.slice(start,end),ctx);
const unknown = {key:'adidas',click_price_unknown:true,price_unconfirmed:true};
for (const n of [0,1,60,119,120,121,300]) {
  const known = Array.from({length:n},(_,i)=>({key:String(i)}));
  const rows = ctx.clickPriceHomeRows([unknown,...known]);
  assert.equal(rows.findIndex(d=>d.key==='adidas'), n < 120 ? -1 : n);
}
query = 'adidas';
assert.equal(ctx.clickPriceHomeRows([unknown]).length,1);
query = ''; ctx.state.catTab = 1;
assert.equal(ctx.clickPriceHomeRows([unknown]).length,0);
query = ''; ctx.state.catTab = 3;
assert.equal(ctx.clickPriceHomeRows([unknown]).length,1);
assert(html.includes('rows = clickPriceHomeRows(rows);'));
assert(html.includes('clickPriceHomeRows(confirmedFirst('));
assert(html.includes('if (d.select_sizes) metarow'));
Object.assign(ctx, {isLocked:()=>false,mediaHtml:()=>'',newSinceVisit:()=>false,savedButtonHtml:()=>'',
  priceCheckedText:()=>'',TIER_LABEL:{},hotHtml:()=>'',ageText:()=>'',num:Number,
  isNew:()=>false,lit:()=>false,dealKey:d=>d.key,isFreePick:()=>false,traceSvg:()=>'',
  checkoutPriceHtml:()=>'',savedWatchHtml:()=>'',esc:s=>String(s || '')});
const cardStart = html.indexOf('  function cardHtml(d)'), cardEnd = html.indexOf('  var TICKER_MAX',cardStart);
assert(cardStart >= 0 && cardEnd > cardStart);
vm.runInContext(html.slice(cardStart,cardEnd),ctx);
assert(ctx.cardHtml({key:'child',select_sizes:true,final_price:4.60}).includes('<span class="chip">select sizes</span>'));
assert(!ctx.cardHtml({key:'plain',final_price:4.60}).includes('select sizes'));
assert(ctx.cardHtml(unknown).includes('Price not confirmed'));
console.log('Click-price home ordering, sparse supply, search/category and size chip passed.');
