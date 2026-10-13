# Codex task 059: more publishable deals per AI dollar

**Read first:** AGENTS.md, CLAUDE.md (money caps, evidence check, math in
code, idempotency, surgical changes). Files: `dealradar/parse/claude_client.py`
(`_reserve`, `_settle`, `_account`, `_record_cost`, `extract`, critic calls),
`dealradar/parse/extractor.py` (`extract_posts` loop, `_smells_like_deal`,
second opinion), `dealradar/pipeline.py` (parse step, run summary),
`dealradar/db.py`, `dealradar/models.py` (`RawPost.content_hash`),
`dealradar/config.py`, `config.yaml` (`parse:`), SAVINGS_RULES.md.

## Why (live numbers, 63 runs, 2026-10-12 07:16 → 10-13 06:17 UTC)
The daily cap is $6.00 and pacing switched parsing OFF for 11 runs in a row
overnight, so every wasted cent is deals we never read. Spend: $7.32 sampled.
- extract (haiku-5-5): 4,352 calls, $3.84 (52%), $0.00088/call.
- critic (sonnet-5-5): 250 calls, $2.01 (27%), $0.0080/call.
- second_opinion (sonnet-5-5): 153 calls, $1.46 (20%), $0.0095/call; the
  hourly lane hits the 8-per-run cap almost every run. Yield is unmeasured.
- The three Slickdeals feeds (frontpage, popular, amazon) are 58% of extract
  spend. `RawPost.content_hash` includes `source_name`, so the same thread
  text arriving in two Slickdeals feeds is read (and paid) twice.
- ~88% of extracted deals end "below floor" (publish.min_pct), so we pay
  ~$0.013-0.015 per publishable deal.
- No call uses prompt caching. Claude API facts (docs, 2026-10): minimum
  cacheable prefix is 512 tokens for Haiku 5.5 and Sonnet 5.5; a 5-minute
  cache write costs 1.25x base input; a cache read costs 0.1x base input
  (0.05x for Sonnet 5.5 and Opus 5.5). Usage reports
  `cache_creation_input_tokens`, `cache_read_input_tokens` and
  `input_tokens` (uncached, after the last breakpoint); total input is their
  sum. Below the minimum nothing is cached and both cache fields are 0.

## Do
1. **Prompt caching, priced exactly.** Mark the static prefix (system prompt,
   and the JSON-only suffix where it is static) of extract, second-opinion
   and critic calls as cacheable (`cache_control: {"type": "ephemeral"}`,
   5-minute TTL). The variable post / deal list stays after the breakpoint.
   Accounting must price every call by the model actually used:
   `input_tokens*in + cache_creation_input_tokens*in*write_mult +
   cache_read_input_tokens*in*read_mult + output_tokens*out`. Put the
   multipliers in `config.yaml` `model_pricing_per_mtok` (per model, e.g.
   `cache_write: 1.25`, `cache_read: 0.10`; sonnet-5-5 `cache_read: 0.05`;
   a model without them falls back to `default`, whose cache_read must not
   be below 0.10 and cache_write not below 1.25). The worst-case reservation
   in `_reserve` must stay >= any possible actual cost: price the whole input
   bound at the write multiplier when caching is on. Paid JSON re-asks and
   the timeout "charge the bound" path keep working.
2. **Read identical post text once across sources.** Add a source-independent
   fingerprint of the cleaned text actually sent to the model. When an
   identical text was already parsed (any source, within
   `posts_seen_days`), reuse that parse instead of paying again: store only
   the structured LLM output (the LLMDeal fields incl. evidence quotes;
   never the post text), re-run `verify_evidence` against THIS post's text
   in code (it must pass, else drop as usual), and record the sighting and
   source credit for THIS source exactly as a fresh parse would. A reused
   post is recorded in posts_seen like a parsed one. Count it in the summary
   (`parse.reused_posts`, `parse.reused_deals`) and never as a paid call.
   Re-running the same run key still adds no rows
   (tests/test_e2e_offline.py::test_rerun_is_idempotent must stay green).
3. **Second opinion only where it can pay off.** Today it fires when Haiku
   found nothing in a post that "smells like a deal". Also require a code-side
   signal that the post can clear the floor: an explicit percent >= the
   effective floor (derive from `publish.min_pct`, as the verification
   thresholds already do; never a new literal), a "free"/"$0"/"100% off"
   offer, or two dollar amounts in the same sentence/line whose implied
   discount (savings.py math) is >= the floor. Add yield counters to the run
   summary: `second_opinion.{calls, deals_extracted, deals_passed_evidence}`.
   Keep `max_second_opinions_per_run`.
4. **Shadow a below-floor pre-filter (no behavior change).** New knob
   `parse.prefilter_below_floor: shadow` (values off|shadow|on; ship
   `shadow`). In shadow, for each post compute in code whether it would be
   skipped: it states exactly one price pair (now + was/reg/list/MSRP),
   no coupon/code/clip/extra/stack/rebate/subscribe words, and the implied
   discount is below `min_pct - 10` points. Still parse it; count
   `prefilter.would_skip` and, of those, how many deals ended publishable
   (`prefilter.would_skip_publishable`). `on` actually skips (tested, but not
   enabled in config).
5. **Value per source in the summary.** `extract_by_source[name]` gains
   `deals` (passed evidence) and `publishable` (passed the publish gate this
   run) next to `calls`/`usd`, so sources can be ranked by deals per dollar.

## Keep exactly
`verify_evidence`, per-run/day caps, pacing, ledger reservation/settlement
semantics (task 054), critic gating and reuse, the 0.50 run cap, all
publish floors. No new network hosts. No LLM arithmetic. No full post text
stored anywhere new.

## Tests
- Accounting: usage with cache write/read fields is priced per the table
  (haiku and sonnet multipliers), the reservation bound is >= the priced
  worst case, and responses without cache fields cost what they cost today.
- Cross-source reuse: same cleaned text from two sources -> one paid call,
  two sightings/credits, evidence re-verified (a text that differs by one
  character is NOT reused); idempotent re-run.
- Second-opinion gate: fires on "60% off" / "$20 (reg $80)" / "free", not on
  "$45 (reg $50)"; counters present.
- Prefilter shadow: counts only, parse output identical to `off`; `on` skips.
- Summary keys present; existing summary keys unchanged.

Run `.venv/bin/python -m pytest -q`; all green except tests needing local
sockets your sandbox blocks (name them). One short HANDOFF bullet. Do NOT
commit or push. End with a note: what changed, expected $/day effect per
item, anything unsure.
