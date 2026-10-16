# Clone Wars: useful code patterns for wowpriced

Study date: 2026-10-16. Recommendation: keep Python publication and static HTML;
make returning visits more useful, preserve the reader's place, and make small
changes safer to ship. None of the inspected code establishes a retention lift.
The benefits below are hypotheses to measure, not promises of lifelong visitors.

## Scope and evidence

Read CLAUDE.md, AGENTS.md, all of docs/HANDOFF.md, the task, and the local source
files cited below. No network, installs, reference-repository execution, product
changes, commits or deletion of temporary directories were needed. This task
writes only this study and briefs 201–206. The task's named style reference,
`docs/codex-tasks/121-safety-real-numbers.md`, is absent in this checkout; the
briefs use the requested Why / Task / Tests / Constraints sections and the
read-first, narrow-scope style of `docs/codex-tasks/074-disclaimer-page.md:8`.
HANDOFF is read as operating context; its older completion notes do not prove
the present live site's behavior. Findings here concern this local checkout.

All reference paths below are relative to `/home/user/`; wowpriced paths are
relative to this worktree. Numbers after colons are inspected source lines.
New filenames and configuration names are explicitly marked proposed.

| Reference checkout | Local HEAD |
|---|---|
| burhan-syed/troddit | eafe83d59201 |
| sebst/pythonic-news | 3a6d03985f40 |
| d11z/asperitas | d445b0a45797 |
| th3wall/fakeflix | 2ef25d14a112 |
| kirkwat/tiktok | 906c9bb0e8f1 |
| muzam1l/tclone | e669ab028998 |
| eknoorpreet/dev.to-clone | 9403220e7f07 |
| emmanuelhashy/amazon-clone | b2727ce09676 |
| plausible/analytics | 598928e7db29 |
| thedevs-network/kutt | 279b491b53bb |
| unleash/unleash | 89c58bafbbdd |
| gorvgoyl/clone-wars | f005e8a2d999 |
| wowpriced | 017cbd739e96 |

The catalog is discovery material, not evidence that a clone reproduces its
namesake's infrastructure. `gorvgoyl/clone-wars/README.md:68`, `:76`, `:84`,
`:105`, `:121`, `:137`, `:146`, `:169`, `:171`, `:207` and `:229` point to the
studied projects. No conclusions are drawn about repositories only linked there.
These are pattern adaptations in our own code, not instructions to copy source,
assets, branding or dependencies. Source reuse would need a separate license review.

## Mandatory acceptance gates

Every ADOPT/ADAPT entry below carries **G**, meaning all eight gates apply;
the additional gate named on an entry addresses its particular failure mode.
ADOPT means a suitable pattern, sometimes already present; ADAPT means only
the stated restricted translation. Neither verdict authorizes implementation
in this documents-only task. SKIP means do not build that source behavior.

| Gate | Required check in every implementation |
|---|---|
| G1 — polite access | No new retailer/source crawling, bot-wall bypass, login bypass or headless partner browsing. Existing robots, host permission, 1 request/s/host, validators and backoff stay. 204 alone proposes bounded first-party navigation hints; never prefetch Get deal/source/image/API URLs. |
| G2 — price evidence | Consume only the public publish; never promote rejected, hidden, locked or absent offers. `dealradar/parse/extractor.py:101` remains the evidence gate; `dealradar/rank.py:398` remains publication authority. No UI gesture confirms a price. |
| G3 — facts and math | Store only required facts/opaque identities; no scraped post bodies or copied images. Price/savings calculations remain in `dealradar/savings.py:263`; browser code only selects and formats published values. No inferred stock, savings, popularity, purchasing or claimed success. |
| G4 — controls and cost | New behavior defaults off through validated config.yaml knobs, not query-string overrides. No new LLM/Keepa call path, altered AI caps, intake schedule, revenue preference or social send. Existing config in `dealradar/config.py:443` remains the entry point. |
| G5 — repeat safety | Same publish/run, repeated setup, bfcache, storage events and retries add no duplicate rows, cards, listeners, notices or sends. Keep `tests/test_e2e_offline.py` rerun-idempotency coverage. State stays encrypted on data; no new state.db in the product branch. |
| G6 — Amazon restriction | Treat the task's restriction as binding, not as a new legal opinion: no Amazon tracking, price alerts or price history. Keep `config.yaml:122` at hide; use `dealradar/verify.py:369`/published Amazon identity, not a retailer-name-only test. Existing current prices keep their as-of stamp/disclaimer (`dealradar/publish/amazon_as_of.py:7`, `:22`). Revisit/follow/prefetch records contain no prices. No Amazon-only history pages, caches of Amazon prices, watch targets, price comparisons or price-change announcements. |
| G7 — privacy | No new third-party script, service or identifier. Category choices, read markers and preference records stay on device and out of URLs, analytics and APIs. Storage denial/corruption gets an in-memory or ordinary-feed fallback; clear/reset is visible. Existing GPC/opt-out/internal gates remain. |
| G8 — access and phone cost | WCAG 2.2 AA: native keyboard controls, meaningful names, visible unobscured focus, status announcements, no color-only states or motion dependence. Use the project's 44px hit areas, 16px inputs, 360px wrapping and reduced-motion support. No framework/carousel dependency. Proposed release budgets: mobile LCP ≤2.5s, CLS ≤0.1, INP ≤200ms when measured; deterministic byte budgets do not prove those runtime results. No initial image priorities/dimensions or first-card position regressions. |

