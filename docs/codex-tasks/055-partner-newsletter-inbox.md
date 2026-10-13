# Codex task 055: read blocked partners' deals from their email newsletters

**Read first:** AGENTS.md, CLAUDE.md (politeness is law; never bypass a bot
wall or firewall, never disguise the crawler; store facts only; evidence or
it doesn't exist; knobs in config.yaml; idempotency is a contract). Files:
`cloudflare/` (the existing subscribe Worker, its KV and secrets pattern),
`.github/workflows/deploy-subscribe-worker.yml` (wrangler deploy pattern),
`dealradar/fetch/` (`base.py`, `registry.py`, `rss.py`, an adapter to copy),
`sources.yaml` (the permission sources and `crawl_permission`),
`dealradar/models.py` (`RawPost`), `dealradar/parse/extractor.py`
(`verify_evidence`), `dealradar/config.py` (`SECRET_NAMES`, `get_secret`).

Operator (2026-10-13): 8 sources gave written permission to use their deals
but their sites' firewalls refuse our crawler (HTTP 403 / bot check): Ben's
Bargains, TechBargains, CouponCabin, DealMama, eDealinfo, Raining Hot
Coupons, Coupon Lady Deals, Coupon With Kayla. The operator will not contact
them again and we never get around a firewall. Instead the operator
subscribes `deals@wowpriced.com` to each one's daily deals newsletter; we read
the deals from those emails. Their servers are never touched.

## 1. Inbox Worker (`cloudflare/newsletter-inbox/`)
- A Cloudflare Email Worker for `deals@wowpriced.com` (the operator adds the
  Email Routing rule "deals@ → this Worker" in the dashboard; give the exact
  steps in the HANDOFF bullet). For every message: forward it unchanged to
  the operator's verified address (config/secret, e.g. hello@wowpriced.com) so
  confirmation emails reach a human; store sender address, subject, date,
  message id and the text/HTML body in KV under a key with a TTL of 7 days.
- An HTTPS endpoint `GET /inbox?since=<iso>` and `POST /inbox/ack` behind a
  bearer secret (`NEWSLETTER_INBOX_TOKEN`, GitHub secret + Worker secret),
  returning pending messages and deleting acknowledged ones. Constant-time
  token check; no listing without the token; size caps.
- Deploy workflow like deploy-subscribe-worker.yml (manual dispatch).

## 2. Adapter `newsletter` (`dealradar/fetch/newsletter.py`)
- Pulls pending messages via the endpoint, maps each sender to a source by an
  allow-list in sources.yaml (new field, e.g. `newsletter_from:
  [deals@bensbargains.com, ...]`; the operator fills real addresses after the
  first emails arrive; unknown senders are ignored and acknowledged).
- Each email becomes RawPost(s): the deal text for the extractor and, as the
  source's credit link, the partner's own post URL found in the email
  (never our link, never a tracker rewrite). Evidence quotes must verify
  against the email text like any post (`verify_evidence` unchanged).
- Acknowledge only after the posts are handled (idempotent: content-hash
  dedupe; a re-run adds no rows). Never persist full email bodies in
  state.db; facts only, as for every source.
- One new source entry per partner (`method: email`, `adapter: newsletter`,
  `enabled: false` until its sender is filled in), keeping the existing RSS
  entry (it recovers by itself if a partner ever lifts the block).

## 3. Tests (offline)
Worker: unit tests for forward+store, token check, ack/delete, TTL, size cap
(Miniflare or plain JS tests, whatever the repo already uses, else a small
node test). Python: adapter maps senders, ignores unknown, extracts the
partner post URL as credit, idempotent re-run, never stores the body, a
missing token or endpoint is "needs_key", not a breaker failure. One short
HANDOFF bullet with the operator's 3 steps (deploy, routing rule, subscribe).

Run `.venv/bin/python -m pytest -q`; all green except tests that need local
sockets your sandbox blocks (name them). Do NOT commit or push. End with a
short note of what changed, what the operator must do, and anything unsure.
