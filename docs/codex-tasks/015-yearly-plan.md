# Codex task 015: a yearly plan ($49/year) next to the monthly one

**Read first:** AGENTS.md, CLAUDE.md, docs/paywall.md ("Worker API",
"Page behavior"). This is the money path: checkout must never sell the
wrong price, never sell on a failed check, and every price shown next to a
buy button must match what Stripe charges.

Operator ask (2026-10-12): offer "$9.99 a month or $49/year" when people
start the trial, ready now so that putting the paywall back is one switch.
Same 7-day free trial, card up front, for both plans. Monthly stays the
default and unchanged.

## Config
- `config.yaml` `paywall.yearly_price_usd: 49` (page copy only; 0 or
  missing = no yearly anywhere on the page). Load it in `dealradar/config.py`,
  put it in deals.json `meta.yearly_price_usd` (0 when off), test it.
- Worker `wrangler.toml` var `STRIPE_YEARLY_PRICE_ID = ""` (the $49/year
  recurring price, price_...; "" = yearly not sold). Document it with the
  other vars in worker.js's header comment and docs/paywall.md.

## Worker (cloudflare/subscribe-worker)
- `POST /api/checkout` reads an optional JSON body `{"plan": "year"}`;
  anything else, a missing body or bad JSON = monthly. The page's request
  is already signed (NEEDS_SIGNING): keep the body inside whatever the
  signing covers, or check how signing works and keep it valid.
- plan year + `STRIPE_YEARLY_PRICE_ID` set: Checkout Session with that price
  (everything else identical, trial included). plan year without it:
  409 `{error: "plan_unavailable", me}`: no session, no fallback link.
- The open-session reuse (`reuseOrExpire`, params hash, Idempotency-Key)
  must never hand a monthly session to a yearly request or the reverse:
  the price is in the params, so prove it with a test.
- Tests in worker.test.mjs (`npm test` in that folder): month default,
  year price used, year unavailable 409, reuse per plan, bad body = month.

## Page (dealradar/publish/templates/index.html; welcome.html)
Only when `meta.yearly_price_usd > 0`:
- A plan choice next to every checkout button (dialog step 2 `#cobtn` and
  the sticky panel's buy button): two radio pills, "Monthly $9.99" (default)
  and "Yearly $49 · save N%", N computed in JS from the two prices
  (round(100 * (1 - yearly / (12 * 9.99))) = 59). A real radio group:
  labels, keyboard, visible focus; no layout shift when it appears.
  One shared choice (state.plan) for both places.
- `checkout()` sends `{plan}` (extend `apiPost` to take a JSON body);
  `track("begin_checkout", {plan})`. A 409 plan_unavailable shows "Yearly
  isn't available right now. Choose monthly." in the error line.
- The disclosure next to a checkout button follows the plan: trial + year
  "Card required. Free for 7 days, then $49/year, renews yearly until you
  cancel. " + cancelLine(); no trial + year "Subscribe — $49/year, renews
  yearly until you cancel. " + cancelLine(). Monthly lines unchanged.
- Copy that names the price says both: panel and band "your first 7 days
  free, then $9.99/month or $49/year"; signed-in trial "Start your 7-day
  free trial, then $9.99/month or $49/year."; subscribe button text stays.
  Footer "Membership — $9.99/month or $49/year." (fill at render time from
  config, or in renderMeta; your call, keep it simple).
- welcome.html says "Then $9.99/month, renews monthly": make it true for
  both plans (plan-neutral wording, or the plan if the claim response
  already carries the price interval; do not add a Stripe call for it).
- Standing rules: no "cancel" wording on the main screen (panel, band,
  header); the full terms stay next to the button that starts checkout.
  With yearly off (0) the page must be byte-for-byte what it is today in
  behavior and copy: test that too.

## Growth report
`dealradar/growth/stripe_stats.py`: the MRR estimate counts a yearly
subscription as its yearly amount / 12 (from the subscription's price
`recurring.interval` and `unit_amount`, already in the list response when
present), else `monthly_price`. Test with a mix.

## Done
`.venv/bin/python -m pytest -q` and the worker's `npm test`, all green.
Update docs/paywall.md. Do NOT commit, push, or deploy. End your reply with
a short note of what changed and anything you were unsure about.
