- **Should-fix — Analytics can leak the query.** With GA4 outbound Enhanced Measurement enabled, clicking the [query-bearing link](dealradar/publish/templates/index.html:1336) also sends its URL as `link_url`, bypassing the parameterless dispatcher. The live stream setting could not be verified offline. **Minimal fix:** exclude these links from automatic collection and test that path.

- **Should-fix — Privacy filter misses Unicode.** `例子.测试/private` and `order ١٢٣٤٥٦` both produce links. The [filter](dealradar/publish/templates/index.html:1328) recognizes only ASCII domain labels and digits. **Minimal fix:** reject Unicode hostname labels and decimal-digit runs; add regression cases.

- **Should-fix — Browser test fails when Chromium launches.** The [assertion](tests/search_amazon_browser.cjs:54) expects `["search_amazon"]`, but production `track()` passes `["search_amazon", undefined]`. Reproduced offline. **Minimal fix:** match the wrapper’s argument shape while preserving the no-query assertion.

- **Should-fix — Saved retains a stale search link.** Search a matching term, then open Saved: the input clears while the old Amazon link remains visible. [Saved clears only the existing search fields](dealradar/publish/templates/engagement.js:427), and rendering returns before updating the new element. **Minimal fix:** clear and hide it when entering Saved; test this transition.

Targeted checks: **345 passed, 2 Chromium skips**. OFF bytes, phone budgets, public copy, and reruns passed. No edits; temporary directories removed.

NOT