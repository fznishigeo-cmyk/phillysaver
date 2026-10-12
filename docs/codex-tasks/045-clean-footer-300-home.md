# Codex task 045: a clean, professional footer + a fuller home page

**Read first:** AGENTS.md, CLAUDE.md. Files: every template with a
`<footer>` (`index.html`, `about.html`, `careers.html`, `contact.html`,
`hacks.html`, `how-it-works.html`, `hub.html`, `404.html`, `privacy.html`,
`subscribe.html` and any other), `footer-signup.html/.css`,
`dealradar/publish/site.py` (footer insertion near `footer-signup.html`,
`AFFILIATE_FOOTER` rewrite), `dealradar/publish/seo.py` (pages built from
hub.html), `config.yaml`, `scripts/site_check.js`, tests (byte-hash
baselines in tests/test_support.py are updated from the new output).

Operator (2026-10-12), comparing with Groupon's one-line footer: "why does it have to be so wordy, it looks
unprofessional" and "slickdeals has several hundred on their homepage, I
want mine to be similar".

## 1. One shared footer on every page, Groupon-style
Operator follow-up: "I want it like Groupon, the other stuff can be tiny
and in grey". Build the footer once (`templates/site-footer.html` + `.css`)
and insert it on every page the way `footer-signup.html` is inserted;
delete each template's own footer content. Top to bottom:
1. The existing "Sign up for the latest deals" box (unchanged).
2. ONE line (wraps on phones), 13px, muted grey, centred on phones and
   left-aligned on desktop, items separated by " · ":
   "© 2026 wowpriced" (year from the render date), About, How it works,
   Deal hacks, Work with us, Contact, Join & support, Privacy. Links in the
   same grey, no underline until hover/focus, 44px-tall tap targets on
   phones (padding, not bigger text).
3. One fine-print paragraph, 11px, muted grey, max-width 900px: "We search
   the top deal sites and stores for hours every day, so you don't have
   to." followed by the existing FTC/Amazon disclosure text exactly as
   today (same source, same words). Small and quiet, but keep WCAG AA
   contrast (the FTC wants it readable; Lighthouse accessibility stays 100).
- Removed from the footer (nothing else changes): the "wowpriced is free
  for everyone. Support our work · Join free" line, the support small
  print ("Once: any amount ... every deal stays free."), the paywall
  membership paragraph, the old link row, and "Every deal links to the
  seller ... re-checked live." Before removing them, make sure: the
  support small print (for-profit, not tax-deductible, cancel anytime)
  still appears on the support page next to its buttons; the "Every deal
  links to the seller..." explanation appears on how-it-works.html. Add
  them there if missing.
- Keep: the footer's `id`s that scripts use (`metaline` disclosure,
  `acctfoot` account line for members, etc.); links work from nested pages
  (absolute `/` paths). Same look in dark mode.

## 2. Home page: about 300 deals, like Slickdeals' front page
- `AUTO_CARDS` 120 -> 300 on Trending: the 120 top picks, then the freshest
  of the rest, auto-loading as you scroll until 300 cards, then the
  existing "Show more deals" and "Back to top" buttons. Category and All
  deals tabs keep endless scroll. Static/no-JS wall unchanged.

## 3. Tests
Every page has exactly one footer: the one link line with "© 2026
wowpriced" and all 7 links, the fine print with the search line and the
disclosure text unchanged; none of the removed sentences remain in any
footer; support small print present on the support page; Trending auto-load
stops at 300; site_check.js still passes its footer/sign-up steps. One
short HANDOFF bullet under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
