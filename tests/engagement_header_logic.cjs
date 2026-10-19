const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const home = fs.readFileSync(process.argv[2], 'utf8');
const tidy = process.argv[3] === 'true', enabled = process.argv[4] === 'true';
function fragment(start, end) {
  const a = home.indexOf(start), b = home.indexOf(end, a);
  assert(a >= 0 && b > a, start);
  return home.slice(a, b);
}
const elements = {};
function element() {
  return {children:[], value:'', hidden:false, setAttribute() {},
    set textContent(value) { this.value = value; this.children = []; },
    get textContent() { return this.value + this.children.map(n => n.textContent).join(''); },
    appendChild(node) { this.children.push(node); if (node.id) elements[node.id] = node; }};
}
for (const id of ['tagline', 'q', 'sincevisit', 'surprise']) elements[id] = element();
assert(home.includes('<p id="sincevisit" class="since" hidden></p>'));
elements.sincevisit.hidden = true;
const ctx = {
  document:{getElementById:id => elements[id], createElement:element,
    createTextNode:text => ({textContent:text})},
  state:{meta:{}, deals:Array.from({length:1893}, () => ({status:'alive'}))},
  num:() => null, roughCount:() => '1,000+',
  engagementOn:() => true, engagement:{storage:true, newCount:1893, newKeys:{fresh:true}},
  surpriseRows:() => [], dealKey:d => d.key,
  SEEN_KEY:'wp.seen', lsGet:() => '[]', liveNotIn:() => 1893,
  mediaHtml:() => '', savedButtonHtml:() => '', freshUI:null
};
vm.createContext(ctx);
if (enabled) vm.runInContext(fragment('  function newSinceVisit(d)', '  function engagementLineText()'), ctx);
vm.runInContext(fragment('  function renderMeta()', '    var disclosure =') + '}', ctx);
vm.runInContext(fragment('  function sinceLastVisit(deals)', '  function rememberSeen(deals)'), ctx);
// Model the desktop's display:none short copy when checking visible wording.
const desktopText = node => node.className === 'tag-short' ? '' :
  (node.value ?? node.textContent ?? '') + (node.children || []).map(desktopText).join('');
for (let repeat = 0; repeat < 2; repeat++) {
  ctx.renderMeta();
  ctx.sinceLastVisit(ctx.state.deals);
  assert.equal(desktopText(elements.tagline), 'verified deals · 1,000+ live deals' +
    (enabled && !tidy ? ' · 1893 new since your last visit' : ''));
  assert.equal(elements.tagline.children.filter(n => n.className === 'live-dot').length, 1);
  assert.equal(elements.sincevisit.hidden, tidy || enabled);
  assert.equal(elements.sincevisit.textContent, !tidy && !enabled ? '1893 new since your last visit' : '');
}
if (enabled) {
  // Execute the emitted card media code, including the actual visit-new markup.
  vm.runInContext('function badge(d) {' + fragment('    var media = mediaHtml(d);', '    var price = d.final_price') +
    'return media;}', ctx);
  const badge = '<span class="visit-new">NEW<span class="sr-only"> since your last visit</span></span>';
  assert.equal(ctx.badge({key:'fresh', status:'alive'}), badge);
  assert.equal(ctx.badge({key:'old', status:'alive'}), '');
  assert.equal(ctx.badge({key:'fresh', status:'dead'}), '');
  assert.equal(ctx.badge({key:'fresh', status:'alive', spotlight:{}}), '');
  ctx.engagement.storage = false;
  assert.equal(ctx.badge({key:'fresh', status:'alive'}), '');
}
console.log('header visit counts and card NEW badges passed');