G6 applies to Amazon offers even when a source labels their store differently,
and to mixed-store product histories. Existing local Saved records still accept
baseline fields at `dealradar/publish/templates/engagement.js:227`; their Amazon
watch display/matching is suppressed at `:288`, `:329` and `:458`. Do not
interpret suppressed display as permission to accumulate new Amazon history.
The proposed identity-only records avoid extending that issue. Any later Saved
storage cleanup must explicitly test old records and current-price rendering.

## Techniques found in the code, with exact translations

### 1. troddit — four techniques

**T1. Update existing identities; stage new entries.**
Source: `burhan-syed/troddit/src/hooks/useFeedPosts.tsx:46` updates a Map keyed
by post name; `:134` and `:139` offer an explicit update action for new posts.
This separates fact refresh from disruptive insertion, preserving reading context.
Ours: `dealradar/publish/templates/index.html:3274` applies a publish and `:3345`
reorganizes the wall. **ADAPT, G:** 202 retains visible identity order while the
reader is deep in the feed; latest facts/removals apply immediately, only new
identities/reordering wait for a button. Never freeze an old price or live link.

**T2. Cursor and identity memory belong to the feed request.**
Source: `burhan-syed/troddit/src/hooks/useFeed.tsx:242` returns after/count and
seen identities; `:264` configures the query and next-page cursor at `:278`.
This avoids duplicate feed entries across pages and gives one pagination owner.
Ours: `dealradar/publish/templates/index.html:911`, `:2129` and `:2140` already
page the downloaded public list in groups of 60. **ADAPT, G:** keep that local
pagination; use an identity map in 202 rather than inventing a feed server or
copying infinite query cache lifetime/refetch intervals. A publish is a full
snapshot: missing identities must be removed, not retained by append-only merge.

**T3. Restore an identity, not just an absolute pixel coordinate.**
Source: `burhan-syed/troddit/src/components/FeedMasonry.tsx:159` stores a clicked
post and `:169` finds it in the current list before scrolling. That survives
insertion above the reader better than a bare scrollY.
Ours: `dealradar/publish/templates/browse.js:69`, `:172` already store a key,
offset and nearest surviving fallback. **ADOPT, G, already present:** reuse this
contract for 202/203; do not add masonry/virtualization or a second scroll owner.

**T4. Resource loading depends on intent.**
Source: `burhan-syed/troddit/src/components/media/video/VideoHandler.tsx:901`
controls autoplay, with conditional auto/none preload at `:908`. The empty
effect at `:200` is a comment, not an implemented prefetch engine.
Ours: `dealradar/publish/templates/deal-actions.js:61` owns native navigation
actions; `dealradar/publish/templates/index.html:3451` includes it.
**ADAPT, G:** 204 translates the loading policy to two maximum same-origin
public HTML prefetch hints after intent and page load. This is a new web
adaptation, not a claim that troddit already implements HTML intent prefetch.
No intercepting navigation, retailer requests, document execution or price cache.

### 2. pythonic-news — three techniques

**P1. Compute ranking once, close to the authoritative data.**
Source: `sebst/pythonic-news/news/views.py:63` computes points/age ranking,
selects related users and slices the result; `:116` caches the front-page query.
A shared calculation gives HTML and downstream consumers the same result.
Ours: `dealradar/rank.py:187`, `:262`, `:328` publish pick order, but
`dealradar/publish/templates/index.html:1498` and `:1540` also reorder it.
**ADAPT, G:** add shared Python/Node order fixtures first (top 10 item 10);
preserve server checkout-price spacing. Do not import voting or its age formula:
first-seen age is not expiry and a stale confirmation cannot become fresh rank.

**P2. Duplicates point at one canonical story.**
Source: `sebst/pythonic-news/news/receivers.py:26` records an exact-URL duplicate;
`sebst/pythonic-news/news/views.py:317` redirects a duplicate detail request.
One landing page concentrates navigation and avoids showing the same story twice.
Ours: `dealradar/db.py:580` inserts deduped offers and
`dealradar/publish/seo.py:740` manages stable deal pages.
**ADAPT, G:** test canonical-path reuse for the same offer identity. Retain
separate variants, conditions, retailers, source credits and tracker ownership.
No new redirects based solely on similar titles; do not copy the auto-upvote
side effect at source `receivers.py:33`.

**P3. Syndication reuses the same selection functions.**
Source: `sebst/pythonic-news/news/feeds.py:7` uses _newest; `:35` uses _front_page.
Subscribers get an alternative daily route into the same selected content.
Ours: `dealradar/publish/traffic.py:91`, `:107`, and `config.yaml:281` already
publish a trimmed 20-item feed. **ADOPT, G, largely present:** expose that feed
better from matching public hubs before adding feeds (top 10 item 9). Keep the
operator's trim, source credit and click-through details. Any future Amazon
feed prices require the same date/time qualification; no historical feed archive.

### 3. Asperitas — three techniques

**A1. Replace an existing vote rather than blindly incrementing.**
Source: `d11z/asperitas/server/models/post.js:45` subtracts the existing user's
vote, then replaces or removes it before saving. Repeated actions converge on
one state; that is useful for reliable Save/follow controls.
Ours: `dealradar/publish/templates/engagement.js:344` rejects duplicate saves;
`:362` removes by identity. **ADAPT, G:** explicit set-followed/set-caught-up
commands in 201/203 must be no-ops when repeated. No public votes, accounts,
server tally or invented community popularity. The source method is not proof
of concurrent database safety; our local reducer still needs multi-tab tests.

