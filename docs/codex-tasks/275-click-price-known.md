# 275 — Homepage prices must match the Add-to-cart price (no compromise)

## Why
Operator 2026-10-17 ~04:10Z: adidas Men's D4T Woven Kansas University
Shorts shown at $4.55 (was $32.93, −86%) — wrong on Amazon, after the
JanSport $2.82 ("THIS IS THE 20TH TIME … PRICES NEED TO BE RIGHT, THERE IS
NO COMPROMISE"). `keepa.buybox_rule: on` (int10) only pulls a deal when a
stored Buy Box / Amazon-offer reading shows a >10% gap
(`buybox.would_unconfirm`); a deal whose only evidence is the cheapest
third-party NEW offer (`stats.current[1]`, often one odd size/colour or a
seller without the Buy Box) has `buybox_check.source == "new"` and is never
judged. Hourly #193: buybox_top requested 25, with_buybox 3; shadow
would_hide 0 of 396 — the rule as built barely bites.

## Task (`keepa.click_price_rule`: off | shadow | on; validated; ship **on**)
1. **Know the click price for what visitors see.** Rechecks of new-condition
   Amazon deals that are visible (top picks, spotlight, the first 300 wall
   cards, social-post candidates) request `buybox=1` (+2 tokens/product,
   https://keepa.com/api-docs/product.html#buybox) inside the existing token
   planner: never above the per-minute grant, visible-first, and the token
   plan summary shows the extra cost. Non-visible deals keep today's request.
2. **Judge by the click price.** A public new Amazon deal is *confirmed* only
   when a fresh reading (same clocks as `buybox.fresh_reading`) shows a
   qualified Buy Box total within 10% of `final_price` (`exceeds_price_tolerance`),
   or — when Keepa reports no Buy Box at all — Amazon's own offer (slot 0)
   within 10%. Codes/coupons/Subscribe & Save (`_price_hidden_by_promo`)
   keep today's handling.
   - Known and >10% above → hidden (today's on-mode path).
   - **Unknown** (no qualified Buy Box, no Amazon offer; only a marketplace
     NEW price) → never a top pick, never in the first 120 home cards, rows,
     spotlight, push/social posts; elsewhere (search, category pages) it
     shows the existing "Price not confirmed" label. In `shadow`, only count.
3. **Clothing/shoes sizes.** When the Keepa record already in hand shows the
   ASIN is a variation child (`parentAsin` present) in fashion/shoes and the
   Buy Box is unknown, treat as Unknown; when the Buy Box is known and
   within 10%, the card's chip reads "select sizes" (fact, not a guess).
4. **Run summary** `price_source.click_price`: counts known_ok / known_above /
   unknown / promo for top picks and the wall, extra tokens, and up to 5
   example ASINs with final vs buybox/amazon/new (ASINs and our own prices
   are fine in logs; no seller IDs, no raw responses).
5. Idempotent: re-running the same run key changes nothing; a missing or
   older reading never clears an existing gap (`buybox.remember` rules).

## Tests
Unit: adidas-shaped fixture (slot 1 $4.55, no Buy Box, slot 0 none) is
not a top pick and labeled; Buy Box $4.60 → confirmed; Buy Box $32 → hidden;
Amazon offer only within 10% → confirmed; promo path unchanged; variation
child + unknown → unknown; shadow counts but changes no bytes; token planner
never exceeds the grant with buybox=1 and drops lowest-visibility first;
rerun idempotency (tests/test_e2e_offline.py style). OFF = today's bytes
(recorded pins set the rule off, "recorded before task 275"; never re-pin).

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task275-*` basetemp
(delete after each run), targeted tests only, never the full suite. No
network, no commit, no push. Money caps and the Keepa budget are never
raised. HANDOFF "In flight": one entry. Ship `click_price_rule: on`.
