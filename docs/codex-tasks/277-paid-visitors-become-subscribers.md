# 277 — Paid visitors become subscribers (Telegram first, email next)

## Why
Operator 2026-10-17 ~04:50Z (strategy to remember): paid ads work for deal
sites when they buy *permanent subscribers*, not single clicks. Big deal
sites run Meta/TikTok ads daily with extreme verified drops as the hook
(e.g. an 85%-off sweater) and put a frictionless capture in front of the
paid visitor — a one-tap Telegram/WhatsApp channel invite, SMS list or a
daily email of the top 80%-off finds. Our only "follow" option today is an
RSS link in the footer. Pay ~$0.50 once for a subscriber who then clicks
our links for a year. Facebook ad test starts 2026-10-17 9am,
utm_campaign=fb_test_oct17.

## Task (each behind its own validated switch; missing = off)
1. **Telegram join, one tap** (`site.telegram_join`, with `url` = the public
   channel link, validated `https://t.me/...`): a "Get deals on Telegram —
   free" button under the search box (all visitors) and a slim dismissible
   top bar for visitors arriving with `utm_source` in a config list
   (ship [facebook, instagram, tiktok]); never both; dismissal remembered
   per device (try/catch storage); no layout shift; phone budgets kept.
   Clicks tracked as `join_telegram` (existing first-party click signal,
   with utm_campaign; no personal data).
2. **Our channel is worth joining** (uses the existing
   `publish/telegram_pub.py` + TELEGRAM_CHANNEL_ID): posts only verified,
   price-confirmed new deals that pass the homepage rules (no used/open box,
   Buy Box rule, impulse/appeal ordering), ≥ 50% off, at most
   `telegram.max_posts_per_day` (ship 24) spread across the day, each post =
   photo + item + price + % off vs Amazon's List Price only (task 276; no
   history wording) + a link to our deal page (not a direct Amazon link —
   Amazon Associates bars Special Links in email/offline/apps; keep Telegram
   on our pages to be safe). Idempotent per run key; never re-post the same
   deal unless the price drops ≥ `resend_price_drop_pct` more.
3. **Email capture for paid visitors** (`site.paid_email_card`): after card
   8 on All, one inline card "Get the best 80%-off finds by email — free"
   using the existing subscribe worker; shown only to `utm_source` visitors
   who have not joined; the 35-second pop-up does not also show to them.
   (The newsletter itself stays `enabled: false` until the operator supplies
   a postal address; addresses just accumulate.)
4. **Ad hooks, ready to upload** (`scripts/ad_hooks.py`, offline): from the
   latest published deals, render 3 square 1080×1080 PNGs of the most extreme
   verified, Buy-Box-confirmed drops (photo, price, List Price crossed out,
   % off; no history wording; "wowpriced.com" footer) into a folder the
   operator downloads. No posting to Meta from code.
5. **Report**: growth report adds Telegram joins and email sign-ups per
   utm_campaign, and cost per subscriber when the operator enters ad spend.

## Tests
Switch OFF = today's bytes (recorded pins, "recorded before task 277"; never
re-pin); bar shows only for listed utm_source, dismissal persists, storage
blocked still renders; Telegram post filter (no used/open box, ≥50%, daily
cap, no history words, deal-page links only); idempotent rerun posts nothing
new; email card/pop-up exclusivity; phone budgets; browser test at 390×844
(skip with exit 77 in the Codex sandbox).

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task277-*` basetemp
(delete after each run), targeted tests only, never the full suite. No
network, no commit, no push. No SMS (US TCPA consent rules; revisit later).
HANDOFF "In flight": one entry. Ship 1 and 3 ON only once `url` is set;
2 ON; 4 is a script.
