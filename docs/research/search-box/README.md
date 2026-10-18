# Search box glow and early Amazon button (task 301)

Native Chromium verification and screenshots are pending; Chromium was not
launched in the sandbox. No substitute screenshots were generated.

Run from the repository root on the native host (replace the executable path
if needed):

```sh
node tests/search_box_glow_browser.cjs /opt/pw-browsers/chromium docs/research/search-box
```

The script serves every page, image and API request from local fixtures or
mock responses. It builds OFF/ON publications for inline and lazy Analyze,
checks 390/1440 widths in light/dark themes with normal/reduced motion, and
writes PNGs plus `metrics.json` here. It compares the mobile audit's `headerH`
and `firstCardTop` selectors, exact field/input/button rectangles, and input
text color between OFF and ON. It also checks the six-second animation,
focus/typed pause, static reduced-motion rainbow, one top tagged Amazon button
before the cards, the small related-section link, local/ASIN/error Analyze,
click destinations, horizontal overflow and page errors. Its own
`/tmp/task301-browser-*` fixture directory is deleted in `finally`.

Targeted offline verification:

```sh
.venv/bin/python -m pytest -q tests/test_search_box_glow.py tests/test_search_more.py tests/test_search_analyze_lazy.py tests/test_search_circle.py tests/test_home_comment_strip.py::test_enabled_shipped_phone_budget_with_headroom -n 1 -p no:cacheprovider -o tmp_path_retention_policy=none --basetemp=/tmp/task301-native-tests
result_code=$?
python -c "import shutil; shutil.rmtree('/tmp/task301-native-tests', ignore_errors=True)"
exit "$result_code"
```

`search_box_glow` is strict boolean, defaults OFF when absent, and ships ON.
The input keeps its original border dimensions, padding and text color; a
positioned pseudo-element paints the three-pixel rainbow behind it. Analyze
uses the existing Amazon shine/twinkle timing, with an absolutely positioned
sparkle so the button's width stays unchanged. Six hashes captured before the
edit cover support/open/membership and shipped lazy/inline/more-OFF output.
`pre_task_295` calls `pre_task_301` to preserve older captures without re-pins.

Task 300's plain-search placement was absent in this worktree. Placement is
implemented in the Analyze and related-results publishing paths: related
results provide the top button even before all local matches are loaded, and
recognize Analyze's existing top button. Related pagination remains after all
local matches. Search terms and pasted URLs remain absent from link hrefs;
the existing click handlers construct the tagged destinations.

Offline shipped-phone check passed: home **73,739 gzip bytes**, below the
unchanged **77,360-byte** ceiling and its extra 1KB headroom requirement.
Native geometry and captures remain pending until the command above runs.
