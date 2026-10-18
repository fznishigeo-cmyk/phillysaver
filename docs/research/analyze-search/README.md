# Analyze search captures

Native visual verification is **pending**. On 2026-10-18, both installed
Chromium executables failed at launch with `Operation not permitted` from the
sandbox (socket operations). No screenshots were generated or substituted.

`tests/search_analyze_browser.cjs` builds both inline and lazy fixtures and serves
every request from local files or mock responses. It checks paste/Analyze and
Enter, ASIN-only requests, the result card and related deals, tagged links,
reduced motion, console errors, horizontal overflow and button bounds at 390
and 1440 pixels in both system themes. Lazy checks hold the module response
across paste/input/repeated submit, require exactly one lookup, then fail the
module on a fresh page and verify plain search and the tagged Amazon fallback.
No module request is allowed before the first search interaction.

On a host that permits native Chromium, run from the repo root:

```sh
node tests/search_analyze_browser.cjs /opt/pw-browsers/chromium docs/research/analyze-search
node tests/search_more_browser.cjs /opt/pw-browsers/chromium docs/research/search-more
```

Replace the executable path if needed; screenshot directories are optional.
Analyze captures go under `inline/` and `lazy/`. Both commands remove their own
temporary browser fixtures in `finally`. Only native Chromium launch
failure returns 77; missing dependencies, fixture or assertion failures fail.
A skipped test does not verify the visuals. Task 295 runs the emitted Node
logic and both browser fixture harnesses in the sandbox, without launching Chromium.
