# Codex task 002: readers for Dealmoon, Jungle Deals and MorningSave

**Read first:** AGENTS.md, CLAUDE.md, docs/HANDOFF.md, and your own merged PR #1
(dealradar/fetch/pages.py, tests/test_pages.py). This task repeats that pattern.

## Why
Three sources are paused (`enabled: false` in sources.yaml) because their URL is an
HTML page, not a feed. Each has the operator's written crawl permission
(`crawl_permission`):

| Source | Configured URL | Notes |
|---|---|---|
| dealmoon_us | https://www.dealmoon.com/en | US (English) edition only |
| jungle_deals | https://jungledealsblog.com/ | the site moved to jungle.deals |
| morningsave | https://morningsave.com/ | Mediocre Corp (the meh.com family) |

## What to do
1. For each source, look for a feed or an official API before writing an HTML reader.
   MorningSave belongs to the same company as meh.com, and the repo already reads
   Meh's API (MEH_API_KEY). Check whether that API, or a sibling API, covers MorningSave.
2. Otherwise add an HTML reader in dealradar/fetch/pages.py, as in PR #1:
   - one RawPost per deal card;
   - the exact price and coupon wording in its text;
   - the source's own permalink as the post URL;
   - no images;
   - changed markup fails loudly.
3. Use PoliteFetcher only: our User-Agent, 1 request/second/host, conditional GETs.
   The robots.txt waiver applies to the source's own host only. No retailer crawling.
4. Re-enable a source only after it works live from your environment, and report the
   item counts. `allowed_html: true` is fine for these three because of their written
   permission.
5. Tests: fixtures plus tests like tests/test_pages.py. The whole suite stays green.

## Network
The operator will allow these hosts in your environment:
dealmoon.com, www.dealmoon.com, jungle.deals, www.jungle.deals, jungledealsblog.com,
morningsave.com, api.meh.com. If a host is still blocked, say which one in the PR and
stop for that source.

## Deliver
Commits on this pull request's branch. If you can't push here, open a new PR into
`claude/dealradar-aggregator-pyqirm`. Never push to the live branch. Update
docs/HANDOFF.md's "Tripped sources" list in your last commit.
