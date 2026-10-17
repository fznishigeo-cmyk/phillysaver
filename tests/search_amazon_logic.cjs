const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const elements = {q:{value:''}, clearsearch:{}, searchcount:{}, 'search-amazon-result':{}};
const handlers = {}, events = [], opened = [], automaticOutbounds = [];
const ctx = {document:{getElementById:id=>elements[id], addEventListener:(type,fn)=>handlers[type]=fn},
  window:{wpTrack:(...args)=>events.push(args), open:(...args)=>opened.push(args)},
  searchText:()=>elements.q.value.trim(), plural:(n,s)=>n+' '+s+(n===1?'':'s'),
  esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))};
vm.createContext(ctx);
const trackStart=html.lastIndexOf('  function track(name, params)',html.indexOf('  function sortVal('));
vm.runInContext(html.slice(trackStart,html.indexOf('\n  }',trackStart)+4),ctx);
vm.runInContext(html.slice(html.indexOf('  function searchAmazon'), html.indexOf('  function renderTabs(')),ctx);
const search = q=>{elements.q.value=q;return ctx.searchAmazonHtml(true);};
const url = q=>'https://www.amazon.com/s?k='+encodeURIComponent(q)+'&tag=example-20';
function click(q, expected=q, type='click', button=0) {
  const markup=search(q), target={href:markup.match(/ href="([^"]+)"/)[1]};
  target.closest=()=>target;
  let prevented=false;
  const event={type,button,target,preventDefault:()=>prevented=true};
  // GA4 Enhanced Measurement can read the anchor in either event phase.
  automaticOutbounds.push(target.href);
  handlers[type](event);
  automaticOutbounds.push(target.href);
  assert.equal(prevented,true);
  assert.deepEqual(opened.at(-1),[url(expected),'_blank','noopener']);
  assert.deepEqual(automaticOutbounds.slice(-2),['https://www.amazon.com/','https://www.amazon.com/']);
}
assert(search('cupping').includes(' href="https://www.amazon.com/"'));
assert(!search('cupping').includes('k=cupping'));
assert.deepEqual(opened,[],'rendering must not open Amazon');
click('cupping');
click('  café   & "tea"  ','café & "tea"');
click('🫖'.repeat(85),'🫖'.repeat(80));
click('kettle '.repeat(20),'kettle '.repeat(20).trim().slice(0,80));
click('抹茶 茶碗');
click('model ١٢٣٤٥');
assert(search('<b>kettle</b>').includes('&lt;b&gt;kettle&lt;/b&gt;'));
for (const q of ['', '  ', 'https://example.com/private', '//example.com', 'example.com/path',
  'www.example.com', 'name@example.test', 'order 123456', '1234567890', 'ftp://example.test',
  '127.0.0.1/account', 'go to https://example.test/private',
  '例子.测试/private', '例子。测试/private', 'shop.公司', 'order ١٢٣٤٥٦', 'order １２３４５６',
  'order १२३४५६', 'order 𝟏𝟐𝟑𝟒𝟓𝟔', 'order 12٣٤٥٦',
  'kettle '+ 'x'.repeat(90)+' ١٢٣٤٥٦', 'kettle '+ 'x'.repeat(90)+' 例子.测试/private',
  'kettle '+ 'x'.repeat(90)+' 123456', 'kettle '+ 'x'.repeat(90)+' a@example.test', '\ud800']) {
  assert.equal(search(q),'',q);
}
assert(search('model 12345'));
ctx.searchRecoveryOptions=()=>[{kind:'clear',value:'query',label:'Clear search'}];
vm.runInContext(html.slice(html.indexOf('  function searchRecoveryHtml('),html.indexOf('  function recoverSearch(')),ctx);
search('cupping');
const recovery=ctx.searchRecoveryHtml();
assert(recovery.includes('class="getdeal"'));
assert(recovery.indexOf('Search Amazon for')<recovery.indexOf('data-search-kind'));
assert(recovery.includes('Amazon links may earn us a commission.'));
ctx.wpAndroidLimited=()=>true;assert.equal(search('cupping'),'');
ctx.wpAndroidLimited=()=>false;
search('kettle');ctx.renderSearchCount([{status:'alive'}]);
assert(elements['search-amazon-result'].innerHTML.startsWith("Not what you're looking for? "));
assert.equal(elements['search-amazon-result'].hidden,false);
ctx.renderSearchCount([]);assert.equal(elements['search-amazon-result'].hidden,true);
assert.equal(elements['search-amazon-result'].innerHTML,'');
search('');ctx.renderSearchCount([{status:'alive'}]);
assert.equal(elements['search-amazon-result'].hidden,true);
events.length=0;opened.length=0;
click('cupping');
click('cupping','cupping','auxclick',1);
const target = {closest:()=>({href:'https://www.amazon.com/'})};
handlers.auxclick({type:'auxclick',button:2,target});
assert.equal(opened.length,2);
assert.deepEqual(events,[['search_amazon',undefined],['search_amazon',undefined]]);
assert(!JSON.stringify(events).includes('cupping'));
// A stale link after editing the input cannot open a newly private query.
search('order ١٢٣٤٥٦');
handlers.click({type:'click',button:0,target,preventDefault(){}});
assert.equal(opened.length,2);assert.equal(events.length,2);
if (html.includes('  function setSavedView(on)')) {
  Object.assign(ctx,{loadedOnce:true,moreObserver:null,paintTicker(){},savedOn:()=>true,
    savedDeals:{view:false,entries:[]},state:{meta:{}},renderSavedControls(){},savedWatchEditor(){},
    savedViewHtml:()=>'<h2>Saved deals</h2>',restoreSavedWatchEditor(){},panel:{}});
  ctx.window.wpSupport={refresh(){}};
  elements.content={appendChild(){}};
  vm.runInContext(html.slice(html.indexOf('  function setSavedView(on)'),html.indexOf('  function savedUnavailableHtml(')),ctx);
  vm.runInContext(html.slice(html.indexOf('  function render()'),html.indexOf('  function showMore()')),ctx);
  search('kettle');ctx.renderSearchCount([{status:'alive'}]);
  assert.equal(elements['search-amazon-result'].hidden,false);
  ctx.setSavedView(true);
  assert.equal(elements.q.value,'');
  assert.equal(elements['search-amazon-result'].hidden,true);
  assert.equal(elements['search-amazon-result'].innerHTML,'');
  assert.equal(elements.searchcount.textContent,'');
  assert.equal(elements.clearsearch.hidden,true);
  assert.equal(elements.content.innerHTML,'<h2>Saved deals</h2>');
}
console.log('Amazon search logic passed');
