import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const code=fs.readFileSync(new URL('../functions/api/review-grade.js',import.meta.url),'utf8');
const {gradeReviews,validReviewSettings,reviewResponse}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
const cfg=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const product=()=>({stats:{current:Array(18).fill(-1),avg180:Array(18).fill(-1),atIntervalStart:Array(18).fill(-1)}});
const passed=(p,id,c=cfg)=>gradeReviews(p,c).checks.some(x=>x.id===id);
for(const [id,field,values,expected] of [
  ['plenty_of_reviews',17,[99,100,101],[false,true,true]],
  ['highly_rated',16,[42,43,44],[false,true,true]]]) {
  values.forEach((v,i)=>{const p=product();p.stats.current[field]=v;assert.equal(passed(p,id),expected[i]);});
}
for(const [v,expected] of [[42,false],[43,true],[45,true],[47,true],[48,false]]) {
  const p=product();p.stats.current[16]=45;p.stats.avg180[16]=v;assert.equal(passed(p,'steady_rating'),expected);
}
for(const [total,before,expected] of [[49,49,false],[50,38,true],[50,37,false],[100,75,true],[100,74,false],[100,76,true],[100,101,false],[100,null,false],[100,-1,false]]) {
  const p=product();p.stats.current[17]=total;p.stats.atIntervalStart[17]=before;assert.equal(passed(p,'natural_pace'),expected);
}
for(const [v,expected] of [[99,false],[100,true],[101,true],[null,false],[-1,false],[Infinity,false],[100.1,false]]) {
  const p=product();p.monthlySold=v;assert.equal(passed(p,'popular_now'),expected);
}
for(const missing of [undefined,null,{}, {stats:{}}, {stats:{current:null}}, {stats:{current:Array(18).fill(-1)}}, {stats:{current:Array(18).fill('100')}}])
  assert.deepEqual(gradeReviews(missing,cfg),{grade:'C',label:'Okay — read a few reviews before you buy',checks:[]});
const p=product();p.stats.current[16]=45;p.stats.avg180[16]=44;p.stats.current[17]=100;p.stats.atIntervalStart[17]=75;p.monthlySold=100;
assert.equal(gradeReviews(p,cfg).grade,'A');p.monthlySold=-1;assert.equal(gradeReviews(p,cfg).grade,'A');
p.stats.current[16]=42;assert.equal(gradeReviews(p,cfg).grade,'B');p.stats.current[17]=-1;assert.equal(gradeReviews(p,cfg).grade,'C');
for(let mask=0;mask<32;mask++) {
  const p=product();p.stats.current[17]=(mask&1)?100:50;p.stats.current[16]=(mask&2)?45:42;
  p.stats.avg180[16]=(mask&4)?p.stats.current[16]:-1;p.stats.atIntervalStart[17]=(mask&8)?p.stats.current[17]:-1;
  p.monthlySold=(mask&16)?100:-1;
  const r=gradeReviews(p,cfg),n=r.checks.length;
  assert.equal(r.grade,n>=4&&(mask&2)?'A':n>=2?'B':'C');assert(!/[0-9]/.test(JSON.stringify(r)));
  assert.deepEqual(Object.keys(r),['grade','label','checks']);
  for(const c of r.checks)assert.deepEqual(Object.keys(c),['id','title','result','detail']);
}
for(const key of Object.keys(cfg)) {
  for(const invalid of [null,'PRIVATE',NaN,Infinity,[],{}]) assert(!validReviewSettings({...cfg,[key]:invalid}),key);
}
assert(!validReviewSettings({...cfg,enabled:false}));
const avg=product();avg.stats.current[17]=100;avg.stats.avg30=Array(18).fill(100);
assert(!passed(avg,'natural_pace'),'weighted averages are not interval-start counts');
assert.equal(gradeReviews({...product(),monthlySold:100},{...cfg,grade_b_checks:1}).grade,'B');
assert.deepEqual(reviewResponse([],[],'B012345678',cfg),{items:[],reason:'no_product'});
assert.equal(reviewResponse([{asin:'B012345678',title:'Product'}],[],'B012345678',cfg).review.grade,'C');
// A34/C16 (Codex review 2026-10-23 #6): shipping facts stay with the price; nothing else is added.
const shown=(extra)=>reviewResponse([{asin:'B012345678',title:'Product'}],[{asin:'B012345678',title:'Product',image:'',
  price_cents:15384,as_of:'2026-10-22T01:00:00Z',list_price_cents:24000,monthlySold:100,category:'Kettles',...extra}],'B012345678',cfg).items[0];
assert.deepEqual(shown({shipping_cents:2515}),{asin:'B012345678',title:'Product',image:'',price_cents:15384,as_of:'2026-10-22T01:00:00Z',shipping_cents:2515});
assert.deepEqual(shown({price_includes_shipping:true}),{asin:'B012345678',title:'Product',image:'',price_cents:15384,as_of:'2026-10-22T01:00:00Z',price_includes_shipping:true});
for(const extra of [{},{shipping_cents:0},{shipping_cents:-1},{shipping_cents:'2515'},{price_includes_shipping:'true'}])
  assert.deepEqual(Object.keys(shown(extra)),['asin','title','image','price_cents','as_of'],JSON.stringify(extra));
const script=fs.readFileSync(process.argv[3],'utf8').split('(function () {')[0];
const context=vm.createContext({URL});vm.runInContext(script,context);
for(const input of ['B012345678','b012345678','https://www.amazon.com/dp/B012345678?tag=other','amazon.com/gp/product/B012345678','https://smile.amazon.com/name/dp/B012345678','https://amazon.com/?asin=B012345678'])assert.equal(context.reviewInput(input).asin,'B012345678');
for(const input of ['', 'tea','https://amazon.com.evil/dp/B012345678','https://amazon.com@evil.test/dp/B012345678','javascript:alert(1)','https://amazon.com:8443/dp/B012345678','https://amazon.com/dp/B012345678?asin=B099999999','https://amazon.com/?asin=B012345678&asin=B012345678'])assert.notEqual(context.reviewInput(input).kind,'amazon',input);
assert.equal(context.reviewInput('https://amzn.to/example').kind,'short');
for(const duration of [10000,17500,25000]) {
  assert.equal(context.reviewProgress(duration/2,duration,false),50);
  assert.equal(context.reviewProgress(duration,duration,false),99);
  assert.equal(context.reviewProgress(duration-1,duration,true),99);
  assert.equal(context.reviewProgress(duration,duration,true),100);
}
assert.equal(context.toolAmazon('B012345678'),'https://www.amazon.com/dp/B012345678?tag=wowpriced-20');
console.log('Review grading boundaries, 32 signal combinations, safe schema, parsing and clock passed.');
