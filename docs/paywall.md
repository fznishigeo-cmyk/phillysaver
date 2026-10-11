# wowpriced paywall — contract (v1)

Visitors who are not signed in see the **top 8** deals in full; every other
live deal is **locked**: a blurred card (real photo blurred, store and
percent visible, no name, price or link) under one call to action:
"Unlock all N deals — 7 days free, then $9.99/mo". Signing up (email +
password, or Google) starts a 7-day trial of full access with no card.
After the trial, full access needs an active Stripe subscription on the
same email (the existing $9.99/mo Payment Link, prefilled with the email).

The static site (GitHub Pages) never holds the locked data. The pipeline
publishes a **teaser** `deals.json` publicly and uploads the **full**
payload to the Cloudflare Worker, which serves it only to members.

## Pieces

1. **Worker** (`cloudflare/worker/worker.js`, one deployment, same
   wrangler project as the newsletter subscribe endpoints): accounts,
   sessions, membership, the full-deals store, the publish endpoint.
2. **Site** (`dealradar/publish/templates/index.html`): teaser rendering,
   locked cards, auth modal (email/password + Google), account menu,
   member fetch of the full data.
3. **Pipeline** (`dealradar/publish/site.py`, `dealradar/pipeline.py`,
   `config.yaml`): teaser split, upload of the full payload, meta fields.

## Config (`config.yaml`)

```yaml
paywall:
  enabled: true
  teaser_count: 8          # unlocked deals for visitors
  trial_days: 7
  api_url: ""              # the Worker base URL, e.g. https://wowpriced-api.<sub>.workers.dev
  google_client_id: ""     # public OAuth Web client ID; Google button hidden when empty
```

`deals.json` meta carries: `api_url`, `google_client_id`, `teaser_count`,
`locked_count`, `trial_days`, plus the existing `subscribe_url`,
`portal_url`, `newsletter_url`, `floors`, `last_run`, `generated_at`,
`cadence_minutes`, `disclosure`, `count`.

## Teaser `deals.json`

`deals` = the top `teaser_count` **alive** published deals (by the site's
score order) as full objects, followed by every other alive deal as a
locked stub, followed by the dead "Just missed" deals unchanged (they are
gone; FOMO is the point):

```json
{"locked": true, "dedupe_key": "...", "pct_at_checkout": 82.1,
 "store": "Dick's Sporting Goods", "category": "sports",
 "first_seen_at": "...", "image_url": "https://...", "status": "alive",
 "conditions": ["used"]}
```

No `item`, `final_price`, `regular_price`, `url`, `credits`, `codes` or
`headline` on a locked stub. `meta.locked_count` is the number of stubs.
With `paywall.enabled: false` the file is the full payload (today's
behavior) and `locked_count` is 0.

## Full payload

Same shape as today's full `deals.json` (every alive deal complete). The
pipeline `POST`s it to `{api_url}/api/publish` with
`Authorization: Bearer <NEWSLETTER_SECRET>` after every site render (skip
quietly, with a log line, when `api_url` is empty or the secret is unset;
a failed upload is a warning, never a failed run; it is retried next run).
The Worker stores it in KV under `deals:full`.

## Worker API

Base URL = `meta.api_url`. JSON in and out. CORS for
`https://wowpriced.com`, `https://www.wowpriced.com`,
`https://fznishigeo-cmyk.github.io` (env `ALLOWED_ORIGINS`), methods
`GET, POST, OPTIONS`, headers `Authorization, Content-Type`.

Session: a stateless token `base64url(json).base64url(hmac-sha256)` of
`{"email": "...", "exp": <unix seconds, 30 days>}` signed with the Worker
secret `SESSION_SECRET`. The page stores it in `localStorage["wp.session"]`
and sends `Authorization: Bearer <token>`. No cookies (the Worker may live
on another origin).

