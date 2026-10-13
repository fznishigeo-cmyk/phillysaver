# Roadmap to beat the big deal sites with code

Analysis for task 063, 2026-10-13. No features are implemented by this document.

Build ranks **1–5 first**: preserve deals when the first data request fails,
make phone filters useful, let visitors save deals, connect the wall to useful
deal pages, and measure which AI intake improvements safely pay off. Then
improve the existing search pages and add truthful check information. The
competitive opportunity is helping someone find an eligible, credible offer
quickly and return to it easily.

**Assumptions:** the operator's 70–80% phone audience is the planning input;
the checkout and standing decisions describe the baseline; benefits below
are hypotheses, not measured traffic or revenue forecasts.

All work must preserve [CLAUDE.md](../CLAUDE.md) and the operator decisions in
[HANDOFF.md](HANDOFF.md): robots.txt and permission scope, one request per
second per host, no wall bypasses or browsers for source collection,
verified evidence, facts only, savings arithmetic in `dealradar/savings.py`,
spend reservations and caps, encrypted state on `data`, and idempotency.
Use `config.yaml` for switches and limits. Customer emails remain disabled,
there are no paid ads or store logos, and protected partner trackers keep
their credit. Browser QA below is for our own generated site only.

The current config has a $6 UTC daily LLM ceiling and $0.50 per-run ceiling;
its temporary boost has ended. The operator accepts roughly $6–10/day, but
this roadmap requires no cap increase, new paid service, upgraded API plan,
or new secret to complete the first five builds. Do not replace the real
critic with its shadow model or skip the critic for Keepa deals.

## What the competitors have, and our actual advantages

This comparison starts from general product knowledge. Official indexed
pages were checked for the linked features; this is not a signed-in app
audit, a current performance benchmark, or an investigation of competitors'
internal verification systems. Uncertainty is explicit, especially for
DealsPlus. Customer emails, SMS, native apps, merchant negotiations and
community moderation are useful competitive context, not proposed work.

