# Codex task 018: the hourly site check clicks through the site like a visitor

**Read first:** AGENTS.md, CLAUDE.md. Files: `scripts/site_check.js`,
`.github/workflows/site-check.yml`.

Operator (2026-10-12): "monitor the website similar to how a consumer
would ... click through things, make sure everything is working". Today
`site_check.js` loads deals.json, the home page and every photo. Add a
visitor walk-through, on desktop (1440x900) and phone (390x844, isMobile),
and report each step as `ok` or `FAIL <short reason>`:

1. Home page: status 200, at least 24 cards, the clock shows m:ss (not
   "soon"), the tagline shows "N+ live deals", no page errors.
2. Scroll to the end of the first 60 cards: more cards load (count grows).
3. Click the second category tab: the wall re-renders with cards; click
   "All" again.
4. Type a word from the first card's title into the search box: at least
   one card stays and it contains that word; clear the search.
5. The first card's "Get deal" link: an `https://` URL whose host is not
   wowpriced.com and not empty. Do NOT open it (never visit retailers or
   sources from this check).
6. Click the header trial button: the sign-up dialog opens with an email
   field; close it (Escape). Never submit anything.
7. With yearly on (deals.json meta.yearly_price_usd > 0): the page source
   holds the "Yearly" plan choice.
8. These pages answer 200 with a `<title>`: /about.html, /how-it-works.html,
   /contact.html, /privacy.html, /subscribe.html, /sitemap.xml, /robots.txt,
   and the first 3 category hubs listed in sitemap.xml.
9. deals.json `meta.published_at` is less than 45 minutes old.

Output: keep today's photo report, then a "Walk-through" block with one
line per step per device. **Exit 1 when any step FAILs** (so the run turns
red and GitHub notifies the owner); a photo shortfall alone stays a report,
not a failure. Keep analytics blocked; never submit forms; never follow
deal links; at most ~40 requests to wowpriced.com beyond the photos.

Test it locally where you can (`node --check`); a Chromium launch may be
blocked in your sandbox: say so. Do NOT commit or push. End with a short
note of what changed and anything unsure.
