# Task 366 — "Deal Spotter by wowpriced": a Fakespot-style Amazon deal grader (ships ON)

Operator (Nishi) 2026-10-22, approved in full: build a page that looks and works
almost exactly like the old Fakespot website (shut down July 1, 2025), but it is
wowpriced and it grades whether an Amazon price is a good deal. Ship it live and
working; it must look beautiful (A+ quality). Read CLAUDE.md first; every rule in
it applies (evidence, money caps, knobs in config.yaml, idempotency, full suite).

## Visual references (untracked, do NOT commit them; they are Mozilla's pages)
`.codex-ref/fakespot/`:
- `g_01..g_15.png` — scroll recording of fakespot.com, top to bottom (How it works
  stepper with the blue illustration, store row, dark video section, "Love
  everything you buy", "As mentioned on", phone carousel, "A Safe Feeling" with
  two phones + Step one/Step two, testimonial cards, footer columns).
- `wayback-page-1..9.png` — the whole fakespot.com homepage (Wayback, 2025-07-08):
  header = logo left, "Paste link to analyze a product" box + blue Analyze button,
  "Menu" (hamburger) right; a thin notice strip under it; hero "Use AI to detect
  fake reviews and scams" centered, sub line, CTA, small "1 million plus shoppers".
- `analyzer-loading.png` — what happens after Analyze: a centered blue modal over
  a dimmed page: logo + "Analyzer", product title, a radar/sonar spinner,
  "Analyzing reviews, please wait..", a white progress bar with a "5.0%" label,
  a step line ("Retrieving product data.."), and a rounded button at the bottom.
- Result page (operator screenshot, described): white page; left a product image
  card with shadow; middle the product title (caps, navy), "Analyzed on <store
  logo>", total count, category, "Share"; two big buttons under it; right column
  "Fakespot review grade" with a large rounded letter box (D was orange), two small
  lines of green/red percentages, "This analysis is outdated. Please reanalyze."
  and a "Reanalyze >" outline button. Below a full-width rule, "Overview" on the
  left and a two-column list of icon bullets on the right.
Match layout, proportions, spacing, typography feel (clean geometric sans, navy
headings, blue #0060DF-ish primary buttons, light grey page bands), the stepper
animation, carousel arrows and the modal. Use `.codex-ref/logo.svg` (our mark) and
the word "wowpriced"; never the Fakespot name, check-mark logo, their images,
their store/press logos or their sentences.

## Name, URLs, switch
- Product name: **Deal Spotter** ("Deal Spotter by wowpriced"). Grade label:
  "Wowpriced deal grade".
- Pages: `/deal-spotter/` (start + loading + result in one page; result state is
  shareable as `/deal-spotter/?asin=B0XXXXXXXX`), and `/fakespot-alternative/`
  (SEO landing page, see below). Add both to the sitemap.
- Config `deal_spotter:` block in config.yaml, `enabled: true` shipped. OFF (false or
  missing) must reproduce every previously published byte (repo convention: add a
  history pin in tests/history_pins.py for any recorded-bytes test you touch, and an
  OFF/missing/back-to-OFF test). Grade thresholds live in this block.
- Site links: footer of all pages + the tool pages' nav/menu (fit on phones: put it
  in the Menu on narrow screens). Keep the homepage and deal cards unchanged.

