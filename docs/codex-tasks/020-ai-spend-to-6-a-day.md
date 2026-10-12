# Codex task 020: AI spend from ~$15/day to under $6/day, same honesty

**Read first:** AGENTS.md, CLAUDE.md (evidence or it doesn't exist; math is
code, never the LLM; money has caps: never remove or route around them;
knobs live in config.yaml; idempotency). Files: `dealradar/parse/claude_client.py`
(`critique_deal`, `_account`, `spend_by_kind`), `dealradar/parse/extractor.py`
(`parse_posts`, `verify_evidence`), `dealradar/parse/schema.py`
(`CriticVerdict`, `CRITIC_SYSTEM_PROMPT`, `ExtractedDeal`), `dealradar/pipeline.py`
(`_run_critic`, the summary), `dealradar/fetch/keepa.py` (`_map_row`,
`_map_warehouse_row`), `dealradar/fetch/woot.py` (`_map_item`),
`dealradar/models.py` (`RawPost`), `config.yaml` (`parse:`).

## Why
The daily cap falls from $15 to $6 on 2026-10-14 by itself (operator
approved; do not touch the caps). Today one fast run costs ~$0.156: the
critic $0.110 (28 Sonnet calls, ~$0.0039 each, mostly output tokens: a
long `reason`), extraction $0.046 (Keepa 40 Haiku calls $0.023, Slickdeals
25 calls $0.023). At ~100 runs a day the $6 cap would stop parsing by
mid-afternoon. Goal: **≤ $0.05 per typical fast run** with the critic still
judging every publishable deal (standing rule: never skip the critic for
Keepa deals; a review showed inflated bases).

## 1. Critic: batched, short, reused
- `critique_deals(parser, deals) -> list[dict | None]`: ONE call judges up
  to `parse.critic_batch_size` deals (config, default 8). Same
  `CRITIC_SYSTEM_PROMPT` rules, plus: judge each deal independently; the
  answer is a list of verdicts with the deal's index; `reason` is one short
  sentence (≤ 120 characters; truncate in code too). Structured output
  (a pydantic model holding the list). `max_tokens` scales with the batch
  (about 80 per deal + 100). Accounting stays `kind="critic"` at the
  critic model's price. A missing/duplicate/out-of-range index or a failed
  call = no opinion for those deals (they stay unjudged and are retried
  next run, exactly like today's `None`). Keep `critique_deal` working (a
  batch of one) for existing callers/tests.
- **Reuse:** before spending, reuse a verdict already stored on another
  deal (any status) with the SAME ASIN (from `asin` or the canonical
  Amazon URL), the SAME condition class (new vs used/open_box/refurbished)
  and a regular price within ±2%. Store it with `"reused_from": <dedupe_key>`.
  Never reuse a low-confidence (< 0.7) "plausible" verdict that has not been
  rechecked; never reuse across different ASINs or for non-Amazon deals.
- `_run_critic` keeps its rules (publishable deals only, the one recheck of
  a low-confidence pass, the spend-cap break, `suspect_msrp`), collects the
  deals to judge, then batches them. Stats: `judged`, `suspect`, `reused`,
  `calls`.

## 2. Keepa (and simple Woot) posts skip the LLM read
Keepa post text is synthesized by our own code from API fields, so an LLM
read only copies numbers we already have.
- `RawPost` gets an optional transient `structured: dict` (never
  persisted, like `text`). `keepa._map_row` / `_map_warehouse_row` fill it
  with the item title, ASIN, store "Amazon", sale price, regular/new price
  basis, conditions (`used`/`open_box` for warehouse) and the exact lines
  that state each number. `woot._map_item` fills it only when SalePrice and
  ListPrice are each ONE plain dollar amount (a range or missing price =
  no `structured`; the LLM reads it as today).
- In `parse_posts`, a post with `structured` builds its `ExtractedDeal`
  in code: the same fields the LLM would return for that text (compare
  with what the LLM path stores today for both Keepa shapes and Woot), with
  evidence quotes copied VERBATIM from `post.text`, and it still goes through
  `verify_evidence` (a failing quote drops the deal, as always). Savings
  math stays in `savings.py`. Product link: the canonical `/dp/ASIN`.
- **Category** (the LLM chose it before): first reuse the category of any
  stored deal with the same ASIN; the rest go in ONE cheap call per run on
  `parse.model` (Haiku) with the titles only, returning one category each
  from `CATEGORIES` (`kind="category"` in `spend_by_kind`; anything invalid
  or a failed call = "other"). Up to 60 titles per call.
- Summary: `extract_by_source` shows Keepa at 0 calls; add
  `structured_parse: {"posts": N, "deals": M, "dropped_evidence": K}`.

## 3. Checks and tests
Offline (fake client/transport, no network):
- batch of 3 → 3 verdicts in one call; a missing index → that deal unjudged;
  a malformed reply → all unjudged; `reason` cut to 120 chars;
- reuse: same ASIN + condition + price within 2% reused (no call); other
  ASIN, other condition class, price 5% off, or an unrechecked low-confidence
  pass → judged;
- Keepa Amazon-price post and warehouse post → structured deals whose
  item/store/final/regular/conditions/canonical match the LLM path's for
  the same text, evidence verified, zero `extract` calls; a structured post
  whose quote is altered → dropped by `verify_evidence`;
- Woot: single prices → structured; a price range → LLM path;
- category reuse by ASIN, the single batched category call, invalid → "other";
- `spend_by_kind` has `category`; caps still stop the critic and the
  category call (`SpendCapReached`);
- tests/test_e2e_offline.py::test_rerun_is_idempotent stays green.
Add `parse.critic_batch_size: 8` to config.yaml with a one-line comment.
Update docs/HANDOFF.md (one short bullet under "In flight").

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note: what changed, the estimated cost per fast run using the
numbers above, and anything unsure.
