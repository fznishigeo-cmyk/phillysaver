// Scores, audit titles and the lab metric values only; report details stay in
// the short-lived artifact.
const fs = require("node:fs");
const path = require("node:path");
const CATEGORIES = ["performance", "accessibility", "best-practices", "seo"];

function phoneBudget(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || typeof value.enabled !== "boolean") {
    throw new Error("invalid phone budget");
  }
  if (value.enabled && [value.lab_lcp_max_ms, value.lab_cls_max].some(
      limit => typeof limit !== "number" || !Number.isFinite(limit) || limit <= 0)) {
    throw new Error("invalid phone budget");
  }
  return value;
}

function vitals(report, budget) {
  return [["LCP", "largest-contentful-paint", budget.lab_lcp_max_ms, "ms"],
    ["CLS", "cumulative-layout-shift", budget.lab_cls_max, "unitless"]].map(([name, id, limit, units]) => {
    const audit = report && report.audits && report.audits[id];
    const measured = Boolean(!(report && report.runtimeError) && audit && !audit.errorMessage &&
      audit.scoreDisplayMode !== "error" && typeof audit.numericValue === "number" &&
      Number.isFinite(audit.numericValue) && audit.numericValue >= 0);
    const actual = measured ? audit.numericValue : null;
    return { name, actual, limit, units, ok: measured && actual <= limit };
  });
}

// Lab metric values (and the LCP element's selector, never its text), so a score
// swing can be told apart from noise after the 3-day artifact is gone.
const METRICS = [["FCP", "first-contentful-paint", "ms"], ["LCP", "largest-contentful-paint", "ms"],
  ["TBT", "total-blocking-time", "ms"], ["SI", "speed-index", "ms"], ["CLS", "cumulative-layout-shift", ""]];

function labMetrics(report) {
  const audits = (report && report.audits) || {}, parts = [];
  for (const [name, id, units] of METRICS) {
    const value = audits[id] && audits[id].numericValue;
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0) continue;
    parts.push(units ? `${name} ${Math.round(value)} ${units}` : `${name} ${value.toFixed(3)}`);
  }
  const items = (((audits["largest-contentful-paint-element"] || {}).details || {}).items || []);
  const node = items.flatMap(item => (item && item.items) || [item]).map(item => item && item.node)
    .find(n => n && typeof n.selector === "string");
  if (node) parts.push(`LCP element ${node.selector.replace(/[\r\n]/g, " ").slice(0, 120)}`);
  return parts.join(" | ");
}

// A page that is noindex on purpose (single-deal pages) is gated on SEO without
// the "blocked from indexing" audit; every other page keeps the full score.
function seoWithout(report, id) {
  let sum = 0, weights = 0;
  for (const ref of report.categories.seo.auditRefs || []) {
    const audit = (report.audits || {})[ref.id];
    if (ref.id === id || !audit || !(ref.weight > 0) || typeof audit.score !== "number" ||
        ["manual", "informative", "notApplicable"].includes(audit.scoreDisplayMode)) continue;
    sum += ref.weight * audit.score; weights += ref.weight;
  }
  return weights ? sum / weights : report.categories.seo.score;
}

function summarize(report, minimum, budget = { enabled: false }, noindexByDesign = false) {
  phoneBudget(budget);
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
  const crawl = (report.audits || {})["is-crawlable"];
  const seoGate = noindexByDesign && crawl && typeof crawl.score === "number" && crawl.score < 1
    ? seoWithout(report, "is-crawlable") : report.categories.seo.score;
  const result = { scores, worst, seoGate, ok: report.categories.accessibility.score * 100 >= minimum &&
    seoGate * 100 >= minimum };
  if (budget.enabled) {
    result.vitals = vitals(report, budget);
    result.ok = result.ok && result.vitals.every(metric => metric.ok);
  }
  return result;
}

function main(files) {
  const minimum = Number(process.env.LH_MIN ?? 85), lines = [];
  if (!Number.isFinite(minimum) || minimum < 0 || minimum > 100 || !files.length) {
    console.error("Lighthouse: reports required; LH_MIN must be 0-100");
    return 1;
  }
  let budget;
  try {
    budget = phoneBudget(JSON.parse(process.env.LH_PHONE_BUDGET ?? '{"enabled":false}'));
  } catch {
    console.error("Lighthouse: invalid phone budget");
    return 1;
  }
  const metricLines = metrics => metrics.map(metric =>
    `  ${metric.name}: ${metric.actual === null ? "not measured" : metric.actual} ${metric.units}; ` +
    `limit ${metric.limit} ${metric.units} (${metric.ok ? "PASS" : "FAIL"})`);
  const noindexOk = new Set((process.env.LH_NOINDEX_OK || "").split(",").map(s => s.trim()).filter(Boolean));
  let failed = false;
  for (const file of files) {
    const label = path.basename(file, ".json");
    try {
      const report = JSON.parse(fs.readFileSync(file, "utf8"));
      const result = summarize(report, minimum, budget, noindexOk.has(label));
      lines.push(`${label}: ${CATEGORIES.map((name) => `${name} ${result.scores[name]}`).join(" | ")}`);
      for (const title of result.worst) lines.push(`  ${title}`);
      const measured = labMetrics(report);
      if (measured) lines.push(`  metrics: ${measured}`);
      if (budget.enabled) lines.push(...metricLines(result.vitals));
      if (result.seoGate !== report.categories.seo.score) {
        lines.push(`  noindex by design: SEO gate ${Math.round(result.seoGate * 100)} without "blocked from indexing"`);
      }
      if (report.categories.accessibility.score * 100 < minimum || result.seoGate * 100 < minimum) {
        failed = true; lines.push(`  FAIL: accessibility or SEO below ${minimum}`);
      }
      if (!result.ok) failed = true;
    } catch {
      failed = true;
      lines.push(`${label}: FAIL: missing, invalid, or unfinished report`);
      if (budget.enabled) lines.push(...metricLines(vitals(null, budget)));
    }
  }
  console.log(lines.join("\n"));
  if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, lines.join("\n") + "\n");
  return failed ? 1 : 0;
}

module.exports = { summarize, main, seoWithout, labMetrics };
if (require.main === module) process.exitCode = main(process.argv.slice(2));
