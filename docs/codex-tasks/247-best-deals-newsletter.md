# 247 — Best-deals newsletter: daily or weekly, wowpriced links only, never Amazon (ships off)

## Why

Operator, 2026-10-16 (Amazon reference amz378_998): Amazon price-drop emails are not allowed, so `amazon_watches: off`
is permanent. Instead the operator wants a daily or weekly best-deals newsletter, launching in 1–3 months.

The code exists (`newsletter.py`, the Worker's double opt-in, RFC 8058 one-click headers), but it is unsafe to turn on:
- **Amazon leaks through.** A rendered Amazon row reads "Echo Dot (5th Gen) — (54% off) at Amazon": `email_deal()`
  hides the price but keeps the percent and the store name.
  - Amazon's Participation Requirements and Trademark Guidelines list email as prohibited promotion for Special
    Links, Program Content and Amazon marks.
  - They allow prices only on a "Site".
  - Together with the operator's call, the rule is: **no Amazon product, price, percent, name, mark or link in any email.**
- **No postal address.** CAN-SPAM requires a physical address in every commercial email.
- **"Get deal" links go to the retailer.** eBay Partner Network needs prior written approval for any email promotion.
- **It sells a trial that no longer exists.** The email ends with "Start the 7-day free trial", but the site is free
  (`support.enabled: true`).
- **Daily only.** The copy hard-codes "every morning" and "once a day".
- **Unconfirmed sign-ups.** No one who signed up since 2026-10-12 has received a confirmation email: those emails are
  gated by `newsletter.enabled`. Records from before double opt-in count as confirmed.

## Read first

- CLAUDE.md, AGENTS.md, docs/HANDOFF.md ("Operator decisions": the Amazon, Walmart, customer-email and affiliate entries).
- `dealradar/publish/newsletter.py`: `build_newsletter`, `_row_html`, `_deal_url`, `send_daily_newsletter`,
  `todays_top`, `send_confirmations`, `build_confirmation`, `fetch_subscribers`.
- `dealradar/publish/email_policy.py`.
- `amazon_history.store_of` (task 250; use `checker._store` if 250 has not merged).
- How checker resolves `deal_path`: `deal["deal_path"]`, else meta `seo_deal_paths[sha256(key)[:8]]`.
- `cloudflare/subscribe-worker/{worker.js,subscribers.js,worker.test.mjs}`.
- `dealradar/cli.py`; config.yaml `newsletter:` and `ftc_disclosure`.
- Tests: tests/test_newsletter.py and tests/test_e2e_offline.py::test_rerun_is_idempotent. tests/test_workflows.py
  holds the Worker harness and asserts `/list` rows are exactly `{email, token}` and `worker.js` is ≤175 lines.

## Task

1. **Config** under `newsletter:`, validated. `enabled` stays `false`.
   ```yaml
   cadence: weekly                  # daily | weekly
   weekday: tue                     # weekly only; mon..sun
   mailing_address: ""              # required before ANY email (newsletter or confirmation); PO box or CMRA mailbox
   exclude_stores: [amazon, ebay]   # amazon mandatory (rejected without it); ebay until EPN approves email in writing
   confirmations_enabled: false     # confirmation emails may start before the newsletter does
   ```
2. **Selection.** Before `todays_top` builds its candidates (so the model never sees them), drop every deal that matches any of:
   - `store_of(deal)` is in `exclude_stores`;
   - `email_deal()` flags it as Amazon;
   - its item, store or condition text contains "amazon" (case-insensitive).

   Matching deals are **dropped, not price-hidden**. Also skip a deal that has no written wowpriced deal page.
3. **Links.** Every URL in the email is either `https://<custom_domain>/…` or the Worker's unsubscribe URL.
   - Title, thumbnail and the button ("See the deal") go to the deal page with `?ref=email` appended.
   - "Live wall" goes to `/best-deals-today/?ref=email`.
   - Source credit stays as plain text ("via <name>", no link); the deal page carries the credit link.
   - `_deal_url` and `rewrite_affiliate` are no longer used by the newsletter.
4. **Body.**
   - Top line: "Prices as of <h:mm a> ET, <Mon D>. Prices change fast; the deal page shows the latest check."
   - Then one disclosure line: "We may earn a commission when you buy through links on wowpriced.com."
   - Footer: why you get this (wording follows the cadence), unsubscribe, the full `ftc_disclosure`, `mailing_address`.
   - The membership/trial CTA appears only when `support_enabled` is false (membership mode).
   - Subject: daily "wowpriced: Today's top N deals"; weekly "wowpriced: This week's top N deals".
   - No store name may appear in the subject or the From line.
5. **Send guards and cadence.**
   - When `mailing_address` is empty, both senders skip with reason "newsletter.mailing_address not configured",
     before any Worker or SMTP call.
   - Weekly sends only on `weekday`, at or after `hour_local`.
   - Weekly progress and pick keys are per ISO week (`newsletter:2026-W46`, the pick key likewise).
   - Daily keeps today's keys and behaviour byte for byte.
6. **Confirmations.**
   - `send_confirmations` runs when `confirmations_enabled` or `enabled` is true (still needs `mailing_address`).
   - The text follows the cadence: "the week's top N deals, every Tuesday" or "the day's top N deals, once a day".
   - Add "This address was entered at wowpriced.com on <Mon D, YYYY>." using `ts`, which `fetch_subscribers(pending=True)` now keeps.
   - The footer carries the mailing address.
7. **Worker** (`cloudflare/subscribe-worker/`; keep `worker.js` ≤175 lines):
   - `isConfirmed(rec)` is true only when `rec.confirmed === true`. Records from before double opt-in become pending
     and get exactly one confirmation email.
   - `/list?pending=1` rows add `ts`. `/list` rows stay exactly `{email, token}`.
   - `/list/confirm-sent` re-puts each record with `expirationTtl` = `CONFIRM_EXPIRE_DAYS` (a var, default 30) × 86400,
     so an unconfirmed address disappears 30 days after its confirmation email. `/confirm` re-puts without a TTL.
   - Copy no longer names a cadence. Confirm page: "You're confirmed. Our best-deals email will land in your inbox."
     Unsubscribe page: "Stop the wowpriced deals email to <email>."
   - The unsubscribe flow is otherwise unchanged: the POST returns 200 with no redirect.
8. **Preview.** `python -m dealradar newsletter-preview [--out DIR]` builds the next issue from local state and writes
   `newsletter-preview.html` and `.txt` for the operator to approve.
   - It uses the stored pick if one exists, otherwise rank order. It makes no model call.
   - It makes no Worker or SMTP call, and the unsubscribe link is a placeholder.
9. **Docs.** Update the comments in config.yaml and HANDOFF: the sender is Amazon SES through the existing `SMTP_*` secrets
   (no code change). Before launch, check SPF, DKIM and DMARC alignment, and that the DKIM `h=` list includes
   List-Unsubscribe and List-Unsubscribe-Post.

## Tests (offline)

New tests/test_newsletter_policy.py, plus updates to the existing tests in tests/test_newsletter.py that pin the old
body (retailer "Get deal" links, the trial CTA, linked credits). List each updated assertion in HANDOFF.

- **No Amazon.** A fixture mixes:
  - Amazon deals by store name, by ASIN, by an `amzn.to` link and by an `amazon.com` link;
  - "Amazon Basics" sold at Walmart;
  - eBay, Best Buy and Target deals.

  The subject, text and HTML must:
  - not contain "amazon" or "ebay" (case-insensitive);
  - not contain the hosts `amazon.`, `amzn.to`, `a.co/` or `ebay.`;
  - not contain the item or price of any dropped deal.

  The model-prompt candidates exclude those deals too.
- **Config.** `exclude_stores` without `amazon` is rejected, and so are a bad cadence or weekday. Shipped values load.
- **Links.** Every `href` and every URL in the text is `https://wowpriced.com/…?ref=email` or the unsubscribe URL. A
  deal without a page is skipped.
- **Address guard.** The footer has the address. When it is empty, both senders make zero SMTP and zero Worker calls
  and report the skip reason.
- **Support mode.** `support_enabled` → no "trial" and no "Members" anywhere in the email.
- **Weekly.**
  - It sends only on the weekday, and a second run the same week sends nothing.
  - Rerunning the same run key adds no rows and no sends.
  - The existing daily idempotency tests pass unchanged apart from the body assertions.
- **Headers.** Every newsletter message carries List-Unsubscribe and List-Unsubscribe-Post.
- **Confirmations.** With `confirmations_enabled` on and `enabled` off, each address is sent one email that has the
  sign-up date and the cadence wording. The 24 h guard tests still pass.
- **Worker** (`node --test cloudflare/subscribe-worker/`, extend `worker.test.mjs`):
  - a record with no `confirmed` key is pending;
  - pending rows carry `ts`, and `/list` rows are exactly `{email, token}`;
  - confirm-sent puts with `expirationTtl` (the mock KV records its options), and `/confirm` puts without one;
  - the unsubscribe POST returns 200 with no `Location`.
- **Preview.** It writes both files, with `httpx` and `smtplib` patched to raise.
- **Rerun.** `tests/test_e2e_offline.py::test_rerun_is_idempotent` stays green.

## Rules

- **Ships off.** Every published site byte is unchanged.
- **Evidence and math.** Prices and percents are the stored, evidence-checked facts formatted by existing helpers
  (`display_pct`). No new math, savings.py is untouched, and the LLM only chooses ids.
- **Never weaken** `email_deal` or any evidence check. Emails never carry affiliate tags, Sovrn wrappers or retailer links.
- **Calls and protected files.** No live SMTP, Worker, Keepa or retailer calls. Do not change tests/test_workflows.py.
- **Testing.** Run targeted tests with `-n 1` and your own basetemp `/tmp/task247-*`, and delete it after each run. Also
  run `node --test cloudflare/subscribe-worker/`. Do not run the whole suite.
- **Finishing.** Do not commit. Update docs/HANDOFF.md "In flight" with one short entry.
