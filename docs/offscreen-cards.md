# Task 237: restore and off-screen cards

`site.offscreen_cards` defaults to false and ships false. The installed
Chromium and headless shell both fail to launch in this sandbox with
`Operation not permitted`. Actual phone/desktop geometry and Lighthouse
performance remain unmeasured here; activation waits for the browser checks.

Restoration corrects anchor errors greater than 2px for at most six animation
frames, ending polling after two stable measurements. A 500ms observation
window permits a pending Saved/support redraw to resume checks, with at most
one additional frame after the six-frame allowance. Each check finds the live
card again. Known redraw clamping is distinguished from unrelated scrolling;
wheel, touch, pointer and key input cancel immediately, including during a
redraw. Cancellation, pagehide, timeout and replacement restores disconnect
the watch and remove its listeners. Snapshot fields, history API calls and
the one `back_restore` event retain their existing behavior.

Enabled containment is scoped to direct home-wall grid cards after each
grid's first eight. Exempting eight in every grid also protects the first
screen when Join/eligibility bands split the wall. Spotlight cards and all
other pages are excluded. The first eight cards and first four static photos
retain their markup and rendering. No card attributes or ordering change.

The CSS uses `content-visibility:auto` and `contain-intrinsic-size:auto` with
a height variable. While enabled, a ResizeObserver measures the content box
of the first fully rendered ordinary card, updates the variable on responsive
size changes, and attaches to the replacement card after wall redraws. This
measures the current breakpoint rather than using an invented desktop size.
Until the first measurement the fallback is the brief's observed 375px phone
height. The `auto` component remembers each rendered card's own size. These
callbacks are tested with synthetic heights; real breakpoint measurements
still require Chromium. With the switch off no sizing observers start.

The mandatory restore fix changes home JavaScript even while containment is
off. The disabled byte baseline therefore deliberately includes that repair
and the shared sizing installer. Relative to that repaired baseline, toggling
containment changes only its CSS and derived site-version stamps; disabling
it again restores all bytes. Pre-task comparison on HEAD `2225efe` with 180
synthetic offers in membership/open/support/shipped modes checked all 689
public files: after removing the intended restore/sizing JavaScript and
normalizing the version, every byte matched, including markup, existing CSS,
photos, deal facts, other pages and headers.

All 70 existing affected byte-pin cases passed with the untouched HEAD
templates before updating expectations. Task 237 refreshes 61 per-file pin
values below (some repeat across cases and files); the exact old/new hashes
are in the diff. No assertion or fixture facts were removed or simplified.
Four additional repaired-baseline tree pins live in `test_offscreen_cards.py`.

| Test file | Pin cases refreshed |
|---|---:|
| `test_support.py` | 4 home access/yearly variants |
| `test_pwa.py` | 4 disabled trees and the support switch-back check |
| `test_post_join_support_ask.py` | 3 missing-link variants |
| `test_photos_every_deal.py` | 4 disabled trees |
| `test_amazon_as_of.py` | 4 disabled trees |
| `test_checkout_price_deals.py` | 4 disabled access variants |
| `test_product_price_pages.py` | 4 disabled trees |
| `test_event_pages.py` | 4 disabled trees |
| `test_unconfirmed_label.py` | 4 disabled trees |
| `test_product_shadow.py` | 4 disabled trees with stored product history |
| `test_hub_inventory.py` | 1 disabled shipped tree |
| `test_incremental_cache.py` | 4 public/member comparisons |
| `test_unique_asin_rechecks.py` | 8 enabled/disabled public/member comparisons |
| `test_critic_tiering.py` | 1 disabled offline-pipeline tree |
| `test_manifest_refresh.py` | 16 access/history/as-of combinations |

`tests/browse_logic.cjs` now controls animation frames, geometry, redraws and
time. It checks the −448px shift, +7px per-card height change, late replacement
nodes, redraw clamping, input cancellation, timeout, superseding restores,
the six-plus-one frame bound and single-event tracking. Its sizing checks
exercise disabled observers, real content-box values, fractional sizes,
responsive changes and replacement nodes.

`tests/test_offscreen_cards.py::test_local_browser_restore` builds synthetic
publications with containment off and on and invokes
`tests/offscreen_cards_browser.cjs`. The script serves only local files through
Playwright routing, substitutes synthetic photos, and blocks external requests.
It scrolls to card 120, reloads, visits a deal page and returns without bfcache
at 390×844 and 1280×800. Both restore errors must be at most 4px. It also checks
the first eight cards and runtime sizing, with no browser download. Both
pytest cases skip with an explicit reason when browser launch is unavailable.
Re-run these cases outside this sandbox before enabling the switch.

Final focused verification: 445 adjacent checks plus 87 feature/pin/transport
checks passed; two browser cases skipped. All pytest runs used `-n 1` and
owned `/tmp/task237-*` basetemps, deleted after each run. The whole suite was
not run. `tests/test_workflows.py` is unchanged; no commit or push was made.
