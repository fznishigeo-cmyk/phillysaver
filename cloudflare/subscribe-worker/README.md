# subscribe worker — the free daily email list and the paywall API

The only moving part of wowpriced that lives outside GitHub: one Cloudflare
Worker + KV namespace. It holds the newsletter's subscriber list (the site's
forms POST to it, people unsubscribe through it, the pipeline reads the list
through it) and, since the paywall (`docs/paywall.md`), the member accounts,
their sessions, the Stripe membership check, Stripe Checkout (the card-up-front
trial) and the billing portal, and the members-only full deals payload. It never sends mail; `dealradar/publish/newsletter.py` does that over
the SMTP secrets, once per subscriber per day.

```
browser ──POST /subscribe──▶ Worker ──▶ KV  sub:<email> = {ts, token, confirmed:false, confirm_sent_at:""}
GitHub Actions ──GET /list?pending=1──▶ mails confirm links ──POST /list/confirm-sent──▶ stamped
subscriber ──GET /confirm?e=&t=──▶ Worker ──▶ confirmed:true
GitHub Actions ──GET /list (Bearer NEWSLETTER_SECRET)──▶ Worker ──▶ confirmed emails + tokens
subscriber ──GET /unsubscribe?e=&t=──▶ page with a button ──POST──▶ KV delete

GitHub Actions ──POST /api/publish (Bearer NEWSLETTER_SECRET)──▶ Worker ──▶ KV deals:full
browser ──POST /api/signup|login|google──▶ Worker ──▶ KV acct:<email>, session token back
browser ──POST /api/checkout──▶ Worker ──▶ Stripe Checkout Session ──▶ {url}: card + trial on Stripe's page
welcome.html ──POST /api/claim {session_id}──▶ Worker ──▶ Stripe Checkout Session ──▶ acct.stripe_customer
browser ──GET /api/deals (Bearer <session>)──▶ Worker ──▶ Stripe: trialing/active? ──▶ the full payload
browser ──POST /api/portal──▶ Worker ──▶ Stripe billing portal session ──▶ {url}: cancel, card, invoices
```

Files: `worker.js` (the router, `/subscribe`, `/list`, `/list/confirm-sent`),
`subscribers.js` (the subscriber record, the `/confirm` and `/unsubscribe`
pages), `api.js` (the `/api/*` handlers), `auth.js` (accounts, password
hashing, sessions, Google ID tokens), `membership.js` (the Stripe membership
check and `me`), `billing.js` (Checkout, the price, the billing portal),
`util.js` (CORS, JSON, isolate memory, rate limits, base64url). wrangler
bundles the ES modules; `main` in wrangler.toml is `worker.js`.

## Endpoints

### Newsletter

| Route | What it does |
|---|---|
| `POST /subscribe` | `{"email": ...}` JSON or a form field `email`. Validates, lowercases, stores `sub:<email>` as `{ts, token, confirmed: false, confirm_sent_at: ""}` with a random token (idempotent: signing up twice keeps the first record, confirmed or not). JSON callers get `200 {"ok": true}`; form posts get a `303` back to `<site>/subscribe.html?ok=1` (or `?err=email`). Abuse control: 20 posts per client IP per hour. |
| `GET /confirm?e=<email>&t=<token>` | Double opt-in: sets `confirmed: true` when the token matches, 200 HTML "You're confirmed" linking to `<site>/subscribe.html?confirmed=1`; a bad link is 403. Records written before double opt-in (no `confirmed` key) count as confirmed. |
| `GET /unsubscribe?e=<email>&t=<token>` | 200 HTML page with one POST button "Unsubscribe" (403 when the token does not match). A GET never deletes: mail scanners prefetch links. |
| `POST /unsubscribe?e=<email>&t=<token>` | Deletes the record when the token matches, 200 HTML "You're unsubscribed" (also when already gone). The page's button and the RFC 8058 one-click form mail apps send both land here; `e`/`t` as form fields work too. |
| `GET /list` | `Authorization: Bearer <NEWSLETTER_SECRET>` -> `{"emails": [{"email", "token"}]}` of **confirmed** addresses, at most 10,000 (KV list pagination; the record rides in key metadata so a page of 1,000 costs one KV operation). `?pending=1` lists the unconfirmed addresses not yet sent a confirm link instead. |
| `POST /list/confirm-sent` | Same bearer, `{"emails": [...]}` (at most 1,000): stamps `confirm_sent_at` on those pending records so the next `?pending=1` leaves them out -> 204; 400 on a bad body. |
| `OPTIONS *` | CORS preflight for `ALLOWED_ORIGINS` only (`GET, POST, OPTIONS`; `Authorization, Content-Type`). |
| anything else | 404. |

