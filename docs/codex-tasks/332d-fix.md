# Task 332d — last native Chromium failure (I ran it)

Now PASS: page_polish, search_more, search_analyze, header_polish, and search_box_glow's geometry/phone label (332c fix works).
FAIL search_box_glow_browser.cjs line 325: after typing "phone holder", waiting 750 ms, clicking #analyze-search, the opened URL is exactly right, but `calls.length` grows by 1 within 200 ms ("typed button adds no API call", 1 !== 0). Log: docs/codex-tasks/332b/d332_search_box_glow.log.
Find out which request it is (URL/path of the extra call) and why: (a) the click also starts the old Analyze flow / lazy Analyze loader / prefetch (REAL bug: typed-word AI Analysis must make no API/Keepa call), or (b) a typing-as-you-search request whose debounce exceeds 750 ms or is triggered by blur/focus change on click (then it is not caused by the button: make the test wait for the search request to settle deterministically instead of a fixed 750 ms, and assert the click itself adds none). Fix the product for (a); fix the test for (b). State which in the reply with the request path.
Targeted pytest (`--basetemp=/tmp/b332w`, delete after) + `node --check`. No full suite, commit or push. Reply <= 4 lines.
