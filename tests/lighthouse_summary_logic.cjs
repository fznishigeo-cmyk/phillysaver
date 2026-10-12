const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const { summarize, main } = require("../scripts/lighthouse_summary.js");
const fixture = name => path.join(__dirname, "fixtures", `lighthouse_${name}.json`);
const passing = JSON.parse(fs.readFileSync(fixture("pass"), "utf8"));
const failing = JSON.parse(fs.readFileSync(fixture("fail"), "utf8"));
const result = summarize(passing, 85);
assert.deepEqual(result.scores, { performance: 42, accessibility: 91, "best-practices": 73, seo: 85 });
assert.equal(result.ok, true); // Low performance/best-practices never trigger the gate.
assert.deepEqual(result.worst, ["Largest Contentful Paint", "Form elements have labels", "Document has a title",
  "Uses HTTPS", "Background and foreground colors have sufficient contrast"]);
assert.equal(summarize(failing, 85).ok, false);
assert.equal(summarize(failing, 80).ok, true);
for (const category of ["accessibility", "seo"]) {
  const report = structuredClone(passing);
  report.categories[category].score = 0.849;
  assert.equal(summarize(report, 85).ok, false); // Gate uses the unrounded score.
  report.categories[category].score = null;
  assert.throws(() => summarize(report, 85), /score/);
}
assert.throws(() => summarize({ ...passing, runtimeError: { code: "NO_FCP" } }, 85), /finish/);

const run = (files, minimum) => {
  const oldMinimum = process.env.LH_MIN, oldSummary = process.env.GITHUB_STEP_SUMMARY;
  const oldLog = console.log, oldError = console.error, lines = [];
  try {
    if (minimum === undefined) delete process.env.LH_MIN; else process.env.LH_MIN = minimum;
    delete process.env.GITHUB_STEP_SUMMARY;
    console.log = console.error = line => lines.push(line);
    return { status: main(files), stdout: lines.join("\n") };
  } finally {
    console.log = oldLog; console.error = oldError;
    if (oldMinimum === undefined) delete process.env.LH_MIN; else process.env.LH_MIN = oldMinimum;
    if (oldSummary === undefined) delete process.env.GITHUB_STEP_SUMMARY; else process.env.GITHUB_STEP_SUMMARY = oldSummary;
  }
};
assert.equal(run([fixture("pass")], "85").status, 0);
let cli = run([fixture("pass"), fixture("fail")], "85");
assert.equal(cli.status, 1);
assert.match(cli.stdout, /performance 42 \| accessibility 91 \| best-practices 73 \| seo 85/);
assert.match(cli.stdout, /Form elements have labels/);
assert.ok(!cli.stdout.includes("Private detail") && !cli.stdout.includes("private@example.test"));
assert.equal(run([fixture("fail")], "80").status, 0);
assert.equal(run([fixture("pass")], "invalid").status, 1);
assert.equal(run([fixture("pass")], "101").status, 1);
assert.equal(run([fixture("pass") + ".missing"], "85").status, 1);
assert.equal(run([], "85").status, 1);
const defaultGate = run([fixture("fail")]);
assert.equal(defaultGate.status, 1);
assert.match(defaultGate.stdout, /below 85/);
