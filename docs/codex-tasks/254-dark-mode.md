# 254 — Dark mode that looks as good as light mode on phones (dark-only CSS, one switch)

## Why

Operator 2026-10-16, relaying the marketing contact: his phone uses a black (dark)
default and the site "was looking really bad" on it. On a white screen it looks good.

QA 2026-10-16: two independent Chromium passes at 390×844 in light, dark and forced
dark. The repo was not edited.

- Every public page already has a dark theme and declares `:root{color-scheme: light dark}`
  in CSS. When the phone reports dark (iPhone Safari, Chrome Android dark theme, or
  auto-dark with a dark preference), the browser uses **our** dark theme and does not
  invert it. Forced-dark and dark screenshots were pixel-identical in 33 of 37 pairs.
  The other 4 differed only in the home drop clock and countdown animation.
- What looks bad is leftover white or bright pieces inside that dark theme, plus a few
  dark-only bugs:
  1. Home `#amazon-strip` ("Top Amazon deals right now") has hard-coded `#fff`, `#1a1d21`
     and `#0e7569` (index.html:171-176). It is a full-width white band and the brightest
     thing on the first screen.
  2. The home card photo area has `.media{background:#fff}` (index.html:284). That gives
     about 24 pure-white 150px blocks per scroll, and pale products disappear into them.
  3. The heart button rule `.save-deal circle{fill:var(--card)}` (index.html:647) turns
     it into a near-black disc on top of the white photo.
  4. Store tiles `.ph` keep their pastel 62% lightness (inline `background:hsl(h,42%,62%)`
     from seo.py:188/197 and index.html:1660). Best-deals-today, /deals/*, /stores/*,
     "Related live deals" and photo-less home cards read as stacks of mint and pink slabs.
  5. The /p/ price page photo `.product-detail img` (product_pages.py:21) is a raw 320px
     white square on #0d1117.
  6. The "Continue with email" button `.joinchoice` (join-card.css:9-11) is a solid white
     pill inside the dark join card, both on /join and in the home prompt.
  7. In the home join pop-up, the disabled primary button's label is 1.21:1.
     `.pwbtn{color:var(--accent-fg)!important}` (index.html:361) beats join-card.css:15-16,
     and index.html:366 then fades the button to opacity .6.
  8. The join pop-up and filter-sheet backdrops (`rgba(10,18,24,.16)` and
     `rgba(0,0,0,.45)`) and the `.12` shadows disappear on #0d1117, so the sheets don't
     separate from the page.
  9. 404.html never defines `--accent-fg`, so its sign-up arrow is #e7eaee on #2fb3a4
     (2.15:1).
  10. Field borders use `var(--line)` (#2a313a on #161c23, 1.31:1), so the footer email
      input and the home filters nearly disappear. WCAG 1.4.11 asks for 3:1.
  11. No page has `<meta name="color-scheme">`. Chromium is fine with the CSS form alone.
      The meta is still worth adding: browsers read it before any CSS (no white flash,
      form controls, scrollbars), and some other browsers' auto-dark logic looks for it.
- Charts (/p/ and deal pages), body text, focus rings and the /check/ page passed the
  dark contrast audit.
- **Out of scope because no page change can fix it:** browsers or in-app webviews that
  darken the page while telling it the user prefers light. Chromium then inverts our
  light theme no matter what the meta or CSS says (`only light` was tested too). The
  teal and yellow fills stay, while the dark text on them flips to light: wordmark and
  tagline drop from 7.1:1 to 1.3:1, and "Join free", "40-90% OFF" and "LIVE DEALS" are
  hit the same way. Photos become hard white squares. Only a brand change to the light
  theme would help. The operator will ask the contact for his phone, browser or app, a
  screenshot, and whether Smart Invert or color inversion is on. Do not redesign the
  light theme here.

**Design choice.** All fixes live in **one dark-only stylesheet** that also holds the
shared dark token set. One global pass injects it, together with the meta, into every
published page, behind `site.dark_polish`.
- OFF publishes byte-identical files.
- ON publishes the OFF files plus exactly that block. Every rule sits inside
  `@media (prefers-color-scheme:dark)`, so light mode cannot change.
- No template, JS, seo.py or STYLE string is edited.

Cost: about 1.9 KB raw per page. On the home page that is +535 B of gzipped CSS and
+589 B of gzipped HTML, measured on the task-206 fixture. The alternative, editing about
15 templates and re-pinning about 20 byte-pin test files, was rejected.

## Read first

CLAUDE.md, AGENTS.md, docs/HANDOFF.md, docs/codex-tasks/237-exact-back-then-skip-offscreen-cards.md
(the config-switch, "adds only CSS" and test pattern to copy),
`dealradar/publish/_pwa_web.py` (`publish`: the global `*.html` pass to copy),
`dealradar/publish/site.py` (`_page_template` 102-121, version mix 851-852, passes
1061-1081), `dealradar/config.py` 308 and 863-865, config.yaml `site:` (143-148),
`dealradar/publish/templates/index.html` (tokens 22-41, header 52-63, 171-176, 181-205,
284-301, 361-366, 635-650, 1660), `hub.html` 13-29 and 75-78, `join-card.css`,
`footer-signup.css`, `404.html` 10-21, `dealradar/publish/product_pages.py` (STYLE 20-30,
READER_STYLE 35-56), `price_history.py` STYLE, `seo.py` 188-198, `templates/support.js:113`,
tests/test_offscreen_cards.py, tests/test_pwa.py (`baseline_config`, `tree_hash`),
tests/phone_budget_fixture.py, scripts/publication_budget.py, tests/pwa_layout.cjs, and the
`node()` helper in tests/test_checker_page.py.
QA screenshots, if they exist on your machine:
`/tmp/claude-0/-home-user/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/darkqa/`
(`emu/`, `mont/`, `shots/`).

## Task

1. **`dealradar/publish/templates/dark.css`** (new), exactly this. The values are
   measured; if you change one, give the reason in HANDOFF.

   ```css
   @media (prefers-color-scheme:dark){
   :root{--bg:#0d1117;--card:#161c23;--fg:#e7eaee;--muted:#a4adb7;--line:#2a313a;--chip:#20262e;
   --accent:#2fb3a4;--accent-fg:#07211d;--link:#6cb1ff;--cond:#f2b544;--cond-fg:#3a2503;
   --good:#3fce7c;--hist:#79a8ff;--unv:#98a2b0;--dead:#ff7a6b;--dead-fg:#2b0e02;
   --tile-fg:#eef1f4;--photo-tile:#d9dee4;--ph-dim:rgba(13,17,23,.6);--card-raised:#1c232c;
   --line-strong:#3a434e;--field-line:#66717e;--scrim:rgba(0,0,0,.6)}
   :root .amazon-strip{background:var(--card);color:var(--fg);border-bottom-color:var(--line)}
   :root .amazon-inner a{color:var(--accent)}
   :root .amazon-inner a:focus-visible{outline-color:var(--accent)}
   :root .media{background:var(--photo-tile)}
   :root .media img{mix-blend-mode:multiply}
   :root .card.gone .media img{mix-blend-mode:normal}
   :root .media .save-deal circle{fill:#f1f3f5}
   :root .media .save-deal path{stroke:#0e7569}
   :root .media .save-deal[aria-pressed="true"] path{fill:#0e7569}
   :root .ph{box-shadow:inset 0 0 0 100vmax var(--ph-dim)}
   :root .product-detail img{background:#fff;border-radius:12px;filter:brightness(.85)}
   :root .joinchoice{background:transparent;color:var(--fg);border-color:var(--line-strong)}
   :root .joinchoice:hover{border-color:var(--accent)}
   :root .joincard .pwbtn:disabled{color:var(--muted)!important;opacity:1}
   :root .joinpopup{background:var(--scrim)}
   :root .joinoverlay .joincard{background:var(--card-raised)}
   :root .joinoverlay>[role="dialog"]{box-shadow:0 0 0 1px var(--line-strong),0 16px 48px rgba(0,0,0,.6)}
   :root #filtersheet{border-color:var(--line-strong)}
   :root #filtersheet::backdrop{background:rgba(0,0,0,.6)}
   :root .footeremail input,:root #controls input,:root #controls select,
   :root #filterform label input[type=number],:root .joincard input{border-color:var(--field-line)}
   }
   @media (prefers-color-scheme:dark) and (max-width:640px){:root .joinoverlay{background:var(--scrim)}}
   ```

   Why the rules look like this (keep these properties):
   - **`:root ` prefix.** It adds one class of specificity, so each rule wins over the
     same selector wherever the page's own CSS sits. `!important` appears only where the
     page itself uses it (index.html:361).
   - **One token set.**
     - The block holds the union of the dark tokens the templates already define (all
       identical today; test 5 pins that) plus 7 new ones.
     - `--shadow`, `--sweep`, `--accent-hover`, `--bad` and `--warn` stay page-owned.
     - A page that lacks a token gains it in dark mode only. On 404 that adds
       `--accent-fg`, which is the arrow fix. On /join it adds `--link`, so the footer
       links turn #6cb1ff in dark. Check that in the screenshots.
   - **Photos.**
     - The `#d9dee4` tile with `mix-blend-mode:multiply` turns the photo's white
       background into the tile. Product colors stay true (about 12% darker),
       transparent PNGs show the tile, and no box edge shows.
     - The card's 14px radius rounds the tile's top corners. An inset "well" rounded on
       all four sides was considered and rejected: it needs `:has()`, collides with the
       -% flag and the heart, and changes photo-less and gone cards.
     - Gone cards keep `--chip` and normal blending, because multiply over a dark fill
       blackens the photo. For the same reason, never use multiply outside the dark
       media query: when a browser darkens a page that reports light, multiply would
       turn the photos black.
   - **Heart disc.** It is `#f1f3f5`, not `#fff`, which keeps the near-white pixel share
     low. The teal heart on it measures 5.0:1.
   - **Store tiles.** The inset shadow paints over the inline `hsl()` without touching
     seo.py or the JS. Text in #eef1f4 measures ≥ 6.3:1 on all 360 hues.
   - **/p/ photo.** Only `background`, `border-radius` and `filter` change, with no
     padding, so the box is the same size in both modes. Padding would change the
     height of non-square photos.
   - **Backdrop.** `::backdrop` gets a literal color, because older browsers don't pass
     custom properties into `::backdrop`.