**A2. Serialization is a boundary.**
Source: `d11z/asperitas/server/models/post.js:31` transforms exported objects,
removing _id and __v. It separates database representation from client data,
but is a denylist, not proof that all private fields are excluded.
Ours: `dealradar/publish/site.py:586` explicitly serializes public facts and
`dealradar/publish/templates/index.html:3217` validates the public payload.
**ADAPT, G:** retain our explicit public-field contract; make an additive
contract test for Amazon identity/as-of and opaque locked stubs before another
field is added. Do not publish all DB fields or local preferences by spreading
an object. This supports weaknesses W3/W7 below, not a new API service.

**A3. Raw score ordering is not hot ranking.**
Source: `d11z/asperitas/server/controllers/posts.js:26` and `:31` sort only
by descending score; no age/decay term exists in those list handlers.
Ours: `dealradar/rank.py:187` already scores deal quality and freshness.
**SKIP:** adopting the clone's list order would substitute unverified voting
for evidence and lets old popular items dominate. Its name in the task is
a search lead, not evidence of an implemented hot algorithm.

### 4. Fakeflix — three techniques

**F1. Declarative, named shelves.**
Source: `th3wall/fakeflix/src/hooks/useRetrieveData.js:26` turns config entries
into row descriptors; `th3wall/fakeflix/src/pages/Homepage/Homepage.jsx:20`
renders rows, and `th3wall/fakeflix/src/components/Row/Row.jsx:91` links each
title to Show all. Relevant shelves give visitors a familiar starting point.
Ours: `dealradar/publish/templates/index.html:2031` assembles the wall and
`dealradar/publish/templates/browse.js:32` identifies categories.
**ADAPT, G:** 201 adds one explicit Followed view over the existing card grid,
using a set of followed category slugs. No horizontal carousel, extra hero,
banner, duplicate first-screen cards, Swiper, Firebase or inferred interests.

**F2. Favourite membership is idempotent by item id.**
Source: `th3wall/fakeflix/src/redux/favourites/favourites.utils.js:1` adds only
if absent; `:9` removes only if present. A durable list gives a reason to return.
Ours: `dealradar/publish/templates/engagement.js:235`, `:344`, `:445` already
handle bounded Saved records and current matches. **ADOPT, G, already present:**
preserve it; later local labels are incremental (top 10 item 7). Do not build
a second wishlist or save a clone's image/price snapshots, particularly Amazon.

**F3. Observe the end of content instead of polling scroll position.**
Source: `th3wall/fakeflix/src/hooks/useLazyLoad.js:8` calls a callback on
intersection and `:20` unobserves on cleanup. Bounded work supports a smoother
phone feed, although the hook alone does not guarantee request deduplication.
Ours: `dealradar/publish/templates/index.html:2129` already observes Show more,
with the explicit button at `:2110`. **ADOPT, G, already present:** test observer
rearming/cleanup in 206; retain keyboard pagination and the Trending auto cap.
Do not replace it with scroll listeners or remove older cards from the DOM.

### 5. TikTok — three techniques

**K1. Bound mounted feed work.**
Source: `kirkwat/tiktok/frontend/src/screens/feed/index.tsx:104` configures
FlatList with windowSize 4, initial 2, batch 2 and stable item ids. Small mounted
windows reduce media work; this is React Native code, not browser virtualization.
Ours: `dealradar/publish/templates/index.html:911`, `:2009`, `:2140` page cards
but rebuild content on render. **ADAPT, G:** use 206's resource/byte budgets and
measure successive Show more renders first. Defer CSS content-visibility or
incremental DOM work until focus, find-in-page and Back checks prove it safe.
Do not copy full-screen snapping, removeClippedSubviews or inaccessible windowing.

**K2. Only visible media is active.**
Source: `kirkwat/tiktok/frontend/src/screens/feed/index.tsx:58` plays viewable
cells and stops those leaving view. It avoids spending battery on unseen work.
Ours: `dealradar/publish/templates/index.html:3118` already observes tab
visibility; `dealradar/publish/templates/deal-actions.js:27` refreshes clocks.
**ADAPT, G:** later pause cosmetic/timestamp DOM work while hidden and paint
once on return, reusing refreshOnReturn. Do not delay evidence/safety updates,
add autoplay/video, or describe a visible card as engagement/purchase evidence.

**K3. Explicit teardown owns expensive resources.**
Source: `kirkwat/tiktok/frontend/src/components/general/post/index.tsx:32`
unloads on unmount; `:93` releases the video instance. Cleanup prevents repeated
navigation from accumulating hidden work.
Ours: `dealradar/publish/templates/index.html:2011` disconnects the observer;
`dealradar/publish/templates/deal-actions.js:124` coalesces layout callbacks.
**ADAPT, G:** 204 owns exactly one hint queue and cleans timers/listeners on
pagehide, with bfcache reactivation. No media subsystem or background workers.

### 6. tclone — three techniques

**TW1. One entity store, multiple timeline views.**
Source: `muzam1l/tclone/src/features/posts/postsSlice.js:11` normalizes by
id_str, while `:31` fetches pages into that store. Different views can reference
the latest facts without keeping separate inconsistent post copies.
Ours: `dealradar/publish/templates/index.html:3274` applies public snapshots;
`dealradar/publish/templates/engagement.js:284` resolves Saved against them.
**ADAPT, G:** 202 separates a current key→deal map from a display-key sequence.
Every card, Saved match and CTA must resolve current facts, never the staged
snapshot. Avoid introducing Redux or persisting that map in localStorage.

