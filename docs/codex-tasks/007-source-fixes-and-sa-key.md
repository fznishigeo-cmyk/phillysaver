# Codex task 007: fix 6 sources from the probe; a clearer Google key error

**Read first:** AGENTS.md and CLAUDE.md (politeness rules; `crawl_permission`
waives robots.txt only on that source's own host).

## Part A: sources.yaml, from the source probe run on GitHub (2026-10-11 21:31 UTC)
| Source | Probe finding | Change |
|---|---|---|
| spoofee | `/feed/` 404; `https://www.spoofee.com/rss.php` is a feed (100 items, redirects to rss.xml) | set `url: https://www.spoofee.com/rss.php` |
| saving_our_way | `/feed/` 404; `https://www.savingourway.com/blog-feed.xml` is a feed (20 items) | set that `url` |
| southernsavers_amazon | category page, not a feed; the site-wide feed is already the `southernsavers` source | `enabled: false` with inline reason "redundant: southernsavers carries the site feed (probe 2026-10-11)" |
| slickdeals_blog | `/deal-news/feed/` 404, no feed found, no crawl permission | `enabled: false`, reason "feed gone (404), probe 2026-10-11" |
| sidedeal | a page, not a feed | `enabled: false`, reason "no feed (page only), probe 2026-10-11" |
| stl_mommy | TLS certificate does not match stlmommy.com | `enabled: false`, reason "site TLS certificate invalid for its host, probe 2026-10-11" |

Leave `dealgenius` alone (429, documented). Follow the existing style for
paused entries (e.g. kinja_deals: `enabled: false   # reason`). Change no
other source. Keep tests green (some tests load sources.yaml).

## Part B: a clearer error when GOOGLE_SA_JSON is not a key
`dealradar/growth/google.py` `access_token` does `json.loads(sa_json)`; a bad
secret only logs `JSONDecodeError`. Add a small `load_service_account(sa_json)`:
- strip whitespace and a leading UTF-8 BOM (`﻿`) before parsing;
- on failure, or when the result is not a dict with `client_email` and
  `private_key`, raise `ValueError` whose message describes the SHAPE only,
  never the content: character count, whether it starts with `{`, whether it
  ends with `.json` (a file name pasted instead of the file's text);
- `access_token` uses it.
In `dealradar/growth/report.py`, when a section fails with that ValueError,
log its message (it is safe: no secret content) instead of only the class
name. Everything else keeps `_error(e)` (class name only). Tests: a BOM-prefixed
key parses; a file name, a truncated key, and a JSON without `private_key`
each raise with the shape message and never include the input text.

## Rules
Run `.venv/bin/python -m pytest -q` from the repo root; all green. Do NOT
commit or push; leave changes uncommitted.
