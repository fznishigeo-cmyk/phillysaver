# 253b — Photo coverage (task 253): fix the 7 confirmed review findings before it ships ON

## Why

Task 253 (committed here as HEAD, tag cx/253) recovers product identity for
"link to the post" deals so they get photos. An adversarial review with probes on
the live config confirmed the findings below. Two are blockers: 253 removed the
pre-253 rewrite of body-ASIN Amazon deals to `https://www.amazon.com/dp/<ASIN>`,
which (a) moves earning, tagged Amazon "Get deal" links to the source post and
(b) changes dedupe keys so an existing product gets a duplicate row/card. That is
the opposite of the brief: only identity/photo fields may change; Get-deal links,
dedupe keys, credits and earnings must stay exactly as before.

## Task

Fix every finding below using the verified fixes (adapt if they conflict), and
revert the test edits that accepted the regressions (listed in finding 1). Add
tests that pin: (1) the old Amazon dp rewrite and tagged link for single-product
body-ASIN posts, (2) a Keepa row + a post for the same ASIN stay one row, (3)
sale/store/search-page deals never take an example product's identity or photo,
(4) the short-link and post-link click repairs still run, (5) private counters
count only genuinely recovered identities. A scratch copy where the reviewer
applied fixes 1, 2, 3 and 6 with 338 passing tests is at
/tmp/claude-0/-home-user/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/fix
(read-only reference; verify before reusing).

## Rules

Facts only; evidence rules untouched; no new fetches; robots/politeness rules
unchanged. Do not change tests/test_workflows.py. Run tests/test_photo_identity.py,
tests/test_dedupe.py, tests/test_photos_every_deal.py, tests/test_e2e_offline.py,
tests/test_short_links.py, tests/test_post_links.py, tests/test_tracker_links.py,
tests/test_product_images.py and any you touch, with -n 1 and your own basetemp
/tmp/task253b-*; delete it after. Do not run the whole suite. Do not commit.

## Confirmed findings

## [blocker] Get-deal link and dedupe key change: the pre-253 rewrite of body-ASIN Amazon deals to amazon.com/dp/ASIN was removed | dealradar/dedupe.py:288
VERIFIER: Confirmed by probe with the live config.yaml (amazon_tag wowpriced-20, live competitor hosts), parent vs HEAD. (A) Amazon deal, product_url '' with code, body amazon.com/dp/B0GLOVE123: parent gives key url|https://www.amazon.com/dp/B0GLOVE123, Get deal ...?tag=wowpriced-20, earns=True. HEAD gives key si|amazon|gloves rigwarl winter, canonical '', Get deal = the hip2save post, earns=False. (A2) product_url = post URL behaves the same way. (B) amzn.to product_url with resolved_links: parent gives a tagged dp link; HEAD keeps amzn.to, earns=False. (C) A Keepa row for dp/B0THERM123 and then a hip2save post for the same ASIN: parent has 1 row, HEAD has 2. Two contracts were edited to accept this:
FIX: dealradar/dedupe.py upsert_extracted: directly after `asin = extract_asin(identity) if identity else asin_from_links([...])` and before `photo_deal = ...`, insert:

    if not asin_from_links([extracted.product_url, extracted.link_as_posted]) \
            and "amazon" in (extracted.store or "").lower() \
            and len({util.normalize_item(i) for i, _ in post.parsed}) <= 1:
        from .publish.site import keeps_source_tracker
        legacy = asin_from_links([extracted.product_url, extracted.link_as_posted]
                                 + _URL_RE.findall(post.text or "") + list(post.photos.links) + post.resolved_links)
        if legacy and legacy == extract_asin(identity or "") \
                and "amazon." not in (extracted.product_url or "").lower() \
                and not keeps_source_tracker(extracted.product_url or "", cfg):
            extracted = dataclasses.replace(extracted, product_url=f"https://www.amazon.com/dp/{legacy}")

This restores the pre-253 routing only where the stricter identity agrees, so the roundup veto still holds.

Tests:
- tests/test_dedupe.py:286-287: go back to canonical_url == "https://www.amazon.com/dp/B0TARTE123" and dedupe_key == "url|https://www.amazon.com/dp/B0TARTE123". The product_identity_url assertion can stay.
- tests/test_photos_every_deal.py:179: go back to `row['canonical_url'] == (URL if single else '')`.
- tests/test_photo_identity.py::test_source_body_identity_survives_parse_upsert_and_post_routing: add `cfg.affiliate_competitor_tracker_hosts = load_config().affiliate_competitor_tracker_hosts` and import load_config.

