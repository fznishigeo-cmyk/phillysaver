# Codex task 025: daily Lighthouse scan + hourly screenshots of the live site

**Read first:** AGENTS.md, CLAUDE.md (politeness; counts only in public
logs). Files: `scripts/site_check.js`, `.github/workflows/site-check.yml`,
`scripts/browser_probe.js`, `.github/workflows/browser-probe.yml`.

Operator (2026-10-12): scan the live site with a headless browser like a
visitor, every hour, and catch problems before visitors do. The hourly
walk-through exists (tasks 018/022). Add two things.

## 1. Screenshots, hourly (artifacts)
In `site_check.js`, after the walk-through: full-page screenshots of the
home page (first screen only, not the whole wall), the first deal page and
`/best-deals-today/` on desktop (1440x900) and phone (390x844), plus the
support banner on phone after scrolling past card 8. Save to `shots/`;
`site-check.yml` uploads `shots/` with `actions/upload-artifact` (pinned
major version already used in the repo if any, else v4), retention 3 days,
artifact name `site-shots`. Analytics stay blocked. No extra requests
beyond those pages (they are already loaded or one more navigation each).

## 2. Lighthouse, once a day
New workflow `.github/workflows/lighthouse.yml`: schedule daily at 06:17 UTC
plus `workflow_dispatch`; Node 20; `npx --yes lighthouse@12.6.1` (exact
version) with the Playwright Chromium (`npx playwright install chromium`,
pass its path via `--chrome-path` or `CHROME_PATH`), `--preset` mobile
default and `--only-categories=performance,accessibility,best-practices,seo`,
`--output=json`. Pages: `/`, the first hub in sitemap.xml, the first
`/deal/` URL in sitemap.xml, `/best-deals-today/`. A small node script
`scripts/lighthouse_summary.js` prints, per page: the four scores (0-100)
and the 5 worst failing audits by weight (title only), and exits 1 when any
page's accessibility or SEO score is below `LH_MIN` (env, default 85) so
GitHub emails the owner. Upload the JSON reports as an artifact (3 days).
Analytics: pass `--blocked-url-patterns` for the same trackers site_check
blocks.

## 3. Tests
`tests/test_workflows.py`: the new workflow's schedule, pinned versions,
the artifact steps, the LH_MIN gate. A node test (like
`tests/site_check_logic.cjs`) for `lighthouse_summary.js` on two fixture
reports (one passing, one failing). `node --check` on the scripts. One
short bullet in docs/HANDOFF.md under "In flight".

Chromium may not launch in your sandbox: say so; do not fake results.
Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
