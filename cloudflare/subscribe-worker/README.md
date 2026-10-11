# subscribe worker — the free daily email list and the paywall API

The only moving part of wowpriced that lives outside GitHub: one Cloudflare
Worker + KV namespace. It holds the newsletter's subscriber list (the site's
forms POST to it, people unsubscribe through it, the pipeline reads the list
through it) and, since the paywall (`docs/paywall.md`), the member accounts,
their sessions, the Stripe membership check and the members-only full deals
payload. It never sends mail; `dealradar/publish/newsletter.py` does that over
the SMTP secrets, once per subscriber per day.

```
browser ──POST /subscribe──▶ Worker ──▶ KV  sub:<email> = {ts, token}
GitHub Actions ──GET /list (Bearer NEWSLETTER_SECRET)──▶ Worker ──▶ emails + tokens
subscriber ──GET /unsubscribe?e=&t=──▶ Worker ──▶ KV delete

GitHub Actions ──POST /api/publish (Bearer NEWSLETTER_SECRET)──▶ Worker ──▶ KV deals:full
browser ──POST /api/signup|login|google──▶ Worker ──▶ KV acct:<email>, session token back
browser ──GET /api/deals (Bearer <session>)──▶ Worker ──▶ Stripe? trial? ──▶ the full payload
```

Files: `worker.js` (newsletter routes and the router), `api.js` (the `/api/*`
handlers), `auth.js` (accounts, sessions, Google ID tokens), `membership.js`
(Stripe + trial), `util.js` (CORS, JSON, rate limits, base64url). wrangler
bundles the ES modules; `main` in wrangler.toml is `worker.js`.

## Endpoints

### Newsletter

| Route | What it does |
|---|---|
| `POST /subscribe` | `{"email": ...}` JSON or a form field `email`. Validates, lowercases, stores `sub:<email>` with a random token (idempotent: signing up twice keeps the first token). JSON callers get `200 {"ok": true}`; form posts get a `303` back to `<site>/subscribe.html?ok=1` (or `?err=email`). Abuse control: 20 posts per client IP per hour. |
| `GET` or `POST /unsubscribe?e=<email>&t=<token>` | Deletes the record when the token matches, 200 HTML "You're unsubscribed". POST is the RFC 8058 one-click form mail apps send. |
| `GET /list` | `Authorization: Bearer <NEWSLETTER_SECRET>` -> `{"emails": [{"email", "token"}]}`, at most 10,000 (KV list pagination; tokens ride in key metadata so a page of 1,000 costs one KV operation). |
| `OPTIONS *` | CORS preflight for `ALLOWED_ORIGINS` only (`GET, POST, OPTIONS`; `Authorization, Content-Type`). |
| anything else | 404. |

### Paywall (`docs/paywall.md` "Worker API")

JSON in and out; every error is `{"error": "<code>"}` with the status below,
never a stack trace. `me` is `{email, member, reason, trial_ends_at,
subscribe_url, portal_url}` where `reason` is `subscribed` (Stripe), `trial`
or `expired`, `subscribe_url` is `STRIPE_PAYMENT_LINK?prefilled_email=<email>`
(empty when the var is empty) and `portal_url` is `STRIPE_PORTAL_LINK` or "".

| Route | Auth | Body | Response |
|---|---|---|---|
| `POST /api/signup` | none | `{email, password}` | `200 {token, me}`; `400 invalid_email` / `weak_password` (under 8 chars); `409 exists` |
| `POST /api/login` | none | `{email, password}` | `200 {token, me}`; `401 bad_credentials` (unknown address, wrong password and Google-only account all cost one PBKDF2 run) |
| `POST /api/google` | none | `{credential}` (Google ID token) | `200 {token, me}`; `401 bad_token`. RS256 checked against Google's JWKS (cached an hour), `aud == GOOGLE_CLIENT_ID`, `iss`, `exp`, `email_verified`; finds or creates the account (the trial starts on first sign-in) |
| `GET /api/me` | `Bearer <session>` | — | `200 me`; `401`. `?fresh=1` skips the 5-minute Stripe cache, at most once a minute per account |
| `GET /api/deals` | `Bearer <session>` | — | `200` the full payload (as published) when `me.member`; `402 me` when not; `401` without a session; `404 no_payload` before the first publish |
| `POST /api/logout` | `Bearer <session>` | — | `204`; `401` |
| `POST /api/publish` | `Bearer <NEWSLETTER_SECRET>` | the full payload (JSON with a `deals` array) | `204`; `400 bad_payload`; `401` |
| any other `/api/*` | | | `404 not_found` |

