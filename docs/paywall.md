# wowpriced paywall — contract (v2)

Visitors who are not signed in see the **top 8** deals in full; every other
live deal is **locked**: a blurred card (real photo blurred, store and
percent visible, no name, price or link) under one call to action:
"Unlock all N deals — 7 days free, then $9.99/mo". Signing up (email +
password, or Google) starts a 7-day trial of full access with no card.
After the trial, full access needs an active Stripe subscription that the
account can prove is its own: the payment is **claimed** from the Payment
Link's after-payment page (`welcome.html?session_id=...`), or the account's
email is **verified** (Google sign-in) and Stripe has a subscription on it.
Typing someone else's address at sign-up earns nothing.

The static site (GitHub Pages) never holds the locked data. The pipeline
publishes a **teaser** `deals.json` publicly and uploads the **full**
payload to the Cloudflare Worker, which serves it only to members.

## Pieces

1. **Worker** (`cloudflare/subscribe-worker/`, one deployment with the
   newsletter endpoints): accounts, sessions, membership, the claim of a
   payment, the full-deals store, the publish endpoint.
2. **Site** (`dealradar/publish/templates/index.html`): teaser rendering,
   locked cards, auth modal (email/password + Google), account menu,
   member fetch of the full data; `welcome.html`: the after-payment page
   that claims the payment.
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
| `POST /api/signup` | none | `{email, password}` | `200 {token, me}`; `409 {error:"exists"}`; `400 {error:"invalid_email"|"weak_password"}` (min 8 chars). The account is **unverified** |
| `POST /api/login` | none | `{email, password}` | `200 {token, me}`; `401 {error:"bad_credentials"}` |
| `POST /api/google` | none | `{credential}` (Google ID token) | checks `aud == GOOGLE_CLIENT_ID`, `iss` in {accounts.google.com, https://accounts.google.com}, `exp` and `email_verified` **before** any network call, then the RS256 signature against Google's JWKS; finds or creates the account by the token's email (**verified**); `200 {token, me}`; `401 {error:"bad_token"}` |
| `POST /api/claim` | session | `{session_id}` (Stripe Checkout Session id from the Payment Link redirect) | binds the session's Stripe customer to the account; `200 {me}`; `400 {error:"bad_session"}` (malformed id, or the session is not `status: complete` + `mode: subscription` with a customer); `404 {error:"unknown_session"}` (Stripe does not know it); `502 {error:"stripe_unavailable"}` (Stripe down or `STRIPE_SECRET_KEY` unset); `401` |
| `GET /api/me` | session | — | `200 me`; `401`. `?fresh=1` bypasses the membership cache (at most once a minute per account) |
| `GET /api/deals` | session | — | `200` full payload when `me.member`; `402 me` when not; `401` without a session |
| `POST /api/logout` | session | — | `204` (client drops the token) |
| `POST /api/publish` | `Bearer NEWSLETTER_SECRET` | full payload | `204`; `401` |
| `/subscribe`, `/confirm`, `/unsubscribe`, `/list`, `/list/confirm-sent` | (newsletter, below) | | |

`POST /api/signup|login|google|claim` answer `503 {error:"signin_unavailable"}`
while the Worker has no `SESSION_SECRET`, before any KV write; and a new
account is saved only after its session token signed, so a signing failure
leaves no half-created account.

`me` = `{"email": "...", "member": true|false, "reason": "subscribed"|"trial"|"expired", "trial_ends_at": "<ISO>", "email_verified": true|false, "claimed": true|false, "subscribe_url": "<Payment Link>?prefilled_email=<urlencoded email>", "portal_url": "<customer portal link or empty>"}`.
`claimed` is true once a Stripe customer is bound to the account.

### Membership and ownership

`member` is true when `now < trial_ends_at` (set at account creation to
`now + trial_days`, env `TRIAL_DAYS`, default 7; available to unverified
accounts too), or when Stripe has a subscription with status `active` or
`trialing` reached one of two ways:

- **(a) a bound customer**: `acct.stripe_customer` set by `POST /api/claim`
  → `GET /v1/subscriptions?customer=<id>&status=all`, any `active|trialing`;
- **(b) the account's email, only when `email_verified`**: as
  `dealradar/publish/members.py` does, page
  `GET /v1/subscriptions?status=active&expand[]=data.customer` and the same
  with `status=trialing`, and compare `customer.email` **lowercased** to the
  account email (Stripe's `customers?email=` filter is exact-case and is
  not used).

(a) is tried first, then (b). An account that is neither bound nor verified
is never looked up (no Stripe call, nothing cached). Stripe's answer is
cached 5 minutes **in isolate memory**, not KV; a Stripe failure counts as
"not subscribed" for that request only and is not cached. `reason` reports
which rule granted access.

`email_verified` is true for an account created by Google sign-in, taken
over by Google sign-in (below), or whose claim's `customer_details.email`
(lowercased) equals the account email; false for a password sign-up. An
account written before this field existed counts as verified only when
Google alone could have created it (`google_sub` set, no password hash).

**Recovery of a squatted address.** When Google sign-in verifies an email
whose existing account is *not* `email_verified`, the verified sign-in
takes the account over: the password hash and salt are cleared, `google_sub`
and `email_verified: true` are set, the trial clock is kept. Whoever typed
that address at sign-up loses the password login (they never owned the
address); an existing session on the account now belongs to the owner.
Google sign-in on a *verified* account only links `google_sub` and keeps
any password.

### Claiming a payment

The Stripe Payment Link's after-payment redirect must be
`https://wowpriced.com/welcome.html?session_id={CHECKOUT_SESSION_ID}`
(Stripe fills the placeholder). `welcome.html` then:

1. loads `deals.json` meta; with a `session_id` in the URL and a session
   token in `localStorage["wp.session"]`, `POST {api_url}/api/claim
   {session_id}` and shows "Membership activated" (or the error's copy);
2. without a session token, keeps the id in `sessionStorage["wp.claim"]`,
   shows "Create your account (or log in) to attach this membership" with a
   link to `/#login`, and claims it on the next visit once signed in (the
   id in `sessionStorage` is used when the URL carries none). A `401` from
   the claim clears the stale session token; `bad_session` and
   `unknown_session` clear the stored id.

The Worker (`POST /api/claim`) fetches
`GET https://api.stripe.com/v1/checkout/sessions/<id>` (basic auth
`STRIPE_SECRET_KEY`), requires `status == "complete"`, `mode ==
"subscription"` and a customer id (string or expanded object), then stores
`acct.stripe_customer`, `acct.claimed_at`, and `acct.email_verified = true`
when the payer email matches the account email (a different payer email
still binds: the payer holds the receipt, which is the session id from
their own redirect). The membership cache for that email is dropped so the
`200 {me}` already says `subscribed`. Rate limit 10 per hour per IP.

### Accounts

KV `acct:<lowercased email>` = `{email, scheme, iterations, salt, hash,
created_at, trial_ends_at, google_sub, email_verified, stripe_customer,
claimed_at}`; `scheme|iterations|salt|hash` are null for a Google-only
account.

Passwords, **scheme 2**: `HMAC-SHA256(SESSION_SECRET, password)` (a
server-side pepper, so leaked KV hashes alone cannot be brute-forced), then
PBKDF2-SHA256 over that with a 16-byte random salt and `PBKDF2_ITERATIONS`
rounds (var, default `"20000"`: ~3 ms CPU, inside the Workers Free plan's
10 ms per request). Every account stores the params its hash was made with
and is verified with them; **scheme 1** (records with no `scheme`: bare
password, 100 000 rounds) still logs in and is re-hashed with the current
params after a successful login, as is any scheme-2 hash made with another
round count. Consequence: rotating `SESSION_SECRET` invalidates every
scheme-2 password as well as every session.

Abuse control, per client IP per hour, counted in isolate memory (never
KV): 10 signups, 20 logins, 20 Google posts, 10 claims, 20 newsletter
subscribes → `429`. Google's JWKS is cached an hour (memory + KV
`jwks:google`); a token with an unknown `kid` forces one refetch at most
once a minute per isolate (inside that minute it is `bad_token`). Never log
passwords or tokens.

Worker env: secrets `NEWSLETTER_SECRET`, `STRIPE_SECRET_KEY`,
`SESSION_SECRET`; vars `GOOGLE_CLIENT_ID`, `STRIPE_PAYMENT_LINK`,
`STRIPE_PORTAL_LINK`, `ALLOWED_ORIGINS`, `TRIAL_DAYS`, `PBKDF2_ITERATIONS`.
KV binding `SUBS` (existing) holds only `sub:`, `acct:`, `deals:full` and
`jwks:google`; counters and the membership cache live in memory.

## Newsletter: double opt-in and unsubscribe

KV `sub:<email>` = `{ts, token, confirmed, confirm_sent_at}` (also in key
metadata). A record written before this version has no `confirmed` key and
**counts as confirmed**, so no existing subscriber is dropped.

| Route | Auth | Behavior |
|---|---|---|
| `POST /subscribe` | none | creates `{confirmed: false, confirm_sent_at: ""}`; an existing record (confirmed or not) is left alone and the call still answers ok (`200 {ok:true}` / `303` to `subscribe.html?ok=1`) |
| `GET /confirm?e=<email>&t=<token>` | the link | sets `confirmed: true` when the token matches; `200` HTML page linking to `<site>/subscribe.html?confirmed=1`; clicking twice is still `200`; a bad link is `403` |
| `GET /unsubscribe?e=&t=` | the link | **no longer deletes**: `200` HTML page with one POST form button "Unsubscribe" (`403` when the token does not match) |
| `POST /unsubscribe?e=&t=` | the link | deletes when the token matches (the page's button, or the RFC 8058 one-click form mail apps send; `e`/`t` also accepted as form fields); `200` HTML "You're unsubscribed", also when already gone |
| `GET /list` | `Bearer NEWSLETTER_SECRET` | `{emails:[{email, token}]}`, **confirmed addresses only** |
| `GET /list?pending=1` | same | `{emails:[{email, token}]}`: unconfirmed addresses whose `confirm_sent_at` is empty (the ones still owed a confirm email) |
| `POST /list/confirm-sent` | same | body `{emails:[...]}` (≤ 1000): sets `confirm_sent_at = now` on those pending records; `204`; `400` on a bad body |

The pipeline builds the links as `<worker_url>/confirm?e=<email>&t=<token>`
and `<worker_url>/unsubscribe?e=<email>&t=<token>`.

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
8. `welcome.html` ("Claiming a payment" above) is where the Payment Link
   lands; it needs `meta.api_url` too and does nothing special without a
   `session_id`.
