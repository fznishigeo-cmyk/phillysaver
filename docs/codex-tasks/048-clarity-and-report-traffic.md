# Codex task 048: Microsoft Clarity on the site + Cloudflare and Bing numbers in the daily report

**Read first:** AGENTS.md, CLAUDE.md (knobs in config.yaml; never log or
publish secrets; the repo and Actions logs are public). Files:
`dealradar/publish/tracking.py` (`PATTERNS`, `head_snippet`, the consent
rules in the module docstring), `dealradar/publish/templates/privacy.html`,
the sign-up / account / checkout forms in the templates (`footer-signup.html`,
`subscribe.html`, `join.html`, the auth dialog in `index.html`),
`dealradar/growth/report.py` (`collect`, `build_report`), the existing
section modules in `dealradar/growth/` (pattern to copy), `dealradar/cli.py`
(`CORE_DOMAINS`), `config.yaml` (`tracking:`, `growth:`),
`.github/workflows/growth-report.yml`, `tests/test_tracking*.py`,
`tests/test_growth*.py`.

Operator (2026-10-12) set up Microsoft Clarity (project id `ywsuwmrauy`,
public, it appears in page source), saved a Bing Webmaster API key as the
secret `BING_WEBMASTER_API_KEY`, and a Cloudflare key with Analytics:Read as
`CLOUDFLARE_OPS_TOKEN` (zone wowpriced.com).

## 1. Clarity tag (`tracking.py`)
- `config.yaml` `tracking.clarity_project_id: "ywsuwmrauy"`; pattern
  `[a-z0-9]{8,12}`; empty = no tag.
- Load Clarity from `https://www.clarity.ms/tag/<id>` exactly like the ad
  pixels: **never** for a visitor whose browser sends Global Privacy Control
  or who opted out on /privacy.html (no request at all for them).
- Never record what people type: add `data-clarity-mask="true"` to every
  form and input that takes an email, name, card or account detail (sign-up
  box, subscribe, join, auth dialog, account/member areas). Prices and deal
  text stay visible (they are public).
- privacy.html: add Clarity to the list of tools, in the same plain style
  (what it records: clicks, scrolls, page views; typed text masked; the
  existing opt-out turns it off).
- Byte-hash baselines in tests/test_support.py are updated from the new output.

## 2. Daily report: Cloudflare traffic (new section `cloudflare_traffic`)
- Needs `CLOUDFLARE_OPS_TOKEN`; zone found by name
  (`publish.custom_domain`). GraphQL `httpRequests1dGroups` over the report
  window (`growth.report_days`), summing `uniq.uniques` (visitors),
  `sum.pageViews`, `sum.requests`; yesterday and window totals.
- Report lines: "Visitors (Cloudflare): yesterday N, last 7 days N; page
  views N". Numbers only in the private email/Telegram report, never in a
  log line (log `cloudflare_traffic: ok|skipped|error` only, like the other
  sections).

## 3. Daily report: Bing search (new section `bing`)
- Needs `BING_WEBMASTER_API_KEY`. GET
  `https://ssl.bing.com/webmaster/api.svc/json/GetRankAndTrafficStats`
  with `siteUrl=https://<custom_domain>/` and `apikey=<key>` (the key is a
  query parameter: never log the URL or an exception text that could carry
  it; errors log the status code only). Sum clicks and impressions over the
  report window and yesterday; it is normal for a new site to return no
  rows (report "no Bing data yet").
- `ssl.bing.com` joins `CORE_DOMAINS`.

## 4. Workflow and config
- growth-report.yml env adds `CLOUDFLARE_OPS_TOKEN` and
  `BING_WEBMASTER_API_KEY` (env-map lines only, like the others).
- Leave everything else in the report as is.

## 5. Tests (no network; httpx MockTransport)
Clarity: tag present with the id, absent when empty or invalid, not loaded
under GPC or opt-out (JS harness like the ad pixels), masked attributes on
every sensitive form, privacy text. Report: both sections ok / skipped
(missing secret) / error (HTTP 500), numbers appear in the report text and
never in caplog, the Bing key never appears in caplog or an exception
message, empty Bing data → "no Bing data yet". One short HANDOFF bullet
under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
