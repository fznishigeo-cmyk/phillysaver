# Bounded numeric prefilter (task 224, proposed; OFF)

`parse.bounded_prefilter.enabled: false` preserves the legacy heuristic path.
Omission also means false. This switch changes heuristic admission only: model
cleaning, structured facts, extraction prompts, evidence/quotes, savings,
critic decisions and public serialization retain their existing owners.

The enabled scanner returns `known`, `unknown`, or `exhausted`. Only a known,
unambiguous below-floor pair can be skipped by the existing prefilter mode.
Unknown/exhausted leaves the post eligible and, after an empty first read,
eligible for the existing second opinion. That can increase calls/spend on
adversarial or unusual inputs. It grants no allowance: configured models,
source-yield routing (including the existing Haiku routing), opinion caps,
run/day reservations, pacing, critic reserve and fast-lane headroom still apply.
The supplied deadline is checked again after extraction before a second read.
With the existing prefilter in `on` mode, a previously skipped unknown post can
receive its ordinary first read as well as that capped second-opinion opportunity.

## Proposed bounds and offline rationale

| Setting | Default | Valid integer range | Scope |
|---|---:|---:|---|
| `max_chars` | 12,000 | 1–100,000 | Forward character positions visited per analysis |
| `max_numeric_chars` | 32 | 1–128 | Entire numeric run, before conversion |
| `max_numeric_tokens` | 128 | 1–4,096 | All numeric runs, including bare numbers |
| `max_price_pairs` | 256 | 1–65,536 | Actual same-sentence savings evaluations |

Booleans are strict; limits reject booleans, strings, fractions, nulls,
nonfinite values and unsupported keys, even while disabled. The character
limit is twice the shipped 6,000-character model-cleaning limit. The scanner
never changes that cleaning or supplies evidence from a truncated token.
The numeric limit permits ordinary decimal/thousands prices well beyond the
fixture values. The count/pair limits accommodate ordinary short posts while
refusing large comparisons: 23 prices require 253 pairs; the 257th evaluation
for 24 prices returns exhausted after exactly 256.

The ordinary table covers decimals, grouped thousands, percentages/percent-word,
free versus shipping/delivery/returns/trials, regular/final labels, coupons and
steps, whitespace/Unicode and sentences. Known decisions are compared against
the legacy functions and explicit expected decisions. A 400-post fixture at
the configured cleaning length uses one pair/post and visits exactly the sum
of the cleaned lengths. These are synthetic fixtures, not production coverage.

The existing roundup plus five recorded RSS entries also calibrate the limits:
six fixtures, maximum 350 forward positions, seven numeric runs and zero pair
evaluations before a conclusive signal or conservative unknown. Five return
unknown (attached units/models, dates, punctuation); one returns known. This
is deliberately conservative and does **not** establish broad enabled parity
or improved live spend. The normal table and repeated-price families provide
the independent pair-bound measurements; stopped legacy fixtures do not.

The scanner advances explicit indices once through the input. Numeric syntax
validation/conversion is bounded by `max_numeric_chars`. Word/label processing
uses bounded neighboring tokens and linear passes; prices are grouped by
sentence before enumeration, so different sentences do not generate quadratic
discarded pairs. Memory is O(character limit + numeric limit); analysis work
is O(character limit + pair limit), with bounded numeric conversions.
`visited_chars` counts forward advances, not every constant lookahead or the
bounded validation/string operations. Deadlines are cooperative checks between
characters, classification tokens and pairs; this is no hard real-time promise.
Canonical cleaning and the rest of parsing are outside this work bound.

## Conservative unknown cases

Any malformed/unsupported numeric token invalidates the whole analysis, even
when two valid below-floor prices preceded it. No valid prefix is kept from
malformed thousands, repeated decimal/comma fragments, scientific notation,
nonfinite money/percent spellings, signed/negative prices, ranges, attached
model IDs/units, Unicode digit spellings or currency-suffix forms. Non-dollar
currency symbols also return unknown. An ambiguous
trailing comma, spaced numeric fragments and money followed by a percent suffix
also return unknown. Ordinary ASCII numbers with Unicode surrounding words or
whitespace remain supported. A terminal sentence period is recognized, but
retains the legacy rule disallowing below-floor skips at a price's period.

Dollar amounts, ordinary grouped thousands/decimals, `%` and `percent`, free
word exclusions, regular/final labels and sentence boundaries retain known
ordinary classifications. The `reg.` abbreviation does not break a sentence.
Existing coupon/multi-step ambiguity still prevents a below-floor skip; the
scanner does not invent compound coupon savings or become an extractor.

The eight-call enabled capture has one extra existing second opinion for the
synthetic malformed `$1,00` post, compared with seven calls while disabled;
the added call follows existing cold-source Haiku routing. The fake response
is empty, so public bytes/decisions are unchanged in that fixture. This is an
eligibility and spend increase, not a cost-saving measurement.

## Diagnostics, baseline and remaining risks

Existing private `prefilter` diagnostics gain aggregate post, known, unknown,
exhausted, visited-character, numeric-token and price-pair counts only. Per-post
transient bookkeeping uses existing content hashes; aggregation deduplicates
those hashes, and one analysis is shared through the pipeline's candidate and
parse phases. No new table, post body, numeric string, response archive or
numeric observation is stored. Repeat keys use the existing pipeline/cache and
ledger rules; diagnostics do not accumulate a second receipt or observation.
Deferred posts' diagnostic entries never mark their source audit as parsed.

The compressed baseline was captured on this checkout at pre-edit HEAD
`ded9a30`, before any production edit, at the fixture's fixed
2026-10-15 20:15 UTC. It records exact request arguments, model-priced
reservations/receipts, parse/critic decisions, facts, member payloads and every
public byte: membership 44, open 50, support 51, shipped 51 (**196 files**).
The full shipped configuration and existing publication switches are retained;
only deterministic one-worker extraction and the fake client's structured
endpoint are selected. Tests compare omitted and explicit false against that
archive; existing byte pins are unchanged.

Supervised legacy digit-without-suffix observations (separate child processes,
two-second outer kill timeout each): 256/1,024/4,096/8,192 digits took about
0.0014/0.022/0.360/1.507 seconds locally; none timed out in that sample. Growth
is consistent with repeated suffix scans; no exponential exploit or production
hang is established. Tests gate deterministic operation counts, not timings.

`_strip_fences` whitespace/fence backtracking and `util.html_to_text`'s
HTMLParser-exception regex fallback remain separate follow-up risks. Evidence
literal searches, HTML parsing and other regexes are unchanged. No claim that
all parsing is bounded or that live latency/spend improved. Review and the
operator's full suite precede any separately authorized activation.

Final targeted validation: **371 passed**, including the new feature cases,
existing parser/yield cases, both offline pipeline rerun modes and injected
publication failure followed by repeat-key recovery and later-key resumption.
Deferred source-audit rows stay unparsed; recovery may complete the existing
first-publication anchors once. Calls, reservations, deal facts, sightings,
price observations and outgoing rows do not duplicate. Adjacent cap, pacing,
reservation, critic-tiering, schema, savings and offline pipeline checks also
passed. All pytest runs used `-n 1` and owned `/tmp/task224-*` basetemps, removed
after each run. The full suite was left to the operator; `test_workflows.py`
is unchanged. No live calls, activation, commit, push or deployment.
