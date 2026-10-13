# Codex task 056: newsletters from every permission partner, matched automatically

**Read first:** AGENTS.md, CLAUDE.md (store facts only; evidence or it
doesn't exist; idempotency is a contract; knobs in config.yaml). Files:
`dealradar/fetch/newsletter.py` (task 055), `sources.yaml` (the ~60 entries
with `crawl_permission`, and the 8 `method: email` entries task 055 added),
`dealradar/models.py` (`SourceConfig.newsletter_from`), `dealradar/config.py`,
`dealradar/pipeline.py` (`_fetch_all`, acknowledge), `dealradar/verify.py`
and `dealradar/db.py` (`distinct_sources_within`: corroboration),
`dealradar/publish/admin_alerts.py`, `dealradar/growth/report.py`, tests.

Operator (2026-10-13): subscribe deals@wowpriced.com to the newsletters of
ALL ~60 permission partners, not only the 8 the firewall blocks. RSS keeps
running; email is a second channel for the same partner ("double is better").

## 1. One partner, two channels
- Replace the 8 separate email entries with ONE enabled source
  `partner_newsletters` (`method: email`, `adapter: newsletter`). It reads
  the inbox and emits each email's posts under the matched partner's own
  `source_name` (the partner's existing sources.yaml entry), so a deal seen
  in the feed and in the newsletter is one partner: one credit, never two
  "independent" confirmations, same evidence rules, same dedupe.
- `newsletter_from` (optional list) moves onto the partner's own entry.
- Without inbox URL/token the source is "needs_key" (not a breaker failure).

## 2. Match senders automatically
- A sender matches a partner when its address is in that partner's
  `newsletter_from`, or when the sender's registrable domain equals the
  partner's site domain (e.g. `deals@mail.hip2save.com` -> hip2save.com;
  handle common two-label public suffixes like co.uk correctly). Ambiguous
  matches (two partners) and no match are "unknown".
- Unknown senders: record address, count and last-seen date in db meta
  (no subject, no body), acknowledge the message (the original is already
  forwarded to the operator), and list them once a day in the admin Telegram
  message / growth report ("New newsletter senders to map: …"), counts only.

## 3. Operator checklist
- `docs/partner-newsletters.md`: every permission partner, its site link,
  a box to tick when subscribed, and the matched sender once known (the
  pipeline does not write this file; it is a static checklist generated
  from sources.yaml by a small script you add under scripts/).

## 4. Tests (offline)
Domain matching (incl. subdomains, unrelated look-alikes such as
hip2save.com.evil.example, two partners on one domain = unknown); posts
carry the partner's source_name; feed + email of one deal = one sighting
source for corroboration; unknown senders recorded without subject/body and
reported once a day; re-run idempotent; HANDOFF bullet updated (replace the
055 operator step 3 with "subscribe; senders match by domain").

Run `.venv/bin/python -m pytest -q`; all green except tests that need local
sockets your sandbox blocks (name them). Do NOT commit or push. End with a
short note of what changed and anything unsure.
