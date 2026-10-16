# 202 — Keep the reader's place while current deal facts update

## Why

A new publish should not rearrange cards under a shopper deep in the wall.
troddit's keyed refresh/staged insertion and tclone's normalized entity store
suggest keeping display order separate from current facts. This must never
keep an old price, unsafe CTA or removed offer visible. Background:
`docs/research/clone-wars-study.md`, T1/TW1/TW2, rank 2.

## Task

Read CLAUDE.md, AGENTS.md and docs/HANDOFF.md first. Coordinate index.html/site.py
edits with tasks 119–121 and preserve their accepted code. No dependency on any
other 201–206 task; test combinations if they already exist.

Read `dealradar/publish/templates/index.html:2009` (render), `:2156`
(reorganization), `:3120` (return refresh), `:3207` (fetch), `:3254`
(new-publish check), `:3274` (apply), `:3324` (poll), `:3345` (present),
`:3371` (drop), `:3405` (first load), plus `browse.js:69`, `:172`, `:197`,
`engagement.js:272`, `dealradar/publish/site.py:890`, `dealradar/config.py:443`,
`tests/test_search_filters.py:37` (static fallback), `tests/browse_logic.cjs`
and `tests/test_publish_site.py:888` (clock/drop regression).

1. Add proposed `publish.stable_updates: {enabled: false}` with strict boolean
   validation and support-mode-only effective activation. Omitted/false and
   dormant membership output must match captured pre-edit bytes/versions.
   Emit the resolved setting only when effective-enabled; no new endpoint.
2. Add one small feature owner, proposed `templates/feed-updates.js` under
   `dealradar/publish/`. Maintain an in-memory latest public identity→facts map,
   the displayed key order and at most one latest pending desired order.
   Never persist prices, whole snapshots or this map in browser storage.
   Treat the public response as a complete snapshot, not an append-only feed.
3. Use the first currently visible deal card to decide stability: when its
   index in browseRows is ≥PAGE_CARDS, or a deal control/details editor has
   focus, preserve the displayed relative order. Otherwise keep normal drop
   presentation. This derives its threshold from the existing 60-card paging,
   not an unrelated hard-coded pixel distance. If task 203/201 views exist,
   preserve their active selection and use their current browseRows.
4. Accept the latest valid publish facts immediately. Update prices, as-of
   stamps, checkout requirements, affiliate destination and safety labels.
   Remove absent/locked/ineligible keys immediately; confirmed-gone identities
   can retain only the existing grey inert representation under existing
   spacing/lifetime rules. An unavailable/changed source must not be called
   dead without current public evidence. Recompute current filters and safety
   partitions: confirmed-first, checkout spacing, spotlight and gone-card
   constraints outrank visual stability. Preserve the nearest surviving key
   and viewport offset through the existing browse restoration helpers.
5. Stage only additions and otherwise-safe reorderings. Count genuinely new
   public live keys once from the latest snapshot, excluding gone/locked
   entries. Use one compact native button in the existing status area:
   “N new deals — show” when N>0, otherwise “Update deal order” if needed.
   Do not call a repriced Amazon identity new. Keep a polite bounded status
   announcement; no auto-focus or forced scroll. Activating applies the latest
   desired order exactly once, preserves the old reader anchor where possible,
   and announces completion. No stale intermediate snapshots in a queue.
6. Keep the existing real drop cadence, waiting state, publish clock, source
   requests, paid budgets and free rotation semantics. Reuse current fetches;
   no new polling/HEAD schedule. When staged, the clock still advances on an
   accepted publish; the optional presentation order waits for the user.
   Saved always reconciles against the newest public facts, not display keys.
7. Give all enabled-mode adoption paths one chronological check. A delayed
   older published_at cannot replace an already accepted newer publication.
   Same key is a no-op. Same timestamp/different key: retain the accepted
   version and wait for a later timestamp, with no new-deal announcement;
   never lexically rank opaque hashes. Reject malformed/missing timestamps
   for enabled-mode updates while retaining the last valid data and ordinary
   failure status. First-load failure must preserve the existing static wall.
   Reset request/presentation state correctly on pagehide/bfcache. Do not
   change dormant membership/auth paths in this session.
8. No new analytics events. Disabling the feature in accepted meta clears the
   staging UI and resumes the existing presentation path. It cannot roll back
   facts or apply an older queued payload. Normal filters/Show more still work.

## Tests

Before editing, capture fixed-time full published trees in support/open/closed
membership/shipped modes. Add offline publisher checks and a fake-timer/request/
DOM Node harness covering observable cards, focus, pending button and clocks:

- Omitted/false and non-support paths match every file/version; malformed knob
  values fail config validation. Enabled init twice installs one listener set.
- Two or more arrivals while deep in the feed coalesce into the latest order;
  repeated same-publish responses/notices do nothing. Button action twice does
  not duplicate cards, reorder again or announce twice.
- A displayed offer reprices in either direction, changes checkout steps,
  becomes unconfirmed, disappears, dies, locks or stops matching filters:
  facts/CTAs update immediately and safety grouping wins. Test multiple deaths
  against gone spacing; new eligible cards wait without holding stale facts.
- A→B responses delivered B→A, duplicate poll/drop/return, equal timestamp
  conflict, invalid timestamp, fetch error and first-load retry. Assert exact
  accepted key and facts; no old price/link revival. No extra fetches.
- Visible anchor, deleted anchor nearest fallback, focused code/Save/details
  control, manual Show more, search/filter changes and bfcache restore. Keep
  reduced motion and keyboard completion usable; no focus loss on safe updates.
- Mixed Amazon identity fixtures retain current as-of/disclaimer and never
  generate price-change history, targets, announcements or local records.
  Saved/current views resolve the latest public object throughout staging.
- Real clock before/on/after a mark, late publication and multiple skipped
  marks preserve cadence; no mark becomes a fabricated new publish.
- Run existing offline pipeline rerun-idempotency cases; focused clock,
  fallback, browse, Saved, checkout-price, Amazon and support tests; then
  `python -m pytest -q`. Report failures and baseline reproduction accurately.

Use only existing local browser tooling, with external requests blocked, for
360/390px/desktop, keyboard and light/dark screenshots if it can launch. Report
unmeasured checks if blocked. Target ≤4 KiB gzip additional own home JS and no
first-card displacement without a pending update; report actual bytes. Preserve
existing byte pins; do not silently refresh unrelated expected hashes.

## Constraints

Ship OFF. Narrow presentation/adoption change; no framework, rewrite, server,
login, extra LLM/Keepa calls, dependency, service worker or Worker edit. Keep
robots/permissions/1 req/s/host, evidence verification, facts-only records,
all savings arithmetic in savings.py, config knobs, AI caps, encrypted
data-branch state and rerun idempotency. Preserve affiliate/source credit,
stock/death logic, social/email cadence and accepted checkout-price spacing.
Keep `amazon_history: hide` and date/time-qualified current Amazon prices;
no tracking, history, watch alerts or persisted price payloads. No new
third-party scripts or preference/identity telemetry; existing privacy gates
stay. WCAG 2.2 AA, 44px controls, 360px wrapping and reduced motion apply;
measure phone cost without claiming runtime LCP from a byte count. No network,
commit, push or deployment. Never delete other /tmp directories. Update
HANDOFF with the exact changed files, tests and remaining review/activation
work; report any unresolved race or browser limitation plainly.