I checked this in the scratch copy /tmp/claude-0/-home-user/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/fix. Together with fixes 2, 3 and 6, 338 tests passed across test_dedupe, test_photos_every_deal, test_photo_identity, test_e2e_offline (including rerun idempotency), test_short_links, test_post_links, test_tracker_links and test_product_images. The probes again return the tagged dp link, the url|dp key and a single row in case C.

## [major] Sale, store and search-page deals take the body's one example product as their identity and photo (no_product guard bypassed) | dealradar/photo_recovery.py:89
VERIFIER: Confirmed by probe with live config. A Walmart /browse/... deal, a Kroger home-page deal and an Amazon /s?k=brawny deal each had a body with one example product. HEAD stores that example as product_identity_url and sets image_url to the hip2save featured image. The parent stores image_url '' for all three, because _post_image's no_product rule applies. post_identity never checks whether the deal's own link names no product. _names_no_product returns False for the usual link-to-post own URLs (source post URLs, amzn.to, a.co, viglink to a product, slickdeals/DOA/DealMoon trackers), so the guard does not block the intended recoveries.
FIX: dealradar/photo_recovery.py post_identity: right after the `if extracted.link_shared or len(...) > 1: return ""` check, add:

    from .product_images import _names_no_product
    own_url = extracted.product_url or ""
    if own_url.lower().startswith(("http://", "https://")) and _names_no_product(own_url):
        return ""  # a sale/store/search deal: the body's one product is an example

Verified in the scratch copy: all three probes give identity '' and image_url ''. The focused suite passes (338). Side effects: the Amazon /s?k= case also loses the ASIN that the pre-253 text scan borrowed, which is the intended stricter result. goto.target.com/c/... links already count as no-product under _names_no_product, as they did before.

## [minor] Private photos.recovered_identity counts every new Amazon-link or Keepa deal as 'recovered' | dealradar/photo_recovery.py:154
VERIFIER: Confirmed. On HEAD, identity_summary({'url|...B0OLD12345': False}, [new keepa_deals row whose canonical is dp/B0KEEPA123 and whose identity is its own link]) returns {'recovered_identity': 1, 'by_source': {'keepa_deals': 1}}. Any new row is missing from `before`, so every new deal with an ASIN counts. The shipped test asserts this too: identity_summary({}, rows) counts row 'a', which has only an asin. Duplicate of finding 7.
FIX: dealradar/photo_recovery.py identity_summary: replace the `recovered = ...` comprehension with:

    def borrowed(d):  # a new row's identity that its own links do not name
        own = {link_key(u) for u in (d.get("canonical_url"), d.get("link_as_posted")) if u}
        return bool(d.get("product_identity_url")) and link_key(d["product_identity_url"]) not in own

    recovered = [d for d in live if (d.get("asin") or d.get("product_identity_url")) and (
        before.get(d["dedupe_key"]) is False or (d["dedupe_key"] not in before and borrowed(d)))]

Update tests/test_photo_identity.py::test_private_summary_counts_recovered_sources_once_and_missing_photos:
- The first assertion now passes before={"a": False, "b": False}.
- Add `identity_summary({}, rows)["by_source"] == {"hip2save": 1}`.
- Add a Keepa row with canonical == identity == URL that counts 0.

Verified: the probe returns 0, and the identity tests pass (48).

## [blocker] Removing the pre-253 Amazon product_url rewrite changes Get-deal links and dedupe keys for new deals, and creates duplicate rows for existing deals | dealradar/dedupe.py:288
VERIFIER: Same defect as finding 1, and confirmed by the same probes. HEAD loses the tagged earning link (earns True on the parent, False on HEAD) for empty/post-URL and amzn.to product_url cases. A legacy url|dp row plus a re-sighting from a hip2save post gives 2 rows instead of 1, because find_existing cannot match link_key(post URL) to amazon:ASIN. Run unchanged against HEAD, the pre-253 test_amazon_asin_recovered_from_post_text and test_post_wide_asin_requires_at_most_one_parsed_product fail; the commit edited them to pass.
FIX: Use the fix from finding 1. Insert the identity-gated legacy rewrite block in upsert_extracted after the asin computation and before photo_deal/find_existing. Revert the two edited assertions in test_dedupe.py:286-287 and test_photos_every_deal.py:179. In test_source_body_identity_survives_parse_upsert_and_post_routing, set cfg.affiliate_competitor_tracker_hosts = load_config().affiliate_competitor_tracker_hosts. Verified: 338 focused tests pass and all probes match the parent.

