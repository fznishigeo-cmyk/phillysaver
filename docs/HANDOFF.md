# Handoff — state of wowpriced.com (updated 2026-10-11)

Keep this file current: whoever stops working updates it in their last commit.

## How the system runs
- **Pipeline:** `.github/workflows/fast.yml` runs every ~10 min and `hourly.yml`
  hourly. Each run fetches the sources, parses deals (Claude Haiku; Sonnet
  critic), and publishes the site to Cloudflare Pages and GitHub Pages.
  `state.db` is committed to the `data` branch.
- **The repository is public, so state.db is encrypted** on the `data` branch
  (`state.db.enc`, key = the `STATE_DB_KEY` repository secret). Restore
  fails the run when the key is missing or wrong: never "fix" that by
  starting empty. Without the secret, commits stay unencrypted with a
  warning. After the first encrypted commit, run `squash data branch` once
  so no unencrypted copy stays in the branch history.
- **Billing and accounts:** the Cloudflare Worker `cloudflare/subscribe-worker`.
  Deploy it with the `deploy subscribe worker` workflow.
  - Stripe Checkout with a 7-day card-up-front trial.
  - `stripe check (read-only)` verifies checkout end to end.
- **Source health is private:** it is in each run's GitHub Actions summary,
  never on the public site.

## Operator decisions (standing rules; do not undo)
- **Free 8:** a new 8 of the top 48 at every drop (`paywall.teaser_pool`).
  The clock says "Next drop".
- **Brand:** a tilted teal tile whose "w" doubles as a price-drop zigzag,
  with a gold spark (inline SVG, also the favicon), and the "wowpriced"
  wordmark. The home header is an ink bar in both themes; top right: drop
  dial, "Log in" pill, trial button with a gold "$0 today" chip.
- **Cards:** the hairline runs on the free 8, on a member's new arrivals, and
  on hover. There is a deal ticker at the top and a sources wall at the bottom.
- **No customer emails at all.** Membership is website access only.
  - `members.email_enabled: false` and `newsletter.enabled: false` in
    config.yaml; the code paths stay, switched off.
  - No page offers or promises an email. `subscribe.html` is a plain
    membership page; old email links land there with a short note.
- **"$0 today":** next to a trial button on the main screen the only price
  line is a "$0 today" badge. The footer opens with "Membership —
  $9.99/month", and the full terms stay in the dialog and on Stripe's page.
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

- **Friendly source credits (Codex task 001 / PR #2):** implemented on
  `codex/task-001`, awaiting Claude review/merge.
  - All 109 primary/backup entries, including disabled sources, now carry
    `display_name` and `site`; feeds of one brand share a display name.
  - Loader falls back to the internal ID when branding is absent. A small
    publishing helper resolves credits; source post URLs and internal IDs
    stay intact. No templates changed.
  - Homepage names confirmed with one polite homepage GET each: Brad's Deals,
    DealMoon, Deals of America, MorningSave. Jungle Deals returned a generic
    Amazon-deals title; Meh was blocked at robots.txt. Other sites are outside
    this environment's source-host allowlist. Unconfirmed names are listed in
    PR #2 and derived from feed domains/source configuration.
  - Validation: full `python -m pytest -q` suite: 993 passed, 1 slow
    integration test deselected by repository defaults.
  - Remaining: review/merge and confirm the remaining brand spellings.
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
- **Brad's Deals / Deals of America page readers:** implemented by Codex on
  `codex/bradsdeals-dealsofamerica-readers` (awaiting PR review/merge).
  - Explicit HTML adapters and page validation replace RSS autodiscovery for
    these two sources; both are enabled in this branch. Brad's URL is now
    `/deals`, since `/blog` contains editorial articles, not live deal cards.
  - Each card becomes one transient RawPost with its own source permalink,
    exact price/coupon wording and stable identity. No source images copied
    or displayed. Brad's unseen cards fetch their source detail page for
    matching `/go/d/<deal-id>-...` shopping links; known content is not
    enriched again, and enrichment does not change the card's content hash.
  - All requests use PoliteFetcher, preserving deadlines, conditional GETs,
    rate limits, permission scoped to the source host, and refusal handling.
    Changed markup/empty cards fail loudly; missing Brad's shopping links
    are excluded from paid parsing. No retailer pages are crawled.
  - Verified live: 24 Brad's posts with matching shopping links and 60 Deals
    of America posts (the per-fetch entry cap). Offline suite: 979 passed,
    1 deselected, including 21 page-reader tests. No paid LLM/publish run.
  - Remaining: review/merge PR; monitor source health in the first deployed
    runs. The cloud network access blocker was resolved in environment settings.
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
    - HTML pages, not feeds: dealmoon_us, jungle_deals, morningsave. Each needs a page reader before it can return.
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
   unlocked, with new deals every 10 minutes. Cancel any time."
3. Done: the Customer Portal is active (1 configuration), so "Manage
   membership" works.
4. Any change under `cloudflare/subscribe-worker/` goes live only after the
   `deploy subscribe worker` workflow runs (fast/hourly deploy the site
   only). It was run for the daily top-10 confirm page on 2026-10-11.
