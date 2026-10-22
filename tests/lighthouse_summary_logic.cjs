const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const { summarize, main, labMetrics } = require("../scripts/lighthouse_summary.js");
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

// Noindex by design (LH_NOINDEX_OK): only the named page's SEO gate leaves out
// "blocked from indexing"; any other SEO problem on it still fails.
{
  const os = require("node:os");
  const report = structuredClone(passing);
  report.categories.seo.auditRefs = [{ id: "is-crawlable", weight: 4 }, { id: "document-title", weight: 1 },
    { id: "meta-description", weight: 1 }];
  report.audits["is-crawlable"] = { score: 0, title: "Page is blocked from indexing", scoreDisplayMode: "binary" };
  report.audits["document-title"] = { score: 1, title: "Document has a title", scoreDisplayMode: "binary" };
  report.audits["meta-description"] = { score: 1, title: "Document has a meta description", scoreDisplayMode: "binary" };
  report.categories.seo.score = 2 / 6;
  assert.equal(summarize(report, 85).ok, false);
  const exempt = summarize(report, 85, { enabled: false }, true);
  assert.equal(exempt.ok, true); assert.equal(exempt.seoGate, 1);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "lh-noindex-"));
  try {
    for (const name of ["deal", "home"]) fs.writeFileSync(path.join(dir, `${name}.json`), JSON.stringify(report));
    process.env.LH_NOINDEX_OK = "deal";
    const ok = run([path.join(dir, "deal.json")], "85");
    assert.equal(ok.status, 0);
    assert.match(ok.stdout, /noindex by design: SEO gate 100/);
    assert.equal(run([path.join(dir, "home.json")], "85").status, 1); // only the named page
    report.audits["document-title"].score = 0;
    fs.writeFileSync(path.join(dir, "deal.json"), JSON.stringify(report));
    assert.equal(run([path.join(dir, "deal.json")], "85").status, 1); // real SEO problems still fail
  } finally {
    delete process.env.LH_NOINDEX_OK;
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

// Lab metric values are printed when present (selector only, never page text).
{
  const os = require("node:os");
  assert.equal(labMetrics(passing), "");
  const report = structuredClone(passing);
  Object.assign(report.audits, {
    "first-contentful-paint": { numericValue: 1234.4 }, "largest-contentful-paint": { numericValue: 3456.6 },
    "total-blocking-time": { numericValue: 210 }, "speed-index": { numericValue: NaN },
    "cumulative-layout-shift": { numericValue: 0.0123 },
    "largest-contentful-paint-element": { details: { items: [{ items: [{ node: {
      selector: "div.card > img.photo", snippet: "<img alt=\"Private detail\">" } }] }] } },
  });
  assert.equal(labMetrics(report),
    "FCP 1234 ms | LCP 3457 ms | TBT 210 ms | CLS 0.012 | LCP element div.card > img.photo");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "lh-metrics-"));
  try {
    fs.writeFileSync(path.join(dir, "home.json"), JSON.stringify(report));
    const out = run([path.join(dir, "home.json")], "85");
    assert.equal(out.status, 0);
    assert.match(out.stdout, /\n  metrics: FCP 1234 ms \| LCP 3457 ms/);
    assert.ok(!out.stdout.includes("Private detail"));
    assert.ok(!run([fixture("pass")], "85").stdout.includes("metrics:"));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}