### Paywall (`docs/paywall.md` "Worker API")

JSON in and out; every error is `{"error": "<code>"}` with the status below,
never a stack trace. `me` is `{email, member, reason, trial_ends_at,
trial_days, trial_available, email_verified, claimed, subscribe_url,
portal_url}`: `member` only with a Stripe subscription that is `active` or
`trialing` (no card-less trial); `reason` is `subscribed` (active), `trial`
(Stripe trialing; `trial_ends_at` is its `trial_end`, else null), `lapsed` (a
bound customer, nothing live) or `none`; `trial_days` is `TRIAL_DAYS`;
`trial_available` is true until the account has had any subscription (no
`trial_used`, no bound customer, none in Stripe's membership answer);
`claimed` says a Stripe customer is bound;
`subscribe_url` is `STRIPE_PAYMENT_LINK?prefilled_email=<email>` (empty when
the var is empty) and `portal_url` is `STRIPE_PORTAL_LINK` or "".

| Route | Auth | Body | Response |
|---|---|---|---|
| `POST /api/signup` | none | `{email, password}` | `200 {token, me}`; `400 invalid_email` / `weak_password` (under 8 chars); `409 exists`. The address is unverified: anyone can type it |
| `POST /api/login` | none | `{email, password}` | `200 {token, me}`; `401 bad_credentials` (unknown address, wrong password and Google-only account all cost one PBKDF2 run). A hash made with an older scheme or round count is re-made after a good login |
| `POST /api/google` | none | `{credential}` (Google ID token) | `200 {token, me}`; `401 bad_token`. `aud == GOOGLE_CLIENT_ID`, `iss`, `exp` and `email_verified` are checked before any network call, then RS256 against Google's JWKS (cached an hour; an unknown `kid` forces a refetch at most once a minute). Finds or creates the account (verified, free). A verified sign-in **takes over** an unverified password account on the same address (password cleared, every earlier session of that account invalidated, and the squatter's Stripe binding dropped: `stripe_customer`, `claimed_at`, `trial_used`, and `claim:<customer>` when it names this account) |
| `POST /api/checkout` | `Bearer <session>` | `{}` or empty | `200 {url}`: a subscription Checkout Session (`payment_method_types[0]=card`, the card always collected, `TRIAL_DAYS` of trial while the account has never had a subscription, `client_reference_id` = the account reference, success `<SITE_URL>/welcome.html?session_id={CHECKOUT_SESSION_ID}`, cancel `<SITE_URL>/?checkout=cancel`, an `Idempotency-Key` per account per 10 minutes). First membership is asked strictly, then every subscription of the customers with the account's email (`/v1/customers?email=`): `409 already_member` (with `me`) for a live one anywhere, no trial (and `trial_used` recorded) for any one at all. `503 checkout_unavailable` with `fallback_url` `""` when either check could not ask Stripe (never sell on a failed check), with the Payment Link when the session failed or there is no price; `401`. 10 per IP per hour |
| `POST /api/portal` | `Bearer <session>` | `{}` or empty | `200 {url}`: a billing portal session for the bound customer under the wowpriced portal configuration (`STRIPE_PORTAL_CONFIG`, else the active one with `metadata.app = wowpriced` that `scripts/stripe_setup.py` made, else the dashboard default), returning to `<SITE_URL>/`; `404 no_customer`; `503 portal_unavailable` with `fallback_url` (`STRIPE_PORTAL_LINK` or ""); `401` |
| `POST /api/claim` | `Bearer <session>` | `{session_id}` from the `welcome.html?session_id={CHECKOUT_SESSION_ID}` redirect (our checkout or the Payment Link) | `GET /v1/checkout/sessions/<id>?expand[]=subscription`; needs `status: complete`, `mode: subscription` and a customer; `403 wrong_account` when its `client_reference_id` is another account's; `409 already_claimed` when that customer is bound to another account (KV `claim:<customer>`, written by its first claim); binds `acct.stripe_customer` (the typed payer email never verifies the address) and sets `trial_used` when the subscription is or was trialing. `200 {me}`; `400 bad_session`; `404 unknown_session`; `502 stripe_unavailable`; `401`. 10 per IP per hour |
| `GET /api/me` | `Bearer <session>` | — | `200 me`; `401`. `?fresh=1` skips the 5-minute Stripe cache, at most once a minute per account |
| `GET /api/deals` | `Bearer <session>` | — | `200` the full payload (as published) when `me.member`; `402 me` when not; `401` without a session; `404 no_payload` before the first publish |
| `POST /api/logout` | `Bearer <session>` | — | `204`; `401` |
| `POST /api/publish` | `Bearer <NEWSLETTER_SECRET>` | the full payload (JSON with a `deals` array) | `204`; `400 bad_payload`; `401` |
| any other `/api/*` | | | `404 not_found` |

