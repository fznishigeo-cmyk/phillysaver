# Task 327 — merged tree 324 + 326 is 125 gzip bytes over the homepage phone headroom

Read CLAUDE.md first. This tree = live 0b22b69 + task 324 (phone_tagline_count, docs/codex-tasks/324-phone-tagline-count.md) + task 326 (discounts_from_link, docs/codex-tasks/326-all-discounts-link.md), already merged (tests/history_pins.py chain: pre_task_320 → pre_discounts_from_link → pre_phone_tagline_count). Each passed alone; together:

```
tests/test_home_comment_strip.py::test_enabled_shipped_phone_budget_with_headroom
assert report["metrics"]["home_html"]["gzip"] < cfg.phone_budget["home_html_gzip_max_bytes"] - 1000
E  assert 76485 < (77360 - 1000)
```

## Do
Reclaim at least ~250 gzip bytes of the shipped (all flags as in config.yaml) homepage HTML WITHOUT changing any behaviour, visible text or OFF bytes, and without raising the budget or weakening the test. Prefer removing code that the shipped flags make dead or immediately overwritten (example: renderMeta() sets `q.placeholder = live ? "Search …" : …` and then immediately overwrites it with "Search deals or any Amazon product" under search-amazon — only remove such things behind a flag-gated publish/template step so OFF stays byte-identical), or tightening 326's inline discounts-from-link.js (same behaviour; its node logic test must still pass), or moving rules into an existing external asset if they are not needed for first paint. Do not change the 324/326 behaviour.

## Tests
Run `.venv/bin/pytest -q -n 4 -p no:cacheprovider -o tmp_path_retention_policy=failed --basetemp=/tmp/b327 tests/test_home_comment_strip.py tests/test_phone_tagline_count.py tests/test_discounts_from_link.py tests/test_header_polish.py tests/test_all_discounts_button.py tests/test_search_polish_1019.py tests/test_search_box_glow.py tests/test_card_tidy.py tests/test_brand_pages.py tests/test_drop_fresh.py` plus node logic tests you touch; delete /tmp/b327. Do NOT run the full suite. No commit or push. Reply <= 5 lines with the new gzip number.