| Site | What it offers that we currently lack | Practical response and uncertainty |
|---|---|---|
| Slickdeals | Community submissions, voting and comments; saved deals in its app; keyword/store/brand alerts with mobile push; a browser coupon extension. | Borrow the useful save/search/watch flows. Its community and distribution cannot be recreated by adding a voting button to an empty site. Features are described in [how it works](https://slickdeals.net/corp/how-slickdeals-works/), [the app](https://slickdeals.net/mobile/), and [deal alerts](https://slickdeals.net/deal-alerts/). Its ranking internals and actual checkout success rates are unknown. |
| DealNews | Personal accounts, favorite deals, comments, saved searches and alerts; richer editorial selection and filter choices. | Improve our existing pages, filtering and deal explanations. Our top picks already provide deterministic curation. [Its FAQ](https://www.dealnews.com/pages/faq.html) and [app page](https://www.dealnews.com/pages/app/hub.html) describe account and retention features; we have not measured their effectiveness. |
| Brad's Deals | Human explanations, negotiated exclusive offers, app saves and sharing. | Make our existing facts easier to understand and revisit. Code can improve presentation but cannot manufacture an exclusive merchant agreement or firsthand review. See [the app](https://www.bradsdeals.com/app) and [exclusive offers](https://www.bradsdeals.com/shop/feeds/exclusives). |
| Hip2Save | A recognizable editorial voice, shopping guides, app browsing, email and text distribution, and reader discussion. | Offer concise, evidence-backed steps rather than copying their articles. Their [app page](https://hip2save.com/hip2save-app/) advertises no ads, so an uncluttered experience is not uniquely ours. [New-reader guidance](https://hip2save.com/new-here/) describes their wider editorial and distribution approach. |
| Krazy Coupon Lady | Favorite stores/brands, product alerts, saved deals and coupons, shopping lists, and coupon instruction. | Eligibility filters, saved deals and cost explanations fit our online-only rules; expanding into in-store coupon coverage does not. Its [app page](https://thekrazycouponlady.com/app) also claims team verification, so do not claim that only wowpriced verifies deals. |
| DealsPlus | Historically a community deal/coupon site with store discovery and user contributions. | **Current status and feature availability are uncertain.** Our source is paused for DNS failure in HANDOFF; that is not proof of the business's current status. Do not spend implementation effort copying an unverified current product. |
| Woot | Direct merchandise sales, daily/event merchandising, product discussion and a distinctive community. | It controls inventory and checkout; we refer visitors elsewhere. Learn from clear offer conditions and known end times. Its [forums](https://forums.woot.com/) document discussion and events; do not infer that every Woot offer ends today. |
| Meh | A focused daily deal, a strong voice and a community habit around returning. | Our existing task 061 proposes a daily spotlight. Complete that brief with its owner rather than inventing another countdown or copying commentary. Its [forum](https://meh.com/forum/topics) supports the community comparison; an unknown end time must remain unknown. |

Where we already have an advantage in the implementation:

- **Verification is inspectable.** `parse/extractor.py::verify_evidence`
  checks quotes and math inputs before use; `savings.py` computes savings;
  `rank.py` enforces publication gates; independent sources must support the
  active price. This is a reproducible quality contract, not proof that
  every retailer will honor a price for every shopper.
- **Fake-MSRP removal has real machinery.** The critic rejects suspect
  regular prices and publication excludes them; deal pages label the
  stated reference price. A model can miss an inflated base. Say we filter
  suspect reference prices, not that every listed percentage is a guaranteed
  market discount or that competitors lack checks.
- **Speed has a good foundation.** A static wall, system fonts, bounded
  initial rendering, lazy product photos and compact JSON avoid an app
  framework and server round trips before every card. HANDOFF records a
  local mobile Lighthouse improvement from 84 to 88 and CLS of 0.001.
  These are historical local results, not today's live results or evidence
  we outperform every competitor. Use the existing weekly benchmark.
- **The free site is relatively uncluttered.** There are no display ads,
  store logos or login requirements to read all deals in support mode.
  Join/support prompts still take space; preserving a quiet experience is
  ongoing work. Affiliate and source disclosure remain visible.
- **We can combine sources without charging for access.** Feed/API and
  partner-newsletter facts merge with source credit, and source duplicates
  do not become independent corroboration. Existing top picks and category
  repair already help with a large inventory.

The baseline already includes deal pages with stable URLs, best-today,
category/store/under-price pages, Product/Offer and breadcrumb/list markup,
sitemaps/IndexNow, phone search, 44px home touch targets, NEW indicators,
weekly finds/streaks, affiliate rewriting, API rechecks and AI parse reuse.
Do not schedule those again as missing features. Some README descriptions
lag the code and HANDOFF, including public health, email, aging-out and
affiliate behavior; this roadmap follows the current implementation and
standing decisions.

Two existing briefs deserve separate completion: [061 daily spotlight](codex-tasks/061-daily-deal-spotlight.md)
and [062 observed-price chart](codex-tasks/062-price-history-chart.md).
Their requested UI is not present in this checkout. Confirm ownership in
HANDOFF before taking either on. The chart must describe our sparse observed
prices, not claim a complete retail history or an all-time low.

## Ranking and measurement rules

There are **21** proposed features/fixes below. Rank reflects evidence of a
gap, phone reach, expected value, effort and dependency order. **S** means
about one focused engineering day, **M** two to four, and **L** five or more,
including meaningful checks; these are estimates. Impact is ordered as
**traffic / trust / conversion / earnings**, using high, medium or low.
Conversion here means a useful outbound Get deal action; Join free is a
separate measure. Earnings estimates describe opportunity, not commissions
already earned. No percentage uplift is asserted.

`Existing` denotes a field/event already in this checkout. `New` denotes
instrumentation that the named build must add; it is not a current report
capability. Browser feature builds include `dealradar/publish/tracking.py`,
`dealradar/growth/google.py`, `dealradar/growth/report.py`,
`tests/test_tracking.py` and `tests/test_growth_report.py` for their proposed
metrics. Use a small GA4-only event allow-list, preserving the existing GPC,
ad opt-out and Clarity rules. Never add search text, saved/watch lists,
product identifiers, titles, coupon codes or target prices to event parameters.
Missing credentials, unregistered dimensions, insufficient samples and old
snapshots are reported as unavailable, not zero.

The existing `ga4` section reports a fixed event list and top five landing
pages. `ga4_stores` needs the registered `store` custom dimension. Neither
already supplies device funnels, retention cohorts, feature events or
affiliate commissions. Counts in `link_mix` describe inventory links, not
visitors' clicks. Likewise, `summary.published` counts current inventory,
including displayed gone cards; it is not new deals produced by that run.
Use standard GA4 device/page/visitor dimensions where compatible, and
report custom breakdowns only for registered dimensions, following the
[Data API schema](https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema).
Do not make new console configuration a prerequisite for the first five
builds' aggregate event counts.

### 1. Keep readable deals when the first data request fails

- **Benefit:** a phone with a flaky connection can still read the delivered
  HTML and use its ordinary links. Today `index.html::loadData` replaces
  `STATIC_WALL` with “Could not load deals.json” on the first failure.
- **Impact:** medium / high / high / medium. This protects an existing
  conversion path, with a small, well-defined fix.
- **Effort / risk:** S / low; retries or an invalid response must not
  corrupt a good wall or pretend the list was freshly checked.
- **Files:** `dealradar/publish/templates/index.html`,
  `dealradar/publish/seo.py`, `dealradar/publish/tracking.py`,
  `dealradar/growth/google.py`, `dealradar/growth/report.py`,
  `tests/search_filters_logic.cjs`, `tests/test_search_filters.py`,
  `tests/test_tracking.py`, `tests/test_growth_report.py`.
- **Measure:** New growth-report `product.home_load_attempts`,
  `product.first_load_failures`, `product.static_fallback_shown`,
  `product.first_load_recoveries`; compare observed failed-page fraction
  and Existing `ga4.window.deal_click / ga4.window.sessions`. Track sample
  counts; browserless visits are outside the event sample.

### 2. Useful phone filters and reliable Back navigation

- **Benefit:** choose a price ceiling, new condition and offers without
  complicated steps; return from a deal to the same results and position.
  Current controls cover store, sort and minimum percent, with only a
  special Amazon browse URL and remembered sort.
- **Impact:** medium / medium / high / high.
- **Effort / risk:** M / medium; “no extra steps” must mean the existing
  `needs_steps` predicate, not “everyone qualifies.” Unknown price stays
  unknown; filters must never change publication floors.
- **Files:** `dealradar/publish/templates/index.html`,
  `dealradar/publish/templates/engagement.js`, `config.yaml`,
  `dealradar/config.py`, shared metric files named above,
  `tests/test_search_filters.py`, `tests/search_filters_logic.cjs`,
  `tests/test_front_page.py`.
- **Measure:** New growth-report `product.filter_apply`,
  `product.filter_empty`, `product.back_restore`, and mobile sessions with
  a Get deal action; Existing `ga4_stores.window` checks store mix. Report
  action rate per session rather than calling outbound clicks purchases.

### 3. Saved deals on this device, without an account

- **Benefit:** save an interesting offer now and revisit it later. Existing
  localStorage conveniences remember sort/visits, not a shopping shortlist.
- **Impact:** medium / medium / high / high, mostly from return visits.
- **Effort / risk:** M / medium; stale saved prices must not look live.
  Storage failures, cleared storage and unavailable items need honest copy.
- **Files:** `dealradar/publish/templates/index.html`,
  `dealradar/publish/templates/engagement.js`,
  `dealradar/publish/templates/privacy.html`, `dealradar/publish/site.py`,
  `config.yaml`, `dealradar/config.py`, shared metric files,
  `tests/engagement_logic.cjs`, `tests/test_quality_trio.py`,
  `tests/test_support.py`.
- **Measure:** New growth-report `product.deal_save`,
  `product.saved_view`, `product.saved_deal_click` and seven-day returning
  sessions. Never upload the list; no cross-device retention claim.

### 4. Make deal pages useful on phones and reachable from the live wall

- **Benefit:** tapping a title opens a stable page explaining the offer;
  Get deal remains a direct action. Static titles already link internally,
  but `index.html::cardHtml` replaces them with outbound links after JSON
  loads. Deal-page codes are plain text, and their outbound links lack the
  home wall's click listener.
- **Impact:** high / high / high / high; improves search landing conversion
  and internal discovery, although an extra title step could reduce clicks.
- **Effort / risk:** M / medium; preserve the exact serialized Get deal
  destination and protected credit, and avoid nonexistent or locked pages.
- **Files:** `dealradar/publish/site.py`, `dealradar/publish/seo.py`,
  `dealradar/publish/templates/index.html`,
  `dealradar/publish/templates/hub.html`, shared metric files,
  `tests/test_seo_deal_pages.py`, `tests/test_rich_results.py`,
  `tests/test_publish_site.py`, `tests/test_tracking.py`.
- **Measure:** New growth-report `product.deal_detail_views`,
  `product.deal_detail_outbound_clicks`, split by device/page family;
  Existing `search_console.pages` gives an initial search signal. Add
  session action rates and watch overall home action rate for regression.

### 5. Make the next AI intake savings measurable and safe

- **Benefit:** more distinct eligible offers within the existing budget,
  without accepting weaker prices. Parse caching, structured Keepa/Woot
  extraction and critic batching already exist; build on them.
- **Impact:** high / high / medium / high, conditional on observed yield.
- **Effort / risk:** M / medium; a false prefilter skip permanently loses
  a handled post. Today the prefilter uses `cfg.min_pct`, while the site's
  gate can be lower. Counting repeated sightings or the whole live inventory
  would exaggerate return per dollar.
- **Files:** `dealradar/parse/extractor.py`, `dealradar/pipeline.py`,
  `dealradar/db.py`, `dealradar/config.py`, `config.yaml`,
  `dealradar/growth/report.py`, `tests/test_parse_value.py`,
  `tests/test_ai_spend.py`, `tests/test_spend_pacing_shadow.py`,
  `tests/test_e2e_offline.py`, `tests/test_growth_report.py`.
- **Measure:** Existing `prefilter.would_skip_publishable`,
  `parse.reused_posts`, `structured_parse`, `extract_by_source`,
  `spend_by_kind`, `llm_cache_tokens`, `price_recheck.tokens_short`;
  New run-summary/growth-report `ai_yield.unique_new_public_deals`,
  `ai_yield.useful_price_updates`, `ai_yield.new_deals_per_usd`,
  `ai_yield.prefilter_unevaluated`. Remain in shadow until the rollout
  evidence described in brief 5 exists.

### 6. Audit and improve existing SEO pages, freshness and structured data

- **Benefit:** people searching a store/category/product land on a useful,
  current page with matching visible facts. Do this before adding more
  page combinations; the basic pages and markup already exist.
- **Impact:** high / high / medium / medium; allow four to eight weeks for
  search feedback, not one publish.
- **Effort / risk:** M / medium; current category indexability uses seven-day
  counts, which can leave a sparse current wall. `lastmod` uses first-seen
  or price-since and can miss other substantive offer changes. Audit these
  before changing rules; do not lower operator-controlled quality floors.
- **Files:** `dealradar/publish/seo.py`, `dealradar/pipeline.py`,
  `dealradar/publish/templates/hub.html`,
  `dealradar/publish/templates/hubs.yaml`, `dealradar/db.py`,
  `dealradar/growth/google.py`, `dealradar/growth/report.py`,
  `config.yaml`, `tests/test_seo.py`, `tests/test_rich_results.py`,
  `tests/test_seo_store_price_pages.py`, `tests/test_seo_deal_pages.py`.
- **Measure:** New run-summary `seo_audit.indexable_by_family`,
  `seo_audit.empty_indexable`, `seo_audit.schema_mismatches`,
  `seo_audit.broken_internal_links`; New growth-report search clicks,
  impressions and CTR by page family/device. Existing `sitemap.errors`
  and `search_console` provide a starting point. Use significant factual
  changes for `lastmod`, not a new timestamp on every build, following
  [Google's guidance](https://developers.google.com/search/blog/2023/06/sitemaps-lastmod-ping).
  For an affiliate referral page target [product snippets](https://developers.google.com/search/docs/appearance/structured-data/product/),
  not a claim that wowpriced sells the item. Keep single-product markup
  factual; no invented reviews, ratings, expiry, shipping or return policy.

### 7. Explain the price basis and the actual check, without overstating it

- **Benefit:** distinguish a source-reported price, independent same-price
  corroboration, price-history comparison, and a successful availability
  reading. A fresh site publish alone checks no price.
- **Impact:** medium / high / high / medium.
- **Effort / risk:** M / medium; `price_rechecked_at` also records a negative
  reading, so it cannot alone justify “available, checked at.” The
  `history-checked` tier may come from our observations or a Keepa check;
  its provenance must be known before naming the method.
- **Files:** `dealradar/verify.py`, `dealradar/pipeline.py`,
  `dealradar/publish/site.py`, `dealradar/publish/seo.py`,
  `dealradar/publish/templates/index.html`,
  `dealradar/publish/templates/how-it-works.html`, shared metric files,
  `tests/test_evidence.py`, `tests/test_verify_rank.py`,
  `tests/test_sure_before_removing.py`, `tests/test_seo_deal_pages.py`.
- **Measure:** New run-summary `trust.basis_coverage`,
  `trust.successful_check_age_buckets`, `trust.unknown_check_count`;
  New growth-report `product.check_explanation_open`. Preserve the
  “Amazon price drop” public name and private source-health boundary.

### 8. Price-drop watches checked when the visitor returns

- **Benefit:** save an offer and a target price, then see matches after
  loading a fresh public payload. This closes part of the alert gap while
  customer emails remain off.
- **Impact:** high / medium / high / high.
- **Effort / risk:** M / medium; depends on rank 3. localStorage does not run
  in the background or across devices. Label it “Check when you visit,”
  with no notification promise. Match exact deal identity and condition,
  never a fuzzy product title; missing/unknown/gone data cannot trigger.
- **Files:** `dealradar/publish/templates/engagement.js`,
  `dealradar/publish/templates/index.html`,
  `dealradar/publish/templates/privacy.html`, `dealradar/publish/site.py`,
  `config.yaml`, `dealradar/config.py`, shared metric files,
  `tests/engagement_logic.cjs`, `tests/test_quality_trio.py`.
- **Measure:** New growth-report `product.watch_create`,
  `product.watch_match`, `product.watch_deal_click`, one match event per
  new observed offer/target crossing. Never send targets or watch identities.
  Compare already computed prices; any displayed savings calculation stays
  in `savings.py`.

### 9. A lightweight installable PWA

- **Benefit:** put wowpriced on a phone's home screen with the existing
  brand, returning directly to the visitor's saves/preferences.
- **Impact:** medium / medium / high / medium.
- **Effort / risk:** M / medium; build after saved deals, with feature
  detection and a quiet user-initiated install option. Use a manifest and
  existing brand-derived icons first; installation does not require a
  service worker everywhere. If adding one, cache only a versioned shell
  and an offline explanation. Never cache offer HTML/JSON, account traffic,
  affiliate redirects, or source/retailer images as live shopping content.
- **Files:** `dealradar/publish/site.py`, `dealradar/publish/seo.py`,
  `dealradar/publish/templates/index.html`,
  `dealradar/publish/templates/hub.html`, `config.yaml`,
  new `dealradar/publish/templates/manifest.webmanifest` and brand icon
  assets, shared metric files, `tests/test_publish_site.py`,
  `tests/site_check_logic.cjs`.
- **Measure:** New growth-report `product.install_offer_shown`,
  `product.install_accepted`, `product.standalone_sessions`, and returning
  session rate. Platform support differs: the programmatic prompt is
  Chromium-specific and is unavailable on iOS; see [MDN installability](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable)
  and [prompt behavior](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/How_to/Trigger_install_prompt).
  An accepted prompt is not proof of a retained user.

### 10. Measure and reduce phone rendering cost

- **Benefit:** search and filters respond quickly even with thousands of
  deals; the wall becomes useful sooner on a modest phone.
- **Impact:** high / medium / high / high.
- **Effort / risk:** M / medium; profile before changing the renderer.
  Existing lazy rendering, first-image priorities and preconnects should
  remain. Avoid redundant first-paint rebuilding; incrementally append
  cards or defer noncritical work only where measurements show a gain.
- **Files:** `dealradar/publish/templates/index.html`,
  `dealradar/publish/seo.py`, `dealradar/publish/site.py`,
  `dealradar/pipeline.py`,
  `scripts/lighthouse_summary.js`, `scripts/mobile_audit.cjs`,
  `scripts/competitor_benchmark.py`, `.github/workflows/lighthouse.yml`,
  shared metric files, `tests/lighthouse_summary_logic.cjs`,
  `tests/test_lighthouse_polish.py`, `tests/test_search_filters.py`.
- **Measure:** New run-summary `publish.payload_gzip_bytes`,
  `publish.html_bytes`, `publish.elapsed_s`; New growth-report
  `performance.mobile_lcp_p75`, `performance.mobile_inp_p75`,
  `performance.mobile_cls_p75`, with sample counts. Prefer existing
  PageSpeed field data when present; otherwise label bounded measured
  distributions honestly. Never report a GA4 average as p75 or use a
  Lighthouse score as field INP. Targets are LCP ≤2.5s, INP ≤200ms and
  CLS ≤0.1 at mobile p75 ([Web Vitals](https://web.dev/articles/vitals)).
  Ship no paid telemetry service or new third-party runtime library.

### 11. Hold the shopper's place and focus through refreshes

- **Benefit:** when a drop, filter or load-more renders, keyboard focus and
  the card under the visitor's finger remain predictable.
- **Impact:** low / high / high / medium.
- **Effort / risk:** M / medium; existing FLIP/reduced-motion behavior
  already addresses animation. Test focus and viewport anchors across DOM
  replacement. Keep live prices, real drop rotation, new-deal announcements
  and the dormant free-8 rules; do not freeze obsolete offers.
- **Files:** `dealradar/publish/templates/index.html`,
  `tests/front_page_logic.cjs`, `tests/search_filters_logic.cjs`,
  `tests/site_check_logic.cjs`, `scripts/mobile_audit.cjs`, shared metric files.
- **Measure:** New growth-report `product.refresh_anchor_lost`,
  `product.refresh_focus_lost`; New own-site audit `mobile.anchor_shift_px`.
  These measure losses, not a new streak or artificial engagement signal.

### 12. Local saved searches and explicit shopping preferences

- **Benefit:** return to “kitchen at my stores” in one tap, with an optional
  For you view based on choices the visitor actually made.
- **Impact:** high / medium / high / high.
- **Effort / risk:** M / medium; depends on rank 2. Use deterministic
  preferences, not an LLM or a tracking profile. Keep All/top picks intact,
  preserve diversity and eligibility, and provide reset controls.
- **Files:** `dealradar/publish/templates/engagement.js`,
  `dealradar/publish/templates/index.html`,
  `dealradar/publish/templates/privacy.html`, `dealradar/publish/site.py`,
  `config.yaml`, `dealradar/config.py`, shared metric files,
  `tests/engagement_logic.cjs`, `tests/test_search_filters.py`.
- **Measure:** New growth-report `product.saved_search_create`,
  `product.saved_search_open`, `product.preference_view`, and seven-day
  return/action rates. Search strings and preference lists stay local.

### 13. Precise search and helpful zero-result recovery

- **Benefit:** searches for models/specs find the intended item; an empty
  result offers clear filter resets or relevant department/store choices.
  Current compact substring matching can overmatch numeric model tokens.
- **Impact:** medium / high / high / medium.
- **Effort / risk:** M / medium; keep numbers and model identifiers exact;
  small typo tolerance only for ordinary words. Derive suggestions from
  current eligible inventory without inventing synonyms or product facts.
- **Files:** `dealradar/publish/templates/index.html`,
  `tests/search_filters_logic.cjs`, `tests/test_search_filters.py`,
  shared metric files.
- **Measure:** New growth-report `product.search_apply`,
  `product.search_zero`, `product.search_recovery`, and sessions with an
  outbound action after search. Log no search text.

### 14. Compare equivalent offers and find live alternatives

- **Benefit:** on a deal page, see another eligible offer for the same
  exact model/variant, or a clearly labeled related option when it ended.
  Existing related links use a stable same-hub key order, not equivalence.
- **Impact:** high / high / high / high.
- **Effort / risk:** L / high; title similarity cannot establish identity.
  Require supported exact identifiers, model/pack/variant, condition and
  comparable shipping/eligibility. Otherwise show ordinary related deals,
  never “cheapest anywhere.” Keep existing dedupe identities distinct.
- **Files:** `dealradar/dedupe.py`, `dealradar/models.py`, `dealradar/db.py`,
  `dealradar/pipeline.py`,
  `dealradar/savings.py`, `dealradar/publish/seo.py`,
  `dealradar/publish/site.py`, `tests/test_dedupe.py`,
  `tests/test_money_math_correctness.py`, `tests/test_seo_deal_pages.py`,
  shared metric files.
- **Measure:** New run-summary `comparison.exact_groups`,
  `comparison.rejected_ambiguous`; New growth-report
  `product.alternative_click`, `product.ended_page_live_click`. Do not
  silently change the operator's inert gone cards; any alternative link
  belongs to a distinct live offer.

### 15. Show checkout cost and requirements clearly

- **Benefit:** distinguish paying now, later rebate/store credit, shipping
  thresholds, coupons and membership requirements before leaving the site.
  Existing flags/headlines/how steps carry some of this; audit missing
  shopper information rather than adding another generic badge.
- **Impact:** medium / high / high / high.
- **Effort / risk:** M / medium; use only verified facts and the savings
  result. Unknown shipping/tax/location cannot become a delivered-price
  guarantee. Deferred value never reduces the checkout headline price.
- **Files:** `dealradar/savings.py`, `dealradar/models.py`,
  `dealradar/pipeline.py`,
  `dealradar/parse/extractor.py`, `dealradar/publish/site.py`,
  `dealradar/publish/seo.py`, `dealradar/publish/templates/index.html`,
  `tests/test_savings.py`, `tests/test_evidence.py`,
  `tests/test_code_deals.py`, shared metric files.
- **Measure:** New run-summary `checkout.known_shipping_share`,
  `checkout.requirement_coverage`; New growth-report
  `product.requirements_open` and outbound session action rate for
  step-based offers. The existing publication math remains authoritative.

### 16. Unit prices for genuinely comparable grocery/household packs

- **Benefit:** compare useful value such as price per ounce or item,
  rather than favoring a dramatic percentage on a tiny pack.
- **Impact:** medium / high / medium / medium.
- **Effort / risk:** L / high; `quantity_or_unit` and price-unit fields
  exist, but their presence is not proof of a verified, comparable unit.
  Add explicit evidence checks and deterministic unit handling first.
  Ambiguous packs, mixed sizes and unknown quantities get no unit price.
- **Files:** `dealradar/models.py`, `dealradar/parse/schema.py`,
  `dealradar/pipeline.py`,
  `dealradar/parse/extractor.py`, `dealradar/savings.py`,
  `dealradar/publish/site.py`, `dealradar/publish/seo.py`,
  `dealradar/publish/templates/index.html`, `tests/test_evidence.py`,
  `tests/test_savings.py`, `tests/test_parse_schema.py`, shared metric files.
- **Measure:** New run-summary `unit_price.verified_coverage`,
  `unit_price.ambiguous_rejected`; New growth-report household/grocery
  outbound action rate. Do not imply we cover the in-store coupon market.

### 17. Report actual earning traffic before tuning monetization

- **Benefit:** fewer broken/wasted shopper journeys and evidence about
  which useful offers also pay for the free site.
- **Impact:** low / medium / medium / high.
- **Effort / risk:** M / medium; tag a bounded earning-link bucket in
  GA4-only analytics, including static-page paths fixed in rank 4. Keep
  source credits and disclosure. Do not make or repeatedly probe affiliate
  clicks just to test earnings, and do not restore tracker repair.
- **Files:** `dealradar/publish/site.py`, `dealradar/publish/tracking.py`,
  `dealradar/publish/templates/index.html`,
  `dealradar/publish/seo.py`, `dealradar/growth/google.py`,
  `dealradar/growth/report.py`, `tests/test_link_mix.py`,
  `tests/test_earning_links.py`, `tests/test_partner_policies.py`,
  `tests/test_money_report.py`.
- **Measure:** Existing `link_mix.earning`, `link_mix.not_earning` and
  `ga4_stores`; New growth-report `money.earning_outbound_clicks`,
  `money.outbound_by_bucket`. Commissions and earnings per click remain
  unavailable until approved read-only network reporting exists. Never
  estimate purchases or revenue from clicks or inventory counts.

### 18. Reliable IndexNow receipt and retry accounting

- **Benefit:** changed and ended search pages reach participating engines
  sooner. HANDOFF identifies the current publish-before-POST accounting,
  which can defer recovery from a failed send for a week.
- **Impact:** medium / medium / medium / medium.
- **Effort / risk:** M / medium; separate content discovery from receipt,
  acknowledge only the submitted batch after a valid response, and retry
  with backoff. Preserve serialization with the existing encrypted-state
  lifecycle; deployment failure must never acknowledge unpublished pages.
- **Files:** `dealradar/publish/seo.py`, `dealradar/cli.py`,
  `dealradar/db.py`, `.github/workflows/fast.yml`,
  `.github/workflows/hourly.yml`, `tests/test_seo.py`,
  `tests/test_workflows.py`, `dealradar/growth/report.py`.
- **Measure:** New run-summary/growth-report `indexnow.pending`,
  `indexnow.received`, `indexnow.key_pending`, `indexnow.failed`,
  `indexnow.oldest_pending_hours`. A 200 acknowledges receipt, and 202
  still has key validation pending; neither promises indexing, per
  [the protocol](https://www.indexnow.org/documentation). Do not call it
  Google instant indexing or add a new external sending channel.

### 19. Spend intake on useful, diverse supply without starving sources

- **Benefit:** more relevant nonduplicate offers across everyday stores
  and departments throughout the day, with the same AI budget.
- **Impact:** high / medium / high / high.
- **Effort / risk:** M / medium; depends on rank 5 measurement. Existing
  source rotation/interleaving, audit counters and Keepa reserve already
  exist. Add bounded per-source intake weights with exploration and a
  maximum wait, rather than pausing a low-yield source permanently or
  filling the wall with one earning store.
- **Files:** `dealradar/pipeline.py`, `dealradar/source_audit.py`,
  `dealradar/config.py`, `config.yaml`, `dealradar/keepa_budget.py`,
  `dealradar/growth/report.py`, `tests/test_pipeline_sources.py`,
  `tests/test_source_audit.py`, `tests/test_spend_pacing_shadow.py`,
  `tests/test_front_page.py`.
- **Measure:** Existing `extract_by_source`, `spend_by_kind`,
  `price_recheck.tokens_short`; New run-summary
  `intake.unique_public_by_source`, `intake.oldest_deferred_hours`,
  `intake.public_category_mix`. Judge unique eligible yield and coverage,
  not raw extracted counts or a source's affiliate earnings alone.

### 20. Extend deterministic extraction only where licensed facts suffice

- **Benefit:** simple API offers can avoid paid extraction and arrive
  sooner; complex posts retain their LLM read and critic.
- **Impact:** medium / high / medium / medium.
- **Effort / risk:** L / high; Keepa and plain-price Woot already take
  this route. Inspect the current Meh API shape for explicit online price,
  reference-price and condition fields; add a fast path only if enough
  supported facts exist. Otherwise retain current behavior. Existing
  permission newsletters may qualify only when a strictly recognized
  format has enough verified evidence; no generic regex guessing.
- **Files:** `dealradar/fetch/meh.py`, `dealradar/fetch/newsletter.py`,
  `dealradar/parse/extractor.py`, `dealradar/parse/schema.py`,
  `dealradar/pipeline.py`, `tests/test_api_adapters.py`,
  `tests/test_newsletter_inbox.py`, `tests/test_evidence.py`,
  `tests/test_ai_spend.py`, `tests/test_e2e_offline.py`.
- **Measure:** Existing `structured_parse`, `parse.deals_dropped_evidence`
  and `spend_by_kind`; New run-summary `structured_parse.by_adapter` and
  `structured_parse.fallbacks`, plus rank 5's unique yield per dollar.
  This is conditional on the adapter audit, not a promise of new supply
  from missing or unapproved eBay/Best Buy API credentials.

### 21. Carry phone accessibility and honest media fallbacks across pages

- **Benefit:** hub, store, price, related-card and no-JS visits are as
  usable as the polished live home wall. Broken photos show a stable text
  tile rather than implying a missing deal.
- **Impact:** medium / high / high / medium.
- **Effort / risk:** M / low–medium; task 058 polished the home template,
  while `hub.html` still contains sub-12px chips/credits and smaller card
  actions. Extend the audit to these pages and actual hit areas; do not
  blindly add overlapping pseudo-element targets. Keep the current
  image-free static pages unless a separate, permission-preserving image
  proposal is justified by measurements.
- **Files:** `dealradar/publish/templates/hub.html`,
  `dealradar/publish/templates/index.html`, `dealradar/publish/seo.py`,
  `scripts/mobile_audit.cjs`, `scripts/site_check.js`,
  `.github/workflows/mobile-audit.yml`, `tests/test_lighthouse_polish.py`,
  `tests/test_seo.py`, `tests/site_check_logic.cjs`, shared metric files.
- **Measure:** Existing `site_health.coverage`; New growth-report
  `accessibility.mobile_failures_by_page`,
  `product.image_fallback_shown`; New audit fields for target overlap,
  clipped requirements, overflow and tiny text. Product image URLs retain
  permission and source credit; no source image files are cached or stored.

## Delivery order and what would count as progress

Build 1 independently, then 2 and 3, then 4 against their navigation/save
interfaces. Start 5's observation period as soon as its tests pass; do not
wait for a PWA to start learning about intake. Finish the existing spotlight
and chart briefs with their owners. Next: 6/7 for search and price trust,
then 8/9 for repeat visits and 10/11 for measured phone performance. Later
items depend on observed traffic and quality, rather than a calendar promise.

Each implementation owns its measurements; there is no separate analytics
platform project. Capture at least one complete baseline week and annotate
the feature date/version. Compare equivalent device/store mixes and
weekdays; before/after movement alone does not prove causation. At low
traffic report counts and uncertainty rather than impressive percentages.
Use two weeks for browsing/return signals and four to eight for search.
Do not use traffic data absent from this checkout to assert current success.

The private scorecard should show useful mobile outbound sessions, returning
sessions, search clicks by page family, truthful verification coverage,
mobile performance samples, unique new public deals per total AI dollar,
and measured affiliate receipts when available. Add a metric only alongside
its feature or existing data source, with a missing-data fallback. The
daily quality loop can select the next 1–3 small refinements from this
scorecard; no fake urgency, counters, testimonials or votes are needed.

## The first five: ready-to-run Codex briefs

These are future implementation instructions, embedded here rather than
creating or assigning new task numbers. They do not authorize this analysis
task to change application code. Check HANDOFF for ownership before each
build. Use the same Read first / Why / Do / Tests structure as task 058.

### Brief 1: keep the static deal wall on a failed first load

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

### Brief 2: price and eligibility filters, with preserved browsing state

**Read first:** AGENTS.md, CLAUDE.md, docs/HANDOFF.md. Files:
`dealradar/publish/templates/index.html` (`filtered`, `applyPublish`,
`populateStores`, `resetWall`, controls and phone CSS),
`dealradar/publish/templates/engagement.js`, `dealradar/config.py`,
`config.yaml`, `dealradar/publish/tracking.py`,
`dealradar/growth/google.py`, `dealradar/growth/report.py`,
`tests/test_search_filters.py`, `tests/search_filters_logic.cjs`,
`tests/test_front_page.py`, `tests/front_page_logic.cjs`,
`tests/test_support.py`, `tests/test_tracking.py`,
`tests/test_growth_report.py`.

#### Why

Phone visitors can choose a store, but cannot express “new, under my budget,
no extra steps.” Returning from an offer should not require rebuilding the
search or finding the old scroll position.

#### Do

1. Add a small accessible filter sheet on phones and equivalent compact
   desktop controls. Include configured price presets (reuse
   `seo.price_pages`), an optional maximum price, “New condition only,”
   “No extra steps,” Apply and Clear. Use 44px hit areas, 16px input text,
   existing tokens and focus/escape/return behavior; no extra sticky bar.
2. Filter existing public facts only. A price ceiling requires a finite
   known `final_price`. New condition excludes normalized used/open-box/
   refurbished markers; do not label it a seller/warranty guarantee.
   No-extra-steps uses `needs_steps === false`. Unknown fields must not
   silently pass those explicit filters. Keep source credit, ranking,
   gone-card spacing and publication floors intact.
3. Make allow-listed store/category/sort and fixed preset selections
   representable in a shareable browse URL; extend the existing Amazon
   route without breaking it. Keep typed search and custom numeric input
   in guarded sessionStorage, outside URLs and analytics. Canonical
   remains `/`; create no crawlable parameter-page sitemap entries.
4. Restore query, filters, shown count and a stable visible-card anchor
   when returning in the same tab. URL selections take precedence over
   remembered defaults. Handle `popstate`, reload, missing stores and
   storage denial; do not push a history entry per typed character.
   After refresh use current facts: a missing anchor falls back to a
   nearby position without reviving a removed offer.
5. Add GA4-only `filter_apply`, `filter_empty`, `back_restore` events,
   once per completed action, with a bounded `method` value only. Extend
   the private `product` report with these counts and a device-segmented
   sessions-with-Get-deal rate. No query, price, threshold or item key is
   an event parameter; mask new inputs for Clarity.

#### Tests

Use mixed stores/conditions, null/missing prices, step-based/plain offers,
gone cards and locked stubs. Verify all filter intersections, clear/reset,
no floor change, valid/invalid/unknown URL values, preserved Amazon route,
URL-vs-default precedence, Back/Forward, reload, refresh removing an anchor,
blocked storage, current price above a saved ceiling and no duplicate
events. Check the existing 300-card Trending behavior, other-view loading,
search/category restore and support/dormant modes. Tracking tests prove
raw input cannot reach any event; report mocks cover zero sessions and
unavailable data. Check focus, keyboard, dark mode and reduced motion.

Run `.venv/bin/python -m pytest -q`; perform own-site phone/desktop Back
navigation QA without opening retailer links. Name sandbox-blocked checks
and leave their verification pending. Add one HANDOFF bullet. Do NOT commit
or push; end with what changed and anything unsure.

### Brief 3: save deals locally and revisit them honestly

**Read first:** AGENTS.md, CLAUDE.md, docs/HANDOFF.md. Files:
`dealradar/publish/templates/engagement.js`,
`dealradar/publish/templates/index.html` (`dealKey`, `cardHtml`,
`filtered`, refresh/render paths), `dealradar/publish/site.py`,
`dealradar/publish/templates/privacy.html`, `dealradar/config.py`,
`config.yaml`, `dealradar/publish/tracking.py`,
`dealradar/growth/google.py`, `dealradar/growth/report.py`,
`tests/engagement_logic.cjs`, `tests/test_quality_trio.py`,
`tests/test_search_filters.py`, `tests/test_support.py`,
`tests/test_tracking.py`, `tests/test_growth_report.py`.

#### Why

A useful offer often appears before someone is ready to buy. Saving it
should be immediate and should not require email or an account.

#### Do

1. Add a configured `engagement.saved_deals` switch and bounded saved-list
   limit. In support mode add a 44px Save button with an accessible name,
   `aria-pressed`, and a Saved view with an exact local count. Hide the
   feature when disabled; keep dormant auth, locked stubs and free-8 rules.
2. Use a versioned, validated localStorage record containing only stable
   public `key`, safe display label/store and optional internal deal path.
   Do not save price, outbound URLs, photos, codes or post content. Home
   saving works before brief 4 adds paths; a missing path is not an error.
3. Reconcile with the current successfully loaded public payload. Present
   normal current cards for matches. A missing match says “Not currently
   listed; availability unknown,” with no cached price or Get deal button.
   A confirmed gone match says it ended and has no outgoing action. Do not
   equate a cap/rotation/missing page with expiration or bypass locked data.
4. Support remove, clear and cross-tab storage changes. A quota/permission
   failure keeps an in-memory shortlist for that tab and says it will not
   persist. Escape labels and validate any restored path as a same-origin
   `/deal/.../` path; corrupt or excessive records cannot break the wall.
   Explain “Saved on this device” and clearing site data on privacy.html.
5. Add GA4-only `deal_save`, `saved_view`, `saved_deal_click` events;
   counts only with permitted `method` labels. Extend `product` in the
   private report and add observed seven-day returning sessions using
   GA4's visitor classification with missing-data handling. Never send the
   shortlist or a key. This is a return-visit signal, not a save-to-purchase
   cohort or cross-device sync statistic.

#### Tests

Offline tests cover save/unsave/clear, duplicate saves, persistence, ordering
and limits, renamed items, live prices changing, a removed match, confirmed
gone versus absent, locked data, invalid paths/labels, malformed storage,
quota denial and cross-tab updates. Assert no saved prices or tagged URLs
are restored as live offers. Verify button events do not navigate, firing
counts once, unchanged ordinary deal/source links, feature-off output,
support/dormant variants and guarded analytics. Report tests include
missing visitor classification and meaningful denominator labels.

Run `.venv/bin/python -m pytest -q`. Own-site visual QA covers 360px,
iPhone/Pixel, keyboard, dark mode and reduced motion. Name blocked browser
checks. Add one HANDOFF bullet. Do NOT commit or push; end with what changed
and anything unsure.

### Brief 4: connect the live wall to useful phone deal pages

**Read first:** AGENTS.md, CLAUDE.md, docs/HANDOFF.md; coordinate with task
062's owner. Files: `dealradar/publish/site.py` (shared serialization and
write order), `dealradar/publish/seo.py` (`_deal_pages`, `_deal_page`,
`card_html`, `_basis_label`), `dealradar/publish/templates/index.html`
(`cardHtml`, outbound click handling),
`dealradar/publish/templates/hub.html`,
`dealradar/publish/tracking.py`, `dealradar/growth/google.py`,
`dealradar/growth/report.py`, `tests/test_seo_deal_pages.py`,
`tests/test_rich_results.py`, `tests/test_publish_site.py`,
`tests/test_search_filters.py`, `tests/test_tracking.py`,
`tests/test_growth_report.py`, `tests/test_support.py`.

#### Why

We already publish useful stable URLs, but live card titles stop linking to
them after JavaScript renders. Search visitors also lack tap-to-copy codes,
and their Get deal clicks are not covered by the home-wall listener.

#### Do

1. Expose an optional `deal_path` only for pages actually emitted for
   that public deal. Reuse the stored `seo_deal_paths` mapping and current
   cap/visibility selection. Do not regenerate a path from a renamed
   title. Arrange one page-selection pass before the final payload write;
   maintain shared public/member values and unchanged opaque locked stubs.
2. Link dynamic titles to that path just like static titles. Deals without
   an emitted page retain the existing fallback. Get deal continues using
   the exact `d.url`; do not add an interstitial or change affiliate,
   step-based, Amazon, EPN or protected partner behavior. Preserve brief
   2's return state; integrate local Save if brief 3 is available.
3. Make the existing deal facts scan well on 360px: current price, price
   basis, condition/requirements, steps, sources, disclosure and a 44px
   Get deal action. Convert known codes into accessible tap-to-copy
   buttons, with visible selectable text when JS/clipboard is unavailable.
   Keep image-free static pages, related offers, ended-page noindex and
   the current absence of Product markup for ended deals. Do not add a
   chart, invented stock check or global sticky sales panel in this brief.
4. Give generated static outbound anchors explicit safe store/category
   metadata and add one scoped delegated handler. It covers detail and
   listing/related-card sponsored outbound links, while internal titles
   and credit links are not `deal_click`. The dynamic home listener and
   shared handler must never count the same action twice. Handle modified
   clicks without preventing normal browser navigation.
5. Add GA4-only detail-view and code-copy counts. For Get deal retain
   `deal_click` with store/category and a bounded page-kind `method`.
   Extend the private report with `product.deal_detail_views` and
   `product.deal_detail_outbound_clicks` by device/page family; label
   click/view ratio as actions, not purchases. No code, product ID, price
   or outbound URL reaches tags. Measure incremental payload gzip bytes
   to account for the added path field.

#### Tests

Check stable paths after rename, missing/capped pages, public/member
equality, no locked leakage, first load and refresh title destinations,
unchanged Get deal URLs for Amazon/EPN/Sovrn/code/multistep/protected
partners, and inert ended pages. Test copy success/failure/no clipboard,
escaping and safe metadata. Offline Node tests exercise static listing,
detail/related and dynamic clicks, modified clicks and no duplicate counts;
credits/internal navigation never count as outbound. Validate HTML/JSON-LD
prices and conditions still agree, no invented review/expiry, correct
canonical and noindex, measured payload size and unchanged no-JS use.

Run `.venv/bin/python -m pytest -q`; verify phone/desktop/light/dark and
keyboard detail journeys on generated own-site pages, never opening
retailer links. Name blocked visual checks. Add one HANDOFF bullet. Do NOT
commit or push; end with what changed and anything unsure.

### Brief 5: audit intake against the real site floor and persist useful AI yield

**Read first:** AGENTS.md, CLAUDE.md, docs/HANDOFF.md,
docs/codex-tasks/059-more-deals-per-ai-dollar.md. Files:
`dealradar/parse/extractor.py` (`_below_floor_post`, `_can_clear_floor`,
`parse_posts`), `dealradar/pipeline.py` (post-critic audit and summary),
`dealradar/db.py` (`run_stats`, source audit, spend ledger),
`dealradar/config.py`, `config.yaml`, `dealradar/growth/report.py`,
`tests/test_parse_value.py`, `tests/test_ai_spend.py`,
`tests/test_spend_pacing_shadow.py`, `tests/test_money_math_correctness.py`,
`tests/test_e2e_offline.py`, `tests/test_growth_report.py`.

#### Why

Task 059 already ships cost controls, but a safe rollout needs persistent,
deduplicated useful yield. The prefilter/second-read threshold must respect
the lower site tier if configured; current headline and site floors happen
to be equal, which hides that edge case.

#### Do

1. Derive the conservative prefilter and second-opinion eligibility floor
   from `min(cfg.min_pct, cfg.site_show_from_pct)`, matching the site's
   effective floor. Keep extra-savings/range/ambiguous/structured exemptions,
   the existing conservative buffer, evidence verification and final
   per-condition publication gates. All discount arithmetic stays in
   `savings.py`; this does not lower any publication floor.
2. Keep `parse.prefilter_below_floor: shadow` in shipped config. Count
   candidate posts and post-critic publishable misses by source, plus
   candidates not fully evaluable because extraction/critic/caps failed.
   Zero judged misses with incomplete evaluation is not a clean result.
3. Persist compact, idempotent yield facts in encrypted state, sufficient
   for private daily/seven-day aggregates. Count distinct newly public
   deal identities once and useful evidence-backed price changes
   separately. Multiple sightings, feed/email copies, critic rejects,
   below-floor items, old inventory and reruns do not inflate new supply.
   Mark pre-instrumentation history unavailable and record coverage dates.
   Use bounded retention consistent with run_stats; store no post bodies,
   rejected extracts or full summary blobs.
4. Add `ai_yield.unique_new_public_deals`, `useful_price_updates`,
   `new_deals_per_usd`, `prefilter_unevaluated` to the run summary and
   private growth report. Denominator includes all actual model-priced
   paid work, category/critic/shadow calls and conservative uncertain
   spend, with zero-spend ratios shown as unavailable. Preserve real
   additional spend when two executions share a run key; yield accounting
   must not overwrite or bypass the reservation ledger.
5. Report readiness for a later prefilter activation, without switching
   automatically. Proposed minimum evidence: seven complete UTC days,
   at least 200 fully evaluated candidate posts across at least three
   independent source sites, zero publishable misses, no unexplained
   evaluation gaps, and preserved source/category supply in the offline
   replay. Fewer candidates means insufficient evidence. Any miss keeps
   shadow mode and prompts a narrower rule. These are rollout criteria,
   not a claim that the present checkout already satisfies them.
6. Keep critic model, critic reservation share, Keepa checks, pacing,
   per-run/day caps, parse-reuse evidence checks, retries and outgoing
   idempotency intact. Add no new live LLM calls solely for this audit;
   use existing shadow observations and offline synthetic replay fixtures.

#### Tests

Exercise equal/lower site floors, per-condition floors, near-boundary
discounts, extra coupons/rebates, ranges, roundups, malformed inputs and
free structured posts. Shadow output must match off at equal floors;
lower-tier useful posts are never skipped. Test publishable-miss and
unevaluated counts after critic, deduped cross-source/feed/email yield,
price updates versus new identities, zero cost, unknown models/uncertain
cost, cap/deadline interruptions, UTC boundaries, old read-only snapshots,
bounded retention, shared-run-key additional spend and idempotent reruns.
Replay synthetic ambiguous and eligible fixtures without network/paid
calls. Report readiness tests must fail closed for incomplete or too-small
samples. Do not weaken existing evidence or Keepa critic tests.

Run `.venv/bin/python -m pytest -q`, including the offline rerun contract;
name sandbox-blocked checks and never describe a blocked suite as green.
Add one HANDOFF bullet recording the observation start and what activation
evidence remains. Do NOT commit or push; end with what changed and anything
unsure.

## Ten-line summary

1. Start with five bounded fixes grounded in gaps in this checkout.
2. Preserve readable static deals when the first JSON request fails.
3. Add useful phone filters and restore results when visitors come back.
4. Save deals locally, with current facts and honest unavailable states.
5. Connect live titles to useful stable deal pages and count outbound actions.
6. Increase distinct useful supply per AI dollar without weakening checks.
7. Improve existing SEO pages before generating more page combinations.
8. Add local watches and installation after the save/return experience works.
9. Judge phone usability, trust, return visits and actual earning traffic.
10. Keep emails off, budgets fixed, partner credit intact and implementation separate.
