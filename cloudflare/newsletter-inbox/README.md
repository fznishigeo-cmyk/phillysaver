# Partner newsletter inbox

`wowpriced-newsletter-inbox` has both an Email Routing handler and a private
HTTPS API. It forwards the original message to `NEWSLETTER_FORWARD_TO`, then
parses MIME with the pinned `postal-mime` dependency. KV holds sender (the
decoded From address, separately from the envelope sender), subject, date,
message id, receipt time, and text/HTML for **7 days**. Attachments are omitted.
No body, sender or secret is logged. Oversized/unreadable mail reaches the
human but is not staged; bodies are never truncated into incomplete deals.

Setup:

1. Add GitHub repository secrets `NEWSLETTER_INBOX_TOKEN` (a new random
   bearer token) and `NEWSLETTER_FORWARD_TO` (the operator's existing verified
   Email Routing destination, directly, such as their Gmail address). The
   Cloudflare API token/account secrets used by the subscribe deployment are
   also required; the token needs Worker Scripts and Workers KV Storage edit
   permissions. Run **deploy newsletter inbox** manually, then paste its
   Worker URL into `config.yaml` → `partner_newsletters.inbox_url`.
2. In Cloudflare, **Compute → Email Service → Email Routing → Routing Rules →
   Create routing rule**: Email pattern **deals**, domain **wowpriced.com**,
   Action **Send to a Worker**, Destination **wowpriced-newsletter-inbox**,
   **Save**. Verify the forwarding destination under **Destination Addresses**
   if it is not already verified. Keep the existing hello@ and catch-all rules.
3. Subscribe `deals@wowpriced.com` to the eight partners in `sources.yaml` and
   confirm subscriptions from the forwarded messages. Copy each actual
   **From** address into its `*_newsletter.newsletter_from` list, then set
   that entry's `enabled: true`. Do not guess the addresses. Existing RSS
   entries remain configured so they can recover independently.

The [Cloudflare Email Workers API](https://developers.cloudflare.com/email-service/api/route-emails/email-handler/)
documents unchanged forwarding and MIME parsing; the
[Email Routing setup](https://developers.cloudflare.com/email-service/configuration/email-routing-addresses/)
documents destination verification and the rule fields.

API (no browser access or public listing):

- `GET /inbox?since=<ISO timestamp>&cursor=<opaque cursor>&limit=<count>`
  returns `{messages: [...], next_cursor: string|null}`. `since` compares the
  **receipt** time; a partner's old/incorrect Date header cannot hide new
  mail. Expired records are filtered even while KV expiry propagates.
- `POST /inbox/ack` with `{"ids":["msg:<sha256>"]}` deletes only validated
  inbox keys and returns `{"acknowledged": <count>}`. Repeated deletion is safe.
- Both require `Authorization: Bearer <NEWSLETTER_INBOX_TOKEN>`. Comparison
  hashes both inputs to a fixed size, then compares all bytes. HTTP responses
  are `no-store`; failures return fixed errors without message contents.

Size knobs are the Worker's `wrangler.toml` vars: 1 MiB raw email, 256 KiB
stored JSON per email, 2 MiB response and at most 25 records per KV page (the
byte cap can lower this to 7). Acknowledgements accept at most 100 ids / 16 KiB.
The Python pull has separate `partner_newsletters.max_messages` and
`max_response_bytes` config limits, shares one pull across sources, and keeps
the existing 1 request/second, retry and deadline limits on this private API.
It never caches bodies in SQLite, follows authentication redirects, visits
partner pages or resolves tracking links. Only exact allow-listed senders and
direct partner post links qualify. HTML cards with distinct credit links are
split conservatively; plain-text paragraphs can qualify too. No direct post
link or an ambiguous layout means no deal is extracted. Unknown senders,
confirmation mail and unusable mail are ignored and acknowledged after a
successful run; the operator still has their forwarded originals.

All parts of a usable email must be recorded in `posts_seen` before its ack.
Missing parser keys, spend caps, parse limits and failed runs leave unfinished
mail pending until a later run or KV expiry. Different delivery headers do
not change post content hashes. The usual extractor verifies price quotes
against the email text unchanged. SQLite contains facts, links and hashes.
Customer newsletter sending remains off.

Offline unit tests inject a MIME parser and an in-memory KV:

```sh
node --test cloudflare/newsletter-inbox/inbox.test.mjs
.venv/bin/python -m pytest -q tests/test_newsletter_inbox.py
```

`npm install --ignore-scripts --no-audit --no-fund` installs the MIME parser
for deployment. Live MIME delivery, actual sender addresses and each partner's
link layout still need checking after setup; tracking-only links are skipped.

Validation for task 055: full `.venv/bin/python -m pytest -q` (with short
tracebacks) produced **2,113 passed, 1 deselected**, plus the seven existing
socket failures below (`PermissionError: Operation not permitted` from creating
a localhost HTTP server). All **11 Worker unit tests** passed when invoked
directly with `node cloudflare/newsletter-inbox/inbox.test.mjs`. Deployment
YAML/shell, Worker TOML and package JSON passed syntax checks. The sandbox
cannot resolve registry.npmjs.org, so the pinned MIME dependency installation
and Wrangler bundle/deploy were not verified here. No commit or push.

Blocked tests (all in `tests/test_workflows.py`):

- `test_deploy_checks_the_yearly_price_with_stripe[change0-]`
- `test_deploy_checks_the_yearly_price_with_stripe[change1-amount is 4999 cents, page shows 4900]`
- `test_deploy_checks_the_yearly_price_with_stripe[change2-billing is every 1 month, not yearly]`
- `test_deploy_checks_the_yearly_price_with_stripe[change3-price is not active]`
- `test_deploy_checks_the_yearly_price_with_stripe[change4-different modes]`
- `test_deploy_checks_the_yearly_price_with_stripe[change5-currency is eur]`
- `test_deploy_yearly_check_skips_without_a_price_and_fails_without_a_key`
