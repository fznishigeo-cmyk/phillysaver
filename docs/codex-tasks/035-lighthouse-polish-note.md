# Task 035 — Lighthouse polish note

Shared light/dark text colours, badges and store labels now meet 4.5:1.
The Join card's description, legal link, controls and heading are checked on
both home and `/join`. Body/credit/footer/legal links have 1px underlines
with a 2px offset; brand links, buttons and category pills keep their treatment.

WCAG sRGB ratios below are rounded for reporting; tests compare unrounded
values. Card/background/chip order follows the CSS tokens. Store minima
cover every integer hue; faded text includes its former opacity.

| Text/background | Before | After |
| --- | ---: | ---: |
| Light muted / card | 5.49 | 5.91 |
| Light muted / page | 5.02 | 5.41 |
| Light muted / chip | 4.84 | 5.22 |
| Dark muted / card | 6.71 | 7.55 |
| Dark muted / page | 7.40 | 8.33 |
| Dark muted / chip | 5.96 | 6.70 |
| Header muted / brightest gradient + chip | 4.44 | 4.55 |
| Light discount badge | 4.30 | 4.83 |
| Light USED / OPEN BOX badge | 3.64 | 5.22 |
| Light unverified colour / chip | 4.27 | 4.66 |
| Store tile label, minimum | 1.73 | 4.67 |
| Gone store tile label, minimum light / dark | 1.34 / 1.54 | 4.52 / 4.52 |
| Free-drop label, light / dark | 3.98 / 5.22 | 5.91 / 7.55 |
| Gone-card muted text, light / dark | 2.29 / 2.89 | 5.91 / 7.55 |
| Dark gone badge text (before card fading) | 2.55 | 7.07 |
| Ticker percent, light / dark | 5.27 / 3.63 | 11.80 / 11.65 |

Placeholder colours previously depended on browser defaults. They now use
the tested muted token at full opacity, including the header's local token.
Gone cards retain muted text, grey photos/tiles and muted badges without
fading the text and its background together.

Home keeps the same media box for static tiles and live images, with a
reserved aspect ratio at each breakpoint and contained, positioned images.
Only the first four images per render get high priority and eager loading;
others stay lazy and decode asynchronously, across grid/banner splits.
Static HTML stays image-free. The ticker reserves its 44px band when there
are three live deals, then builds after a paint using idle callback or timeout.
There are no external blocking scripts before the header.

Hidden h2s cover the static/live main grid, best-today, store/price grids and
Deal hacks. Card titles remain h3. Tests check every emitted HTML page in
support, open-access and membership modes, including engagement disabled;
the existing Join card already uses h2 on home and the only h1 on `/join`.

Validation: 24 new offline checks plus existing publishing/Node harnesses.
The full-suite result is recorded in `docs/HANDOFF.md`. Chromium cannot
launch in this sandbox, so the ≥95 accessibility / ≥90 home performance
scores and desktop/phone appearance still need a Lighthouse/browser run.
No commit or push.
