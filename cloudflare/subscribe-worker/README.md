# subscribe worker — the free daily email list and the paywall API

The only moving part of wowpriced that lives outside GitHub: one Cloudflare
Worker + KV namespace. It holds the newsletter's subscriber list (the site's
forms POST to it, people unsubscribe through it, the pipeline reads the list
through it) and, since the paywall (`docs/paywall.md`), the member accounts,
their sessions, the Stripe membership check and the members-only full deals
payload. It never sends mail; `dealradar/publish/newsletter.py` does that over
the SMTP secrets, once per subscriber per day.

```
browser ──POST /subscribe──▶ Worker ──▶ KV  sub:<email> = {ts, token, confirmed:false, confirm_sent_at:""}
GitHub Actions ──GET /list?pending=1──▶ mails confirm links ──POST /list/confirm-sent──▶ stamped
subscriber ──GET /confirm?e=&t=──▶ Worker ──▶ confirmed:true
GitHub Actions ──GET /list (Bearer NEWSLETTER_SECRET)──▶ Worker ──▶ confirmed emails + tokens
subscriber ──GET /unsubscribe?e=&t=──▶ page with a button ──POST──▶ KV delete

GitHub Actions ──POST /api/publish (Bearer NEWSLETTER_SECRET)──▶ Worker ──▶ KV deals:full
browser ──POST /api/signup|login|google──▶ Worker ──▶ KV acct:<email>, session token back
welcome.html ──POST /api/claim {session_id}──▶ Worker ──▶ Stripe Checkout Session ──▶ acct.stripe_customer
browser ──GET /api/deals (Bearer <session>)──▶ Worker ──▶ Stripe? trial? ──▶ the full payload
```

