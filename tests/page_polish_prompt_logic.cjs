// Exercise the published signup controller with its real timing/storage rules.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {harness} = require('./join_prompt_logic.cjs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const source = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map(m => m[1]).find(s => s.includes('window.wpSupport ='));
for (const focus of [false, true]) {
  const h = harness({source, delay:35});
  const get = h.document.getElementById;
  const q = {value:focus ? '' : 'wireless mouse'};
  h.document.getElementById = id => id === 'q' ? q : get(id);
  h.document.activeElement = {closest:selector => focus && selector === '.search'};
  h.engage(); h.advance(35000); h.advance(60000);
  assert(h.prompt.hidden, 'search focus and a blurred results query both defer the automatic ask');
  assert(!h.storage.has('wp.join.promptUntil'), 'deferral must not consume the frequency limit');
  q.value = ''; h.document.activeElement = h.opener;
  h.advance(1000);
  assert(!h.prompt.hidden, 'the eligible ask resumes after leaving search');
  h.key('Escape'); h.advance(60000); h.refresh();
  assert(h.prompt.hidden, 'existing once-per-visit rule remains');
}
const h = harness({source, delay:35});
const get = h.document.getElementById;
h.document.getElementById = id => id === 'q' ? {value:'mouse'} : get(id);
h.click(h.subscribe);
assert(!h.prompt.hidden, 'an explicit Join click still opens immediately during search');
console.log('Search defers automatic join prompts; manual join and frequency rules pass');
