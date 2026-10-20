const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const path = require('node:path');
const home = fs.readFileSync(process.argv[2], 'utf8');
const script = fs.readFileSync(path.join(path.dirname(process.argv[2]), 'drop-fresh.js'), 'utf8');
const labels = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const ctx = {document:{createElement:()=>({}), head:{appendChild(){}}, addEventListener(){}}};
vm.createContext(ctx);
const start = home.indexOf('  function roughCount('), end = home.indexOf('  function openKeys(', start);
assert(start >= 0 && end > start);
vm.runInContext(home.slice(start, end), ctx);
vm.runInContext(script.replace('export default function (host)', 'function mount(host)'), ctx);
const host = {state:{deals:[]}}, ui = ctx.mount(host);
for (const [total, label] of labels) {
  // A dead row never adds to the live count; eligibility does not reduce it.
  host.state.deals = Array.from({length:total}, () => ({status:'alive', locked:true, niche:true})).concat({status:'dead'});
  const html = ui.freshControls();
  assert.equal(label, ctx.roughCount(total) + (total === 1 ? ' deal' : ' deals'), 'header parity');
  assert.equal(html, '<div class="more fresh-more"><button type="button" id="fresh-all" class="pwbtn">See all ' + label + '</button></div>');
  assert(!html.includes('<p') && !html.includes('Full rotation'));
}
assert(!script.includes('Full rotation'));
console.log('PASS: live counts, plural, header parity and no rotation paragraph');
