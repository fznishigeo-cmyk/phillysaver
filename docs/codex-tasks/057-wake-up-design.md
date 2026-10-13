# Codex task 057: wake up the top of the page (design refresh, not a rebuild)

**Read first:** AGENTS.md, CLAUDE.md (surgical changes; match the existing
style). Files: `dealradar/publish/templates/index.html` (the whole page:
`:root` tokens at the top, `header{...}` dark tokens, `.tk` live-deals
ticker, `.dial` drop clock, `#q` search, the category tabs, `.grid`), the
other templates that share the header look (`about.html`,
`how-it-works.html`, `subscribe.html`, `join.html`, `privacy.html` and the
SEO pages built in `dealradar/publish/seo.py`), `tests/test_support.py`
(byte-hash baselines), `tests/test_front_page.py`, Lighthouse workflow.

Operator (2026-10-13), after showing the site and Slickdeals to a
marketing-minded friend: "the top portion... I feel like I'm falling asleep";
Slickdeals "wakes you up right away"; Walmart's yellow "brings people in";
our search bar is dark while theirs is clean white; they show 5 deals per
row, we show 4 ("people can see almost ten deals when they scroll down
slightly"); a "Top Voted Amazon Deals - The Community is Loving Now - See
Deals" strip "makes it more legit". "Not tear down the whole website ...
some subtle things ... something to pop. Wake up people."

## Do
1. **Header color scheme.** Keep the wowpriced mark and the teal brand,
   but drop the sleepy all-dark-teal look: a bright, high-energy header.
   Add ONE warm accent (a Walmart-like yellow, e.g. #FFC220, or a hot
   orange) used sparingly: the drop clock ring, the "Join free" button or
   its hover, the active tab underline, the % OFF badge family. Light mode
   and dark mode both get a deliberate palette (dark mode keeps a dark
   header but gains the same warm accent). Text contrast meets WCAG AA
   everywhere (4.5:1 body, 3:1 large/UI); check every pair you add.
2. **Search bar:** clean white field (dark text, visible border/shadow,
   magnifier icon), clearly the biggest thing in the header, like
   Slickdeals'. Dark mode: light-on-dark but still the brightest element.
3. **5 deals per row on wide screens.** At desktop widths (>= ~1280px)
   the grid shows 5 cards per row without cramping the card content
   (price, title 2 lines, chips, Get deal). Tablet/phone layouts stay as
   they are (phones keep 2 per row). Adjust the page max-width if needed.
4. **"Top Amazon deals right now" strip** under the header, above the
   tabs/grid: a slim white band with the word Amazon in bold dark text with
   a small orange accent (NO Amazon logo image, no Amazon smile artwork, no
   imitation of their wordmark: Associates trademark rules), the line
   "Top Amazon deals right now", and a "See deals ›" link that filters the
   wall to store = Amazon, sort = top picks (reuse the existing store
   filter; no new data). Hide it when there are fewer than 10 live Amazon
   deals.
5. **Category tabs:** more lively (e.g. a small colored dot or icon per
   category using existing inline-SVG/emoji-free CSS shapes, brighter
   active state). Keep them one wrapping row; keyboard/ARIA unchanged.

## Don't
- No new external requests (no web fonts, no CDN, no images from other
  hosts); the page stays self-contained (existing tests check this).
- No logos or trademarked artwork of any store. Store names as text only.
- Don't change ordering logic, data, prices, links, tracking/consent
  behavior or any copy outside the header/strip/tabs.
- Don't break `prefers-reduced-motion`, focus rings, or the ticker's pause.

## Tests
Update byte-hash baselines in tests/test_support.py from the new output
only. Add tests: the strip appears with >= 10 Amazon deals and links to the
Amazon filter, is absent below that; no `<img>`/svg of a store logo; grid
CSS has a 5-column rule for wide screens; no external URLs added. One short
HANDOFF bullet.

Run `.venv/bin/python -m pytest -q`; all green except tests that need local
sockets your sandbox blocks (name them). Do NOT commit or push. End with a
short note of the palette (hex values + where used, contrast ratios) and
anything unsure.