## Page content (our words; honest; nothing negative about any Amazon product)
Header: logo + "Menu" (opens our site menu: Today's Deals, Deal Checker, Discount
Finder, Deal Spotter, Review Checker [/review-checker/, being built in parallel —
link it], About, FAQ) + compact analyze box.
Notice strip: "New: Deal Spotter is free. No extension needed."
Hero: "Spot fake discounts before you buy" / "Paste any Amazon link and get a deal
grade in seconds." / big link box + Analyze button (primary) / small line "Every
price checked against its source".
How it works (stepper, auto-advancing like Fakespot, with our own blue illustration
built in HTML/CSS/SVG: a product card, a grade badge "A", a "Real deal checked by
wowpriced" pill, a price row):
1. Paste a link — Copy any Amazon product link into the box.
2. We check the price — Today's price, the list price and our verified deals.
3. Spot fake discounts — An inflated "was" price can't fool the grade.
4. Get your grade — A great price, B good price, C fair price.
Store row: "Deals from stores you know:" Amazon, Walmart, Target, Best Buy, Woot
(plain grey text styled like a logo row; no logos).
Dark video section: "Want to learn more? Check out this (not very) expensive demo
we made 🎉" + an autoplaying, looping, muted CSS/SVG animation in a 16:9 frame
(no video file): a product card appears, the four checks tick in, the grade
badge lands on "A". Respect prefers-reduced-motion (static frame).
"Love every deal you buy, and pay less for free" + "Deal Spotter checks the price
before you buy, so you only pay for real discounts." + "Install the free app"
(reuse the existing install button / PWA prompt; hide when not installable) +
small "Every price checked before it goes live".
Stats row in the "As mentioned on" style: real numbers from the published site
data at build time (live verified deals count, number of stores, "prices
re-checked all day"). No press names.
"Your deal guide for desktop or mobile shopping" + paragraph + a phone carousel
(CSS phone frame with arrows) showing simplified live-HTML mock screens of
Deal Spotter, Today's Deals and Discount Finder (no screenshots of other sites).
"A safe feeling, no matter how you shop" + two phones + Step one "Open
wowpriced.com/deal-spotter on your phone or computer." Step two "Paste a product
link and tap Analyze. Your grade appears in seconds."
Card row in the testimonial style, titled "Today's top verified deals": 4 real
Amazon deals from the published deals (photo, title, price, % off, Get deal link
built by the existing link code). No reviews or testimonials of any kind.
Footer in Fakespot's column layout with our links (About, How to use Deal Spotter,
Careers, Today's Deals, Deal Checker, Discount Finder, Review Checker, FAQ,
Contact, Terms, Privacy), social icons (Facebook, Instagram, Pinterest), SEO link
row ("Grade Amazon deals", "Check Amazon reviews", "Today's Amazon deals"), logo,
"Copyright 2026 wowpriced".
Cookie bar (bottom, dismissible, remembered in localStorage): "This website uses
cookies. We use cookies to measure our traffic and improve our ads. Google, Meta
and Microsoft may receive information about your visit." + Privacy link + OK.
(Accurate: the site runs GA4, the Meta Pixel and Microsoft Clarity.)
FAQ (with FAQPage JSON-LD): Is it free? Do I need an extension? What do the grades
mean? Where do prices come from? (reuse the agreed "Is it really AI?" answer from
tool_faq_wording if you mention AI). Is Deal Spotter part of Amazon? (No;
independent; affiliate disclosure.)

## Analyze flow
1. Accept an Amazon product URL (any amazon.com format, short a.co/amzn.to only if
   the existing checker already resolves them) or a bare ASIN; reuse the existing
   Deal Checker parsing. Invalid input: friendly inline message.
2. Loading modal exactly like `analyzer-loading.png` in our colors: logo + "Deal
   Spotter", product title as soon as known, radar spinner, "Analyzing the price,
   please wait..", progress bar with percent, step line cycling "Finding the
   product.." → "Checking today's price.." → "Comparing with verified deals.." →
   "Grading..". Duration: reuse the Deal Checker's rule (random 10–25 s with the bar
   following real time; never finish before the data is back). Bottom button:
   "Install the wowpriced app" (existing install flow) or "See today's deals" when
   not installable. Keyboard/focus trap, Escape cancels, aria-live progress.
