# 276 — Amazon deals: crossed-out price from Amazon's own List Price, never our history

## Why
Operator 2026-10-17 ~04:40Z, after Amazon reviewed the site: price history
may be used only as a hidden backend process — "as long as you don't start
telling them about historical pricing … all that needs to be backend
processing (hidden from the consumer)". Today, Keepa "Amazon price drop"
deals show the 90-day average as the crossed-out price: the deal page says
"Regular price: $X (90-day average price on Amazon)" (`seo._basis_label`),
cards say "60% off the usual $24.99". That is a historical price shown to
the consumer. Operator also: "do whatever looks the best and will get people
to buy buy buy".

## Task (`publish.amazon_reference`: history | list_price; validated; ship **list_price**)
1. For new-condition Amazon deals whose regular price comes from Keepa's
   90-day basis, the *displayed* reference becomes Amazon's own List Price
   (Keepa `csv[4]` / `stats.current[4]`, the "List Price" Amazon itself shows),
   when present, fresh (same reading as the price confirmation) and above the
   final price. Savings math follows SAVINGS_RULES.md in `dealradar/savings.py`
   (new basis `amazon_list_price`; add it to SAVINGS_RULES.md); the LLM is not
   involved. Label: "List price" on cards/deal pages ("List price $24.99").
2. No List Price (or not above final): show no crossed-out price and no
   "% off" for that deal; the card shows a "Price drop" chip instead. The deal
   still qualifies and ranks by the hidden 90-day comparison exactly as
   today (`min_pct` gates and pick_score unchanged — backend only).
3. Never display, anywhere public (cards, deal pages, deals.json, alt text,
   social posts, Pinterest, Telegram, emails, structured data): the 90-day
   average, "usual", "regular price … average", "lowest/highest", or any
   wording implying a past-price comparison, for any store. Source-stated
   was-prices from deal posts keep today's display ("as stated by <source>").
4. Publish floors that read the displayed pct (e.g. `site_show_from_pct`)
   use the hidden pct for Amazon deals without a List Price, so supply does
   not collapse; say so in HANDOFF.
5. Run summary: counts of Amazon deals shown with list price / price-drop chip.

## Tests
savings.py unit tests for `amazon_list_price` (rounding, list ≤ final → no
basis, stale list price ignored); card/deal-page bytes for both paths; a
grep-style test that shipped output for Keepa Amazon deals contains none of
"90-day", "average", "usual", "lowest", "highest", "history"; social/email
formatters likewise; idempotent rerun; OFF (`history`) = today's bytes
(recorded pins set it, "recorded before task 276"; never re-pin).

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task276-*` basetemp
(delete after each run), targeted tests only, never the full suite. Evidence
rule (`parse/extractor.verify_evidence`) untouched. No network, no commit,
no push. HANDOFF "In flight": one entry. Ship `amazon_reference: list_price`.
