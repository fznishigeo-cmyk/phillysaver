const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8'), adult = process.argv[3] === 'true';
const source = name => {
  const match = new RegExp('  function ' + name + '\\([^\\n]*\\) \\{\\n[\\s\\S]*?\\n  \\}').exec(html);
  assert(match, name); return match[0];
};
let input = '', opened = [], tracked = [];
const ctx = {URL, searchText:() => input.trim(),
  document:{getElementById:id => {assert.equal(id, 'q'); return {value:input};}},
  window:{open:(...args) => opened.push(args)}, track:(...args) => tracked.push(args),
  fetch:() => {throw Error('discounts must never fetch');},
  XMLHttpRequest:function () {throw Error('discounts must never fetch');}};
vm.createContext(ctx);
for (const name of ['searchAmazonQuery', 'discountsLink', 'searchAmazonClick']) vm.runInContext(source(name), ctx);
const generic = 'https://www.amazon.com/deals?tag=example-20';
const product = asin => 'https://www.amazon.com/dp/' + asin + '?tag=example-20';
const words = text => 'https://www.amazon.com/s?k=' + encodeURIComponent(text) + '&rh=p_n_deal_type%3A23566065011&tag=example-20';
const asin = 'B012345678';
const link = 'https://www.amazon.com/Nulaxy-Adjustable-Anti-Slip-Smartphone-Essentials/dp/' + asin + '?tag=PRIVATE#PRIVATE';
const title = 'Nulaxy Adjustable Anti Slip Smartphone Essentials';
function click(raw, expected, discounts = true) {
  input = raw;
  for (const [type, button] of [['click', 0], ['auxclick', 1]]) {
    let prevented = false;
    const before = opened.length;
    const target = {href:'https://www.amazon.com/', closest:selector =>
      selector === '.all-discounts' ? discounts : selector === '[data-search-amazon]' ? !discounts : false};
    ctx.searchAmazonClick({type, button, target, preventDefault(){prevented = true;}});
    if (expected === null) {assert.equal(opened.length, before); continue;}
    assert(prevented); assert.equal(opened.length, before + 1);
    assert.deepEqual(opened.at(-1), [expected, '_blank', 'noopener']);
    assert.deepEqual(tracked.at(-1), [discounts ? 'search_discounts' : 'search_amazon']);
    assert.equal(target.href, 'https://www.amazon.com/', 'no private URL in the analytics href');
  }
}
click(link, words(title));
click('https://amazon.com/Caf%C3%A9%20Tea_Matcha-%E8%8C%B6%E7%A2%97/gp/product/' + asin, words('Café Tea Matcha 茶碗'));
click('amazon.com/' + asin + '-Nulaxy-ref/dp/' + asin + '/ref=PRIVATE', words('Nulaxy'));
click('https://amazon.com/one-two-three-four-five-six-seven-eight-nine/dp/' + asin, words('one two three four five six seven eight'));
click('https://amazon.com/' + 'a'.repeat(110) + '/dp/' + asin, words('a'.repeat(80)));
for (const path of ['/dp/', '/gp/product/']) {
  click('https://amazon.com' + path + asin, product(asin));
  ctx.analyzeTask = {asin, finished:true, items:[{asin, title}]};
  click('https://amazon.com' + path + asin, words(title));
  ctx.analyzeTask = {asin, finished:true, local:{item:title}, items:[]};
  click('https://amazon.com' + path + asin, words(title));
  for (const task of [null, {asin, finished:false, items:[{asin, title}]},
    {asin, finished:true, reason:'excluded', items:[{asin, title}]},
    {asin:'B000000001', finished:true, local:{item:title}, items:[{asin:'B000000001', title}]}]) {
    ctx.analyzeTask = task;
    click('https://amazon.com' + path + asin, product(asin));
  }
}
ctx.analyzeTask = {finished:true, items:[{asin, title}]};
click('https://amazon.com/dp/' + asin, words(title));
ctx.analyzeTask = null;
for (const raw of ['https://a.co/123', 'https://amzn.to/123', 'https://example.com/Name/dp/' + asin,
  'https://amazon.com.evil.test/Name/dp/' + asin, 'https://evilamazon.com/Name/dp/' + asin,
  'https://amazon.com@evil.test/Name/dp/' + asin, 'https://user@amazon.com/Name/dp/' + asin,
  'https://amazon.com:123/Name/dp/' + asin, 'https://amazon.com\\@evil.test/Name/dp/' + asin,
  'ftp://amazon.com/Name/dp/' + asin, 'https://amazon.com/%ZZ/dp/' + asin]) click(raw, generic);
click('https://amazon.com/adult-toys/dp/' + asin, adult ? generic : words('adult toys'));
click('https://amazon.com/one-two-three-four-five-six-seven-eight-vibrator/dp/' + asin,
  adult ? generic : words('one two three four five six seven eight'));
ctx.analyzeTask = {asin, finished:true, items:[{asin, title:'adult toys'}]};
click('https://amazon.com/dp/' + asin, adult ? generic : words('adult toys'));
ctx.analyzeTask = null;
for (const raw of ['name%40example.com', 'order-123456', 'https%3A%2F%2Fexample.com'])
  click('https://amazon.com/' + raw + '/dp/' + asin, generic);
click('https://amazon.com/one-two-three-four-five-six-seven-eight-name%40example.com/dp/' + asin, generic);
ctx.analyzeTask = {asin, finished:true, items:[{asin, title:'Nulaxy Anti-Slip Smartphone_Stand'}]};
click('https://amazon.com/dp/' + asin, words('Nulaxy Anti Slip Smartphone Stand'));
ctx.analyzeTask = null;
for (const raw of ['mouse pad', ' café  & "tea" ', '抹茶 茶碗', '🫖'.repeat(85), 'adult coloring book']) {
  const q = Array.from(raw.trim().replace(/\s+/g, ' ')).slice(0, 80).join('');
  click(raw, words(q));
  click(raw, 'https://www.amazon.com/s?k=' + encodeURIComponent(q) + '&tag=example-20', false);
}
click(link, null, false);
ctx.wpAndroidLimited = () => true;
click(link, generic); click('https://amazon.com/dp/' + asin, generic);
assert(!JSON.stringify(tracked).includes('PRIVATE'));
for (const script of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g))
  if (!script[0].includes('application/ld+json')) new vm.Script(script[1]);
console.log('Discount link logic passed: slug, title/ASIN, short/adult/private fallback, unchanged typed/other exits, no fetch');
