# 203 — Make “new since your last visit” actionable

## Why

We already show new-since-visit tags and a count. A returning visitor needs a
quick way to see those deals and know when they have finished reviewing them.
Use identity-based local state and our existing Back restoration; do not build
price watches or infer reading from scrolling. Background:
`docs/research/clone-wars-study.md`, T3/A1/AM1, rank 3.

## Task

Read CLAUDE.md, AGENTS.md and docs/HANDOFF.md. Preserve accepted work from tasks
119–121 and coordinate edits with their owners. This is independent of 201/202;
use their existing interfaces if present, without making them prerequisites.

Read `dealradar/publish/templates/engagement.js:43`, `:67`, `:70`, `:172`
(visit baseline, tags and pagehide), `index.html:2031`, `:3274` (render/current
publication), `browse.js:69`, `:99`, `:172` (snapshot and anchor),
`dealradar/publish/site.py:890`, `dealradar/config.py:776`,
`tests/engagement_logic.cjs`, `tests/browse_logic.cjs`, `tests/test_support.py`
and `tests/test_amazon_as_of.py`.

1. Add proposed `engagement.catch_up_enabled: false` and
   `engagement.catch_up_max_keys: 500`. Omitted means false; strict boolean,
   integer cap 50–1000. Add dedicated AppConfig fields/loader handling; the
   current boolean engagement loop must not silently choose this default.
   Effective activation requires support mode and existing new_since_visit
   enabled. Disabled/non-support output and versions match pre-edit bytes.
2. Reuse the current new-since-visit line, with an accessible Catch up button
   shown only when a valid prior baseline exists. First visit has no invented
   backlog/count. Use a small proposed `templates/catch-up.js` in the publisher,
   one feature owner and current card rendering. It must require no account,
   endpoint, extra publish request, new card shape or price arithmetic.
3. At first valid public adoption, capture a fixed window ending at that visit's
   start time. Start with the feature's prior checkpoint, otherwise valid
   wp.lastVisit; reject future/invalid times. Bound the window to the prior
   seven days and explain that limit in the view. Candidate keys are current
   public live unlocked offers whose first_seen_at is after the baseline and
   at/before the window end; missing timestamps do not count. This means
   “first seen since your last visit”, not every later price change or proof
   of first public appearance. Keep the same window through re-renders/drops.
4. Catch up filters existing current rows by those keys, intersects ordinary
   filters and uses their existing order, safety grouping and Show more.
   Include matching spotlight once; do not promote aged/unconfirmed offers
   into top picks or remove their labels. Always offer All. Distinguish no
   new candidates from no matches under current filters. Reprice, removal,
   hidden/locked and confirmed-gone changes resolve from the latest payload;
   never resurrect a saved card or label an absent offer ended.
5. Use explicit review, not viewport/dwell inference. “Mark shown deals
   reviewed” marks only the currently rendered, matching live candidate keys,
   including only the rendered page of a long list. Immediately remove their
   catch-up eligibility and preserve the nearest surviving anchor/focus.
   Repeated activation does nothing. When no unreviewed candidates remain
   across the entire unfiltered window, advance the checkpoint to the fixed
   window end and show “You're caught up.” No automatic timer, pagehide or
   filtered completion may acknowledge unseen keys outside that view.
6. Store proposed `wp.catchUp` as a versioned record containing a checkpoint
   timestamp plus reviewed `{key, at}` entries only. Validate timestamp/key
   bounds, dedupe and retain reviewed entries for at most seven days; cap
   serialized input at 128 KiB and inspected entries at the configured cap.
   When full, explain that review cannot be durably recorded; do not silently
   evict markers and reannounce already-reviewed entries. Keep the current
   session usable. Never import Saved price fields, item bodies, images,
   Amazon flags/targets, searches or category choices into this record.
7. Corrupt/unknown-version data gets ordinary new-since-visit behavior; storage
   denial gets a session-only catch-up state and note. Cross-tab changes
   reconcile reviewed keys without advancing beyond a tab's fixed window,
   taking focus or switching views. Use the existing browse snapshot for a
   bounded catch-up-view boolean and anchor, stored only in the tab; no private
   state in share URLs or external payloads. Include a Reset catch-up memory
   action with a truthful next-visit/ordinary-feed fallback.
8. Leave the existing wp.lastVisit/new-tag contract unchanged while disabled.
   Enabled Catch up uses its own reviewed/checkpoint state; don't change Saved,
   weekly finds, streaks, drop announcements or the count into a price alert.
   On disable, remove controls and use the normal wall. No new telemetry.

## Tests

Capture same-checkout fixed-time published trees before editing for all four
access/config modes. Add offline config/publisher tests and fake-clock/storage/
DOM Node cases for:

- Omitted/false, new_since_visit off, support-only effective activation and
  invalid limits/booleans; disabled bytes and versions all equal baseline.
- First visit, returning visit, seven-day cap, malformed/missing/future times,
  DST/local-day changes, duplicate keys and current payload replacements.
- Mark shown twice, more than one page, filtered subset with unseen candidates
  outside it, completion only after all current window candidates are reviewed,
  checkpoint/window races across tabs and bfcache reload/Back restoration.
- Corrupt/oversized/unknown-version records, full quota, read/write exceptions,
  marker cap and reset. At cap, no silent eviction/reannouncement loop.
- Reprice in either direction, gone/hidden/locked/absent keys, matching
  spotlight, unconfirmed labels and filter no-match recovery. Review records
  contain keys/times only and never cause an outbound click or offer creation.
- Amazon ASIN, URL-only and mislabeled-store offers keep existing current
  timestamps/disclaimers; no watch, history, target, old-price text or alert
  is introduced. Price changes alone never add a catch-up candidate.
- No new network or analytics calls, preference URLs or duplicate listeners;
  same input/publish/action sequence has identical records and visible keys.
- Existing offline pipeline rerun-idempotency; focused engagement/browse/Saved/
  support/Amazon checks, then `python -m pytest -q`. Name failures and verify
  any claimed baseline failures; do not re-pin unrelated output.

If existing local browser tooling launches, check 360/390px and desktop in
light/dark/reduced motion, keyboard review/reset/All and focus after the last
card disappears. Block external requests. Otherwise report visual/focus QA
unmeasured. Target ≤3 KiB added gzip own home JS; no initial ordinary-wall
height change. Report actual bytes and limitations, not inferred LCP results.

## Constraints

Ship OFF. No framework, server, login, new dependency, Worker edit, service
worker, paid call, source crawl or email/social send. Keep robots, permissions,
1 req/s/host, evidence verification, facts only, savings.py money math,
config-only controls, AI spend caps, encrypted state on data and idempotency.
Keep Amazon history hidden, current prices date/time-qualified and source
identity authoritative. No Amazon tracking, alerts or price-history storage;
the review record is identity/time only. Preserve affiliate credit, locked
stubs, ordinary ranking/drop cadence and existing privacy/internal controls.
No new third-party scripts or personalization telemetry. WCAG 2.2 AA, 44px
controls, 16px inputs, 360px wrapping, reduced motion and the ≤2.5s mobile LCP
target apply. No network, commit, push or deploy. Never delete other /tmp
directories. Update HANDOFF with changes, checks, limitations and what remains;
finish with a short summary and leave activation for later review.
