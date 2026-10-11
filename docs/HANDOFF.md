# Handoff — state of wowpriced.com (updated 2026-10-11)

Keep this file current: whoever stops working updates it in their last commit.

## How the system runs
- **Pipeline:** `.github/workflows/fast.yml` runs every ~10 min and `hourly.yml`
  hourly. Each run fetches the sources, parses deals (Claude Haiku; Sonnet
  critic), publishes the site, pings IndexNow, and commits state.
- **Hosting:** since 2026-10-11 **Cloudflare Pages** (project `wowpriced`)
  serves wowpriced.com and www (DNS at Cloudflare, proxied CNAMEs to
  wowpriced.pages.dev). GitHub Pages is being retired: its deploy steps come
  out of the workflows, then the operator unpublishes it. `_headers` keeps
  the *.pages.dev copies out of search.
- **Email:** Cloudflare Email Routing forwards hello@wowpriced.com (and the
  catch-all) to the operator's Gmail. Members cancel by email there.
- **State:** the repository is public, so `state.db` is encrypted on the
  `data` branch (`state.db.enc`, key = the `STATE_DB_KEY` repository secret;
  done and history squashed 2026-10-11). Restore fails the run when the key
  is missing or wrong: never "fix" that by starting empty.
- **Billing and accounts:** the Cloudflare Worker `cloudflare/subscribe-worker`.
  Deploy it with the `deploy subscribe worker` workflow.
  - Stripe Checkout with a 7-day card-up-front trial.
  - `stripe check (read-only)` verifies checkout end to end.
- **Search:** the home page carries the free deals as plain HTML; 11 category
  pages at `/deals/<slug>/` (copy in `publish/templates/hubs.yaml`, noindex
  under `seo.hub_min_items` verified deals in 7 days); 404.html; a stable
  sitemap; IndexNow (`python -m dealradar indexnow`, after deploy). Google
  gets the sitemap from the growth report's daily submit.
- **Tracking:** `config.yaml tracking:` holds the public IDs (GA4, Meta,
  TikTok, Pinterest, Google Ads, Bing, site verifications). Empty = no tag
  and no third-party request. Ad pixels never load under Global Privacy
  Control or the /privacy.html opt-out; Meta's automatic event capture is
  off; welcome.html strips the Stripe session id before any tag runs.
- **Growth report:** `growth-report.yml` (daily 12:23 UTC) reads GA4, Search
  Console, Meta and Stripe when their secrets exist (`GOOGLE_SA_JSON`,
  `META_SYSTEM_TOKEN`, `STRIPE_SECRET_KEY`) and sends the report only by
  email (`REPORT_EMAIL_TO`, needs SMTP_*) or the Telegram admin chat. The
  public log shows section names and status only.
- **Source health is private to the operator's view:** it is in each run's
  Actions summary, never on the site (note: Actions pages of a public repo
  are viewable by anyone who looks).
- **Codex (second agent):** Claude writes a task brief to
  `docs/codex-tasks/NNN-*.md` on a branch `codex/task-NNN`, opens a draft PR
  and tags @codex. Codex runs in the cloud environment `phillysaver-cloud`
  (network allowlist: the source hosts it reads) and pushes to that branch;
  Claude reviews and merges. Codex never pushes to the live branch.

## Operator decisions (standing rules; do not undo)
- **Free 8:** every drop unlocks 8 completely new deals; none is free twice
  until every live deal has had a turn (state.db meta `teaser_recent`). A
  drop is a new publish on a `publish.drop_minutes` (15) clock: one that
  lands early waits for its mark; when the mark comes first, the clock says
  "Next drop soon" and the page waits for it; it never re-deals the same 8.
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
- **Codex task 003 (PR #4):** docs-only fix of the locked-stub example in
  docs/paywall.md; also the first test of @codex picking up a GitHub task.
- **GitHub Pages retirement:** remove the Pages deploy steps from fast.yml
  and hourly.yml, then the operator unpublishes it (Settings -> Pages).
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
  - **Paused** (`enabled: false`, reason inline in sources.yaml): dealsplus and
    kinja_deals (DNS), carolinas_and_dollars (hosting suspended), couponflick
    (no feed), hip2save_freebies (redundant).
  - **Back since 2026-10-11 (Codex readers):** bradsdeals, dealsofamerica,
    dealmoon_us, morningsave (HTML readers in fetch/pages.py) and
    jungle_deals (its frontend's keyless JSON API, fetch/jungle.py; the
    schema may change without notice).
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
5. Operator: approve or edit the category-page copy in
   `dealradar/publish/templates/hubs.yaml`; lower `seo.hub_min_items` (10)
   if small categories stay noindex.
6. Operator: paste the tracking IDs (config.yaml `tracking:`, quoted) and add
   the growth-report secrets (Growth Kit artifact has the steps).
7. Google sign-ins don't fire `sign_up` (the Worker's /api/google reply
   doesn't say whether the account is new). Needs a Worker change + deploy.
8. Open legal question from the SEO review: six subreddits come through
   Reddit's Data API into a paid product; Reddit's terms want a separate
   agreement for commercial use. Operator decision.
9. IndexNow records a URL as sent at publish time, before the POST; a failed
   POST is retried within a week (`seo.indexnow_resubmit_days`).
