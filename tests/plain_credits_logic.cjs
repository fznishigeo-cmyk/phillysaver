// Reuse the offline browser model, exercising the emitted page's real render path.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const model = fs.readFileSync(path.join(__dirname, 'browse_logic.cjs'), 'utf8').split('(async()=>{')[0].split('module.exports =')[0];
const {harness, row, fragment, fillGridRows} = new Function('require', 'process', model +
  ';return {harness,row,fragment,fillGridRows};')(require, process);
const plain = fs.readFileSync(process.argv[2], 'utf8');
const linked = fs.readFileSync(process.argv[4], 'utf8');
for (const html of [plain, linked]) {
  for (const match of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
}
const rows = Array.from({length:240}, (_,i) => row('credit'+i, {
  status:i%3===0?'dead':'alive', gone:i%3===0, pct_at_checkout:90-i%40, final_price:1+i%30,
  category:i%4?'home':'electronics', store:i%5?'Amazon':'Walmart',
  allure:i%7===0, cl:i%8===0, image_url:i%5?'https://image.test/photo':'',
  first_seen_at:new Date(Date.parse('2026-10-13T18:00:00Z')-i*60000).toISOString(),
  credits:[{name:'Slickdeals',url:'https://slickdeals.net/f/1'},
    {name:'Source <unsafe> & "text"',url:'https://example.invalid/post'}, {name:'Amazon price drop',url:''}],
  url:'https://www.amazon.com/dp/B000000001?tag=example-20'
}));
const alive = values => Array.from(values).filter(d=>d.status!=='dead').map(d=>d.key);
function checkSpacing(values) {
  const slots=Array.from(values).flatMap((d,i)=>d.status==='dead'?[i]:[]);
  assert(slots.length>0,'gone cards remain available');
  assert(slots[0]>=9);
  assert(slots.every((n,i)=>!i || n-slots[i-1]>=10),'sliding window of ten');
}
for (const sort of ['top:-1','fresh:-1','pct:-1','price:1']) {
  for (const query of ['', 'Gadget']) {
    const a=harness({rows,template:plain}), b=harness({rows,template:linked});
    for (const h of [a,b]) {
      h.el('q').value=query;
      [h.ctx.state.sortKey,h.ctx.state.sortDir]=sort.split(':');
      h.ctx.state.sortDir=Number(h.ctx.state.sortDir);
      h.ctx.state.shown=300;
      h.ctx.render();
    }
    const visible=h=>h.keys().map(key=>rows.find(d=>d.key===key));
    checkSpacing(visible(a));
    assert.deepEqual(alive(visible(a)),alive(visible(b)),'live ordering unchanged');
    const all=a.keys();
    a.ctx.state.shown=60;a.ctx.render();
    assert.deepEqual(a.keys(),all.slice(0,60));
    a.ctx.state.shown=120;a.ctx.render();
    assert.deepEqual(a.keys(),all.slice(0,120));
    for (const columns of [1,2,3,4]) fillGridRows(a.el('content').innerHTML,columns);
  }
}
for (const html of [plain,linked]) {
  const h=harness({rows,template:html});
  Object.assign(h.ctx,{TIER_LABEL:{},auth:{checking:false},isMember:()=>false,unknownMembership:()=>false,
    newSinceVisit:()=>false,hotHtml:()=>'',savedButtonHtml:()=>'',isSaved:()=>false});
  vm.runInContext(fragment('  function storeHue(', '  var TICKER_MAX =',html),h.ctx);
  for (const d of [rows[0],rows[1],{...rows[1],spotlight:{source:'Woot',until:'2026-10-14T00:00:00Z'}}]) {
    const card=h.ctx.cardHtml(d), credit=card.match(/<span class="credit">([\s\S]*?)<\/span>/)[1];
    assert(credit.includes('via ') && credit.includes('Slickdeals') && credit.includes('Amazon price drop'));
    assert(credit.includes('Source &lt;unsafe&gt; &amp; &quot;text&quot;'));
    assert.equal(credit.includes('<a '),html===linked);
    const get=card.match(/class="getdeal" href="([^"]+)"/);
    assert.equal(get?.[1],d.status==='dead'?undefined:d.url);
  }
}
console.log('plain credits, escaping, Get deal, sliding windows, live order and pagination passed');