Files: `worker.js` (the router, `/subscribe`, `/list`, `/list/confirm-sent`),
`subscribers.js` (the subscriber record, the `/confirm` and `/unsubscribe`
pages), `api.js` (the `/api/*` handlers), `auth.js` (accounts, password
hashing, sessions, Google ID tokens), `membership.js` (Stripe + trial),
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
email_verified, claimed, subscribe_url, portal_url}` where `reason` is
`subscribed` (Stripe), `trial` or `expired`, `claimed` says a Stripe customer
is bound to the account, `subscribe_url` is
`STRIPE_PAYMENT_LINK?prefilled_email=<email>` (empty when the var is empty)
and `portal_url` is `STRIPE_PORTAL_LINK` or "".

| Route | Auth | Body | Response |
|---|---|---|---|
| `POST /api/signup` | none | `{email, password}` | `200 {token, me}`; `400 invalid_email` / `weak_password` (under 8 chars); `409 exists`. The address is unverified: anyone can type it |
| `POST /api/login` | none | `{email, password}` | `200 {token, me}`; `401 bad_credentials` (unknown address, wrong password and Google-only account all cost one PBKDF2 run). A hash made with an older scheme or round count is re-made after a good login |
| `POST /api/google` | none | `{credential}` (Google ID token) | `200 {token, me}`; `401 bad_token`. `aud == GOOGLE_CLIENT_ID`, `iss`, `exp` and `email_verified` are checked before any network call, then RS256 against Google's JWKS (cached an hour; an unknown `kid` forces a refetch at most once a minute). Finds or creates the account (verified; the trial starts on first sign-in). A verified sign-in **takes over** an unverified password account on the same address (password cleared, every earlier session of that account invalidated) |
| `POST /api/claim` | `Bearer <session>` | `{session_id}` from the Payment Link's `welcome.html?session_id={CHECKOUT_SESSION_ID}` redirect | `GET /v1/checkout/sessions/<id>`; needs `status: complete`, `mode: subscription` and a customer; binds `acct.stripe_customer` (the typed payer email never verifies the address). `200 {me}`; `400 bad_session`; `404 unknown_session`; `502 stripe_unavailable`; `401`. 10 per IP per hour |
| `GET /api/me` | `Bearer <session>` | — | `200 me`; `401`. `?fresh=1` skips the 5-minute Stripe cache, at most once a minute per account |
| `GET /api/deals` | `Bearer <session>` | — | `200` the full payload (as published) when `me.member`; `402 me` when not; `401` without a session; `404 no_payload` before the first publish |
| `POST /api/logout` | `Bearer <session>` | — | `204`; `401` |
| `POST /api/publish` | `Bearer <NEWSLETTER_SECRET>` | the full payload (JSON with a `deals` array) | `204`; `400 bad_payload`; `401` |
| any other `/api/*` | | | `404 not_found` |

`signup`, `login`, `google` and `claim` answer `503 signin_unavailable` while
`SESSION_SECRET` is unset, before touching KV; a new account is written only
after its session token signed.

Sessions are stateless: `base64url({"email", "exp"}).base64url(hmac-sha256)`
signed with `SESSION_SECRET`, 30 days, compared in constant time.

Membership: `member` while `now < trial_ends_at` (`TRIAL_DAYS` after
sign-up), or when Stripe has an `active` or `trialing` subscription for
(a) the customer bound by `/api/claim` (`/v1/subscriptions?customer=&status=all`)
or (b) the account's email, **only when `email_verified`**: the Worker pages
`/v1/subscriptions?status=active|trialing&expand[]=data.customer` and compares
`customer.email` lowercased, as `dealradar/publish/members.py` does (the
`customers?email=` filter is exact-case). An account that is neither bound nor
verified is never looked up, so a password sign-up with someone else's
address inherits nothing. Stripe's answer is cached 5 minutes in isolate
memory; a Stripe error counts as "not subscribed" for that request only
(logged, never cached). Abuse control per client IP per hour, in memory:
10 signups, 20 logins, 20 Google posts, 10 claims -> `429 rate_limited`.
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
| `acct:<email>` | `{email, scheme, iterations, salt, hash, created_at, trial_ends_at, google_sub, email_verified, stripe_customer, claimed_at, session_version}`; `scheme|iterations|salt|hash` null for a Google-only account; a session token carries the version it was signed under and dies when a takeover bumps it |
| `deals:full` | the full payload exactly as the pipeline posted it |
| `jwks:google` | Google's signing keys, one hour |

Nothing else: no post text, no card data. Abuse counters (`rl:*`), the
5-minute membership cache (`member:<email>`) and the `?fresh=1` throttle live
in isolate memory (best effort; an isolate restart only means a fresh counter
or one more Stripe call), which keeps KV writes to what the Free plan allows
(1,000 a day): one per sign-up, one per subscriber per step
(subscribe, confirm-sent, confirm), one per claim, one per re-hash.

## Deploy (once, then whenever the code changes)

Run the **deploy subscribe worker** workflow from the repository's Actions tab
(`.github/workflows/deploy-subscribe-worker.yml`). Repository secrets:

| Secret | Where it comes from |
|---|---|
| `CLOUDFLARE_API_TOKEN` | Cloudflare dashboard -> My Profile -> API Tokens -> Create Token -> template "Edit Cloudflare Workers". |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare dashboard -> Workers & Pages -> Overview, right-hand column "Account ID". |
| `NEWSLETTER_SECRET` (required) | Any long random string (e.g. `openssl rand -hex 32`). The same secret lets the pipeline call `/list` and `/api/publish`. |
| `SESSION_SECRET` | Another long random string; signs session tokens. Skipped with a notice when empty (nobody can sign in until it is set and the workflow re-run). Changing it signs everyone out **and invalidates every password** (it peppers the hashes); set it once. |
| `STRIPE_SECRET_KEY` | The live Stripe secret key the pipeline already uses. Skipped with a notice when empty (only trials count as members until then). |

Workflow inputs (all optional) set the public vars on the deployed copy of
`wrangler.toml` for that run: `google_client_id` (`GOOGLE_CLIENT_ID`, the
OAuth Web client ID; the Google button stays off while empty),
`stripe_payment_link` (`STRIPE_PAYMENT_LINK`), `stripe_portal_link`
(`STRIPE_PORTAL_LINK`), `trial_days` (`TRIAL_DAYS`, default 7). Re-run the
workflow with the inputs filled in to change them; an empty input keeps the
value in `wrangler.toml`, so commit real values there to make them stick
across deploys (none of them is secret). `PBKDF2_ITERATIONS` is set in
`wrangler.toml` only.

In the Stripe dashboard, set the Payment Link's after-payment behaviour to
redirect to `https://wowpriced.com/welcome.html?session_id={CHECKOUT_SESSION_ID}`:
that page claims the payment for the signed-in account (`POST /api/claim`).
Without the redirect, only a verified (Google) address reaches its
subscription.

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
curl -s -H "Authorization: Bearer $T" "$W/api/me?fresh=1"
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
