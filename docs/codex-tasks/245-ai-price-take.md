# 245 — "AI take" on product price pages: words by AI, every number checked in code (ships off)

## Why

Operator 2026-10-16: "AI sells." Product pages (`/p/…`) and Deal Check should
open with a short plain-English take a shopper can act on, e.g. "Good time to
buy: $89.99 is the lowest we've seen since August. It has dropped this low 3
times in 90 days, usually for under a week." The project rule stays: **math is
code, never the LLM.** Code computes every fact; the model only phrases them
and may copy numbers exactly as written in the facts.

## Read first

CLAUDE.md, AGENTS.md, docs/HANDOFF.md, SAVINGS_RULES.md,
`dealradar/savings.py` (`product_price_facts`, `price_verdict`),
`dealradar/publish/product_pages.py` (`prepare`, `answer`, `facts_html`,
`publish`), `dealradar/parse/claude_client.py` (`ClaudeParser`: `_reserve`,
`_settle`, `_check_spend_cap`, ledger, `categorize_titles` as the pattern for
a small batched non-extraction call), `dealradar/parse/extractor.py`
(`verify_evidence` for the spirit of exact checking), `dealradar/pipeline.py`
(where publish runs and where spend caps are bound), `dealradar/db.py`
(meta/cache tables), config.yaml `parse:` spend caps and model pricing,
tests/test_e2e_offline.py::test_rerun_is_idempotent.

## Task

1. Config `product_pages.ai_take:` `enabled: false`, `model:
   "claude-haiku-5-5"`, `max_pages_per_run: 40`, `max_usd_per_run: 0.05`,
   `max_usd_per_day: 0.40` (both also count inside the existing per-run and
   per-day caps; whichever is lower wins), `max_words: 45`. Validated in config.py.
2. `dealradar/publish/ai_take.py`:
   - `facts_for(page)` → a small dict of **pre-formatted strings** from code
     only: current price, usual (median) price + window, lowest/highest with
     dates, checks count, first check date, deals in 90 days, average deal
     discount, the shipped `answer()` verdict sentence, store, condition. No
     raw floats reach the model.
   - One batched call per run for up to `max_pages_per_run` pages whose facts
     hash changed (structured output: `[{"key": ..., "take": ...}]`), system
     prompt: plain words, ≤ max_words, no hype words list (e.g. "insane",
     "steal", "best ever"), no advice beyond the verdict's direction, never a
     number that is not in the facts.
   - **`verify_take(text, facts)`** — the gate, in code: every `$` amount,
     `%`, count, month/date token in the text must appear verbatim in the
     facts strings; the text must not contradict the verdict direction
     (lower/about/higher → allowed lead phrases table); length ≤ max_words;
     no banned words; no URLs. Any failure → no take for that page (logged
     privately with the reason), never a retry with different numbers.
   - Cache: table/meta keyed by (page key, facts hash, prompt version) →
     verified take + model + created_at. Rerunning the same run key makes 0
     calls and changes 0 bytes. Facts unchanged → reuse; facts changed → the
     old take is withdrawn immediately (never shown against new facts) and a
     new one is requested within the caps.
3. Rendering (only when enabled and a verified take exists for the current
   facts hash): a "AI take" block at the top of the product page above the
   facts table, with a small label "AI summary · every number checked against
   our price records" linking to `/how-it-works.html#ai`. Add that anchor
   section (3 sentences) to how-it-works. Also add `take` to the Deal Check
   entry (task 240's `build`) for `history` stores only, when present.
   Amazon pages follow `amazon_history` (show → allowed); Walmart/eBay never
   get a take.
4. Private run summary `ai_take: {eligible, requested, verified, rejected:
   {reason: n}, cached, usd}`.

## Tests (offline, fake client)

- Off (default): no calls, every published byte unchanged.
- `verify_take`: table of accepted and rejected texts (invented number,
  rounded number "$90" vs "$89.99", wrong month, contradicting direction,
  hype word, too long, URL) — rejected ones never render.
- Caps: per-run and per-day ceilings stop requests; existing caps still win;
  cost priced by the model actually used.
- Idempotency: extend test_rerun_is_idempotent (enabled run): second run 0
  calls, identical bytes, no new rows.
- Facts change → old take gone in the same publish.
- Amazon hide → no Amazon takes; Walmart/eBay never.

## Rules

Math stays in savings.py; the model never computes. Never weaken
`verify_evidence`. No retailer/Keepa calls. Do not change
tests/test_workflows.py. Run tests with -n 2 and your own basetemp
/tmp/task245-*; delete it after each run. Run the full suite once at the end
(`-n 2`), report the exact pass/fail line. Do not commit. Update
docs/HANDOFF.md "In flight" with one short entry.
