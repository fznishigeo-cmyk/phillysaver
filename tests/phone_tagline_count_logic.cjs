// Execute the published builder, including its original rounding and plural rules.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const home = fs.readFileSync(process.argv[2], 'utf8');
const enabled = home.includes('var tagShort =');
function fragment(start, end) {
  const a = home.indexOf(start), b = home.indexOf(end, a);
  assert(a >= 0 && b > a, start);
  return home.slice(a, b);
}
function node() {
  return {own:'', children:[], attributes:{},
    set textContent(value) {this.own = String(value); this.children = [];},
    get textContent() {return this.own + this.children.map(n => n.textContent).join('');},
    appendChild(child) {this.children.push(child);},
    setAttribute(key, value) {this.attributes[key] = value;}};
}
const elements = {tagline:node(), q:node()};
const now = 1000000000;
const ctx = {state:{}, num:v => typeof v === 'number' ? v : null,
  Date:{now:() => now, parse:Date.parse}, renderEngagement() {},
  document:{getElementById:id => elements[id], createElement:node,
    createTextNode:text => ({own:text, children:[], textContent:text})}};
vm.createContext(ctx);
vm.runInContext(home.match(/  function plural[^\n]+/)[0] + '\n' +
  fragment('  function roughCount(', '  function openKeys('), ctx);
vm.runInContext(fragment('  function renderMeta() {', '    // The browser tab') + '}', ctx);
function visible(node, phone) {
  const classes = (node.className || '').split(' ');
  if (classes.includes(phone ? 'tag-full' : 'tag-short')) return '';
  return node.own + node.children.map(child => visible(child, phone)).join('');
}
for (const floor of [null, 40, 90]) for (const count of [0, 1, 2, 99, 100, 101, 999, 1000, 1234]) {
  for (const spotlight of [false, true]) {
    ctx.state = {meta:{floors:{min_pct:floor}},
      deals:Array.from({length:count}, () => ({status:'alive', pct_at_checkout:95})).concat({status:'dead', pct_at_checkout:95}),
      spotlight:spotlight ? [{pct_at_checkout:null, spotlight:{until:new Date(now + 1000).toISOString()}}] : []};
    ctx.renderMeta();
    const live = count + Number(spotlight), countText = live < 100 ? ctx.plural(live, 'live deal') : ctx.roughCount(live) + ' live deals';
    const prefix = 'verified deals' + (floor !== null && !spotlight ? ', ' + floor + (floor < 90 ? '–90% off' : '%+ off') : '') + ' · ';
    const original = prefix + (live ? countText : 'Offers checked before publication');
    assert.equal(visible(elements.tagline, false), original);
    assert.equal(visible(elements.tagline, true), enabled && live ?
      (live < 100 ? ctx.plural(live, 'verified deal') : ctx.roughCount(live) + ' verified deals') : original);
    const children = elements.tagline.children;
    assert.equal(children.filter(n => n.className === 'live-dot').length, 1);
    assert.equal(children.find(n => n.className === 'live-dot').attributes['aria-hidden'], 'true');
    assert.equal(children.at(-1).id, 'tagsince');
    assert.equal(children.filter(n => n.className === 'tag-short').length, enabled && live ? 1 : 0);
  }
}
console.log('PASS: phone/desktop counts, rounding, plural, zero fallback, floor, spotlight, dot and visit slot');
