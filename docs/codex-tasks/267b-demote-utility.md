# 267b — Keep phone-model cases and utility parts out of the first screens (replaces 267)

## Why
Operator guardrail (2026-10-17): the homepage is for impulse buys — no
phone-model cases, replacement parts, toner, filters or hardware in the first
screens. Live 7c882fc already moves "what people buy" up front (273), keeps
open box off the homepage (274) and checks Add-to-Cart prices (275). Task 267
re-implemented ordering with its own budgets and kept colliding with those
(five review rounds), so it is replaced by this small change on the live code.
Live run evidence: appeal.niche_in_top30 = 2, examples "OtterBox Samsung
Galaxy S24 FE React Series Case", "iPhone 16 Pro Max Symmetry Series Case".

## Task (`publish.front_page.demote_words`, list, missing/empty = off; ship on)
Reuse the EXISTING niche mechanism (`front_page.niche_categories`,
`max_niche_top`, `niche_last` in rank.py / the browser's nicheLast): a deal
whose title contains any configured demote word (whole words/phrases,
case-insensitive) is treated exactly like a niche-category deal for ordering —
nothing else changes. No new budgets, no new ordering pass, no changes to gone
cards, spotlight, click price, allure spacing or the critic.
Ship list (config.yaml): phone case, iphone case, galaxy case, pixel case,
otterbox, spigen, screen protector, tempered glass, replacement, replacement
part, toner, ink cartridge, filter cartridge, adapter plate, mounting bracket,
fuse, gasket, o-ring, hose clamp. Keep it in config; never hardcode.
Server (rank.py pick order + static wall + JSON-LD/preconnects via the same
helper) and browser (the same flag published per deal, e.g. `niche: true`
only when on — OFF bytes identical) must agree.

## Tests
- A deal titled "iPhone 16 Pro Max Symmetry Series Case" ranks behind ordinary
  deals exactly like a niche category (server order and real JS order).
- max_niche_top still caps them in the top picks.
- Titles that merely contain "case" as another word ("Briefcase", "Pillowcase",
  "Case of 24 water bottles") are NOT demoted.
- OFF (missing/empty list) = today's bytes exactly (recorded pins, never re-pin);
  phone budget passes; idempotent rerun.
- Real-Chromium smoke (exit 77 skip only when Chromium cannot launch).

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task267b-*` basetemp
(delete after each run), targeted tests only. No network, no commit, no push.
HANDOFF "In flight": one entry. Ship ON.
