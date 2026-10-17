# 278 — Strip comments from the published home page (phone-budget headroom)

## Why
2026-10-17 ~06:40Z: with everything shipped (dark polish, search box) plus the
top-card rotation (269), the home page is 77,724 B gzip against the 77,360 B
phone budget (tests/test_drop_rotation.py::test_enabled_shipped_phone_budget).
More D-Day features (photos everywhere 274, rows 271, what people buy 273)
add bytes too. The template carries many developer comments (CSS `/* */`,
JS `//` lines, HTML `<!-- -->` notes) that visitors download on every visit.
Never raise a phone-budget limit; win the bytes back instead.

## Task (`site.strip_comments`, validated bool, missing = off; ship **on**)
At the very end of publishing `index.html` (after every template pass and
injection, so no marker another pass needs is touched), remove:
1. CSS comments inside `<style>` blocks (`/* … */`), never inside strings/urls;
2. whole-line JS comments inside inline `<script>` blocks (lines whose first
   non-space characters are `//`), never trailing comments, never inside
   template literals/strings/regex; block comments `/* … */` that start a line
   and end on a line by themselves;
3. HTML comments outside `<script>`/`<style>` except conditional or
   structural markers any later consumer reads (audit: grep the codebase for
   every `<!--name-->` marker a downstream step or test parses; keep those).
Collapse runs of blank lines to one. Bytes change only when ON; OFF = today's
bytes exactly. Fold the switch into `site_version`.

## Tests
- A parser-level test: the stripped page's inline JS still parses (node
  `new Function` / vm.Script per script block) and CSS has balanced braces.
- Real-Chromium smoke (390×844 + 1280×800, light + dark): home loads, search,
  tabs, Load more, Get deal hrefs, no console errors — same as ON vs OFF
  (exit 77 skip only when Chromium truly cannot launch; use
  executablePath /opt/pw-browsers/chromium).
- Shipped-config phone budget passes with headroom; print OFF→ON gzip bytes.
- OFF bytes identical (recorded pins set it off, "recorded before task 278";
  never re-pin); idempotent rerun.

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task278-*` basetemp
(delete after each run), targeted tests only, never the full suite. No
network, no commit, no push. HANDOFF "In flight": one entry. Ship ON.
