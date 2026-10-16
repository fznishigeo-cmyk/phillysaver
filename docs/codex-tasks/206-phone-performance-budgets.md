# 206 — Enforce small phone payloads and explicit lab LCP limits

## Why

Most visitors use phones. Our Lighthouse summary gates accessibility/SEO but
does not currently gate LCP. Plausible compares compressed built variants;
TikTok bounds mounted feed work. Add reviewable asset budgets and check the
lab results we already produce, without adding shopper scripts or rebuilding
the feed. Background: `docs/research/clone-wars-study.md`, PL4/K1 and W8, rank 6.

## Task

Read CLAUDE.md, AGENTS.md and docs/HANDOFF.md; preserve accepted tasks 119–121
and coordinate any shared test/workflow edits. Independent of tasks 201–205.

Read `scripts/lighthouse_summary.js:4`, `:28`,
`.github/workflows/lighthouse.yml:61`, `:74`,
`scripts/mobile_audit.cjs:8`, `dealradar/publish/templates/index.html:911`,
`:2129`, `tests/test_pwa.py:19`, `:52`, `tests/test_support.py:27`,
`tests/lighthouse_summary_logic.cjs`, `tests/fixtures/lighthouse_pass.json`,
`dealradar/config.py:443`, and `dealradar/publish/site.py:1153`.

1. Add proposed config `quality.phone_budget` with enabled false, strict
   boolean, lab_lcp_max_ms 2500 and lab_cls_max 0.1. Add positive integer byte
   ceilings for home HTML gzip, own home JavaScript gzip, own home CSS gzip and
   deals.json gzip. During implementation, BEFORE code edits, measure the
   fixed fixtures below and set each byte ceiling to ceil(1.05 × the largest
   corresponding baseline size across modes). Record the measured values and
   fixture definition in the handoff. These ceilings are proposed headroom,
   not observed live vitals. Invalid/non-finite limits fail config validation;
   no zero/unlimited escape while enabled. All numeric controls live in config.
2. Add one proposed `scripts/publication_budget.py` read-only analyzer using
   Python's standard library plus the project's existing config loader. Given
   a generated output directory, validate it is the intended fixture output,
   read files, and emit bounded JSON plus a concise pass/fail summary. No
   writes to that output, state, receipts or public pages. No remote URLs,
   symlink traversal outside the supplied output or additional packages.
3. Measure raw bytes and gzip with fixed level 9/mtime 0. Home HTML includes
   its actual inline content. For own JS/CSS metrics separately, include
   inline executable scripts/styles and each referenced existing same-origin
   .js/.css asset once, in deterministic order; skip JSON-LD as executable JS,
   report it as markup. Recognize MIME type and inline/external boundaries;
   don't count a stylesheet tag or remote tag URL as downloaded bytes.
   Avoid double-counting repeated local references. A missing/unsafe local
   asset fails analysis; a remote reference is counted/listed as external,
   never fetched or silently included in the own-byte denominator. Count
   deals.json independently. Explain that component byte metrics overlap the
   HTML transfer metric; never sum them into a fictional transfer total.
4. Use a deterministic 1,400-offer synthetic publish fixture: fixed UTC time,
   stable keys, representative categories/stores, codes/conditions, varied
   current prices and permitted synthetic image URLs, with zero fetches and
   no real state. Reuse publisher factories and cover support, open, closed
   membership and shipped config. Fixture inputs cannot be simplified after
   measuring just to satisfy the budget. Keep the complete source/seed in
   tests so reports reproduce. Analyze outputs without committing generated
   pages or any state.db. No product code byte changes are expected.
5. Extend the existing Lighthouse result parser to accept resolved budget
   values from the existing config through the workflow's Python config
   loader/env wiring; no scattered numeric thresholds. Keep disabled parser
   behavior/output/exit status exactly as before. When enabled, the existing
   accessibility/SEO gates still apply; additionally require finite nonnegative
   numeric largest-contentful-paint and cumulative-layout-shift values for
   each audited page. Missing/error/non-finite audits are “not measured” and
   fail the enabled quality check rather than pass. Report actual/limit/units
   per page. Do not equate category performance score with LCP.
6. Consume only reports already produced by lighthouse.yml. Do not run a new
   site audit, install a browser, add a schedule, expand the URL set, fetch
   retailers or load trackers. Preserve existing bot/privacy request blocking.
   The parser/workflow branch remains OFF until reviewed; no workflow dispatch
   in this implementation. Offline test fixture wiring proves the parser
   branch without a live call. No changes to fast/hourly deploy jobs.
7. Include the byte analyzer in an offline pytest regression wrapper, using
   tmp_path only. It must exercise explicit enabled/disabled configs, so its
   tests remain useful while shipped enabled is false. Repeated analysis of
   identical output is byte-identical and writes no state. Failure prints
   only counts/path labels/thresholds, never public payload bodies or secrets.
   Add file-level delta diagnostics to this analyzer's own fixture comparisons;
   do not replace or re-pin existing whole-tree tests in this session.

## Tests

- Baseline/candidate synthetic publications yield identical product files,
  versions, state/meta and receipts in all four modes. Analyzer adds no public
  byte or browser request; unchanged input produces identical analyzer output.
- Just below/at/above each byte ceiling; raw/gzip reproducibility; JSON-LD
  handling; duplicate own assets, query-bearing local asset paths with safe
  normalization, missing files, external URLs and symlink/path escapes.
- Valid config, omitted/false behavior, enabled invalid/NaN/Infinity/zero
  ceilings and malformed report roots. Config errors have safe diagnostics.
- Existing Lighthouse pass/fail fixtures preserve disabled behavior; enabled
  thresholds below/at/above limits, missing audit, runtimeError, wrong type,
  non-finite value and one failing page among multiple reports. LCP uses ms,
  CLS is unitless. Keep accessibility/SEO failures blocking.
- All network transports are fake or unavailable. Test workflow wiring as
  code/arguments with mock inputs, never dispatch it. No source/retailer call,
  analytics event, screenshot fetch or paid model invocation.
- Run focused analyzer/config/Lighthouse/workflow/publish tests and existing
  offline pipeline rerun-idempotency, then `python -m pytest -q`. Report exact
  failures and independently establish any claimed baseline failure. If any
  Worker edit appears, remove it: this task has no Worker scope.

Report before/after raw/gzip values, explicit ceilings and unchanged public
trees. State that these checks do not measure real-user LCP, INP, cache reuse,
visual accessibility or retention. Existing lab LCP/CLS are lab samples; the
≤200ms INP target needs real-user data and must remain “not measured” here.
If no local Lighthouse reports exist, test synthetic inputs and say the real
measurement is unavailable; don't fetch one. No browser installation needed.

## Constraints

Ship the quality knob OFF pending review. Small analyzer/parser/workflow-test
change; no framework, server, login, new dependency, service worker, shopper
script, DOM virtualization, new telemetry or page redesign. Keep robots,
permissions, 1 req/s/host, verified evidence, facts-only storage, all monetary
math in savings.py, config knobs, AI caps, encrypted data-branch state and
idempotency. Budget compression arithmetic is infrastructure measurement,
not a new price/savings calculation. Keep Amazon history hidden and current
prices date/time-qualified; do not create Amazon tracking/alerts/history or
persist real deal snapshots for analysis. Preserve affiliate/source credit,
privacy/internal controls, WCAG 2.2 AA/phone layout, existing snapshot tests
and operator decisions. No network, commits, push, deployment or workflow
dispatch. Never delete other /tmp directories. Update HANDOFF with measured
fixture values, files/checks and unmeasured live vitals; finish concisely.