3. Result (layout of the Fakespot result page): product image card; title; "Analyzed
   on amazon.com"; today's price + "Price as of <time>" disclaimer exactly as the
   Deal Checker shows it; list price + % off when present; Share (Web Share API,
   copy-link fallback). Right column: "Wowpriced deal grade" + big letter box
   (A green, B teal/light green, C amber — friendly, never red) + one line meaning
   ("Great price" / "Good price" / "Fair price") + "Reanalyze >" button (re-runs,
   cache-respecting). Buttons in place of Fakespot's download buttons: "Get this deal
   on Amazon" (affiliate link via the existing Amazon link builder, same tag/cap
   rules as the Deal Checker) and "See similar deals" (existing similar-deals flow).
   Overview: two-column icon bullets built only from facts we have, e.g. "% below
   the list price", "Verified deal on wowpriced" (ASIN present in the published
   deals), "Price checked N minutes ago", "Popular: N+ bought in the past month"
   only if the Deal Checker already shows monthlySold. Never show past prices, price
   history, "lowest", averages or wording implying a past-price comparison (Amazon
   told the operator this is not allowed; see ops decision 2026-10-17). Never show
   negative wording about a product (no "bad", "overpriced", "fake", "wait", no D/F).
   Add a "Check the reviews →" link to `/review-checker/?asin=...`.

## Grade (code only, deterministic, documented in SAVINGS_RULES.md, tested)
Only A, B or C exist. Inputs from the same Keepa product response the checker uses:
price (the checker's buy box/new price), list price (stats.current[4]), and the
90-day weighted average of that same price series (Keepa stats `avg90`; request
stats=90 instead of 1 only if that does not change token cost or the existing
checker's output; otherwise read `avg90` if present) — this average is used ONLY
inside the grade and is never sent to the browser. Plus whether the ASIN is a live
verified deal in the published deals data.
- A "Great price": price ≤ `a_max_avg_ratio` × avg90 (ship 0.85), or the ASIN is a
  live verified wowpriced deal and price ≤ that deal's price × 1.02.
- B "Good price": price ≤ `b_max_avg_ratio` × avg90 (ship 0.97), or no avg90 and
  list-price discount ≥ `b_min_list_pct` (ship 20).
- C "Fair price": everything else, including missing data.
Compute the grade server-side in the Pages Function (pure exported function with
unit tests), return only {grade, label, facts} to the browser.

## Backend and money caps
Extend `functions/api/search.js` minimally: a `grade=deal` option for `asin=`
requests that adds the grade object to the response. Same KEEPA_KEY, same
SEARCH_BUDGET/SEARCH_LIMITS reservation and settlement, same cache — never a second
budget, never a bypass of `search.tokens_per_day` / `max_per_minute`. A parallel task
(367, Review Checker) adds `grade=reviews` to the same function: keep your change in
one clearly separated block and put pure logic in a new module under functions/api/
that exports no onRequest* handler.

## SEO page /fakespot-alternative/
Title "Fakespot Alternative: Free Amazon Deal Grader | wowpriced". Plain, factual:
Fakespot (by Mozilla) shut down on July 1, 2025; Deal Spotter is a free tool that
grades the price of an Amazon product (it does not read reviews; for reviews link
the Review Checker); CTA to /deal-spotter/. Our layout, not Fakespot's.

## Quality bar and tests (all required)
- Python render tests for both pages (ON/OFF/missing bytes), JS unit tests for
  URL parsing and the grade (A/B/C boundaries, missing data → C, never D).
- Real Chromium (repo's existing browser test harness) at 390×844, 768×1024,
  1280×800 and 1920×1080: start, loading modal mid-progress, result A, B, C, input
  error, cookie bar; no horizontal scroll, no overlap, CLS < 0.01 on load, all
  images sized, focus visible, contrast AA, dark mode readable. Save screenshots to
  tmp_path (never into docs/).
- Keep phone budgets (task 206 tests) green; lazy-load anything below the fold.
- Run the full suite: `.venv/bin/pytest -q -n 3`, all green. Do not push, do not
  commit .codex-ref. Commit your work in this worktree with a clear message.
Reply with: files changed, how OFF preserves bytes, test counts, and paths of the
screenshots you produced.
