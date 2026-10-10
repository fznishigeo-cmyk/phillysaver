# CLAUDE.md — dealradar

Working rules for any AI agent (or human) touching this repository.

## How to work

1. **Think before coding.** Read the code you are about to change first.
   State assumptions in one line; surface tradeoffs instead of silently
   picking one.
2. **Simplicity first.** Write the minimum code the request needs. No
   speculative abstractions, no features nobody asked for.
3. **Surgical changes.** Touch only what the request requires. Match the
   existing style, comment density, and naming. Keep diffs small.
4. **Goal-driven execution.** Define the verifiable success check first —
   here that is almost always a test. Iterate until it passes; never claim
   done without running it.

## Non-negotiables of this project

- **Politeness is law.** robots.txt is always honored (conservative on
  errors). Never bypass Cloudflare/Akamai/login walls, no headless
  browsers, at most 1 request/second/host, descriptive User-Agent with the
  contact email, exponential backoff, conditional GETs. This protects the
  business built on top of this pipeline.
- **Evidence or it doesn't exist.** Every published price must be backed by
  a verbatim quote verified in code (`parse/extractor.verify_evidence`).
  Never weaken that check; a deal without evidence is dropped, not guessed.
- **Store facts only.** Extracted facts, a product link, and credit to the
  source. Never persist or publish full post text or images.
- **Math is code, never the LLM.** All savings arithmetic lives in
  `dealradar/savings.py` per SAVINGS_RULES.md. The LLM only copies numbers
  as written.
- **Money has caps.** LLM spend is bounded per run and per UTC day in
  config.yaml, and accounting prices each call by the model actually used.
  Never remove or route around the caps.
- **Knobs live in config.yaml.** Publish floors (`min_pct`,
  `site_show_from_pct`), models, tiers — change the config, not scattered
  constants. Verification thresholds derive from `min_pct`; keep it that
  way.
- **state.db lives on the `data` branch only.** Never commit it to the
  default branch.
- **Idempotency is a contract.** Re-running the same run key must add no
  rows and resend no alerts. If a change could break that, test it
  (see tests/test_e2e_offline.py::test_rerun_is_idempotent).
- **Run the suite before every push:** `.venv/bin/pytest -q` (or
  `python -m pytest -q`), all green. The scheduled workflows run from this
  branch — a broken push goes live within 15 minutes.
