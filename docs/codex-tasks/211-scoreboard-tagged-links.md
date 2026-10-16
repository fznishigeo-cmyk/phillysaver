# 211 — Real-visitor scoreboard + tagged social links (traffic plan B1)

## Why
Operator 2026-10-16: very discouraged by "no traffic"; GA4 week one mixed our own visits and robots with
real people. From Oct 16 GA4 drops internal traffic (IP filter + `?internal=1`), the phone-audit robot no
longer loads analytics, and GA4 now receives landing campaign tags (commit 6192789). We need ONE honest
number each Monday ("real visitors this week", with the trend) and per-channel credit for our social posts.

## Task (read CLAUDE.md, AGENTS.md, docs/HANDOFF.md first)
1. Tag outbound social links with `utm_source=<pinterest|facebook|instagram|telegram>&utm_medium=social&
   utm_campaign=deal` using `dealradar/publish/traffic.py` `tagged_url` (extend it for a medium argument;
   keep share/rss behavior byte-identical). Apply in `pinterest.pin_payload`, `meta.caption` (Facebook/
   Instagram) and `telegram_pub.format_message` (path form). CRITICAL: posting dedup keys/alert tokens must
   stay computed from the untagged canonical path, so a rerun posts nothing new and already-posted deals are
   never re-posted (test it). Canonical/og URLs on our pages stay untagged. No prices in social copy (already
   a rule — keep it).
2. `config.yaml` `social.profiles: {pinterest: "", facebook: "", instagram: "", telegram: ""}` (URLs only,
   validated https on the right host). When any is set: a small "Follow wowpriced" row in the footer and on
   /join, and `sameAs` on the home page Organization JSON-LD. All empty = every published byte unchanged.
3. Growth report (`dealradar/growth/report.py`, `google.py`; private report only, never public logs):
   - Every day: "Real visitors yesterday: N (people), M sessions" from GA4 totalUsers/sessions.
   - Mondays: a "Scoreboard" block: real visitors this week vs last week (with % change), top channels
     (Organic Search, Organic Social by source pinterest/facebook/instagram/telegram via sessionSource and
     our utm_source, Direct, Referral), Google Search Console clicks/impressions for the week, Telegram
     subscribers (`getChatMemberCount` with the existing bot token/channel config; skip quietly if absent),
     Pinterest outbound clicks if the existing pinterest growth code exposes them.
   - Add a GA4 event `credit_click` (param: partner = source display name, no URL) fired when a visitor
     clicks a source-credit link on cards/deal pages; count it per partner in the weekly block.
   - Plain-language lines, no jargon; numbers labeled "not confirmed" when an API call fails.
4. Tests (offline): every social payload tagged and price-free; dedup/rerun posts 0; canonicals unchanged;
   profiles empty = bytes unchanged, set = footer row + sameAs; report math for week-over-week and channel
   grouping from fixture GA4 responses; credit_click wired through the existing consent-gated tracking path.
5. Full suite: `.venv/bin/python -m pytest -q -n 2 -p no:cacheprovider --basetemp=/tmp/task211-full`.
   Re-pin only intended byte changes. Do not commit. Short HANDOFF note.

## Constraints
CLAUDE.md non-negotiables; no new third-party scripts; no LLM calls; keep secrets out of logs; own
basetemp under /tmp/task211-*; never delete other /tmp dirs.