2. **`dealradar/publish/dark.py`** (new, about 30 lines, modeled on `_pwa_web.publish`):
   - Define `CSS` (read from the template),
     `META = '<meta name="color-scheme" content="light dark">\n'` and
     `STYLE = '<style id="wp-dark">' + CSS.strip() + '</style>\n'`.
   - `publish(out, cfg)` walks every `out.rglob("*.html")` in sorted order:
     - First remove any existing META and STYLE (exact strings), so a rerun or an
       ON→OFF flip leaves no trace.
     - If `cfg.dark_polish` is set, insert META right after the first
       `<meta name="viewport" …>` tag (right after `<head>` if there is none), and
       insert STYLE right before `</head>`.
     - Write a file only when its bytes change. Leave pages without `</head>` alone and
       count them.
     - Return `{"pages": n, "skipped": m}`.
   - When disabled, it adds no bytes.
3. **`site.py`.**
   - Call `dark.publish(out, cfg)` right after `inventory.publish(out, cfg)` (1075),
     before `apply_tracking`.
   - Next to the offscreen version mix (851-852), add
     `if cfg.dark_polish: site_version = sha256(site_version + dark.CSS)[:12]` in the
     same form.
   - Change nothing else in site.py.
4. **Config.**
   - `config.py`: add `dark_polish: bool = False` next to `offscreen_cards` (308). Load
     `site.dark_polish` with default False. A non-boolean raises
     `ValueError("site.dark_polish must be a boolean")` (copy 863-865).
   - `config.yaml` `site:`: add
     `dark_polish: true   # task 254: dark-mode-only CSS + color-scheme meta; light bytes unchanged`.
