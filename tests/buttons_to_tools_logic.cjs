const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(process.argv[2],'utf8');
const handlers={},tracks=[],opened=[];
const input={value:'',form:{addEventListener:(event,fn)=>handlers['form:'+event]=fn},
  addEventListener:(event,fn)=>handlers[event]=fn};
const links=['deal-checker','discount-finder','discount-finder'].map(tool=>({dataset:{toolLink:tool},
  addEventListener(event,fn){this[event]=fn;}}));
const ctx={document:{getElementById:()=>input,querySelectorAll:()=>links},URL,
  track:(...args)=>tracks.push(args),window:{open:(...args)=>opened.push(args)},
  searchText:()=>input.value.trim(),analyzeTask:null,analyzeUI:null};
vm.createContext(ctx);
const start=html.indexOf('  function searchToolHref(');
assert(start>=0);
vm.runInContext(html.slice(start,html.indexOf('  function searchAmazonClick(',start)),ctx);
for(const name of ['analyzeInput','analyzeSearchText','searchAmazonQuery','discountsLink','searchAmazonClick']) {
  const i=html.indexOf('  function '+name+'(');
  if(i>=0) vm.runInContext(html.slice(i,html.indexOf('\n  }',i)+4),ctx);
}
for(const raw of ['wireless earbuds','https://www.amazon.com/dp/B012345678?tag=PRIVATE',' café & tea ','','   ']) {
  input.value=raw;handlers.input();
  for(const link of links) {
    const expected='/amazon-'+link.dataset.toolLink+'/'+(raw.trim()?'?q='+encodeURIComponent(raw):'');
    assert.equal(link.href,expected);
    link.click();assert.equal(link.href,expected);
    assert.equal(tracks.at(-1)[0],link.dataset.toolLink==='deal-checker'?'search_analyze':'search_discounts');
    assert.equal(tracks.at(-1)[1].to_tool,true);
    const target={closest:s=>s==='[data-tool-link]'?target:null};
    ctx.searchAmazonClick({target,type:'click',button:0,preventDefault(){throw Error('must navigate natively');}});
  }
}
assert.equal(opened.length,0,'tool buttons must never open Amazon or run inline analysis');
input.value='headphones';
const target={closest:s=>s.includes('[data-search-discounts]')?target:null};
ctx.searchAmazonClick({target,type:'click',button:0,preventDefault(){}});
assert.equal(new URL(opened.at(-1)[0]).searchParams.get('rh'),'p_n_deal_type:23566065011','342 remains discounts');
console.log('Native tool hrefs, raw words/links/empty, tracking, untouched 342 passed');
