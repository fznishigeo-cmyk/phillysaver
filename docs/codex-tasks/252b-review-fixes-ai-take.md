# 252b — AI take (task 245): fix the 18 confirmed review findings (ships off)

## Why

Task 245 built the "AI take" (dealradar/publish/ai_take.py): the model writes a
short take; code must guarantee every number is right. An adversarial review
confirmed the findings below, two of them blockers: the gate accepts
misattributed numbers and verdict-contradicting text. Fix all of them. The gate
must be strict: a take that cannot be proven faithful is dropped, never shown.

## Decisions (engineer of record, Claude)

- **"AI" may appear in public copy** (operator 2026-10-16: "AI sells"); a parallel
  task (252a) removes `AI` from the banned list in tests/test_public_copy.py — do
  not touch that file; keep your how-it-works copy free of the other banned words
  (Keepa, Claude, Codex, LLM, Python, ...).
- **Walmart allowed** (operator call 2026-10-16); eBay never feeds a take. The
  tree already routes this through `amazon_history.hidden()` + `store_of()`.
- **Number binding, not number pools:** every number in the take must match a
  specific labeled fact (price ↔ price fields with `$`, percent ↔ percent fields,
  counts ↔ count fields with their noun, dates ↔ date fields); unlabeled or
  cross-labeled numbers fail. Prefer a small closed set of allowed sentence
  shapes over a growing blocklist if that is simpler to prove.
- **Direction:** the take's direction must match the shipped verdict; use an
  allow-list of lead phrases per verdict, not a blocklist of contradictions.
- Strip code fences and parse defensively; a malformed reply loses only the bad
  rows, never the batch; cap text length before any regex; reject bidi/format
  characters; never retry the same page/facts/prompt-version after a rejection
  (record the rejection in the cache) but do let other pages get their turn
  (fair rotation, not key order); prune cache/meta rows (keep current facts
  hash + last N per page; delete per-run rows older than 30 days).

## Rules

Only touch dealradar/publish/ai_take.py, its tests (tests/test_ai_take.py,
tests/fixtures/ai_take_*), the ai_take-related parts of
dealradar/parse/claude_client.py, dealradar/pipeline.py, dealradar/db.py,
templates/how-it-works.html and tests/test_e2e_offline.py's ai_take tests.
Math stays in savings.py; the model never computes. Spend caps must still win.
Run the files you touch plus tests/test_e2e_offline.py and tests/test_claude_client.py
with -n 1 and your own basetemp /tmp/task252b-*; delete it after. Do not run the
whole suite. Do not commit. Update docs/HANDOFF.md "In flight" with one entry.

## Confirmed findings — task 245 (AI take)

## [blocker] New how-it-works copy breaks an existing test, so the suite is red | dealradar/publish/templates/how-it-works.html:103
FIX: Operator decision. If the brief's AI label stands, exempt only the gated block. In tests/test_public_copy.py, change the import to `from dealradar.publish import ai_take, site` and line 28 to `assert INTERNAL.search(ai_take.template(path.read_text(), False)) is None, path.name`. I checked that every template passes with this change. Leave the published-pages test as is, since it runs with ai_take off. Then run the full suite with `-n 2` and record the exact pass/fail line in HANDOFF.

## [major] verify_take accepts text that contradicts the verdict, gives advice or predicts prices | dealradar/publish/ai_take.py:110
FIX: In verify_take, after finding the lead, set `body = text[len(lead):]` and return (False, 'empty') if `not re.search(r'[A-Za-z0-9$]', body)`. Extend ADVICE with grab|get it|act|ends?|ending|today only|sells? out|going (?:up|down)|ris(?:e|ing)|next (?:week|month|year)|years?|ever|all[- ]time|record. Add `cheap(?:er|est)?|bargain|(?:good|great|nice) deal|sav(?:e|ing|ings)|discount|less than|down from` to DIRECTIONS for 'Higher than usual' and 'About usual'. Add `more than|pricier|expensive|up from` for 'Lower than usual', and `get it|for \$` for 'No current offer'. Tie '$X now/current' to current_price through the role binding in the misattribution fix. Add every probe string to the rejected rows of test_verifier.

