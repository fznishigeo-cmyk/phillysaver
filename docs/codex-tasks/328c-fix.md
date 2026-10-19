# Task 328c — full-suite failures for task 328 (read 328-tool-pages.md, 328b-fix.md; your work is in this tree)

My full suite (8,786 passed) has 8 failures, all from the new tool pages:
```
FAILED tests/test_established_look.py::test_no_published_page_has_unfinished_visible_copy[False-shipped] / [True-shipped]
  AssertionError: amazon-discount-finder/index.html
  TRUST_LINKS (About, How we verify, Contact, Privacy, Disclosure) <= footer links  -- tool pages only have About, Privacy, Disclosure
FAILED tests/test_search_polish_1019.py::test_emitted_polish_and_exits[shipped|inline|no-page|no-footer|no-header]
  tests/test_search_polish_1019.py:92: re.search(r'<div class="discounts-mobile">.*?</div>', html) is None  -- the test walks every page that carries the discounts click script; the tool pages carry it without the homepage's discounts-mobile markup
FAILED tests/test_home_links.py::test_home_links_in_real_chromium
```
Fix the PRODUCT where the site-wide rule applies (tool page footer must use the site's standard trust links, same as every other page — keep it clean), and make the search_polish/home_links tests understand the tool pages correctly (they are a different page type; keep every assertion for the pages it was written for — never weaken). For test_home_links run it locally (`.venv/bin/pytest -q tests/test_home_links.py -p no:cacheprovider --basetemp=/tmp/b328c`); if it needs Chromium and is skipped for you, reason from the test source about what the new pages/footers change (e.g. new links on the homepage? in preview mode there must be none). Then run the targeted set from the brief plus tests/test_established_look.py tests/test_search_polish_1019.py tests/test_home_links.py (basetemp /tmp/b328c, delete after). Reply <= 4 lines.
