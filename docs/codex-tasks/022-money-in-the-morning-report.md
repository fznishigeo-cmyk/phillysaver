# Codex task 022: clicks per store and sign-ups in the morning report; the hourly check guards the Amazon tag

**Read first:** AGENTS.md, CLAUDE.md (never print secrets; the repo is
public: Actions logs and reports carry counts only, never an email
address). Files: `dealradar/growth/google.py` (`ga4`, `EVENTS`),
`dealradar/growth/report.py` (or wherever the report is assembled),
`.github/workflows/growth-report.yml`, `cloudflare/subscribe-worker/`
(`subscribers.js`, `wrangler.toml`: how subscribers are keyed in KV),
`.github/workflows/deploy-subscribe-worker.yml` (how it finds the KV
namespace), `scripts/site_check.js`, `.github/workflows/site-check.yml`,
`config.yaml` (`affiliate:`), tests.

Operator (2026-10-12): affiliate commission is the income; the site is
free with **Join free** (email) and **Support our work**. The morning
report must show what earns: where visitors click out, and how the email
list grows.

## 1. Morning report: a "Money" block
- **Clicks out by store:** the page sends GA4 `deal_click` with params
  `store` and `category`. Query GA4 with dimension `customEvent:store`
  for `deal_click`: yesterday and the report window, top 10 stores with
  counts, plus the total. If GA4 answers that the dimension is unknown
  (not registered yet), the block says exactly: "Register the GA4 custom
  dimension: Admin → Custom definitions → Create, name store, scope Event,
  parameter store" and the rest of the report is unaffected.
- Add `support_click` to `EVENTS` (counted like the others).
- **Join free list:** total subscribers and new ones yesterday, counted
  from the Worker's KV through the Cloudflare API (`CLOUDFLARE_API_TOKEN`,
  `CLOUDFLARE_ACCOUNT_ID`; add both to growth-report.yml env from secrets;
  find the namespace the way the deploy workflow does). Counts only: never
  read values you don't need, never print or store an address. Missing
  token or a 403 = one line "subscribers: not available (<reason>)".
- Headline: put "Clicks out yesterday: N" and "Join free yesterday: N" in
  the report's top lines next to sessions.
- Each new section follows the report's existing ok/skipped/error pattern.

## 2. Hourly site check: the Amazon tag
New walk-through step 10 (desktop only; reads deals.json once, no page
loads): read `affiliate.amazon_tag` from the checked-out `config.yaml`
(a small regex is fine). Tag empty → `n/a (no Amazon tag yet)`. Tag set →
every deal URL on https amazon.com / www.amazon.com / smile.amazon.com has
exactly one `tag` query parameter equal to the configured tag, and
`meta.disclosure` contains "As an Amazon Associate"; else FAIL with the
count of bad links and one example path (no query string other than tag).
Never open the links.

## 3. Tests
GA4: the store query body, the parse, the unregistered-dimension message;
`support_click` counted; KV counting with a fake Cloudflare API (pagination,
new-yesterday from the stored timestamp, 403 path), and that no address
reaches the output; the workflow env names; site_check step 10's logic
(extract it into a small function you can test with node, like
tests/support_logic.cjs does) for: no tag, all good, a missing tag, a wrong
tag, two tags, a non-Amazon link ignored. One short bullet in
docs/HANDOFF.md under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
