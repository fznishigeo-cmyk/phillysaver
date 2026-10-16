# 204 — Bounded navigation hints for our own deal details

## Why

Native detail navigation can feel quicker if the browser has the next HTML
document ready. troddit's conditional media preload suggests loading only on
intent; TikTok and Dev.to show why lifecycle cleanup matters. HTML prefetch is
our adaptation, not a feature proved in those clones. Browser reuse and any
latency benefit must be measured. Background:
`docs/research/clone-wars-study.md`, T4/K3/D3, rank 4.

## Task

Read CLAUDE.md, AGENTS.md and docs/HANDOFF.md first. Coordinate changes to files
owned by tasks 119–121 and preserve their accepted behavior. Independent of
201–203/205–206. Use only the existing static stack and browser APIs.

Read `dealradar/publish/templates/deal-actions.js:5`, `:61`, `:78`,
`index.html:2009`, `:3451`, `browse.js:172`, `dealradar/publish/site.py:70`,
`:1048`, `dealradar/publish/seo.py:740`, `dealradar/config.py:443`,
`tests/deal_pages_logic.cjs`, `tests/test_seo_durable_pages.py` and
`scripts/mobile_audit.cjs:8`.

1. Add proposed `navigation_prefetch: {enabled: false, max_pages: 2,
   intent_ms: 180}` in config.yaml. Strict boolean; integer max_pages 1–2,
   intent_ms 150–1000. Omitted means false. Effective activation requires
   support mode. Inject a small proposed publisher `templates/intent-prefetch.js`
   only on home/listing pages with current public detail links, after existing
   page setup; not on detail/auth/join/checkout pages. Disabled/non-support
   output, metadata and versions must match captured baseline bytes.
2. Use progressive enhancement only: require browser support for
   `link.relList.supports('prefetch')`, a visible document after window load,
   `navigator.connection.saveData !== true`, and a known effectiveType of 4g.
   If the connection API/type is absent, disable this optional feature.
   Wait for a requestIdleCallback opportunity with no timeout-forced busy
   execution; absent idle API means no hint. This favors phone performance
   over speculative coverage. No hero/first-screen automatic prefetch.
3. Delegate mouse/pen pointerover and keyboard focusin on existing card title
   links only. Require stable intent for intent_ms; cancel on pointerout,
   focusout, hidden/pagehide or a target removed by rendering. Touch scroll
   does not trigger hints. No click interception or preventDefault. Enter,
   modifier/middle click, history and browser Back remain native.
4. Strictly allow only HTTPS, same-origin canonical `/deal/<slug>/` URLs that
   are existing current public live unlocked title links. Validate with URL,
   exact path shape, same origin, no credentials/query/fragment/encoded path
   separators/dot segments and the current card's emitted deal_path. Home
   static fallback and hub static cards must have a clear current-live
   eligibility signal; add one only in enabled output if necessary. Missing
   or ambiguous eligibility means no hint. Never infer live from href alone.
5. Insert `link rel=prefetch as=document` as a low-priority browser hint;
   no fetch/DOMParser/innerHTML/document execution, custom document cache or
   pre-render. Do not add a service worker or change cache headers. At most
   one active insertion, at least 1 second between insertions per host and
   at most max_pages distinct attempts per page lifetime. Spend the attempt
   on insertion even if the request fails or events never fire. No retries.
   Native browser caching is not a new price-history store; do not persist
   documents/JSON or override current-price timestamps/freshness behavior.
6. An anchor whose card becomes dead, hidden, locked, removed or unconfirmed
   before dispatch loses eligibility. Never hint Get deal, credited source,
   image/media, Amazon/retailer/tracker, `/p/` history, Worker/API, member,
   Stripe, join, welcome, arbitrary query or redirect URLs. Configure no new
   first-party redirects. Existing same-origin detail routes are static files;
   abandon any route known to redirect. Local browser verification must show
   zero third-party/script/image/API loads caused by the hint. If that cannot
   be demonstrated, leave the knob off and report the failed acceptance check.
7. One lifecycle owner tracks attempted URLs, queued intent, the idle handle
   and current hint. Cleanup timers/listeners/hints on pagehide; bfcache
   restore re-enables once without resetting attempts. Hidden state cancels
   queued work. New pub meta false disables it immediately. Repeated setup,
   focus/hover pairs, failed hints and DOM replacement never duplicate attempts.
   Hint failures have no visible error and never prevent real navigation.
8. No analytics events for hint/hover/focus. A hint must not execute the
   detail page's tracking or count a deal_detail_view, visit or affiliate
   click. No preference/target upload, additional scripts or loader packages.

## Tests

Capture same-checkout fixed-time output trees in support, open, closed
membership and shipped modes. Add Python config/publisher checks plus a Node
fake DOM/connection/idle/timer harness:

- Omitted/false, invalid settings, support-only gate and exact disabled tree/
  site_version equality. Enabled script only on intended pages and only once.
- Load/visible/4g/support/idle preconditions; absent APIs, Save-Data, slow type,
  hidden-before-dispatch, touch and short/transient intent cause zero hints.
- Valid title link; all forbidden origins/routes/encoded forms/queries,
  missing current-live signals, changed or removed cards cause no hint.
  Include Amazon Get deal, source trackers, mixed-store histories and auth
  links even if they resemble a card link. No hostname-suffix shortcuts.
- At most two distinct attempts, 1s pacing, no retries on errors/no events,
  duplicate hover/focus, reinit, pagehide/bfcache and feature disable.
- Original anchors/rel/targets and all click behavior stay unchanged; no
  tracking calls, HTML execution, image requests or persisted document data.
- Existing offline pipeline rerun-idempotency, focused deal-page/action,
  publisher, browse, privacy and support checks; then `python -m pytest -q`.
  Report exact failures and baseline verification; preserve unrelated pins.

Use an existing local browser and intercepted in-memory same-origin static
responses (no socket/server requirement) if available. Block all actual network.
Compare ordinary navigation with hinted navigation in that controlled fixture,
record whether bytes were reused, and verify no detail-view event before a
real navigation. Mock results are not a live speed claim. Check native Back,
keyboard, reduced motion and 360/390px layout. If the browser cannot launch or
does not honor hints, report that limitation and leave activation pending.
Target ≤2 KiB added gzip own JS, no initial request/first-card/LCP regression;
actual mobile LCP remains unmeasured without suitable existing lab results.

## Constraints

Ship OFF. This future behavior permits only bounded same-origin static HTML
hints; implementation/tests themselves use no network. No server, framework,
SPA router, login, dependency, Worker edit, paid call, source crawl, service
worker or custom cache. Keep robots/permissions/1 req/s/host, verified prices,
facts-only storage, savings.py math, config knobs, AI caps, encrypted state on
data and rerun safety. Preserve affiliate credit and direct navigation.
Keep `amazon_history: hide`, current Amazon date/time stamps/disclaimers, no
price tracking/history/alerts and no stored Amazon document snapshots. No
new third-party scripts, fingerprint or analytics event. Preserve existing
privacy/internal controls. WCAG 2.2 AA and phone budgets apply; all actions
remain available without hints. No commit, push, deployment or network
measurement. Never delete other /tmp directories. Update HANDOFF with code,
checks and browser/cache limitations; leave actual activation to later review.