Sessions are stateless: `base64url({"email", "exp"}).base64url(hmac-sha256)`
signed with `SESSION_SECRET`, 30 days, compared in constant time. Membership:
`member` when Stripe has an `active` or `trialing` subscription for a customer
with that email (`GET /v1/customers?email=` then `/v1/subscriptions?customer=&status=all`,
answer cached 5 minutes), or while `now < trial_ends_at` (`TRIAL_DAYS` after
sign-up). A Stripe error counts as "not subscribed" for that request only
(logged, never cached). Abuse control per client IP per hour: 10 signups,
20 logins, 20 Google posts -> `429 rate_limited`. Passwords, tokens and
secrets are never logged.

## What KV holds

| Key | Value |
|---|---|
| `sub:<email>` | `{ts, token}` (token also in metadata) |
| `acct:<email>` | `{email, salt, hash, created_at, trial_ends_at, google_sub}`; PBKDF2-SHA256, 100,000 iterations, 16-byte salt; `salt`/`hash` null for a Google-only account |
| `member:<email>` | `{member, checked_at}`, Stripe's answer, good for 5 minutes |
| `deals:full` | the full payload exactly as the pipeline posted it |
| `jwks:google` | Google's signing keys, one hour |
| `rl:<ip>:<hour>`, `rl:signup|login|google:<ip>:<hour>`, `rl:fresh:<email>` | abuse counters, expire within two hours |

Nothing else: no post text, no card data, no Stripe ids.

## Deploy (once, then whenever the code changes)

Run the **deploy subscribe worker** workflow from the repository's Actions tab
(`.github/workflows/deploy-subscribe-worker.yml`). Repository secrets:

| Secret | Where it comes from |
|---|---|
| `CLOUDFLARE_API_TOKEN` | Cloudflare dashboard -> My Profile -> API Tokens -> Create Token -> template "Edit Cloudflare Workers". |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare dashboard -> Workers & Pages -> Overview, right-hand column "Account ID". |
| `NEWSLETTER_SECRET` (required) | Any long random string (e.g. `openssl rand -hex 32`). The same secret lets the pipeline call `/list` and `/api/publish`. |
| `SESSION_SECRET` | Another long random string; signs session tokens. Skipped with a notice when empty (nobody can sign in until it is set and the workflow re-run). Changing it signs everyone out. |
| `STRIPE_SECRET_KEY` | The live Stripe secret key the pipeline already uses. Skipped with a notice when empty (only trials count as members until then). |

Workflow inputs (all optional) set the public vars on the deployed copy of
`wrangler.toml` for that run: `google_client_id` (`GOOGLE_CLIENT_ID`, the
OAuth Web client ID; the Google button stays off while empty),
`stripe_payment_link` (`STRIPE_PAYMENT_LINK`), `stripe_portal_link`
(`STRIPE_PORTAL_LINK`), `trial_days` (`TRIAL_DAYS`, default 7). Re-run the
workflow with the inputs filled in to change them; an empty input keeps the
value in `wrangler.toml`, so commit real values there to make them stick
across deploys (none of them is secret).

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
curl -s "$W/unsubscribe?e=you@example.com&t=<token from the list>"

curl -s -X POST -H 'Content-Type: application/json' -d '{"email":"you@example.com","password":"long enough"}' $W/api/signup
T=<token from that reply>
curl -s -H "Authorization: Bearer $T" "$W/api/me?fresh=1"
curl -s -H "Authorization: Bearer $T" $W/api/deals | head -c 300
```

## Costs and limits

Fits the Workers free plan: one `/list` and one `/api/publish` per pipeline
run, sign-ups, and one `/api/me` + `/api/deals` per member visit, well under
the 100k/day request and the KV read/list allowances; Stripe is asked at most
once per member per 5 minutes. `/list` returns at most 10,000 subscribers.
