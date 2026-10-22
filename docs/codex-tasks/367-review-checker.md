# Task 367 — "Review Checker by wowpriced": a ReviewMeta-style Amazon review grader (ships ON)

Operator (Nishi) 2026-10-22, approved in full: build a page that looks and works
almost exactly like the old ReviewMeta.com, branded wowpriced, live and working,
beautiful (A+). Read CLAUDE.md first; all rules apply (evidence, money caps, knobs in
config.yaml, idempotency, full suite). A sibling task (366, Deal Spotter at
/deal-spotter/, a Fakespot-style price grader) is being built in parallel in another
worktree; link to it, and keep your backend change in its own block (see Backend).

Hard product rules from the operator:
- Never negative about any Amazon product. Only grades A, B, C exist (no D/F, no
  FAIL/WARN, no red). C is a friendly, neutral grade.
- We cannot read Amazon review text (robots/ToS; never fetch amazon.com pages).
  Never claim we detect fake reviews or adjust ratings. We grade the review signals
  Keepa gives us.
- Do not display Amazon's star rating or review count numbers (Amazon content
  rules); show our grade and our checks. Never show past prices.

## Visual references (untracked; do NOT commit)
`.codex-ref/reviewmeta/r_01..r_11.png` — scroll recording of reviewmeta.com (Wayback,
2025): "Why is ReviewMeta.com a Must-Have for Amazon Shoppers?" with a video, "Free
Phone Apps" / "Free Browser Extensions", a bordered "Currently analyzing reviews on"
table, a small independence disclaimer, blue title bars ("Recent Reports", "About
ReviewMeta.com") with an icon, report cards (product photo, "Original" vs "Adjusted"
with an arrow and stars), a long About text, six image tiles with blue captions,
"As seen on" press grid, a blue footer (links, newsletter sign-up, follow-us icons,
logo) and a sticky dark bottom bar "Watch Me: Why is ReviewMeta.com a Must-Have for
Amazon Shoppers?". The ReviewMeta header (from memory): logo left, a wide search box
"Paste an Amazon product link" + blue button. Their report page: product title and
photo; a big score box; a table of tests (Reviewer Participation, Unverified
Purchases, Single-Day Review Spikes, Rating Trend, …) each with a status icon, a
one-line result and an expandable explanation; small charts.
`.codex-ref/fakespot-sibling/analyzer-loading.png` — the loading modal style shared
with Deal Spotter (use the same component idea in Review Checker colors).
Match ReviewMeta's layout, blue palette (#2a6ebb-ish bars, white cards, light grey
borders), density and section order. Use `.codex-ref/logo.svg` and "wowpriced";
never ReviewMeta's name, logo, images, press logos or sentences.

## Name, URLs, switch
- Product name: **Review Checker** ("Review Checker by wowpriced"); grade label
  "Wowpriced review grade".
- Pages: `/review-checker/` (start, loading, report; shareable
  `/review-checker/?asin=B0XXXXXXXX`) and `/reviewmeta-alternative/` (SEO page).
  Sitemap both.
- Config `review_checker:` block, `enabled: true` shipped; OFF/missing reproduces
  every previously published byte (history pins in tests/history_pins.py where
  recorded-bytes tests change; OFF/missing/back-to-OFF test). Thresholds live here.
- Links: footer of all pages + tool pages' nav/menu (Menu on phones). Homepage and
  deal cards unchanged.

## Page content (our words, positive, honest)
Header: logo + wide link box + "Check reviews" button + Menu (Today's Deals, Deal
Checker, Discount Finder, Deal Spotter, Review Checker, About, FAQ).
Intro block (in the "Why is ... a Must-Have" spot): "Why use Review Checker before you
buy on Amazon?" + a looping, muted CSS/SVG animation in a 16:9 frame (a product card,
checks ticking in, the review grade landing on A); prefers-reduced-motion = static.
Under it: "Free on any device" + "Install the free app" (reuse the existing install
button; hide when not installable).
"Currently checking reviews on" bordered table: amazon.com plus real numbers from
the published site data at build time (live verified Amazon deals, stores we cover).
No invented counts.
Small disclaimer: "Review Checker is independent of Amazon. We don't read or change
reviews; we look at how a product's ratings and reviews have built up and show the
checks it passes. Always read a few reviews before you buy."
Blue bar "Today's top deals — check their reviews": 3–6 real Amazon deals from the
published deals (photo, title, price, % off) each with a "Check reviews" button that
runs the checker for that ASIN, and a Get deal link (existing link code).
Blue bar "About Review Checker": 4 short positive paragraphs (what it checks, what
the grades mean, how it works with Deal Spotter and the Deal Checker, free/no
sign-up). No criticism of Amazon or of sellers.
Six tiles with blue captions (our own simple SVG/CSS art): How it works · FAQ ·
What the grades mean · Deal Spotter · Deal Checker · Today's Deals.
(No "As seen on".) Blue footer: links (FAQ, Contact, About, Privacy, Terms), a
newsletter sign-up that reuses the site's existing subscribe endpoint/form, follow-us
icons (Facebook, Instagram, Pinterest), logo, independence + affiliate disclosure.
Sticky dark bottom bar (dismissible): "Watch: Why is Review Checker a must-have for
Amazon shoppers? ↓" scrolling to the animation.
Cookie bar: identical text/behaviour to Deal Spotter's (GA4, Meta Pixel, Clarity are
real): "This website uses cookies. We use cookies to measure our traffic and improve
our ads. Google, Meta and Microsoft may receive information about your visit." +
Privacy + OK (localStorage, shared key with Deal Spotter if 366 lands first).
FAQ with FAQPage JSON-LD: Is it free? Does it read reviews? (No — it looks at rating
and review signals over time.) What do A, B, C mean? Is it part of Amazon? (No.)