**TW2. Loading state guards repeated refreshes.**
Source: `muzam1l/tclone/src/features/notify/notifySlice.js:45` polls every 15s,
with a loading guard at `:59`; `:82` upserts by notification identity.
Ours: `dealradar/publish/templates/index.html:3120` and `:3405` already coalesce
return/initial requests, but other publish paths exist at `:3324` and `:3371`.
**ADAPT, G:** 202 tests overlapping poll/drop/return answers and stale-response
rejection; retain the existing cadence. The socket block at source `:15` is
commented out: do not call this working WebSocket delivery or add a server.

**TW3. Push requires permission and a subscription backend.**
Source: `muzam1l/tclone/src/subscription.js:6`, `:23` and `:87` request browser
permission, create a push subscription and POST it to an API.
Ours: `dealradar/publish/templates/pwa.js:46` owns install prompts, while
`dealradar/publish/templates/engagement.js:287` contains the existing watch gate.
**SKIP:** no push service, login or notification permission prompt. Amazon
price alerts are prohibited by this task; even non-Amazon push would introduce
subscription storage, delivery lifecycle and operator work absent from our stack.

### 7. Dev.to clone — three techniques

**D1. Follow topics directly.**
Source: `eknoorpreet/dev.to-clone/client/src/components/Tags/FollowTag.js:14`
derives followed state; `:22` sends follow/unfollow and changes the label at `:46`.
Explicit choice makes a return feed relevant without guessing interests.
Ours: `dealradar/publish/templates/browse.js:32` and
`dealradar/publish/templates/index.html:2031`. **ADAPT, G:** 201 stores only
allowlisted category slugs locally and provides Followed/All/reset. No account,
tag API, click/dwell inference or upload of the followed set. Prefer explicit
choices before considering any implicit category learning.

**D2. A reading list is its own first-class view.**
Source: `eknoorpreet/dev.to-clone/client/src/pages/ReadingList/ReadingList.js:13`
loads bookmarks and `:32` renders the list. The source needs auth; the useful
pattern is a familiar destination for previously chosen items.
Ours: `dealradar/publish/templates/engagement.js:445` already renders Saved.
**ADOPT, G, already present:** improve small empty/recovery states and optional
local labels later; keep unknown separate from confirmed-ended. No account
sync, server list, email or stale Amazon price restored from a saved record.

**D3. Request lifetime follows UI lifetime.**
Source: `eknoorpreet/dev.to-clone/client/src/hooks/useHttpClient.js:21`
attaches an AbortController; `:63` aborts active requests on cleanup.
Ours: `dealradar/publish/templates/index.html:3207` fetches publishes and
`dealradar/publish/templates/deal-actions.js:61` receives link intent.
**ADAPT, G:** 204 cancels queued hints on hidden/pagehide and ignores obsolete
callbacks. Do not copy source `:35` verbatim: comparing reqCtrl with
!httpAbortCtrl does not remove the completed controller. Hints are optional,
so a stale hint must never mutate current public facts or block navigation.

### 8. Amazon clone — three techniques

**AM1. Named actions separate interaction from state transitions.**
Source: `emmanuelhashy/amazon-clone/src/components/product/Product.js:7`
dispatches ADD_TO_BASKET; `emmanuelhashy/amazon-clone/src/reducer.js:16` handles it.
This gives transitions one testable owner, but that add action permits duplicates.
Ours: `dealradar/publish/templates/engagement.js:344` and `:362` already centralize
Save/remove. **ADAPT, G:** use explicit, idempotent commands for category choices
and catch-up acknowledgement in 201/203. No shopping cart, checkout promise,
Amazon price snapshot or second state framework; retain our duplicate guards.

**AM2. Basket aggregation is commerce-specific.**
Source: `emmanuelhashy/amazon-clone/src/reducer.js:6` sums item prices in JS.
Its shopper benefit is a basket total; it is not verified deal mathematics.
Ours: `dealradar/savings.py:263` and `dealradar/publish/templates/engagement.js:78`.
**SKIP:** wowpriced does not sell a multi-retailer basket. No projected basket
savings, spend totals or new client price arithmetic; published savings already
come from Python. A click is not a purchase or realized saving.

**AM3. Empty state and selected-item detail share a journey.**
Source: `emmanuelhashy/amazon-clone/src/components/checkout/Checkout.js:14`
shows empty guidance; `:24` renders selected items with CheckoutProduct.
Ours: `dealradar/publish/templates/engagement.js:445` and
`dealradar/publish/seo.py:607` already cover Saved and deal details.
**ADAPT, G:** offer a return to All from empty Followed/Catch-up views using
the current renderer (201/203), without implying unavailable items are live.
`emmanuelhashy/amazon-clone/src/App.js:48` defines login/checkout/home routes;
there is no production product-detail architecture here to transplant.

### 9. Plausible — four techniques

**PL1. Named ingestion stages can stop processing.**
Source: `plausible/analytics/lib/plausible/ingestion/event.ex:137` lists stages;
`:160` halts on dropped events, with drop telemetry at `:123`. Explicit boundaries
make failures observable rather than silently allowing partial invalid data.
Ours: `dealradar/publish/site.py:1020` and `:1153` order publisher passes.
**ADAPT, G:** 205 starts with one strict template slot and a failure reason;
later name the passes while retaining their order. Do not move verification
into presentation, relax the evidence gate or install an analytics backend.