`signup`, `login`, `google`, `claim` and `checkout` answer `503
signin_unavailable` while `SESSION_SECRET` is unset, before touching KV or
Stripe; a new account is written only after its session token signed.

Sessions are stateless: `base64url({"email", "sv", "exp"}).base64url(hmac-sha256)`
signed with `SESSION_SECRET`, 30 days, compared in constant time.

Membership: no card-less trial. `member` only when Stripe has an `active` or
`trialing` subscription (the trial is Stripe's, card on file) for
(a) the customer bound by `/api/claim` (`/v1/subscriptions?customer=&status=all`),
(c) the account's own checkouts, claimed or not: every Worker checkout stamps
`metadata[wowpriced_ref]`, found with one Search call
(`/v1/subscriptions/search?query=metadata['wowpriced_ref']:'<ref>'`, any status;
Search lags about a minute), or (b) the account's email, **only when
`email_verified`**: the Worker pages
`/v1/subscriptions?status=active|trialing&expand[]=data.customer` and compares
`customer.email` lowercased, as `dealradar/publish/members.py` does (the
`customers?email=` filter is exact-case). A password sign-up with someone
else's address is never looked up by email, so it inherits nothing. Active
beats trialing. Stripe's answer (the best live subscription, `{status,
trial_end}`, or none, and whether any subscription of the account was seen,
which ends `trial_available`) is cached 5 minutes in isolate memory; a Stripe
error counts as "not a member" for that request only (logged, never cached),
except in checkout, which answers 503 instead. Checkout's price is `STRIPE_PRICE_ID`, else the
price of the active Payment Link whose URL is `STRIPE_PAYMENT_LINK` (looked
up once, cached 6 hours in memory and a day in KV `stripe:price`). Stripe's
error text never reaches a client: the Worker logs status and path only.
Abuse control per client IP per hour, in memory: 10 signups, 20 logins,
20 Google posts, 10 claims, 10 checkouts -> `429 rate_limited`.
Passwords, tokens and secrets are never logged.

Passwords (scheme 2): `HMAC-SHA256(SESSION_SECRET, password)` as a
server-side pepper, then PBKDF2-SHA256 with a 16-byte salt and
`PBKDF2_ITERATIONS` rounds (var, default 20,000: ~3 ms CPU, inside the Free
plan's 10 ms per request; 100,000 would cost ~17 ms). Each account stores its
own `{scheme, iterations, salt, hash}` and is verified with them; scheme-1
records (pre-pepper, 100,000 rounds) still log in and are re-hashed with the
current params. Rotating `SESSION_SECRET` therefore invalidates every
scheme-2 password as well as every session.

## What KV holds

| Key | Value |
|---|---|
| `sub:<email>` | `{ts, token, confirmed, confirm_sent_at}` (also in metadata); no `confirmed` key = confirmed (pre double opt-in) |
| `acct:<email>` | `{email, scheme, iterations, salt, hash, created_at, google_sub, email_verified, stripe_customer, claimed_at, trial_used, session_version}`; `scheme|iterations|salt|hash` null for a Google-only account; a session token carries the version it was signed under and dies when a takeover bumps it; older records may still carry an unused `trial_ends_at` |
| `deals:full` | the full payload exactly as the pipeline posted it |
| `jwks:google` | Google's signing keys, one hour |
| `stripe:price` | `{link, price}`: the Payment Link's price for checkout, one day |
| `stripe:portal` | `{mode, config}`: the wowpriced billing-portal configuration for that key mode (live/test), one day |
| `claim:<customer>` | the email of the one account that Stripe customer is bound to (written by its first claim; deleted by a Google takeover that unbinds it) |

Nothing else: no post text, no card data. Abuse counters (`rl:*`), the
5-minute membership cache (`member:<email>`) and the `?fresh=1` throttle live
in isolate memory (best effort; an isolate restart only means a fresh counter
or one more Stripe call), which keeps KV writes to what the Free plan allows
(1,000 a day): one per sign-up, one per subscriber per step
(subscribe, confirm-sent, confirm), one per claim (two on a customer's first
claim: `claim:<customer>` too), one per re-hash, at most one per account when
checkout first finds an earlier subscription (`trial_used`), one per price
or portal-configuration lookup (at most daily).

## Deploy (once, then whenever the code changes)

Run the **deploy subscribe worker** workflow from the repository's Actions tab
(`.github/workflows/deploy-subscribe-worker.yml`). Repository secrets:

| Secret | Where it comes from |
|---|---|
| `CLOUDFLARE_API_TOKEN` | Cloudflare dashboard -> My Profile -> API Tokens -> Create Token -> template "Edit Cloudflare Workers". |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare dashboard -> Workers & Pages -> Overview, right-hand column "Account ID". |
| `NEWSLETTER_SECRET` (required) | Any long random string (e.g. `openssl rand -hex 32`). The same secret lets the pipeline call `/list` and `/api/publish`. |
| `SESSION_SECRET` | Another long random string; signs session tokens. Skipped with a notice when empty (nobody can sign in until it is set and the workflow re-run). Changing it signs everyone out **and invalidates every password** (it peppers the hashes); set it once. |
| `STRIPE_SECRET_KEY` | The live Stripe secret key the pipeline already uses. Skipped with a notice when empty (nobody is a member and checkout is off until then). The workflow reports its mode (live or test, from the prefix; never the key) and warns when it differs from the Payment Link's. A restricted key (`rk_`) needs write on Checkout Sessions and Customer portal, read on Subscriptions, Customers, Payment Links and Prices. |

Workflow inputs (all optional) set the public vars on the deployed copy of
`wrangler.toml` for that run: `google_client_id` (`GOOGLE_CLIENT_ID`, the
OAuth Web client ID; the Google button stays off while empty),
`stripe_payment_link` (`STRIPE_PAYMENT_LINK`), `stripe_price_id`
(`STRIPE_PRICE_ID`; empty = the Payment Link's price), `stripe_portal_link`
(`STRIPE_PORTAL_LINK`), `trial_days` (`TRIAL_DAYS`, the Stripe trial, default
7; 0 = none). Re-run the workflow with the inputs filled in to change them; an
empty input keeps the value in `wrangler.toml`, so commit real values there
to make them stick across deploys (none of them is secret).
`PBKDF2_ITERATIONS`, `SITE_URL` (default `https://wowpriced.com`, where
Checkout and the portal send people back) and `STRIPE_PORTAL_CONFIG`
(optional: a `bpc_...` id; empty = the configuration `scripts/stripe_setup.py`
made) are set in `wrangler.toml` only.