| Route | Auth | Body | Response |
|---|---|---|---|
| `POST /api/signup` | none | `{email, password}` | `200 {token, me}`; `409 {error:"exists"}`; `400 {error:"invalid_email"|"weak_password"}` (min 8 chars) |
| `POST /api/login` | none | `{email, password}` | `200 {token, me}`; `401 {error:"bad_credentials"}` |
| `POST /api/google` | none | `{credential}` (Google ID token) | verifies signature against Google's JWKS, `aud == GOOGLE_CLIENT_ID`, `iss` in {accounts.google.com, https://accounts.google.com}, `exp`; finds or creates the account by the token's verified email; `200 {token, me}`; `401 {error:"bad_token"}` |
| `GET /api/me` | session | — | `200 me`; `401`. `?fresh=1` bypasses the membership cache (at most once a minute per account) |
| `GET /api/deals` | session | — | `200` full payload when `me.member`; `402 me` when not; `401` without a session |
| `POST /api/logout` | session | — | `204` (client drops the token) |
| `POST /api/publish` | `Bearer NEWSLETTER_SECRET` | full payload | `204`; `401` |
| `POST /subscribe`, `GET /unsubscribe`, `GET /list` | (newsletter, unchanged) | | |

`me` = `{"email": "...", "member": true|false, "reason": "subscribed"|"trial"|"expired", "trial_ends_at": "<ISO>", "subscribe_url": "<Payment Link>?prefilled_email=<urlencoded email>", "portal_url": "<customer portal link or empty>"}`.

Membership: `member` is true when a Stripe subscription with status
`active` or `trialing` exists for a customer with that email
(`GET /v1/customers?email=` then `GET /v1/subscriptions?customer=&status=all`,
`STRIPE_SECRET_KEY` secret, cached 5 minutes in KV `member:<email>`), or
when `now < trial_ends_at` (set at account creation to `now + trial_days`,
env `TRIAL_DAYS`, default 7). `reason` reports which.

Accounts: KV `acct:<lowercased email>` = `{email, salt, hash, created_at,
trial_ends_at, google_sub}`; passwords hashed with PBKDF2-SHA256, 100 000
iterations, 16-byte random salt (WebCrypto). Abuse control: KV counters,
10 signups + 20 logins per client IP per hour. Never log passwords or
tokens.

Worker env: secrets `NEWSLETTER_SECRET`, `STRIPE_SECRET_KEY`,
`SESSION_SECRET`; vars `GOOGLE_CLIENT_ID`, `STRIPE_PAYMENT_LINK`,
`STRIPE_PORTAL_LINK`, `ALLOWED_ORIGINS`, `TRIAL_DAYS`. KV binding `SUBS`
(existing) holds everything, keyed by prefix (`sub:`, `acct:`, `member:`,
`deals:`, `rl:`).

## Page behavior

1. Load `deals.json` (teaser). If `localStorage["wp.session"]` exists,
   `GET /api/me`; if `member`, `GET /api/deals` and render the full
   payload instead (same renderer). A `401` clears the token.
2. Signed out: unlocked cards render normally; locked stubs render as
   blurred cards (photo with `filter: blur(8px)` or the store tile, the
   percent badge, skeleton bars for title and price, no link). One sticky
   CTA panel over the locked area: "Unlock all {count} deals — 7 days
   free, then $9.99/mo" with **Sign up free** and **Log in**.
3. Signed in, not a member: same locked rendering; CTA reads "Your free
   week ended — keep every deal for $9.99/mo" → `me.subscribe_url`, plus
   "I just subscribed" → `GET /api/me?fresh=1`.
4. Header: "Log in" link when signed out; the account email with
   "Log out" (and "Manage billing" → `portal_url` when set) when signed in.
5. Auth modal: email + password with a Sign up / Log in toggle, inline
   errors from the API, and the Google button (Google Identity Services,
   loaded only when `meta.google_client_id` is set; the credential
   callback posts to `/api/google`).
6. Filters, tabs, search, sorts and the countdown keep working on
   whatever list is loaded; the Just-missed strip is always public.
7. `meta.api_url` empty: no paywall UI at all (everything renders as
   today), so the site keeps working before the Worker is deployed.
