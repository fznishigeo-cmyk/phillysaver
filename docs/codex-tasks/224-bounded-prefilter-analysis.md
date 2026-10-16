# 224 — Bound numeric scanning and price-pair analysis before second opinions

## Why

`dealradar/parse/extractor.py:282` and `:291` search unanchored digit runs before
required suffixes; long near misses can revisit suffixes. `_can_clear_floor`
at `:327` enumerates all price pairs. Existing 6,000-character cleaning limits
input but does not prove bounded analysis time. QuickJS libregexp backtracks;
its explicit interrupt facility is the lesson, not a linear-regex replacement.
No production hang or exponential exploit has been established by task 220.

## Read first

Read CLAUDE.md, AGENTS.md, HANDOFF and the
[study](../research/genius-code-study.md), J3/N2/T3 and the CPU-risk inventory.
Read clean_for_llm, prefilter_post_counts, _smells_like_deal, _can_clear_floor,
_below_floor_post, second-opinion pacing/deadlines, evidence verification and
savings.py. Read existing prefilter/AI-cap/idempotency tests. Reference only:
`/home/user/bellard/quickjs/libregexp.c:2740` and `:2774`.

## Task

1. Add proposed `parse.bounded_prefilter.enabled: false` with validated finite
   analysis limits in config.yaml: numeric-token length/count and maximum price
   pair evaluations. Choose/document conservative bounds from offline fixtures.
   Omitted/false retains existing decisions, calls, spend and all site bytes.
2. In enabled heuristic analysis only, scan digit/money/percent runs once with
   explicit indices instead of suffix-seeking unbounded digit regex searches.
   Support the existing ordinary decimal/thousands/percent vocabulary and
   sentence/regular/final labels. Preserve normal classifications; document any
   deliberately conservative unknown cases. Do not accept numeric prefixes of
   malformed numbers, ranges or model IDs as complete price evidence.
3. Make analysis return explicit known/unknown-exhausted results. Enforce token
   and pair work limits, with a deadline check between bounded units. Oversized,
   malformed/nonfinite numbers, too many candidates or exhausted budget cannot
   classify a post as safely below the floor. Do not add a timeout around a
   blocking regex and call that a hard per-match timeout.
4. Unknown/exhausted means prefilter does not skip the post. For the existing
   second-opinion opportunity, treat uncertainty conservatively as eligible,
   still subject to current source-yield selection, call/day/run caps, spend
   reservations, pacing, critic reserve and fast-lane deadline/headroom. Do not
   create a new LLM path, retry, model, extra allowance or uncapped fallback.
   Any changed eligibility on adversarial input must be reported honestly.
5. Reuse existing savings.py computation for any price-pair floor arithmetic;
   leave evidence quotes/verification, canonical cleaning, structured parsing,
   actual extraction, critic decisions and published money math unchanged.
   Heuristic scanner output never supplies evidence or publishes a deal.
6. Add private aggregate exhausted/unknown/work counters to existing diagnostics
   only, repeat-key safe. No text, numeric strings, model responses or post bodies
   persisted/logged. No changes to _strip_fences, HTML parsing or other regexes
   in this brief; retain their separately documented follow-up risks.

## Tests and acceptance (later implementation session)

- Same-checkout disabled baseline: exact extraction/second-opinion/critic calls,
  reservations, decisions and all public files across four access modes.
  Keep current switches; do not repair unrelated fixtures by weakening gates.
- Table fixtures for ordinary valid and invalid inputs: comma/decimal prices,
  percent/percent-word, free versus shipping, reg/was/list labels, ranges,
  negative numbers, coupon/multi-step ambiguity, model numbers, whitespace,
  Unicode and sentence boundaries. Compare legacy and enabled known decisions;
  compare unknowns to a conservative specification, not implementation copies.
- Adversarial size families: digit runs without a suffix, repeated decimal/comma
  fragments, many small prices below a floor, long whitespace and many ordinary
  posts at configured length. Assert deterministic visited-character/token/pair
  bounds and unknown results at limits. Run potentially slow legacy comparisons
  in child processes with an outer kill timeout, never inline in the main suite.
  Record timeout/growth observations without labeling them exponential unless
  demonstrated. Wall-clock timings supplement operation counts only.
- Test unknown with exhausted daily/run budget, deadline, second-opinion cap,
  no configured model, source-yield deferral and structured posts. No broadened
  evidence acceptance, guessed price or extra budget. A dropped/unknown numeric
  token must not create an apparently unambiguous pair that skips a real deal.
- Replay same run key, failures and later resumes: no duplicate spend, rows,
  observations or sends. Run existing offline pipeline rerun cases, parser/cap
  checks and then `python -m pytest -q`. No live inputs, APIs or paid calls.

## Constraints and finish

All study gates and CLAUDE.md apply: polite robots/source permission,
1 request/s/host/UA/backoff and no bot/login bypass; evidence-verified facts only;
savings.py math, existing config LLM caps/reservations and Keepa reserves/clocks;
encrypted data-branch state and idempotency. No Amazon price history, alerts,
watches or price cache; preserve shared Amazon identity/hide/as-of/disclaimer.
No new regex engine, dependency, third-party script, tracker, service worker,
shopper feature or phone payload. Existing GPC/opt-outs remain.

Keep disabled. Update HANDOFF with classification parity, unknown cases, work
bounds and remaining regex risks in the later implementation session. No claim
that all parsing is bounded, or that live spend/latency improved. No activation,
network, commit, push or deployment is authorized by this brief.
