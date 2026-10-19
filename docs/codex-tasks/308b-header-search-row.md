# Task 308b — header row breaks while a search is active (follow-up to your 308)

After 308, my Chromium run of tests/header_polish_browser.cjs passes the plain 900 px desktop checks but fails at `900-light-on-search: centred .brand` (line 64) after `#q` is filled with "kettle" and `#searchcount` shows 24. Measured geometry at 900 px during the search:
field {x:204, y:56, w:386, h:56, cy:84}; .brand {y:111.5, h:36, cy:129.5}; #drop {y:101.5, h:56, cy:129.5}; #pwa-header-install {cy:129.5}; #subscribe {cy:129.5}.
So while searching, the `.search` grid cell becomes ~146 px tall (something inside .search below the field now takes layout space) and every other row-1 item centres on the taller cell, ~45 px below the field. Your 308 removed `#search-spelling{position:absolute;background:var(--card)}` and `.hrow>.search{position:relative}` from header_polish CSS — likely the cause, or another in-flow helper line (spelling, search count, analyze hint) under the field.
Fix: while searching (with and without a spelling correction), the row-1 items must stay centred on the field within 2 px at every desktop width the test covers (900–2560), and the search_more_browser requirement that blurring the field must not move the undo link `[data-search-original]` must still hold. Run the 20 targeted pytest files again. Chromium is blocked for you; I'll run header_polish, search_more, search_box_glow and drop_shuffle browser tests. Do not touch anything else. No full suite, commit or push. Reply <= 5 lines.


RESTART NOTE: your previous run died when the disk filled up (No space left on device). Continue from your partial work already in this tree. Add `-o tmp_path_retention_policy=failed` to every pytest command and delete your /tmp/b308* basetemps when done.
