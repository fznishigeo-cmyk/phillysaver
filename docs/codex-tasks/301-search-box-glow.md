# 301 — Circling rainbow search box + shining Analyze button + Amazon button at the top of Analyze results

C. Search box that captures attention (operator 2026-10-18 ~13:50Z, screenshot of
   the desktop header: "the search button at the top needs to be more enticing
   like circling rainbow color or something that captures the consumer"):
   - The main search box (#q's field wrapper) gets the same circling border
     technique as search_circle (conic-gradient border-box + @property --wpspin),
     rainbow stops (red, orange, yellow, green, teal/blue, violet, back to red),
     3 px, one slow turn every ~6 s. It keeps circling until the visitor focuses
     the box; while focused (and after a search is typed) it holds still at full
     colour; prefers-reduced-motion: static rainbow border, no animation.
   - The Analyze/Go button gets the golden shine sweep + twinkle already used on
     the Amazon buttons (search_more BOLD); reduced motion keeps it still.
   - Box size, padding and every coordinate stay identical (no layout shift; the
     mobile audit's headerH/firstCardTop must not change); dark mode looks good;
     text contrast unchanged; tiny CSS only (phone budget passes).
   - Behind its own config knob (search_box_glow) (missing = previous bytes); pins
     via a new pre_task_301 chained from pre_task_295. Screenshots 390/1440 light/dark in docs/research/search-box/.

D. Amazon button at the TOP on every search/Analyze result (operator 2026-10-18
   ~13:55Z, screenshot: Analyze result for "phone casess" showed "Your verified
   deal", "Related verified deals", and only at the very bottom "More from Amazon
   for "phone casess"" with the big "See all … on Amazon →" button). Put the big
   circling tagged Amazon button directly under the page's first heading on the
   Analyze result view and on search results with matches (above "Your verified
   deal" / the deal cards). Do not show two identical big buttons on one screen:
   the bottom related section keeps a small text link. (Codex 300 also touches
   the plain-search button position — keep your change in the Analyze/related
   render path and reuse 300's placement if it is already present.)

## Tests
Logic/CSS presence tests; recorded pins unchanged with the knob off; phone
budget passes; mobile audit metrics unchanged (headerH, firstCardTop); browser
screenshots (I run Chromium natively: tell me the command). Offline, -n 1
-p no:cacheprovider, own /tmp/task301-* basetemp (delete after), targeted tests
only. No commit/push. HANDOFF one entry. Read CLAUDE.md first.
