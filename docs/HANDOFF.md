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
  best 200 of what arrived since the last one, plus a "+N more" line.
- **Cancel wording:** no "cancel" reminders on the main screen. "Manage
  membership" sits in the footer only. The full auto-renewal terms stay in
  the checkout dialog, on Stripe's page, on the welcome page and on the help
  pages (legal requirement).
- **Deals stay until genuinely over.** A live deal is never removed for its
  age (Amazon "used - like new" offers last until someone buys them). A
  deal dies only when:
  - its stated expiry passes;
  - its source post says it ended or sold out;
  - a live price check (Keepa offers, the Woot API) finds it gone.
  Dead deals are deleted 14 days after they die.
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
- **Tripped sources** (`source probe` run, 2026-10-11). `crawl_permission`
  waives robots.txt only; a site's firewall still decides.
  - **Firewall blocks GitHub's servers.** The owner must allowlist our
    User-Agent: `dealradar/0.1 (+https://github.com/fznishigeo-cmyk/phillysaver;
    contact: hello@wowpriced.com)`.
    - Cloudflare challenge: bensbargains, coupon_lady_deals, dealmama,
      edealinfo, offers_blog (no permission).
    - couponcabin_blog: 403.
    - coupon_with_kayla: 202 challenge page.
    - dealgenius: 429 on the first request.
    - raining_hot_coupons: serves its feed WITH a 403 status. We treat a 403
      as a refusal; the owner should fix the status.
  - **Paused** (`enabled: false`, reason inline in sources.yaml):
    - dead: dealsplus and kinja_deals (DNS), carolinas_and_dollars (hosting
      suspended), couponflick (no feed);
    - redundant: hip2save_freebies (its posts are in the hip2save feed);
    - HTML pages, not feeds: bradsdeals, dealmoon_us, dealsofamerica,
      jungle_deals, morningsave. Each needs a page reader before it can return.
  - **inspiring_savings:** their server returns 500; it retries on its own.

## Open tasks
1. Bring daily LLM spend to $6 by 2026-10-14 (task "cut AI spend"). Ideas:
   - a cheaper critic for clear cases;
   - a per-run critic cap;
   - tuned intake.
2. Operator (Stripe dashboard, Product catalog -> "WowPriced Alerts" -> Edit):
   the live description still reads "Every verified deal, 50-90% off,
   emailed to you the hour it is found. Cancel any time." (confirmed by
   `stripe check`, 2026-10-11). Replace it with "Every deal on the wall
   unlocked, plus the best of each day's new verified deals in one email
   every morning. Cancel any time."
3. Done: the Customer Portal is active (1 configuration), so "Manage
   membership" works.
