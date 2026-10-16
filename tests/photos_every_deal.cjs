const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[2], 'utf8');
const start=html.indexOf('  function cmp('), end=html.indexOf('  function storeHue(',start);
let query='';
const ctx={state:{sortKey:'top',sortDir:-1,catTab:0,newKeys:{},seed:1,meta:{front_page:{top_picks:true}}},
  document:{getElementById:()=>({value:query})},CATGROUPS:[['Trending',null],['All deals',null],['Kitchen',['kitchen']]],
  dealKey:d=>d.key,isLocked:d=>!!d.locked,num:x=>x,sortVal:(d,k)=>d[k],JITTER_H:3,NO_PHOTO_H:12,
  eligibilityActive:()=>false,frontPageView:()=>ctx.state.catTab===0&&!query,frontPageMix:r=>r.slice().reverse()};
vm.createContext(ctx);vm.runInContext(html.slice(start,end),ctx);
const rows=[
 {key:'n1',pick:1,first_seen_at:'2026-10-15',ps:1},
 {key:'p1',pick:2,image_url:'photo',first_seen_at:'2026-10-01',ps:2},
 {key:'n2',first_seen_at:'2026-10-15',ps:3},
 {key:'p2',image_url:'photo',first_seen_at:'2026-10-01',ps:4},
 {key:'u',image_url:'photo',price_unconfirmed:true,pick:0,ps:0},
 {key:'l',image_url:'photo',locked:true}];
for(const tab of [0,1,2]) for(const sort of ['top','fresh','final_price']) {
 ctx.state.catTab=tab;ctx.state.sortKey=sort;
 for(const defer of [false,true]) {
  const result=ctx.ordered(rows,defer);
  assert.equal(result.at(-1).key,'u');
  const open=result.filter(d=>!d.locked&&!d.price_unconfirmed);
  assert(open.slice(0,2).every(d=>d.image_url));assert(open.slice(2).every(d=>!d.image_url));
  assert.equal(result.at(-2).key,'l');
 }
 query='kettle';
 const original=ctx.photosFirst;ctx.photosFirst=r=>r;
 const baseline=ctx.ordered(rows).map(d=>d.key).join();ctx.photosFirst=original;
 assert.equal(ctx.ordered(rows).map(d=>d.key).join(),baseline,'search order unchanged');
 query='';
}
