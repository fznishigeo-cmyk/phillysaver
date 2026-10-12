# Codex task 045: a clean, professional footer + a fuller home page

**Read first:** AGENTS.md, CLAUDE.md. Files: every template with a
`<footer>` (`index.html`, `about.html`, `careers.html`, `contact.html`,
`hacks.html`, `how-it-works.html`, `hub.html`, `404.html`, `privacy.html`,
`subscribe.html` and any other), `footer-signup.html/.css`,
`dealradar/publish/site.py` (footer insertion near `footer-signup.html`,
`AFFILIATE_FOOTER` rewrite), `dealradar/publish/seo.py` (pages built from
hub.html), `config.yaml`, `scripts/site_check.js`, tests (byte-hash
baselines in tests/test_support.py are updated from the new output).

Operator (2026-10-12), comparing with Groupon's one-line footer and
Slickdeals' link columns: "why does it have to be so wordy, it looks
unprofessional" and "slickdeals has several hundred on their homepage, I
want mine to be similar".

## 1. One shared footer on every page
Build the footer once (`templates/site-footer.html` + `.css`) and insert it
on every page the way `footer-signup.html` is inserted; delete each
template's own footer content. Order, top to bottom:
1. The existing "Sign up for the latest deals" box (unchanged).
2. A link grid: 4 columns on desktop, 2 on phones (CSS grid, 24px gap,
   column headings 13px 600 uppercase muted, links 15px, no underlines
   until hover/focus, 32px tap rows on phones):
   - **wowpriced**: the logo mark + name, then one muted line "We search
     the top deal sites and stores for hours every day, so you don't have
     to."
   - **Company**: About, How it works, Work with us, Contact
   - **Help**: Deal hacks, Join & support, Privacy
   - **Follow us**: links from new config `publish.social_links`
     (`pinterest`, `facebook`, `instagram`, all "" for now; https URLs
     only); the column is left out when none is set.
3. A thin top-bordered bottom bar, 12px muted: "© 2026 wowpriced" on the
   left (year from the render date); on the right the existing FTC/Amazon
   disclosure text exactly as today (same source, same words). On phones
   the two stack.
- Removed from the footer (nothing else changes): the "wowpriced is free
  for everyone. Support our work · Join free" line, the support small
  print ("Once: any amount ... every deal stays free."), the paywall
  membership paragraph, and "Every deal links to the seller ... re-checked
  live." Before removing them, make sure: the support small print (for-
  profit, not tax-deductible, cancel anytime) still appears on the support
  page next to its buttons; the "Every deal links to the seller..."
  explanation appears on how-it-works.html. Add them there if missing.
- Keep: the footer's `id`s that scripts use (`metaline` disclosure,
  `acctfoot` account line for members, etc.); links work from nested pages
  (absolute `/` paths). Same look in dark mode; WCAG AA contrast.

## 2. Home page: about 300 deals, like Slickdeals' front page
- `AUTO_CARDS` 120 -> 300 on Trending: the 120 top picks, then the freshest
  of the rest, auto-loading as you scroll until 300 cards, then the
  existing "Show more deals" and "Back to top" buttons. Category and All
  deals tabs keep endless scroll. Static/no-JS wall unchanged.

## 3. Tests
Every page has exactly one footer with the 4 headings (3 when no social
links), the disclosure text unchanged, "© 2026 wowpriced"; none of the
removed sentences remain in any footer; support small print present on the
support page; social links only from config and https; Trending auto-load
stops at 300; site_check.js still passes its footer/sign-up steps. One
short HANDOFF bullet under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
