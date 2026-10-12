# Handoff — state of wowpriced.com (updated 2026-10-12)

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
- **Google Ads (read-only so far):** ad account 441-227-5803 (reached
  directly; manager 586-101-9939 holds no accounts, 497-666-7972 never existed); API access is on Cloud project `wowpriced-511321` (Explorer
  level applied 2026-10-12; developer tokens are sunset). The report's
  `google_ads` section uses the OAuth secrets `GOOGLE_ADS_CLIENT_ID`,
  `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN` (refresh token from
  the OAuth Playground with the project's own web client) and logs only
  Google's error codes. API version pinned in config (`v26`).
- **Source health is private to the operator's view:** it is in each run's
  Actions summary, never on the site (note: Actions pages of a public repo
  are viewable by anyone who looks).
- **Codex (second agent):** since 2026-10-11 Claude runs Codex itself from
  its cloud session: the Codex CLI signed in with the operator's ChatGPT
  plan (device-code sign-in, approved by the operator; the token stays in
  the session's ~/.codex, never in the repo). The session's network allows
  chatgpt.com, auth.openai.com and api.openai.com. Claude writes the brief
  to `docs/codex-tasks/NNN-*.md`, runs `codex exec` on a `codex/task-NNN`
  worktree, reviews the diff, runs the suite and merges. A new container
  needs a new sign-in (`codex login --device-auth`, the operator enters
  the code). Fallback: the operator pastes the one-line task into Codex
  cloud (environment `phillysaver-cloud`); the @codex tag on GitHub
  still answers "create an environment" (Codex bug).

## Operator decisions (standing rules; do not undo)
- **Free site + optional support (Codex task 019, LIVE 2026-10-12):**
  `support.enabled: true` replaces the membership pitch (operator: people
  won't pay; affiliate is the income). Every deal is free. **Join free**
  (header, phone bar, a box after 8 cards, footer) collects only an email
  through the Worker's `POST /subscribe` (unconfirmed until the deals email
  launches, about a month out; `newsletter.enabled` also gates the one
  confirmation email each address gets first). **Support our work**: a
  closable banner after the 8th card (closed for 7 days, guarded storage)
  with the operator's copy, a one-time pay-what-you-want Stripe Payment Link
  (`support.payment_link`, only `https://buy.stripe.com/...`; empty = Join
  only) and the small print "for-profit business: not tax-deductible, buys
  nothing extra". All membership code stays; `support.enabled: false`
  restores the previous pages byte-for-byte. Verified in Chromium (desktop +
  phone): wall renders, Join posts `{"email"}` and shows the confirmation,
  banner appears/closes/stays closed, no membership wording, 0 errors. Two
  fixes found in that check: an `offer()` stub (the ticker called it; the
  wall stayed empty) and the banner edge on phones (the header scrolls away).
- **Membership mode (dormant while `support.enabled: true`):** the rules
  below on open access, Free 8, "$0 today" and cancel wording apply only
  when the membership pitch is switched back on.
- **Open access (2026-10-11, for "a month or two"):** every visitor sees
  every deal in full, nothing blurred (`config.yaml paywall.open_access:
  true`). Sign-up, the trial button, "$0 today" and Log in are unchanged.
  Pages show their open wording (no "members only", no "unlock"); never
  add copy saying the trial is support. To bring the paywall back, set
  `open_access: false`: the free-8 rotation resumes where it paused.
- **Free 8** (while the paywall is closed): every drop unlocks 8 completely new deals; none is free twice
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
- **Get deal:** a deal whose price needs steps (a code, a coupon to clip,
  Subscribe & Save, a rebate, or an in-cart / targeted / new-customer /
  multi-buy / in-store condition) links to the source post that explains
  them, and its card shows "How to get this price" with tap-to-copy codes.
  A plain-price deal links straight to the product (publish/site.py
  `_needs_steps`).
- **Customer emails (changed 2026-10-12):** nothing is sent yet. Join free
  collects addresses for a daily or weekly deals email the operator plans
  to start in about a month (`newsletter.enabled: false` until then;
  `members.email_enabled: false`). Emails never carry Amazon-tagged links.
- **Affiliate (2026-10-12):** affiliate commission is the income. The
  Amazon Store ID goes in `config.yaml affiliate.amazon_tag`; only
  `publish/site.py rewrite_affiliate` tags links (https amazon.com only),
  and the site disclosure switches to `affiliate.disclosure` (FTC + the
  Amazon Associates sentence; keep it visible).
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
- **AI spend under $6/day (Codex task 020, merged 2026-10-12):** the critic
  judges up to `parse.critic_batch_size` (8) deals per Sonnet call with
  reasons cut to 120 characters, and reuses a stored verdict for the same
  Amazon ASIN, condition class and regular price within 2% (`reused_from`).
  Keepa posts and plain-price Woot posts are parsed in code from the API facts
  (`RawPost.structured`), still through `verify_evidence`; their category
  comes from a stored deal with the same ASIN or one Haiku title batch per run
  (`kind="category"`, 60 max, else "other"). Caps unchanged. Estimate before
  launch: ~$0.045 per fast run (was ~$0.156). Check `spend_by_kind` and
  `structured_parse` in the next fast-run summaries.
- **Hourly site check walks the site like a visitor (Codex task 018, merged
  2026-10-12):** `site-check.yml` (hourly) on desktop and phone: home loads
  (cards, clock, tagline, no JS errors), load-more, a category tab and back,
  search, a "Get deal" link's shape (never opened), the trial dialog opens
  (nothing submitted), the yearly choice, content pages + robots + sitemap
  + 3 hubs answer 200, and the publish is under 45 min old. Any failed step
  exits 1 (red run, GitHub emails the owner); photos stay a report. At
  most 40 page requests, 1/s, analytics blocked.
- **Amazon links from posts (Codex task 016, merged 2026-10-12):** for live
  site deals with no photo and no product link (or Amazon without an ASIN),
  the fast lane fetches the first credited post politely (robots, official-
  feed-only policy, 40 posts per run, 10 short links per post), takes the
  page's single Amazon link (or, in a roundup, the uniquely best anchor
  match covering 60% of the item's words), sets canonical + ASIN and lifts
  the photo flags so Keepa fills the photo. One attempt per deal
  (`post_link_tried`); only the link is stored. Rakuten `murl` decodes.
- **The wall loads as you scroll (Codex task 017, merged 2026-10-12):** the
  home page renders 60 cards (PAGE_CARDS) and adds 60 when the "Show more
  deals" box nears the viewport (IntersectionObserver, button fallback);
  search, filters, sort and category reset it to 60, a drop keeps it; a
  ticker click shows up to its card. "Just missed" appears once every live
  card is shown. Verified in Chromium: desktop 7,234 px at open (was
  ~150,000), no scroll jump, 0 errors.
- **Yearly plan (Codex task 015, LIVE 2026-10-12):** `paywall.yearly_price_usd: 49`
  with the Worker's `STRIPE_YEARLY_PRICE_ID` (live price, checked against
  Stripe by every Worker deploy; 0 hides yearly). With it on, the dialog
  and sticky panel offer monthly/yearly (default monthly), disclosures follow
  the plan, and the panel's buy button opens the full-terms dialog first.
  The Worker sells yearly only with `STRIPE_YEARLY_PRICE_ID` (else 409
  plan_unavailable, never a monthly fallback); switching plans expires the
  open session and the previous session id is in the idempotency key so a
  switch back never replays an expired one. MRR counts annual as amount/12.
  Verified in Chromium (desktop + phone): picker, terms, `{plan:"year"}` body.
- **Photo redirects (Codex task 014, merged 2026-10-12):** the fast lane
  follows a photo-less deal's redirect link (60 per run, newest first, one
  attempt ever) when its host is a known tracker (`_SHORT_LINK_HOSTS`) or
  the site of one of the deal's own sources (dealnews.com, slickdeals.net
  ... linking through themselves); retailers, Amazon and bot walls are never
  resolved. An Amazon landing sets the ASIN (Keepa photo); any other
  retailer goes to the og:image pass. Fast runs write "photo gaps" (top
  canonical hosts, counts only) and redirects resolved/tried to the Actions
  summary. The hourly `site-check.yml` loads every live photo in Chromium.
