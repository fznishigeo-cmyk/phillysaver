- **should-fix — Historical stock still becomes current scarcity.** Source text `Yesterday: Socks: $25; only 2 left. Restocked to 25 today.` passes when the evidence quote omits the surrounding sentences, publishing “OPEN BOX · only 2 left.” [open_box.py:35](/tmp/claude-0/-home-user-phillysaver/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/wt273/dealradar/publish/open_box.py:35). **Minimal fix:** validate the complete source context before retaining a count; add regressions for qualifiers outside the selected quote.

- **should-fix — Gone allure cards bypass spacing.** Reproduced in published JavaScript: live/gone allure cards occupy slots **7/9** in Trending and **11/14** in All deals, violating the one-per-six requirement. Both implementations bypass dead cards. [appeal.py:183](/tmp/claude-0/-home-user-phillysaver/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/wt273/dealradar/appeal.py:183), [appeal.py:224](/tmp/claude-0/-home-user-phillysaver/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/wt273/dealradar/appeal.py:224). **Minimal fix:** enforce both spacing constraints on the final visible sequence, including gone allure cards.

592 targeted tests passed; one Chromium layout check skipped. OFF-byte pins and rerun checks passed. No edits, commits, or pushes; review scratch deleted.

NOT
--- Operator decision for this round (2026-10-17 ~08:45Z, ad starts 13:00Z) ---
Take the simplest safe fix for the stock finding: the open-box label never publishes a number for now — always "OPEN BOX · often only 1" (keep the count code path behind a config flag `site.open_box_counts`, default off). Fix the gone-allure spacing on the final visible sequence. Keep the change minimal.
