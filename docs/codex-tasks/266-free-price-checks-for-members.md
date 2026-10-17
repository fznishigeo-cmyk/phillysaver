# 266 — Free live price checks for EVERYONE (design + build, OFF)

## Why
Operator 2026-10-17: be camelcamelcamel-style and give visitors "a free
Keepa token in exchange for their loyalty". Everyone already gets search,
our deals, /p/ price pages and (soon) the best value finder from data we
already pay for. Operator decision 2026-10-17 ~03:10Z: "MAKE IT FREE FOR
EVERYONE — I'm getting 60 tokens per minute" (Keepa plan ≈ 86k tokens/day,
mostly unused). Live price checks on ANY Amazon product for every visitor, no
sign-up, no visible daily limit; guardrails only stop bots and protect the
pipeline's own Keepa needs.

## Task
1. Read how accounts work today (the account service: `meta.api_url`, token,
   isMember/join flow, Stripe/free join, docs/paywall.md) and how /p/ pages,
   Deal Check (/check/) and Keepa budgets work (keepa_budget.py, caps).
2. Design note in docs/ (≤ 1 page): flow, data, caps, abuse controls, Amazon
   and Keepa policy constraints (no ratings/reviews/monthlySold shown; Amazon
   price shown only with "as of" time + disclaimer; links tagged), privacy.
3. Build OFF (`free_checks: {enabled: false, per_ip_per_day: 50,
   per_ip_per_minute: 6, global_tokens_per_day: 20000, cache_hours: 6,
   pipeline_reserve_tokens: …}`): an endpoint in the existing account service
   (or a new Worker route in the same deploy) that takes an ASIN or amazon.com
   product URL from ANY visitor (no sign-in), enforces the global daily token
   cap and the per-IP limits atomically, calls Keepa product (minimal params),
   stores only allowed facts (price now, 90-day low/high, as-of), returns
   them, and caches per ASIN for `cache_hours` (a cache hit costs no token and
   does not count toward any limit). The Keepa key stays server-side.
   **The pipeline always wins**: a lookup is refused (friendly "try again in a
   few minutes", or the cached answer if any) whenever the shared Keepa
   bucket is below the pipeline's reserve (reuse keepa_budget.py's
   reserve/bucket logic and its state; never starve price rechecks, photo
   search or the best value finder).
4. Frontend: on /check/ (Deal Check) and in the homepage search box when the
   query is an Amazon URL or ASIN: result card (price now with "as of" time +
   Amazon disclaimer, 90-day low/high, a plain verdict like "Lower than usual"
   computed in code, tagged Amazon link opening in a new tab), plus a quiet
   "Get an alert when it drops — sign up free" link to the existing join flow
   (optional, never a wall). No visible check counter for normal use.
5. Money caps are law: global cap and pipeline reserve first, then per-IP;
   spend counted by the tokens Keepa actually reports; never retries a 429;
   IPs stored only as a salted daily hash.

## Tests
Offline: cap enforcement (global, per-IP, pipeline reserve, race), cache hit =
no Keepa call and no limit used, allowlisted fields only, no sign-in needed, OFF = today's bytes/endpoints,
no key in any client bytes, rerun idempotency of accounting.

## Rules
Offline only (fake Keepa/HTTP), `-n 1 -p no:cacheprovider`, own
`/tmp/task266-*` basetemp (delete after each run), targeted tests only, never
the full suite. No network, no commit, no push. HANDOFF "In flight": one
entry.
