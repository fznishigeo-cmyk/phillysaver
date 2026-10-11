# wowpriced paywall — contract (v3: the trial is Stripe's, card up front)

Visitors who are not signed in see **8** deals in full, a new 8 from the top
48 at every drop (every publish); every other
live deal is **locked**: a blurred card (real photo blurred, store and
percent visible, no name, price or link) under one call to action. Signing
up (email + password, or Google) is **free** and shows the same 8-deal
teaser, plus a clear next step: start the free trial.

Every deal beyond the top 8 unlocks **only** with a Stripe subscription
whose status is `trialing` or `active`, i.e. a card on file. There is no
card-less trial for anyone, existing accounts included. The trial is
Stripe's: `TRIAL_DAYS` days (default 7), the card collected up front
(`payment_method_collection=always`), then $9.99/month billed automatically
every month until cancelled. One trial per account and email address, and
never a second subscription (nor a second open checkout) while one has not
ended. Members cancel either way: online in Stripe's customer portal
(the "Manage membership" link at the bottom of the home page), or by emailing hello@wowpriced.com
(`config.yaml` `contact_email`), and the operator cancels the subscription
for them in the Stripe dashboard.

The account must prove a subscription is its own: the Checkout Session is
**claimed** from the success page (`welcome.html?session_id=...`, the same
page for the Worker's checkout and for the Payment Link), or the Worker's
own checkout made the subscription for this account (its reference in the
subscription metadata), or the account's email is **verified** (Google
sign-in) and Stripe has a subscription on it. Typing someone else's address
at sign-up earns nothing.

The static site (GitHub Pages) never holds the locked data. The pipeline
publishes a **teaser** `deals.json` publicly and uploads the **full**
payload to the Cloudflare Worker, which serves it only to members.

## Pieces

1. **Worker** (`cloudflare/subscribe-worker/`, one deployment with the
   newsletter endpoints): accounts, sessions, membership, Stripe Checkout
   and the billing portal, the claim of a payment, the full-deals store, the
   publish endpoint.
2. **Site** (`dealradar/publish/templates/index.html`): teaser rendering,
   locked cards, auth modal (email/password + Google), account menu, the
   start-trial and manage-billing buttons, member fetch of the full data;
   `welcome.html`: the after-payment page that claims the payment.
3. **Pipeline** (`dealradar/publish/site.py`, `dealradar/pipeline.py`,
   `config.yaml`): teaser split, upload of the full payload, meta fields.

## Config (`config.yaml`)

```yaml
paywall:
  enabled: true
  teaser_count: 8          # unlocked deals for visitors and free accounts
  teaser_pool: 0           # 0 = every live deal; N = rotate the top N only
  trial_days: 7            # page copy only; keep it equal to the Worker's TRIAL_DAYS
  api_url: ""              # the Worker base URL, e.g. https://wowpriced-api.<sub>.workers.dev
  google_client_id: ""     # public OAuth Web client ID; Google button hidden when empty
```

