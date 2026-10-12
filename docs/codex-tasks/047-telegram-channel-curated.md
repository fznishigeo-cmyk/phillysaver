# Codex task 047: the Telegram channel posts a few top picks, not every deal

**Read first:** AGENTS.md, CLAUDE.md (idempotency is a contract; knobs in
config.yaml; store and publish facts only). Files:
`dealradar/publish/telegram_pub.py` (`format_message`, `_should_send`,
`send_alerts`), `dealradar/pipeline.py` (the `send_alerts` call),
`dealradar/publish/social.py` (`_candidates`, `_word_trim`,
`_without_prices`: the published picks shared by Pinterest and Meta),
`dealradar/config.py` (`telegram_*`), `config.yaml` (`telegram:`),
`tests/test_telegram*.py`, `tests/test_e2e_offline.py`.

Operator (2026-10-12) is about to create a public Telegram channel and add
`TELEGRAM_CHANNEL_ID`. Today `send_alerts` would post **every** published
deal not yet alerted (over 1,000 on the first run, 15 a minute), each
message with a price, a link to the competitor post we found it on ("via
… URL") and a raw store link. That would flood a brand-new channel and
breaks the rules we follow on Pinterest/Facebook/Instagram. Fix the channel
only; admin alerts (`TELEGRAM_ADMIN_CHAT_ID`, growth report) are untouched.

## 1. Config (`config.yaml`, under `telegram:`)
```yaml
  channel:                     # public deals channel (operator, 2026-10-12)
    posts_per_run: 1           # fast lane runs every 10 min
    daily_max: 24              # per America/New_York day
    min_gap_minutes: 30        # between two channel posts
    hours_local: [8, 23]       # post only from 08:00 to 22:59 New York
    disclosure: "#ad"
```
Load into `cfg` like the other telegram knobs (`telegram_channel` dict).

## 2. What gets posted
- Candidates: `social._candidates` (live published picks in pick order with
  an https image and a live deal page), excluding deals already posted to
  the channel. Each deal is posted to the channel **once** (no re-sends on
  price drops or tier upgrades for the channel).
- Caps: `posts_per_run`, `daily_max`, `min_gap_minutes`, `hours_local`, all
  from config; nothing posted outside the window.
- Message (plain text, no price anywhere; reuse `_without_prices`,
  `_word_trim`, item ≤ 120 chars):
  `{pct}% off {item} at {store}`
  `Verified by wowpriced: https://wowpriced.com{deal path}`
  `{disclosure}`
  No "via" source credit or source URL, no store/affiliate link (the deal
  page carries the store link and the credit). Telegram link preview on.
- First enable: no backlog flood. The caps above guarantee it; add a test
  with 1,500 eligible deals proving one run sends `posts_per_run` only.

## 3. Idempotency and state
Keep `alerts_sent` / `last_alert` as the record (channel `telegram`), with a
dedup token per deal (`dedupe_key|channel`), so a re-run of the same run key
sends nothing twice (`test_rerun_is_idempotent` must pass). Missing
credentials still record nothing (today's fallback-dry-run rule); explicit
`dry_run: true` still logs the would-be message.

## 4. Tests
Message format (no "$", no digits that are a price, disclosure last, deal
page URL, no source URL); picks only, in pick order; per-run, daily,
min-gap and hours caps in New York time across a DST edge; once per deal
(price drop does not resend); 1,500-deal backlog sends 1; missing
credentials record nothing; admin alert path unchanged; e2e idempotency.
One short HANDOFF bullet under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
