# Task 304 — search on Black Friday / Cyber Monday pages, and a flashier Support page (operator 2026-10-18)

## Operator asks (quotes, with screenshots)
1. "no search button appears on blackfriday url and cyber monday url" — the event pages (/black-friday/, /cyber-monday/; dealradar/publish/event_pages.py + seo.py) have a plain header with tabs and no search.
2. Support page ("Support our work", dealradar/publish/templates/subscribe.html support variant, "Support once / Support monthly", "Join free: the best deals in your inbox"): "make this page more flashy. like the others you did" — i.e. the bold hero + visuals style of the brand pages (dealradar/publish/brand_pages.py: About/Join/Contact/Careers/Hacks; reuse its CSS/visual language, do not invent a new one).

## Requirements
1. Event pages get the same search experience entry point as the homepage header: the rainbow search field (task 301 `search_box_glow` look) with the purple "Analyze" button (card_tidy look; phones say "Analyze"). Submitting (Enter or Analyze) goes to the homepage with the query, e.g. `/?q=<query>` (or `/?analyze=<query>` for the Analyze button), and the homepage, on load, fills the search box and runs the same search/Analyze as if typed (only when the param is present; the param is then removed from the address with history.replaceState; values escaped; max 200 chars). No new API calls from event pages themselves. Keyboard and screen-reader accessible; 44 px tap targets; phone 390/360 widths without sideways scroll.
2. Support page: hero in the brand-page style (headline, short lede, a visual like the other brand pages), the two Stripe buttons kept exactly (same links, same text "Support once" / "Support monthly", same disclosure text — legal wording must not change), the "Join free" block restyled to match (button text may become "Join free · price alerts" only if it fits the existing pattern). Dark mode supported like the other brand pages. Do not change Stripe URLs, prices, or the "not tax-deductible ... every deal stays free" sentence.
3. Behind one new strict-bool config flag `page_polish` (AppConfig default False = previous bytes; config.yaml true). Chain a `pre_page_polish` helper from `pre_task_295` in tests/history_pins.py (like pre_card_tidy); never re-pin recorded hashes.

## Tests
- OFF/missing bytes test; strict config test.
- Event page HTML contains the search form + Analyze button when on; homepage ?q= / ?analyze= handling in a node logic test (escaping, replaceState, no request when param absent).
- Support page keeps the exact Stripe hrefs and legal sentences (assert verbatim).
- Run only the targeted files you add/touch: `.venv/bin/pytest -q -p no:cacheprovider --basetemp=/tmp/b304 <files>`. Chromium is unavailable to you; name the browser test I should run (add a small tests/page_polish_browser.cjs modeled on tests/search_box_glow_browser.cjs: event page search at 390/1440 light/dark navigates to the homepage and shows results; support page screenshots).
- No full suite, no commit, no push. Update docs/HANDOFF.md. Reply <= 8 lines.
