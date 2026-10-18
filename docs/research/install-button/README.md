Task 290 browser verification (2026-10-18)

The operator's Chromium run outside this sandbox reported empty manifest errors
and only `in-incognito` for installability (Playwright contexts are incognito).
The harness ignores exactly that error ID. The four home-page screenshot and
layout checks passed, then the iPhone check caught Tab leaving the open dialog.

The sheet still uses `showModal()`, with explicit Tab/Shift+Tab containment for
its only control, the close button. The updated browser harness checks both
directions repeatedly, Escape/backdrop/X closing, an inside click staying open,
and focus returning to both header and footer openers. A successful full native
rerun, the two sheet screenshots and `chromium.json` remain pending.

The offline harness serves an actual local publication on loopback, intercepts
every external page request, and substitutes neutral placeholders for remote
product photos. It checks `Page.getAppManifest` and `Page.getInstallabilityErrors`,
the synthetic install prompt, dismissal across reload, the Safari instruction
dialog and keyboard focus, search-box stability, button height, horizontal
overflow and console errors.

Run with an existing local publication and a working Chromium installation:

```sh
node tests/install_button_browser.cjs /path/to/local/site docs/research/install-button
```

The targeted pytest entry is
`tests/test_install_button.py::test_real_chromium_installability_layout_and_screenshots`.
Use `-q -n 1 -p no:cacheprovider` and an owned `/tmp/task290f-*` basetemp, removed
in `finally` immediately after the run.

Output paths (the four home-page PNGs exist from the operator's run; the remaining
three files await a successful rerun):

- `docs/research/install-button/home-390x844-light.png`
- `docs/research/install-button/home-390x844-dark.png`
- `docs/research/install-button/home-1440x900-light.png`
- `docs/research/install-button/home-1440x900-dark.png`
- `docs/research/install-button/ios-sheet-390x844-light.png`
- `docs/research/install-button/ios-sheet-390x844-dark.png`
- `docs/research/install-button/chromium.json`

The existing manifest has 192px and 512px PNG icons, a separate maskable icon,
`display: standalone`, and `/` for its ID, scope and start URL. No service worker
or caching change was needed for the reported browser findings.
