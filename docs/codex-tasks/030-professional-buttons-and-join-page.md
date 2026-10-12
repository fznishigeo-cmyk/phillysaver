# Codex task 030: professional buttons and a real "Join free" page

**Read first:** AGENTS.md, CLAUDE.md. Files: `dealradar/publish/templates/index.html`
(header `#subscribe`, `#subbar`, `.getdeal`, `.pwbtn`, the Join dialog and
`data-join` handlers), `support.js` (`open()`, the join form submit),
`subscribe.html`, `hub.html`, `dealradar/publish/site.py` (support-mode
copy, `JOIN_FORM`), `dealradar/publish/seo.py` (sitemap: public pages),
`scripts/site_check.js` (step 6 opens the Join dialog), tests.

Operator (2026-10-12), pointing at sovrn.com: buttons that look premium
(clean outlined pill "Sign Up", calm solid primary buttons, small
letter-spaced capitals for kicker labels), and "Join free" opening its own
page, a centered "Create a free account" card, instead of a pop-up. The
site should feel like a dignified casino: polished, confident, never
loud.

## 1. Buttons (support mode; keep the paywall/open variants working)
- Header **Join free**: an outlined pill (1.5px border in the accent
  colour, transparent fill, accent text, ~10px 22px padding, 999px radius,
  500 weight), filled on hover/focus with a 150 ms transition; same height
  as the drop clock pill; visible focus ring.
- **Get deal**: solid accent, 10px radius, 600 weight, no shadow by
  default, subtle lift of colour on hover (no movement: the page must not
  shift; see the hover fix from earlier: no transform on cards/buttons).
- Secondary buttons (`.pwbtn.alt`, Surprise me, Show more deals): quiet
  outline style matching the pill.
- One type scale: kicker labels (tagline, section labels) 11-12px,
  uppercase, 0.14em letter spacing, muted colour.
- Light and dark themes both; contrast ≥ 4.5:1 for button text (WCAG AA).

## 2. `/join` page
- New template `join.html` published as `join.html` (Cloudflare serves
  `/join`): the site header (logo, link back to deals), then a centered card
  (max 520px, 1px border, 12px radius, soft shadow) with the kicker
  "THE DAY'S BEST DEALS. FREE." and the heading "Create a free account",
  one Email field (label above, full-width), a full-width **Join free**
  button, the line "One email with the best deals. Unsubscribe anytime.",
  and under it the privacy line linking /privacy.html. Same POST as today
  (`{api_url}/subscribe`, `{"email"}`), same messages, `track("sign_up",
  {method: "email"})`. A quiet decorative shape in the accent colour on the
  right on desktop only (pure CSS, no image), hidden on phones.
- Every "Join free" link and button (header, phone bar, banner, footer,
  in-wall box, hub/deal pages) goes to `/join` (plain links; no dialog).
  `#join`/`#signup` hashes on the home page redirect to `/join` (old links
  keep working). Remove the Join dialog markup and code in support mode only.
- Add `join.html` to the public page list (sitemap) with a `<title>` and
  canonical; `robots` allowed.
- `scripts/site_check.js` step 6 (support mode): the header Join link's
  href ends with `/join`, and `/join` answers 200 with an email field
  (never submit).

## 3. Tests
Button CSS markers (pill, no transform on hover), every Join link points to
/join, the hash redirect, join.html content and the POST body, the dialog
gone in support mode only, sitemap entry, site_check step 6 update. Do not
loosen other tests. One short bullet in docs/HANDOFF.md under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
