# Handoff — state of wowpriced.com (updated 2026-10-11)

Keep this file current: whoever stops working updates it in their last commit.

## How the system runs
- **Pipeline:** `.github/workflows/fast.yml` runs every ~10 min and `hourly.yml`
  hourly. Each run fetches the sources, parses deals (Claude Haiku; Sonnet
  critic), and publishes the site to Cloudflare Pages and GitHub Pages.
  `state.db` is committed to the `data` branch.
- **Billing and accounts:** the Cloudflare Worker `cloudflare/subscribe-worker`.
  Deploy it with the `deploy subscribe worker` workflow.
  - Stripe Checkout with a 7-day card-up-front trial.
  - `stripe check (read-only)` verifies checkout end to end.
- **Source health is private:** it is in each run's GitHub Actions summary,
  never on the public site.

## Operator decisions (standing rules; do not undo)
- **Free 8:** a new 8 of the top 48 at every drop (`paywall.teaser_pool`).
  The clock says "Next drop".
- **Cards:** the hairline runs on the free 8, on a member's new arrivals, and
  on hover. There is a deal ticker at the top and a sources wall at the bottom.
- **Member email:** at most ONE a day, 7 am New York time. It carries the
  best 40 of what arrived since the last one, plus a "+N more" line.
- **Cancel wording:** no "cancel" reminders on the main screen. "Manage
  membership" sits in the footer only. The full auto-renewal terms stay in
  the checkout dialog, on Stripe's page, on the welcome page and on the help
  pages (legal requirement).
- **No trial reminder emails.** Members cancel by emailing
  hello@wowpriced.com or through Stripe's portal.
- **LLM spend:**
  - $15/day until 2026-10-14; it then falls back to $6/day by itself.
  - The goal is $6/day.
  - Never skip the critic for Keepa deals: a review showed inflated bases.

## In flight
- **Walmart/eBay photos:**
  - The redirect resolver stores the retailer's bot-wall page
    (`walmart.com/blocked?url=…`, `ebay …/splashui/challenge?ru=…`) as the
    product URL. That kills the photo and links shoppers to the wall.
  - Fix:
    1. Stop redirects at challenge pages.
    2. Run a one-time repair that decodes the real URL, without changing
       dedupe keys.
    3. Persist `link_as_posted`.
    4. Skip listing and wall pages in the og:image backfill.
  - Status: being built and reviewed by Claude, branch `photo-fix-botwall`.
- **Tripped sources:** almost all carry `crawl_permission`. That waives
  robots.txt only; it does not stop the site's firewall or server errors.
  - **403:** bot protection blocks GitHub's servers. Never bypass it; ask the
    owner to allowlist our User-Agent.
  - **404 / not a feed:** the feed URL is wrong.
  - **DNS errors:** the site is gone (kinja_deals, dealsplus).
  - Next step: probe the tripped sources from GitHub Actions, then correct the
    URLs and retire dead sources.

## Open tasks
1. Bring daily LLM spend to $6 by 2026-10-14 (task "cut AI spend"). Ideas:
   - a cheaper critic for clear cases;
   - a per-run critic cap;
   - tuned intake.
2. Operator (Stripe dashboard): turn on the Customer Portal (Settings →
   Billing → Customer portal → Save). Until then "Manage membership" tells
   people to email.
3. Operator: check that the Stripe product description matches the daily
   email ("one email every morning").
