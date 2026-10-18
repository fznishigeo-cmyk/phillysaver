# Brand-page preview checks — tasks 289 / 289F

`site.brand_pages` ships **false** for operator preview. The existing OFF
captures in `tests/fixtures/brand_pages_off.json` have not been changed.

The original task 289 native Chromium launch returned **77** in this environment:
`browserType.launch: Target page, context or browser has been closed`.
289F leaves the native run to the operator, as requested. **No new screenshots
were produced; the modern layout still needs native visual QA.**

`tests/test_brand_pages.py::test_real_chromium_screenshots` publishes the
existing offline fixture and runs `tests/brand_pages_browser.cjs`. When
Chromium can launch, it writes these paths here:

- `{about,join,contact,careers,hacks,how-it-works}-390x844-{light,dark}.png`
- `{about,join,contact,careers,hacks,how-it-works}-1440x900-{light,dark}.png`
- `join-prompt-{390x844,1440x900}-{light,dark}.png`

The harness intercepts every request. Fixture photo URLs receive an empty
image in the reserved box, with no invented product artwork or downloaded
images. Screenshot prices/titles are the synthetic fixture's actual published
fields, not live-production claims. Inspect a production-data preview before
enabling the feature. The harness also checks phone overflow, heading sizes,
console errors, popup timing, half-scroll, dismissal and reload/navigation memory.
It also checks the About one-liners, pill actions, 1100px content ceiling and
unchanged homepage signup wording.

The old line-62 failure was a clock/frame race: the correct daily key,
`wp.join.promptUntil`, was already being cleared, but the shared controller
queues its refresh with `requestAnimationFrame`. `clock.install()` also leaves
time running during screenshots. The harness now pauses the clock before loading
pages, uses `runFor()` to drain callbacks in order, checks that the prompt is
hidden at 24,999ms, and allows the refresh frame after the 25-second timeout.
Independent visitor cases clear both `wp.join.promptUntil` and `wp.join.joined`;
reload/navigation checks keep the keys, and an existing subscriber stays quiet.
Scroll checks wait for native scroll delivery before advancing both queued frames.
Offline emitted-script checks cover these queued refreshes and one-time reveal
motion, including reduced-motion and missing-observer fallbacks.

Run only the scoped check, with cleanup even on failure:

```sh
.venv/bin/python - <<'PY'
import shutil, subprocess, tempfile
base = tempfile.mkdtemp(prefix='/tmp/task289f-browser-')
try:
    result = subprocess.run([
        '.venv/bin/python', '-m', 'pytest', '-q', '-n', '1',
        '-p', 'no:cacheprovider', '--basetemp', base,
        'tests/test_brand_pages.py::test_real_chromium_screenshots'])
finally:
    shutil.rmtree(base)
raise SystemExit(result.returncode)
PY
```
