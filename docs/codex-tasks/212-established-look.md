# 212 — Established-look pass: make every page read like a confident, long-running site

## Why (operator 2026-10-16, firm)
"Stop making my website look amateur! It needs to look established!" Examples that upset the operator:
"Be first to get our deals email when it launches", "starting soon", random car parts on the front page.
The operator delegated wording: "you decide" — but every customer-visible wording change must be listed
(before → after) so it can be reviewed in the morning.

## Task (read CLAUDE.md, AGENTS.md, docs/HANDOFF.md first)
1. Audit every published page and template (`dealradar/publish/templates/*`, page builders in
   `dealradar/publish/*.py`: home, hubs, deal pages, best-deals-today, store/price pages, /p/, about,
   how-it-works, contact, privacy, disclosure, careers, hacks, join, subscribe, welcome, 404, emails) for
   anything that makes the site look new, unfinished, small or amateur:
   - words like soon, launch, beta, new site, coming, early access, we're just starting, test, TODO,
     placeholder, lorem, "n/a", empty sections, apologetic or hedging copy, exclamation clutter;
   - inconsistent capitalization/naming (wowpriced vs WowPriced), mixed date/time formats, mixed currency
     formats, duplicate or competing calls-to-action, badge clutter, jargon ("tier", "corroborated" where
     a shopper-friendly word exists — keep meaning identical), typos;
   - empty/zero states that read as "nothing here" (e.g. 0 deals, "No qualifying deals") — reword calmly;
   - footer completeness and trust: About, How we verify, Contact, Privacy, Disclosure links present and
     consistent on every page; copyright line with the current year computed in code.
2. Fix what you find with minimal, surgical edits. Do NOT change: prices, savings math, evidence rules,
   the Amazon disclaimer text, the FTC/affiliate disclosure meaning, the "price checked Xm ago" chip,
   legal meaning of privacy text (rephrase only for tone, never the commitments), sign-up mechanics.
   No new features, no layout redesign, no new scripts/fonts/images.
3. Write `docs/research/established-look-changes.md`: a table of EVERY customer-visible wording change
   (page, before, after, why) plus a short list of issues you saw but did not change (and why).
4. Tests: update copy assertions that intentionally change; add a test that no published page contains
   "coming soon|launch|beta|starting soon|be first|lorem|TODO" (case-insensitive, visible text only);
   re-pin published-byte hashes for intended changes only; idempotent rerun still passes.
5. Full suite: `.venv/bin/python -m pytest -q -n 2 -p no:cacheprovider --basetemp=/tmp/task212-full`.
   Do not commit. Short HANDOFF note.

## Constraints
CLAUDE.md non-negotiables; WCAG 2.2 AA; no LLM calls; own basetemp under /tmp/task212-*; never delete
other /tmp directories.
