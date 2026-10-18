# Related Amazon pages, spelling, List Price and empty searches

Task 297 keeps the existing Amazon button and shows `See every <query> price
on Amazon` until current-price cards actually render. Empty responses, budget
limits and request failures leave no live-price heading or empty grid; later
cards restore the heading. Native checks now include a `budget_exhausted`
no-match search, the unchanged button/affiliate click, and a later-page budget
cap that keeps earlier cards and the final button without grid error text.
These checks run in every inline/lazy, phone/desktop, light/dark combination;
native execution remains pending. Optional `*-empty.png` captures show the
fallback. The Node harness exercises all empty reasons, retries and cached cards.

The operator's first Chromium run timed out waiting for 20 initial cards. The
harness published only one: its 24 Amazon fixtures inherited October 9 timestamps
without price confirmations, 60 hours before the October 12 publish clock. The
48-hour freshness guard correctly hid them. Fixtures now include confirmations
at publish time; a pre-launch assertion requires all 24 Amazon mice in deals.json.
The offline regression verifies all 25 published fixtures and timeout diagnostics.

The next external run passed paging and List Price, then failed the spelling
undo click. Follow-up 293G keeps the search hint's space when blur hides it, so
the undo link cannot move between pointer down and click. Undo restores the
exact typed text and suppresses correction until that text changes. Only known
successful empty answers can correct again from cache after an edit; failures
stay failures. Pending corrected requests are cancelled by undo.

Native Chromium verification of 293G is pending the operator's external rerun;
the sandbox still rejects Chromium launch. The original undo assertion remains,
with additional blur-position, settled-query, edit and keyboard-undo checks.
Task 295 also runs every native scenario with Analyze inline and lazy. Its module
response is held until related search has mounted, exercising both module load
orders and the shared cancellation hook; the initial page cannot request Analyze.
The offline emitted-code harness also follows the remainder of the native
sequence in one module: a second spelling correction, List Price, ASIN Analyze
and rate-limit stopping. The browser script
builds the current configuration offline, intercepts every request, and checks
390/1440 light/dark views: no request before reaching local results' end, pages
0–2, ASIN deduplication, adult exclusions, current-price labels, the tagged final
button, reduced motion, spelling/undo, one corrected request, quiet rate-limit
stopping, analytics privacy, console errors and horizontal overflow. List Price
strikes and percentage badges are checked in ordinary Amazon results, Analyze's
live-price card and related pages. Browser waits report the named condition,
viewport/theme, query, card counts, page text, API calls and captured errors on
timeout. Page-count waits allow already-loaded later pages without missing a
transient exact count; final counts and page requests remain exact assertions.
The local search module (including the shared Amazon card renderer and shopping
vocabulary) loads on the first search; the home page stays within its existing
byte limits. Amazon page requests still wait for the results-end intersection.

From the repo root, on a host that permits Chromium:

```sh
node tests/search_more_browser.cjs /opt/pw-browsers/chromium docs/research/search-more
```

The executable path is the first argument; the optional second argument saves
page/spelling/Analyze/empty screenshots, prefixed `inline-` or `lazy-`. The script removes its own `/tmp/task297-search-more-browser-*`
directory in `finally`, including failed runs. Screenshots are retained only in
the requested destination. It never contacts a live service.
