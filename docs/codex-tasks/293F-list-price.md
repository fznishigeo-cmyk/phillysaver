# 293F — Amazon's own List Price and % off on every live Amazon card

Operator 2026-10-17: "show it like it makes it alluring for them to click on it, like
percentage off or like low price". Rule: Amazon's own numbers only, never invented.
1. functions/api/search.js currentItems: add list_price_cents from Keepa stats.current[4]
   (LISTPRICE) when it is a safe integer > price_cents; omit otherwise. Same for
   q=, asin= and page= answers; cache shape stays backward compatible (old cached
   items without the field still render).
2. Every live Amazon card (#amazon-results, the Analyze "Live Amazon price" card,
   "More from Amazon" pages): when list_price_cents is present and the discount is
   >= 5%, show the List Price struck through and a "-NN%" badge (same look as our deal
   cards, label "List Price" in the accessible text), price math in the browser is
   only Math.round((1 - price/list) * 100) for display (no money math stored). No badge,
   no strike when absent. Never "price drop", never history.
3. Tests: function unit cases (present / absent / <= price / not integer), card markup
   cases, Chromium screenshot run by me.
Rules as in 293.