## Check flow
Input parsing as the Deal Checker (Amazon URL or ASIN). Loading modal in the shared
style: logo + "Review Checker", product title when known, spinner, "Checking the
reviews, please wait..", percent bar, steps "Finding the product.." → "Reading
rating signals.." → "Checking the review pace.." → "Grading..", duration rule from the
Deal Checker (random 10–25 s following real time; never before data is back),
accessible (focus trap, Escape, aria-live).
Report (ReviewMeta report layout): product photo + title + "Checked on amazon.com"
+ today's price with the Deal Checker's "Price as of" disclaimer; big grade box
("Wowpriced review grade" A green / B teal / C soft amber) + one positive line; a
checks table listing ONLY the checks the product passed, each with a green check, a
short name, a one-line result and an expandable "What this means"; a line "We show
the checks this product passed." Buttons: "Read the reviews on Amazon" (affiliate
product link via the existing Amazon link builder, same tag/cap rules), "Is the
price right? → Deal Spotter" (`/deal-spotter/?asin=`), "Check another product".

## Signals and grade (code only, deterministic, documented in SAVINGS_RULES.md, tested)
Data: the same Keepa product request the checker makes, plus `rating=1` only if it is
needed (it may cost up to 1 extra token — account for it in settlement); prefer
stats fields: current[16] (RATING ×10), current[17] (COUNT_REVIEWS) and the stats
averages for those indices (avg30/avg90/avg180) when present; `monthlySold` when
present. Numbers never reach the browser; return only {grade, label, checks:[{id,
title, result, detail}]}.
Checks (thresholds in config, ship values below):
- plenty_of_reviews: review count ≥ 100 ("Plenty of reviews").
- highly_rated: rating ≥ 4.3 ("Highly rated").
- steady_rating: |current rating − 180-day average rating| ≤ 0.2 when both exist
  ("Steady rating over time").
- natural_pace: reviews gained in the last 30 days ≤ 25% of the total when total ≥
  50 ("Natural review pace" — no sudden burst).
- popular_now: monthlySold ≥ 100 ("Popular right now").
Grade: A when ≥ 4 checks pass including highly_rated; B when ≥ 2 pass; C otherwise
(also when data is missing; C label "Okay — read a few reviews before you buy").

## Backend and money caps
Extend `functions/api/search.js` minimally with `grade=reviews` for `asin=` requests:
same KEEPA_KEY, same SEARCH_BUDGET/SEARCH_LIMITS reservation, settlement (count any
extra token) and cache; never a second budget or a bypass of search.tokens_per_day /
max_per_minute. Task 366 adds `grade=deal` in the same function — keep your change in
one clearly separated block; put pure logic in a new module under functions/api/
that exports no onRequest* handler.

## SEO page /reviewmeta-alternative/
Title "ReviewMeta Alternative: Free Amazon Review Checker | wowpriced". Factual:
ReviewMeta is no longer available; Review Checker is a free tool that grades the
review signals of an Amazon product (it does not read or remove reviews); CTA.

## Quality bar and tests (all required)
- Render tests (ON/OFF/missing bytes), JS unit tests (parsing, every check boundary,
  grade A/B/C, missing data → C, never anything below C, no numbers in the response).
- Real Chromium harness at 390×844, 768×1024, 1280×800, 1920×1080: start page,
  loading mid-progress, reports A/B/C, input error, cookie bar, sticky bar; no
  horizontal scroll, no overlap, CLS < 0.01, focus visible, AA contrast, dark mode
  readable. Screenshots to tmp_path only.
- Phone budgets (task 206 tests) stay green; lazy-load below the fold.
- Full suite `.venv/bin/pytest -q -n 3` all green. Do not push; do not commit
  .codex-ref. Commit in this worktree. Reply with files changed, OFF byte strategy,
  test counts, screenshot paths.
