# 270 — real Chromium results (operator, outside the Codex sandbox, 2026-10-17 ~05:45Z) — include as findings
tests/test_visitor_watch.py: 3 failed, 7 passed.
1. BLOCKER test_chromium_search_navigation_and_delayed_white_image: `page.screenshot: Timeout 30000ms exceeded` at tests/visitor_watch_browser.cjs:75 (a screenshot waits on fonts/animations or a never-settling page; use animations:'disabled', caret:'hide', a bounded timeout, and never let one screenshot stall the 6-minute job).
2. BLOCKER health check on the local clean build reports `text` FLAG (expected none) — the new text check false-positives on a healthy fixture (probably matches the browser script source, hidden elements, or "price history" in non-visible text). Restrict to visible text of rendered cards/pages; add a fixture proving a clean site gives `health: ok`.
3. Same test with the search case: flags {search, text}, expected {search}: same text false positive.