`deals.json` meta carries: `api_url`, `google_client_id`, `teaser_count`,
`locked_count`, `trial_days`, plus the existing `subscribe_url`,
`portal_url`, `newsletter_url`, `floors`, `last_run`, `generated_at`,
`cadence_minutes` (the drop clock's step, `publish.drop_minutes`), `disclosure`, `count`. A signed-in page prefers
`me.trial_days` (the Worker's real trial length) over `meta.trial_days`.

## Teaser `deals.json`

`deals` = `teaser_count` **alive** published deals as full objects: the
best (site score order) not unlocked before, at most 2 per store while
others remain. Each publish unlocks a completely different set: the keys
past drops unlocked are kept in state.db `meta` (`teaser_recent`: every
live deal's key once it was free, at its latest showing; a dead deal's key
leaves), so no deal is free twice until every live deal has had its turn
(then strictly the least recently shown go first), while a new best deal is
unlocked at once. `teaser_pool: N > 0` limits the rotation to the top N (a
lap of N / teaser_count drops). On the page a drop is a new publish, on a
clock of `publish.drop_minutes` (15, the real gap between publishes): a
publish that lands early waits for its mark; a mark that comes before its
publish says "Next drop soon" and polls; the same deals are never re-dealt
as a drop. They
are followed by every other alive deal as a locked stub, then by the dead "Just missed" deals unchanged (they are
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
`{"email": "...", "sv": <account session_version>, "exp": <unix seconds,
30 days>}` signed with the Worker secret `SESSION_SECRET`. The page stores
it in `localStorage["wp.session"]` and sends `Authorization: Bearer
<token>`. No cookies (the Worker may live on another origin). Every session
route also requires `sv` to equal the account's current `session_version`
(a takeover bumps it, below), else `401`; tokens from before the field
count as `sv: 0`.

| Route | Auth | Body | Response |
|---|---|---|---|
| `POST /api/signup` | none | `{email, password}` | `200 {token, me}`; `409 {error:"exists"}`; `400 {error:"invalid_email"|"weak_password"}` (min 8 chars). The account is **unverified** and free: `me.member` is false until a Stripe subscription |
| `POST /api/login` | none | `{email, password}` | `200 {token, me}`; `401 {error:"bad_credentials"}` |
| `POST /api/google` | none | `{credential}` (Google ID token) | checks `aud == GOOGLE_CLIENT_ID`, `iss` in {accounts.google.com, https://accounts.google.com}, `exp` and `email_verified` **before** any network call, then the RS256 signature against Google's JWKS; finds or creates the account by the token's email (**verified**); `200 {token, me}`; `401 {error:"bad_token"}` |
| `POST /api/checkout` | session | `{}` or empty | `200 {url}`: a Stripe Checkout Session URL to send the browser to ("Starting the trial" below; the account's still-open session when there is one); `409 {error:"already_member", me}` (an `active` or `trialing` subscription: also when it is a Stripe customer with the account's email the account has not proven is its own, `me.member` is then false); `409 {error:"payment_issue", me}` (one that is `past_due`, `unpaid`, `incomplete` or `paused`: Stripe may still collect it, so the card is fixed in the portal instead of buying again); `409 {error:"contact_support", me}` (the customer a Google takeover unbound from the account still has a subscription that has not ended, and no other account claimed it: the page says to email hello@wowpriced.com); `503 {error:"checkout_unavailable", fallback_url}`: `fallback_url` is `""` when Stripe could not be asked whether the account already pays or had a trial, or could not expire the account's last open session (never sell on a failed check), else `me.subscribe_url` (may be `""`) when the session itself failed or there is no price; `401 {error:"unauthorized"}`. 10 per IP per hour |
| `POST /api/portal` | session | `{}` or empty | `200 {url}`: a Stripe billing portal session (cancel, card, invoices; returns to the site home) for the account's customer: the bound one, else the customer of the subscription membership found by the account's own checkout (c) or its verified email (b) ("Billing" below); `404 {error:"no_customer"}` (none of those); `503 {error:"portal_unavailable", fallback_url}` (`STRIPE_PORTAL_LINK` or `""`); `401` |
| `POST /api/claim` | session | `{session_id}` (the Stripe Checkout Session id from the success redirect, Worker checkout or Payment Link) | binds the session's Stripe customer to the account; `200 {me}`; `400 {error:"bad_session"}` (malformed id, or the session is not `status: complete` + `mode: subscription` with a customer); `403 {error:"wrong_account"}` (the session carries a `client_reference_id` that is not this account's reference); `404 {error:"unknown_session"}` (Stripe does not know it); `409 {error:"already_claimed"}` (that Stripe customer is already bound to another account: one payment, one account); `502 {error:"stripe_unavailable"}` (Stripe down or `STRIPE_SECRET_KEY` unset); `401` |
| `GET /api/me` | session | — | `200 me`; `401`. `?fresh=1` bypasses the membership cache (at most once a minute per account) |
| `GET /api/deals` | session | — | `200` full payload when `me.member`; `402 me` when not; `401` without a session |
| `POST /api/logout` | session | — | `204` (client drops the token) |
| `POST /api/publish` | `Bearer NEWSLETTER_SECRET` | full payload | `204`; `401` |
| `/subscribe`, `/confirm`, `/unsubscribe`, `/list`, `/list/confirm-sent` | (newsletter, below) | | |

| `POST /subscribe`, `GET /confirm`, `GET /unsubscribe`, `POST /unsubscribe`, `GET /list`, `GET /list?pending=1`, `POST /list/confirm-sent` | (newsletter: "Newsletter double opt-in" below) | | |

`POST /api/signup|login|google|claim|checkout` answer `503
{error:"signin_unavailable"}` while the Worker has no `SESSION_SECRET`,
before any KV write or Stripe call; and a new account is saved only after
its session token signed, so a signing failure leaves no half-created
account. (`/api/portal`, like `/api/me`, is just `401` then.)

`me` = `{"email", "member", "reason", "trial_ends_at", "trial_days",
"trial_available", "email_verified", "claimed", "subscribe_url",
"portal_url"}`:

| Field | Value |
|---|---|
| `member` | `true` iff Stripe has a subscription with status `active` or `trialing` for the account (rules below) |
| `reason` | `"subscribed"` (active), `"trial"` (Stripe `trialing`), `"lapsed"` (a Stripe customer is bound to the account but has no live subscription: cancelled, past due, unpaid, ..., or Stripe says that customer does not exist for the Worker's key), `"none"` (no live subscription and nothing bound) |
| `trial_ends_at` | when `reason` is `"trial"`: that subscription's `trial_end` as ISO; `null` otherwise |
| `trial_days` | the Worker's `TRIAL_DAYS` (default 7) |
| `trial_available` | `true` while the account has never had a subscription as far as the Worker knows: no `acct.trial_used`, no bound Stripe customer, and Stripe's membership answer saw none (any status for the account's own checkouts, live ones by verified email). Checkout also asks the customers with the account's email ("The trial rule"); what it finds there shows here from then on |
| `email_verified` | below |
| `claimed` | `true` once a Stripe customer is bound to the account |
| `subscribe_url` | `<STRIPE_PAYMENT_LINK>?prefilled_email=<urlencoded email>`, or `""` without a Payment Link: the fallback when checkout is unavailable |
| `portal_url` | `STRIPE_PORTAL_LINK` (the customer-portal login link) or `""`: the fallback when `/api/portal` is unavailable or finds no customer |

### Membership and ownership

`member` is true only when Stripe has a subscription with status `active`
or `trialing` (Stripe's trial: a card is on file) reached one of three ways:

- **(a) a bound customer**: `acct.stripe_customer` set by `POST /api/claim`
  → `GET /v1/subscriptions?customer=<id>&status=all`, any `active|trialing`.
  When Stripe answers `400` or `404` with error code `resource_missing`
  (the customer was deleted, or made under the other mode's key or another
  Stripe account), the customer is **gone**, not down: it counts as history
  (no trial), opens no portal, and checkout bills `customer_email` instead
  (nothing is written: a key switched by mistake costs no binding). A
  `429`, a `5xx` or a network error stays a failure (strict `503` in
  checkout);
- **(c) the account's own checkouts**: every Worker checkout stamps
  `subscription_data[metadata][wowpriced_ref] = <account reference>`, so
  `GET /v1/subscriptions/search?query=metadata['wowpriced_ref']:'<ref>'`
  (one call, any status) finds them, claimed or not. A password account's
  own checkout therefore counts even when it never reached `welcome.html`.
  Only the account's **current** reference is searched, so a takeover
  (below) retires every checkout made before it. Stripe's Search is
  eventually consistent (about a minute behind), which is fine:
  `welcome.html` claims at once, and checkout's history check (below) is
  not eventually consistent;
- **(b) the account's email, only when `email_verified`**: as
  `dealradar/publish/members.py` does, page
  `GET /v1/subscriptions?status=active&expand[]=data.customer` and the same
  with `status=trialing`, and compare `customer.email` **lowercased** to the
  account email (Stripe's `customers?email=` filter is exact-case and is
  not used here). When neither is found and (a) and (c) have nothing that
  has not ended, the same with `status=past_due`, `unpaid`, `incomplete`,
  then `paused`: such a match makes no member, it only names the customer
  whose card to fix (below).

(a) is tried first, then (c), then (b); the first live subscription wins,
and within each **active beats trialing**. Only `active` and `trialing`
make a member; `past_due`, `unpaid`, `incomplete` and `paused` do not, but
they have not ended (Stripe may still collect them), so checkout refuses
to sell beside them (`409 payment_issue`). The answer is `{sub, had,
customer}`: the best live subscription (`{status, trial_end}`) or none;
whether any subscription of the account was seen (a live one anywhere, any
one of the bound customer's or of its own checkouts, a not-ended one found
by (b), or a gone bound customer), which ends `trial_available`; and the
customer its billing portal opens on: the live subscription's (whichever
way it was found), else that of a not-ended one of (a) or (c) (a card to
fix), else that of a not-ended one found by (b) (the same, verified
accounts only), else the bound customer (not a gone one), else none.
Every account is asked at least (c); only a verified one is looked up by
email. Stripe's answer is cached 5 minutes **in isolate memory**, not KV; a
Stripe failure counts as "not a member" for that request only and is not
cached (`POST /api/checkout` asks strictly instead: a failure there is
`503`). `reason` follows from the answer: `active` → `subscribed`,
`trialing` → `trial`, none with a bound customer → `lapsed` (no extra
Stripe call), none otherwise → `none`. Signing up grants nothing, and an
account's `trial_ends_at` from the old card-less trial is ignored.

`email_verified` is true for an account created by Google sign-in or taken
over by Google sign-in (below); false for a password sign-up, and a claim
never sets it (the payer email on a Checkout is typed by the payer, so a
squatter paying once with the owner's address must not inherit the owner's
subscription afterwards). An account written before this field existed
counts as verified only when Google alone could have created it
(`google_sub` set, no password hash).

**Recovery of a squatted address.** When Google sign-in verifies an email
whose existing account is *not* `email_verified`, the verified sign-in
takes the account over: the password hash and salt are cleared, `google_sub`
and `email_verified: true` are set, `session_version` is incremented and the
cached membership answer is dropped. The squatter's Stripe binding goes too:
`stripe_customer`, `claimed_at` and `trial_used` are cleared, and KV
`claim:<that customer>` is deleted when it names this account, so the owner
never bills onto, opens the portal of, or inherits the squatter's customer
(and the squatter may claim it into an account of their own). The unbound
customer is kept as `acct.released_customer`: while it has a subscription
that has not ended and no other account has claimed it (KV
`claim:<customer>` empty or naming this account), checkout answers `409
contact_support` instead of selling a second subscription beside it; the
owner claims it back if it is theirs, or emails hello@wowpriced.com. The
account reference is **epoch-bound** (below), so the takeover also retires
the squatter-era reference: their Worker checkouts no longer count for the
account by (c), and their receipts are `403 wrong_account` for the owner.
Membership by (b) still counts every `active`/`trialing` subscription on
the owner's verified address, including one a squatter's Worker checkout
paid for with their own card (Checkout sent the account's address as
`customer_email`): the owner is then a member through it and can cancel it
in the portal, and the squatter, who lost the account, asks
hello@wowpriced.com (operator steps below). The owner keeps their own first
trial unless Stripe shows a prior subscription for the address ("The trial
rule": a squatter's Worker checkout carried the owner's address, so it
does count, found by checkout's email history). Whoever typed that address
at sign-up loses the password login and every session they hold (their
tokens carry the old `sv`); the owner's sign-in response carries the only
valid token. An owner who had claimed their own payment before signing in
with Google stays a member through (b) when it is on their address, and
can claim a Payment Link session again (a Worker checkout session from
before the takeover carries the retired reference). Google sign-in on a
*verified* account only links `google_sub` and keeps any password and
session.

### The trial rule

One Stripe trial per account and email address: any earlier subscription
(any status) tied to the account means no trial. Tied means the bound
customer (or one that is gone for the key), the account's own checkouts
(the reference search, (c) above), or a Stripe customer whose email is the
account's lowercased email (`GET /v1/customers?email=`, which is
exact-case: the Worker's own checkouts always send the lowercased address,
a Payment Link payer who typed another case is missed). It is not one
trial per person or per card: a password sign-up needs no email
verification, so a new address gets a new trial with the same card (card
fingerprints are not checked).
`trial_available` (in `me`) = no `acct.trial_used`, no bound
`acct.stripe_customer`, and no subscription in the membership answer.
`POST /api/checkout` then asks the email's customers too, and adds
`subscription_data[trial_period_days] = TRIAL_DAYS` only when all of them
say "never" and `TRIAL_DAYS > 0`; when only that last check finds a
subscription, the checkout carries no trial and `acct.trial_used = true` is
written (one KV write, once), so `me` says so from then on. A claim of a
subscription that is (or was) trialing sets `acct.trial_used = true` too.
A returning (lapsed) account checks out with its bound customer and pays
from day one. The card is always collected up front, so the trial converts
to $9.99/month automatically and renews every month until the member
cancels, online in the customer portal or by emailing hello@wowpriced.com
(Stripe ends it at the period end, or as configured in the portal
settings). Accounts from the card-less era never gave a card and never had
a Stripe trial, so `trial_available` is true for them unless Stripe shows
otherwise.

### Starting the trial: `POST /api/checkout`

The page's start-trial (or resubscribe) button posts here with the session
and sends the browser to the returned `url` (Stripe-hosted Checkout). The
Worker:

1. answers `409 already_member` (with `me`) when the account is a member,
   asked **strictly** under the `?fresh=1` rule so a member is never sold a
   second subscription;
2. reads the account's whole history **uncached** (the 5-minute membership
   cache and the `?fresh=1` throttle never apply here), every subscription
   any status: the bound customer's (`GET
   /v1/subscriptions?customer=<id>&status=all`; a gone one, as in (a),
   counts as history), the account's own checkouts (the reference search,
   (c)), and those of the customers with the account's email (`GET
   /v1/customers?email=<lowercased email>&limit=100`, then each customer's
   subscriptions). The best one that has not ended (anything but
   `canceled` and `incomplete_expired`; active beats trialing beats the
   rest) decides: `active`/`trialing` → `409 already_member`; `past_due`,
   `unpaid`, `incomplete`, `paused` (or any other status Stripe adds) →
   `409 payment_issue` (Stripe may still collect it: the member fixes the
   card in the portal rather than buying twice). Both carry `me`, with
   `trial_available` false. Any one at all → no trial ("The trial rule");
3. then the customer a takeover released (`acct.released_customer`, its
   subscriptions as in step 2): one that has not ended → `409
   contact_support` (with `me`), unless KV `claim:<that customer>` names
   another account (it is theirs now). Any Stripe failure in steps 1 to 3
   (or no `STRIPE_SECRET_KEY`) → `503 {error:"checkout_unavailable",
   fallback_url: ""}` and no session: never sell on a failed check, not
   even through the Payment Link;
4. finds the price: env `STRIPE_PRICE_ID` when set, else the Payment Link's
   own price: `GET /v1/payment_links?active=true&limit=100` (paging with
   `starting_after`, up to 5 pages), the link whose `url` equals
   `STRIPE_PAYMENT_LINK` (origin + path, trailing slash ignored, path
   case-sensitive), then `GET /v1/payment_links/<id>/line_items?limit=1` →
   `data[0].price.id`. The price is cached 6 hours in isolate memory and a
   day in KV `stripe:price` (`{link, price}`, ignored when the link
   changed), so a cold isolate costs at most one KV read; a miss is never
   cached;
5. keeps **one open Checkout Session per account**: KV `checkout:<ref>` =
   `{id, url, trial, params}` (the account's last session; `params` is the
   hash below; one hour) → `GET /v1/checkout/sessions/<id>`. Still `open`
   and made with the very same params → `200` with its `url`, nothing new.
   Still `open` with other params (the trial used up meanwhile, another
   price, a customer bound) → `POST /v1/checkout/sessions/<id>/expire`
   first (a `4xx` answer means it completed or expired meanwhile: fine).
   `complete`, `expired` or unknown to Stripe → nothing to do. A Stripe
   failure here (`429`, `5xx`) → `503 checkout_unavailable` with
   `fallback_url: ""`: an open session may remain, so no Payment Link
   either;
6. `POST /v1/checkout/sessions` (form-encoded, basic auth
   `STRIPE_SECRET_KEY`, header `Idempotency-Key: <ref>-<10-minute
   slot>-<params hash>`, where the hash is the first 16 hex characters of
   SHA-256 of the form-encoded params below without `expires_at`: a double
   click inside a slot makes one session, and a change of params a new one
   instead of Stripe's idempotency `400`) with, in this order:

| Param | Value |
|---|---|
| `mode` | `subscription` |
| `line_items[0][price]`, `line_items[0][quantity]` | the price, `1` |
| `payment_method_collection` | `always` (the card up front, trial or not). No `payment_method_types`: Stripe refuses it on Checkout Sessions for this account ("no longer supported"), so the methods are the dashboard's, as on the Payment Link |
| `client_reference_id` | the account reference |
| `customer` / `customer_email` | the bound `acct.stripe_customer` when there is one and Stripe has not said it is gone, else the account email |
| `subscription_data[trial_period_days]` | `TRIAL_DAYS`, only when `trial_available`, the history shows no subscription, and `TRIAL_DAYS > 0` |
| `subscription_data[metadata][wowpriced_ref]` | the account reference |
| `success_url` | `<SITE_URL>/welcome.html?session_id={CHECKOUT_SESSION_ID}` (the braces literal in the decoded value: Stripe fills them in) |
| `cancel_url` | `<SITE_URL>/?checkout=cancel` |
| `expires_at` | the end of the 10-minute slot + 31 minutes, in unix seconds: Stripe's minimum is 30 minutes after it creates the session (one more minute covers the clocks), and counting from the slot's end keeps a retry inside the slot byte-identical for the idempotency key. A session is payable for 31 to 41 minutes, not Stripe's default 24 hours |

After a new session, `checkout:<ref>` is written (one KV write per new
session; when the write fails the session still works and still expires
on its own).

The **account reference** is the first 32 hex characters of
SHA-256(`"wowpriced-acct:" + email`) while the account's `session_version`
is 0 (every reference made so far is unchanged), else of
SHA-256(`"wowpriced-acct:" + email + ":" + session_version`): **epoch-bound**,
so a takeover (which bumps `session_version`) retires the squatter-era
reference for (c), for claims (`403 wrong_account`) and for
`checkout:<ref>`. `SITE_URL` is a Worker var, default
`https://wowpriced.com`. Once steps 1 to 3 answered, any failure (no price,
a Stripe error on the new session) is `503 {error:"checkout_unavailable",
fallback_url: me.subscribe_url}`, except step 5's (`""`); Stripe's error
text never reaches the client (the Worker logs status and path only).
Checkout writes `checkout:<ref>` for a new session, and `acct.trial_used`
only when step 2 found an earlier subscription the account did not know
about.

### Claiming a payment

Both the Worker's checkout and the Stripe Payment Link land on
`https://wowpriced.com/welcome.html?session_id={CHECKOUT_SESSION_ID}` (the
Payment Link's after-payment redirect: `scripts/stripe_setup.py` creates
the link with it and updates an existing link whose redirect differs;
Stripe fills the placeholder). A cancelled checkout returns to
the home page with `?checkout=cancel`. `welcome.html` then:

1. loads `deals.json` meta; with a `session_id` in the URL and a session
   token in `localStorage["wp.session"]`, `POST {api_url}/api/claim
   {session_id}` and shows "Membership activated" (or the error's copy);
2. without a session token, keeps the id in `sessionStorage["wp.claim"]`,
   shows "Create your account (or log in) to attach this membership" with a
   link to `/#login`, and claims it on the next visit once signed in (the
   id in `sessionStorage` is used when the URL carries none). A `401` from
   the claim clears the stale session token; `bad_session`,
   `wrong_account`, `unknown_session` and `already_claimed` clear the
   stored id (`already_claimed`: "This payment is already attached to
   another account"; email hello@wowpriced.com if that is wrong).

The Worker (`POST /api/claim`) fetches
`GET https://api.stripe.com/v1/checkout/sessions/<id>?expand[]=subscription`
(basic auth `STRIPE_SECRET_KEY`), requires `status == "complete"`,
`mode == "subscription"` and a customer id (string or expanded object);
answers `403 wrong_account` when the session carries a
`client_reference_id` that is not this account's current reference (a
session the Worker's checkout made for another account, or for this one
before a takeover; a Payment Link session carries none); answers `409
already_claimed` when KV `claim:<customer>` names
another account (one payment, one account: a shared or leaked success URL
unlocks nothing more, and nobody else gets the payer's portal or bills onto
their customer); then writes `claim:<customer> = <account email>` (first
claim of that customer only) and stores `acct.stripe_customer` and
`acct.claimed_at`, and `acct.trial_used = true` when the session's
subscription is or was trialing (`status: trialing` or a `trial_end`),
whatever payer email the session carries (the payer holds the receipt,
which is the session id from their own redirect; the typed email proves
nothing and never verifies the account). The membership cache for that
email is dropped so the `200 {me}` already says `trial` or `subscribed`.
Rate limit 10 per hour per IP. A Google takeover deletes the marker of the
customer it unbinds ("Recovery of a squatted address"). Bindings made
before the marker existed have none, so their customer can still be
claimed once more, by whoever claims it first.

### Billing: `POST /api/portal`

The customer is the one membership's answer names ("Membership and
ownership"; the cached answer, asked strictly): the live subscription's,
whichever way it was found (the bound customer (a), the account's own
checkout (c) even when it was never claimed, or a customer on the account's
**verified** email (b)), else that of a not-ended subscription of (a) or (c)
(`past_due` and the like: the card is fixed here, which is where the page
sends `409 payment_issue`), else that of a not-ended one on the verified
email (b) (the same; the account is still no member), else the bound
customer (not a gone one). Then
`POST /v1/billing_portal/sessions` with `customer=<that customer>`,
`configuration=<bpc_...>` and `return_url=<SITE_URL>/` → `200 {url}`
(Stripe's hosted portal: cancel, update the card, invoices). An unverified
account never opens the portal of a customer that only shares its typed
email. The configuration is env `STRIPE_PORTAL_CONFIG` when set, else the
active one `scripts/stripe_setup.py` created (`GET
/v1/billing_portal/configurations?active=true&limit=100`, paged like the
Payment Links, the one with `metadata.app == "wowpriced"`: cancel at period
end, card update, invoices), cached like the price (6 hours in memory, a
day in KV `stripe:portal` = `{mode, config}`, ignored when the key's mode,
live or test, differs; a miss is never cached). Only when none is found is
`configuration` left out, so Stripe uses the dashboard's default portal.
When Stripe refuses a **discovered** configuration with a `4xx` other than
`429` (deactivated, replaced by a re-run of the setup, another account's),
the Worker drops it from memory and KV `stripe:portal`, looks it up again
once (cached as usual) and retries with the one found when it differs,
then without `configuration`; an operator-set `STRIPE_PORTAL_CONFIG` is
never dropped or retried. No customer → `404 no_customer`; any other
Stripe error or a missing key → `503 {error:"portal_unavailable",
fallback_url: STRIPE_PORTAL_LINK or ""}`. The page follows `url`, else
`fallback_url` / `me.portal_url` (the portal's own email login) when set.

**Cancelling.** Members cancel either way: online through the "Manage or
cancel" link (the portal session above, or `me.portal_url`, the portal's
email login, for a member the Worker finds no customer for), or by emailing
hello@wowpriced.com (`config.yaml` `contact_email`), and the operator
cancels for them in the Stripe dashboard (Customers → the member's
subscription → Cancel). Either way membership ends when Stripe says the
subscription is no longer `trialing` or `active` (within the 5-minute
cache).

### Accounts

KV `acct:<lowercased email>` = `{email, scheme, iterations, salt, hash,
created_at, google_sub, email_verified, stripe_customer, claimed_at,
trial_used, session_version}`, plus `released_customer` once a takeover
unbound a customer ("Recovery of a squatted address"); `scheme|iterations|salt|hash` are null for
a Google-only account; `session_version` starts at 0 (missing = 0);
`trial_used` starts false (missing = false). Records written before v3 may
also carry `trial_ends_at`, which nothing reads any more.

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
KV): 10 signups, 20 logins, 20 Google posts, 10 claims, 10 checkouts, 20
newsletter subscribes → `429`. Google's JWKS is cached an hour (memory + KV
`jwks:google`); a token with an unknown `kid` forces one refetch at most
once a minute per isolate (inside that minute it is `bad_token`). Never log
passwords or tokens.

Worker env: secrets `NEWSLETTER_SECRET`, `STRIPE_SECRET_KEY`,
`SESSION_SECRET`; vars `GOOGLE_CLIENT_ID`, `STRIPE_PAYMENT_LINK`,
`STRIPE_PRICE_ID` (optional; else the Payment Link's price),
`STRIPE_PORTAL_LINK`, `STRIPE_PORTAL_CONFIG` (optional; else the
wowpriced configuration), `SITE_URL` (default `https://wowpriced.com`),
`ALLOWED_ORIGINS`, `TRIAL_DAYS`, `PBKDF2_ITERATIONS`. KV binding `SUBS`
(existing) holds only `sub:`, `acct:`, `claim:`, `deals:full`,
`jwks:google`, `stripe:price`, `stripe:portal` and `checkout:<ref>` (the
account's last Checkout Session, an hour); counters, the membership cache
and the in-memory price and portal configuration live in memory.

### Operator setup (Stripe dashboard)

1. **Customer portal, cancellation allowed**: run the **stripe setup
   (one-off)** workflow (`scripts/stripe_setup.py`) with the same key; it creates the
   portal configuration the Worker uses (cancel at period end, card update,
   invoices). Also in Settings → Billing → Customer portal: activate it,
   allow customers to **cancel subscriptions** (and to update payment
   methods), set the default redirect to `https://wowpriced.com/`, and copy
   the portal's login link into `STRIPE_PORTAL_LINK` (the "Manage or
   cancel" link for members the Worker finds no customer for, and the
   fallback; the deploy workflow warns while it is empty). The
   dashboard settings are also what `POST /api/portal` falls back to when
   no wowpriced configuration exists.
2. **Cancelling by email**: requests to hello@wowpriced.com are cancelled
   by the operator in the dashboard (Customers → the member → the
   subscription → Cancel). No reminder email before a trial ends is set up
   (operator decision, 2026-10-11). The same address handles `409
   contact_support` (a customer a Google takeover released still bills:
   have the owner claim it back when it is theirs, cancel it when its payer
   asks, or, when it is someone else's and should keep running, delete
   `released_customer` from the account's KV record `acct:<email>`) and a
   squatter whose own card pays for an owner's on-address membership after
   a takeover (cancel it on request).
3. **Payment Link**: its price is the $9.99/month recurring price the
   Worker's checkout bills (or set `STRIPE_PRICE_ID`), and its after-payment
   redirect is `https://wowpriced.com/welcome.html?session_id={CHECKOUT_SESSION_ID}`:
   the setup workflow creates the link with it and updates an existing link
   whose redirect differs, so re-run it once for a link made before (the
   redirect without `session_id` could not be claimed). It stays the
   fallback whenever checkout is unavailable after its checks passed.
4. **Same mode**: the key must match the Payment Link's mode: a live key
   (`sk_live_`/`rk_live_`) with a live link (`https://buy.stripe.com/...`),
   or test with test (`.../test_...`); a test key cannot see live sessions,
   so every claim and checkout would fail. The deploy workflow reports both
   modes (never the key) and warns when they differ. A restricted key
   (`rk_`) needs write access to Checkout Sessions (create and expire) and
   Customer portal (sessions; the configurations are read), and read access
   to Subscriptions (including Search), Customers, Payment Links and Prices.

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

## Newsletter double opt-in

KV `sub:<email>` = `{ts, token, confirmed, confirm_sent_at}`. A record
written before the field existed (no `confirmed` key) counts as confirmed,
so nobody already on the list is dropped. `POST /subscribe` creates
`confirmed: false` (and leaves an existing record alone: re-subscribing an
unconfirmed address just returns ok).

| Route | Auth | Effect |
|---|---|---|
| `GET /confirm?e=&t=` | token | sets `confirmed: true`; 200 HTML page linking to `<site>/subscribe.html?confirmed=1` (or a 303 there for a browser) |
| `GET /list` | `Bearer NEWSLETTER_SECRET` | confirmed addresses only, `{emails:[{email, token}]}` (unchanged shape) |
| `GET /list?pending=1` | same bearer | unconfirmed addresses with an empty `confirm_sent_at`, same shape |
| `POST /list/confirm-sent` `{emails:[...]}` | same bearer | sets `confirm_sent_at = now` on those records; `204` |
| `GET /unsubscribe?e=&t=` | token | no longer deletes: 200 HTML page with a POST form button "Unsubscribe" |
| `POST /unsubscribe?e=&t=` | token | deletes when the token matches (the form, or the RFC 8058 one-click POST mail clients send) |

Pipeline (`dealradar/publish/newsletter.py::send_confirmations`): EVERY run,
the fast lane included, right before the daily send. It reads
`/list?pending=1`, emails each address ONE confirmation link
(`<worker_url>/confirm?e=<urlencoded>&t=<token>`; subject "Confirm your
wowpriced email"; text + minimal HTML; the line "If you didn't sign up,
ignore this and you won't hear from us"; no deals, no list headers), then
`POST /list/confirm-sent` with those addresses so the Worker never lists
them again. At most `MAX_CONFIRMATIONS_PER_RUN` (50) per run, never past
the run deadline; it skips quietly (one log line) when
`newsletter.worker_url`, `NEWSLETTER_SECRET` or `SMTP_HOST` is missing or
the list call fails. The Worker's `confirm_sent_at` is the record; state.db
meta `newsletter:confirm_sent` (fingerprints of the addresses emailed in
the last 24 h, never addresses) keeps a failed confirm-sent POST from
causing a second email the same day: the next run re-posts the mark for
those addresses instead of emailing them.

The daily send keeps using `GET /list` (confirmed only) and builds the
unsubscribe link exactly as before (`<worker_url>/unsubscribe?e=&t=`, now
a confirm page; the `List-Unsubscribe-Post` one-click header targets
`POST /unsubscribe`). `subscribe.html`: `?ok=1` says to check the inbox
and click the confirmation link, `?confirmed=1` says "You're confirmed",
`?unsubscribed=1` is unchanged.

## Page behavior

1. Load `deals.json` (teaser). If `localStorage["wp.session"]` exists,
   `GET /api/me`; if `member`, `GET /api/deals` and render the full
   payload instead (same renderer). A `401` clears the token.
2. Signed out: unlocked cards render normally; locked stubs render as
   blurred cards (photo with `filter: blur(8px)` or the store tile, the
   percent badge, skeleton bars for title and price, no link). One sticky
   CTA panel over the locked area, along the lines of "Unlock all {count}
   deals — {trial_days}-day free trial, then $9.99/mo" with **Sign up
   free** and **Log in**. Nothing promises access without a card.
3. Signed in, not a member (`me.member` false): the same top-8 teaser and
   locked rendering, and the next step by `me.reason`:
   - `none` with `me.trial_available`: "Start your {me.trial_days}-day free
     trial" → `POST /api/checkout` → `location = url`; the fine print says
     a card is required, $9.99/mo after the trial, renews monthly, cancel
     anytime online or by emailing hello@wowpriced.com;
   - `lapsed`, or no trial available: "Resubscribe — $9.99/mo" → the same
     `POST /api/checkout` (no trial is added);
   - `503 checkout_unavailable` → go to `fallback_url` when non-empty, else
     show an error (try again in a minute); `409 already_member` →
     re-render with the returned `me` (when its `member` is still false, a
     subscription already exists for this email: say so, with "I just
     subscribed", Google sign-in and hello@wowpriced.com as the ways on);
   - `409 payment_issue` → "Your last payment didn't go through: update
     your card" with the "Manage membership" action (`POST /api/portal`, else
     `me.portal_url`), never a second checkout;
   - `409 contact_support` → "Email hello@wowpriced.com and we'll sort it
     out" (a subscription from before this account's Google sign-in is
     still running);
   - "I just subscribed" → `GET /api/me?fresh=1`.
4. `?checkout=cancel` on the home page (Stripe's cancel_url): a short
   "Checkout cancelled, nothing was charged" note; nothing else changes.
5. Header: "Log in" link when signed out; the account email with
   "Log out" when signed in, and "Manage billing" for an account with
   `me.claimed` or `me.member` (or `me.portal_url` set): the portal serves
   members found by their own checkout or verified email too → `POST
   /api/portal` → `location = url`; on `404`/`503` → `fallback_url` or
   `me.portal_url` when set. A
   member on `reason: "trial"` sees "Free trial: ends {trial_ends_at}".
6. Auth modal: email + password with a Sign up / Log in toggle, inline
   errors from the API, and the Google button (Google Identity Services,
   loaded only when `meta.google_client_id` is set; the credential
   callback posts to `/api/google`). Signing up lands on step 3 (free
   account, start-trial button), not on the full list.
7. Filters, tabs, search, sorts and the countdown keep working on
   whatever list is loaded; the Just-missed strip is always public.
8. `meta.api_url` empty: no paywall UI at all (everything renders as
   today), so the site keeps working before the Worker is deployed.
9. `welcome.html` ("Claiming a payment" above) is where both the Worker's
   checkout and the Payment Link land; it needs `meta.api_url` too and does
   nothing special without a `session_id`.
