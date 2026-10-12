# Codex task 036: AI spend that lasts all day (pacing) + a measured critic check

**Read first:** AGENTS.md, CLAUDE.md ("Money has caps": never remove or route
around them; knobs live in config.yaml). Files: `dealradar/config.py`
(`daily_spend_cap`), `dealradar/pipeline.py` (the "Daily ceiling" block
before parse, the deal critic), `dealradar/parse/` (critic, spend
accounting by kind and model), `dealradar/db.py` (`spend_usd_for_day`),
`dealradar/growth.py` / the growth report Money block, tests.

Measured 2026-10-12 (00:00–07:36 UTC, 32 runs): $5.05 spent, about $0.45 per
hour since 04:00, so about $11–16 a day. The temporary $15/day boost ends on
2026-10-14 and the cap returns to $6/day. With no pacing, the $6 runs out
around 13:00 UTC and parsing stops until midnight UTC, exactly when US
shoppers browse. Fast-lane spend is 65% critic (Sonnet), 29% extract (Haiku).

## 1. Pacing (stricter, never looser)
- Config `parse.spend_pacing: true`, `parse.spend_pacing_headroom_hours: 2`.
- Allowed so far today = `day_cap × min(1, (minutes since 00:00 UTC +
  headroom × 60) / 1440)`, where `day_cap = daily_spend_cap(cfg, now)`.
  This run may spend `min(per-run cap, allowed so far − spent today)`; at or
  below zero it takes the existing "cap exhausted" path (posts stay
  unrecorded and are retried by the next run, not lost; prove it in a test).
- The daily and per-run caps are unchanged; pacing can only lower a run's
  allowance. `spend_pacing: false` restores today's behaviour exactly.
- Step summary + log line (counts and dollars only): `paced allowance $X
  (allowed $A of $C by HH:MM UTC, spent $S)`.

## 2. Critic shadow (data for a later model decision; changes nothing)
- Config `parse.critic_shadow_model: "claude-haiku-5-5"`,
  `parse.critic_shadow_batches_per_day: 12` (0 = off).
- For up to that many critic batches per UTC day, send the same batch to
  the shadow model too. Its verdicts are recorded only; they never change
  what publishes. Store per-day counts in the db: deals judged, same
  verdict, shadow-kept-but-critic-killed, shadow-killed-but-critic-kept.
- Shadow spend is real spend: kind `critic_shadow`, priced by its model,
  inside every cap (skip the shadow when the run's allowance is short;
  the real critic always comes first).
- Growth report Money block: one line "critic shadow (Haiku vs Sonnet):
  agree N/M today, 7-day N/M".

## 3. Tests
Pacing maths at 00:00, 06:00, 12:00, 23:59 and on a boost day; never above
the hard daily or per-run cap; zero allowance skips parse and the same posts
parse on the next run; `test_rerun_is_idempotent` still passes; shadow never
alters a verdict or a published deal, respects its daily batch limit, is
priced by model and counted under the caps; switches off = old behaviour.
One short bullet in docs/HANDOFF.md under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
