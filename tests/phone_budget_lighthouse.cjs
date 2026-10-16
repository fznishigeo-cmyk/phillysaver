// Only local synthetic reports; no Lighthouse/browser execution.
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const { summarize, main } = require("../scripts/lighthouse_summary.js");
const fixture = name => path.join(__dirname, "fixtures", `lighthouse_${name}.json`);
const passing = JSON.parse(fs.readFileSync(fixture("pass"), "utf8"));
const budget = { enabled: true, lab_lcp_max_ms: 2500, lab_cls_max: 0.1 };
const measured = () => {
  const report = structuredClone(passing);
  report.audits["largest-contentful-paint"] = { numericValue: 2500 };
  report.audits["cumulative-layout-shift"] = { numericValue: 0.1 };
  return report;
};
for (const [id, limit, step] of [["largest-contentful-paint", 2500, 1], ["cumulative-layout-shift", 0.1, 0.001]]) {
  for (const delta of [-step, 0, step]) {
    const report = measured();
    report.audits[id].numericValue = limit + delta;
    const result = summarize(report, 85, budget);
    assert.equal(result.ok, delta <= 0);
    assert.equal(result.vitals.find(v => v.name === (id.startsWith("largest") ? "LCP" : "CLS")).actual, limit + delta);
    assert.deepEqual(summarize(report, 85, { enabled: false }), summarize(report, 85));
  }
  for (const invalid of [undefined, null, "0", true, {}, [], NaN, Infinity, -Infinity, -1]) {
    const report = measured();
    report.audits[id].numericValue = invalid;
    const result = summarize(report, 85, budget);
    assert.equal(result.ok, false);
    assert.ok(result.vitals.some(v => v.actual === null && v.ok === false));
  }
  for (const error of [{ scoreDisplayMode: "error" }, { errorMessage: "PRIVATE" }]) {
    const report = measured();
    Object.assign(report.audits[id], error);
    assert.equal(summarize(report, 85, budget).ok, false);
  }
  const report = measured();
  delete report.audits[id];
  assert.equal(summarize(report, 85, budget).ok, false);
  report.audits[id] = { numericValue: 0 };
  assert.equal(summarize(report, 85, budget).ok, true);
}
assert.equal(summarize(measured(), 85, budget).ok, true); // performance 42 is separate from LCP.
assert.equal(summarize(measured(), 85, { ...budget, lab_lcp_max_ms: 2499 }).ok, false);
assert.equal(summarize(measured(), 85, { ...budget, lab_cls_max: 0.099 }).ok, false);
for (const category of ["accessibility", "seo"]) {
  const report = measured();
  report.categories[category].score = 0.849;
  assert.equal(summarize(report, 85, budget).ok, false);
}
assert.throws(() => summarize({ ...measured(), runtimeError: { code: "NO_FCP" } }, 85, budget), /finish/);
for (const invalid of [null, [], "PRIVATE", { enabled: "false" },
  { ...budget, lab_lcp_max_ms: Infinity }, { ...budget, lab_cls_max: 0 }]) {
  assert.throws(() => summarize(measured(), 85, invalid), /invalid phone budget/);
}

function run(files, settings, minimum = "85", summaryFile) {
  const old = { LH_MIN: process.env.LH_MIN, LH_PHONE_BUDGET: process.env.LH_PHONE_BUDGET,
    GITHUB_STEP_SUMMARY: process.env.GITHUB_STEP_SUMMARY };
  const oldLog = console.log, oldError = console.error, lines = [];
  try {
    process.env.LH_MIN = minimum;
    if (settings === undefined) delete process.env.LH_PHONE_BUDGET;
    else process.env.LH_PHONE_BUDGET = JSON.stringify(settings);
    if (summaryFile) process.env.GITHUB_STEP_SUMMARY = summaryFile;
    else delete process.env.GITHUB_STEP_SUMMARY;
    console.log = console.error = line => lines.push(line);
    return { status: main(files), stdout: lines.join("\n") };
  } finally {
    console.log = oldLog; console.error = oldError;
    for (const [name, value] of Object.entries(old)) {
      if (value === undefined) delete process.env[name]; else process.env[name] = value;
    }
  }
}
for (const files of [[fixture("pass")], [fixture("fail")], [fixture("pass"), fixture("fail")],
  [fixture("pass") + ".missing"], []]) {
  for (const minimum of ["85", "80", "invalid"]) {
    assert.deepEqual(run(files, { enabled: false }, minimum), run(files, undefined, minimum));
  }
}
const root = process.argv[2];
assert.ok(root, "pytest tmp_path required");
const good = path.join(root, "home.json"), bad = path.join(root, "hub.json");
fs.writeFileSync(good, JSON.stringify(measured()));
fs.writeFileSync(bad, JSON.stringify(measured()));
let result = run([good, bad], budget, "85", path.join(root, "summary.txt"));
assert.equal(result.status, 0);
assert.match(result.stdout, /LCP: 2500 ms; limit 2500 ms \(PASS\)/);
assert.match(result.stdout, /CLS: 0.1 unitless; limit 0.1 unitless \(PASS\)/);
assert.equal(fs.readFileSync(path.join(root, "summary.txt"), "utf8"), result.stdout + "\n");
const slow = measured(); slow.audits["largest-contentful-paint"].numericValue = 2501;
fs.writeFileSync(bad, JSON.stringify(slow));
result = run([good, bad], budget);
assert.equal(result.status, 1);
assert.match(result.stdout, /hub:[\s\S]*LCP: 2501 ms; limit 2500 ms \(FAIL\)/);
for (const broken of [null, [], "PRIVATE", { ...measured(), runtimeError: { code: "PRIVATE" } },
  { ...measured(), audits: {} }]) {
  fs.writeFileSync(bad, JSON.stringify(broken));
  result = run([good, bad], budget);
  assert.equal(result.status, 1);
  assert.match(result.stdout, /not measured/);
  assert.ok(!result.stdout.includes("PRIVATE"));
}
assert.equal(run([good], { ...budget, lab_cls_max: "PRIVATE" }).status, 1);
result = run([fixture("pass")], budget);
assert.equal(result.status, 1); // Older report fixture has no numeric vitals.
assert.match(result.stdout, /LCP: not measured ms; limit 2500 ms \(FAIL\)/);
assert.match(result.stdout, /CLS: not measured unitless; limit 0.1 unitless \(FAIL\)/);