- **Site health in the daily report** (Codex task 012, merged 2026-10-11):
  the operator's email opens with photo coverage, the stores and sources
  missing most photos, a 60-photo load check, minutes since the last publish
  and the home page; a ⚠ line flags coverage under 85%, broken over 5%, a
  stale publish or a bad home page. Broken = 404/410 or not an image only.
- **More photos** (Codex task 013, merged 2026-10-11): the retailer photo
  pass reads any https product page (allow-listed retailers first; blogs,
  trackers, short links, bot walls, listings never), 40 per run; Woot photos
  from all item shapes; the fast lane re-checks 30 stored photos a run and
  clears only proven-broken ones (404/410 or not an image) so they refetch.
- **Codex task 011 (merged 2026-10-11
  deal objects through their shared serializer from 40 to 26 fields; removed
  only fields with no serialized-data reader. `flags` and `alive` remain
  because the existing offline e2e/publish tests read them. Published JSON
  uses compact separators; locked stubs and every retained value are unchanged.
  Added a documented consumer allow-list and compact public/member equality
  test. Offline open-access render of 1,400 varied deals (seven stores, eight
  product types, prices, codes/coupon steps, photos, two source credits each;
  fixed publish time, current config) measured 2,385,669 -> 1,281,965 bytes
  raw and 121,736 -> 103,128 bytes gzip (level 9, mtime 0). All retained values
  and metadata matched the baseline. `.venv/bin/python -m pytest -q` passed:
  1,160 passed, 1 deselected (including offline e2e/idempotency). Changes remain
  merged 2026-10-11. No commit or push made.
- **LLM spend breakdown** (Codex task 009, merged 2026-10-11): each run's
  summary and Actions step summary show calls and USD per kind (extract,
  second_opinion, critic, newsletter_pick, other) and extraction spend per
  source. Use it to bring daily spend to $6 (open task 1).
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
    bradsdeals, dealmoon_us and jungle_deals stayed tripped on their old
    feeds' failures; a source whose method, adapter or URL changes now
    starts with a fresh breaker (fetch/base.py), so they retry at once.
  - **inspiring_savings:** their server returns 500; it retries on its own.
  - **Codex task 007 (merged 2026-10-11):** corrected spoofee to
    `/rss.php` and saving_our_way to `/blog-feed.xml`; paused
    southernsavers_amazon (redundant), slickdeals_blog (feed gone), sidedeal
    (page only), and stl_mommy (invalid host TLS certificate), with the probe
    reasons inline. No other source changed; dealgenius remains as documented.
    Google service-account loading now accepts whitespace and a leading BOM
    and reports invalid JSON or missing key fields using shape only; growth
    logs show that safe diagnostic and keep other exception messages private.
- **Page copy:** the drop cadence on every page comes from
  `publish.drop_minutes` through the `{{DROP_MINUTES}}` token (Codex task 008).
- **Photos:** Codex task 005 (merged 2026-10-11) decodes the Deals of
  America tracker destination offline for the ASIN at ingest and for the
  retailer-photo pass; a one-time repair gave existing deals their ASIN and
  lifted the photo retry flags. Tracker links, affiliate credit and dedupe
  keys are unchanged. Codex task 006 (merged 2026-10-11): the fast lane
  resolves photo-less `on.ltk.com`, `goto.target.com`, `amzn.to` and `a.co`
  links once each through the PoliteFetcher before the photo passes (at
  most 20 per run; flag `link_resolve_tried`), keeping dedupe keys.

## Open tasks
1. Bring daily LLM spend to $6 by 2026-10-14 (task "cut AI spend"). Ideas:
   - a cheaper critic for clear cases;
   - a per-run critic cap;
   - tuned intake.
2. Done (2026-10-11): the Stripe product description reads "wowpriced
   membership. Cancel any time." (confirmed by `stripe check`). When the
   paywall returns: "Every deal on the wall unlocked, with new deals every
   15 minutes. Cancel any time."
3. Done: the Customer Portal is active (1 configuration), so "Manage
   membership" works.
4. Any change under `cloudflare/subscribe-worker/` goes live only after the
   `deploy subscribe worker` workflow runs (fast/hourly deploy the site
   only). It was run for the daily top-10 confirm page on 2026-10-11.
5. Operator: approve or edit the category-page copy in
   `dealradar/publish/templates/hubs.yaml`; lower `seo.hub_min_items` (10)
   if small categories stay noindex.
6. Operator: paste the tracking IDs (config.yaml `tracking:`, quoted) and add
   the growth-report secrets (Growth Kit artifact has the steps). GA4 done
   2026-10-11 (G-MHLJXK2WHF, property 558475393); Meta Pixel done
   (1634493648070496; automatic advanced matching and automatic events off);
   TikTok, Pinterest, Google Ads and the secrets still to come.
7. Done (Codex task 010, 2026-10-11): `/api/google` returns `new_account`
   and the page fires `sign_up` {method: google} only then. Live after the
   `deploy subscribe worker` workflow runs.
8. Open legal question from the SEO review: six subreddits come through
   Reddit's Data API into a paid product; Reddit's terms want a separate
   agreement for commercial use. Operator decision.
9. IndexNow records a URL as sent at publish time, before the POST; a failed
   POST is retried within a week (`seo.indexnow_resubmit_days`).