## [major] Rejected pages are re-requested every run, and pages later in key order never get a take | dealradar/publish/ai_take.py:198
FIX: Store failed attempts under the same cache_key, e.g. on rejection `db.meta_set(cache_key, json.dumps({'facts_hash': digest, 'prompt_version': _version(cfg), 'rejected': reason, 'attempted_at': iso(now)}))`. In the scan, treat a record with 'rejected' for the same digest and version as settled: no take, not pending. Sort pending by the page's last attempted_at with never-attempted first, then by key; use the per-page record from the meta-growth fix. Add a multi-run test: every page is eventually requested, and a rejected (page, facts) pair is never paid for twice.

## [minor] verify_take rejects faithful takes: the Best Buy store name and the shipped About-usual verdict | dealradar/publish/ai_take.py:56
FIX: Mask exact fact strings before the direction and advice regexes: `body = text[len(lead):]` and then `for fact in (facts.get('store'), facts.get('verdict', '').partition(' — ')[2]): body = body.replace(fact, ' ') if fact else body`. Run DIRECTIONS[direction].search(body) and ADVICE.search(body) on the masked body. Add both texts as accepted rows in test_verifier.

## [minor] The model reply is parsed with bare json.loads; a fenced reply loses the whole paid batch | dealradar/publish/ai_take.py:226
FIX: In prepare, replace `answers = json.loads(raw)` with `text = claude_client._strip_fences(raw)` followed by `try: answers = json.loads(text) except ValueError: lb = text.find('['); answers = [json.loads(o) for o in (_closed_objects(text, lb) if lb != -1 else [])]`. Guard each salvaged object with its own try. Add RecursionError to the except tuple. Add fenced-reply and truncated-reply tests asserting verified takes.

## [minor] Batch size ignores feature budget already spent today, so a smaller batch that would fit is never sent | dealradar/publish/ai_take.py:208
FIX: In the selection loop of ai_take.prepare, compare against the remaining amounts, not the caps: `limit = min(cfg.ai_take['max_usd_per_run'], cfg.ai_take['max_usd_per_day'] - kind_day_spend, spend_allowance(cfg, now, 0)[1] - db.spend_usd_for_day(now), parser.run_cap_usd - parser.spend_usd)`. kind_day_spend is the same SUM that reserve_llm_call uses for kind_day_cap; factor it into a DB helper and reuse it there. Add a test with a partly spent day that expects a smaller verified batch.

