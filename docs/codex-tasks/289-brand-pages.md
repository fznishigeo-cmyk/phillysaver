# 289 — Brand pages that look like a million-dollar site (About, Join, Contact, Work with us, Deal hacks, How we verify)

## Why
Operator 2026-10-17, with Slickdeals' About page and Groupon's pop-up as the bar:
"all of these pages are indexed in Google, but when I go to them they are boring
... it's just a wall of text ... mine looks like some kind of website someone
built in the basement and theirs look like a million dollar website ... I want
these pop-ups to come up when we're doing the about us and stuff like that.
Again, it needs to be nuanced, but it needs to look good."
Slickdeals' About: a bold hero ("Where savvy shoppers discover and share great
deals"), three short bullets, one big "START SAVING NOW" button, a collage of deal
cards beside it, then a "Why us" section of three photo-led stories.

## Task (config `site.brand_pages`, bool, missing/false = today's bytes exactly; ship false → operator previews → true)
1. One shared, static brand layout for /about, /join, /contact, /careers (Work with
   us), /hacks and /how-we-verify (find their current generators in
   dealradar/publish), keeping each page's own words/sections and SEO title,
   description, canonical and JSON-LD unchanged:
   - Hero: large bold headline (short, punchy, page-specific; e.g. About: "Real
     deals, checked before you see them"), a one-line subhead, three short
     bullets, one primary button "Start saving now" → the homepage, and on the
     right (below on phones) a collage of 3–4 REAL live deal cards from the
     current top picks (photo, store, final price, % off exactly as published;
     the same public fields the homepage shows; links to their deal pages).
     Never stock photos, never invented numbers.
   - "Why wowpriced" band: three icon blocks (inline SVG, no image downloads):
     verified prices (evidence quoted word-for-word), fresh every 15 minutes,
     everyday things people actually buy — copy from facts the site already states.
   - Live proof strip using real published numbers only (e.g. live deal count from
     the current publish, "checked every 15 minutes", number of stores/sources).
   - The existing body text below, in readable cards with clear headings, generous
     spacing, the site's teal/amber palette; header and footer as today.
2. The existing join pop-up (the homepage's sign-up modal: "Save up to 90% OFF"
   style) on these pages too, nuanced: once per visitor per day, after 25 s or 50%
   scroll, never on /join itself, closes with X/Escape/backdrop, respects the
   existing dismissal memory. Its copy must not promise an email cadence that is
   not running (config newsletter.enabled is false): use the honest wording the
   homepage card uses when email is not live (e.g. "Get the best deals by email —
   first issue soon") — check support.js / join-card.html and keep them consistent.
3. Phone first (390 px), dark mode, WCAG AA contrast, visible focus, reduced-motion
   respected, no layout shift (reserve image boxes), phone budgets pass, no new
   third-party requests.

## Tests
- OFF (missing/false) = today's bytes exactly for every page (recorded pins; never re-pin).
- ON: each page has the hero, CTA to "/", 3–4 collage cards that are real published
  deals (title/price/% equal to deals.json), no invented numbers, SEO head unchanged,
  JSON-LD unchanged; pop-up rules (timing, once per day, not on /join, closes).
- Real Chromium screenshots at 390×844 and 1440×900, light and dark, saved under
  docs/research/brand-pages/ (exit 77 skip only if Chromium cannot launch).
- Phone budgets pass; idempotent rerun.

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task289-*` basetemp (delete
after each run), targeted tests only. No network, no commit, no push. HANDOFF
"In flight": one entry. Ship OFF (operator previews first).

## Design direction (operator, 7:30–7:40pm; read this before Task above)
IMPROVE, DON'T REBUILD. Keep each page's existing text, sections and order
(trim only obvious repetition); add visuals and a bold hero on top. References:
camelcamelcamel About (simple, big centered logo, one sentence, two links),
DealNews About (full-width hero: small accent-bar eyebrow "WOWPRICED", huge
bold 2-line headline, one short paragraph, one button), Slickdeals Careers
(values row, numbered steps, illustrations). Bold and beautiful, few new words.
- Hero on every brand page (DealNews pattern): eyebrow label with a teal
  accent bar, big bold headline (≥ 44px desktop / 32px phone), one-line
  subhead, one button. Background: a soft teal/amber gradient with the
  3–4 real-deal collage on the right (below on phones). No stock photos of
  people, no image downloads beyond the deal photos the homepage already uses.
- About: hero headline e.g. "Real deals, checked before you see them";
  button "See today's deals" → /, secondary text link "Contact us". Then the
  existing About text as it is, in 2-column cards with a small inline-SVG icon
  per section, plus the "Why wowpriced" icon band and live proof strip.
  Affiliate disclosure and contact email stay verbatim.
- Work with us (careers): hero (e.g. "Deals, done honestly" + one-line
  mission + "Say hello" → mailto:hello@wowpriced.com), then a values row:
  3–4 values, each a check icon on a dashed connector line with a short label
  (e.g. Real prices only / Shoppers first / Fresh every 15 minutes / Polite to
  every store), then the existing text, then a numbered 3–4 step "Every day at
  wowpriced" strip with small inline-SVG illustrations. No invented openings,
  team size, benefits, offices or numbers.
- Join: hero + the existing email box + three short honest bullets ("first
  issue soon" while newsletter.enabled is false).
- Contact: hero, the email as a big button, the existing text as short cards.
- Deal hacks / How we verify: hero + the existing tips as cards (heading +
  text, icon each).
- FAQ pages: unchanged.
- Shared: generous whitespace, rounded cards, subtle shadows, site teal/amber.
- Already captured before this note (reuse, do not recapture):
  tests/brand_pages_fixture.py and tests/fixtures/brand_pages_off.json
  (OFF pins from the untouched tree).