5. **Existing byte pins: never re-pin a hash.**
   - Where a test publishes with the shipped config and pins bytes or head structure,
     add `cfg.dark_polish = False  # recorded before task 254`. That is how task 237
     handled `offscreen_cards` (tests/bounded_prefilter_fixture.py:25/77,
     tests/fetch_parallel_fixture.py:40).
   - Start with tests/test_pwa.py::baseline_config("shipped") and
     tests/phone_budget_fixture.py::fixture_config("shipped").
   - List every file changed this way in HANDOFF.
   - If a pin can only be kept by re-pinning, stop and report.
6. **Out of scope:**
   - extension/, android/ and any iOS work. The operator put the apps and browser
     extensions on hold for 1–2 weeks.
   - Newsletter and email HTML.
   - The forced-over-light case.
   - Light-mode fixes. The same QA found these; leave them for a separate task:
     - The join pop-up's disabled label is 1.19:1 in light too.
     - Field borders are 1.15–1.25:1 in light.
     - The 404 arrow is 3.03:1 in light. That passes 3:1 for its 26px glyph.
   - Task 255 (homepage popular/affordable boost) may be in progress in the same tree.
     It edits config.yaml `publish.front_page`, rank.py, verify.py and the site.py
     picks. Touch only the lines named in this brief.

## Tests

**Offline, in a new `tests/test_dark_mode.py`:**

1. **Config.**
   - true and false load as given; a missing key gives False.
   - `AppConfig().dark_polish is False` and `load_config().dark_polish is True`.
   - `[0, 1, "false", "true", None, [], {}]` are each rejected with the exact message.
2. **OFF bytes, ON additive only.** Copy test_offscreen_cards.py::test_off_bytes_and_on_only_css:
   4 modes, its offers, its `pre_extension_privacy` fixture and its four expected tree
   hashes.
   - OFF: the tree hash equals the pin.
   - ON: every `*.html` contains META exactly once (right after the viewport meta) and
     STYLE exactly once (right before `</head>`).
   - Removing both blocks and swapping the site_version back gives the OFF bytes for
     every file. Non-HTML files are identical apart from site_version.
   - Add one case with product pages and the checker enabled, so /p/ and /check/ are
     covered.
3. **Dark-only.** Parse dark.css:
   - After removing the two `@media (prefers-color-scheme:dark)…{…}` wrappers, nothing
     remains.
   - There is no `url(`, `@import` or `@font-face`, so no new requests.
   - The only `!important` is the one in the disabled-label rule.
4. **Idempotent.**
   - Publishing twice gives identical bytes.
   - ON and then OFF on the same directory gives the OFF bytes.
   - `tests/test_e2e_offline.py::test_rerun_is_idempotent` (both modes) passes with the
     shipped config.