## [minor] With the flag on, a real second run sharing a run key publishes the first run's last_run | dealradar/publish/site.py:738
FIX: Delete the `if cfg.ai_take['enabled'] and (last_run or {}).get('run_key'):` block (site.py 738-745). prepare already gets `run_key=(last_run or {}).get('run_key', '')`. In test_ai_take_rerun_is_idempotent, compare the tree with deals.json normalized: load both, pop meta.last_run, then compare. Alternatively compare only /p/**, /check/data/** and how-it-works.html, as the existing rerun test does.

## [minor] AI take meta rows grow without limit | dealradar/publish/ai_take.py:247
FIX: Key the cache by page key only: `cache_key = 'ai_take:' + key`. Overwrite it on every verified or rejected outcome; the record already carries facts_hash and prompt_version, which the read checks. Remove ai_take_publish entirely (see the last_run finding). In DB.prune, add `self.conn.execute("DELETE FROM meta WHERE k GLOB 'ai_take_run:*' AND v < ?", (iso(now - timedelta(days=run_stats_days)),))`; v holds the claim's iso time. Add a test that runs many keys and checks the row count stays bounded.

## [blocker] verify_take accepts misattributed prices, percentages and counts; a false current price renders under the 'every number checked' label | dealradar/publish/ai_take.py:112
FIX: Bind each token to its role. For every $/%/count/date token in the take, find the fact keys whose formatted value matches it exactly. Require a role word from that fact within 4 words, and no role word belonging to a different fact. Role words: current_price → now|current|currently|today|at|for; usual_price → usual|typical|median; lowest → lowest|low; highest → highest|high|peak; the verdict % → below|above, and it must equal the verdict's percentage; average_deal_discount % → average; checks → checks; deals → deals. A cleaner equivalent: have the model emit {current_price}-style placeholders, reject any digit, $ or % outside placeholders, and substitute in code. Also ban unsupported superlatives: ever|all[- ]time|record|in (?:a )?years?. Add every probe above to the rejected rows of test_verifier.

## [minor] Unbounded ai_take meta rows in state.db: one cache row per facts change plus two rows per run, never pruned | dealradar/publish/ai_take.py:172
FIX: Same as 'AI take meta rows grow without limit'. Use a per-page-key cache row that is overwritten in place, remove the ai_take_publish block in site.py (738-745), and prune 'ai_take_run:*' rows by their iso value in DB.prune.

## [minor] A fenced or truncated JSON reply loses the whole paid batch, and the run claim blocks any retry | dealradar/publish/ai_take.py:226
FIX: Same as the fenced-reply fix: `_strip_fences`, then json.loads, falling back to `_closed_objects` salvage. Add RecursionError to the except tuple at ai_take.py line 231. Add fenced, truncated and nested-reply tests.

## [major] verify_take treats all bare numbers in the facts as one pool, so prices without a $ sign and mislabeled counts pass the gate | dealradar/publish/ai_take.py:112
FIX: In verify_take, reject any currency symbol other than ASCII `$` with `any(unicodedata.category(c) == 'Sc' and c != '$' for c in text)`, and reject `\b(?:USD|bucks?|dollars?|cents?)\b`. Replace the bare-number pool with allowed phrases built from the facts: `{facts['checks'] + ' checks', facts['deals'] + ' deals', '90 days', '90-day'}` plus the dates. A bare integer must start one of those phrases; this sits alongside the role binding in the misattribution fix. Add the probe strings as rejected rows.

## [major] No character limit plus a quadratic URL regex: a long verified take can stall the publish or crash it in checker.publish | dealradar/publish/ai_take.py:100
FIX: At the top of verify_take, after the empty check, add `if len(text) > 8 * max_words or any(len(w) > 30 for w in text.split()): return False, 'length'`, so no regex runs on long input. In render_site, wrap the optional `ai_take.prepare(...)` in `try/except Exception` that logs and falls back to a report with no takes. Add RecursionError to prepare's JSON except tuple. Add a test with an oversized single-word take.

## [major] Fenced JSON replies are not stripped, so the run's single paid batch is lost | dealradar/publish/ai_take.py:226
FIX: Same as the fenced-reply fix: parse `claude_client._strip_fences(raw)`, salvage with `_closed_objects` on ValueError, catch RecursionError, and add a fenced-reply test.

## [major] ai_take meta rows are never pruned and grow on every facts change and every run | dealradar/publish/ai_take.py:172
FIX: Same as 'AI take meta rows grow without limit': a per-page-key cache row overwritten in place, removal of the ai_take_publish block, and pruning of ai_take_run:* in DB.prune by claim timestamp. Then extend the idempotency test to check the row count stays bounded across many run keys.

## [minor] Free-text store/condition strings from third-party posts reach the prompt and widen the number whitelist | dealradar/publish/ai_take.py:78
FIX: In facts_for, send only canonical conditions: `[c for c in page.get('conditions') or [] if c in conditions._CANONICAL]`. In verify_take, build the allowed token pool only from code-formatted numeric facts (current_price, usual_price, lowest, highest, checks, deals, first_check, usual_window, average_deal_discount, plus the verdict's percentage as its own fact), never from store, condition or verdict text. Add the probe as a rejected row.

## [minor] Bidi and other invisible format characters pass the gate and can make a checked price display as a different number | dealradar/publish/ai_take.py:106
FIX: In verify_take, replace the markup check with `if re.search(r'[<>]', text) or any(unicodedata.category(c) in {'Cc', 'Cf', 'Co', 'Cs'} for c in text): return False, 'markup'` and add `import unicodedata`. Add a rejected row with U+202E and one with U+200B.

## [minor] The direction check is a short blocklist; common synonyms contradict the verdict and pass | dealradar/publish/ai_take.py:54
FIX: Same as the direction-contradiction fix. Add `cheap(?:er|est)?|bargain|(?:good|great|nice) deal|sav(?:e|ing|ings)|discount|less than|down from` to DIRECTIONS['Higher than usual'] and ['About usual'], and `more than|pricier|expensive|up from` to ['Lower than usual']. Do not ban plural 'deals', which the factual count uses. Add both probes as rejected rows.

REJECTED:
