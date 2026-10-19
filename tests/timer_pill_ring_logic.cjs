// Run the emitted one-second callback with no animation frames or real clock.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const start = html.indexOf('  function paintClock(now) {');
const interval = '  setInterval(tick, 1000);';
const end = html.indexOf(interval, start) + interval.length;
assert(start >= 0 && end > start);
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {style:{setProperty(key, value) {this[key] = value;}},
    classList:{toggle(key, on) {this[key] = on;}}, setAttribute(key, value) {this[key] = value;},
    querySelector() {return element('label');}});
  return elements.get(id);
}
const clock = {target:900000, step:900000, dropping:0, burstAt:0};
let now = 0;
const timers = [];
const sandbox = {clock, state:{meta:{}}, loadedOnce:false, reduceMotion:() => true,
  Date:{now:() => now}, setInterval(callback, delay) {timers.push({callback, delay});},
  paintSpotlightTime() {}, mmss:ms => String(ms), document:{getElementById:element}};
vm.createContext(sandbox);
vm.runInContext(html.slice(start, end), sandbox);
assert.equal(timers.length, 1);
assert.equal(timers[0].delay, 1000);
for (const [time, phase] of [[0, 'calm'], [120000, 'calm'], [121000, 'calm'],
    [600000, 'warm'], [780000, 'hot'], [870000, 'final']]) {
  now = time;
  timers[0].callback();
  const f = now / clock.step;
  assert.equal(element('countdown').textContent, String(clock.target - now));
  assert(Math.abs(element('drop').style['--pill-progress'] - f) < 1e-12);
  assert.equal(element('dhead').style.transform, `rotate(${(f * 360).toFixed(2)}deg)`);
  assert.equal(element('drop')['data-phase'], phase);
  assert.equal(element('drop').classList['clock-pulse'], false);
}
clock.dropping = 900000;
now = 900000;
timers[0].callback();
assert.equal(element('countdown').textContent, '0');
assert.equal(element('drop').style['--pill-progress'], 1);
assert.equal(element('dhead').style.transform, 'rotate(360.00deg)');
assert.equal(element('drop').classList.dropping, true);
clock.dropping = 0;
clock.target += clock.step;
now = 901000;
timers[0].callback();
assert(element('drop').style['--pill-progress'] < .002);
assert.equal(element('drop').classList.dropping, false);
assert.equal(element('drop')['data-phase'], 'calm');
console.log('PASS: one-second callback updates digits, dial and pill without animation frames; drop and reset');
