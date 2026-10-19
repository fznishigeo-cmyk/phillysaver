# Task 331 — photo floor red again: post-linked Amazon deals without a photo (investigate, then fix)

Read CLAUDE.md first (politeness, store facts only: NEVER publish the source's own images or post text). This tree = live d301254 + 328/329 (unreleased, unrelated).

Live site check is red on the photo floor only (floor 98%, scripts/site_check.js):
- #230 17:10Z: 97.62% (1270/1301) — no photo 31: 28 Amazon, 3 Under Armour; by source 17 Slickdeals, 11 Deals of America, 3 Money Saving Mom; "deals that link to their post (codes/steps): 31".
- #231 18:06Z: 97.89% (1299/1327) — no photo 28: 24 Amazon, 2 Walmart, 1 Kohls, 1 Under Armour; 19 Slickdeals, 5 DOA, 3 Hip2Save, 1 MSM; 25 link to their post; 3 direct via redirect.viglink.com.
- Hourly #255 summary photo_coverage live_public 1330 / with_photo 1289 (96.9%).
Earlier today photos were 99%. publish.photos.max_photoless_h is 1 (photoless cards sort last and hide after 1 h), so the floor counts deals in their first hour.

## Do
1. Find out from code + the offline fixtures/state paths why these deals have no photo. Hypotheses to confirm or reject: (a) deals whose Get deal goes to the source post (codes/steps) never get an ASIN, so the Keepa image backfill (images list, task 17/253b/265 identity backfill) skips them; (b) the identity backfill from source posts/short links (265) doesn't run for them, or is capped per run and falls behind after the Amazon-used removal changed the mix; (c) the 1 h photoless window lets a burst of new deals push the share under 98%; (d) the site check counts something the publisher's photoless rule doesn't (e.g. hidden-after-1h cards still in deals.json).
2. Fix the real cause with the smallest change: e.g. a post-linked Amazon deal whose post carries an Amazon product link/ASIN (already parsed facts) gets the product's Amazon photo via the existing Keepa image path (budget-capped, honors keepa.visitor_reserve_tokens), and the photo-less ones keep their existing behaviour. If (c)/(d), make the site check and the publisher agree on what counts (e.g. the check counts only cards older than max_photoless_h, or the publisher's rule), without lowering the 98% floor. Any knob lives in config.yaml; new behaviour behind ONE strict-bool flag (config.py default False = previous bytes; config.yaml true with a comment).
3. Never use the source's images, never fetch pages beyond what the polite fetcher already does, never weaken verify_evidence.

## Tests
Unit tests for the cause you fixed (a fixture deal like the failing ones gets a photo / is counted correctly); OFF = previous bytes; idempotency (tests/test_e2e_offline.py::test_rerun_is_idempotent) still passes. Targeted only: `.venv/bin/pytest -q -n 4 -p no:cacheprovider -o tmp_path_retention_policy=failed --basetemp=/tmp/b331 <relevant files>`; delete /tmp/b331. No full suite, commit or push. Short note in docs/HANDOFF.md. Reply <= 8 lines: confirmed cause (with evidence), fix, expected photo share.
