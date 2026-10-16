# 201 — Follow departments on this device

## Why

Returning shoppers can choose their departments once and reach relevant deals
quickly. The Dev.to clone's explicit tag follow control and Fakeflix's named
rows suggest a small local view, using our existing grid. No measured retention
gain is assumed. Background: `docs/research/clone-wars-study.md`, D1/F1, rank 1.

## Task

Read CLAUDE.md, AGENTS.md and docs/HANDOFF.md first. Coordinate with the owners
of tasks 119–121 before editing their files; preserve their accepted changes.
This brief is self-contained and does not require tasks 202–206.

Read these implementation anchors:

- `dealradar/config.py:443`, `config.yaml:281`, `dealradar/publish/site.py:890`
  for validated settings and feature metadata/versioning.
- `dealradar/publish/templates/browse.js:32`, `:69`, `:99` for category
  slugs, tab snapshots and restore; `index.html:2031` for current wall rendering.
- `dealradar/publish/templates/engagement.js:192`, `:445` for bounded local
  records and Saved. Reuse the pattern, not its price-baseline fields.
- `tests/browse_logic.cjs`, `tests/test_saved_deals.py`,
  `tests/test_support.py`, `tests/test_pwa.py` for offline harnesses/modes.

1. Add proposed config `local_preferences: {enabled: false, max_categories: 6}`.
   Omitted means false. Validate enabled as a real boolean and max_categories
   as an integer 1–11; no string truthiness. Resolve once in config.py. Enable
   UI only in support mode. Disabled and dormant membership modes emit no
   new metadata, controls, script bytes or version change. Do not put a nested
   object into the existing boolean engagement normalization loop.
2. Add a small proposed `dealradar/publish/templates/category-follows.js`,
   included only when effective-enabled, with one init/update/dispose owner.
   Use existing category-group slugs and category membership; omit All,
   Trending, empty/unknown slugs and duplicate aliases. Do not infer categories
   from product titles or introduce new taxonomy/remote requests.
3. Put a native, labelled Follow departments disclosure/checklist in the
   existing Filters sheet. Checkboxes say what they select, work by keyboard,
   wrap at 360px and have 44px hit areas. Save explicit choices; show the
   maximum and explain it when reached. Include Clear followed departments
   and the plain note “Saved on this device.” No header/hero or first-card
   height increase in the initial default view.
4. Store proposed `wp.categoryFollows` as `{version:1, categories:[slug,...]}`.
   Allowlist strings against currently rendered category groups; dedupe, cap
   input length at 4 KiB and inspect at most 32 entries. No prices, products,
   clicked items, searches, targets, counts, timestamps or inferred weights.
   Missing/corrupt/unknown-version data becomes an empty set. Blocked or full
   storage allows an in-memory choice with a truthful session-only note.
5. Once a category is followed, expose a Followed view beside existing view
   controls, with a visible way back to All. Use the current public payload,
   existing card renderer and existing within-view order; apply followed
   membership as a filter, never a rank boost. Intersect current store/search/
   price/category filters. Preserve spotlight placement among matching items,
   avoid duplicate keys and retain existing labels on unconfirmed/aged offers.
   Do not place those offers into top picks or change promotion eligibility.
   With no matching live offers, explain it and offer All; never silently
   clear the user's choices or declare an absent offer ended.
6. Extend the existing tab-only browse snapshot with a bounded boolean view
   state, gated by this feature; defaults/legacy records remain valid. Keep
   the followed set and view out of share URLs, analytics, APIs and history
   payloads that leave the tab. Do not auto-open Followed on first setup or
   overwrite a shared URL's explicit filters. Back/bfcache restore current
   facts using the existing key/offset mechanism; another tab's storage event
   reconciles choices without taking focus or switching the active view.
7. Repeated initialization, publish adoption and identical checkbox actions
   must be no-ops for listener installation/storage writes. Respect a later
   disabled meta value immediately: remove controls and return to ordinary
   view without leaking preferences. No new analytics events in this task.

## Tests

Capture a same-checkout baseline before edits: fixed time, fixed deals and
published filenames/bytes in support, open, closed membership and shipped
config. All features changed by other owners remain at their accepted values.

Add offline Python publisher checks and a Node fake DOM/storage harness:

- Omitted/false, invalid booleans/limits, effective support-only gate and
  disabled byte-for-byte trees including site_version and deals.json.
- First choice, repeated set/unset, cap, Clear, aliases, malicious/oversized
  records, unknown versions, failed reads/writes and cross-tab changes.
- All/Followed, intersections, matching spotlight once, no-match recovery,
  search order, current unconfirmed labels, disappearing/gone offers and
  malformed current records. A preference cannot create a new offer.
- Back/Forward/reload/bfcache with old snapshots and current inventory;
  no preference fields in URLs, analytics payloads, fetches or serialized deals.
- One listener set after repeated init/update; no new network calls. Same
  input produces the same view and storage state. Include Amazon URL/ASIN/
  mislabeled-store fixtures: current as-of copy stays; no watches/history.
- Run the existing offline pipeline rerun-idempotency cases. Run focused
  browse/Saved/support/phone checks, then `python -m pytest -q`. If any Worker
  file unexpectedly changes, remove that scope; this task needs no Worker edit.

Check generated pages locally at 360/390px and desktop, light/dark, keyboard,
reduced motion, focus restoration and screen-reader labels when the existing
browser is available. Block all external requests. If browser execution is
blocked, report visual/focus QA as unmeasured. Target no first-card displacement
in ordinary view and ≤3 KiB added gzip own JS in enabled home; report actuals.
Do not re-pin unrelated snapshots. Report exact failed tests and baseline
reproduction rather than claiming a green suite.

## Constraints

Only this behavior and necessary config/publisher/tests/docs wiring; no
framework, server, login, service worker or dependency. Ship the knob OFF.
Keep robots/permissions/1 req/s/host, verified evidence, facts-only storage,
savings.py arithmetic, config-only knobs, AI caps, encrypted data-branch state
and idempotency intact. No new paid calls, source fetches or social/email sends.
Keep `amazon_history: hide`, current-price as-of stamps/disclaimers and Amazon
identification by publisher facts; never create Amazon tracking, alerts,
history or price-bearing preference records. Preserve affiliate routing,
partner credit, access modes and existing GPC/opt-out/internal controls.
No new third-party scripts or telemetry. WCAG 2.2 AA and phone budgets apply;
byte measurements do not prove the ≤2.5s mobile LCP target. No commit, push,
deployment or network use for implementation/verification. Modify only owned
scratch paths, never delete other /tmp directories. Update HANDOFF with files,
checks, limitations and remaining activation/review work; end with a short
summary. A later operator-approved config activation is a separate step.
