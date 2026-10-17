# 282 — "Would you click this?" appeal score for the first screens; allure spacing knobs

## Why
Operator 2026-10-17 (screenshot of the live first rows): "make sure the deals
are actually good deals ... the tupperware deal is a shit deal bro ... 4 pieces
of plastic for 20 bucks ... make sure you're pushing deals that when people
visit from facebook they want to click on something ... quick dopamine hits is
the whole name of the game". The critic only judges whether a regular price is
plausible; nothing judges whether a deal is tempting. Also: "women and men are
horny ... as long as it's not dildos or sex toys you don't have to put it way
down ... make sure everything is subtle".

## Task A — appeal score (private ranking signal, ships ON within caps)
- For top-pick candidates only (the best `appeal_score.candidates` by current
  pick_score, ship 80 per run), ask the cheap model (`appeal_score.model`, ship
  the configured Haiku model) in batches of 20 to rate each deal 1–5: "Would a
  deal-hunting woman scrolling Facebook stop and click this right now?" given
  ONLY public facts: title, store, final price, regular price/percent as
  published, category. Reply schema: `[{"key","score"}]`, integers 1–5, nothing
  else stored (no reasons persisted or logged; logs show type/counts only).
- Cache per (dedupe_key, final_price) in the DB for `appeal_score.ttl_h` (ship
  72); a price change re-rates. Reruns of the same run key make no calls and add
  no rows (idempotency contract).
- Money caps: every call goes through the existing per-run/per-day LLM spend
  ledger and pricing by actual model; own caps `appeal_score.max_usd_per_run`
  (ship 0.03) and `max_usd_per_day` (ship 0.40); over cap → unrated deals keep
  factor 1.0. Never route around the global caps.
- Ranking: `appeal_score.factors` (config, ship {1: 0.6, 2: 0.8, 3: 1.0, 4: 1.1,
  5: 1.2}) multiplies pick_score in rank.py like the other soft lifts and shares
  the same combined ceiling logic so no single signal dominates; unrated = 1.0.
  Score 1 deals never enter the first 24 picks (they stay on the wall).
- Never published: the score is not in deals.json, HTML, JSON-LD or logs.
- Missing/false `appeal_score.enabled` = today's bytes and calls exactly
  (recorded pins keep the old values; never re-pin).

## Task B — allure spacing knobs
`publish.front_page.appeal.allure_first` (ship 3: the first allure card may be
card 3) and `allure_every` (ship 4: at most 1 in 4 after that). Today's
behavior is first=7 / every=6; both server (`appeal.space_allure`,
`seo.home_order`) and browser (`spaceAllure`) must read the same knobs and
agree; adult exclusions unchanged (never boosted, never in the first screens).
Missing knobs = today's 7/6 bytes exactly.

## Tests
- Fake LLM client: ratings applied; cache hit = no call; price change re-rates;
  caps stop calls; idempotent rerun; nothing in public bytes/logs.
- A $19.97 4-piece plastic shaker set rated 1 stays out of the first 24; a 4-5
  rated gadget moves up; unrated keeps order.
- Allure: server and real-JS order agree for first=3/every=4 and for missing
  knobs (= old order).
- Real-Chromium smoke (exit 77 skip only if Chromium cannot launch).

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task282-*` basetemp (delete
after each run), targeted tests only. No network, no commit, no push. HANDOFF
"In flight": one entry. Ship ON.
