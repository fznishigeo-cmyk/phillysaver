# Codex task 064 (roadmap brief 1): keep the static deal wall on a failed first load

Source: docs/roadmap-beat-big-sites.md (task 063). Tasks 061 (spotlight, in flight) and 062 (price chart, shipped) are owned elsewhere; do not touch their code.


**Read first:** AGENTS.md, CLAUDE.md, docs/HANDOFF.md. Files:
`dealradar/publish/templates/index.html` (`fetchPublish`, `loadData`,
`loadedOnce`, `STATIC_WALL`), `dealradar/publish/seo.py::static_wall`,
`dealradar/publish/tracking.py`, `dealradar/growth/google.py`,
`dealradar/growth/report.py`, `tests/test_search_filters.py`,
`tests/search_filters_logic.cjs`, `tests/test_support.py`,
`tests/test_tracking.py`, `tests/test_growth_report.py`.

#### Why

The server delivers readable deals before JavaScript. A rejected first JSON
request currently destroys them. Phone visitors should keep those deals and
receive a small, truthful retry message.

#### Do

1. Preserve the existing static wall until a successful, validated public
   payload is ready. Reject non-OK HTTP responses, malformed JSON and an
   invalid `meta`/`deals` shape without emptying or partially replacing it.
   A valid empty deals array is a successful publish, not a network error.
2. Place one inline `role="status"` message outside the wall: “Live updates
   are unavailable. These are the last published deals.” Add a 44px Retry
   control using the current fetch path. If no static deals exist, show a
   neutral unavailable message rather than inventing inventory.
3. Avoid overlapping retry requests. A retry click and the existing
   fallback timer may recover, but there must be only one successful first
   adoption. Clear the message after success. Later refresh failures keep
   the last good data, drop cadence and existing membership logic.
4. Add GA4-only events `home_load_attempt`, `first_load_failed`,
   `static_fallback_shown`, `first_load_recovered`; once per page lifecycle
   for each applicable event, with no URL/error message/product parameters.
   Extend the fixed growth event query and render the `product` counts
   named in rank 1; absent credentials stay unavailable. Do not add new
   mappings to ad pixels or alter their privacy gates.
5. Keep ordinary static links/source credit working with JavaScript off.
   The fallback does not claim any new price check or offline availability.
   Keep desktop appearance unchanged on a successful first load.

#### Tests

Extend the offline Node harness through its pytest wrapper: failed fetch,
404/503, invalid JSON, invalid shape, valid empty array, static/nonstatic
fallback, successful retry, repeated Retry, timer/retry overlap, failure
after success and event counts. Assert the original static card links stay
usable after failure and success clears only the status message. Run
tracking/report tests for missing IDs, GA4-only dispatch and aggregate
counts; verify no raw exception or URL leaks. Exercise support and dormant
membership cases. Update byte-hash expectations only from intentional new
rendered output, never to hide a failure.

Run `.venv/bin/python -m pytest -q`. Verify the generated page on a 360px
phone, iPhone 14, Pixel 7 and desktop, blocking/delaying only our own JSON
request. If sockets/Chromium are blocked, name the affected checks and leave
visual verification explicitly pending. Add one HANDOFF bullet. Do NOT
commit or push; end with what changed and anything unsure.

Run `.venv/bin/python -m pytest -q`; all green except tests needing local
sockets your sandbox blocks (name them). One short HANDOFF bullet. Do NOT
commit or push. End with what changed and anything unsure.