**PL2. Exclude automation before calling activity human.**
Source: `plausible/analytics/lib/plausible/ingestion/event.ex:263` drops recognized
bots/Headless Chrome; `plausible/analytics/tracker/src/track.js:22` excludes local
and automated contexts and `:41` reads the local ignore flag.
Ours: `cloudflare/subscribe-worker/clicks.js:57` already gates anonymous clicks;
`dealradar/publish/tracking.py:105` tags internal GA4 traffic.
**ADAPT, G:** top 10 item 8 documents eligible samples and reuses existing
GA4/internal controls. Bot signatures are imperfect; do not label surviving
requests unique people or retrofit IP storage. Internal GA4 marking needs the
operator's GA4 filter before it proves exclusion.

**PL3. Anonymous uniqueness has a limited definition.**
Source: `plausible/analytics/lib/plausible/ingestion/event.ex:566` hashes user
agent, IP and site using a salt; `plausible/analytics/lib/plausible/session/salts.ex:64`
rotates salts, with old-salt cleanup at `:90`. That limits linkability, but is
not identity-free and does not prove long-term returning-person retention.
Ours: `dealradar/growth/cloudflare_traffic.py:1` sums daily uniques;
`dealradar/growth/google.py:313` already reports classified returning sessions.
**SKIP the fingerprint:** no IP/UA hashes or server visitor ids. Use the
existing classified-session report with unknowns shown separately; neither
Cloudflare totals nor D1 deal clicks can measure a seven-day user cohort.

**PL4. Compare built variants in compressed and raw bytes.**
Source: `plausible/analytics/tracker/compiler/analyze-sizes.js:49` reports three
size forms; `:93` compares baseline/current variants and percentage increases.
This catches shipping-cost regressions hidden by source-code size alone.
Ours: `tests/test_pwa.py:19` defines four configurations;
`scripts/lighthouse_summary.js:28` currently gates accessibility/SEO scores.
**ADAPT, G:** 206 adds deterministic published-asset budgets and an explicit
LCP lab threshold using existing reports. Use standard-library gzip, not
ClickHouse/building Plausible; artifact bytes and runtime vitals stay separate.

### 10. Kutt — three techniques

**KU1. Resolve the destination before doing optional statistics.**
Source: `thedevs-network/kutt/server/handlers/links.handler.js:473` looks up the
link, `:486` handles banned links, `:531` queues statistics and `:544` redirects.
The structural lesson is to keep navigation simple and side work separate;
the source is not proof that all queue failures are fail-open.
Ours: `dealradar/publish/site.py:141` rewrites affiliate destinations once;
`dealradar/publish/templates/deal-actions.js:78` records outbound actions
without preventing navigation. **ADOPT, G, already present:** keep direct
anchors in 204 and later share work. No /go redirect service, tracker unwrap,
merchant-link prefetch or credit rewrite outside rewrite_affiliate.

**KU2. Reject recognized bots before visit work.**
Source: `thedevs-network/kutt/server/handlers/links.handler.js:532` uses isbot
before the normal redirect path enqueues a visit.
Ours: `cloudflare/subscribe-worker/clicks.js:62` rejects known automation.
**ADAPT, G:** retain that defense in scorecards (item 8); a bot pass is a
heuristic, not proof of a human. Do not copy IP/geolocation queue data from
`thedevs-network/kutt/server/queues/visit.js:15`. The protected redirect at
`links.handler.js:564` also shows why a guard on one path is insufficient.

**KU3. Aggregate by UTC hour inside a transaction.**
Source: `thedevs-network/kutt/server/queries/visit.queries.js:15` derives an hour;
`:18` locks/updates or inserts aggregate data in a transaction. Compact reporting
reduces storage and permits useful totals without a row per click.
Ours: `cloudflare/subscribe-worker/clicks.js:6` already uses an atomic capped
hour upsert; `dealradar/clicks.py:70` imports absolute aggregates idempotently.
**ADOPT, G, already present:** keep this design; document unavailable/capped
hours in item 8. No extra queue/database, per-click rows or retry incrementing
the same downloaded hour; aggregates count actions, not conversions or people.

### 11. Unleash — four techniques

**U1. Rollout strategy has explicit stickiness and grouping.**
Source: `unleash/unleash/src/lib/features/feature-toggle/default-strategy.ts:3`
defines rollout, stickiness and groupId; `:28` resolves project defaults.
Stable assignment avoids experiences changing on every refresh. This file
configures a strategy; it does not itself implement percentage hashing.
Ours: `dealradar/config.py:443` resolves settings and
`dealradar/publish/site.py:890` emits bounded feature metadata.
**ADAPT, G:** begin with off/on config and one feature at a time. If cohorts
are later justified, a device-local random bucket must never be sent as an ID;
no remote Unleash SDK, service or experiment override of evidence/Amazon gates.

**U2. Local false is not necessarily a kill switch.**
Source: `unleash/unleash/src/lib/util/flag-resolver.ts:58` falls through from
a false local value to the external resolver; `:67` similarly resolves variants.
That is useful layering there, but dangerous if copied as an emergency stop.
Ours: `dealradar/config.py:591` handles amazon_history and `:776` engagement.
**SKIP that precedence:** a wowpriced false/off must terminate evaluation.
No external override or cohort can restore a disabled shopper feature, history,
extra spending, social send or access mode.

