# Codex task 071 (roadmap #8): price-drop watches on saved deals, checked when the visitor returns

Source: docs/roadmap-beat-big-sites.md item 8 (task 063). Shipped on this base and owned elsewhere (keep working, do not restyle or move): 061 Today-only spotlight, 062 price chart on deal pages, 064 static wall fallback (`loadedOnce`, `#load-status`, per-deal `goodDeal`), 066 saved deals (heart in the photo corner from `cardHtml`, "♥ Saved (n)" first in `#controls` only once something is saved, `engagement.js` saved-deals block), 067 AI yield, 068 deal pages linked from cards (`deal_path`, `deal-actions.js`, tap-to-copy codes), 069 installable app (`pwa.py`, manifest, footer install link). Task 065 (phone filters + Back state: `#controls`, `filtered`, URL/sessionStorage state) runs in parallel: do not touch those areas. Phone is 70-80% of traffic: every layout claim must hold at 360px, and report the first card's top on a 390x844 phone before/after if the home page changes.

**Read first:** AGENTS.md, CLAUDE.md, docs/HANDOFF.md, roadmap item 8.
Files: `dealradar/publish/templates/engagement.js` (saved-deals block),
`dealradar/publish/templates/index.html` (`cardHtml`, Saved view),
`dealradar/publish/templates/privacy.html`, `dealradar/publish/site.py`,
`dealradar/publish/tracking.py`, `dealradar/growth/google.py`,
`dealradar/growth/report.py`, `config.yaml`, `dealradar/config.py`,
`tests/engagement_logic.cjs`, `tests/test_saved_deals.py`,
`tests/test_quality_trio.py`, `tests/test_tracking.py`,
`tests/test_growth_report.py`.

## Why
Customer emails stay OFF. A saved deal can still tell the visitor, the
next time they open wowpriced, that its price dropped. Honest label:
"Checked when you visit" — no notification promise.

## Do
1. **Remember the price seen at save time** in the existing versioned
   saved-deals record (bump the version; migrate v1 records with no
   price as "no baseline yet"). Store only the number already shown on
   the card (`final_price`) and the condition; still never URLs, codes,
   photos or post text. Optional target price per saved deal (a small
   44px "Watch price" control inside the Saved view only; 16px input).
2. **Match exactly.** On each successful public payload load, compare by
   exact deal identity (`key`) and the same condition; never a fuzzy
   title. Missing, unknown, locked or gone data never triggers. A drop is
   a strictly lower `final_price` than the baseline (or at/below the
   target). Any savings figure shown is computed in code from the two
   prices already published; no new percentage claims about "regular"
   prices.
3. **Show it quietly.** The "♥ Saved (n)" button gains a small count
   badge of dropped deals; the Saved view lists dropped ones first with
   "Price dropped since you saved: was $X, now $Y" (screen-reader text
   included). One toast per new drop per visit, never repeated for the
   same price. No modal, no sticky bar, nothing on first paint for
   visitors who never saved.
4. **Measure (GA4-only counts).** `watch_create`, `watch_match` (once per
   new observed price crossing), `watch_deal_click`; method labels only;
   never a key, a price or a target. Extend the private report's Product
   section; label as actions.
5. **Privacy page:** one sentence that saved prices and targets stay in
   this browser.

## Tests
v1→v2 migration; save records price; drop / same / higher / gone /
locked / missing / condition change; target crossing once; no repeat
toast on reload at the same price; badge count; cross-tab updates; quota
denial keeps the tab-only list; events once with allowed params only;
feature off removes everything (byte-identical output when
`engagement.saved_deals` is false).

Run `.venv/bin/python -m pytest -q`; all green except tests needing local
sockets your sandbox blocks (name them). One short HANDOFF bullet. Do NOT
commit or push. End with what changed and anything unsure.