## [major] New identity skips in the short-link and post-link passes stop the existing click-link repair | dealradar/pipeline.py:842
VERIFIER: Line 842 is confirmed separately from fixes 1 and 2. Probe: a Lowes deal with product_url https://shoplowes.me/3xYz9 and body lowes.com/pd/dewalt-20v-drill/1000123456, with resolve_redirects mocked. Parent: resolved 1, canonical and Get deal become the lowes.com product page. HEAD: resolved 0, Get deal stays shoplowes.me. The finding's own goto.target.com/c/... example is weaker, because _names_no_product already treats that path as a listing, so fix 2 removes its identity. Line 937 is also confirmed, but as an edge case: an Amazon row with identity and asin but empty canonical was upgraded to the dp link before 253 (found 1) and is now skipped (found 0). The finding's proposed 937 change wo
FIX: dealradar/pipeline.py _resolve_short_links:
1. Remove `deal.get("product_identity_url") or` from the first skip condition.
2. Right after the `if ((deal.get("image_url") and not tentative_photo) or deal.get("link_shared")) and not tracker: continue` block, add:

    if deal.get("product_identity_url") and not tracker \
            and host.removeprefix("www.") not in _SHORT_LINK_HOSTS:
        continue

_links_from_posts: change the skip to:

    if (deal.get("product_identity_url") and (deal.get("canonical_url")
            or not extract_asin(deal["product_identity_url"]))) or (deal.get("canonical_url") and (
            deal.get("asin") or "amazon" not in (deal.get("store") or "").lower())):
        continue

extract_asin is already imported in that function.

Verified: the shoplowes probe resolves again, the empty-canonical Amazon identity row gets the dp link, the Walmart identity row is not fetched, and test_identity_avoids_repeating_existing_post_and_redirect_photo_fetches plus the focused suite pass.

## [minor] photos.recovered_identity counts every new deal that has an ASIN, not recovered identities | dealradar/photo_recovery.py:154
VERIFIER: Same as finding 3, confirmed by probe: a new Keepa row with its own dp link is counted as recovered_identity 1 on HEAD. The shipped test identity_summary({}, rows) == 2 also counts row 'a', which has only an asin and no prior state.
FIX: Use the identity_summary change from finding 3. Count existing rows only when before.get(key) is False. Count new rows only when link_key(product_identity_url) is not among the link_keys of their own canonical_url/link_as_posted. Update the summary test as in finding 3. Verified: the probe gives 0 and the tests pass.

## [minor] DealMoon identity fixture contradicts the observed card layout, so its positive test does not show live recovery | tests/fixtures/photo_identity/dealmoon_us.html:4
VERIFIER: Confirmed, minor (test validity and an overstated HANDOFF claim).
- The repo's observed-layout fixture, tests/fixtures/pages/dealmoon_us.html, has zoom-brief as an href-less <a>, and an <a> cannot contain another <a>. Its only outbound link is btn-buy exec/j?d=.
- The new tests/fixtures/photo_identity/dealmoon_us.html makes zoom-brief a <div> with an inner exec/j?url=<amazon> anchor.
- That fixture's comment says 'only the buy anchor carries the product', but its buy anchor is the opaque d= link, so the comment contradicts the markup.
- No other file in the repo shows a DealMoon url= anchor, and HANDOFF itself says d= links are not recoverable.
So test_source_body_identity...[dealmoon_us] pa
FIX: 1. Replace tests/fixtures/photo_identity/dealmoon_us.html with the observed shape: <a class="zoom-title" href=".../en/brawny-paper-towels/253001.html">, an href-less <a class="zoom-brief j-read"> with the price text, <a class="btn-buy" href="https://go.dealmoon.com/exec/j?d=253001">.
2. In tests/test_photo_identity.py, set SOURCES = ["slickdeals_frontpage", "dealsofamerica", "hip2save"]. With only the rival link left, the roundup test would otherwise wrongly treat the rival as the single product.
3. Add test_dealmoon_opaque_buy_link_recovers_no_identity: parse and upsert the DealMoon fixture, then assert no product_identity_url and no asin.
4. In HANDOFF, change 'Four supplied ... fixture cases recover identity' to three, and state that observed DealMoon d= cards recover none.
Verified in the scratch copy: test_photo_identity passes 48.

REJECTED:
- Re-enables the retailer og:image fetch pass that da3cfbb turned off; its main non-Amazon target, Walmart, walls crawlers :: The behaviour change is real: da3cfbb set product_images=0 and 253 turns the pass back on with identity_only=True. But the 253 brief asks for exactly this ('For Walmart/other retailers ... else the ex
- identity_changes strips tried flags from deals that already show a photo, changing their public flags :: The mechanics are true: identity_changes drops image_backfill_tried and product_image_tried the first time a row gets an identity, and _serialize_deal emits the flags list. But nothing consumes these 