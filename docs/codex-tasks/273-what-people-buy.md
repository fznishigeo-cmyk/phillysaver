# 273 — What people buy, in their face; open box says "often only 1"

## Why
Operator 2026-10-17 ~03:35Z, scrolling the homepage: the cards that make her
want to buy are bras, 5-pack underwear, socks, an Owala bottle ("I actually
wanted to buy this"), and from Slickdeals: mesh/fishnet lingerie, a $2 bikini
bottom, light bulbs, 2-pack 30 W USB cables, cheap shoes, a hat, a watch.
"Get what people are buying in their face." Christmas and Thanksgiving are
coming. Open-box cards: "there's only one of them ... you click get deal and
there's only one" — say so on the card.

## Task
1. **Want words** (`publish.front_page.appeal.want`, validated; missing = off):
   `enabled`, `factor` (ship 1.2), `max_price` (ship 50), and `groups`
   (ship below; config, not code). A new, price-confirmed deal at or under
   `max_price` whose title matches a group word (whole word, case-insensitive,
   plural-tolerant) gets `factor` in `pick_score`, once (never stacked with
   itself; with staples from 260 and everyday_price the combined lift is
   capped at `factor`). `niche_reason` still wins: a phone-model case never
   rises. Ship groups:
   - basics: bra, bras, bralette, underwear, panties, boxer, briefs, socks,
     leggings, pajamas, lingerie, bodysuit, bikini, swimsuit, slippers,
     sneakers, shoes, hat, beanie, hoodie, sweatshirt
   - carry: owala, stanley, hydro flask, yeti, tumbler, water bottle
   - small wins: light bulbs, led bulbs, usb c cable, charging cable,
     charger, power bank, watch, earbuds, batteries
   - beauty: perfume, makeup, lipstick, mascara, skincare, serum,
     moisturizer, sunscreen, shampoo, conditioner, hair dryer, straightener,
     curling iron, nail, lip gloss
   - wear & carry: sunglasses, purse, handbag, tote, backpack, jewelry,
     earrings, necklace, bracelet, jacket, coat, dress, jeans, pajama set
   - home: towels, sheets, pillow, blanket, throw, candle, storage bins,
     organizer, vacuum, air purifier, humidifier, string lights
   - kitchen: air fryer, knife set, cookware, pan, coffee, k cup, blender,
     food storage, lunch box
   - kids: diapers, wipes, toys, lego, crayons, kids shoes, school backpack,
     baby bottle, sippy cup, stroller
   - everyday: paper towels, toilet paper, detergent, dish soap, trash bags,
     toothpaste, toothbrush, deodorant, razor, vitamins
   - pets & fitness: dog treats, cat litter, dog toy, yoga mat,
     resistance bands, dumbbells
   - allure (operator: "subtly push it"): lingerie, babydoll, bralette,
     corset, satin, silk robe, sleepwear, nightgown, cologne, perfume,
     boxer briefs, swim trunks, date night. Subtle = lifted like the rest,
     but never in the first 6 cards of All (Meta reviews the ad's landing
     page) and at most 1 in any 6 consecutive cards. Mainstream apparel and
     fragrance only: titles matching adult-product words (toy, vibrator,
     sex, adult, erotic, intimate wellness and similar; config list) never
     get any lift (Amazon Associates and Meta rules).
   The list is a starter only; the real signal is what sells. 255's private
   Amazon sales count (popular_affordable) and 261's own-click learner rank
   ALL products by what people buy; this list must not fight them (same cap).
2. **Gift season** (`publish.front_page.appeal.season`, list of windows;
   missing = off): each window has `from`/`to` (MM-DD, New York date),
   `words`, `factor` (≤ 1.15). Ship: Thanksgiving `11-10`..`11-27`
   (roasting pan, baking, casserole, tablecloth, slow cooker, air fryer,
   meat thermometer) and gifts `11-01`..`12-24` (gift set, stocking stuffer,
   perfume, jewelry, watch, toy, lego, pajamas, slippers, blanket). Same
   max_price, same cap, deterministic for a given run time (inject `now`).
3. **Open box says so** (`site.open_box_scarcity`, missing = off): a live
   open_box card's corner tag reads "OPEN BOX · often only 1". If facts we
   already store (no new Keepa calls, no new fetches) give a stock count
   ≤ 3 checked within 2 h, it reads "OPEN BOX · only N left" instead; never
   invent a number. Same on the deal page. Contrast AA in light and dark;
   fits the 390 px card without wrapping the price row.
4. A private line in the run summary: picks lifted by want/season this run
   (counts per group only).

## Tests
Unit: each group matches/doesn't (e.g. "bra" not in "brass", "watch" not in
"watchband"? — decide and test), max_price edge, used/open-box never lifted,
niche still sinks, cap with staples/everyday, season windows at boundaries
(Nov 1 00:00 NY vs UTC), config validation. Template logic test for the
label (count vs. no count, stale count). OFF = today's bytes (recorded pins
pin `want`, `season`, `open_box_scarcity` off, "recorded before task 273";
never re-pin). Phone budgets unchanged.

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task273-*` basetemp
(delete after each run), targeted tests only, never the full suite. No
network, no commit, no push. Math stays in code; no LLM. HANDOFF "In
flight": one entry. Ship all three switches ON in config.yaml.