**U3. Validate lifecycle and environment before activation.**
Source: `unleash/unleash/src/lib/features/feature-toggle/feature-toggle-service.ts:1953`
checks project/environment and `:1969` rejects archived features.
This makes invalid activation visible before users get a broken combination.
Ours: `dealradar/config.py:431`, `:591`, and
`dealradar/publish/site.py:1047` resolve switches and variants.
**ADAPT, G:** 205 documents the one migrated slot's dependencies/order; add
focused compatibility cases rather than a universal flag platform. Later
feature records should name owner, default, interactions and removal condition.
Keep operator-approved switches; do not silently turn existing features off.

**U4. Fakes implement the real storage contract.**
Source: `unleash/unleash/src/lib/features/feature-toggle/fakes/fake-feature-toggle-store.ts:18`
implements the store interface and concrete archive/stale operations.
Tests can exercise stateful behavior without live services or pervasive mocks.
Ours: `tests/test_e2e_offline.py:27` already uses a fixture HTTP handler;
`tests/test_support.py:27` provides a publisher fixture.
**ADOPT, G:** 201–206 use fake storage, timers, requests and real local publish
fixtures; compare observable results. No new framework, reference project
execution, localhost socket dependency or test that merely repeats implementation.

## Seven habits: present capability and the remaining gap

| Habit and shopper benefit | Present in our code | Missing / recommendation |
|---|---|---|
| Catch up in a minute | `dealradar/publish/templates/engagement.js:43` freezes last-visit new keys and `:70` shows a count/tag; `:172` writes departure time. | 203 makes the count actionable: an explicit Catch up view, identity-only reviewed markers, completion and Back preservation. Do not claim unviewed cards were read or compare Amazon prices. |
| Return for the next real drop | `dealradar/publish/templates/index.html:1101` anchors clock marks; `:3345` presents a publish, `:3367` waits for a real one. | 202 stops deep-scroll reshuffling while preserving the real clock and fresh facts. No fake scarcity, daily streak pressure or new polling cadence. |
| Find my departments immediately | Categories/filters at `dealradar/publish/templates/browse.js:32`, wall at `dealradar/publish/templates/index.html:2031`. | 201 explicit local category follows, one view, one reset; no implicit profile. More ambitious learning waits for evidence that explicit follows are useful. |
| Revisit a chosen deal | `dealradar/publish/templates/engagement.js:192`, `:272`, `:445` implement bounded Saved, reconciliation and unavailable states. | Already substantial. Later labels/organization can help; first keep new local records free of Amazon baseline/history fields. No second wishlist or new price-alert service. |
| Open detail, return to the same place | `dealradar/publish/templates/browse.js:172`, `:197` restore by key after current-data refresh; `dealradar/publish/templates/deal-actions.js:61` preserves native anchors. | 204 tests conservative same-origin intent hints; 202 preserves anchors through updates. No SPA router. Normal navigation remains the dependable fallback. |
| Keep an easy home-screen route | `dealradar/publish/pwa.py:59` publishes manifest/icons; `dealradar/publish/templates/pwa.js:20`, `:46`, `:72` cover supported install/iOS help. | No service worker/offline deals, and install-event availability varies. Keep this deliberate boundary: an offline fallback, if ever built, may contain only price-free help/navigation and must never cache deal HTML/JSON/images or member/API traffic. Not a top-six task. |
| Share a useful find, or follow the feed | `dealradar/publish/templates/traffic.js:15` native/copy/manual sharing; `dealradar/publish/traffic.py:91` RSS; hub navigation at `dealradar/publish/seo.py:386`. | Better contextual discovery of existing actions, not new social SDKs or automated sends. Keep price-free share payloads, truthful handoff/copy wording and trimmed RSS. Arrival actions do not prove a new person. |

The proposed repeat loop is choose departments → browse current verified deals →
save an identity or share a link → return and catch up → resume the prior place.
Everything works without registration. There is no basis to promise addiction,
purchases, daily supply in a particular category, or measured retention gains.

## Architecture review: eight incremental changes, no rewrite

**W1. Assembly and version calculation know the same feature internals twice.**
Evidence: `dealradar/publish/site.py:70` strips optional blocks for a version;
`:875` repeats support-variant handling; `:1020` and `:1049` assemble markers.
Regex/string substitution can silently miss a renamed token; hashing source
can churn unrelated output. PL1/U3 suggest one owner per assembly operation.
First step: 205 migrates only BROWSE_SCRIPT to a counted slot/helper, uses the
same resolved script bytes for insertion and existing version inputs, and
rejects missing/duplicate tokens before output mutation. Keep exact output.
Risk medium: version compatibility. Payoff high: missing assets fail clearly;
future extraction need not duplicate another set of stripping rules.

**W2. Home behavior has a wide shared mutable scope.**
Evidence: `dealradar/publish/templates/index.html` is 3,453 lines; state starts
at `:917`, rendering at `:2009`, auth at `:2254`, publish logic at `:3200`.
Externalized engagement/browse scripts still execute within the home closure.
K3/D3 suggest explicit lifecycle ownership, not a React rewrite. First step:
put each new feature in one small vanilla module with init/update/dispose and
only the callbacks it needs; migrate existing code only in a separate byte-equal
change. Risk medium: order/global coupling. Payoff high: smaller review surface,
fewer duplicate listeners and independently testable request lifetimes.

