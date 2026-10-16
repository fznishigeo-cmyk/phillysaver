# 244c — Extension looks prices up on the device: one compact index, nothing sent per page (ships off)

## Why

Apps launch research (2026-10-16): Chrome's 2026 user-data policy and Firefox's
data-consent rules treat any per-page request as handling browsing activity. Today
the extension fetches `/check/data/<shard>.json` for the product a shopper is
viewing (2 hex chars of a hash, but still a per-page signal). Better: download one
compact index of every alias every 6 hours and look up locally, so visiting a page
sends nothing and "collects nothing" is literally true. The iPhone app (task 248)
and the Android app reuse the same index. This tree already contains tasks 240–252.

## Read first

CLAUDE.md, AGENTS.md, docs/HANDOFF.md, dealradar/publish/checker.py (`build`,
`publish`, `shard`, `js_rules`, entry fields and store policies),
dealradar/publish/checker_page.py + templates/checker.js, extension/* (background.js,
lookup.js, content.js, popup.js, rules.json, manifest.json, store/listing.md,
store/PUBLISH.md), dealradar/build_extension.py, privacy template's extension
section, docs/codex-tasks/248-ios-app.md (it needs `/check/data/all.json`),
tests/test_checker_index.py, tests/test_browser_extension.py, tests/extension_*.cjs.

## Task

1. `checker.publish` also writes `site/check/data/all.json`: a compact form of the
   same entries (same policy rules; no field the shards don't carry), keyed by
   alias, deterministic and byte-identical on rerun, with `generated_at` and a
   `version`. Keep it small: short keys or a column layout is fine; target
   < 1.5 MB uncompressed for 10,000 aliases (test with a synthetic 10,000-alias
   build). Cloudflare serves it gzip/brotli; headers as for shards (noindex,
   short cache). If over the cap, drop the oldest `ended` entries first and count
   them (never a live entry). Only when `checker.enabled`.
2. Extension: the background worker downloads `all.json` at install/startup and
   then every 6 hours (`chrome.alarms` permission is acceptable; or check age on
   each lookup), stores it in `chrome.storage.local` (unlimitedStorage only if
   truly needed; prefer compact format under the default quota), and answers
   lookups locally. No per-page network request. If the index is missing or
   older than 24 h, show nothing (never stale prices) and refresh in the
   background. Remove the per-shard fetch path from the extension.
3. Popup paste flow uses the same local index.
4. Update listing.md, PUBLISH.md (permission justifications: storage, alarms,
   host permission wowpriced.com for the daily index download only) and the
   privacy page section: "the extension downloads our public price list a few
   times a day and checks prices on your device; it sends nothing about the pages
   you visit."
5. The /check/ web page may keep using shards (fast first paint); do not change
   its behavior beyond what sharing code requires.

## Tests

- all.json: deterministic bytes, policy parity with shards (same entries for
  every alias), size cap + ended-drop order, off = no file.
- Extension (node + real Chromium at /opt/pw-browsers/chromium): visiting a
  product page makes zero network requests; the index refreshes on the alarm;
  stale (>24 h) index shows no card; popup lookup works offline after download;
  SPA navigation still updates the card (244b behavior kept).
- `build-extension` byte-identical rerun; rules.json fresh.

## Rules

No retailer/Keepa/LLM calls. Do not change tests/test_workflows.py. Run the test
files you touch plus tests/test_checker_index.py, tests/test_checker_page.py,
tests/test_browser_extension.py with -n 1 and your own basetemp /tmp/task244c-*;
delete it after. Do not run the whole suite. Do not commit. Update
docs/HANDOFF.md "In flight" with one entry.