In Stripe (same mode as the key: live for real customers):

- run the **stripe setup (one-off)** workflow with the same key: it creates
  (or finds) the product, the price, the card-only Payment Link and the
  billing-portal configuration (`metadata.app = wowpriced`: cancel at period
  end, card update, invoices) that `POST /api/portal` uses;
- set the Payment Link's after-payment behaviour to redirect to
  `https://wowpriced.com/welcome.html?session_id={CHECKOUT_SESSION_ID}`: that
  page claims the payment for the signed-in account (`POST /api/claim`), for
  the Worker's checkout and the Payment Link alike. Without the redirect, only
  a verified (Google) address reaches a Payment Link subscription;
- activate the **customer portal** (Settings -> Billing -> Customer portal)
  with **cancellation allowed** and payment-method updates on, and put its
  login link in `STRIPE_PORTAL_LINK`: the "Manage or cancel" link for members
  with no bound customer, and the fallback (the dashboard settings are also
  what `POST /api/portal` uses when no wowpriced configuration exists).

Members cancel either way: online in the customer portal ("Manage or
cancel"), or by emailing hello@wowpriced.com (`config.yaml` `contact_email`),
and the operator cancels the subscription in the Stripe dashboard (Customers
-> the member -> the subscription -> Cancel). No reminder email before a
trial ends is set up (operator decision, 2026-10-11).

The workflow creates the KV namespace `wowpriced_subs` if it is missing,
substitutes its id (and the inputs) into a copy of `wrangler.toml`, runs
`wrangler deploy`, puts the secrets on the Worker and prints the Worker URL
(`https://wowpriced-subscribe.<your-subdomain>.workers.dev`) in the job
summary. Paste that one URL into `config.yaml` twice:

```yaml
newsletter:
  worker_url: "https://wowpriced-subscribe.<your-subdomain>.workers.dev"
paywall:
  api_url: "https://wowpriced-subscribe.<your-subdomain>.workers.dev"
```

The next site deploy carries them in `deals.json` meta (`newsletter_url`,
`api_url`), the forms and the sign-in modal start talking to the Worker, the
pipeline starts sending the daily email at `newsletter.hour_local` and
uploading the full payload after every render.

Manual deploy from a laptop (same steps the workflow runs):

```sh
cd cloudflare/subscribe-worker
npx wrangler kv namespace create wowpriced_subs      # once; note the id
sed 's/REPLACED_AT_DEPLOY/<that id>/' wrangler.toml > wrangler.deploy.toml
npx wrangler deploy --config wrangler.deploy.toml
for s in NEWSLETTER_SECRET SESSION_SECRET STRIPE_SECRET_KEY; do
  printf '%s' "${!s}" | npx wrangler secret put "$s" --config wrangler.deploy.toml
done
```

## Tests

Offline, under node's built-in runner (an in-memory KV, a fetch stub for
Stripe and Google's JWKS, a locally generated RS256 key pair standing in for
Google), from the repository root:

```sh
node --test cloudflare/subscribe-worker/
```

`package.json` points `main` at `worker.test.mjs` so that command resolves
the directory to the test file (node 22 takes files and globs, not
directories); wrangler takes its entry from `wrangler.toml`, not from there.
`tests/test_workflows.py` also runs the newsletter routes under pytest.

## Smoke test

```sh
W=https://wowpriced-subscribe.<sub>.workers.dev
curl -s -X POST -H 'Content-Type: application/json' -d '{"email":"you@example.com"}' $W/subscribe
curl -s -H "Authorization: Bearer $NEWSLETTER_SECRET" $W/list
curl -s -H "Authorization: Bearer $NEWSLETTER_SECRET" "$W/list?pending=1"
curl -s "$W/confirm?e=you@example.com&t=<token from the pending list>"
curl -s "$W/unsubscribe?e=you@example.com&t=<token>"          # the page with the button
curl -s -X POST "$W/unsubscribe?e=you@example.com&t=<token>"  # what the button sends

curl -s -X POST -H 'Content-Type: application/json' -d '{"email":"you@example.com","password":"long enough"}' $W/api/signup
T=<token from that reply>
curl -s -H "Authorization: Bearer $T" "$W/api/me?fresh=1"     # member:false, reason:none, trial_available:true
curl -s -X POST -H "Authorization: Bearer $T" $W/api/checkout  # {url}: Stripe Checkout, card required
curl -s -H "Authorization: Bearer $T" $W/api/deals | head -c 300
```

## Costs and limits

Fits the Workers Free plan: two `/list` calls and one `/api/publish` per
pipeline run, sign-ups, and one `/api/me` + `/api/deals` per member visit,
well under the 100k/day request and the KV read/list allowances; Stripe is
asked at most once per member per 5 minutes. `/list` returns at most 10,000
subscribers. Two Free-plan limits do bite as the site grows, and **Workers
Paid ($5/month) is recommended** before they do:

- **CPU: 10 ms per request** on Free (30 s on Paid). Password hashing is the
  only heavy step; `PBKDF2_ITERATIONS` defaults to 20,000 (~3 ms) for that
  reason. A legacy 100,000-round hash costs ~17 ms once, at the login that
  re-hashes it. On Paid, raise the var (100,000 is the usual recommendation).
- **KV writes: 1,000 per day** on Free (Paid: pay as you go, 1 million
  included). Every sign-up, subscribe, confirm-sent, confirm, claim and
  re-hash is one write; a busy launch day can exhaust the quota, after which
  writes fail until midnight UTC (reads keep working, so existing members are
  unaffected).