**W3. Two ranking/rendering paths can disagree on safety-sensitive order.**
Evidence: `dealradar/rank.py:262`, `:328` select and order picks;
`dealradar/publish/templates/index.html:1498`, `:1540`, `:2056` reorder groups;
`dealradar/publish/seo.py:166` renders static cards separately from
`dealradar/publish/templates/index.html:1731`. Checkout spacing has a special
browser bypass at `:1552`. P1/TW1 suggest an authoritative order contract.
First step (item 10): shared fixed JSON fixtures for Python and Node, testing
keys, section boundaries, condition/photo/freshness grouping and checkout windows.
Then remove only demonstrably redundant ordering. Do not move money math to JS.
Risk medium: operator's drop behavior. Payoff high: static/live agreement without
rewriting either renderer; do not require identical CSS/HTML strings for a test.

**W4. Whole-site hashes obscure the reason for a failure.**
Evidence: `tests/test_pwa.py:19` sets many historical switches;
`:52` hashes a tree and `:58` embeds four opaque digests. HANDOFF records repeated
stale-pin repairs, but this study does not re-run or endorse those results.
PL4/U4 suggest inspecting produced variants. First step: keep every existing
pin; add a baseline/candidate file-manifest comparison with changed filenames,
gzip deltas and first difference for fixtures touched by a feature. Add semantic
assertions for prices/links/locked payloads. Never re-pin to hide a failure.
Risk low: test-only. Payoff high: reviewable intended changes, reliable off-mode
proof. Changing the snapshot strategy broadly is not in any top-six brief.

**W5. Switch defaults and cross-feature relationships are spread out.**
Evidence: `dealradar/config.py:82` defines AppConfig, `:443` loads config,
`:776` normalizes engagement, while `dealradar/publish/site.py:890` derives
support-dependent flags. This is a concrete dual-default/interaction burden,
not a reason to install Unleash. U1/U3 suggest explicit resolved policy.
First step: validate each proposed feature's bounded settings once; emit only
resolved settings, with omitted = false. Record its dependencies in its module
and test support/open/member modes. Later consolidate one feature family at a
time, preserving its current defaults. Risk medium: accidental default flips.
Payoff high: real kill switches and fewer undocumented switch combinations.

**W6. Multiple async entry points can present older data after newer data.**
Evidence: `dealradar/publish/templates/index.html:3120` coalesces return refresh,
`:3405` coalesces first load, but `:3324` polls and `:3371` starts another fetch;
`:3254` tests key inequality, not chronological freshness. This is an exposed
race surface, not a claim of a reproduced live failure. TW2/T1 suggest one
adoption decision. First step: 202 fixtures deliver A/B in reverse order,
reject regressive publish timestamps, and resolve current facts separately from
display keys. Equal timestamp/conflicting keys must have an explicit safe rule.
Risk medium: clock/auth/drop interactions. Payoff high: no old CTA/price revival
or repeated new-deal notice after overlapping responses.

**W7. Amazon policy is implemented across several UI surfaces.**
Evidence: `dealradar/publish/amazon_history.py:10`,
`dealradar/publish/templates/engagement.js:288`, `:458`,
`dealradar/publish/product_pages.py:91`, and
`dealradar/publish/amazon_as_of.py:22` serve different boundaries; local Saved
parsing at `dealradar/publish/templates/engagement.js:220` still accepts prices.
A2/U3 suggest a single derived capability contract. First step: add one fixture
matrix for Amazon identities (ASIN, URL-only, mislabeled store, mixed product)
across current card/detail, Saved, catch-up and product pages. Derive client
capabilities from publisher facts, never browser store-name heuristics. Treat
old Saved baseline cleanup as a separately scoped change; new features must
store identities only. Risk medium: accidentally removing legitimate current
prices or non-Amazon history. Payoff very high: fewer routes around hide mode.

**W8. Operational audits do not currently enforce a phone latency budget.**
Evidence: `scripts/lighthouse_summary.js:28` gates accessibility and SEO;
`scripts/mobile_audit.cjs:1` checks phone geometry/errors, and
`.github/workflows/mobile-audit.yml:38` executes that report. Performance scores
may be reported, but that predicate does not gate LCP. PL4/K1 suggest concrete
built-output and runtime limits. First step: 206 measures raw/gzip HTML, own
inline/external JS, CSS and JSON for fixed fixtures, plus finite LCP/CLS results
from the existing Lighthouse artifacts. Keep budget settings in config.yaml.
Risk low for byte checks, medium for noisy lab gates. Payoff high: catches
framework creep and payload growth before faster navigation gains disappear.

The 2,849-line `dealradar/pipeline.py` (run at `:2177`) also merits later
extraction of one pure phase at a time, following PL1. It is outside the first
six because those changes can improve shopper usefulness without disturbing
paid scheduling, evidence verification or the 12-minute fast job
(`.github/workflows/fast.yml:30`). Keep publication, SEO audit and IndexNow
sealing order at `dealradar/publish/site.py:1153` unchanged.

## Ranked top 10 and release sequence

Priority is an estimate of (retention benefit + free-traffic benefit) ÷
(implementation cost + failure risk). The 1–5 ordinal inputs below expose the
judgment; they are not measured lift or engineering-day promises. Retention
includes preventing broken or slow returning visits. Ties favor a smaller
shopper-facing change. Scores guide discussion, not automatic launch.

