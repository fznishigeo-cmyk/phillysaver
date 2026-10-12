# Codex task 032: every non-Amazon store link earns through Sovrn Commerce

**Read first:** AGENTS.md, CLAUDE.md (credit to the source stays; emails;
knobs live in config.yaml; idempotency). Files: `dealradar/publish/site.py`
(`rewrite_affiliate`, `_serialize_deal`, `_override_key`, `link_mix`),
`dealradar/config.py` (`affiliate_*`), `config.yaml` (`affiliate:`),
`scripts/site_check.js` + `scripts/site_check_logic.cjs` (step 10),
`dealradar/publish/newsletter.py` (`_deal_url`: emails), tests.

Operator (2026-10-12): Sovrn Commerce (50k+ merchants: Walmart, Target,
Macy's, …) signed up; site under review. Amazon keeps our own tag
(`wowpriced-20`). Every OTHER retailer "Get deal" link should go through
Sovrn.

## 1. Link format (Sovrn's documented manual link)
`https://redirect.viglink.com?key=<affiliate.sovrn_key>&u=<URL-encoded
merchant URL>` (`urllib.parse.quote(url, safe="")`), optional
`&cuid=<up to 32 alphanumerics>`: use `cuid=` the deal's short key (first
16 hex of the key already in deals.json) so Sovrn reports show which deal
earned. The key is the site's public link key (it appears in every link):
it lives in `config.yaml affiliate.sovrn_key` (validate `[0-9a-f]{32}`;
anything else = off, warn without echoing it). `affiliate.sovrn_enabled:
true` switch.

## 2. Which links
- Wrap: an https store link (the deal's canonical retailer URL) whose host
  is not Amazon (amazon.*, amzn.to, a.co, link.amazon: those keep our own
  tag logic), not wowpriced.com, not a known deal-source/blog host (the
  deal's credit hosts, slickdeals.net, dealnews.com, etc.: a link that is
  the source's post stays as is), not already a network link
  (redirect.viglink.com, sovrn.co).
- Before wrapping, strip third-party affiliate tracking from the merchant
  URL so the click is ours: Impact (`irgwc`, `afsrc`, `sourceid`, `veh`,
  `wmlspartner`, `affiliates_ad_id`, `campaign_id`, `sharedid`,
  `clickid`, `irclickid`), CJ (`cjevent`, `cjdata`), Rakuten (`ranMID`,
  `ranEAID`, `ranSiteID`, `siteID`), ShareASale (`sscid`), and `utm_*`.
  Keep every other parameter (variants, sizes).
- `affiliate.link_overrides` (task from tonight) still wins for its
  pages.
- Deals that link their source post (needs steps) stay unchanged.
- Emails never wrap (keep `_deal_url` as is).

## 3. Reporting
- `link_mix`: a new bucket `sovrn` (counted before `other_store`), and the
  morning report line shows it ("N via Sovrn").
- `scripts/site_check.js` step 10: report "sovrn: N links" and FAIL if a
  wrapped link is malformed (missing key, `u` not an https URL once
  decoded, or a wrapped Amazon URL).

## 4. Tests
Wrapping (encoding, cuid), each exclusion (Amazon hosts, source posts,
blogs, already-wrapped, wowpriced), param stripping (each family; other
params kept), the override precedence, the switch and key validation
(warning never contains the key), emails untouched, link_mix bucket,
site_check logic. tests/test_e2e_offline.py::test_rerun_is_idempotent
green. Config: `affiliate.sovrn_enabled: true` and `affiliate.sovrn_key`
(the operator's key is already in config.yaml when you start? If not,
leave it "" and the tests set it). One short bullet in docs/HANDOFF.md
under "In flight"; also update the Affiliate standing rule (Amazon via our
tag, everything else via Sovrn, emails never tagged).

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
