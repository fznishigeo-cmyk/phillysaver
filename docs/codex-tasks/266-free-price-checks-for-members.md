# 266 — Free live price checks for signed-up visitors (design + build, OFF)

## Why
Operator 2026-10-17: be camelcamelcamel-style and give visitors "a free
Keepa token in exchange for their loyalty". Everyone already gets search,
our deals, /p/ price pages and (soon) the best value finder from data we
already pay for. Signed-up (free account) visitors get live price checks on
ANY Amazon product, with hard caps.

## Task
1. Read how accounts work today (the account service: `meta.api_url`, token,
   isMember/join flow, Stripe/free join, docs/paywall.md) and how /p/ pages,
   Deal Check (/check/) and Keepa budgets work (keepa_budget.py, caps).
2. Design note in docs/ (≤ 1 page): flow, data, caps, abuse controls, Amazon
   and Keepa policy constraints (no ratings/reviews/monthlySold shown; Amazon
   price shown only with "as of" time + disclaimer; links tagged), privacy.
3. Build OFF (`free_checks: {enabled: false, per_user_per_day: 5,
   global_tokens_per_day: 1500, …}`): an endpoint in the existing account
   service (or a new Worker route in the same deploy) that takes an ASIN or
   amazon.com product URL from a signed-in free member, enforces per-user and
   global daily caps atomically, calls Keepa product (minimal params), stores
   only allowed facts (price now, 90-day low/high, as-of), returns them, and
   caches per ASIN for 6 h (cache hits cost the user nothing). The Keepa key
   stays server-side. Rate limits per IP too.
4. Frontend: on /check/ (Deal Check) and in the homepage search box (when the
   query is an Amazon URL/ASIN): signed-out → "Sign up free to check any Amazon
   price" (existing join flow); signed-in → result card (price, 90-day low,
   as-of, tagged Amazon link, "N free checks left today").
5. Money caps are law: global cap first, then per-user; spend counted by the
   tokens Keepa actually reports; never retries a 429.

## Tests
Offline: cap enforcement (per user, global, race), cache hit = no Keepa call,
allowlisted fields only, signed-out path, OFF = today's bytes/endpoints,
no key in any client bytes, rerun idempotency of accounting.

## Rules
Offline only (fake Keepa/HTTP), `-n 1 -p no:cacheprovider`, own
`/tmp/task266-*` basetemp (delete after each run), targeted tests only, never
the full suite. No network, no commit, no push. HANDOFF "In flight": one
entry.