| Rank | Change / evidence | R | Traffic | Cost | Risk | Ratio |
|---|---|---:|---:|---:|---:|---:|
| 1 | Explicit local category follows — D1/F1 | 5 | 1 | 2 | 1 | 2.00 |
| 2 | Stable current-fact updates while reading — T1/TW1/TW2 | 5 | 1 | 2 | 2 | 1.50 |
| 3 | Actionable catch-up view — T3/A1/AM1 | 4 | 1 | 2 | 2 | 1.25 |
| 4 | Bounded intent prefetch of our detail HTML — T4/K3/D3 | 4 | 1 | 2 | 2 | 1.25 |
| 5 | Strict publisher slot, first migration only — PL1/U3/U4 | 3 | 2 | 2 | 2 | 1.25 |
| 6 | Phone asset and LCP budgets — PL4/K1 | 3 | 3 | 3 | 2 | 1.20 |
| 7 | Saved identity hygiene, then local organization — F2/D2 | 3 | 0 | 2 | 1 | 1.00 |
| 8 | Retention scorecard with honest sample definitions — PL2/PL3/KU3 | 2 | 1 | 2 | 1 | 1.00 |
| 9 | Contextual discovery of existing feed/share links — P3/KU1 | 1 | 3 | 2 | 2 | 1.00 |
| 10 | Python/browser order parity fixtures — P1/A2 | 2 | 2 | 3 | 2 | 0.80 |

1. **201 — Follow categories on this device.** A dedicated optional view uses
   current published cards and their existing order. One reset and a clear
   device-only note; no new hero, carousel, implicit profiling or API.
2. **202 — Keep the wall steady while facts update.** Separate current facts
   from visible identities. Reprices, removals and safety changes apply at once;
   the reader decides when to insert/reorder new deals. Preserve the real clock.
3. **203 — Catch up since last visit.** Turn the existing count into a useful
   filtered view and explicit reviewed checkpoint. Store keys/times only, with
   bounded retention; no Amazon price comparisons or alerts.
4. **204 — Open our deal details faster.** After load and clear hover/focus
   intent, issue at most two low-priority HTML hints; respect Save-Data and
   slow/unknown connections. Native navigation stays intact. Measure benefit
   before activation because browsers may ignore hints or not reuse the cache.
5. **205 — Fail clearly when a template slot breaks.** Migrate BROWSE_SCRIPT
   only, preserving every public byte/version in all access modes. This protects
   new features from silent missing-marker errors without a template rewrite.
6. **206 — Keep phone costs bounded.** Offline fixed-fixture asset reports plus
   finite LCP/CLS checks on existing lab results. Establish measured baselines
   during implementation; do not invent this checkout's performance numbers.
7. **Saved improvement:** first review/purge Amazon baseline fields without
   losing saved identities; then consider one local organizational label per
   item. Reuse the existing list, enforce its cap and keep unknown/ended distinct.
8. **Scorecard:** reuse `dealradar/growth/google.py:313` and
   `dealradar/growth/report.py:604`. Report returning/classified sessions,
   unclassified counts, device session outbound rate and existing save/share/feed
   actions. Existing data cannot establish feature causality, D1/D7 cohorts,
   purchases or unique people. No new tracker/fingerprint or raw-event ledger.
9. **Feed/share discovery:** add a small existing-feed link on relevant public
   hubs (`dealradar/publish/traffic.py:144`) and improve deal-page action labels
   using `dealradar/publish/templates/traffic.js:15`. Preserve the 20-item trim,
   disclosures, date-qualified Amazon current prices and price-free share text.
   No category-feed explosion, new SDKs, auto-sharing or newsletter activation.
10. **Order parity:** shared fixture expectations for
    `dealradar/rank.py:328` and `dealradar/publish/templates/index.html:1540`;
    compare safety groupings and checkout windows before removing any duplicate
    logic. Existing operator-approved rotation remains unless a separate brief
    explicitly changes it.

All ten recommendations inherit G. Items 7–10 need their own future briefs;
they are recommendations, not extra deliverables or authorization to implement.
Build 201–206 independently, one reviewed change at a time. Coordinate edits to
index.html/site.py with the owners of tasks 119–121 recorded in HANDOFF; rebase
against their accepted code, never undo it. No dependency between the six briefs
is required; where another feature already exists, test the combination too.

For each behavior change: capture a same-checkout disabled baseline before
editing, ship disabled, verify enabled fixtures, and use existing private
analytics only for aggregate observation after operator-approved activation.
A config kill switch takes effect after the next successful Pages deployment;
it is not an instantaneous remote switch and cannot revoke already downloaded
code. Do not add cohort identifiers merely to claim experimental causality.

Suggested observation order: phone performance/errors and stale-data safety
first; then existing GA4 classified returning-session share and outbound
session rate by device; then existing save/share/feed actions and Search Console
clicks. Name the sample, time window and exclusions. Missing data stays unknown;
release-to-release differences are descriptive, not proof of retention lift.

## Document validation and remaining work

Validation for this task is documentary: check all cited local paths/line
bounds, 3–6 techniques per implementation repository, G coverage, the ranked
six-to-brief mapping, study length, relative document links and a seven-file-only
diff. No product tests or live measurements are claimed by this study. Each
build brief specifies the offline and full-suite checks for its later session.
No product change, activation, source-code transplant or architectural rewrite
has been made. Remaining work is review, selection and later execution of the
briefs; actual mobile vitals, traffic and retention remain unmeasured here.

Build briefs:
[201](../codex-tasks/201-local-category-follows.md),
[202](../codex-tasks/202-stable-feed-updates.md),
[203](../codex-tasks/203-catch-up-view.md),
[204](../codex-tasks/204-intent-prefetch.md),
[205](../codex-tasks/205-strict-template-slot.md),
[206](../codex-tasks/206-phone-performance-budgets.md).
