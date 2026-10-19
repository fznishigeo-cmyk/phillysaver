// Exercise the emitted inline/deferred labels and unchanged row borrowing offline.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = process.argv[2], on = process.argv[3] === 'true';
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const asset = path.join(root, 'search-analyze.js');
const source = fs.existsSync(asset) ? fs.readFileSync(asset, 'utf8') : html;
function fragment(text, start, end) {
  const at = text.indexOf(start);
  assert(at >= 0, start);
  return text.slice(at, text.indexOf(end, at));
}
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
let query = 'kitchen & kettle';
const ctx = {URL, esc, searchAmazonQuery:() => query, amazonResultsSettings:{tag:'example-20'}};
ctx.host = ctx;
vm.createContext(ctx);
vm.runInContext(fragment(source, '  function analyzeInput(value)', '  function analyzeSearchText()') +
  fragment(source, '  function analyzeFallback(task)', '  function analyzeRender()'), ctx);
const asin = 'B0D6R8FDPY', url = 'https://amazon.com/dp/' + asin + '?private=PRIVATE';
const label = task => ctx.analyzeFallback(task).match(/noopener">(.*?)<\/a>/)[1];
for (const value of [asin, asin.toLowerCase(), url]) {
  query = value;
  const input = ctx.analyzeInput(value);
  if (on) assert.equal(input.asin, asin);
  const task = {asin, items:[], related:[]};
  assert.equal(label(task), on ? 'See this product on Amazon →' : 'See all &quot;' + asin + '&quot; on Amazon →');
  for (const local of [false, true]) {
    const title = 'Grace Form Strappy Sports Bra Extra Words';
    task.local = local ? {item:title} : null;
    task.items = local ? [] : [{title}];
    if (on) assert.equal(label(task), 'Analyze &quot;Grace Form Strappy Sports Bra&quot; on Amazon →');
  }
  if (on) {
    for (const title of [asin, url, '   ', '']) {
      task.local = {item:title}; task.items = [];
      assert.equal(label(task), 'See this product on Amazon →');
    }
    task.local = {item:'<Bra> & "Soft" Sports Top Extra'};
    assert.equal(label(task), 'Analyze &quot;&lt;Bra&gt; &amp; &quot;Soft&quot; Sports Top&quot; on Amazon →');
    task.local.item = 'A'.repeat(60);
    assert.equal(label(task), 'Analyze &quot;' + 'A'.repeat(40) + '&quot; on Amazon →');
    task.reason = 'budget_exhausted';
    assert.equal(label(task), "See today's Amazon price drops →");
  }
}
query = 'kitchen & kettle';
assert.equal(label({items:[]}), (on ? 'Analyze' : 'See all') + ' &quot;kitchen &amp; kettle&quot; on Amazon →');
if (on && fs.existsSync(asset)) {
  const loader = {document:{getElementById:() => ({value:asin})}};
  vm.createContext(loader);
  vm.runInContext('var analyzeUI;\n' + fragment(html, '  function analyzeInput(value)', '  function analyzeCurrent(task)'), loader);
  assert.notEqual(loader.analyzeInput(asin).kind, 'query');
  assert.equal(loader.analyzeSearchText(), '', 'ASIN is masked before module loading, including failure');
}

const parent = {};
function node(key) { return {key, style:{}, remove() {
  if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1);
}}; }
function grid(count) {
  const g = {parentNode:parent, children:[],
    querySelectorAll:() => g.children.filter(n => n.key),
    querySelector:() => g.children.find(n => n.className === 'gridfill'),
    appendChild(n) {n.remove(); n.parentNode = g; g.children.push(n);},
    get lastElementChild() {return g.children.at(-1);}};
  for (let i = 0; i < count; i++) g.appendChild(node('card-' + i));
  return g;
}
const grids = [grid(2), grid(5)];
const row = {scope:{querySelectorAll:() => grids}, document:{createElement:() => node()},
  getComputedStyle:() => ({gridTemplateColumns:'100px 100px 100px'})};
vm.createContext(row);
vm.runInContext(fragment(html, '  function columns(grid)', '  var pending = false;'), row);
row.fillRows(); row.fillRows();
assert.deepEqual(grids.map(g => g.querySelectorAll().length), [3, 4], 'borrowing stays intact');
assert.equal(!!grids[1].querySelector(), !on, 'filler is created only with card_tidy OFF');
if (!on) assert(grids[1].querySelector().innerHTML.includes('Looking for more?'));
assert.equal(html.includes('.gridfill{'), !on, 'unused filler styles are omitted');
console.log('card_tidy logic passed: safe product labels, loader masking, filler omission and row borrowing');
