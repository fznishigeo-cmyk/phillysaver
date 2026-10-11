# Codex task 009: show where the LLM money goes, per run

**Read first:** AGENTS.md, CLAUDE.md ("Money has caps", "Math is code").

Daily LLM spend must fall to $6 by 2026-10-14 (config caps already enforce
it). To decide what to cut we need to know which calls cost what. Today
`dealradar/parse/claude_client.py` adds every call's cost to one
`spend_usd` total (around line 210), and the run summary shows only that.

1. Keep `spend_usd` exactly as it is (the caps read it). Alongside it, keep
   `spend_by_kind`: cost and call count per kind of call. Kinds, at least:
   `extract` (post parsing), `second_opinion`, `critic`, `newsletter_pick`,
   `other`. Find every call site; tag each call with its kind at the call
   site, not by guessing from the prompt.
2. Put `spend_by_kind` in the run summary dict (pipeline.run) and as one
   small table in the GitHub Actions step summary the run already writes
   (aggregates only: kind, calls, USD; never post text, items, or prices).
3. Also count, per source, how many posts were sent to `extract` and their
   cost (`extract_by_source`: name -> calls, USD) in the summary dict and the
   step summary, so we can see which sources are expensive.
4. Tests: kinds sum to `spend_usd`; caps unchanged; step summary carries no
   post text.

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push.
