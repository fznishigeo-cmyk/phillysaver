const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = process.argv[2];
const home = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const source = home.slice(home.indexOf('  var pagePolishQuery ='), home.indexOf('  function loadData() {'));
assert(source.includes('function applyPagePolishQuery()'));
function run(search, analyze = true) {
  const box = {value:'old query'}, clear = {hidden:true}, calls = [], replacements = [];
  Object.defineProperty(box, 'innerHTML', {set() {throw Error('Never insert query markup');}});
  const history = {state:{privateState:1}, replaceState(state, title, url) {replacements.push({state, url});}};
  const context = {URL, location:{href:'https://wowpriced.test/' + search}, history,
    document:{getElementById(id) {return id === 'q' ? box : clear;}}, browsePending:{old:true},
    applyBrowseSnapshot(snapshot, useURL) {calls.push('reset'); box.value = ''; assert.equal(snapshot.sort, 'top'); assert(useURL);},
    updateSearch() {calls.push('search');}, fetch() {throw Error('No new fetch path');}};
  if (analyze) context.analyzeSubmit = () => calls.push('analyze');
  vm.createContext(context); vm.runInContext(source, context);
  const result = context.applyPagePolishQuery();
  assert.equal(context.applyPagePolishQuery(), false, 'consume only once');
  return {box, clear, calls, replacements, result, context};
}
for (const query of ['', '?utm_source=test#top']) {
  const got = run(query);
  assert.equal(got.result, false); assert.deepEqual(got.calls, []); assert.deepEqual(got.replacements, []);
  assert.equal(got.box.value, 'old query');
}
for (const key of ['q', 'analyze']) for (const value of ['mouse', '<img src=x onerror=alert(1)> & " + café', 'x'.repeat(250), 'a'.repeat(199) + '😀']) {
  const got = run('?' + key + '=' + encodeURIComponent(value) + '&utm_source=event#top');
  assert.equal(got.box.value, Array.from(value).slice(0, 200).join(''));
  assert.doesNotThrow(() => encodeURIComponent(got.box.value));
  assert.deepEqual(got.calls, ['reset', key === 'q' ? 'search' : 'analyze']);
  assert.equal(got.clear.hidden, false); assert.equal(got.context.browsePending, null);
  assert.deepEqual(got.replacements, [{state:{privateState:1}, url:'/?utm_source=event#top'}]);
}
assert.deepEqual(run('?analyze=mouse', false).calls, ['reset', 'search']);
assert.deepEqual(run('?q=').calls, ['reset', 'search']);
assert.deepEqual(run('?analyze=%20%20').calls, ['reset', 'search']);
assert.equal(run('?q=one&analyze=two&q=three').box.value, 'two');
assert.equal(run('?q=one&analyze=two&q=three').replacements[0].url, '/');
assert.equal(run('?q=one&analyze=').box.value, '');
for (const slug of ['black-friday', 'cyber-monday']) {
  const html = fs.readFileSync(path.join(root, slug, 'index.html'), 'utf8');
  const script = html.match(/<script>\s*\(function \(\) \{\s*var form = document.getElementById\("event-search"\)[\s\S]*?<\/script>/)[0]
    .replace(/^<script>|<\/script>$/g, '');
  const events = {}, assigned = [], input = {value:'', addEventListener(type, fn) {events[type] = fn;}, focus() {events.focused = true;}};
  const form = {addEventListener(type, fn) {events[type] = fn;}, requestSubmit() {events.submit({preventDefault(){}});}};
  vm.runInNewContext(script, {URLSearchParams, document:{getElementById(id) {return id === 'q' ? input : form;}},
    location:{assign(url) {assigned.push(url);}}, fetch() {throw Error('Event page must not fetch');}});
  for (const value of ['mouse', '<script>alert("&")</script> + café', 'a'.repeat(250), 'a'.repeat(199) + '😀']) {
    input.value = value;
    events.keydown({key:'Enter', preventDefault(){}});
    let url = new URL(assigned.at(-1), 'https://wowpriced.test');
    assert.equal(url.pathname, '/'); assert.equal(url.searchParams.get('q'), Array.from(value).slice(0, 200).join(''));
    assert(!url.href.includes('<script>'));
    events.submit({submitter:{id:'analyze-search'}, preventDefault(){}});
    url = new URL(assigned.at(-1), 'https://wowpriced.test');
    assert.equal(url.searchParams.get('analyze'), Array.from(value).slice(0, 200).join(''));
  }
  const count = assigned.length; input.value = '  '; events.submit({preventDefault(){}});
  assert.equal(assigned.length, count); assert(events.focused);
}
console.log('Page polish: query handoff, escaping, truncation, history, absent params and event navigation passed');