5. **One token set.** Take every token in the dark.css `:root`. Every dark `:root` block
   that defines it, in templates/*.html (except android-offline.html) and in
   `product_pages.READER_STYLE`, has the same value.
6. **Contrast table** (pure Python WCAG formula, using dark.css values plus index.html's
   dark header tokens).
   - Text must be ≥ 4.5:1:
     - fg, muted, link and accent on bg, card, chip and card-raised;
     - accent-fg on accent;
     - good, hist and unv on card;
     - cond-fg on cond, dead-fg on dead and flag-fg on flag;
     - header fg, muted, link and accent on #0f1b22;
     - tile-fg on every hue 0–359 of `hsl(h,42%,62%)` with `--ph-dim` composited over it;
     - muted on the disabled button fill (a 20% `color-mix` of accent into card, and
       into card-raised).
   - Non-text must be ≥ 3:1:
     - field-line on card, card-raised and bg;
     - the hist and muted chart strokes on card;
     - the #0e7569 heart on #f1f3f5.
7. **Budget.** Run phone_budget_fixture "shipped" with dark ON and measure with
   `scripts.publication_budget`.
   - home_css gzip must be ≤ 14,913 and home_html gzip ≤ 77,360 (the config limits).
   - Print the OFF and ON numbers. When this brief was written they were: CSS
     14,289 → 14,824; HTML 75,186 → 75,775.
   - Do not raise a limit. If it fails, stop and report.

**Browser: a new `tests/dark_mode_browser.cjs` with a pytest wrapper.**

The pattern is `node()` in tests/test_checker_page.py plus tests/pwa_layout.cjs.
Playwright is under /opt/node-tools and Chromium is /opt/pw-browsers/chromium. Exit code
77 means skip because Chromium cannot launch; if that happens, say so in HANDOFF.

- **Fixture.**
  - Build the site twice, dark OFF and dark ON, with `site.render_site(...)` from at
    least 24 deals across Amazon, Walmart, Target, Best Buy and Woot. Create them with
    `tests/test_product_entities.insert` plus `db.record_price`, 10 checks each, so the
    /p/ pages draw charts.
  - Turn on product pages and the checker (`validated_checker`), and set
    `seo_hub_min_items = seo_store_page_min_deals = 1`.
  - Include one photo-less deal, one gone deal, one pale gray product and one
    transparent-background photo.
  - Serve both trees on a fake origin with `context.route` and abort everything else.
    Answer every image request with a generated SVG (white 400×400 plus a product shape;
    one URL gets a transparent variant). No real retailer or CDN request.
- **Pages,** at 390×844 with isMobile:
  - Home: `/` at the top, scrolled to the card grid, at the footer sign-up, with the
    join pop-up open (open it the way support.js:113 does if there is no public
    trigger), and with the filter sheet open (`#filtertoggle`).
  - `/best-deals-today/`, one `/deals/<hub>/`, one `/stores/<store>/`, one
    `/deal/<slug>/` and one `/p/<product>/`.
  - `/check/`, /join, `/subscribe.html`, `/404.html` and `/about.html`.
- **Modes.**
  - Light, and dark (via `colorScheme`).
  - Forced dark with a dark preference: CDP `Emulation.setAutoDarkModeOverride
    {enabled:true}` plus the launch flag `--enable-features=WebContentsForceDark`.
  - Forced dark with a light preference: screenshots for the record only, no assertions.
  - Freeze time with `page.clock.setFixedTime`. Take screenshots with
    `animations:'disabled'` and `caret:'hide'`, masking `.tk` and `.drop`.
- **Assertions.**
  1. Light ON equals light OFF pixel for pixel on every shot, i.e. light mode is unchanged.
  2. Forced dark (dark preference) ON equals dark ON, i.e. no double inversion. A probe
     page with no color-scheme declaration IS darkened under the same settings, which
     proves the forced mode was active.
  3. Dark ON and dark OFF have the same scrollHeight and the same boxes for the first
     12 `.grid .card` and their `.media`, i.e. no layout change.
  4. Contrast of key text in dark ON (headings, prices, titles, chips, badges, buttons,
     store tiles, footer, join card, chart caption).
     - Compare each element's computed text color with its background, measured as the
       median pixel of the element's box with its text made transparent.
     - Require ≥ 4.5:1, or ≥ 3:1 for text ≥ 24px or bold text ≥ 18.66px.
     - Write `/tmp/task254-shots/contrast.json`.
  5. Glare: near-white pixels (minimum channel ≥ 245) make up ≤ 1% of every dark ON
     shot. Report the dark OFF share next to each one.
  6. No console errors, and no request leaves the fake origin.
- **Screenshots.** Keep the PNGs at `/tmp/task254-shots/<mode>/<on|off>/<page>.png` for
  the reviewer; do not delete them. If your sandbox blocks Chromium, the parent will
  rerun `node tests/dark_mode_browser.cjs <off> <on> <shots>`.

**Targeted regression (`-n 1`):**
- tests/test_dark_mode.py, test_offscreen_cards.py, test_pwa.py, test_phone_budget.py,
  and `test_e2e_offline.py -k rerun`.
- Every file changed in step 5.
- The shipped-config byte-pin files: test_seo.py, test_seo_deal_pages.py,
  test_product_price_pages.py, test_checker_page.py, test_checker_index.py,
  test_store_policy_pages.py, test_support.py, test_established_look.py,
  test_template_slots.py, test_public_copy.py, test_hub_inventory.py,
  test_event_pages.py, test_android_app.py, test_appeal.py, test_niche_last.py,
  test_unconfirmed_label.py, test_amazon_as_of.py, test_own_price_checks.py,
  test_product_shadow.py and test_browser_extension.py.

## Rules

- **Scope of edits.** Dark mode only.
  - Do not edit any existing template, JS file, seo.py or STYLE string. Light-mode
    output must stay byte-for-byte what it is today, apart from the injected dark-only
    block.
  - No new network requests: no fonts, no external CSS, no linked stylesheet.
  - Never re-pin an existing hash.
  - Do not change tests/test_workflows.py.
- **Running tests.**
  - Use `-n 1` and your own basetemp `/tmp/task254-*`, and delete the basetemp after
    each run. `/tmp/task254-shots/` is the exception: keep it.
  - Run only the targeted tests above, never the full suite. Report the exact
    pass/fail lines.
- **Finishing up.**
  - Do not commit.
  - Update docs/HANDOFF.md "In flight" with one short entry:
    - the switch value;
    - the files changed, including the step-5 fixtures;
    - the OFF and ON budget numbers;
    - whether the browser checks ran or were blocked;
    - where the screenshots are.
  - In the same entry, list the manual checks still open: iPhone Safari in dark,
    Chrome Android with the dark theme, and Samsung Internet's dark mode (not verified
    here). Also note the questions for the marketing contact: browser or app,
    screenshot, and inversion settings.
