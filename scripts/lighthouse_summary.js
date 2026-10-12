// Scores and audit titles only; report details stay in the short-lived artifact.
const fs = require("node:fs");
const path = require("node:path");
const CATEGORIES = ["performance", "accessibility", "best-practices", "seo"];

function summarize(report, minimum) {
  if (report.runtimeError) throw new Error("audit did not finish");
  const scores = {}, failing = new Map();
  for (const name of CATEGORIES) {
    const category = (report.categories || {})[name];
    const score = category && category.score;
    if (typeof score !== "number" || !Number.isFinite(score) || score < 0 || score > 1) {
      throw new Error("missing or invalid score");
    }
    scores[name] = Math.round(score * 100);
    for (const ref of category.auditRefs || []) {
      const audit = (report.audits || {})[ref.id];
      if (!audit || !(ref.weight > 0) || typeof audit.score !== "number" ||
          audit.score >= 1 || ["manual", "informative", "notApplicable"].includes(audit.scoreDisplayMode)) continue;
      const previous = failing.get(ref.id);
      if (!previous || ref.weight > previous.weight) {
        failing.set(ref.id, { weight: ref.weight, score: audit.score, title: String(audit.title).replace(/[\r\n]/g, " ") });
      }
    }
  }
  const worst = [...failing.values()].sort((a, b) => b.weight - a.weight || a.score - b.score ||
    a.title.localeCompare(b.title)).slice(0, 5).map((audit) => audit.title);
  return { scores, worst, ok: report.categories.accessibility.score * 100 >= minimum &&
    report.categories.seo.score * 100 >= minimum };
}

function main(files) {
  const minimum = Number(process.env.LH_MIN ?? 85), lines = [];
  if (!Number.isFinite(minimum) || minimum < 0 || minimum > 100 || !files.length) {
    console.error("Lighthouse: reports required; LH_MIN must be 0-100");
    return 1;
  }
  let failed = false;
  for (const file of files) {
    const label = path.basename(file, ".json");
    try {
      const result = summarize(JSON.parse(fs.readFileSync(file, "utf8")), minimum);
      lines.push(`${label}: ${CATEGORIES.map((name) => `${name} ${result.scores[name]}`).join(" | ")}`);
      for (const title of result.worst) lines.push(`  ${title}`);
      if (!result.ok) { failed = true; lines.push(`  FAIL: accessibility or SEO below ${minimum}`); }
    } catch {
      failed = true;
      lines.push(`${label}: FAIL: missing, invalid, or unfinished report`);
    }
  }
  console.log(lines.join("\n"));
  if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, lines.join("\n") + "\n");
  return failed ? 1 : 0;
}

module.exports = { summarize, main };
if (require.main === module) process.exitCode = main(process.argv.slice(2));
