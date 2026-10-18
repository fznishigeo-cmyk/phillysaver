# Analyze search captures

Native visual verification is **pending**. On 2026-10-18, both installed
Chromium executables failed at launch with `Operation not permitted` from the
sandbox (socket operations). No screenshots were generated or substituted.

`tests/search_analyze_browser.cjs` serves every request from local fixtures or
mock responses. It checks paste/Analyze and Enter, ASIN-only requests, the result
card and related deals, tagged links, reduced motion, console errors, horizontal
overflow and button bounds at 390 and 1440 pixels in both system themes.

On a host that permits native Chromium, run from the repo root:

```sh
.venv/bin/python - <<'PY'
import subprocess, sys, tempfile
with tempfile.TemporaryDirectory(prefix='task291-browser-', dir='/tmp') as scratch:
    result = subprocess.run([
        sys.executable, '-m', 'pytest', '-q', '-n', '1', '-p', 'no:cacheprovider',
        '--basetemp', scratch,
        'tests/test_search_analyze.py::test_real_chromium_light_dark_phone_desktop',
    ])
raise SystemExit(result.returncode)
PY
```

Successful captures will be saved here as `390-light.png`, `390-dark.png`,
`1440-light.png`, and `1440-dark.png`. A skipped test does not verify the visuals.
