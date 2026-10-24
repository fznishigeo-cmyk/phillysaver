# Handoff — state of wowpriced.com (updated 2026-10-21)

Keep this file current: whoever stops working updates it in their last commit.

## Starting a new session (if the chat is lost)
Nothing lives only in the chat: code, workflows, briefs (`docs/codex-tasks/`),
this file and the encrypted state (`data` branch) are all in the repo, the
site is on Cloudflare, keys are GitHub secrets, and the schedules keep running
on their own. A new session reads CLAUDE.md, AGENTS.md and this file, then:
- **Working agreement with the operator** (non-technical, runs the business):
  answer first, fast, in plain words and short bullets; ask nothing that can
  be decided; put the important answer in the last message of a turn; work in
  the background and say so in a line. "What do you think" = discuss, don't
  build. Every published number has a check behind it; report failures
  plainly.
- **Codex** does routine coding (`codex exec -m gpt-6.1-sol`, effort
  `xhigh`, `max` only if xhigh fails; operator 2026-10-12: "A+ quality" while
  the free usage resets last, use the earliest-expiring reset first); Claude
  writes the brief, reviews every diff, runs the suite and pushes.
- **Recreate the session routines** (they fire into the session that made
  them): hourly recap at :55 from 7:55am to 1:55am New York (none 3-8am);
  keys reminder at 7:53, 13:53 and 19:53 New York until every key is in.
- **Never** paste a key in chat or a log; the operator saves keys as GitHub
  secrets. Probes (`*-probe.yml`) check each key read-only.

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
  under `seo.hub_min_items` current public live offers); 404.html; a stable
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
  Google's error codes. API version pinned in config (`v25`).
- **Keyword volumes (manual, read-only):** after review, select Actions →
  keyword volumes → Run workflow (or `gh workflow run keyword-volumes.yml`).
  Results save only to `keywords/<UTC date>.md` in `growth.reports_repo` using
  `REPORTS_REPO_TOKEN`; public logs show counts, save status and error codes.
  Explorer access is expected to refuse Keyword Planner: exit 1, no private
  file. Owner: complete brand verification on the OAuth consent screen, then
  apply for Basic access from the Google Ads API page in Cloud Console.
  First real run remains for Claude after review; record the observed codes here.
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
- **Amazon used withdrawn (operator 2026-10-19 ~03:40 UTC, task 319):**
  Stop publishing Amazon used/open-box/Warehouse/Resale offers and stop their
  Keepa discovery/recheck spend. Preserve stored rows/history; Woot and other
  stores retain used/refurbished offers. New Amazon discovery favors women,
  beauty, some kids, alluring clothing and everyday essentials, with existing
  adult exclusions and real 90-day price-drop evidence. Top price/Buy Box
  confirmations may be up to 2 hours old; next wall targets <=6 hours, rest
  12–24 hours; existing budget caps and hidden recovery remain.
- **Public price history withdrawn (2026-10-17 ~04:30–04:35 UTC):** the operator
  reports Amazon's email disallows historical price elements on Amazon listings,
  and drops public price history for every store. This supersedes the public
  history display approvals below. Hidden backend history may still filter/rank
  products. The finder shows today's price and evidenced unit price only: no
  historical lows/highs, averages, patterns, charts, counters or history links,
  including JSON and alt text. Current-price confirmation clocks remain.
- **"AI sells" (2026-10-16):** public copy may say "AI" (Deal Check subhead,
  the AI take label, how-it-works#ai). Keepa, Claude, Codex, LLM, Python and the
  other internals stay out of public copy (tests/test_public_copy.py).
- **Walmart history everywhere (2026-10-16, operator's call with Walmart):** Deal
  Check `checker.stores.walmart: history`, AI takes and the extension include
  Walmart; `store_policy.walmart` can switch it to live-only in one line. eBay stays
  live-only because listings end.
- **Amazon price history (updated 2026-10-17, Amazon email, task 274):**
  ships `amazon_history: hide`; no Amazon history links, charts or statistics.
  This supersedes the earlier phone approval below; watches remain off.
  Earlier Associates phone support (2026-10-16, reference amz378_998)
  approved 90-day price history charts, lowest recent price
  next to Amazon links, Keepa as the data source, our own history kept longer
  than 24h only if private and secured, and "checked X minutes ago" prices.
  Previously shipped `amazon_history: show`. Follow-up call, same reference
  (2026-10-16 evening): the browser extension MAY show our history/verdict on
  Amazon product pages; Amazon price-drop emails are NOT allowed, so
  `amazon_watches: "off"` is permanent. Instead: a daily or weekly
  best-deals newsletter (operator; launch in 1-3 months). Newsletter links
  go to wowpriced pages, not tagged Amazon links (Associates rules on links
  in email: verify before launch). Associates account approval waits for 3
  qualifying sales.
- **Walmart price history and alerts (2026-10-16, operator spoke to Walmart):**
  Walmart said it is fine to show Walmart price history and the lowest recent
  price, and to send Walmart price-drop alerts (the written agreement bans
  tracking/alerts by default; this is the operator's verbal permission —
  keep any reference number or email with the operator's file). Deal Check
  moves Walmart from live-only to history.
- **Daily quality loop (2026-10-13):** "be the senior engineer; refine, don't
  reinvent; check every day without being told." Routine "Daily quality
  loop" (6:17am New York) reads the daily `mobile-audit` run (phones first:
  70-80% of traffic), run health and, on Mondays, the `competitor-benchmark`
  run (Google PageSpeed vs ~63 competitor/partner sites), then ships 1-3
  small Codex-built, Claude-reviewed refinements.
- **AI budget (2026-10-13):** $6/day normal ($6-10 acceptable). Up to
  $15-20/day only for truly necessary work until ~2026-10-17 (e.g. the first
  partner-newsletter backlog), via `spend_cap_boost_*`, then back. More
  later once affiliate income arrives. **2026-10-14:** $10/day boost through
  Oct 21 UTC, then $6 automatically; operator wants the code cheap enough
  that $6 covers a full day ("write CODE so we can stay within $6").
- **Email on the operator's behalf (2026-10-13):** never send. Show the
  text in chat; after the operator approves, create a Gmail draft (on the
  original thread when replying); the operator presses Send.
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
  `_needs_steps`). Exception (operator, 2026-10-12): when the store link
  is an https amazon.com link and the Amazon Store ID is set, Get deal goes
  to Amazon with our tag; the card keeps the code, the steps and the "via"
  link to the post.
- **Customer emails (changed 2026-10-12):** nothing is sent yet. Join free
  collects addresses for a daily or weekly deals email the operator plans
  to start in about a month (`newsletter.enabled: false` until then;
  `members.email_enabled: false`). Emails never carry Amazon-tagged links.
- **Affiliate (2026-10-12):** affiliate commission is the income. The
  Amazon Store ID `wowpriced-20` (live 2026-10-12) is `config.yaml affiliate.amazon_tag`; only
  `publish/site.py rewrite_affiliate` rewrites links: Amazon via our tag
  (https amazon.com only), every other eligible retailer via Sovrn Commerce
  (`affiliate.sovrn_enabled`, validated public `affiliate.sovrn_key`).
  Source posts and credit links stay unchanged; page-specific
  `affiliate.link_overrides` win. Emails never get our tags or Sovrn wrappers.
  The site disclosure uses `affiliate.disclosure` (FTC + the Amazon
  Associates sentence; keep it visible).
- **"$0 today":** next to a trial button on the main screen the only price
  line is a "$0 today" badge. The footer opens with "Membership —
  $9.99/month", and the full terms stay in the dialog and on Stripe's page.
- **Cancel wording:** no "cancel" reminders on the main screen. "Manage
  membership" sits in the footer only. The full auto-renewal terms stay in
  the checkout dialog, on Stripe's page, on the welcome page and on the help
  pages (legal requirement).
- **Deals stay until genuinely over.** A live deal is never removed for its
  age (Amazon "used - like new" offers last until someone buys them). A
  deal is confirmed gone only after two agreeing Keepa/Woot API readings
  at least an hour apart, or Woot's own API end date. A post saying it
  ended or a stated expiry is unconfirmed: Amazon ASINs and Woot offers
  become suspects for API rechecks; other stores are hidden. Unconfirmed
  deaths are never shown as gone. Confirmed-gone cards stay greyed and
  unclickable inline for 24 hours, outside the first 8, at most one per 8.
  Dead deals are deleted 14 days after they die.
- **No trial reminder emails.** Members cancel by emailing
  hello@wowpriced.com or through Stripe's portal.
- **LLM spend:**
  - $15/day until 2026-10-14; it then falls back to $6/day by itself.
  - The goal is $6/day.
  - Never skip the critic for Keepa deals: a review showed inflated bases.

## Pending approvals (track until done; the "Keys + approvals tracker" routine nudges)
- **eBay Partner Network:** APPROVED 2026-10-13, campaign 5339221823
  (`affiliate.ebay_campaign_id`): every https ebay.com Get deal link carries
  our EPN parameters (`rewrite_affiliate`), bucket `ebay_epn`, earns.
- **eBay developer API** (`EBAY_CLIENT_ID`/`EBAY_CLIENT_SECRET`), **Best Buy
  API** (`BESTBUY_API_KEY`): pending.
- **Sovrn:** links tracked; full approval pending, then direct deposit and
  the reporting key.
- **Impact.com:** marketplace declined 2026-10-13, support ticket 895150
  (reason + manual review). The account still joins brands through their own
  sign-up pages: Walmart, Target, Home Depot, Kohl's (skip Lowe's, 0% pay).
  Each approval → one tracking link → wire that store.
- **Pinterest Standard access:** APPROVED 2026-10-14; operator reconnects the
  production account (sandbox tokens do not work in production).

## In flight
- **Best deals today category mix (Nishi 2026-10-24; uncommitted):**
  `publish.front_page.best_today_mix` ships ON for `/best-deals-today/` only:
  sexy 3, wow 2, seasonal 4, pets 3, kids 1, health 3, essentials 5, beauty 5,
  home/kitchen 4, rest 0. Named title words precede categories; adult exclusions
  still win. Seasonal words combine every active appeal window on the build's
  New York date with configured fallback words. Eligible named supply fills
  shortages before rest; picks precede score-ranked extras within each bucket.
  The adapter reuses `front_mix.validate`, `bucket` and `schedule`, leaving that
  source unchanged because its bytes feed the homepage's version stamp. The
  homepage mix/labels stay unchanged; OFF/absent use the original selection.
  Historical config pins remove the new knob. The 58 focused checks pass;
  OFF matches all 172 original-checkout publication files byte-for-byte,
  including version stamps; ON changes only the best-today page. Requested
  related run: 4,861 passed, 23 skipped; all 14 initial failures resolved:
  the copied deploy fixture now includes the adapter, and the Stripe workflow
  fixture uses offline urllib responses instead of forbidden sandbox sockets
  (both affected files: 83 passed). Two existing 50ms timing checks pass alone
  (2 passed). Additional matching JS test wrappers: 194 passed, 21 skipped;
  search-results Node test: 1 passed. Full-suite verification remains with the
  operator's agent. No network, commit or push.
- **Hourly Keepa overflow (dad61e5, 2026-10-23; ships with relR):**
  Hourly #352 admitted only 10 Buy Box candidates despite a 350-token surplus:
  ordinary cadence excluded other top deals before the surplus queue. New
  strict `keepa.hourly_surplus_buybox` defaults false and ships true, admitting
  early top reads within the existing idle lookahead only on hourly runs.
  Their separate surplus label enforces overflow/premium costs in planning
  and transport; ordinary published/Buy Box work keeps priority and funding.
  Fast behavior, visitor 150 + next-run 250 reserves, 20/min plan, deadlines,
  paid-work stop, evidence rules and money caps remain. Mocked full-bucket
  checks spend 400 forecast tokens on top Buy Box then rest; rollback, reserve,
  time, retry and same-run checks pass. Related-file selection: **331 passed
  in 672.27s**, including offline rerun idempotency; final ordering-guard
  follow-up: **17 hourly checks passed in 27.17s**. Diff check passes. Remaining:
  measure live overflow/candidates after deploy.
- **"Best today" home pill (A48, branch `best-tab`, 2026-10-23; ON, committed,
  not pushed):** operator ~20:00Z: "yes add that button" (/best-deals-today/
  had no homepage link). `best_today_tab: true` (strict bool, default false;
  `publish/best_today_tab.py`): a `.cattab` link right after Trending in the
  home row, no `data-i`, so renderTabs redraws it in place and binds filters
  only to `.cattab[data-i]`. No GA4 event (no generic nav-link event exists).
  Off: no byte (`pre_best_today_tab`). Real Chromium: phones keep one sideways
  row (52px), 1280 wraps to two lines either way; desktop windows ~920-1030px
  wide get a third line (+51px, static from first paint). Phone budget: home
  HTML gzip +61 bytes (fixture headroom 1,012 → 951: under the ceiling, below
  the tests' 1,000 reserve, which was already nearly used up); the two reserve
  tests pin it off and `test_phone_budget_cost_is_bounded` caps it (HTML <= 64,
  inline JS <= 40). Operator call: accept, or win bytes back elsewhere. Tests
  `tests/test_best_today_tab.py` (runs `tests/best_today_tab_browser.cjs`).
- **Phone first screen (branch `phone-first` from relR `635c755`, 2026-10-23; OFF, not
  pushed, waits for the operator's approval phrase):** Nishi ~21:50Z: "The fix: the first 3
  deals and their buttons fit on the first screen, with fewer words per card." (laptops ~36
  of 100 home visitors tap a deal, phones ~2). `phone_first_screen: false` (strict bool,
  default false, needs keep_first_cards; `publish/phone_first_screen.py`). Phones only
  (<=640px), home only: tighter header (48px search, the two tool buttons side by side at
  44px, no empty status line, 24px tagline row, tighter tabs/Amazon strip/filters); one deal
  per row, photo left; the headline sentence (a repeat of title, price and store) hidden
  where the tier badge shows; a step deal whose How box holds only steps/codes shows the tool
  pages' "How to get this price" link to its deal page instead (boxes with rebate/price
  varies/shipping text stay). Price, store, credit, "Price not confirmed", the Amazon
  checkout-price line and every disclosure stay; laptops pixel-identical. The hidden ticker's
  CSS leaves the home page while it is hidden (the phone CSS budget had 23 gzip bytes left):
  test_drop_fresh's fixture OFF -> ON home HTML gzip 76,348 -> 76,203, CSS 14,890 -> 14,496
  (limit 14,913). Real Chromium, cards with Get deal fully on the first screen OFF -> ON:
  390x844 0 -> 3, 375x667 0 -> 2, 430x932 0 -> 3 (realistic 24-offer scratch fixture;
  budget fixture 430x932 2 -> 4); CLS 0, first cards kept. Pin `pre_phone_first_screen`
  (in pre_social_profiles, + raw pops); test_keep_first_cards builds with it off. Tests:
  `tests/test_phone_first_screen.py` (+ `phone_first_screen_browser.cjs`). To ship: flip
  config.yaml and that file's shipped assertion; the related test files pass with it on.
- **Phone home LCP / taps (branch `phone-lcp`, 2026-10-23; `keep_first_cards` ON,
  committed, not pushed):** the drop module's first render replaced the server's
  whole card wall (`content.innerHTML`) with the same cards, and its open How box
  became the LCP; an older publication swapped in a different drop. Now a
  fresh-view render of the cards already on screen patches them in place (same
  final markup), a stale mark keeps the painted drop until the next mark, and the
  home wall's static cards draw the module's How box. Real Chromium 390x844 / 4x
  CPU, 1,400-offer fixture: LCP 1308 → 368 ms (= first paint), 90/90 → 0 server
  cards lost, first 8 kept 0 → 8; stale case 1112 → 252 ms, no drop swap. Off:
  1,411 files byte-identical (`pre_keep_first_cards`). Numbers and reasoning:
  `docs/research/phone-lcp-2026-10-23.md`; tests `tests/test_keep_first_cards.py`
  (runs `tests/phone_lcp_browser.cjs`). Next: Lighthouse mobile and the phone
  Get-deal rate after deploy. `test_drop_fresh.py::test_emitted_fresh_logic`'s
  50 ms timing check also fails at `9459a61` under `-n 4` on a 4-core box; it
  passes serially.
- **Tools merge (C36/A42, branch `tools-merge`, 2026-10-23; ON):** operator
  "CONFIRMED GO AHEAD, please use those reference videos", "push it through
  when its done", then ~01:55Z keep the old addresses for organic search.
  `tools_merge: true` (strict bool, default false; `publish/tools_merge.py`).
  Fake Deal Spotter takes Fakespot's home (order how-it-works, stores, dark
  band, "Love" band with "See today's best deals" -> /best-deals-today/, grey
  "trusted guide" band with tabs Price grade / Verified deals / Price
  details / Find discounts and HTML illustrations, footer columns wowpriced /
  Tools / Support / Social) and report (blue analyzer + "wowpriced recommends"
  strip; header photo/title/"Checked on Amazon N min ago"/category with the
  big A/B/C box, "What does this mean?" and Get this deal on Amazon; rows
  Price details, Overview, On wowpriced, Top sellers like this, Review
  signals; "Today's top deals" sidebar; "Was this analysis helpful?" ->
  GA4-only `tool_feedback`). No price-history row and no past-price wording
  anywhere (2026-10-17 rule; tests/test_no_price_history_shipped.py): the
  grade may use the hidden avg90 but pages describe it by today's price, the
  list price and our verified deals. /amazon-deal-checker/ and
  /amazon-discount-finder/ are keyword landing pages (own title, description,
  H1, intro, FAQ, self-canonical, in the sitemap) whose tool is the spotter;
  no 301s. Typed words never call /api/search: one box with the Discount
  Finder's Amazon discounts link, the highly rated link and matching deals
  from /deals.json. Review Checker drops the old tools from its menus and
  shows one "Price check: B — Good price" row (letter + label): search.js
  computes the grade from the review lookup's own stats when
  search-config.json has `tools_merge` (no extra token).
  Needs both tools on (else inert). Homepage: the blue button is
  "Fake Deal Spotter →" (/deal-spotter/?q=), the purple one "Review Checker →"
  (/review-checker/?q=); events unchanged per slot plus `tool`. The live
  site check takes the merged path when config.yaml says so. Pins:
  `pre_tools_merge` (+ raw pops). Ads final URL stays /amazon-deal-checker/.
- **Typed words wait through the analyzer (2026-10-23 ~20:05Z; ON, branch
  `words-analyzer`):** operator "both products, no matter if they search a
  keyword or a url should go through the analzyer process", then ~20:10Z "no
  this will still come up. just make them wait for it". `tool_words_analyzer:
  true` (strict bool, default false; needs tools_merge; `publish/words_analyzer.py`,
  templates `tool-words-analyzer*.js`). On both tools typed words open the
  link's analyzer box (same look, ×, focus, the spotter's recommends strip)
  for the link's wait (10-25 s), status "Searching for deals, please wait.."
  and steps Searching our verified deals / Finding discounts on Amazon /
  Finding top-rated picks, then today's words box unchanged. Still no
  /api/search call; adult words: no box, no analyzer; × = no box (as a link).
  No new GA4 event (the link flow has no open/complete event). The Discount
  Finder intro drops "so the buttons appear right away". The live site check
  waits 40 s for the words box (was 20 s). Pins: `pre_tool_words_analyzer`
  (+ raw pops). Tests: `tests/test_words_analyzer.py` (+ `words_analyzer_browser.cjs`).
- **Softer search buttons (2026-10-23; ON):** `soft_buttons: true`
  (`publish/soft_buttons.py`): same hues ~15% less saturated, 6% darker,
  glows 40% weaker: the --pill-colors/--pill-glow values are rewritten in
  place in header-polish.css / page-polish.css (no added bytes; the phone CSS
  budget is nearly full); white text >= 5.4:1. Pin `pre_soft_buttons`.
- **Shipping note (C16, 2026-10-23; not behind a switch):** a Buy Box price
  with no item/shipping split carries `price_includes_shipping` and the
  spotter shows "incl. shipping" under it; scripts/tools_live_check.js prints
  `shipping=`.
- **Tool-page events (2026-10-22, branch `tool-events`, not pushed):** on
  /deal-spotter/ and /review-checker/ every click on an amazon.com link in
  the tool area (photo, title, grade, buttons, typed-name button) sends the
  site's `deal_click` {store: Amazon}, the event the operator will import as
  the Google Ads conversion. Each graded link result sends GA4-only
  `tool_grade` (deal_a..c, review_a..c; never for typed names), behind
  `tool_grade_event` (config.yaml true, default false; +35 gzip bytes on the
  home page, 1,063 of headroom left). Recorded hashes: `pre_tool_grade_event`.
  The pages' "Today's top deals" cards are outside the tool area and send
  nothing. Left: review and merge.
- **Sticky Get deal bar (branch `sticky-buy`, 2026-10-22; ON, not pushed):**
  operator: "yes great idea implement it", then "a bit more transparent ...
  less obtrusive". `sticky_buy_bar: true` (strict bool, default false).
  Phones only (<= 640px): each `/deal/<slug>/` page pins a 56px frosted-glass
  bar (price + the main Get deal link with the same href/target/rel/data-*,
  so `deal_click` and click-signal count it; no new event) once the main
  button scrolls out of view above (IntersectionObserver), with a .2s fade,
  safe-area padding and matching body padding so the footer stays readable.
  Laptops unchanged; nothing opens without a tap. `publish/sticky_buy_bar.py`;
  history fixtures pin it off via `pre_sticky_buy_bar` and raw pops.
  Preview: wowpriced-reports `ops/shots/2026-10-22/sticky-button-preview-v2.png`.
  Remaining: review, merge, deploy.

- **Add to Amazon cart (2026-10-22; built, ships OFF, branch `add-to-cart`):** the
  operator approved the button; `add_to_cart: {enabled: false}` (strict bool) until
  the operator approves the preview (`ops/shots/2026-10-22/add-to-cart-*.png` in the
  reports repo). `publish/add_to_cart.py` links Amazon's Add to Cart form,
  `https://www.amazon.com/gp/aws/cart/add.html?ASIN.1=<ASIN>&Quantity.1=1&AssociateTag=<affiliate.amazon_tag>`,
  target `_blank`, rel `sponsored nofollow noopener`: a plain link the shopper clicks
  (no form, script submit or prefetch). Deal pages: an outline button right after
  Get deal, only when Get deal is our tagged amazon.com product link and the offer is
  new (`amazon_used.used_offer`); it uses that link's ASIN, so a th=1&psc=1 child adds
  its exact size. Fake Deal Spotter: a secondary button under "Get this deal on
  Amazon" on a graded link, never a typed name. Deal pages' `deal_click` carries
  `button: "add_to_cart"` for it; the Spotter fires nothing, as for its main button.
  OFF/missing is byte-identical to 96bbb2d. Amazon's rules were read 2026-10-22 only
  through search excerpts (amazon.com hosts are blocked from the session): open the
  pages before switching on. Commission: items added during the 24-hour session
  count if ordered within 89 days (policies) / before the cart expires, "usually
  after 90 days" (help). To ship: set `enabled: true` (history pins keep fixtures
  OFF), run the suite, push.
- **Deal Spotter (Codex task 366, 2026-10-22; enabled, not deployed):**
  `deal_spotter.enabled` ships true. New `/deal-spotter/` and
  `/fakespot-alternative/` pages, sitemap entries, footer discovery and tool
  menus. Original HTML/SVG illustrations, animated stepper/demo, phone
  carousel, published deal counts/cards, FAQ schema, cookie notice and the
  existing install flow. The input parser, affiliate/variant links, similar
  discounts and price disclosure come from the existing checker code.
  Shareable ASIN results, 10–25-second real-time progress gated on response,
  modal keyboard handling, Escape cancellation, copy/share and reanalysis.
  `functions/api/deal-grade.js` owns deterministic A/B/C math; `grade=deal`
  decorates the existing search lookup, cache, reservation and settlement.
  Ordinary and graded requests share one cache; its private selected-series
  `avg90` is stripped from every browser response. Current published verified
  membership is checked again on cache hits. `stats=1, history=0`, all search
  caps and token options stay unchanged. Task 367 can add `grade=reviews`
  separately; no review grading was implemented here.
  OFF/missing/back-to-OFF matches three pre-edit whole-publication hashes;
  historical fixtures opt out via `pre_deal_spotter`, without re-pinning.
  ON leaves homepage/card bytes unchanged apart from requested footer links.
  Validation so far: 22 focused Python tests passed; 80 backend Node tests
  passed, plus emitted UI timing/cancel/reanalysis/share/parser checks. Task
  206's shipped phone budget passed. Full `.venv/bin/pytest -q -n 3` is in
  progress; final results will replace this note. Real Chromium is blocked by
  sandbox `setsockopt: Operation not permitted`; the new harness covers all
  four requested viewports, light/dark, states, focus, contrast, geometry and
  CLS and writes screenshots only to its supplied tmp_path. No screenshots
  could be produced here. Git branch/commit writes are blocked by the shared
  Git directory's read-only filesystem. No push, deployment or live API call;
  `.codex-ref` remains ignored and untouched. Remaining: full-suite result,
  native visual review, commit in a writable Git checkout, review/merge/deploy.

- **Review Checker (Codex task 367, 2026-10-22; implementation ON, not deployed):**
  Standalone `/review-checker/` and `/reviewmeta-alternative/`, deterministic
  A/B/C review signals only, no stars/review-count numbers or past prices.
  The existing search request uses `grade=reviews`, `stats=30`, `rating=1`;
  the unchanged shared reservation/receipt accounts for the possible extra token.
  Pure logic lives in `functions/api/review-grade.js`; the integration hooks in
  `search.js` are marked Task 367 for the sibling task 366 merge. Natural pace
  uses actual `atIntervalStart[17]`, never the misleading avg30 subtraction.
  Current product prices and affiliate links retain existing checks/builders.
  Pages reuse the install button/script and newsletter form/subscribe endpoint.
  Header input/Menu, CSS illustration, real published deal cards, passed-check
  report, FAQs/schema, footer discovery and sitemap are included. No home/card
  changes outside the required footer link. Phone tool navigation uses a Menu.
  Cookie acknowledgement key: `wp.tools.cookies` (value `1`), intended to be
  shared with task 366; watch-bar dismissal: session `wp.review.watch`.
  Social profile config is empty: icons are labelled as awaiting profile links,
  never linked to invented accounts. `/deal-spotter/` depends on sibling 366,
  and the current standalone publication audit reports that missing route.
  OFF pre-edit tree hash `dcccf93f1d7c69045ed7492697dcde499dd5369372d8eb6adc697a9a6751b037`
  is preserved; missing/OFF/ON/rerun/back-to-OFF tests and historical helpers
  pin the feature OFF without changing older expected hashes.
  Validation is in progress; final counts will be recorded before handoff.
  **Environment blockers:** Chromium launch is denied by the sandbox
  (`setsockopt: Operation not permitted`), so native screenshots/CLS/visual AA
  remain unmeasured here. The four-size/two-theme harness writes only tmp_path
  and reports an explicit skip if Chromium cannot launch. Git branch creation
  failed because `/home/user/phillysaver/.git` is read-only; committing in this
  worktree is also unavailable. No push, deployment, paid calls or key access.
- **Off-site unseen expiry (Codex task 365, 2026-10-21; uncommitted, OFF):**
  Strict `retention.offsite_unseen_days` defaults/ships null; integer 1–3650 opts
  in. Current render selections, retained pages, public aggregate contributors
  and live IndexNow receipts protect rows; incomplete mappings keep every
  non-dead row. Uses existing 64-row transactions, cursor, run allowance and
  shared maintenance budget with 337 OFF/ON. Sightings/recheck samples expire;
  independent product/history facts and all send/replay guards survive, with
  lifetime yield anchors restored on re-sighting. OFF matched pre-change DB
  and publication bytes in three modes. Synthetic 33,919-deal measurement:
  N=7 saves **2,554,343 gzip bytes**, N=3 **6,879,986**, eight bounded runs each;
  production savings/timing remain unmeasured. See
  [measurement and dependency audit](research/offsite-expiry-365.md).
  Relevant regression run: **667 passed, 1 browser skip**; final publication
  recheck **392 passed, 1 browser skip**, then **4/4** expanded guard cases.
  No full suite, live calls, commit or push;
  Claude owns review/full suite, operator owns enabling the switch.
- **Homepage buttons to tools (Codex task 344, 2026-10-20; uncommitted):**
  Strict `buttons_to_tools` defaults false/ships true; three pre-edit OFF hashes retained.
  Home/event buttons use native tool links with raw `?q=`; tools auto-run once per load.
  Finder exits use coupons (210906366011); Checker lead uses similar discounts (23566065011),
  shortened to six title words for links. Task 342's exit/cards, Enter and clean canonicals stay unchanged.
  Tracking: `search_analyze`/`search_discounts` with `to_tool:true`; lead `search_similar_deals`.
  Validation: **296 distinct targeted passes, 1 Chromium skip**; final **19/19** recheck passes
  after isolating the legacy tracking test. Node syntax/VM checks and `git diff --check` pass.
  Shipped phone HTML gzip **75,766 → 76,009 / 77,360 bytes**, **1,351 spare**.
  Removed owned `/tmp/b344` and scratch. No full suite, commit or push. Native Chromium remains:
  `buttons_to_tools_browser.cjs` (390/1280 navigation/no scroll) and updated `tool_pages_browser.cjs`.
- **Tool pages v2 + home links (Codex task 339, 2026-10-20; uncommitted):**
  Strict `tool_pages_1020` defaults false/ships true; phone enum ships `chips`,
  with `line` available. OFF preserves pre-edit bytes/pins and task 332 behavior.
  Linear 10–25s clock, 99% hold/+10s cap, no submit scroll, no emitted meters,
  distinct Checker cloud, factual badges, AI headings/FAQ/schema and home links.
  Shared ticker CSS plus removal of unused support-home membership CSS keeps
  HTML gzip **75,676 chips / 75,362 line**, headroom **1,684 / 1,998 bytes**;
  CSS **14,796 / 14,913 bytes**. AI copy avoids unproven hourly volume and
  universal store verification. Targeted coverage **233 passed, 4 browser skips**;
  final affected selection **17 passed**, fake-clock logic and Node syntax green.
  Native screenshots/audits remain with the operator (`tool_pages_browser.cjs`,
  `tool_pages_1020_home_browser.cjs`): phone `headerH/firstCardTop` targets
  **chips 182/388; line 182/416**, not measured here. Removed owned `/tmp/b339`
  and inspection scratch. No full suite, commit or push.
- **Plain See all copy (Codex task 340, 2026-10-20; uncommitted):** Strict
  `fresh_more_plain` defaults false/ships true with the operator's quote.
  Static and browser buttons match the header's rough count (exact/plural
  below 100, hundreds rounded down, capped at `1,000+`); the rotation
  paragraph is absent, with no empty `<p>`. Button classes, click
  handling, inventory, drop selection and timer stay unchanged. A small
  ON-only controls fragment preserves the legacy fresh module's source/hash
  inputs; its replacement asset has a matching content-hashed import.
  Three pre-edit whole-publication OFF hashes are unchanged; historical pins
  disable this flag without changing recorded hashes. Count/parity tests cover
  85, 999, 1,000, 1,381 and 2,400 plus plural/rounding boundaries and dead rows.
  Fixed phone HTML gzip is **76,274 bytes**, **1,086 bytes headroom**.
  Targeted pytest: **284 distinct passed, 1 Chromium skip**. Three focused
  serial reruns passed after correcting the new harness's event-listener stub
  and isolating the existing 50 ms rotation benchmark from parallel contention.
  All three changed/new JS/CJS files pass `node --check`; `git diff --check`
  passes. Used `--basetemp=/tmp/b340`; that directory and owned baseline scratch
  removed. No full suite, commit or push. Git's shared directory is read-only, so branch creation
  was blocked; edits remain in the supplied worktree for review.
- **Yellow search exit + clickable cards (Codex task 342, 2026-10-20; uncommitted):**
  Strict `search_discounts_button`/`card_click_1020` default false/ship true with operator quotes.
  Yellow results/recovery exit shares All Discounts URLs, pasted-link names and `search_discounts` tracking.
  Native Get deal stretches via CSS above badges; other controls stay above it; gone/no-link cards unchanged.
  OFF pins retained. **260 distinct targeted passes, 1 Chromium skip** after serial rechecks; Node/diff checks pass.
  Phone gzip: **75,812 / 77,360 HTML (1,548 spare)**; JS **51,176 / 53,767**,
  CSS **14,585 / 14,913**, JSON **90,836 / 103,976**; final stacking/payload checks pass.
  Glow/page harnesses cover 390/1280 light/dark URLs, popups, title/heart and screenshots; native run remains with operator.
  Owned `/tmp/b342*` scratch removed. No full suite, commit or push.
- **Tool heroes + header Join (Codex task 335, 2026-10-19; uncommitted):**
  Approved mock CSS behind strict `tool_pages_hero` (default false, shipped true);
  OFF preserves a69031d bytes. Meter/example and six-photo cloud use only final
  verified public deals; gauge math stays in savings.py. Header Join lazily opens
  the shared /join card/handlers in an isolated dialog, with /join href retained.
  Fixed phone HTML gzip **76,302 → 76,332 bytes**, **1,028 bytes headroom**;
  targeted suite **173 passed, 4 Chromium skips**; final focused and Node checks pass.
  Native screenshots/focus/overflow remain with the operator.
  Browser harness covers 360/390/412/1280/1440 light/dark and no-JS Join; hourly
  Join check updated. No full suite, commit or push.
- **Remove zero-match Amazon pill (Codex task 332e, 2026-10-20; uncommitted):**
  With `search_buttons_1019` ON, the recovery Compare/See-all pill is now a
  plain `Search Amazon for "x"` link: the same pill was also the zero-zero
  fallback. Its styling wrapper and button class are gone, including inline,
  lazy and search-more-disabled paths; Analyze error exits are plain links too.
  Live Amazon cards and their View on Amazon links, fewer-words hint, Clear
  search, browse chips and the approved search buttons remain. OFF keeps its
  existing bytes and recorded hash pins. Python/emitted-logic coverage checks
  live, empty and error states; glow/more/Amazon browser harnesses assert pill
  absence and a plain fallback when ON, retaining legacy assertions when OFF.
  Targeted search-buttons/glow/more/Amazon pytest: **91 distinct passes,
  2 Chromium skips**; the final search-buttons run is **16 passed** after
  correcting the harness's paged-URL assumption for search-more OFF. The phone
  payload guard and recorded OFF-byte checks pass. All four changed CJS files
  pass `node --check`; `git diff --check` passes. Used
  `--basetemp=/tmp/b332v` and removed it after validation. Native browser review
  remains with the operator. No full suite, commit or push.
- **Search click request timing (Codex task 332d, 2026-10-19; uncommitted):**
  Case (b): `/api/search?q=phone%20holder&page=0` is typing-as-you-search,
  scheduled by the 150ms input debounce then 600ms search debounce plus async
  module work. The fixed 750ms wait raced that request. Native glow harness
  now waits for that query's response completion and rendered live card before
  taking the click baseline; unexpected API counts include their full paths.
  Deterministic emitted inline/lazy logic reproduces the typing request at
  150+600ms and verifies the settled click opens Amazon without starting
  Analyze, loading another Analyze module or adding an API request over 1s.
  Product code unchanged. Native rerun remains with the operator because
  Chromium launch is blocked here by sandbox socket permissions.
  Targeted search-buttons/glow pytest: **33 passed** with
  `--basetemp=/tmp/b332w`; both changed CJS files pass `node --check`, and
  `git diff --check` passes. Removed that basetemp directory after validation.
  No full suite, commit or push.
- **Homepage phone label cascade (Codex task 332c, 2026-10-19; uncommitted):**
  With `search_box_glow=false`, `card_tidy` leaves its <=600px label rules in
  an inline sheet after `header-polish.css`; equal specificity overrides the
  task-332 phone labels. Phone selectors now include `#analyze-search`, so
  Analyze wins without changing tests, desktop labels or font sizes. Offline
  parsed CSS reproduces the old failure and passes 24 combinations of
  360/390/412, light/dark, inline/lazy and glow OFF/ON. Inter 16px placeholder
  width is 187.95px versus conservative text room 190/220/242px; the widest
  Analyze pill plus its 6px inset fits the existing 96px reservation.
  Targeted search-buttons/glow/polish/lazy and phone-payload pytest: **79 passed**;
  the 1,000-byte phone headroom guard and both relevant Node syntax checks pass.
  Used `--basetemp=/tmp/b332z`; that directory and owned inspection scratch
  removed. Native Chromium remains for the operator: local launch is blocked
  by sandbox socket permissions. No full suite, commit or push.
- **Native Chromium follow-up (Codex task 332b, 2026-10-19; uncommitted):**
  Shared Analyze geometry uses the task-332 flag/900px label rule. Real desktop
  clipping is on Black Friday/Cyber Monday's All discounts at >=900px: the
  event-specific cascade retains 15px/800 while the old 108px box leaves 92px
  after padding. Offline Inter ExtraBold font metrics: AI Analysis 84.16px,
  All discounts 97.23px; event twins now both 114px/15px/800 with 98px content.
  Home remains 108px/14px/800 (labels 78.58/90.77px, 92px content), with unchanged
  typing room and timer layout; event typing room remains +12px at 900–1100
  and +42px above 1100 versus the pre-332 padding. No text size reduced.
  Geometry checks actual padding/bounds, reports page/viewport/label/button
  widths and includes laptop breakpoints through 1599px; event logs include
  all measured widths for the operator's next native run. Search-more runs
  inline/lazy, both flag states, 390/1440 and light/dark: ON asserts top-pill
  absence; OFF retains every old assertion and both retain the remaining flow.
  Amazon navigation issue is pre-existing invocation misuse: first arg is a
  published site directory, not a shots directory. Read-only HEAD/current route
  replay gives 404 for an empty shots directory and 200 for a published fixture;
  no Chromium was launched for this comparison and no stash disturbed the tree.
  Native geometry confirmation remains with the operator. Requested targeted
  selection: **380 passed, 5 Chromium skips in 391.78s**, with `-n 4`, no cache
  and `--basetemp=/tmp/b332y`; that directory and owned inspection files removed.
  All six changed/new `.cjs` files pass `node --check`; Python compile and diff
  checks pass. No full suite, commit or push.
- **Calm search buttons (Codex task 332, 2026-10-19; uncommitted):** Strict
  `search_buttons_1019` defaults false/ships true. Desktop AI Analysis and
  phone Analyze share the existing discounts click handler for typed words:
  configured tag, coupon filter 210906366011 and Best Sellers; pasted links,
  ASINs, Enter and All discounts retain their flows. Static purple/blue twins,
  keyboard outlines, shorter phone prompt; redundant results/product pill gone.
  Event forms follow the same routing. Pre-edit OFF snapshots match all 50/49/49
  shipped/inline/membership fixture files; no recorded hashes changed. CSS typing
  room delta at 900/1024/1100/1280/1440/1600/1920: 0/0/0/+30/+30/+30/+30px.
  Native harness measures actual room, equal boxes, all interaction paints,
  360/390/412 prompt fit and light/dark screenshots; syntax passes. Chromium
  measurements/captures remain with the operator. Requested selection and focused
  fixes verify **394 distinct passes, 6 Chromium skips**; final new-feature run
  **14 passed**. Both phone gzip guards retain the 1,000-byte reserve. Own
  `/tmp/b332x` and inspection scratch removed; no full suite, live requests,
  commit or push.
- **Retention pipeline regressions (Codex task 337c, 2026-10-20; uncommitted):**
  Reproduced all seven reported failures serially. All are product regressions:
  the three prefilter baseline cases include new `audit_detail_days` and
  `audit_hot_days` in the prefix-selected audit fingerprint, invalidating
  existing paid verdicts even with retention OFF; the other four expose new
  reads bypassing `posts_seen` for immediate cold storage (including an inbox
  durable-ack assertion). Fingerprints now select only audit decision knobs;
  every fresh processed post again gets its normal `posts_seen` row, failed
  reads included. Existing bounded cold compaction at seven days, live-sighting
  protection, cold readers and the 30-day dedup window remain. All original
  behavioural assertions remain; baseline cases additionally exercise retention
  OFF/ON with the same saved calls, decisions and four public trees. Added
  tiered/untiered paid-verdict replay and fresh-to-cold/reopen/rollback checks.
  Serial focused validation: **13 passed in 133.36s** (all seven reported
  failures plus OFF/ON and new regressions). Final requested selection plus
  legitimacy/critic coverage: **635 passed in 249.33s**, including both
  `test_rerun_is_idempotent` modes. No `tests/test_db*.py` files exist in this
  checkout. All runs used `--basetemp=/tmp/b337c`; that directory and owned
  logs were removed. `git diff --check` passes. No full suite, live calls,
  commit or push. Prior 337b size projections assumed immediate cold writes
  and need remeasurement after this correction; the encrypted-size target
  remains unproven.
- **Retention adversarial review (Codex task 337r, 2026-10-20; uncommitted):**
  Reviewed only 337/337b's working diff and new files. Fixed second-opinion
  rollups losing successful-post counts in the routing reader and deleting
  event replay guards: aggregate readers now retain measured yield, with
  permanent retired-event keys checked atomically by the writer. Fixed flagged
  hourly VACUUM ignoring the run deadline: expired runs defer it; checkpoint
  lock waits and SQLite execution honor the remaining deadline, with connection
  settings restored after interruption. OFF keeps the previous vacuum policy.
  Non-blocking: post receipts still expire at the existing 30d dedup boundary;
  the live-sighting hash scan and permanent aggregate/guard growth need production
  timing/sizing. Allowance-inclusive <60 MB remains unproven; the prior 337b
  model was not rerun for these fixes. Restore/decrypt ordering is unchanged:
  scheduled pipeline steps follow successful restore, whose failure is fatal.
  Targeted validation: **300 passed, 1 browser skip** (retention, readers,
  publication bytes, both required rerun modes and protected state), then
  **54 passed** (new regressions, Telegram alert deduplication and both
  posted/pruned rerun modes), then **2 passed** after explicitly exercising
  the retired-event guard with the flag OFF. A direct pre-337/current OFF
  receipt/audit/cache/maintenance comparison matched all **499,712 DB bytes**.
  `/tmp/b337r` and owned scratch removed; no full suite, live calls, commit
  or push. Native browser verification and production measurements remain.
- **State growers (Codex task 337b, 2026-10-20; uncommitted, ON):** Supersedes
  337's post-detail clearing. Same strict `state_retention_337` flag. New posts
  use lossless binary-key/UTC-second receipts immediately; legacy unlinked
  receipts backfill at 7d, retaining IDs/UIDs/URLs/clocks for the existing 30d
  dedup window. Full post text/HTML/prompts are absent from storage; the 2d
  parse cache is validated facts only. Source audits pack full detail after 1d,
  aggregate after 14d (pending/recent parses survive); critic/run audit detail
  becomes counts at 14d while identities remain. Raw clicks archive at 30d to
  exact daily totals/hour slots/coverage bitmaps; all click/source readers
  include compact data. Accuracy keeps 14d/last 24 per deal/source, with exact
  scoreboard counts and permanent retired-run/cached-reading replay guards.
  Protected deal/evidence/death facts and today's/future money remain unchanged.
  All sweeps keep 64-row transactions, 4096 examined/table/run and 1s shared
  budget; old run/send/alert guards survive. OFF-byte and rollback readers pass.
  Largest expanded fixture: **98.55 → 57.05 MB encrypted** after 276 bounded
  calls + VACUUM; mature-writer projection **52.28 → 52.73 MB** after the next
  24h46m of publication clocks (+3,300 protected own-price facts). Full 90-day
  own-check envelope adds 6.60 MB; with 5% mix margin: **59.67 MB** modeled,
  **69.67 MB with the separate 10 MB unmodeled-state allowance**. Thus the
  modeled cohorts meet 60 MB; production/allowance-inclusive <60 is not proven.
  [Per-table measurements and limitations](research/state-size.md#337b-retain-identities-compact-hot-growers-2026-10-20).
  **609 distinct targeted passes**, then **239 final replay/accuracy/alert passes**,
  including both required rerun modes; final **50** knob/grower checks pass
  (**616 distinct cases** across the final selections).
  The last model clock/replay changes were bounded/documented rather than
  rerunning the entire long model. No full suite, live access, commit or push.
  `/tmp/b337b` and owned model scratch removed; owner review, live snapshots
  and deployed catch-up/size monitoring remain. Warning/stop stay 70/90 MB.
- **State growth (Codex task 337, 2026-10-19; superseded by 337b above):** Strict
  `retention.state_retention_337` defaults false/ships true. Existing bounded
  maintenance expires only opaque API validators after 1d, clears unused
  post-receipt uid/URL/published-clock details after 2d (hash/source/discovery
  guards remain), and clears terminal IndexNow maps after 1d (permanent run/
  status receipts and sending maps remain). Evidence/history, live/hidden/
  suspect deals, photo retry markers, alert/send/run guards and money caps
  retain their prior policies. OFF adds no schema/writer/bookkeeping bytes.
  New read-only `python -m scripts.state_growth later.db --before earlier.db
  --hours 1` reports every table's counts/bytes/column lengths and deltas,
  using dbstat or labelled logical lengths. Keepa bodies are memory-only;
  photo markers add 64 raw bytes once/offer, zero on repeated lookups.
  [Measurements/reader audit](research/state-size.md#337-receipt-details-and-disposable-apiindexnow-payloads-2026-10-19):
  corrected 448k-post/122k-audit/432k-accuracy/3,300-live-offer stress model
  **84.57 → 81.36 MB encrypted before VACUUM / 80.66 after**; retained next
  day **80.43 MB, −0.0095 MB/hour**, after extra bounded drains. Initial
  receipt sweep takes 110 calls; no immediate production saving is promised.
  **60 MB target NOT met:** with the existing 10 MB reserve, **90.43 MB**;
  ordinary VACUUM will not immediately trigger (zero freelist pages). Current
  supplied live rate is 0.3689 MB/hour, ~50.9 hours to refusal from #260;
  production top growers need two decrypted snapshots. Protected receipt/
  history cohorts dominate; further lossless receipt compaction or separate
  R2 work remains, so this change does not establish that live saves are safe.
  **261 distinct targeted passes, 1 browser skip**, including both rerun modes,
  exact publication bytes, protected money/alerts/run keys, bounds and rollback.
  `/tmp/b337` and owned model scratch removed. No full suite, live calls,
  commit or push; owner review and production measurements remain.
- **Phone timer alignment (Codex task 330, 2026-10-19; uncommitted):** Strict
  `phone_timer_raise` defaults false/ships true with the operator's quote.
  At <=640px the tagline's 19.5px line sits atop its legacy 40px box:
  CSS-derived timer/text offset **+10.25 -> +0.25px** with relative `top:-10px`;
  header-bottom gap **0 -> 10px**, leaving **8px** above the 2px orange line.
  Grid tracks, type, ring and shared CSS remain unchanged; OFF matches all
  **78 pre-edit published files**. Browser checks measure visible text at
  360/390/412/430 in both themes, preserve 182/388 audit guards and compare
  900/1280 geometry; syntax passes, native Chromium remains with the operator.
  Merged 328/329 release pins isolate the parallel feature; no hashes changed.
  Targeted runs: **213 distinct passes, 1 Chromium skip**, including all **63**
  follow-up checks after fixing the three cross-feature release-pin failures.
  Phone gzip retains its 1,000-byte reserve. `/tmp/b330`, `/tmp/b330-recheck`
  and owned inspection scratch removed; no full suite, commit or push.
- **Photo catch-up adversarial review (Codex task 331r, 2026-10-19):**
  Confirmed duplicate paid reads after a failed fetch-time recheck and for
  newly ingested rows sharing an already-read ASIN (both regressions failed
  before the fix). A fetcher-local, normalized ASIN set now defers those
  catch-up reads until the next run; OFF has no new tracking or output.
  Targeted offline checks: **161 distinct passed** across post-ingest photos,
  rerun idempotency, existing photo recovery and visitor reserves, including
  sent/unsent deadline accounting and later recovery after an empty lookup.
  No other confirmed blockers; live coverage remains unmeasured and shared
  identities may wait one run for a photo. `/tmp/b331r` removed; no full suite,
  live calls, commit or push. Changes remain uncommitted for owner review.
- **Photo catch-up (Codex task 331, 2026-10-19; uncommitted):** Confirmed
  `_fetch_all` runs Keepa rechecks before parsing; `early_recheck or ...`
  then skips the post-ingest pass. The separate tier cross-check caps at 25.
  Source-shaped DOA fixtures retain exact ASINs with post click URLs yet miss
  photos after that quota; post routing itself does not prevent identity.
  Existing offline identity/short/post passes remain capped at 60/10/40;
  task 265's separate proposed pass is absent. No decrypted live state here
  (`scripts/restore_state.sh` restores encrypted data-branch state), so the
  live no-ASIN/backlog mix and any effect of used removal remain unconfirmed.
  The first-hour burst counts toward 98%; expired photoless rows already leave
  JSON before the check reads it. Strict `images.post_ingest_keepa` defaults
  false/ships true: exact-ASIN photo-only catch-up after recovery, both lanes,
  using the existing 60 cap/remaining 300 photo slots and shared Keepa reserves,
  including visitors. No new page reads, source images/text, price/evidence or
  click changes; unresolved photos keep the one-hour rule. OFF public bytes
  match a pre-edit whole-output hash. Requested targeted validation: **208
  passed**, including rerun idempotency; actual fetch-hook regression: **1 passed**.
  All runs used `-n 4`, no cache, failed-only retention; `/tmp/b331` removed.
  #231 needs 2 recovered photos for 98%; all 24 Amazon gaps would give 99.70%,
  conditional on exact identity/images/budget, not a measured live forecast.
  No full suite, live calls, commit or push; review/live measurement remain.
- **Tool-page suite compatibility (Codex task 328c, 2026-10-19; uncommitted):**
  Both tool footers now carry the five standard trust links, adding How we
  verify and Contact while preserving the clean layout and 44px targets.
  Preview/public tool render checks enforce the shared trust-link contract.
  Search-polish checks identify tool forms explicitly, assert their own script,
  footer disclosure and lack of homepage discount markup, and retain every
  existing homepage/event-page assertion. Home browser coverage retains all
  original controls, history and exact-card checks in OFF, preview and public
  modes, adds ordinary Home navigation from both tools, and checks discovery
  links before Chromium launches: none on home in OFF/preview; both in the
  public footer. The original Home fixture used AppConfig with tools OFF, and
  preview preserves existing home bytes, so its reported native failure could
  not be attributed to tool links from the source. The exact requested local
  Home-file command returned **23 passed, 1 Chromium skip** before edits;
  Chromium cannot launch here (browser target closed). Final requested targeted
  selection plus savings: **357 passed, 6 Chromium skips in 239.13s**, with
  `-n 4`, no cache, failed-only retention and `/tmp/b328c`; that directory was
  removed. Both browser harnesses pass `node --check`; diff checks pass.
  Native Chromium verification remains with the operator. No full suite,
  live API calls, commit or push.
- **Tool grid audit repair (Codex task 328b, 2026-10-19; uncommitted):**
  The first post-checker failure is the Finder's hidden `#top-sellers` grid:
  its computed `repeat(2, minmax(0px, 1fr))` has three whitespace tokens,
  which the original audit incorrectly counted as columns. CSS now defaults
  every product grid to two fixed tracks, with five from 761px; unused tracks
  preserve sparse-card widths. The browser audit checks both grids, including
  hidden grids, handles the unresolved repeat value, and reports page, viewport,
  theme, motion setting, section and template on failure. It also checks every
  visible product card's width against a full grid, including the single verified
  deal. Other assertions and the operator's 44px footer fix are preserved.
  Browser-file `node --check` passed. Requested targeted pytest plus savings:
  **304 passed, 3 Chromium skips in 170.58s**, using `-n 4`, the requested
  no-cache/failed-retention options and `/tmp/b328b`; that directory was removed.
  Native Chromium verification remains with the operator; no full suite,
  live API calls, commit or push.
- **Amazon SEO tools (Codex task 328, 2026-10-19; uncommitted preview):**
  `/amazon-deal-checker/` and `/amazon-discount-finder/` follow the approved
  white layout, real brand mark, phone grid and site's dark palette. Strict
  `tool_pages` defaults OFF and ships `{enabled: true, public: false}`: preview
  adds only the two pages and two assets; existing files and OFF release pins
  stay byte-identical. Public mode adds footer links, sitemap/IndexNow discovery;
  rollback excludes even queued tool URLs. Canonical/social metadata and visible
  five-question FAQ schemas are included; previews have `noindex,nofollow`.
  Checker uses only the existing `/api/search` with the homepage's input checks,
  cache/retry/timeout behavior and matching; badges use current Amazon list price
  only (browser savings function lives in `savings.py`). **Endpoint limitation:**
  ASIN lookup returns one product, whereas text lookup returns up to five sellers.
  Show only returned sellers; no extra paid lookup or invented related products.
  Finder derives the exact homepage discount URL locally and reads `/deals.json`;
  no API lookup. Ordered work gates nine-second steps; reduced motion skips delays;
  unresolved products never receive completed steps. Tracking carries safe events
  and `page_view` source `tool_page`; affiliate tags/disclosure remain visible.
  Public home is **76,260 gzip bytes / 77,360 ceiling (1,100 spare)**; text palettes
  meet AA. Node logic covers URL parity, badge boundaries, real timing, cache,
  unavailable data, API limits/timeouts and filtering. Native Chromium harness:
  `node tests/tool_pages_browser.cjs /path/to/chromium /tmp/tool-page-shots`;
  syntax checked and local fixture built, native run remains with the operator.
  Requested selection plus savings/IndexNow: **351 checks verified, 3 Chromium
  skips across the run and follow-up**. The broad run had 350 passes and one
  new tracking-harness assertion using the wrong output key; corrected to the
  harness's `dataLayer`, then all **34 tool-page checks passed in 51.39s**.
  Publish/footer/public-copy/tracking selection: **348 passed**. The existing
  1,600-deal file-count assertion now accounts for four tool files; no recorded
  hashes or budget ceilings changed. Commands used the requested `-n 4`,
  no-cache/failed-retention options and `/tmp/b328`; that directory and owned
  scratch were removed. Syntax/compile/diff checks passed.
  No full suite, live API calls, commit or push. Public flip requires the operator's
  preview approval in a separate commit.
- **Compact timer ring (Codex task 329, 2026-10-19; uncommitted):** Strict
  `timer_pill_ring` defaults false/ships true with option A's operator quote.
  At 900–1599px a masked 3px conic outline uses `paintDial`'s existing fraction,
  phase colours and drop green; no markup, extra timer or layout changes.
  Three original-release OFF pins pass without changing older hashes. Browser
  harness adds exact header-box comparisons, light/dark and reduced-motion
  coverage, progress/drop/reset checks and `ring-<width>-<theme>.png` captures;
  syntax checked, native Chromium remains for the operator. Requested targeted
  run: **116 passed, 1 Chromium skip in 148.35s**. Phone HTML **76,250 / 77,360
  gzip bytes** retains **1,110 bytes** of reserve; CSS **14,843 / 14,913**.
  `/tmp/b329` and owned recheck scratch removed. No full suite, commit or push.
  **329b fix round:** Pill progress now writes beside the digits in `paintClock`;
  offline Node drives the emitted one-second callback without animation frames.
  Browser sampling advances two seconds and polls the DOM from Node; phone
  ticking guard and all geometry assertions remain. Added 1023/1280/1600px and
  both motion settings across the matrix. Both `node --check` calls pass;
  targeted six-file run: **116 passed, 1 Chromium skip in 144.82s**;
  `/tmp/b329b` removed.
  **329c resolution:** Operator confirmed the ring covers every desktop width
  where the dial is hidden. Its media query now comes from the composed CSS's
  dial-hiding rule (900–1599px shipped; 900–1199px without task 317), with no
  independent breakpoint. OFF CSS and byte pins remain intact; >=1600px keeps
  the dial and phones keep their layout. Browser matrix adds 1512/1599/1920px;
  all requested ring widths retain exact OFF header-box, growth and green-drop
  assertions in both themes/motion settings. Native Chromium remains for the
  operator; both `node --check` calls pass. Targeted six-file run: **117 passed,
  1 Chromium skip in 202.54s**; `/tmp/b329c` removed. No full suite, commit or push.
  **329d clock fix:** Confirmed the emitted countdown uses `setInterval(tick,
  1000)` → `paintClock`, with no visibility or animation-frame gate. Browser
  helper now advances the fake clock 250ms before every sample (5s simulated
  limit), retaining every digits/ring, dial, geometry and colour assertion.
  Both Node syntax checks and emitted countdown logic pass; targeted six-file
  run: **117 passed, 1 Chromium skip in 166.38s**. `/tmp/b329d` and inspection
  scratch removed. Native Chromium rerun remains for the operator; no full
  suite, commit or push.
- **Merged homepage headroom (Codex task 327, 2026-10-19; uncommitted):**
  Under `discounts_from_link`, remove support-mode membership-dialog/account-email
  CSS with no consumer and the homepage placeholder assignment immediately
  overwritten by enabled Amazon search. Join's shared phone styles, membership
  mode, search-disabled fallback, 324/326 behavior and original OFF pins remain.
  Shipped fixture: **76,485 → 76,220 gzip bytes** (265 saved; 1,140 below the
  unchanged 77,360 ceiling). Requested ten-file run: **225 passed, 2 browser
  skips, 1 fresh-drop timing failure** (50.6 ms against 50 ms under four workers).
  Both unchanged fresh-drop variants passed on rerun: **2 passed**; discounts
  Node logic passes. All distinct requested checks pass after that timing retry.
  `/tmp/b327` and owned scratch removed. No full suite, commit or push.
- **Amazon-link discounts (Codex task 326, 2026-10-19; uncommitted):** Strict
  `discounts_from_link` defaults OFF/ships ON. Slug or completed same-ASIN
  Analyze title becomes sanitized discount words; bare ASIN falls back to its
  tagged product page. Short/adult/non-Amazon fallbacks, typed queries and other
  buttons stay unchanged; no fetch or UI added. Three original-release OFF byte
  pins pass; existing hashes unchanged. Final requested selection: **301 passed,
  6 skipped in 259.14s**. Phone HTML **76,350 / 77,360 gzip bytes** (1,010 spare).
  Desktop/phone Nulaxy Chromium assertion added and syntax-checked; operator
  owns native browser run. Own `/tmp/b326*` scratch removed. No full suite,
  commit or push.
- **Phone tagline count (Codex task 324, 2026-10-19; uncommitted):**
  Strict flag defaults false, ships true with the operator's quote. At <=899px
  the dot and short verified-deal count show; 200px uses spare space before
  the timer. Desktop copy/type, zero-count fallback and visit slot stay intact.
  OFF matches all 74 pre-edit files; no hashes/ceilings changed. Superseded
  tagline rules fund the width rule: CSS 14,913 gzip bytes; HTML keeps 1,000
  bytes of reserve. Site check reads visible text; other visitor scripts work.
  **484 distinct focused passes, 1 Chromium skip** after new fixture/guard fixes;
  final feature/native-fixture recheck **17 passed in 23.57s**. Syntax/diff checks
  pass. Operator runs `header_polish_browser.cjs`: four-digit fit at 360/390/412/430,
  headerH 182/firstCardTop 388 and unchanged desktop. `/tmp/b324` and owned
  scratch removed. No full suite, commit or push.

- **Visitor Keepa reserve on release 8ceabdf (Codex task 323, completing 311, 2026-10-19; uncommitted):**
  Resolved all six content conflicts while preserving task 319's Amazon-used
  switch, recheck cadence, rotating two-page focus discovery and intake caps.
  Strict `keepa.visitor_reserve_tokens` defaults 0 and ships 150; every pipeline
  Keepa budget gate and batch size preserves the extra floor, including task
  319 focus/overflow discovery, finder/sweeps, categories, photos, verification,
  accuracy and comparison reads. Existing priorities, money caps and raw bucket
  receipts remain. Run/recheck summaries report `visitor_reserve_deferred_calls`.
  Historical `pre_task_319` helpers also pin the visitor reserve to 0, isolating
  legacy click-price/Buy Box/demotion/quality/price-accuracy fixtures without
  changing their assertions or recorded hashes. Added task-319 focus rotation
  and boundary, shared overflow-budget and full-run discovery-summary coverage.
  Requested 33-file selection (including every Keepa-budget reference file):
  **1,649 passed, 1 Chromium skip in 497.73s**. Final reserve/overflow/task-319
  follow-up: **112 passed in 28.98s**, including new full-run summary coverage.
  Runs used `-q -n 4 -p no:cacheprovider -o tmp_path_retention_policy=failed`,
  with basetemps `/tmp/b323`, `/tmp/b323-reserve` and `/tmp/b323-final`; all
  three directories removed afterward. Diff/marker checks pass. Reviewer
  owns the full suite.
  Git metadata is read-only (`/home/user/phillysaver/.git/worktrees/wt323`), so
  the resolved contents cannot be staged here and index entries remain unmerged.
  No full suite, live API calls, commit or push.

- **Live deal-page check + retired Amazon used URLs (Codex task 322, 2026-10-19; uncommitted):**
  Reproduced with shipped config and Amazon used, Woot used, new and ended
  offers: there is no first sitemap deal page (`deal_pages_indexable: false`).
  The old deals.json fallback could choose an ended row first; the fixture's
  `/deal/ended-kettle-28a3a5e8/` correctly had no Get deal. Step 13 now selects
  an unlocked `status: alive` row with a local deal_path, keeping the existing
  Get deal assertion. Real rendered-output checks cover desktop and phone.
  Separately, task 319 removed remembered suppressed URLs instead of retaining
  ended pages. These now use the existing ended renderer with a render-only
  suppression reason: noindex, category alternatives, no Get deal or live
  Product/Offer schema; stored rows/history remain unchanged. Suppressed rows
  stay out of the wall, deals.json and sitemap; IndexNow retains its existing
  ended/noindex transition notifications. No ended-page template/design change.
  Regression compares suppressed pages byte-for-byte with ordinary ended pages,
  with indexing on/off and fresh/reused output folders; Woot used and new live
  pages retain Get deal, and deleting a stored row removes its page.
  Requested SEO/Amazon-used/footer/identity files plus publish, IndexNow receipts
  and offline idempotency: **315 passed in 83.02s**, using `-q -n 4
  -p no:cacheprovider -o tmp_path_retention_policy=failed --basetemp=/tmp/b322`.
  The unchanged 1,600-deal file-count check passes; no recorded hashes re-pinned.
  Both standalone site-check Node harnesses, JS syntax and diff checks pass.
  `/tmp/b322` and owned reproduction scratch removed. No full suite, live
  API/browser calls, commit or push. Reviewer owns review/deployment/live check.

- **Integrated 318 + 319 + 320 suite (Codex task 321, 2026-10-19; uncommitted):**
  Phone-budget fixtures now use the shared visible-offer count, including task
  319's Amazon-used suppression (1,270 offers instead of 1,300 after photos).
  The reported budget failures were payload-count validation failures; measured
  HTML is 75,936 / 77,360 gzip bytes, retaining 1,424 bytes of headroom, and CSS
  is 14,898 / 14,913. No byte ceilings, headroom requirements or hashes changed.
  Task 320's original-release OFF pins isolate 318 via the existing helper chain
  and 319 via `pre_task_319`. Social condition tests use Woot so all three
  channels still reject used/open-box/refurbished website-only offers. The price
  accuracy test now enforces the <=2-hour top-pick deadline, pick-first order,
  adaptive fallback and zero spend on suppressed Amazon used picks. The CLI
  privacy test freezes its fixture's Sunday clock, avoiding real Monday-only
  Telegram subscriber requests in the delivery mock. Emitted fresh-drop logic
  passed unchanged, including a separate two-variant rerun.
  All eight affected files: **309 passed, 3 Chromium skips**; the fresh rerun:
  **2 passed**. Chromium launch here fails with `setsockopt: Operation not
  permitted`; native Home-link verification remains for an unrestricted run.
  The single requested full run ended **7 failed, 8629 passed, 35 skipped in
  1543.01s (0:25:43)**. All seven additional failures were the existing yearly
  Stripe deploy tests trying to open a localhost socket (`PermissionError`).
  Their test helper now preloads an offline urllib transport in the child
  Python process and executes the unchanged deployment shell step. Request
  path/authentication and all seven validation/error cases remain covered;
  unexpected hosts/methods/timeouts fail. Entire `tests/test_workflows.py`
  follow-up: **65 passed in 8.74s**. No second full run, as requested. All runs
  used `-q -n 4 -p no:cacheprovider -o tmp_path_retention_policy=failed`;
  `/tmp/b321*` output removed afterward. No commit or push.

- **Search/header polish (Codex task 318, 2026-10-19; uncommitted):**
  Strict `search_polish_1019` defaults OFF, ships ON: removes the search hint,
  moves button disclosures to the existing footer (keeps them without that
  disclosure), adds 6px oval insets, +40px desktop field limits, AA Dropbox
  Blue, outlined 14px tabs and `Join free · Price Alerts`. Only top Amazon
  buttons get bestseller sorting; secondary/discounts exits keep their URLs.
  Original-release OFF bytes match in shipped/inline/membership modes; no
  hashes re-pinned. Requested files + phone budgets: **406 distinct passes,
  1 Chromium skip**; final frozen-code recheck **26/26 passed**. Phone HTML
  **75,931 / 77,360 gzip** (1,429 spare); CSS **14,898 / 14,913**. Browser
  syntax checks pass; operator still runs the three updated Chromium scripts.
  `/tmp/b318*` output removed. No full suite, commit or push.

- **Amazon used off + new-deal discovery (Codex task 319, 2026-10-19; uncommitted):**
  `amazon_used.enabled` defaults true and ships false. Shared publication,
  explicit render inputs, remembered ended deal pages, checker and social
  gates exclude Amazon used without deleting/killing rows. Warehouse fresh,
  deep, overflow and Finder sweep are disabled; rechecks, photo requests,
  suspects, hidden recovery, clicks and single-price checks cannot spend on
  suppressed rows. Woot remains unchanged. Run summaries report live rows
  suppressed, zero used-token spend, and clearly labeled avoided ceilings.
  Buy Box/click proof remains ON with a shared 120-minute clock; top targets
  1.5–2 h, wall 4–6 h, rest 12–24 h; surplus idle floor 12 h prevents
  30-minute rest repurchases. Hidden targets unchanged. Expired shopper
  proof still suppresses price numbers. Strict config validation added for
  changed discovery/cadence knobs. New discovery uses 12 general pages and
  two pages per six existing focus categories (rotating first audience),
  validates rank 1–30,000 and >= min_pct below the 90-day average in responses,
  and uses existing appeal adult exclusions. Intake cap 10 -> 20 new posts;
  parse/critic evidence, money caps and existing reserves unchanged. Task 311
  remains separate; no visitor-reserve implementation is included here.
  Estimate from the fixed 1,400-offer fixture: 34 live Amazon used rows
  suppressed (30 pass today's photo cutoff), all 34 Woot used retained.
  Production state is unavailable in this tree; the summary measures it live.
  Ordinary NEW discovery ceiling 40 -> 120 tokens/pull (+80); disabled
  Warehouse pages/sweep remove up to 60+111=171 tokens/pull, plus up to
  13 per used recheck (14 with live update). These are ceilings, not measured
  savings; short pages, intake caps and reserve gates usually spend less.
  Validation: final requested core + task-319 regressions **403 passed in
  155.70s**, including offline idempotency and the 12-hour surplus boundaries.
  Wider affected-file run: **1,130 passed**, one new photo-test setup error
  subsequently corrected (the helper already supplied image_url). Legacy
  follow-up: **395 passed, 1 Chromium skip in 234.59s**, including the 144-run
  scheduler simulation. All previously failing checks pass in the follow-ups.
  Added explicit `pre_task_319` helpers for old used/cadence fixtures; changed
  no recorded hashes or byte ceilings. Site-check's 45-minute publish-clock
  check remains independent of the two-hour price proof. All pytest runs used
  `-q -n 4 -p no:cacheprovider -o tmp_path_retention_policy=failed
  --basetemp=/tmp/b319`; owned temporary output removed. Compile/diff checks
  pass. No full suite, live API calls, commit or push. Reviewer owns full-suite
  and native Chromium validation.

- **Analyze top five Amazon sellers (Codex task 320, 2026-10-19; uncommitted, ON):**
  Operator approved `analyze_top_sellers: true`; strict boolean, default/missing
  false preserves the preceding release bytes. Text Analyze now shows our
  matching deals alongside up to five related live Amazon products, ordered by
  safe positive Keepa `monthlySold` descending, then current sales rank ascending.
  The Function and page share the existing query matcher; published search
  settings supply the flag and existing adult exclusions. Only evidenced safe
  positive monthly counts enter the response; card labels round down through
  50/100/200/500/1K/2K/5K/10K buckets and omit absent/invalid/sub-50 counts.
  Headings say Deals or Verified deals only when every shown card qualifies;
  inline/lazy paths retain matching deals when live prices fail. ASIN/link
  analysis retains its one-product/local-first behavior. One search page, no
  extra product reads, six-hour cache, 10,000/day cap, tagged links, commission
  wording and all dispatch/accounting guards remain; ON cache bodies use v2.
  New OFF pins were captured from release 6de86c0 before editing; existing pins
  are untouched and older fixtures use `pre_task_320`. New composed rendering
  checks passed **20/20**, and all **72 Function Node checks** passed.
  Final requested `tests/test_search*.py`, card-tidy and comment-strip run:
  **267 passed, 6 Chromium skips in 404.20s**, with `-n 4 -p no:cacheprovider`,
  `-o tmp_path_retention_policy=failed --basetemp=/tmp/b320`. OFF-byte checks,
  idempotent publication and the unchanged phone gzip ceilings/1,000-byte
  reserve all pass. Ordinary paging detaches without aborting an already-paid
  request. Native browser fixtures now exercise new headings, five cards,
  monthly labels and tagged links; `node --check` and real Function import
  resolution pass. `/tmp/b320` and owned scratch removed. Chromium remains for
  the operator: `node tests/search_analyze_browser.cjs /path/to/chromium`.
  No live API/LLM calls, full suite, commit or push.

- **Flush Analyze pill (Codex task 314, 2026-10-18; uncommitted):**
  Supersedes the earlier white-space request: card_tidy removes the transparent
  4/5px border and padding-box clip, uses a 2px field inset and matching 10px
  right corners. Phones retain Analyze and a >=44px target; input padding now
  clears the existing clear button too. Gradient, sparkle, charge animation and
  reduced-motion rules remain. CSS shrinks **1,534 -> 1,528 bytes**.
  Shared native assertions cover flush painted edges, corners, tap size, label,
  text/clear separation and overflow in search/header/event fixtures; search
  adds 360px and hover/focus/active stability and motion checks.
  Requested five-file run: **109 passed, 1 Chromium skip in 193.69s**, including
  OFF/missing/back-OFF comparisons and the unchanged 1,000-byte phone headroom
  guard. Used `-n 4 -p no:cacheprovider -o tmp_path_retention_policy=failed`
  with `/tmp/b314`, removed afterward. JS syntax and diff checks pass.
  Operator still runs search_box_glow_browser, header_polish_browser and
  page_polish_browser; no native Chromium result claimed. No recorded hashes
  changed, full suite, commit or push; prior staged work preserved.

- **Header visit-count removal (Codex task 315, 2026-10-18; uncommitted):**
  With card_tidy ON, its publish swaps empty the engagement header suffix and
  keep legacy `#sincevisit` hidden/empty. The live dot/count and per-card NEW
  badges remain; OFF templates, recorded hashes and Analyze CSS are untouched.
  New emitted-JS tests cover both flag values with engagement enabled/disabled,
  repeated renders, a fake 1,893-new count, and fresh/old/dead/spotlight badges.
  Requested four-file run: **70 passed, 1 Chromium skip, 2 harness failures**
  (missing `freshUI` stub). After correcting that test-only stub, the entire
  new engagement file passed **4/4 in 6.06s**: **72 distinct tests pass**, with
  the native comment-strip check still skipped. OFF-byte rollback and the
  unchanged phone gzip budget/1,000-byte reserve passed in the requested run.
  Both runs used `-n 4 -p no:cacheprovider -o tmp_path_retention_policy=failed`
  and `/tmp/b315`; temporary output removed. JS syntax/diff checks pass.
  Native browser verification remains for the reviewer; no full suite,
  commit or push. Existing staged work is preserved.
- **Shared-page header action parity (Codex task 313b, 2026-10-18; uncommitted):**
  Native header checks compare each deal/event page's header Join and install
  counts against that page's header_polish OFF render at 390/1440 in both
  themes; the no-sideways-scroll check remains. Product code is untouched.
  Requested `tests/test_header_polish.py` run: **16 passed in 67.26s**, with
  `-p no:cacheprovider -o tmp_path_retention_policy=failed --basetemp=/tmp/b313b`.
  JS syntax check passes; temporary output removed. Native Chromium rerun
  remains for the reviewer. No commit or push.
- **Header rollback isolation after task 310 (Codex task 313, 2026-10-18; uncommitted):**
  Header tests compare each shipped/inline/membership config's own OFF render:
  task 310 changed the enabled card_tidy output in the recorded base, which
  disabling card_tidy cannot restore. OFF, missing and back-OFF must match;
  ON must differ, with its stylesheet present only when enabled. Recorded
  hashes and product code are untouched. Requested header/card-tidy/comment-strip
  run: **60 passed, 1 Chromium skip in 139.65s**, using `-n 4`,
  `-p no:cacheprovider -o tmp_path_retention_policy=failed --basetemp=/tmp/b313`.
  Temporary output removed. Native comment-strip verification remains for
  the reviewer; no commit or push.
- **Search-active header alignment (Codex task 308b, 2026-10-18; uncommitted):**
  Desktop header cells now align at the top; each row-1 control is centred on
  the search field's 56px band using its own height. In-flow Amazon/search
  helpers can grow without shifting those controls. Spelling stays in flow,
  preserving its separation from the Amazon CTA and its position on blur.
  Header browser coverage now checks corrected search, blur stability and
  clickable undo at every desktop width in both themes. After restart, the
  first run found four CSS-budget failures; folding alignment into the
  existing control rules and using equivalent grid shorthand fixed them.
  Final requested 20-file pytest run: **580 passed, 5 Chromium skips in
  368.23s**, with `-n 4 -p no:cacheprovider`,
  `-o tmp_path_retention_policy=failed`, basetemp `/tmp/b308b-final`.
  Home gzip **76,034 / 77,360 bytes** leaves **1,326 bytes** of headroom;
  CSS **14,913 / 14,913**, JS **50,479 / 53,767**, JSON **92,294 / 103,976**.
  JS syntax and diff checks pass; own `/tmp/b308b-*` artifacts removed.
  Chromium remains blocked; operator will run
  header_polish, search_more, search_box_glow and drop_shuffle browser tests.
  No full suite, commit or push; prior staged integration work is preserved.
- **Integrated Chromium repairs (Codex task 308, 2026-10-18; uncommitted):**
  At 900–1199px the header now gives search a flexible track with a 320px
  minimum, retaining the compact install/timer controls and existing phone CSS.
  Spelling correction stays in normal flow: the desktop Amazon CTA previously
  covered its undo link, causing the long click retry before the join popup
  appeared. Page-polish home now postpones automatic signup while a query or
  search focus is active; manual Join and existing delay/frequency rules remain.
  Fresh Trending reserves two title lines in critical CSS for both static and
  hydrated cards (the reported 18.22px jump was one title line). No geometry
  rule arrives in the lazy module. Glow's fixture now supplies `electronics`:
  its uncategorized mice were all niche `other`, excluded from fresh Trending;
  all eight must now pass real rotation eligibility before the browser starts.
  Added emitted-controller fake-clock coverage, a native >35s search check,
  and delayed-drop-import first-paint geometry/CLS checks at all three widths
  and both motion settings; existing assertions and recorded hashes remain.
  Requested 20-file run: **580 passed, 5 Chromium skips in 469.86s**, using
  `.venv/bin/pytest -q -n 4 -p no:cacheprovider --basetemp=/tmp/b308`.
  All four inline/lazy × glow OFF/ON fixtures separately build eight eligible
  static cards. Final shipped phone fixture: **1,300 offers; home 76,038 /
  77,360 gzip bytes**, leaving **1,322 bytes**, above the required 1,000-byte
  reserve. JS **50,478 / 53,767**, CSS **14,902 / 14,913**, JSON **92,293 /
  103,976** also pass unchanged ceilings. Final header/phone-budget recheck:
  **17 passed in 119.22s** (`-n 4`, `/tmp/b308-final`). Changed browser scripts
  pass `node --check`; `git diff --check` is clean.
  Chromium launch is blocked by sandbox
  `setsockopt: Operation not permitted` (SIGTRAP), so no native pass or measured
  CLS is claimed. Reviewer must run `header_polish_browser.cjs`,
  `drop_shuffle_browser.cjs`, `search_box_glow_browser.cjs` and
  `search_more_browser.cjs` with `/opt/pw-browsers/chromium` and
  `NODE_PATH=/opt/node-tools/node_modules`; drop covers 360/390/1280 and both
  motions automatically. No full suite, commit or push; existing staged work
  is preserved.
- **302/303c/304/305 integration (Codex task 307, 2026-10-18; uncommitted):**
  Header rollback tests disable the independent drop/page flags through their
  `pre_*` helpers and retain all three recorded hashes. Header layout checks
  cover both inline and shared search CSS, seasonal links below the header,
  unique drop controls, unchanged scripts/other pages, and idempotent re-runs.
  The fresh-drop failure reproduced as a cold-selection timing miss, not a
  relocated-nav failure. Selection now scans for the stable priority winner
  instead of sorting at every slot, and avoids per-row balancing callbacks;
  all rank/mix rules and the 50 ms limit stay unchanged. Exact Python/JS parity
  now also covers twenty varied-inventory marks.
  Native drop fixtures now exercise seasonal navigation at 360/390/1280 widths,
  serve the shared/header CSS with the correct MIME type, and check visible
  toast bounds. Event-search/Analyze browser checks wait for the fresh module
  and verify the query survives with no drop controls/confetti. Existing native
  header checks cover desktop/phone geometry and light/dark themes.
  Final requested **20 files: 579 passed, 5 Chromium skips in 418.25s**, using
  `.venv/bin/pytest -q -n 4 -p no:cacheprovider --basetemp=/tmp/b307`.
  Both emitted cold/cached selection cases pass the unchanged 50 ms assertion;
  the first wider run isolated the remaining failure to a 60.91 ms cold pass
  before optimization. No recorded hashes, budgets or assertions were loosened.
  Shipped phone fixture: **1,300 offers; home 75,976 / 77,360 gzip bytes**,
  **1,384 bytes headroom**, retaining the required 1,000-byte reserve. JS
  **50,439 / 53,767**, CSS **14,889 / 14,913**, JSON **92,294 / 103,976**;
  all existing ceilings pass. Native fixture builds and JS syntax checks pass;
  real Chromium remains for the reviewer.
  Reviewer Chromium commands: `node tests/header_polish_browser.cjs /path/to/chromium`,
  `node tests/drop_shuffle_browser.cjs /path/to/chromium`, and
  `node tests/page_polish_browser.cjs /path/to/chromium`. With Chromium available,
  rerun `tests/test_brand_pages.py::test_real_chromium_screenshots`,
  `tests/test_drop_rotation.py::test_phone_drop_browser`,
  `tests/test_home_comment_strip.py::test_chromium_home_on_matches_off`, and
  `tests/test_search_analyze.py::test_real_chromium_light_dark_phone_desktop`
  (inline/lazy); these account for the five native skips. Retain task 306's
  search/Back checklist below. No full suite, live requests, commit or push.
  Existing staged integration work and screenshots are preserved.
- **302/303c/304 integration (Codex task 306, 2026-10-18; uncommitted):**
  Preserves the reviewed family-spacing implementation, balanced 90-card drops,
  lazy confetti, one-hour photo cutoff, event search and Support terms. Legacy
  full-wall/search harnesses explicitly disable fresh drops; the browse mock
  now maintains `location.href`. New all-on emitted logic verifies event queries
  survive a later drop-module mount without restoring Trending or replaying the
  query. Native comment-strip parity now checks 90 Trending cards, See all,
  ordinary pagination and return to Trending. Budget fixtures derive the visible
  offer count from the shipped photo cutoff; ceilings and 1,000-byte headroom
  remain unchanged. Task-303/304 rollback tests compare this config's own OFF
  render because their recorded base predates 302's reviewed identifier/regex
  changes; every recorded hash file is untouched, and older pins still run.
  The lazy selector resolves callbacks once per rotation and computes winner
  mix signatures once per row; ordering and the 50 ms assertion are unchanged.
  The requested **18 files: 546 passed, 5 Chromium skips in 506.51s** (`-n 2`).
  Both emitted 4,000-row timing cases pass the unchanged 50 ms limit. The earlier
  four-worker run had 545 passes and one 53.5 ms cold-selection miss under load;
  reducing worker contention made the complete run green, without changing a
  timing assertion. Focused rollback/query/idempotency checks also passed (11),
  as did the isolated fresh-loader/mix/confetti checks (2).
  Shipped phone fixture: **1,300 offers; home 76,005 / 77,360 gzip bytes**
  (**1,355 bytes spare**), JS **50,438**, CSS **14,412**, JSON **92,294**;
  every existing ceiling and the 1,000-byte reserve passes. Six shared browse
  consumers passed **143 tests, with 3 Chromium skips**. Offline e2e found two
  pruned-page fixture failures: the two-hour-old photo-less fixture no longer
  published before pruning. It now has an offline photo and asserts both the
  initial page and remembered redirect exist; all rerun/no-duplicate/no-alert
  assertions remain. The full e2e and posted-redirect files passed **70 tests in
  45.19s**. Final combined coverage: **759 passed, 8 Chromium skips across 26
  files**; no remaining test failures. All runs used `-p no:cacheprovider` and
  owned `/tmp/task306-*` basetemps, removed afterward. No hash was re-pinned.
  Native review remains: `node tests/drop_shuffle_browser.cjs /path/to/chromium`,
  `node tests/page_polish_browser.cjs /path/to/chromium`,
  `node tests/search_box_glow_browser.cjs /path/to/chromium`,
  `node tests/search_analyze_browser.cjs /path/to/chromium` and
  `node tests/search_more_browser.cjs /path/to/chromium`. Also rerun
  `tests/test_home_comment_strip.py::test_chromium_home_on_matches_off`
  (builds fixtures for `home_comment_strip_browser.cjs`),
  `tests/test_brand_pages.py::test_real_chromium_screenshots` and
  `tests/test_drop_rotation.py::test_phone_drop_browser`, plus the skipped
  `tests/test_home_links.py::test_home_links_in_real_chromium` and
  `tests/test_offscreen_cards.py::test_local_browser_restore`, with working Chromium.
  No full suite, live requests, commit or push. Git metadata is read-only in this
  sandbox, so branch creation was refused; edits remain in the provided detached
  worktree. Existing staged changes and screenshots are preserved.
- **Balanced 90-card drops + lazy confetti (Codex task 303c, 2026-10-18; uncommitted, ON):**
  Supersedes 303b's sixty-card/zero-overlap behavior below. Config ships 90;
  strict size validation remains 24..120 (missing size still defaults 60).
  Existing ranked, eligible, family-deduplicated rotation supplies fresh cards.
  Up to 22 matching late slots can carry the strongest previous-mark winners;
  the first 15 remain fresh, at least 68/90 are new, and returning winners move
  at least 12 positions. Matching mix signatures preserves women/impulse shares,
  clothing/checkout caps, category runs and Allure spacing. Lookahead excludes
  winners that would collide with the next rotation page. Small pools degrade
  within eligible inventory. Python static first paint and JS remain identical.
  `/drop-fresh.js?v=<content hash>` now contains selection, drop controls/styles
  and self-contained confetti, imported after a paint opportunity on home only.
  A small loader retains the static wall while loading and falls back to ordinary
  rendering on failure; OFF/missing removes the asset and keeps historical bytes.
  Confetti has two depth layers, nine colors, a 300-particle cap, DPR-aware fixed
  canvas below modals, and a hard ten-second cleanup. Hidden tabs, reduced motion
  and other views skip/cancel it. It never fires on initial load or replays when
  accepting the scrolled-down pill. Only fresh cards receive badges/entrances.
  Updated Node checks cover the real factory/loader, 20 marks, winners, mix,
  4,000-row timing and fake-canvas/rAF cleanup; native phone/desktop checks cover
  90 keys, canvas lifetime, reduced motion, geometry and the scrolled pill.
  The phone headroom test now derives its expected offer count from the existing
  photo cutoff (1,300), with all byte ceilings and the 1,000-byte reserve intact.
  Final focused run: **41 passed in 255.06s**, including OFF/missing/back-OFF
  hashes, strict config, Python/JS parity, real lazy-loader/factory and confetti
  tests, native fixture build, the unchanged phone headroom guard, parsing and
  idempotent publication. Home **76,354 / 77,360 gzip bytes** leaves **1,006 bytes**;
  JS **50,217**, CSS **14,421**, JSON **92,296** also pass their existing ceilings.
  An earlier wider run had 78 passes; its cold-timing and 32-byte headroom misses
  were corrected by precomputing selection priorities, testing the actual factory
  closure and sharing the inline view predicate with the lazy asset. No limits or
  hashes were loosened. All pytest calls used `-p no:cacheprovider --basetemp=/tmp/b303`.
  Chromium is intentionally left for the reviewer:
  `node tests/drop_shuffle_browser.cjs /path/to/chromium`.
  No full suite, network/LLM calls, commit or push. Existing unrelated work and
  screenshots preserved.
- **Sixty fresh Trending cards (Codex task 303b, 2026-10-18; uncommitted, ON):**
  Replaces partial 303's 30-card/300-pick selection. Strict `site.drop_fresh`
  still defaults false; `publish.front_page.fresh_page_size` defaults 60 and
  accepts integers 24..120. Trending/top shows one page plus See all, opening
  the existing All deals tab; its infinite-scroll control is absent. Other
  tabs, sorts and search retain their ordering/pagination (ON/OFF comparison).
  The complete eligible inventory is ranked using existing picks/scores and
  earning ties, deduplicated with task 302's family key, then balanced across
  women-oriented and mix groups. Consecutive circular slices have zero adjacent
  overlap with >=120 families, no return before wrapping, and maximal replacement
  for smaller pools (90 yields 30 new). A public full-inventory rotation duration
  is shown. Python and browser selections agree, including static first paint
  using the actual publication mark when a pipeline crosses a mark before publish.
  Women/category/allure/kids and a small title heuristic, impulse prices, clothing,
  checkout, Allure and category spacing are covered; insufficient inventory can
  relax impossible mix constraints, never eligibility/adult exclusion. Clothing
  and checkout flags now cover eligible unranked rows as well. No inventory is
  removed from All deals/search. Historical price rules and evidence stay intact.
  At a mark, scrollY <200 swaps with <=550ms staggering, badges and a four-second
  toast; a scrolled wall stays untouched with a fixed pill until tapped. Badges
  expire at the next mark; reduced motion skips animation; reload does not replay
  the moment. The superseded Trending-only window repair is omitted on the ON
  page; OFF/missing/back-OFF retain the three recorded hashes, with no re-pins.
  Final focused feature/drop/spacing run: **67 passed in 69.65s**. A separate
  shipped phone-budget regression passed (**48.32s**): home **77,101 / 77,360**,
  JS **52,347 / 53,767**, CSS **14,599 / 14,913**, JSON **92,293 / 103,976** gzip
  bytes. Node covers 1,200 eligible +400 excluded rows over 20 marks, varied mix,
  family deduplication, non-divisible/small pools, animations/pill, other views,
  and <50ms per mark on 4,000 eligible rows (including uncached construction).
  The existing extra-headroom test is NOT green: its fixed 1,393-offer assertion
  predates the already-present one-hour photo cutoff (1,300 with fresh OFF or ON).
  The new budget check derives that expected visibility from the existing cutoff;
  it does not change a ceiling or hash. Current HTML has only 259 bytes of margin,
  so it also falls short of the older test's extra 1KB headroom goal. Follow-up
  fixture maintenance and further size reduction remain before a fully green suite.
  Native fixture build with late-publication static parity: **1 passed in 13.83s**.
  Chromium deliberately not launched; reviewer command:
  `node tests/drop_shuffle_browser.cjs /path/to/chromium` (phone/desktop,
  normal/reduced motion, all 60 keys, badge/toast expiry, held scroll/pill, All
  deals, reload/Back, geometry/CLS; requests served locally). Initial stress-test
  layout and cold-performance failures were corrected; a missing test directory
  and a nonmatching synthetic search token were corrected too. Required checks
  used targeted pytest with no cache; main basetemp `/tmp/b303`, exploratory
  `/tmp/b303-*` scratch removed at completion. No full suite, live requests,
  LLM calls, commit or push. Existing unrelated work and screenshots preserved.
- **Event search + Support polish (Codex task 304, 2026-10-18; uncommitted, ON):**
  Strict top-level `page_polish` defaults false; config.yaml ships true. Black
  Friday/Cyber Monday headers get the shared rainbow field and purple Analyze
  button. Enter sends `/?q=`, Analyze sends `/?analyze=`; the homepage consumes
  at most 200 Unicode characters through its existing handlers after inventory
  loads, clears only these URL parameters with replaceState, and assigns the
  input value without HTML interpolation. Event pages make no search API calls.
  Support reuses brand-page hero/collage/icon styling and a matching Join block;
  Stripe anchors, amounts, renewal and for-profit/legal text remain verbatim.
  Shared search CSS is render-blocking and versioned, preserving first paint
  and the existing home payload reserve. Independent home styling switches
  still apply; OFF removes the asset and reproduces the three pre-edit hashes.
  `pre_page_polish` chains through the historical helpers; no hash re-pins.
  Initial feature run: **16 passed**. An added payload check found insufficient
  1 KB headroom with inline CSS; the shared stylesheet addresses that finding.
  Final targeted run: **49 passed in 145.42s** (`test_page_polish.py`,
  `test_card_tidy.py`, `test_search_box_glow.py`, with `-p no:cacheprovider
  --basetemp=/tmp/b304`). Home: **76,037 gzip bytes**, **1,323 bytes** below
  the unchanged 77,360-byte ceiling; the extra 1 KB reserve passes. Inline/lazy
  emitted scripts and both new Node harnesses pass syntax checks; diff checks
  pass. Owned temporary output removed. Native review remains:
  `node tests/page_polish_browser.cjs /path/to/chromium` (offline fixtures,
  inline/lazy Analyze, 360/390/1440 light/dark, both event routes, Enter/button,
  overflow/tap targets and Support screenshots in `docs/research/page-polish`).
  Chromium not launched; no full suite, network, commit or push.

- **Aligned header (Codex task 305, 2026-10-18; uncommitted, ON):**
  Strict `header_polish` defaults false and ships true. Desktop uses a centred
  search/action row, a 56px timer, compact controls below 1200px, and scrolling
  text categories with an active underline, edge fade and adjacent status.
  The rainbow border and purple Analyze styles remain; the desktop helper is
  hidden and search announcements retain their accessible status region.
  Phones retain the logo/actions, full-width search, helper and timer layout;
  category pills keep the body palette below the bar, with 44px header targets.
  A 945-byte gzip versioned stylesheet loads before first paint and counts toward the
  unchanged CSS budget. Removed obsolete flex/direct-child rules; rollback
  removes the stylesheet. Shared deal/event output is unchanged apart from
  the site version; seasonal chips remain outside the two-row header.
  Three pre-edit publication hashes cover OFF/missing/back-OFF. Historical
  `pre_header_polish` chains from `pre_task_295`, with raw-config rollback;
  no existing pin or budget ceiling changed. Initial checks exposed insufficient
  HTML headroom and a stale shared-page fixture; both were corrected.
  Targeted regression run: **48 passed, 2 deselected in 224.98s**. Final checks:
  **57 passed in 369.80s**, including all task-305 cases, historical drop/search
  pins, lazy/inline Analyze, card styling, shared pages, reruns and native fixture
  construction. Shipped home: **76,347 gzip bytes**, **1,013 bytes headroom**;
  CSS **14,876**, JS **52,476**, all within the unchanged ceilings. Node syntax
  and diff checks pass. All pytest runs used `-p no:cacheprovider
  --basetemp=/tmp/b305`; the owned basetemp was removed afterward.
  Native Chromium is deliberately not launched: the operator runs the
  [offline harness](research/header/README.md) for 900–2560 desktop, 390/360
  phone audits, light/dark, category/search interactions and shared pages.
  Screenshots/metrics go to `docs/research/header/`; native visual QA remains.
  No full suite, commit or push. Branch creation was refused by the read-only
  shared Git metadata; work remains in the supplied detached checkout.

- **Front-page audit identity (Codex task 309, 2026-10-18; uncommitted):**
  The old join indexed only unlocked, alive `deals.json.deals`: it missed
  deliberately displayed confirmed-gone cards and price-only offers held in
  `deals.json.spotlight`. A real `render_site` publication plus its emitted
  browser renderer reproduces a gone robot vacuum at #10 and a price-only
  spotlight at #1, both previously empty joins. Every fixture card exists in
  the combined published payload; no product payload bug or product edit.
  Static HTML cards have no `data-key`; keyed cards come from the browser's
  `deals`/`spotlight` lists. Top picks/clock shuffle reorder these lists;
  Woot/eBay use the same rows, with no separate staples/bestseller feed.
  The 48-card static cap/browser pagination does not cap the JSON inventory.
  Serialized keys are opaque IDs; `dealKey` uses key then dedupe_key and DOM
  attributes decode HTML escaping. No renderer key mismatch was found.
  The audit now snapshots DOM title/store/category and captures the Home
  page's actual same-origin JSON GET responses (no extra requests), including
  dead/spotlight rows. It cross-checks key/title/store; ambiguous fetched
  categories fail closed unless the DOM supplies one. This also avoids the
  earlier independent report fetch racing a deploy. Unknowns print their own
  `unidentified card #N key=<key>` line; positions 1–24 turn red, 25–48 report
  only. Real niche/car-part failures retain the same boundary.
  Targeted pytest with `-p no:cacheprovider --basetemp=/tmp/b309`: **4 passed**
  (renderer regression, site-check guard, two existing spotlight cases).
  Node logic/Home-response/screenshot harnesses: **2 passed**. Includes stale
  publications, entity decoding, DOM labels, unknown positions and genuine
  niche/car-part cases. Syntax/diff checks pass. Exact historical run keys
  were not available; live Chromium verification remains for review.
  No live requests, full suite, commit or push.

- **Seamless grids + product-safe Amazon labels (Codex task 310, 2026-10-18; uncommitted):**
  With `card_tidy` ON, publishing omits filler creation and its unused CSS from
  home, tabs, search/Analyze and every page carrying the shared deal actions.
  Grid borrowing remains unchanged. Analyze uses the product's own first five
  title words (at most 40 characters, escaped), or "See this product on Amazon";
  text queries and the price-drops fallback retain their wording. Bare ASINs
  are parsed/masked in both inline Analyze and its loader, including before
  loading or after import failure. OFF/missing/back-OFF compare against the
  build's own OFF render; no recorded hashes changed.
  Added emitted Node/Python coverage for local/live titles, absent/unsafe titles,
  escaping, length limits, ASIN/link inputs, loader masking, global filler
  coverage and preserved card borrowing. Final requested targeted tests plus
  publish/SEO/event regressions: **356 passed, 3 Chromium skips in 202.91s**
  (`-n 4 -p no:cacheprovider --basetemp=/tmp/b310`). Home is **76,107 gzip bytes**,
  leaving **1,253 bytes** below the unchanged 77,360-byte ceiling. Initial
  failures (stale page fixtures and CSS-only suppression missing the extra
  headroom by 38 bytes) are resolved. Node syntax/diff checks pass; owned
  `/tmp/b310` scratch removed. No remaining targeted failures.
  Native review remains: `node tests/search_analyze_browser.cjs /path/to/chromium`,
  `node tests/search_more_browser.cjs /path/to/chromium`, and the search-box
  [Chromium capture commands](research/search-box/README.md). Check the shipped
  card_tidy ON build on phone/desktop: home/tabs, search, Analyze and deal/event
  scrolling, ASIN/link titles and generic fallback. No full suite, commit or push.

- **Honest Analyze headings (Codex task 312, 2026-10-18; uncommitted):**
  Behind `card_tidy`, inline/lazy Analyze uses the card's verification tier and
  unconfirmed markers for "Your deal" versus "Your verified deal". Related
  headings claim verification only when every displayed photo card qualifies;
  progress/comparison copy no longer assumes the inventory is verified.
  OFF/missing/back-OFF bytes and recorded hashes are preserved. Added Python/Node
  coverage across lazy/glow/tidy modes, real serialized tiers, mixed related
  cards and later publications. Requested targeted run: 103 passed, 3 browser
  skips; four new fixture failures fixed, then all 8 heading cases passed.
  Phone gzip budget retains its 1,000-byte headroom; no pins changed. Owned
  `/tmp/b312` removed. Review remains; no full suite, commit or push.

- **Same-product family spacing (Codex task 302, 2026-10-18; uncommitted, ON):**
  Strict top-level `same_product_spacing` defaults false; config.yaml ships true.
  Store + first five meaningful title words identify families, stripping variant
  sizes/colors/pack counts. Final first-paint and browser ordering aim for eight
  intervening cards and at most two family listings in the first 48, including
  spotlight boundaries, Trending, All deals, categories, sorts and clock drops.
  Highest-ordered siblings stay first; repeated families cannot trade places in
  the clock shuffle. Existing gone/clothing/checkout/Allure safeguards remain.
  Review 302b retains every card even when spacing is impossible: scan at most
  60 pending rows, use the first if none fits, and space only the first 240
  positions (including spotlights). The remaining tail keeps its order. Python
  and JS normalize only the reachable 299-row window, reduced by prefix length.
  No source, price, evidence, budget or state changes.
  Three pre-edit publication hashes cover OFF/missing/back-OFF; historical
  `pre_same_product_spacing` chains from `pre_task_295`, with raw-config rollback
  and no re-pins. Python/Node parity, full-publication inventory and boundary
  regressions added. Final focused run: **23 passed in 156s**, including all
  task-302 tests, both emitted-drop modes, the native fixture build (no launch)
  and the unchanged phone budget/headroom test. Earlier feature/drop/search
  checks: **52 passed**; four older publication hashes also passed. The first
  inventory fixture lacked fresh Amazon timestamps; corrected. The initial
  91-byte extra-headroom miss was resolved by simplifying emitted ordering code,
  not changing a ceiling: shipped home **76,354 gzip bytes**, limit **77,360**
  (1,006 bytes spare). All pytest runs used `-p no:cacheprovider
  --basetemp=/tmp/b302`; owned scratch removed. Syntax/diff checks pass.
  **302b review fixes:** 30-family/10-other inventory, bounded lookahead, prefix
  and position-240 tail cases now match in Python/JS. A five-run Node benchmark
  keeps all 4,000 rows (300 ten-member families + 1,000 singles), median
  **4.83 ms**, below 50 ms. Initial review run: 54 passed; extra phone headroom
  missed by 72 bytes. Simplified bounded JS resolved it without changing limits
  or pins. Final targeted run: **25 passed in 133.84s**, including OFF bytes,
  emitted drop logic, native fixture build and phone headroom. Review runs used
  `-p no:cacheprovider --basetemp=/tmp/b302b`; owned scratch removed.
  Native check (not launched here): `node tests/drop_shuffle_browser.cjs /path/to/chromium`
  now includes four same-family variants, spacing at first render/All deals/drops,
  phone/desktop, motion modes, reload/Back and layout checks. Full suite and
  native review remain for the senior engineer; no network, commit or push.
- **Search token settlement + robot isolation (Codex task 298, 2026-10-18; uncommitted):**
  All five live-site Playwright scripts intercept `/api/search` before navigation;
  empty fixtures preserve inventory/empty-state checks, and first-screens asserts
  a synthetic product/photo. Service workers are blocked. Its only explicit live
  API probe is fixed `q=cupping`, using the existing six-hour cache. Visitor watch
  also catches redirected search requests. D1 atomically creates a pending
  reservation with each 110-token debit, then consumes it while refunding trusted
  integer receipts in the original day's row; retries cannot refund twice.
  Missing/untrusted receipts keep the debit, and oversized-receipt halts survive
  late settlements. Minute/host gates and other requests' debits stay intact.
  [Capacity report](research/search-tokens.md): at the test fixture's 20 tokens,
  5,000/day permits **45 → 245** uncached pages (3,000/day: **27 → 145**), retaining
  room for the next full reservation. Final targeted pytest: **6 passed in 20.25s**
  (function, all robot routes, visitor/mobile logic, site-check logic/screenshots).
  Earlier targeted run: **4 passed**. Both used `-n 1 -p no:cacheprovider`; owned
  `/tmp/task298-*` basetemps removed. Offline only; no commit or push. Review,
  full-suite validation and live browser verification remain before deployment.
- **Clock-driven Trending + Amazon above matches (Codex task 300, 2026-10-18; uncommitted, ON):**
  `site.drop_shuffle` is a strict boolean, missing/false keeps legacy drop bytes.
  The frozen timer waited for a new publication; unchanged/failed fetches reset
  the countdown without presenting anything, and unequal clothing counts could
  reject the old whole-window rotation. A final ordering pass now selects a
  deterministic, distinct first-15 permutation per clock mark from eligible
  ranked picks in the first 120 slots. It keeps 24-card membership and exact
  clothing/checkout/Allure slots, so store/brand/boost shares, category runs,
  photos, niche/team demotion, gone spacing and spotlight exclusion survive.
  No unsafe cards are promoted to manufacture variety in a depleted pool.
  UTC cadence marks prevent a late publication from shifting the timer or
  rolling its seed backward. Drops redraw even when fetching fails or times out;
  Trending reload/Back uses
  the current mark, independent of stale saved seeds or arrival-only storage.
  Clock-only changes use no FLIP motion and preserve a scrolled reader's anchor.
  Matching searches show the tagged circling Amazon button above local cards,
  including before pagination/module loading finishes; related Amazon cards stay
  below with a small text link. Existing empty-search/Analyze behavior remains.
  Three pre-edit drop rollback publication hashes were recorded (search_more
  disabled to isolate the separate search change); `pre_task_300` chains from
  `pre_task_295`, including older raw fixtures. No existing hash was re-pinned.
  Final focused runs: **54 passed** (both ordering modes and mix rules),
  **53 passed** (drop/search/pins), **40 passed** (late publishes/lazy Analyze).
  The final drop/native-fixture/phone-budget run had **13 passed**, including
  unchanged ceilings and the extra 1 KB headroom; its two new scroll probes
  overshot a safely shortened wall. Corrected to a rendered card, with an
  explicit full-first-15 assertion: **2 passed**. Initial failures from stale
  bottom-button assertions, missing clock helpers in the old mix harness and
  accumulated test fixtures exhausting /tmp are resolved. The initial scoped
  run also passed all 75 existing non-Chromium rotation checks. All runs used
  `-n 1 -p no:cacheprovider`; every owned `/tmp/task300-*` directory was removed
  after its run. Node syntax and diff checks pass; no existing hash changed.
  Chromium is deliberately not launched.
  Native checks: `node tests/drop_shuffle_browser.cjs /path/to/chromium` and
  `node tests/search_more_browser.cjs /path/to/chromium` (all requests fulfilled
  from local fixtures; owned temporary directories removed). Review and native
  layout/motion QA remain. Offline, this worktree only; no commit or push.
- **Search attention + early Amazon button (Codex task 301, 2026-10-18; uncommitted, ON):**
  Strict `search_box_glow` defaults false and ships true. The unchanged search
  input geometry gets a positioned 3px rainbow border, one turn every 6s,
  paused on focus/entered text; reduced motion leaves a static rainbow.
  Analyze/Go reuses the golden shine/twinkle with positioned decoration.
  Analyze results put one circling, tagged Amazon button immediately after
  the first heading and before verified cards; errors retain their generic
  label. Related-search results put it above the cards even with 70+ local
  matches, reuse Analyze's button, and keep a small text link at the bottom.
  Task 300's placement was absent; edits stay in the Analyze/related paths.
  Existing privacy-safe click handlers, pagination and affiliate rules remain.
  Six pre-edit publication hashes verify OFF/missing/back-OFF; `pre_task_295`
  chains `pre_task_301`, including older raw-config fixtures, without re-pins.
  Final task-specific checks: **19 passed**; phone-budget/lazy-Analyze/circle
  regressions: **31 passed**. Shipped home is **73,739 gzip bytes** against
  the unchanged **77,360-byte** ceiling; the extra 1KB headroom check passes.
  Final inline/lazy/local/error placement recheck: **5 passed**; no remaining
  targeted failures. Syntax and diff checks pass.
  Initial harness/config mismatches were corrected. A broader run hit a full
  temporary filesystem; only owned scratch was removed, then tests were
  rerun with per-test cleanup. Every run uses `-n 1 -p no:cacheprovider` and
  an owned `/tmp/task301-*` basetemp deleted afterward. No network, full suite,
  Chromium launch, commit or push. Native screenshots/geometry remain pending:
  [commands and capture script](research/search-box/README.md) check 390/1440
  light/dark, normal/reduced motion, exact OFF/ON headerH/firstCardTop and
  field/button bounds, local/ASIN/error results, tagged clicks and overflow.
- **Write less state (Codex task 296c, 2026-10-18; uncommitted, ON):**
  Strict retention switches intern accuracy receipt keys/timestamps and pack
  click exposure into exact hourly slots per deal/day. Mixed-state reports,
  six-hour sampling guards, learner features and switch-back reads remain
  equivalent; no evidence, cap, request or public-output changes. Existing
  bounded maintenance migrates old rows atomically, expires compact records
  and clears unused receipt dictionaries. Missing/false preserves old writer
  bytes. Production-shaped measurement and R2 design (no storage code) are in
  [state-size research](research/state-size.md). Initial fixtures found and
  fixed a migration-bookkeeping replay issue; legacy accuracy assertions now
  run against both formats. Final targeted checks: **224 passed in 86.54s**,
  including 296b retention/compaction, both pipeline rerun modes and unchanged
  publication/Node output. All runs used `-n 1 -p no:cacheprovider`; owned
  `/tmp/task296c-*` basetemps/models were removed. Syntax/diff checks pass.
  Production-shaped 3,300-deal/2,000-hourly-recheck/120-runs-per-day model:
  encrypted growth **4,094,976 → 1,478,944 bytes/day** (mean **34,125 → 12,325
  bytes/run**); 296b hourly deal payload writes **7,737,056 → 732,500 bytes**.
  **Below-70-MB target remains unmet:** saturated model **95.61 → 83.94 MB**
  encrypted, **93.94 MB** after allowing 10 MB for other state. Unchanged
  post/audit receipts at the full observed-rate × window alone measure
  **60.63 MB encrypted**. Further lossless receipt compaction or measured
  lower arrival rates are needed; do not claim production saves are assured.
  Offline only, no commit or push; size target and production rollout remain.
- **Honest empty searches (Codex task 297, 2026-10-18; uncommitted, ON):**
  The `search_more` ON path starts with `See every <query> price on Amazon`
  above the existing circling button; only rendered current-price cards restore
  the live-price heading. Empty/error/disabled results and expired cached cards
  leave no grid. Button labels, affiliate destinations and rel attributes stay
  unchanged; later related-page failures keep earlier cards and the final button.
  Node regressions cover every requested empty reason, bounded retries, cached
  cards and module/network failures. Native browser cases add budget exhaustion
  and quiet later-page caps across inline/lazy Analyze and phone/desktop themes.
  The five recorded search_more OFF/missing/back-OFF pins and supplier-name
  guard pass. Six historical lazy-Analyze captures included search_more ON:
  their toggle regression now compares current inline bytes, retaining the
  recorded more-OFF pin and every stored hash without re-pinning. Initial
  targeted checks: **4 passed**, then **102 passed, 6 historical ON snapshot
  failures**. Final corrected toggle checks plus shipped phone budget: **8 passed
  in 104.75s**. No remaining targeted failures; all recorded hashes are untouched.
  All pytest runs use `-n 1 -p no:cacheprovider` with owned `/tmp/task297-*`
  basetemps removed after each run. Function Node harness and syntax checks pass.
  Native commands/captures: [search-more research](research/search-more/README.md)
  and [Analyze research](research/analyze-search/README.md). Chromium is not
  launched here; no network, full suite, commit or push. Review/native QA remain.
- **State growers (Codex task 296b, 2026-10-18; uncommitted):**
  Reader audit and limits in [state-size research](research/state-size.md):
  accuracy 9d, click exposure 8d (longer learner lookback wins), source audit 9d
  after both discovery/parse completion (pending discoveries stay), Keepa 12
  samples/retained deal (configured reader count wins). Dead deals retain the
  standing 14d policy; live inventory and 30d post parse receipts stay.
  Duplicate deal SQL fields/sightings compact with exact-match checks and full
  read/read-only reconstruction. Already-known posts cannot recreate expired
  discovery counters. Shared cooperative maintenance uses 64-row transactions,
  4,096 examined/table/run and 1s within the existing tail deadline, durable
  counters/cursors and fair resumption. Fast VACUUM defers; hourly/manual policy
  stays. Missing knobs preserve legacy windows/writers. No evidence/cap changes.
  Initial targeted runs: **37 passed**, **341 passed**, including both pipeline
  rerun modes and retained reader/revival/money checks. All use `-n 1 -p
  no:cacheprovider`; own `/tmp/task296b-*` basetemps removed after each run.
  Final targeted checks: **144 passed in 73.21s**, covering new maintenance,
  incremental-cache compatibility, evidence, protected money/facts, full
  publication/Node parity, unchanged frozen fetch snapshots and both pipeline
  rerun modes. No failing targeted checks remain; diff checks pass.
  Adversarial review proved an interleaved-run sweep replay bug: after another
  connection finished its sweep, a resumed run could join a new sweep and
  examine rows again. Cursors and run counters now share the initiating run's
  identifier, so that run finishes without disturbing a successor's cursor.
  Two reproductions failed before the fix; expanded coverage exercises all
  three swept tables, both raw category repairs, social stock facts and disabling
  future compact writes. First review regression run: **330 passed** (including
  publication, social/Pinterest, growth reports, evidence and both pipeline
  rerun modes). Final retention-reader run: **412 passed in 314.63s**, including
  the ten new cases, accuracy/click windows, history schedulers, source audit,
  dedupe/revival and incremental-cache compatibility. No further fact-loss defect
  found. Review changes are unstaged; no commit/push. All review pytest runs use
  `-n 1 -p no:cacheprovider` and owned
  `/tmp/task296br-*` basetemps, deleted after each run.
  **70 MB target unresolved:** a sustained-rate/whole-state-compression proxy
  estimates 141.20 MB for the four dated tables alone; live inventory has no
  finite bound. The 296 IndexNow-heavy synthetic projection is superseded by
  the new production mix. Compaction-only 2,000-offer/40,000-sighting fixture
  preserved every fact but saved only 0.169 MB encrypted (4.756 → 4.587 MB).
  Do not claim this guarantees continued production saves. Actual row ages,
  post-maintenance compressed sizes and drain rate remain for operator review;
  sustained growth needs a further lossless storage/aggregation change.
  Review and the production-size target remain; no network, full suite, commit
  or push.
- **Lazy Analyze (Codex task 295, 2026-10-18; uncommitted, ON):**
  Strict `search_analyze.lazy` defaults false and ships true. A final publishing
  pass moves the composed Analyze logic and CSS into a versioned, interaction-only
  module, preserving search_more's spelling/shared cards/cancellation/error/hint
  swaps. The loader masks links before loading, replays the current submit once,
  adopts pending text lookups and leaves plain search plus a tagged Amazon button
  usable after import failure. Missing/false/back-false retain seven pre-edit
  publication hashes; `pre_task_295` chains from older fixture helpers without
  re-pinning. Removed the headroom test's Analyze-OFF override; shipped config
  passes the unchanged 77,360-byte ceiling and its extra 1 KB headroom assertion.
  Initial targeted check: **23 passed, 2 failed in 81.78s** (shipped-layout Node
  harness lacked a wall-ordering stub); corrected. Follow-up emitted logic,
  shipped headroom and native fixture checks: **8 passed in 132.93s**.
  Final search/legacy-pins/privacy/feature/rerun checks: **129 passed in 326.68s**.
  Final Analyze native-fixture harness and shipped phone headroom: **2 passed in
  106.66s**. Shipped home is **73,312 gzip bytes**, 4,048 below the unchanged
  77,360 ceiling; JS/CSS/data ceilings also pass. Node syntax and diff checks pass.
  Native 390/1440 light/dark inline/lazy Analyze and search_more QA remains for the operator; commands in
  [Analyze research](research/analyze-search/README.md). No Chromium launch,
  network, full suite, commit or push. Every pytest run used `-q -n 1 -p
  no:cacheprovider`; owned `/tmp/task295-*` fixtures were deleted after each run.
- **State size guard and retention (Codex task 296, 2026-10-18; uncommitted):**
  `commit_state.sh` reports top-eight table/index bytes (dbstat, page-count fallback)
  and actual gzip/encrypted sizes once per invocation, including no-ops; it warns
  above 70 MB and refuses at/above 90 MB before remote access. Retries reuse the
  measured snapshot; differently sized old encodings cannot bypass the guard.
  New retention knobs ship seven days: completed IndexNow version maps become
  `{}` while permanent run/status receipts and sending maps remain; expired appeal
  scores are removed only beyond both retention and the configured reader TTL.
  Missing/null knobs preserve prior behavior. Evidence, all-time history/sightings,
  social dedupe, AI-take caches and spend protections remain. Synthetic real-schema
  state measured 253.82 MB raw / 58.90 MB encrypted → 34.60 MB encrypted; conservative
  estimate buys 5.4 days at unchanged growth, conditional on the modeled table mix.
  Offline targeted runs: **101 passed**, **113 passed**, **76 passed**, all with
  `-n 1 -p no:cacheprovider`; owned basetemps removed after each run. Includes rerun
  idempotency, retained-reader fixtures, money caps, encrypted restore/save, size
  boundaries/fallback, identical publication bytes and the emitted Deal Check Node
  harness. Shell/Python syntax and diff checks pass. Browser regression written;
  Chromium deliberately not launched here. Native command, measurement script and
  remaining production-size confirmation: [state-size research](research/state-size.md).
  Adversarial review found one save blocker: empty/comment-only/YAML-null configs
  accepted by `load_config` were rejected by the size guard. Matched its `None`
  normalization; three encrypted-save regressions failed before and pass after.
  Final size/retention regressions plus temporary local Git race and gzip/OpenSSL
  failure probes: **47 passed**. No further defect found in snapshot reuse,
  unchanged-state comparison, gzip determinism or retained-reader/run anchors.
  Broader state/workflow/appeal/IndexNow/offline-e2e run: **241 passed, 1 skipped,
  7 failed** solely because existing Stripe fixtures cannot open localhost sockets
  in this sandbox. All review pytest runs used `-n 1 -p no:cacheprovider`; owned
  `/tmp/task296r-*` basetemps and temporary probes removed.
  Native browser QA/deployment remain; no network, full suite, commit or push.
- **Plain source credits (Codex task 294, 2026-10-18; uncommitted, ON):**
  Strict `site.credit_links` defaults true and ships false: muted plain attribution
  on home/spotlight/static cards, category/store/price/best/event pages, deal/product
  pages and Deal Check. Get deal destinations and public/member credit data stay
  unchanged; RSS/email/Telegram/social generation is untouched. Comparison pages
  have no source credit links and remain byte-identical. The previous gone-card cap
  was one in eight; plain mode enforces one in ten after final ordering, before
  pagination, preserving live order. True/missing/back-true match four pre-edit
  full-publication hashes and existing history pins, without any hash re-pins.
  The supplied search_boost/search_circle/Analyze/Install release is retained.
  Emitted Node checks cover plain credits/escaping, Get deal, sorting/search,
  pagination, row filling, Deal Check and the remaining tracking events. Product
  reader credits explicitly use the muted color despite their reduced stylesheet.
  Initial fixture/assertion fixes: **4 failed, 25 passed in 40.46s**;
  **6 failed, 1 passed in 15.35s**; **4 failed, 2 passed in 18.75s**; all corrected.
  Broad targeted release/legacy/feature/rerun run: **1 failed, 137 passed in
  242.54s (0:04:02)**. Its sole failure was the extra 1 KB phone headroom guard,
  already missed by the supplied release (77,105 gzip bytes; ceiling 77,360).
  Plain mode removes unused credit-link CSS/click tracking and compacts CSS
  whitespace while preserving literals, URLs, comments and escapes. Home is now
  **76,184 gzip bytes**, with 1,176 bytes headroom; all original ceilings remain.
  Final affected-feature, frozen-byte, phone-headroom and both real pipeline
  rerun modes: **48 passed in 222.05s (0:03:42)**. No failing targeted checks remain.
  Node syntax and `git diff --check` pass. Offline only, `-n 1 -p no:cacheprovider`;
  every owned `/tmp/task294-*` basetemp/probe directory was removed after its run.
  No network, full suite, commit or push. Review/deployment remain.
- **Related Amazon pages + spelling (Codex task 293, 2026-10-18; uncommitted, ON):**
  `search_more` ships enabled, three pages, two-character minimum; missing/false
  restores the supplied release byte for byte. After all matching local cards,
  an intersection loads Amazon pages 0–2, deduplicated against visible ASINs and
  one another. Adult exclusions, current-price clocks, tagged/sponsored links
  and the circling final Amazon button remain. Rate/budget failures stop paging
  quietly. Page bounds/cache keys use the existing reservation, minute/day caps,
  lease, completion cooldown and Retry-After path; ASIN lookups stay unchanged.
  Only successful empty local/Amazon searches can try one spelling correction,
  using live-title vocabulary/bigrams and 878 gzip bytes of bundled shopping
  words. Digit-bearing identifiers stay intact; a link restores the original.
  Corrected lookups (including corrected local matches' first Amazon page) wait
  for the cooldown. Analyze adopts an already-running corrected request.
  GA4 receives only page integers or the corrected boolean, never query text.
  The local search module and shared Amazon card renderer load on first search;
  the initial home page retains the existing phone ceilings. Five pre-edit
  publication hashes cover OFF/missing/back-OFF; `pre_task_293` preserves older
  fixtures without changing any expected hash. Existing unpushed release retained.
  Final search/install/history-pin/analytics/public-template regressions:
  **174 passed in 146.19s (0:02:26)**. Final emitted-module/correction timing:
  **4 passed in 5.59s**. Shipped phone budget: **1 passed in 47.28s**.
  Function harness: **43 passed, 0 failed**; Node syntax and diff checks pass.
  All pytest runs used `-q -n 1 -p no:cacheprovider`, targeted selections only;
  each owned `/tmp/task293-*` directory was removed after its run.
  Follow-up 293F: the operator's Chromium timeout was a fixture failure. Its 24
  Amazon mice had no price confirmation and were 60 hours old at publish time;
  the 48-hour guard hid them, leaving one card while the script waited for 20.
  Fixtures now carry publish-time confirmations and assert all 24 mice survived
  before launching Chromium. Named waits report viewport/theme, condition, card
  counts, query/page text, API calls and captured errors; page-count waits tolerate
  automatic later-page arrivals while final count/request assertions stay exact.
  The Function adds optional `list_price_cents` only from safe-integer current
  LISTPRICE greater than the selected price, shared by text, ASIN and paged
  answers; old cached items remain usable. The ON-only shared card builder shows
  accessible List Price strikes and matching percentage badges at rounded 5%+
  across ordinary results, Analyze and related pages. No historical comparison.
  Follow-up targeted checks: **39 passed in 76.98s (0:01:16)**, including Function
  cases, card markup with/without photos, threshold/malformed values, Analyze,
  fixture/timeout regressions and all five unchanged OFF/missing/back-OFF pins.
  Own `/tmp/task293f-*` scratch was removed after each run. Native 390/1440
  light/dark QA (now also capturing List Price on Analyze) remains for the
  operator: script and command in [search-more research](research/search-more/README.md).
  No native screenshots, network calls, full suite, commit or push; review/deployment remain.
  Follow-up 293G: the operator's next Chromium run passed paging/List Price and
  failed at spelling undo. The ON-only CSS now reserves the hint's space when
  input blur hides it, preventing the undo link from moving during a click.
  Undo restores exact input text (including case/spacing) and keeps correction
  suppressed until a real text edit. Successful empty answers can then correct
  again from cache without another request; failures cannot. Offline regressions
  cover unchanged-input events, rerenders, edit/undo, stale corrected replies,
  and the remaining native coffee → ASIN Analyze → rate-limit sequence in the
  same mounted module. The original browser assertion stays; added checks cover
  blur geometry, settled undo and keyboard undo after editing. Native rerun
  remains with the operator: this sandbox still denies Chromium launch.
  Final targeted checks: **12 passed in 94.55s**, including all five unchanged
  OFF/missing/back-OFF pins; emitted logic/syntax: **4 passed in 9.05s** across
  Analyze/boost/access/comment modes. Node syntax and diff checks pass. All
  pytest runs used `-q -n 1 -p no:cacheprovider`; owned `/tmp/task293g-*` scratch
  was deleted after every run. No network, full suite, commit or push.
- **Header Install app (Codex task 290, 2026-10-18; uncommitted, ON):**
  Strict `pwa.install_button` defaults OFF and ships ON. Header prompt / compact
  phone App button, iPhone/iPad Safari dialog, seven-day dismissal and GA4-only
  install events share the existing footer install controls. Original PWA source,
  manifest, icons and request behavior stay unchanged. Six pre-edit publication
  captures pin every file with OFF/missing; historical helpers preserve existing
  hashes. Reserved button space prevents prompt-driven search movement; the native
  Safari dialog uses `showModal()` plus explicit Tab/Shift+Tab containment for
  its sole close button, with X/Escape/backdrop close and opener focus return.
  Node checks cover prompt races, footer use, iPhone/iPad, standalone, social
  browsers/Android, dismissal expiry, blocked storage and deduplicated events.
  Initial feature run: **25 passed in 26.67s**. Final targeted feature/PWA/tracking,
  recorded Home/Android bytes, shipped phone budgets, both offline pipeline reruns
  and public-template checks: **153 passed, 1 skipped in 210.71s (0:03:30)**.
  No failing targeted checks; existing byte hashes and phone ceilings unchanged.
  Header normal/hover text contrast is 7.10–16.22:1 across light/dark themes.
  Follow-up 290f: the operator's external Chromium run found empty manifest
  errors and only `in-incognito` (the harness ignores exactly that ID). Four
  home-page captures/layout checks passed; the iPhone Tab check exposed focus
  leaving the sheet. The added Tab/Shift+Tab containment fixes that path without
  changing OFF output. Node tests now track the active element; browser checks
  repeat both Tab directions, verify native modal state, wait for native Escape
  closure, keep inside clicks open and verify header/footer focus return.
  Chromium still cannot launch inside this sandbox; the updated native dialog
  rerun, two sheet captures and completed report remain pending. Details and
  screenshot paths: [install-button research](research/install-button/README.md).
  Follow-up install-button checks (including all six OFF/missing tree pins and
  rerun/analytics cases) plus shipped phone headroom: **26 passed, 1 skipped in
  149.01s (0:02:29)**; the skip is the sandbox Chromium launch. The strengthened
  Node harness also passes directly against the source script.
  Node syntax and `git diff --check` pass. All pytest runs use
  `-q -n 1 -p no:cacheprovider`; every owned `/tmp/task290-*` and `/tmp/task290f-*`
  basetemp was removed after its run. No network, full suite, hash re-pin, commit
  or push.
  Review/native browser QA/deployment remain.
- **Analyze header search (Codex task 291, 2026-10-18; uncommitted, ON):**
  Strict `search_analyze.enabled` ships ON, layered over the supplied unpushed
  search_boost release. Analyze/phone Go and Enter accept product names or full
  US Amazon links; short/unsupported links get fixed guidance. URLs stay out of
  requests, persisted browse state and analytics; only the extracted ASIN reaches
  the product lookup. Existing live deals lead with their own cards/links;
  otherwise one current Amazon-price card leads related live deals. Adult
  exclusions apply in both paths, including when search_boost itself is OFF.
  Honest promise-driven steps remain visible for at least 900 ms; local matches
  explicitly say they use our verified deal. Reduced motion stops the spinner.
  An already-running text lookup is reused, retries remain bounded, and later
  publications/expired clocks cannot revive old results. GA4 accepts only counts
  and booleans for the three new events. `/api/search?asin=` uses the same
  110-token D1/KV reservation, minute gate, lease, cooldown, Retry-After, gzip and
  fixed-error path as text search; ASIN/policy cache keys contain no secret.
  Five pre-edit complete publication hashes verify OFF/missing/back-OFF; current
  historical fixtures use `pre_task_291`, with no hash re-pins. Targeted feature,
  existing searches, 34 Function Node cases, original byte pins, shipped phone
  budgets and both pipeline rerun modes: **107 passed, 4 skipped in 373.41s
  (0:06:13)**. Earlier focused checks: **9 passed in 61.81s (0:01:01)**; the first
  **4 failed, 22 passed in 17.58s** were an escaped-apostrophe harness assertion,
  corrected without weakening the product check. Final emitted-code checks for
  private mixed input, fixed-vocabulary errors, arrivals during lookup, both
  boost modes, phone budgets and public-copy templates: **8 passed in 49.21s**.
  Native Chromium and its headless executable were both denied by sandbox
  permissions; no screenshots were captured. The offline 390/1440 light/dark
  harness and capture instructions are in `tests/search_analyze_browser.cjs` and
  `docs/research/analyze-search/README.md`; native visual QA/review remain.
  All pytest runs use `-q -n 1 -p no:cacheprovider`, targeted selections only,
  and owned `/tmp/task291-*` basetemps removed in `finally` after each run;
  none remain. All four skips were native browser checks.
  No network, full suite, commit or push; existing release edits are retained.
- **Brand pages (Codex tasks 289 / 289F, 2026-10-18; uncommitted, OFF):**
  `site.brand_pages: false` keeps the supplied four-mode OFF pins unchanged.
  ON adds a shared bold gradient hero, public top-pick photo collage, icon
  cards around the existing text, a Why wowpriced band and publication counts.
  Careers adds values and numbered daily steps; Join keeps its existing form.
  289F tightens the layout to 1100px, with pill actions, 16–20px rounded cards,
  layered shadows and an 8px spacing grid. About adds four factual
  "What makes us different" one-liners and Work with us / Contact us pills.
  Cards fade/rise 8px once on entry and buttons lift on hover; reduced motion
  disables both, and content stays visible without scripting/observer support.
  Shared signup uses 25 seconds OR half-scroll, daily memory, the existing
  dismissal/join keys and close/focus behavior; /join never auto-prompts.
  Brand-page signup uses embedded public metadata and email only, with no new
  provider request. **Operator 2026-10-17 8:31pm: keep existing Join wording
  everywhere.** Removed round-one "first issue soon" substitutions and custom
  popup headlines; brand prompts use the homepage's existing "Save up to 90% OFF"
  headline and email wording for either newsletter setting. Header/footer copy,
  section order, SEO and JSON-LD stay. Collage prices/discounts come only from
  public fields, with condition/price-step notes and existing Amazon timestamps;
  sparse publishes show fewer cards, never filler.
  Chromium harness line-62 failure traced to queued refresh frames and a running
  fake clock, not a different storage key. Harness now pauses before navigation,
  uses `runFor` through the timeout and its next frame, and waits for native scroll
  delivery. Independent cases clear `wp.join.promptUntil` and `wp.join.joined`;
  reload/cross-page checks retain daily memory and existing subscribers stay quiet.
  Offline queued-frame regression covers a second independent non-Join page.
  Targeted 289F verification: **116 passed in 100.23s (0:01:40)**, covering all
  four missing/OFF/toggle-back pins, preserved copy/SEO/public prices, shared signup
  and support behavior, motion fallbacks, static text contrast and rerun state/output.
  Includes the 1,400-offer shipped phone fixture (1,393 public) and brand-page
  HTML/JS/CSS against unchanged ceilings. No failing targeted checks remain.
  Native Chromium is unavailable here and was deliberately excluded this round;
  the operator will run `tests/brand_pages_browser.cjs`. Screenshot refresh,
  native visual/phone QA and preview remain before enabling. Paths and offline
  fixture limits: `docs/research/brand-pages/README.md`.
  `git diff --check` and Node syntax pass. The targeted run used
  `-q -n 1 -p no:cacheprovider`; its owned `/tmp/task289f-*` basetemp was deleted.
  No network, full suite, hash re-pin, commit or push.
- **Home links start fresh (Codex task 286, 2026-10-17; uncommitted, ON):**
  Strict `site.home_link_resets` defaults OFF and ships ON. Plain logo,
  Live deals and timer clicks reuse the Amazon-selection reset, clear every
  browse selection/Saved, persist Top picks, render from the first page and
  scroll to the top, pushing history so Back restores the previous search.
  Modified clicks and the pre-load logo retain native navigation. Ordinary
  home navigation starts fresh; explicit public URL selections still apply,
  while reload/Back retain the existing exact-anchor restore. Ticker/timer
  have pointer cursors and titles with no new focus stops. One GA4-only
  `home_reset` allows logo/ticker/timer and drops all other input.
  Five pre-edit full-tree captures cover missing/OFF and switching back OFF;
  historical helpers explicitly disable the new knob, with no hash re-pins.
  Emitted-JS checks cover all three access modes with comments ON/OFF.
  Feature/search/shipped phone budgets/both pipeline rerun modes:
  **35 passed, 1 skipped in 233.27s (0:03:53)**. Final extended emitted-JS
  card-120 restore plus eight existing frozen byte checks:
  **14 passed in 52.16s**. Initial harness runs were **6 failed, 17 passed,
  1 skipped in 72.56s (0:01:12)** and **4 failed, 2 passed in 15.85s**;
  corrected cross-realm object comparison and the history-length assertion
  when a new entry after Back replaces Forward. No failing targeted checks remain. Chromium
  launch returns 77; native phone/desktop QA remains unverified. Node syntax
  and `git diff --check` pass. All pytest runs use `-q -n 1 -p no:cacheprovider`;
  every owned `/tmp/task286-*` basetemp was removed after its run. No network,
  whole suite, hash re-pin, commit or push. Review/native browser QA remain.
- **Release C adversarial review fixes (2026-10-17; local, uncommitted, fixes complete):**
  The existing local Release C config remains `compare.refresh: true`,
  `compare.publish: "preview"`; no deployment or activation was performed.
  Comparison sale badges consume the published deal projection while retaining
  private confirmation/evidence gates. Regressions cover private 80% projected
  to 50%, null and an entirely omitted public percentage; private percentages
  cannot supply a badge. Clothing is a greedy insertion constraint: overflow
  is deferred by score and re-offered at the next available window, with all
  checkout, store, brand, category, twin and run checks applied on insertion.
  The post-pass reorder is removed. Clothing top picks alone carry `cl: 1`;
  the flag is absent when the clothing knob is off. Rotation and arrivals keep
  clothing/non-clothing slots separate; preparation exchanges match class,
  checkout kind and boost. Incompatible rotations retain validated server order.
  Final Allure repair and its static Python mirror validate clothing, checkout
  and first-eight runs within the existing 24-card membership / 1,024-state
  bounds, using safe ordered deferral on exhaustion. Spotlight and Add-to-Cart
  rules remain protected. The clothing path also supports rotation zero,
  disabled/omitted checkout metadata and absent top-pick configuration.
  Requested complete files (first-screen mix, drop rotation, front page,
  comparison): **518 passed, 1 skipped in 358.84s (0:05:58)**.
  Expanded toggle regressions: **24 passed in 39.16s**. Intermediate related
  compatibility/phone run: **4 failed, 361 passed, 1 skipped in 722.31s
  (0:12:02)**. All four failures were historical checkout-OFF tree pins whose
  fixtures inherited preview publishing and four new mix keys. The historical
  helper now removes all new mix keys; those four fixtures explicitly select
  the original finder OFF configuration. No expected hash was changed.
  All shipped phone checks in that run passed: click-price OFF/ON, comment
  stripping OFF/ON with headroom, and the existing rotation budget fixture.
  Final first-screen/whole-comparison/four original checkout-OFF pins/existing
  gone-and-Allure static/emitted-JS verification:
  **368 passed in 85.50s (0:01:25)**. This includes the latest absent-percentage
  and omitted-checkout-metadata cases and the missing-top-picks browser path.
  No failing targeted checks remain. Native Chromium was skipped by its local
  launcher, so native phone QA remains pending; emitted-JS checks run offline.
  Existing hashes and phone ceilings are unchanged. Clothing-OFF rotation
  script bytes match the supplied version exactly; `git diff --check` passes.
  All pytest runs use `-q -n 1 -p no:cacheprovider` with owned
  `/tmp/taskCfix-*` basetemps removed in `finally` after each run; none remain.
  No network, full suite, hash re-pin, commit or push. Review/deployment remain.
- **Private impulse appeal (Codex task 282, 2026-10-17; uncommitted, ON):**
  Price-bound 1–5 ratings use the configured Haiku model, best 80 eligible
  candidates in batches of 20, a 72-hour private DB cache, durable run claims
  and the shared spend ledger with $0.03/run and $0.40/day feature ceilings.
  Only published title/store/prices/category reach the model; no reasons or
  ratings reach public/member payloads or logs. Pick lifts share existing
  headroom; score-1 offers stay on the wall outside rotating picks, whose later
  windows can lead. The existing adult deny list also excludes those offers
  from rating requests and picks, without changing its classification. Allure
  ships first=3/every=4 across Python, emitted JS and rotation repair; absent
  knobs emit exactly the previous 7/6 JavaScript. Historical fixtures retain
  their existing hashes through explicit pre-282 settings. The historical mix
  helper now also omits four inert metadata keys absent from its old captures;
  no hashes were re-pinned. Legacy AI-take fakes explicitly select rating OFF;
  a new combined-client test verifies both features share one parser/ledger
  and cache without adding calls or rows on replay.
  Initial 11-file scoped regression: **5 failed, 1130 passed, 3 skipped in
  798.91s (0:13:18)**; four failures were the old single-purpose AI-take fake,
  and one was its historical mix metadata. Further frozen checks identified
  the same metadata omission; all are corrected. Final feature/AI-take and
  both real-pipeline rating rerun modes: **247 passed, 1 skipped in 173.95s
  (0:02:53)**. All 16 selected existing publication hashes plus the adult
  first-screen regression: **17 passed in 27.55s**. Core coverage includes
  batch/candidate limits, price/TTL cache invalidation, all spend ceilings,
  malformed/error privacy, unchanged public numeric scores, four OFF byte/member
  modes, rating-1 shaker exclusion, rating-4/5 gadget lifts, emitted JS and
  rotation repair at 3/4 and legacy 7/6, plus existing phone ceilings.
  `git diff --check` and Node syntax pass; absent-knob emitted Allure/rotation
  JS also matches pre-edit HEAD byte for byte. Native Chromium launch returned
  77, so phone/desktop light/dark smoke remains unverified. No implementation
  work remains; review/deployment remain. No network, full suite, commit or push.
  Every owned `/tmp/task282-*` basetemp was removed after its run; none remain.
- **Photo-less public cutoff (Codex task 283, 2026-10-17; uncommitted, ON):**
  `publish.photos.max_photoless_h: 3` hides live cards without an allowed HTTPS
  photo at three hours; omitted is OFF. Publication and alert/digest gates leave
  stored rows and recovery eligibility intact. Existing removed-page/posted
  redirect and IndexNow paths handle disappearance and later photo return.
  Private run summary adds count-only `unpublished_photoless`; site-check floor
  ships 99%. Tests cover 2h59/3h00, source-image grants, gone retention, all
  public files/social selection, manifest/sitemap/IndexNow removal and return,
  unchanged deal rows and same-run output/state, plus both audit-mode reruns.
  Initial feature run: **1 failed, 41 passed in 34.02s** (the canned categorizer
  did not know the extra seed title; marked that seed already categorized).
  Broader targeted run: **2 failed, 317 passed, 1 skipped in 564.15s (0:09:24)**.
  Both failures were pre-existing shipped snapshot drift: the pre-task publisher
  and cutoff-OFF emitted identical files. Historical helpers now omit four
  newer clothing-mix metadata fields; original hashes are unchanged. A standalone
  pre-fix snapshot reproduction was **1 failed in 4.57s**. Final task, all 12
  historical byte cases, shipped phone budget and site-check guard:
  **56 passed in 156.62s (0:02:36)**. All broader phone checks passed too;
  seven older photo-less offers now leave 1,393 of the fixed 1,400 inputs public,
  with every original byte ceiling retained. No failing targeted checks remain.
  Every pytest run used `-q -n 1 -p no:cacheprovider` and an owned
  `/tmp/task283-*` basetemp, removed in `finally` after its run; owned baseline
  comparison scratch was removed too. No network, full suite, hash re-pins,
  commit or push. Review/deployment remain; optional native Chromium QA skipped.
- **Search any product (Codex task 280, 2026-10-17; uncommitted, PREVIEW):**
  Pages `/api/search` and home-only `?preview=search` cards show current qualified
  Buy Box/new prices with their own real reading clocks, both image formats and
  tagged Amazon links. No history/rating/review/sales fields. Normal visitors
  keep the fallback button; OFF restores original files without re-pinning hashes.
  Debounced, abortable requests run once per normalized query; GA4 gets count only.
  Config remains preview / 6-hour cache / 3,000 tokens daily / 20 misses per minute.
  Conservative 110-token reservations persist before dispatch, including errors;
  timeout covers headers/body; cache never extends a reading beyond 24 hours.
  D1 `SEARCH_LIMITS` provides atomic daily/minute debits; KV `SEARCH_BUDGET`
  mirrors/checks counters. Missing either fails closed. Operator reports both
  dashboard bindings live (`wowpriced_search_limits`, `wowpriced_search_budget`).
  Both deploy lanes bundle root `functions/`; pipeline Keepa keys stay in place.
  **Adversarial review fixes:** unqualified Buy Box prices fall back only to fresh
  current-new prices. A shared non-expiring D1 dispatch lease spans the upstream
  call and adds a one-second completion cooldown; delayed KV/D1 storage and UTC
  rollover cannot overlap calls. Lost execution/release fails closed; lease
  recovery is documented in [Amazon search results](amazon-search-results.md).
  Scheduled deploys never inspect/write Pages secrets. Explicit manual workflow
  **install search secret** installs/rotates the repository key silently, with
  missing-key and failed-write coverage. Equivalent case/spacing input preserves
  pending requests and cached results. Preview activation survives browse URL
  replacement. Chromium harness now expects the tab's restored unmatched query,
  rather than waiting for local cards; routing already ignores query strings.
  Each finding has offline regression coverage; function harness executes the
  real SQLite statements (20 Node cases, including delayed storage/held leases
  and oversized-receipt accounting before lease release).
  Feature checks and the 1,400-offer shipped phone budgets:
  **36 passed, 1 skipped in 64.88s (0:01:04)**. Chromium cannot launch locally;
  emitted-JS preview/URL-replacement regressions pass without a browser.
  Existing search behavior, all six selected offline pipeline reruns and three
  recorded OFF modes pass. Compatibility run: **1 failed, 28 passed, 3 skipped in
  137.69s (0:02:17)**. The only failure is the already recorded shipped offscreen
  pin (`145e417e…` expected, `8b6e2890…` observed), matching the earlier pre-edit
  HEAD comparison with zero differing files. No existing hash or fixture changed.
  Final function check after holding the lease through oversized accounting:
  **1 passed in 6.65s** (all 20 Node cases). All skips are native Chromium.
  Node syntax and `git diff --check` pass.
  All pytest runs use `-q -n 1 -p no:cacheprovider` and an owned
  `/tmp/task280fix-*` basetemp, removed in `finally` immediately after each run.
  No network, full suite, hash re-pin, commit or push. Real browser QA and reviewed
  Pages deployment/API billing verification remain; operator approval before ON.
- **int13 integration review fixes (2026-10-17; local, uncommitted, fixes complete):**
  Final adversarial review covers both ordering findings and the 11 named
  compatibility failures. Final repair now uses separate policy-filtered
  24-card windows, preserving membership, boost shares and the three-window
  rotation boundary. Allure feasibility ends at the current window boundary;
  the original 1,024-state budget remains. Failed/impossible repair returns
  a validated ordered subset, enforcing checkout/Allure/gone/unknown spacing.
  It stops at the first shortened window, so later windows cannot advance;
  deferred cards remain in inventory, search and other views. Spotlights never
  enter either path. Regressions reproduce the 24-pick/19-Allure sparse case,
  the 120-pick/Allure-at-24-and-119 boundary case, boost shares, repeated drops,
  and fallback boundary safety. The existing 107-arrangement regression now
  checks window membership on successful repair and safe deferral when a
  protected window cannot fit or the bounded search is exhausted, rather
  than requiring unsafe cross-window swaps.
  Fetch/critic captures pin their historical rotation/support switches; all
  existing hashes remain unchanged. Score and Buy Box Node harnesses execute
  the emitted spotlight dependency. Tracking privacy checks deliberately use
  an initialization timestamp ending `23.450Z`: the prior whole-output price
  scan mistook those seconds for a leaked price. Every outbound event still
  requires the exact allowed payload; private strings/URLs stay absent.
  Native restore setup clicks the current pagination button without the
  observer/Playwright scroll race. A new offline regression covers auto/button
  pagination, a queued action after button removal, and reload/Back anchors.
  Previous integration fixes (stripped-marker harnesses, Buy Box promo facts,
  spotlight row filling and saved-arrival startup) remain covered.
  Final rotation/support-timing/offscreen/browse/privacy checks (including
  all shipped phone budgets and four historical OFF-byte modes):
  **126 passed, 3 skipped in 305.34s (0:05:05)**. All three skips are native
  Chromium checks; emitted-JS regressions run offline. Final timestamp-collision
  privacy regression: **3 passed in 3.03s**. Node syntax and scoped
  `git diff --check` pass; the supplied review document retains its pre-existing
  trailing whitespace.
  An intermediate compatibility run exhausted disk (**2 failed, 6 passed,
  4 errors in 68.04s (0:01:08)**); its owned scratch was removed and subsequent
  runs delete each test's scratch as well as the basetemp after the run.
  Corrected compatibility checks: **12 passed in 152.98s (0:02:32)**.
  No network, full suite, hash re-pin, commit or push. All pytest runs use
  `-q -n 1 -p no:cacheprovider` with an owned `/tmp/taskint13fix-*` basetemp,
  removed in `finally` after each run. Native Chromium QA remains pending
  where the local launcher is unavailable; no other review work remains.
- **Utility-title niche demotion (Codex task 267b, 2026-10-17; uncommitted, ON):**
  Ships the requested `publish.front_page.demote_words` list; missing/empty is
  OFF. Configured whole-word phrases match contiguously. The operator's one
  special rule demotes whole-word case/cases plus iphone, galaxy, pixel, samsung,
  motorola, moto g or oneplus anywhere in the title while enabled, including SE.
  It does not use the separate appeal detector or permit gaps in utility phrases.
  One shared classifier extends the existing pick gate, niche cap and niche-last ordering,
  including static cards, Best-today JSON-LD, home preconnects and saved browser
  wall scope. Public/member deals carry `niche: true` only while enabled; emitted JS
  uses it in the existing niche predicates. No new budgets or ordering pass;
  gone, spotlight, allure spacing, price rules and critic remain unchanged.
  Adversarial follow-up fixes both roots in that classifier: no numbered-phone
  requirement and no unordered utility-word fallback. Rendered-order regressions
  cover iPhone SE, separated utility words and a contiguous positive control in
  server picks, static cards and emitted JS (top/fresh), plus 17 phone-word
  boundary cases. The utility regression uses Cover for iPhone to isolate phrase
  matching; the review's Case for iPhone also satisfies the phone-case rule.
  Review reproduction: **2 failed, 1 passed in 16.45s**. First feature run:
  **1 failed, 70 passed, 1 skipped in 64.60s (0:01:04)**; the new test incorrectly
  required the eligible utility cover to rank first, despite existing appeal/mix
  rules placing it fourth. Corrected the assertion to the first screen, preserving
  those rules. Final targeted verification: **383 passed, 1 skipped in 390.54s
  (0:06:30)**. Covers feature/ranking/appeal/popularity, all 12 selected recorded
  OFF publication hashes, four OFF byte/member comparisons, enabled full-state
  rerun stability, both 1,400-offer shipped click-price phone-budget modes and
  both offline pipeline rerun modes. No failing targeted checks remain.
  Historical fixtures explicitly select OFF; all 32 existing SHA-256 pins remain
  unchanged. `git diff --check` and both Node harness syntax checks pass.
  Native Chromium was attempted; launch returns 77, so phone/desktop light/dark
  smoke remains unverified. All review-fix runs use `-q -n 1 -p no:cacheprovider`
  and owned `/tmp/task267bfix-*` basetemps, automatically deleted after each run;
  none remain. No network, full suite, commit or push. Review/deployment remain.
- **int12 round-4 compatibility fixes (2026-10-17; local, uncommitted, fixes complete):**
  Reproduced the review's 144 failures in the 26 affected files on `8740ee5`:
  **144 failed, 1148 passed, 2 skipped in 288.12s (0:04:48)**.
  Two production bugs: the missing-discount ticker rewrite escaped the new
  price switches and changed historical OFF HTML; the want/scarcity version
  hash included disabled watch fragments. Both are fixed at their template
  and version inputs; new price modes retain their missing-discount behavior.
  Other groups were stale expectations/fixtures: recorded prefilter/fetch/
  critic/link captures now explicitly pin their pre-273/274/275/276/278 switches;
  one-token cadence/efficiency fixtures pin click-price OFF; delivery/earning
  fixtures likewise isolate their original gates. Shipped copy checks expect
  List Price mode to omit /p/ history, while the historical reader/toggle checks
  explicitly select history. Public earning-score checks expect dense ordinals
  and still verify backend numeric scores. JS harnesses specialize feature
  blocks and use executable boundaries surviving comment removal. The offline
  click deploy fixture copies config validation's new local dependencies.
  **Money/correctness: all 11 were fixture expectations, not money bugs.**
  Missing Buy Box facts stopped Pinterest's second-batch/replay check,
  Telegram's accepted-send replay and checkpoint/save-failure checks, each
  reopened lost-response case (Pinterest/Facebook/Instagram/Telegram), and
  Instagram's accepted-publish/lost-response check before any delivery.
  Pending critic and both invalid-verdict retry cases judged successfully but
  stayed behind the independent click-price gate. Explicit pre-275 settings
  restore those tests' intended scope; every original replay/checkpoint,
  verdict, arithmetic and evidence assertion remains. Delivery/critic/recheck
  complete-file verification: **438 passed in 100.96s (0:01:40)**.
  Publication/compatibility first pass: **7 failed, 847 passed, 2 skipped in
  252.81s (0:04:12)**. Four failures were the grey-card harness omitting the
  photo/confirmation helpers it explicitly exercises; two were missing browser
  category context in the freshness harness; the historical reader still
  needed its pre-275 click-price pin after selecting history. Corrected complete
  files: **91 passed in 20.55s** (both harness files), **30 passed in 7.67s**
  (product reader). Final combined 26-file verification:
  **1292 passed, 2 skipped in 244.19s (0:04:04)**. All reported failures now
  pass; the two optional native Chromium restore cases skip in this sandbox,
  so native browser QA remains unverified.
  Round-3 regression, hidden-discount JS in both modes, and shipped 1,400-offer
  phone checks: **33 passed in 159.23s (0:02:39)**. Click rule OFF/ON gzip bytes:
  HTML **66,705 / 66,942**, JS **44,751 / 44,863**, CSS **13,040 / 13,040**,
  JSON **98,012 / 97,866**; original ceilings remain **77,360 / 53,767 /
  14,913 / 103,976**. The comment-strip OFF/ON budget test also passes.
  Existing SHA-256 pins, config, savings/evidence code and budgets are unchanged.
  All runs use `-q -n 3 -p no:cacheprovider`, owned `/tmp/taskint12r4-*`
  basetemps removed after each run; owned logs/list files were removed too.
  `git diff --check` and the changed Node harness syntax check pass.
  No full suite, network, commit or push.
- **int12 adversarial fixes (2026-10-17; local, uncommitted, fixes complete):**
  Qualified Buy Box totals correct plain Amazon prices exactly, including
  one-cent changes and readings without optional averages. Corrections retain
  the unchanged evidence verifier and compute savings only in `savings.py`;
  missing comparison facts produce a verified price with no invented savings.
  An unapplied mismatch stays hidden from the homepage, picks and spotlight.
  Promo rechecks retain their private comparison target but omit it from
  public logs in List Price mode, in both single and batched paid paths.
  Public/member `ps` becomes a dense ordinal in List Price mode, preserving
  the previous rounded-score ordering and ties; backend pick/popularity math
  and history-mode numeric scores stay unchanged. The homepage ticker uses
  the same used/open-box filter as the wall; inventory stays in the Used tab
  and search. New real-pipeline/evidence and emitted-JS regressions cover all
  four findings, exact corrections, rejection fallback and idempotency.
  Final feature verification: **201 passed in 291.64s (0:04:51)**.
  The expanded compatibility run finished **1 failed, 366 passed in 532.19s
  (0:08:52)**, including all four recorded OFF publication hashes. Its one
  failure was an existing test-isolation leak: a correction test's no-LLM
  monkeypatch was retained by a lazy critic-tiering import. That test now
  patches/restores both critic consumers; the affected complete price-accuracy
  and popularity files pass together: **132 passed in 121.23s (0:02:01)**.
  The strengthened real-feed-merge logging cases additionally passed:
  **4 passed in 6.70s**; nonempty used/open-box emitted-JS checks:
  **4 passed in 12.46s**. Shipped 1,400-offer phone checks, click rule OFF/ON,
  comment stripping OFF/ON and unchanged ceiling derivation:
  **5 passed in 350.57s (0:05:50)**. Shipping gzip bytes / original ceilings:
  HTML **66,942 / 77,360**, JS **44,863 / 53,767**, CSS **13,040 / 14,913**,
  JSON **97,865 / 103,976**. Existing OFF/SHADOW public/member comparisons
  also pass; no hash re-pins or raised limits. Every run used
  `-q -n 1 -p no:cacheprovider` and an owned `/tmp/taskint12fix-*` basetemp
  removed after the run; owned logs were removed too. All four review findings
  are addressed. Native Chromium QA was not rerun. No network, full suite,
  commit or push.
- **int11 integration review (2026-10-17; uncommitted, fixes complete):**
  **Round 2 (273/274/275/276/278):** List Price keeps its own product clock;
  fresh Buy Box data cannot refresh it. Feed confirmations, ingest and paid
  reads share a monotone reference guard, preserving newer paid facts and
  rejecting missing/future or mismatched readings. Critic reasons remain in
  private state and are omitted from shipping List Price logs. Hub medians
  use the same policy projection as public cards; unavailable comparisons
  produce neither ticker percentages nor false department discount bands.
  Backend popularity, everyday-price tilts and pick math retain hidden facts.
  Final static/emitted-JS Allure spacing also preserves Unknown exclusion
  from the first 120 cards when spacing reduces the known-card supply.
  Confirmed popularity fixtures now include qualified Buy Box facts; the
  historical one-token no-opinion case explicitly selects OFF. The shipped
  1,400-offer budget fixture includes its required critic evidence. Native
  comment-strip parity now uses qualified Amazon offers with the other shipped
  switches ON and tracking IDs cleared for offline transport. Each finding
  has a database, real-pipeline or actual emitted-JS regression, including feed ingestion,
  public/member privacy and same-run idempotency. Initial round-2 reproduction:
  **46 failed, 366 passed, 2 skipped in 150.30s (0:02:30)**; first corrected
  feature check: **434 passed, 2 skipped in 226.85s (0:03:46)**.
  Expanded primary: **1 failed, 470 passed, 2 skipped in 321.45s (0:05:21)**;
  the byte comparison overlapped an edit to the List Price module, whose file
  bytes deliberately enter the shipping page-version hash. With source held
  steady, the four private-byte modes and final review/spacing regressions:
  **35 passed in 70.25s (0:01:10)**. Compatibility: **362 passed, 1 skipped in
  330.81s (0:05:30)**, including all 12 historical OFF hashes, four OFF/SHADOW
  public/member byte comparisons, paid/feed rechecks and six audit-mode pipeline
  reruns. Final source-frozen feature verification: **474 passed, 2 skipped
  in 327.69s (0:05:27)**. All current review findings are addressed; native
  Chromium QA remains unavailable (launch exit 77), including the corrected
  shipped comment-strip smoke. Every run used `-q -n 1 -p no:cacheprovider`
  and its own `/tmp/taskint11fix-*` basetemp, deleted after that run. Owned
  logs/XML were removed. No full suite, network, existing hash re-pins, raised
  limits, commit or push.
  Shipped 1,400-offer gzip bytes / unchanged ceilings: HTML **66,935 / 77,360**,
  JS **44,856 / 53,767**, CSS **13,040 / 14,913**, JSON **98,845 / 103,976**.
  **Earlier integration review:**
  Premium Buy Box scope follows the browser's default arrival order while
  retaining the static/recovery scope, including sparse Unknowns. Timestamp
  ties retain eligibility across drop jitter. Position-301 legacy candidates
  have stable fallback ordering with either planner. Later partners' quoted
  prices resolve attribution before the history filter; stored facts stay intact.
  New emitted-JS/real-pipeline regressions cover browser card 121 versus static
  order, tied arrivals across seeds, both planners, position 301 and sparse
  recovery with same-run idempotency. Initial reported checks reproduced
  **40 failed, 8 passed in 174.71s (0:02:54)**; the two recheck findings reproduced
  **4 failed in 38.68s**, with separate tie/sparse failures before their fixes.
  Historical fixtures select their recorded history/display/used-home switches;
  shipped fixtures supply qualified Buy Box readings, critic facts and a funded
  recovery bucket. The used-offer clock check runs all four features ON and
  checks the Used hub instead of the intentionally empty home wall. JS harnesses
  use executable boundaries that survive stripping. Backend byte pins exclude
  the later private List Price fact alongside their existing later-field list;
  all 19 existing pins in the touched tests and the recorded fixture files
  remain unchanged. Dark QA artifacts stay under test basetemp.
  The requested **32-file union**, started before the final fixture corrections,
  finished **15 failed, 1568 passed, 4 skipped, 2 errors in 1491.19s (0:24:51)**.
  Case-ID audit: 16 now pass; the native case reaches its explicit skip after
  its fixture is fixed. Final complete-file reruns:
  checkout + click price + new integration regressions **186 passed in 324.89s
  (0:05:24)**; dark mode **19 passed, 1 skipped in 148.41s (0:02:28)**;
  price accuracy engine **38 passed in 21.44s**; unconfirmed labels **43 passed
  in 37.49s**; static photos **74 passed, 1 skipped in 100.48s (0:01:40)**;
  unique ASIN rechecks **50 passed in 50.33s**. Corrected used-clock case:
  **1 passed in 4.04s**; the union's unchanged day simulation/tier checks
  passed. Native Chromium QA remains unverified (launch exit 77); its corrected
  dark fixture now reaches the explicit skip instead of failing setup.
  Shipped 1,400-offer gzip bytes / unchanged ceilings: HTML **66,543 / 77,360**,
  JS **44,499 / 53,767**, CSS **13,018 / 14,913**, JSON **98,659 / 103,976**.
  Every run used `-q -n 1 -p no:cacheprovider` and an owned
  `/tmp/taskint11fix-*` basetemp, deleted after that run. Owned scratch/logs were
  removed; no existing hash re-pins, raised limits, network, full suite, commit
  or push. No open int11 findings remain; native browser QA remains external.
- **Known click prices (Codex task 275, 2026-10-17; uncommitted, ON):**
  Validated `keepa.click_price_rule: "on"`; code/missing-key default OFF.
  Plain new Amazon prices require a qualified Buy Box with its own fresh
  clock and the savings engine's 10% tolerance. Matching Amazon/NEW slots
  alone stay Unknown: labeled in search/categories, excluded from picks,
  spotlight, pushes/social and the first 120 home cards (including All deals
  and sparse inventory). Known gaps hide; stale gaps count Unknown but stay
  hidden until a valid later reading clears them. Fashion variation children
  with confirmed prices show `select sizes`; promo/used paths stay unchanged.
  Candidate/spotlight/social and first-300 wall reads reserve the +2-token
  premium, shorten batches before dropping it, preserve reserves/grants and
  expose extra demand/reservations in the plan. Shadow only measures, preserving
  requests and published/member bytes. Private `price_source.click_price`
  counts the frozen published pick/wall scopes before corrections, labels extra
  tokens as reserved and includes at most five ASIN/price examples; no seller IDs.
  Existing recorded fixtures explicitly select OFF; no hashes were re-pinned.
  Broader targeted run: **1 failed, 479 passed in 354.01s (0:05:54)**. The failure
  exposed a test seed retaining its $25 database column; the adidas fixture now
  ingests verified $4.55/$32.93 quotes through the real savings/upsert path.
  Final feature, pipeline/transport pins and four-mode public-byte checks:
  **74 passed in 66.27s (0:01:06)**, including both enabled audit-mode reruns,
  actual emitted-JS ordering/chips, scarce grants and failed-request allowances.
  Every run used `-q -n 1 -p no:cacheprovider`; all owned `/tmp/task275-*`
  basetemps were deleted after their runs. Review/live verification remain;
  no network, full suite, commit or push.
  Adversarial follow-up: legacy Buy Box gaps are carried into the click-price
  check before either fact check is overwritten. Missing, clockless,
  unqualified and older readings retain fresh/stale gaps; valid later readings
  still recover. Actual published picks and the first 300 home cards now
  precede hidden recovery candidates. New real-pipeline regressions reproduced
  both findings: **14 failed, 2 passed in 20.48s** before fixes; feature checks
  after fixes: **72 passed in 81.01s (0:01:21)**. Added shipped ON/OFF phone
  byte-budget checks with the existing ceilings; no existing hashes changed.
  Expanded compatibility run: **1 failed, 166 passed in 279.95s (0:04:39)**;
  all 165 existing Buy Box/price correction/unique-ASIN tests passed, including
  pipeline/transport and four-mode public/member byte pins. The new phone ON
  check expected 1,400 offers but its historical fixture lacked critic facts,
  so 133 Unknown rows correctly failed the normal publish gate. That fixture
  now supplies critic facts and still requires all 1,400 offers in both modes.
  Corrected phone checks: **2 passed in 106.80s (0:01:46)**; all shipped gzip
  ceilings pass with dark mode ON and click prices OFF/ON. All follow-up runs
  used `-q -n 1 -p no:cacheprovider` and owned `/tmp/task275fix-*` basetemps,
  deleted after each run. Both review findings are fixed; review/live
  verification remain. No network, full suite, commit or push.
  Resumed the interrupted second review after the live-config rebase. Actual
  published picks/home cards now lead hidden Buy Box recovery across new,
  used and promo transport modes, with both planner and unique-ASIN switches.
  Premium reads enter planner selection and, after acquisition, honor funded
  paid-request cadence instead of overriding stretch with the hourly clock.
  Compatibility checks exposed incomplete batches at the per-call minimum:
  compatible following rows now fill a call without letting hidden work pass
  another published transport mode. Due cards retain regular funding when
  premium reads consume their surplus; selection keeps used-share limits,
  per-ASIN charging, reserves and same-run allowances. Added 12 surplus
  regressions alongside the 11 interrupted-review cases. The legacy volatility
  fixture explicitly selects OFF; historical byte hashes and live history-off
  settings stay unchanged. Overflow regression before its fix: **2 failed in
  5.64s**. Initial compatibility run: **15 failed, 285 passed in 460.08s
  (0:07:40)** (five batch-fill failures plus ten legacy volatility expectations
  lacking the OFF pin). Corrected feature checks excluding phone renders:
  **95 passed in 83.33s (0:01:23)**. Continued validation: feature, Buy Box,
  volatility tiers and batching **182 passed in 278.14s (0:04:38)**. A planner
  boundary regression exposed premium demand beyond the first 300 saved wall
  cards; saved and fallback scopes now match the pipeline. Before/after:
  **2 failed in 3.80s** / **2 passed in 3.46s**. Existing OFF byte pins across
  publication modes plus shipped OFF/ON phone ceilings and the boundary cases:
  **58 passed in 150.66s (0:02:30)**; no hashes changed. The historical
  value-per-token fixture now explicitly selects OFF for its cached one-token
  reads. Broader Buy Box/price-correction/unique-ASIN/value/wall compatibility:
  **15 failed, 164 passed in 351.80s (0:05:51)**; all 15 failures were that
  historical value-per-token fixture inheriting ON. Its corrected full-file
  rerun, including the daily yield simulation: **29 passed in 144.46s
  (0:02:24)**. No targeted failures remain. Every run used `-q -n 1
  -p no:cacheprovider` and a private `/tmp/task275fix-*` basetemp removed
  immediately afterward; the interrupted run's 384 MB basetemp was also removed.
  Both review findings have real-pipeline regressions; review/live verification
  remain. No network, full suite, hash re-pins, commit or push.
  Latest review: otherwise-publishable Unknown Amazon deals now reach all three
  capped identity/link recovery paths (redirects, ASIN short links, source posts)
  when the click-price rule is ON. OFF/SHADOW selection and publication gates
  stay unchanged. Nine real-pipeline regressions cover the three paths in all
  modes, hourly/fast selection, confirmation only after recovery, and same-key
  reruns. Initial fixture setup: **3 failed in 4.78s**, corrected without changing
  shared helpers; the actual blockage then reproduced: **3 failed in 9.96s**.
  Focused recovery/legacy pipeline checks after the fix: **10 passed in 39.95s**.
  Final click-price/recovery compatibility, 12 recorded OFF publication-byte
  pins and shipped OFF/ON phone budgets: **254 passed in 214.75s (0:03:34)**.
  Additional ASIN caps, polite HEAD-only short links, deadline deferral and
  retry checks: **9 passed in 2.84s**. All runs used `-q -n 1
  -p no:cacheprovider`; owned `/tmp/task275fix-*` basetemps were deleted after
  each run. No open findings remain in `275-review-findings.md`; review/live
  verification remain. No network, full suite, hash re-pins, commit or push.
- **Static photos + Used tab (Codex task 274, 2026-10-17; uncommitted, ON):**
  Validated `site.static_photos` and `publish.front_page.used_tab_only` ship true;
  omitted/false retains recorded bytes. Static grids use HTTPS product photos,
  four eager per page (hero included), then lazy/async, fixed dimensions and
  store-tile fallback. Deal pages have a contained 320px hero, up to four related
  cards, wider columns and two-line titles/descriptions; dark tiles reuse 254.
  Used/open-box offers stay in their tab/search; static home agrees. Category
  pagination puts new offers before used/open-box/refurbished, in stable groups.
  **All four adversarial findings fixed at their shared paths:** the public
  serializer clears history-derived regular prices, basis, discount and savings
  before generating headlines/JSON/cards; stored facts stay unchanged. Explicit
  list prices and current new-offer comparisons (including "lowest new price
  (current offer)") remain. Retained ended pages now serialize with `cfg`, so
  they retain the history policy and verified badge after leaving the wall.
  Search merges matching spotlights without duplicates, orders new live matches
  before used, then applies ended-card spacing (outside the first eight).
  **Live rebase baseline 75ad39f retained:** all stores' price history hidden,
  `seo.product_pages` off, Buy Box rule on, Amazon search on, 35-second popup.
  Historical fixtures retain their original switches/policies via
  `tests/history_pins.py` and explicit "recorded before" settings. Redundant
  shipped-policy assertions from the union merge were removed; no hash re-pinned.
  `test_static_photos.py` and the actual published-JS Node harness cover every
  finding: public/member JSON and all static/browser card surfaces, legacy and
  explicit average bases, real current-price bases, retained ended Amazon pages,
  nonempty used spotlight inventory and mixed live/ended search across 3 sorts.
  Review reproduction: **8 failed, 12 passed in 13.16s**. Initial feature check:
  **1 failed, 68 passed, 1 skipped in 97.87s (0:01:37)**; its static-card matcher
  assumed a JS-only data attribute, corrected to match the rendered deal link.
  Policy/publisher regression: **279 passed in 145.49s (0:02:25)**.
  Final targeted compatibility/feature validation: **398 passed, 1 skipped in
  418.23s (0:06:58)**, including original OFF hashes, all search/spotlight/event
  cases, dark contrast, phone ceilings and both pipeline idempotency modes.
  Shipped gzip bytes / unchanged ceilings: HTML **77,022 / 77,360**, JS
  **53,219 / 53,767**, CSS **14,846 / 14,913**, deals.json **99,044 / 103,976**.
  Four eager home photos; remaining static photos lazy. Chromium returns 77 in
  the sandbox; native photo/CLS/console QA remains for the operator via
  `test_static_photos.py::test_chromium_photos_and_cls_offline` (390x844 and
  1280x800, light/dark). The test builds its own disposable fixture site.
  Every run uses targeted `-n 1 -p no:cacheprovider` and its own
  `/tmp/task274fix-*` basetemp, removed after each run; owned logs/runner removed.
  `git diff --check` and both Node syntax checks pass. No network, full suite,
  commit or push. Review/deployment and native browser QA remain.
- **Amazon display references (Codex task 276, 2026-10-17; uncommitted, ON):**
  Ships validated `publish.amazon_reference: list_price`; `history` preserves
  the previous output. This supersedes earlier public-history permissions for
  this mode: all stores' charts, past-price wording, saved-price watches,
  product-history pages and checker history stay hidden; stored checks remain
  private. New Amazon API-reference offers display only the same-confirmation,
  fresh Amazon List Price, with savings computed in `savings.py` under the
  `amazon_list_price` basis. Missing/non-discounted/stale lists show Price drop
  with no reference, percentage or dollar savings. Source-stated pairs keep
  their facts and attribution. Existing licensed feed/paid records supply the
  list, through the unchanged evidence verifier, without new requests or LLMs.
  **Qualification, `site_show_from_pct`, `min_pct`, pick scores and selection
  still use the original hidden comparison**, including offers without a list;
  browser tier membership uses only a boolean, never the hidden percentage.
  Run summaries count public List price/Price drop offers. Cards, ticker
  (including its accessible text), social/email subjects and bodies share the
  display policy; stock-evidence gates retain private math. A later partner's
  quoted pair keeps its attribution even after an original Keepa discovery;
  used/current-new comparisons keep their facts without past-price wording.
  Existing recorded fixtures explicitly select `history` ("recorded before
  task 276"); no hashes re-pinned. Initial feature checks: `5 failed, 77 passed
  in 7.97s`, then `4 failed, 78 passed in 9.91s`. Affected regression:
  `3 failed, 511 passed in 330.13s (0:05:30)`; expanded regression:
  `3 failed, 614 passed, 1 skipped in 535.12s (0:08:55)`. Isolated renderer:
  `1 failed in 5.18s`. All failures corrected: historical AI/checker fixtures
  now select history, the file count retains both split sitemap children but
  omits /p/, and the renderer assertion excludes CSS color percentages.
  **Initial targeted: `132 passed in 172.99s (0:02:52)`**, covering savings,
  emitted browser cards/ticker/tier split, JSON/structured data/alt text,
  source provenance, same-reading freshness, hidden site floors, remaining
  historical byte trees and all six existing audit-mode rerun cases.
  Every run used `-n 1 -p no:cacheprovider`; owned basetemps were deleted in
  `finally` after each run, and `/tmp/task276-*` is empty. `git diff --check`
  passes. One optional Chromium check skipped; native browser QA and review
  remain. No network, full suite, commit or push.
  **Review fixes (2026-10-17):** Keepa cross-check logs omit private averages
  in `list_price` mode, including fallback averages and rejected offers;
  history-mode logs and backend verdicts stay unchanged. Category introductions
  and descriptions omit qualification-floor claims in this mode, including
  used/open-box hubs; legacy copy still uses the configured floors. The failing
  watch regression now explicitly selects `history`; a separate shipping-mode
  Node check exercises the emitted label, every sort, ticker and Surprise logic,
  and verifies Amazon/non-Amazon watch matching and controls stay suppressed
  even with the older history gates open. Reproduction before fixes:
  `9 failed, 7 passed in 10.26s`. **Final review checks: `205 passed in 185.99s
  (0:03:05)`; `302 passed in 256.49s (0:04:16)`.** These cover each finding,
  savings/rechecks, SEO, historical OFF byte trees, AI takes, Saved/public copy,
  both the actual shipping and recorded 1,400-offer phone budgets, and all six
  pipeline rerun cases. Existing SHA-256 pins are unchanged across every modified
  tracked test; rebased fixtures and shipping expectations pass. All review runs
  used `-n 1 -p no:cacheprovider` with owned `/tmp/task276fix-*` basetemps,
  removed in `finally` after each run. No review findings remain; native Chromium
  QA/review remain. No network, full suite, hash re-pins, commit or push.
- **Home comment stripping (Codex task 278, 2026-10-17; uncommitted, ON):**
  `site.strip_comments: true`, missing/false OFF; final home-only lexical pass
  after all injections, before sitemap/audit/IndexNow sealing. Audited structural
  wrappers and conditional comments stay; CSS strings/URLs and JS strings,
  regexes/nested templates stay intact. Only standalone JS comments disappear;
  blank-line collapse preserves literals and all five JS line-ending forms.
  The switch and implementation feed `site_version`; OFF retains original bytes.
  Recorded fixtures explicitly pin OFF (recorded before task 278); no repins
  or budget increases. New checks compare every file across four access modes,
  parse executable scripts, balance CSS and verify file/full-DB rerun stability.
  Final targeted `-q -x -n 1 -p no:cacheprovider`: **103 passed, 1 skipped in
  323.43s (0:05:23)**, including 72 compatibility/offline pipeline-rerun checks.
  Earlier runs: **5 failed, 21 passed, 1 skipped** (Node argv limit; use stdin),
  **21 passed** (lexical checks), **1 failed, 97 passed, 1 skipped** (closed
  fixture advanced the deliberate free-eight rotation), **5 failed, 98 passed,
  1 skipped** (new newline test expected uncollapsed blanks). All corrected;
  CRLF matching was also tightened so a pair cannot split on a repeat pass.
  Actual shipped config, existing 1,400-offer fixture, gzip bytes OFF → ON:
  HTML **76,458 → 65,670**, JS **53,091 → 44,163**, CSS **14,846 → 13,018**;
  HTML ceiling **77,360**, headroom **11,690**. JSON **99,031 → 99,032** changes
  only through the version. The brief's `tests/test_drop_rotation.py` is absent
  here; the new `test_home_comment_strip.py` runs the shipped-budget comparison.
  Real Chromium was attempted at `/opt/pw-browsers/chromium` but cannot launch
  (exit 77); phone/desktop, light/dark ON/OFF browser assertions remain unverified.
  All owned `/tmp/task278-*` basetemps were deleted after each run. Review and
  native browser QA remain; no network, whole suite, commit or push.
- **What people buy (Codex task 273, 2026-10-17; uncommitted, ON):**
  Validated `publish.front_page.appeal.want`/`season` and
  `site.open_box_scarcity` ship ON; missing sections remain OFF. Literal,
  plural-tolerant title groups and New York date windows share a soft ceiling;
  stronger private sales evidence keeps its lift without stacking. Used,
  unconfirmed, model-specific and configured adult titles receive no new lift.
  The requested literal `toy` exclusion also blocks ordinary toy/toys titles;
  the deny list remains configurable (no contextual exception was invented).
  Allure starts after six cards and stays six positions apart in both Trending
  and All deals; allure spotlights are hidden in both unfiltered tabs. Sparse
  overflow remains in search/category views. Review fixes keep allure filtering
  at homepage boundaries, so seasonal/best/category pages retain every offer;
  Python/static and actual JS check the final visible sequence, including gone
  allure cards: none in the first six and at least six positions apart; gone
  cards remain outside the first eight and at least eight positions apart.
  Conflicting cards stay off the unfiltered homepage. Popularity quota accounting
  uses the caller's actual popularity settings and the capped marginal lift.
  Open-box cards/details always use “often only 1” for now (operator decision
  2026-10-17 ~08:45Z). Validated `site.open_box_counts: false` ships/defaults OFF;
  serialization omits counts and generated JS ignores even cached count fields.
  The retained opt-in exact “only N left” path needs a short,
  verified source quote naming this offer, bound to its item/link/price/source
  and original observation/publication clocks within two hours. Invented titles,
  ambiguous roundups, changed offers and rechecks cannot supply/renew counts.
  Current-stock parsing rejects historical, negated, conditional, quoted or
  contradictory text inside the quote, including previously stored evidence.
  Qualifiers outside a selected quote can still be missed; complete-context
  validation remains required before enabling counts. Explicit hoodie/hoodies
  and knife/knives normalization preserves the configured whole-word lift.
  No new fetch/Keepa/LLM calls. Private `want_season`
  counts also persist as the single latest `want_season_lifts` meta entry,
  and join the private homepage report through its existing encrypted ring;
  excluded from public logs/CLI/site bytes. Tasks 260/261 are absent in this
  tree; no separate staples/learner implementation was added. Recorded fixtures
  pin all three switches OFF (before task 273); no hashes/phone limits changed.
  Initial task run: `11 failed, 219 passed in 125.68s (0:02:05)`; test setup
  corrections applied. First regression:
  `1 failed, 790 passed in 380.06s (0:06:20)`; the sales-only privacy fixture
  now avoids a newly matched want word. Main targeted regression:
  `795 passed, 1 skipped in 437.18s (0:07:17)`, including all recorded pins,
  offline reruns, public copy, contrast and unchanged phone byte ceilings.
  The skip is the native 390px light/dark check: Chromium cannot launch.
  Final spotlight search/order/bytes/phone-budget follow-up:
  `7 passed in 70.07s (0:01:10)`. All earlier failures are fixed.
  All runs used `-n 1 -p no:cacheprovider`; every owned `/tmp/task273-*`
  basetemp was deleted after its run. Review fixes resumed after the rebase onto
  `75ad39f`, retaining the live history-off/Buy Box/everyday-price/search/35 s
  settings. Every finding has a regression, including explicit Chromium path
  `/opt/pw-browsers/chromium` and exit 77 only for an actual launch failure
  (import/assertion failures stay red). Resumed review verification:
  `257 passed, 1 skipped in 104.96s (0:01:44)` (findings, all switches ON,
  the unchanged 1,400-offer phone ceilings); historical OFF fixtures:
  `64 passed in 114.74s (0:01:54)` (all original hashes retained).
  Additional targeted ranking/rendering/seasonal/budget/privacy/history-off/
  idempotency checks: `795 passed, 1 skipped in 357.04s (0:05:57)`.
  Native 390px light/dark visual QA remains unverified: the explicit Chromium
  executable exits at launch with crashpad `setsockopt: Operation not permitted`.
  All resumed runs used `-n 1 -p no:cacheprovider`; owned `/tmp/task273fix-*`
  basetemps and run logs were removed. No regression remains failing.
  Earlier adversarial follow-up (`273-review-findings.md`): all three findings
  fixed with score-level plural tests, both unfiltered tabs/sorts/spotlights,
  and historical/ambiguous stock regressions for new ingestion and old rows.
  Regression-first run: `20 failed, 11 passed in 6.57s` (before fixes).
  Final targeted ranking/ingestion/rendering/privacy/ON-phone-budget checks:
  `833 passed, 1 skipped in 363.02s (0:06:03)`. Unchanged historical OFF hashes,
  phone ceilings/boundaries and offline rerun idempotency:
  `80 passed in 126.82s (0:02:06)`. The skip remains native 390px light/dark
  layout: Chromium launch fails on sandbox `setsockopt`; actual JS checks pass.
  These three runs used `-n 1 -p no:cacheprovider` and isolated
  `/tmp/task273fix-*` basetemps, deleted after each run. No hash was re-pinned;
  no regression remains failing. Native layout QA/review remain.
  Current two-finding review: the operator's default-OFF count switch covers
  fresh/legacy evidence and cached browser counts, including qualifiers outside
  the selected quote; the final visible-card pass checks both spacing limits
  after all ordering. Regressions cover both tabs, three sorts, pick settings,
  sparse/ample gone allure supply and opt-in count freshness/validation.
  Regression-first: `9 failed, 8 passed in 7.91s` (before fixes).
  Targeted task/rendering/ranking/1,400-offer phone checks:
  `520 passed, 1 skipped in 136.51s (0:02:16)`. The skip remains the native
  390px light/dark check: Chromium launch fails on sandbox `setsockopt`.
  Unchanged historical OFF hashes, phone ceiling/boundary checks and offline
  rerun idempotency: `75 passed in 106.77s (0:01:46)`. All three current runs
  used `-n 1 -p no:cacheprovider` with isolated `/tmp/task273fix-*` basetemps,
  deleted after each run; no existing hash or phone limit changed. No regression
  remains failing. Native layout QA and full-context validation before any
  future count activation remain; counts stay OFF as the operator requested.
  No full suite, network, commit or push.
- **Visitor's-eye health watch (Codex task 270, 2026-10-17; uncommitted):**
  Read-only `visitor-watch.yml` stays on `*/10`, serialized, six-minute timeout.
  It retrieves only the immediately preceding completed run (failures included),
  saves the snapshot on repeated flags, and requires the same check/view to flag
  twice before exit 1. Redirect validation/pacing, pending rotation deadlines,
  actual intercepted search navigation and decoded dark screenshots retain their
  earlier regressions. Task 267 utility groups and task 269 rotation remain
  report-only until their missing config arrives. Amazon search fixtures now use
  the rebased shipped feature; the explicitly disabled case still flags.
  The health addendum now checks loaded photos through three screens on both
  viewports, separately counts store tiles/broken photos, and samples the beauty
  hub plus the first card's internal deal page. Screenshot/DOM checks cover
  overflow, overlapping/stretched cards and 400px uniform bands (including bands
  crossing screenshots), plus the unchanged dark glare measure. Working checks
  exercise the second actual department (Beauty, using the published taxonomy),
  a search word from live published inventory, deal-page HTTP/price/Get deal,
  and every first-24 outbound URL.
  Visible malformed values/history wording and gated used/open_box on the actual
  All tab flag separately. Only text findings are retained, never page bodies.
  First-screen internal HEADs are deduplicated across all four views, capped at
  20, paced, and never follow redirects; Get deal/sponsored links are excluded.
  Operator's 2026-10-17 13:46Z allowlist decision replaces the watcher host
  blocklists. Browser GETs allow only our origin and images from exact
  `quality.visitor_watch.image_hosts` entries, checked before fetching every
  redirect hop. External non-images are refused even on configured image hosts;
  missing/malformed host lists fail closed. Publisher/serializer photo fixtures
  cover the configured CDN and licensed source-image hosts. Get deal checks use
  HTTPS URL shape, reject our own domain, and require our tag on direct Amazon
  destinations, without retailer/source/tracker host lists or credit matching.
  Task 274 is absent here: its future category/deal photo and used-on-All gates
  are explicit watcher settings
  `quality.visitor_watch.{category_photos,deal_photos,exclude_used_on_all}`, all
  false until those features ship. Related images cannot mask a missing main
  deal photo. Every new check uses the existing two-consecutive-run rule.
  Adversarial review fixes: snapshots record `.gone` status and exempt only
  intentionally linkless ended cards; live/malformed links still flag. Inventory
  search requires the applied-query acknowledgement and matching titles, with
  ignored-input and unrelated-result regressions. Context-wide page listeners
  capture sampled-page JavaScript/console/request/HTTP failures. The delayed-image
  browser regression takes its baseline before starting the held image response.
  Shared screenshot capture disables animations/caret, caps waits at 3 seconds
  or the remaining deadline, and refuses expired captures; decode waits stay
  bounded. Normal "the usual $50.00", struck-through "List Price $X" and "Price
  drop" are clean wording; actual history claims still flag. Clean-build fixtures
  retain regular prices. Text probes inspect rendered card/main text and exclude
  hidden/transparent descendants and script/style source. The sampled deal needs
  its own visible main Get deal button with a valid destination matching its home
  card; six related-card buttons cannot mask a missing or incorrect main button.
  Regressions cover the real serializer's approved routes, missing/wrong main
  buttons, normal/history wording, hidden/script wording, ended cards, ignored
  input and a sampled-page exception. Network regressions prove ShareASale
  `/r.cfm`, bit.ly, walmrt.us, amzn.to and tagged Amazon product URLs are aborted
  unsent on initial requests and redirects (including a configured CDN hop),
  across resource types and offline fixtures. Additional cases cover unknown
  photo hosts, spoofed/subdomain/trailing-dot hosts, credentials, ports and pacing.
  Real serializer regressions cover credited Hip2Save/KCL/Money Saving Mom/
  Passionate Penny Pincher posts, protected trackers, and direct MorningSave,
  Meh, Woot and Target links with Sovrn disabled/enabled plus real Sovrn wrappers.
  Duration regressions retain supply/warranty headlines. Durations now
  require history/statistical context; 90-day supplies, warranties and price
  guarantees stay clean while actual history claims still flag.
  Latest review: search no longer assumes socks exist. It selects a word from
  live, unlocked published items, preserves model tokens, and still requires an
  applied-query acknowledgement plus matching result titles. Regressions cover
  healthy no-socks inventory on consecutive runs, missing/unrelated results,
  ignored input, accents, model-only items and query cleanup. Shared text
  collection retains invalid values and history findings from both sampled
  pages, labels their origin and preserves earlier findings. Regressions cover
  each page independently, combined findings and repeated-run failures; offline
  browser scenarios include each page, both pages and healthy no-socks runs.
  The brief now matches the operator's standing `*/10` schedule; the workflow
  cadence and assertion remain unchanged, with a new brief/workflow regression.
  Final department review: the probe selects the department with the most live,
  unlocked eligible published cards, skipping empty departments and price-only
  spotlight rows. It verifies the active tab and complete matching key set,
  including departments larger than the first page; correct unchanged cards
  pass. Missing, unrelated, duplicate or inactive results and stalled loading
  still flag. Two consecutive-run regressions cover empty departments skipped
  and identical cards passing, with corresponding offline browser scenarios.
  No production storefront/template bytes changed.
  Summaries start `health: ok` / `health: N flags (names)`; the always-saved
  `visitor-health` artifact contains compact `health.json` for failure watch and
  recaps. Latest pre-fix regressions: **5 failed, 4 passed in 10.71s**, reproducing
  the egress and MorningSave/Meh findings. Inventory fixture refinements:
  **1 failed, 27 passed, 7 skipped in 21.88s**, then
  **1 failed, 49 passed, 7 skipped in 108.60s (0:01:48)**; both failures were
  assumptions that the serializer displays source images without current grants.
  Those images were removed from the fixture and network allowlist; publisher
  grants were unchanged. Latest review's first watcher run:
  **29 passed, 12 skipped in 32.68s**. Prior targeted watcher, unchanged four-mode
  OFF byte pins, 1,400-offer phone budgets, budget boundaries/OFF parsing and
  workflow checks: **51 passed, 12 skipped in 244.08s (0:04:04)**, including all
  **49 Node regressions** through the wrapper. The final department regressions
  reproduced before the fix: **1 failed in 6.81s** (49 Node checks passed, both
  new regressions failed); the fixed wrapper then reported **1 passed in 5.91s**.
  Final targeted watcher, four unchanged OFF-byte pins, 1,400-offer phone budgets,
  budget boundaries/OFF parsing and workflow checks:
  **50 passed, 14 skipped in 237.93s (0:03:57)**, including **52 Node regressions**
  through the wrapper. Chromium launches still return 77, so all fourteen browser
  checks (including both consecutive-run department scenarios) remain unverified.
  Syntax and `git diff --check` pass.
  All pytest runs use `-n 1 -p no:cacheprovider` and owned
  `/tmp/task270fix-*` scratch, deleted after each run. No hashes changed;
  no full suite, network, commit or push. Review, browser QA and deployment remain.
- **Per-drop top windows (Codex task 269, 2026-10-17; uncommitted, ON):**
  `site.drop_rotation_windows: 3` cycles the strongest complete 24-card windows
  on the checkout-price path; 0 preserves the old template/payload bytes.
  `publish/drop_rotation.py` and `templates/drop-rotation.js` retain ordinary
  window membership (including boost/price/store/brand shares), promote arrivals
  only inside the first displayed window, and repair sliding checkout spacing on
  the ordinary-pick list. **Operator decision (2026-10-17 12:50Z): rotation never
  places, moves, inlines or reorders a spotlight.** Active spotlight keys are
  excluded before rotation; the original banner rendering, pagination exemption,
  filtering and deduplication remain exact. The server excludes active spotlight
  keys before ordinary pick selection, so removing a duplicate banner cannot
  compress checkout spacing or shift window shares, including the fallback order.
  Server category preparation exchanges ordinary picks only; checkout/boost
  matches preserve window shares, with store/price matches preferred. Server-derived
  `pick_checkout` on picked deals also covers offers without paid labels; the
  separate spotlight payload is unchanged and no private boost facts are published.
  Guarded tab-local opaque arrival keys retain 20 drop/list
  signatures across Saved startup, refresh and Back. The repair jointly searches
  category/promo choices in the first eight, with greedy promo feasibility checks
  and a shared 1,024-state limit; window membership and server-order fallback
  remain intact. Browse, Saved, filters and other sorts retain their behavior.
  If no legal repair exists or the search limit is reached, retain server order.
  Recorded fixtures pin 0
  ("recorded before task 269"); no hashes changed. Initial focused run:
  **5 failed, 22 passed, 1 skipped in 86.98s (0:01:26)** (fixture assumptions fixed).
  Expanded run: **1 failed, 233 passed, 3 skipped in 199.55s (0:03:19)**;
  its randomized join case exposed the suffix repair and now passes directly.
  Final targeted regressions: **492 passed, 1 skipped in 330.99s (0:05:30)**,
  including publication, all selected byte pins and both audit rerun modes.
  Final feature checks (including checkout-enabled search/category/sort parity):
  **37 passed, 1 skipped in 70.40s (0:01:10)**. Those runs used
  `-q -rs -n 1 -p no:cacheprovider`. Chromium launch returns 77 under sandbox
  permissions; native phone drop/refresh/layout checks remain unverified.
  Review fixes reproduce all three findings: **3 failed in 64.23s (0:01:04)**.
  Intermediate checks: **1 failed, 68 passed, 1 skipped in 174.33s (0:02:54)**
  (wide-spacing search timeout fixed by equivalent-state memoization), then
  **1 failed, 2 passed in 58.42s** (an added fixture requested an impossible
  rotation; corrected, with explicit no-solution fallback assertions retained).
  Separate rotation-enabled 1,400-offer budget checks use shipped config,
  including dark polish, and enforce every existing ceiling: JS **53,537** /
  **53,767**, HTML **76,821** / **77,360**, CSS **14,823** / **14,913**,
  deals JSON **98,897** / **103,976** gzip bytes. Frozen hashes are unchanged.
  Final review-fix targeted verification: **314 passed, 1 skipped in 328.77s
  (0:05:28)**, covering every rotation regression, front-page/checkout behavior,
  browse/Saved, all selected frozen byte pins and both audit rerun modes.
  Review-fix runs used `-n 1 -p no:cacheprovider`; owned `/tmp/task269fix-*`
  basetemps were removed after every run. `git diff --check` passed; no existing
  hash lines or fixture files changed. No network, full suite, commit or push;
  native Chromium QA (launch unavailable, exit 77) and review remain.
  Follow-up performance review: a category-independent greedy feasibility check
  rejects promo prefixes that cannot meet every remaining window's quota before
  category enumeration. Earliest legal promos leave maximum room for later
  windows, so successful ordering choices and no-solution fallback are preserved.
  A server-accepted ten-category fixture with promo counts **5/5/5/4/5** reproduces
  **59,289** recursive calls at seed 2; all three seeds, with and without arrivals,
  now take at most **149**, guarded by a **500-state** regression ceiling.
  Reproduction: **1 failed in 4.91s**, then the stronger fixture **1 failed in
  5.18s**. Intermediate: **1 failed, 2 passed in 64.04s (0:01:04)** (feasibility
  pruning extended beyond the first eight); focused ordering: **15 passed in
  17.46s**. Final compatibility/byte/budget verification: **310 passed, 1 skipped
  in 356.50s (0:05:56)**, including every rotation regression, front-page/checkout,
  Saved/browse, selected frozen byte pins and both audit-mode idempotency reruns.
  Shipped 1,400-offer gzip bytes / unchanged ceilings: JS **53,741 / 53,767**,
  HTML **77,027 / 77,360**, CSS **14,823 / 14,913**, deals JSON **98,896 / 103,976**.
  No existing hash or fixture changes. All follow-up runs used `-n 1
  -p no:cacheprovider`; owned `/tmp/task269fix-*` basetemps were deleted after
  each run. No network, full suite, commit or push; native Chromium QA remains
  unverified (unavailable/skip), as does review.
  Post-rebase findings (75ad39f): reproduced the shipped budget failure at
  **77,724 / 77,360** home HTML gzip bytes: **1 failed in 45.91s**. The new
  hard-limit regression also failed before the fix: **1 failed in 3.36s**.
  A shared **1,024-state** limit now bounds all category/promo branches and
  deterministically falls back to the untouched server order on exhaustion.
  The regression disables feasibility pruning to force exhaustive category
  choices, checks the hard stop/fallback, then repeats to verify reset and
  stable order; arrivals and the normal pruning regression remain covered.
  The enabled template replaces the old rotator and removes its obsolete
  comments/unreachable return. Task 269's JS has no shipped comments; one stable
  candidate loop and recent promo positions replace duplicated traversal/bit
  logic and failed-state bookkeeping. The OFF transform still returns its input
  exactly. Intermediate: **2 failed, 40 passed, 1 skipped in 89.84s (0:01:29)**
  (further JS trimming needed; hard-limit fixture allowed a valid early solution
  with arrivals, so its fallback assertion now applies only on exhaustion).
  Final focused checks: **42 passed, 1 skipped in 92.74s (0:01:32)**.
  Shipped 1,400-offer gzip bytes / unchanged ceilings: HTML **77,086 / 77,360**,
  JS **53,714 / 53,767**, CSS **14,846 / 14,913**, deals JSON **99,050 / 103,976**.
  Final compatibility checks: **438 passed in 300.06s (0:05:00)**, covering
  front-page/checkout/publication behavior, Browse/Saved, selected frozen byte
  pins, template slots, unchanged phone ceilings and both audit-mode reruns.
  All post-rebase runs used `-n 1 -p no:cacheprovider`; owned
  `/tmp/task269fix-*` basetemps were removed after every run and sizing scratch
  was deleted. Existing hashes and fixture files are unchanged; `git diff
  --check` passed. No network, full suite, commit or push. Native Chromium
  phone QA remains unverified (launch unavailable, exit 77); review remains.
  Spotlight review: actual-renderer regressions reproduce the pinned first card
  and checkout join violation: **2 failed in 4.58s**. Repair now includes spotlight
  insertion/deduplication before rendering and preserves each picked window's
  membership, banner markup and checkout flags, including price-only offers.
  Added coverage for three-drop cycling, a single complete window, every rendered
  checkout span, dense-window fallback, spotlight-only offers, other views/Saved
  and the OFF path. The native phone fixture now has an active spotlight.
  A standalone-banner fallback regression reproduced **1 failed in 4.10s**;
  fallback retains that banner's original placement instead of appending it
  next to the final checkout offer. Enabled output strips only full-line comments
  in the changed function blocks, preserving source comments and OFF bytes.
  Intermediate focused checks: **1 failed, 45 passed, 1 skipped in 93.02s
  (0:01:33)** (HTML budget fixed), then **1 failed, 47 passed in 95.31s (0:01:35)**
  (the active spotlight fixture needed a valid stored critic verdict).
  Compatibility checks: **515 passed in 320.75s (0:05:20)**, covering publication,
  checkout/front-page behavior, Browse/Saved, all selected frozen byte pins,
  template slots, public copy, unchanged ceilings and both audit-mode reruns.
  Final spotlight/feature checks: **54 passed, 1 skipped in 123.63s (0:02:03)**.
  Shipped 1,400-offer gzip bytes / unchanged ceilings: HTML **77,069 / 77,360**,
  JS **53,701 / 53,767**, CSS **14,846 / 14,913**, deals JSON **99,050 / 103,976**.
  All spotlight runs used `-n 1 -p no:cacheprovider`; owned `/tmp/task269fix-*`
  basetemps were deleted after every run. Existing hashes/fixture files are
  unchanged; `git diff --check` passed. No network, full suite, commit or push.
  Native Chromium phone QA remains unverified (unavailable, exit 77); review remains.
  Latest adversarial findings: fallback now checks every candidate spotlight
  insertion against sliding checkout caps (including a promo in the first pick);
  accepted mixed/home-only/beauty-only inventories receive diverse server windows
  and rotate across all three drops; inline `other`/`auto` price-only spotlights
  bypass the niche partition and remain in the initial 60 cards with banner markup.
  Actual-renderer/server regressions reproduced **4 failed in 9.05s**. First fix
  check: **1 failed, 55 passed, 1 skipped in 130.76s (0:02:10)** (JS was 20 gzip
  bytes over budget). Enabled ordering comment stripping now includes the complete
  ordering block; every OFF byte and all existing hashes remain unchanged.
  Final feature checks: **58 passed, 1 skipped in 120.92s (0:02:00)**, including
  varied-price/store exchanges, inventory/tail retention, exact checkout positions
  and private boost shares. Shipped 1,400-offer gzip bytes / unchanged ceilings:
  HTML **76,867 / 77,360**, JS **53,501 / 53,767**, CSS **14,846 / 14,913**,
  deals JSON **99,050 / 103,976**. Final compatibility/byte-pin checks:
  **669 passed in 251.24s (0:04:11)**, covering publication, front-page/checkout,
  Browse/Saved, template slots, public copy, all selected frozen byte pins, phone
  ceilings and all six offline reruns across both audit modes. Every latest run
  used `-n 1 -p no:cacheprovider`; owned `/tmp/task269fix-*` basetemps were deleted
  after each run and logs removed. Existing hash lines and recorded fixtures are
  unchanged; `git diff --check` passed. No network, full suite, commit or push.
  Native Chromium phone QA remains unverified (unavailable, exit 77); review remains.
  Second spotlight review (`269-review-findings.md`): confirmed placement now
  controls banner suppression. Unplaceable standalone spotlights retain the
  separate visible banner and the server's checkout-safe pick order. New
  spotlight picks in the first displayed window enter its normal repair pool;
  existing spotlight placement in other windows is unchanged. Regression coverage
  includes a real published 24-pick payload with checkout slots 0/5/10/15/20 plus
  a price-only checkout spotlight, and both checkout/plain spotlight arrivals,
  exact ordering parity without spotlight metadata, remembered arrivals, Saved
  return, uniqueness and window caps. Initial reproduction: **3 failed in 5.80s**
  (including a stale fixture sighting, corrected); dense-banner reproduction:
  **1 failed in 4.08s**. Intermediate: **2 failed, 59 passed, 1 skipped in 121.60s
  (0:02:01)** (new comparison used mismatched render limits; corrected).
  Final feature checks: **61 passed, 1 skipped in 122.93s (0:02:02)**.
  Shipped 1,400-offer gzip bytes / unchanged ceilings: HTML **76,905 / 77,360**,
  JS **53,538 / 53,767**, CSS **14,846 / 14,913**, deals JSON **99,052 / 103,976**.
  Final compatibility/byte-pin checks: **380 passed in 286.33s (0:04:46)**,
  covering front-page/checkout behavior, Browse/Saved, drop clock/pagination,
  template slots, public copy, selected frozen byte pins/phone ceilings and all
  six offline reruns across both audit modes. Every second-review run used
  `-n 1 -p no:cacheprovider`; owned `/tmp/task269fix-*` basetemps were removed
  after every run. Existing hash lines and recorded fixture files are unchanged;
  `git diff --check` passed. No network, full suite, commit or push. Native
  Chromium phone QA remains unverified (unavailable, exit 77); review remains.
  Operator simplification / third spotlight review (2026-10-17 12:50Z):
  removed spotlight pools, insertion/fallback repair, inline rendering, niche
  overrides and banner suppression. Spotlights use the original renderer/slot;
  only ordinary picks rotate. Exclusion happens before server pick selection and
  before browser rotation, preserving ordinary checkout spacing and window shares
  even when a duplicate banner would compress positions. Spotlight JSON and all
  card/render functions match OFF exactly. Rewritten regressions cover the default
  60-card limit at seed 2, dense standalone checkout banners, niche price-only
  banners, expanded pagination, arrivals, Saved and other views; every legal
  multi-window case still cycles its first ordinary card across three seeds.
  Initial red regressions: **3 failed in 8.77s**; server-selection boundary guards
  at checkout widths 3/5: **2 failed in 10.67s**. Final feature checks:
  **66 passed, 1 skipped in 138.73s (0:02:18)**. Shipped 1,400-offer gzip bytes /
  unchanged ceilings: HTML **76,687 / 77,360**, JS **53,319 / 53,767**,
  CSS **14,846 / 14,913**, deals JSON **99,050 / 103,976**. The ordinary search
  still prunes the adversarial fixture to 149 states and retains its hard 1,024
  state bound. Final compatibility/byte-pin checks: **581 passed in 303.92s
  (0:05:03)**, covering front-page/checkout behavior, Browse/Saved, pagination,
  drop clock, publication/template slots, public copy, unchanged phone ceilings,
  all selected frozen byte pins and six offline reruns across both audit modes.
  Every run used `-n 1 -p no:cacheprovider`; owned `/tmp/task269fix-*` basetemps
  were deleted after every run. Existing hashes and recorded fixtures are
  unchanged; `git diff --check` passed. No network, full suite, commit or push.
  Native Chromium phone QA remains unverified (unavailable, exit 77); review remains.
  Native spotlight follow-up (`269-review-findings.md`): the reported extra keys
  identify ordinary fixture offers 30 and 60; the published spotlight is offer 24.
  This checkout's unchanged publisher/renderer does **not** reproduce the extra
  card offline. Added a Node regression using the emitted real card/Saved-button
  markup and real `present`/`applyPublish` path across three drops, plus parsed
  grid ownership and ordinary three-window cycling checks. Every publication
  clones the same deals/spotlight; only publication timestamps/key change.
  The original native cross-drop assertion remains; initial render must now
  match the published spotlight keys exactly, and reload must preserve them,
  with grid outerHTML diagnostics. No fixture spotlight inputs were changed.
  Initial markup check: **1 passed in 7.79s**. Extended harness setup initially
  missed its toast-age stub: **1 failed in 7.08s**; corrected before final checks.
  Final feature checks: **67 passed, 1 skipped in 146.42s (0:02:26)**, including
  the unchanged shipped phone ceilings. Final exact-markup/cycling, Browse/Saved,
  template harness and four frozen OFF hash checks: **10 passed in 18.15s**.
  Standalone native attempt: **1 skipped in 6.96s**, launch denied/unavailable
  (77). Every run used `-n 1 -p no:cacheprovider`; owned `/tmp/task269fix-*`
  scratch was removed after each run. No production change or hash re-pin;
  `git diff --check` passed; no network, full suite, commit or push.
  Native row-filler root fix: `deal-actions.js` treated `.spotlight-grid` as an
  ordinary incomplete row and borrowed the next grid's first ordinary card after
  rendering. `state.spotlight` was never polluted; the previous renderer-only
  harness omitted this DOM mutation. The enabled home now excludes spotlight
  grids from row filling, both as recipients and lenders, without adding filler
  tiles to banners. Its transformed actions script is resolved before versioning;
  shared pages and every OFF byte retain the original script. New emitted-JS
  regressions reproduce **3 failed, 1 passed in 21.33s** at 1/2/3/5 columns, then
  verify three drops and reloads, exact published spotlight state/DOM ownership,
  multiple-banner lender protection, ordinary inventory/order, and unchanged
  ordinary borrowing/padding. Initial focused fixes: **6 passed in 24.23s**.
  Final feature checks: **71 passed, 1 skipped in 166.78s (0:02:46)**. Shipped
  1,400-offer gzip bytes / unchanged ceilings: HTML **76,689 / 77,360**,
  JS **53,322 / 53,767**, CSS **14,846 / 14,913**, deals JSON **99,051 / 103,976**.
  The native phone cross-drop/reload assertions remain unchanged; Chromium launch
  is still unavailable here (exit 77); standalone attempt: **1 skipped in 8.12s**.
  Final compatibility checks: **428 passed in 264.66s (0:04:24)**, covering
  front-page/checkout behavior, Browse/Saved, ordinary row filling, publication
  and template slots, public copy, drop timing, all selected frozen OFF byte pins,
  unchanged phone ceilings and six offline reruns across both audit modes.
  Every run used `-n 1 -p no:cacheprovider`; owned `/tmp/task269fix-*` basetemps
  were deleted after each run. Existing hashes and recorded fixtures are unchanged;
  `git diff --check` passed. No network, full suite, commit or push. The reported
  root defect is fixed and covered offline; native Chromium QA/review remain.
- **Support panel timing/cost copy (Codex task 272, 2026-10-17; uncommitted):**
  Shipped `support.panel_delay_s: 120` adds the elapsed-page-time gate to the
  existing card-eight/ask/dismissal rules, with one timeout refresh and no polling.
  Shipped `support.cost_phrase: "thousands of dollars"` supplies escaped support
  copy and invalidates the support page version. Strict config validation;
  defaults remain 0 / "hundreds of dollars", omitting inactive script/meta bytes.
  Historical fixtures explicitly pin those defaults (recorded before task 272);
  existing hashes/archives are unchanged. Offline fake-clock checks exercise both
  delays, the unchanged Join/weekly/engagement rules and one ask per page view.
  Brief discrepancy: this checkout ships `join.prompt_delay_s: 15`, not the
  brief's stated 35. Preserved that existing setting; explicitly tested the
  configured 35-second Join behavior alongside the 120-second support gate.
  Initial focused run: `1 failed, 135 passed in 60.32s (0:01:00)`; the only failure
  was the new test assuming the brief's 35-second shipped setting, corrected to 15.
  Focused support/Join/post-Join/public-copy rerun: `137 passed in 65.82s (0:01:05)`.
  Affected recorded-byte checks: `66 passed in 122.43s (0:02:02)`. Final expanded
  Join/post-Join checks (both delays and both Stripe link choices): `6 passed in 7.83s`.
  All runs used `-q -n 1 -p no:cacheprovider`; every owned `/tmp/task272-*`
  basetemp was deleted immediately when its run ended. `git diff --check` passed;
  existing recorded hash literals/archives are unchanged. Review remains;
  no network, whole suite, commit or push.
- **Dark polish (Codex task 254, 2026-10-17; uncommitted, ON):**
  `site.dark_polish: true` ships; code/missing-key defaults are false. New
  `publish/dark.py` injects the exact `templates/dark.css` and color-scheme meta
  after inventory, before tracking; config.py/config.yaml and only the named
  site.py import/version/pass lines changed. New `tests/test_dark_mode.py` and
  `tests/dark_mode_browser.cjs` cover additive bytes, dark tokens/contrast,
  idempotency, budgets and offline phone rendering. Recorded fixtures explicitly
  pin dark OFF in `tests/phone_budget_fixture.py`, `test_pwa.py`,
  `test_amazon_as_of.py`, `test_android_app.py`, `test_checker_index.py`,
  `test_checker_page.py`, `test_product_price_pages.py`, `test_product_shadow.py`,
  `test_store_policy_pages.py` and `test_unconfirmed_label.py`; no hashes changed.
  Gzip bytes OFF → ON: CSS **14,289 → 14,823** (limit 14,913), HTML
  **75,186 → 75,748** (limit 77,360). Initial new checks: `2 failed, 17 passed
  in 182.09s (0:03:02)`; both placement assertions corrected. Final targeted
  `-q -rs -n 1 -p no:cacheprovider`: **1051 passed, 6 skipped in 986.56s
  (0:16:26)**, including all listed regressions and both audit rerun modes.
  All six skips were browser launch failures. New Chromium check returned 77:
  browser assertions/PNG captures remain unverified; retain
  `/tmp/task254-shots/` (OFF/ON sites in `fixtures/` for the parent rerun).
  The required pass order leaves tracking/click blocks after dark CSS in the head;
  placement checks account for those blocks and require every other byte unchanged.
  Manual checks remain: iPhone Safari dark, Chrome Android dark theme, Samsung
  Internet dark mode. Ask the marketing contact for browser/app, a screenshot
  and inversion settings. Owned `/tmp/task254-*` basetemps/logs removed;
  screenshots/rerun fixtures retained. No network calls, full suite, commit or push.
- **Popular affordable drops (Codex tasks 255/255b, 2026-10-17; 255b uncommitted, OFF):**
  Both `publish.front_page.{popular_affordable,everyday_price}.enabled` switches stay
  false; `popular_affordable.shadow: true`, `keepa.popular_affordable_page: false`.
  `rank.py`/`publish/site.py`/`pipeline.py`/`cli.py`/`fetch/keepa.py` fix private
  scores/logs, first-window membership, shadow/page errors and clock skew, and add
  the soft price tilt. `popular_affordable.py`/`growth/report.py` deliver latest
  plus 24h medians privately from the same-key 26h encrypted-state ring
  `popular_affordable_shadow`. Capture: +0 tokens/+0 requests; tilts/shadow:
  0 tokens/0 LLM calls; existing read costs unchanged. Optional page: 5 tokens
  within six pages, normally 0 extra, at most +5/pull or 720/day. Offline targeted
  feature/report runs: **200 passed in 74.19s (0:01:14)**, **201 passed in 72.99s (0:01:12)**;
  front-page/appeal/unique-ASIN/Keepa: **307 passed in 132.40s (0:02:12)**;
  e2e `-k rerun`: **6 passed in 55.52s**. All used `-n 1 -p no:cacheprovider`;
  owned `/tmp/task255b-*` scratch deleted, hashes unchanged. Review then flip both
  ranking switches true; optional-page validation/activation remains separate.
  No full suite, network, commit or push.
- **Amazon search fallback (Codex task 259, 2026-10-17; review fixes uncommitted, ON):**
  Search URLs are built only on click and opened with `noopener`; the plain
  Amazon href keeps the query out of GA4 automatic outbound URL collection.
  Unicode hostname labels/decimal-digit runs fail privacy checks; Saved clears
  stale search links. Regression checks cover each finding, including the real
  tracking wrapper's argument shape and automatic href collection in both event
  phases. Existing OFF/empty-tag byte hashes and phone budgets pass unchanged.
  Targeted search/Saved/phone/public-copy/OFF-byte checks: **292 passed, 2 skipped
  (105.57s)**; offline `-k rerun`: **6 passed (62.54s)**, all
  `-n 1 -p no:cacheprovider`. Chromium launch blocked (exit 77); parent browser
  QA/review remain. Own `/tmp/task259fix-*` scratch removed; no network, whole
  suite, hash re-pins, commit or push.
- **Best value finder (Codex tasks 256/256b, 2026-10-17; 256 committed, review fixes uncommitted, OFF):**
  `compare.refresh: false`, `compare.publish: "off"`; all 256b review findings
  and the **On sale now — N% off** badge are implemented. The badge requires a
  live, confirmed published deal with a written local page; it copies the
  published checkout percentage and never changes the comparison order.
  Latest `256-review-findings.md`, including the 15:35Z operator note: title
  checks now remove the evidenced quantity and reject unresolved counts anywhere
  else, without an item/container noun list. Missing pack metadata requires one
  quantity expression; repeated quantities, cables/cases/cans, reverse counts,
  sets, word packs and multipliers drop the row from ranking. Clean single
  cables and attached capacity/wattage prefixes still work; plain uncounted
  `each` items keep the brief's rule. Explicit pack multiplication in `savings.py`
  remains available with pack metadata and verbatim evidence for both numbers.
  The earlier blocking-read deadline and public-history fixes remain intact.
  `compare.finder_reserve_tokens: 30` now gates BOTH quotas and the shared bucket
  before every finder dispatch. Receipts reconcile both ledgers and the bucket;
  absent receipts retain the full reservation, and unsent requests refund it.
  An oversized finder receipt records its actual cost and a durable `halted_day`,
  logs only `FinderReserveExceeded`, and stops all comparison work until the next
  UTC day, across run keys/reloads/cap changes. Shared rechecks remain available.
  The 11-cap/15-receipt regression configures an 11-token reservation to exercise
  this stop; the default 30-token reservation blocks the initial 11-cap call.
  This follow-up changes only compare.py, config YAML/loader, comparison tests
  and this entry; no existing hash or publication-budget fixture is re-pinned.
  Files: `data/compare_terms.yaml`, `compare.py`, `publish/compare_pages.py`,
  config YAML/loader, savings/rules, pipeline/site/SEO, `verify.py` and the
  HTTP API socket/stream deadlines and `errors.py` sent-request marker; tests in
  compare, HTTP client, API politeness and offline e2e.
  Failed terms back off and yield; finder receipts include total-result counts.
  Observation-based scheduling defaults to 12 h (<24 h price expiry).
  Browser clocks expire rows/banners and each sale badge's own confirmation
  expiry (new/used limits, with a hidden-style override). Unchanged facts keep
  sitemap/IndexNow versions stable. Both image formats work. Comparison reads avoid category,
  planner-latency and accuracy samples. Unsent deadlines refund both ledgers;
  sent deadline failures retain conservative debits and same-key replay safety.
  requests preserve reserves and leave ≥80% of bucket capacity after cost.
  A comparison-only 5 s inactivity timeout (capped by the global timeout) plus
  host spacing gates dispatch. The shared HTTP transport now caps each blocking
  read by the remaining absolute time, and bounds connect/write/TLS timeouts,
  using request-local thread state. Checks after headers, raw/decoded chunks and
  completion remain; expiry closes the response and stops paid work. Offline
  HTTP/HTTPS regressions use 4.9 s reads against 20 s (ending at 20, not 24.5),
  partial headers, identity/gzip bodies, injected clients and reused connections;
  subsequent requests retain their own deadlines/default inactivity timeouts.
  Four original OFF tree hashes match; no existing hash/fixture was re-pinned.
  avg90 NEW remains private; no historical label/badge, /p/ history link or
  history footer enters finder pages. ItemList URLs use current-row anchors.
  Sale badges keep the published percentage only for nonhistorical discount
  bases; average/median/history-based discounts cannot surface as sale badges.
  Policy tests cover preview/on, both old Amazon-history settings, all-store
  history policies, HTML attributes/alt text and inline JSON, before/after shared
  publishing. Only the unchanged analytics browser History API identifiers and
  its error comment are excluded from the keyword scan; they contain no facts.
  Oct 16 measurements: 60 tokens/min = 86,400/day,
  recent use 68–74k/day, 12–19k/day lost at the 3,600 cap; no recheck starvation.
  At the 12 h cadence, reserving 30 finder tokens/week + 40 product tokens/day
  per term is ≈44.3/day; 120 terms ≈5,314/day before receipt reconciliation,
  bounded by 6,000/day and 300/run while receipts stay within the reservation.
  Early observation refreshes share those caps. First live probe must confirm
  finder page vs total-result billing (including the 30-token bound), unitCount
  coverage and fresh/evidenced rows per term. Next Amazon call: permission for
  “bought in past month” and comparison pages themselves under amz378_998.
  Prior 256b targeted groups passed 241 + 389 + 7 + 98 checks. Adversarial
  follow-up reproduced **15 failed, 1 passed in 5.16s** before fixes. The first
  broader run had **1 failed, 331 passed in 39.65s** (new used-condition fixture
  used the wrong field, corrected), then **336 passed in 28.94s**. Earlier offline
  `-q -n 1 -p no:cacheprovider`: **389 passed in 98.77s (0:01:38)** (public copy,
  product pages, SEO, photos and phone budgets); **431 passed in 300.39s (0:05:00)**
  (feature/API/HTTP, gzip deadlines, shared rechecks and all requested reruns).
  After tightening the modified-count boundary to preserve capacity prefixes,
  the complete comparison module passed **234 passed in 19.33s**, including the
  four original OFF hashes and the independent new/used badge browser clocks.
  Prior review regressions reproduced **27 failed, 4 passed in 8.05s** before
  fixes. Focused fixes passed **69 passed in 4.45s**; the expanded policy and
  HTTP/HTTPS deadline/pooling checks passed **17 passed in 4.93s**. Final feature,
  HTTP and API modules: **382 passed in 28.97s**. Final public-copy, product-page,
  SEO, photo and phone-budget modules: **389 passed in 80.81s (0:01:20)**.
  Shared unique-ASIN/value-per-token rechecks, source concurrency and all seven
  selected offline e2e reruns: **119 passed in 255.78s (0:04:15)**.
  The 15:35Z operator follow-up reproduced **36 failed, 2 passed in 11.61s**
  before fixes; the initial focused group then passed **38 passed in 7.13s**.
  Latest final feature/HTTP/API run: **437 passed in 70.41s (0:01:10)**,
  including four unchanged OFF tree pins, reservation durability/reconciliation,
  quota/bucket boundaries, day halt/reload/replay/reset and continued rechecks.
  Latest public-copy/product-page/SEO/photo/phone-budget run:
  **389 passed in 112.79s (0:01:52)**; both existing baseline files keep their
  original digests. Latest shared unique-ASIN/value-per-token rechecks, source
  concurrency and seven selected offline e2e reruns:
  **119 passed in 290.94s (0:04:50)**. All latest runs used
  `-q -n 1 -p no:cacheprovider` with owned `/tmp/task256fix-*` basetemps.
  Own `/tmp/task256fix-*` scratch is deleted after each run; prior
  `/tmp/task256b-*` scratch was already removed. `git diff --check` passes.
  Review/live probe/activation remain; no network calls, full suite, commit or push.
- **Device-local extension index (Codex task 244c, 2026-10-16; uncommitted, checker OFF):**
  Adds versioned, deterministic `/check/data/all.json` with lossless dictionary
  columns/front-coded JSON values; every alias/field matches the shards. The
  10,000-alias full-history fixture is **1,414,035 bytes uncompressed**, with no
  dropped aliases. Oldest ended aliases (date, then alias) drop from both outputs
  at the 1.5 MB cap; live overflow fails before replacing files. Existing
  noindex/no-store headers apply. The extension saves the compact public list in
  local storage and downloads only on install/browser startup/six-hour alarms;
  page visits, misses, SPA navigation and popup paste make no network requests.
  Missing/expired data waits for a scheduled background refresh. Both publish
  and download clocks enforce 24 hours; open cards expire, and alarm updates
  refresh open cards/clear old popup results. No unlimitedStorage; global/site
  controls and Amazon/Walmart/eBay policies stay intact. Listing, permission
  justifications and privacy now explain device-local checks; PUBLISH.md
  documents the version-1 native-app decoder (the referenced task-248 iOS brief
  is absent from this tree). Rules/runtime-only zip rebuilt reproducibly.
  Initial focused checks: **9 passed / 0 failed**, then **152 passed / 2 failed /
  2 skipped**; both failures were the old rerun helper's checker file count,
  updated for all.json without changing its byte/state/send assertions.
  Final `-q -n 1 -p no:cacheprovider` across checker index/page, browser extension
  and the touched offline e2e file: **168 passed / 0 failed / 2 skipped (154.68s)**,
  including both enabled rerun modes, reproducible packaging and fresh rules.
  Node checks: **22 passed / 0 failed**. Both Chromium checks attempted the real
  browser but sandbox permissions blocked launch; browser assertions remain
  unverified. Owned `/tmp/task244c-*` basetemps/logs removed; `git diff --check`
  passed. Review, native browser QA and checker activation remain.
  No whole suite, workflow-test edits, retailer/Keepa/LLM calls, commit or push.
- **Checker/page review fixes (Codex task 252a, 2026-10-16; uncommitted, checker OFF):**
  AI wording follows the operator decision; Walmart checker history is on and
  eBay stays live-only. Amazon hide falls back to links with a private config
  note; prices/as-of clocks require Amazon confirmations. Checker cards credit
  sources, name other stores by host, clear interrupted lookups, and use slug
  words handed to home through sessionStorage, with no search query in the URL.
  Shared PWA/footer passes now include /check/ while tracking skips it; Android's
  checker adapter follows the new signature. Product retention counting and
  mixed Amazon-hide blank-photo behavior are pinned. Photo failures print two
  decimals; search caps are 20/run and 49/day, with the 90% floor unchanged.
  Each lookup can cost 11 finder + 50 product tokens: 49 * 61 = 2,989/day.
  Raising the old 120/day cap cannot meet the requested ~3,000-token limit;
  the initial 270/day estimate counted only finder calls and was corrected.
  Scoped `-n 1`: **517 passed, 0 failed, 2 skipped** across checker index/page,
  store policy, own checks, public copy, product prices, photos, Android, tracking
  and PWA. After correcting the daily cap, the final photo rerun was **75 passed,
  0 failed**. Node site-check harness: **1 passed, 0 failed**. Historical privacy
  fixtures preserve old store/Android comparisons; only 12 checker-enabled
  home/reader/policy-metadata hashes were refreshed, with member payloads unchanged.
  Native Chromium launch is blocked by sandbox socket permissions even with
  crash reporting disabled; checker/Android browser QA remains unverified.
  Owned `/tmp/task252a-*` scratch removed. Review/merge remain; no whole suite,
  live calls, commit or push. Parallel task 252b's AI-take files and workflow
  tests are untouched.
- **AI take review fixes (Codex task 252b, 2026-10-16; uncommitted, OFF):**
  Findings 2–18 (including duplicates) use verdict-specific leads and a closed
  sentence grammar binding each price, percentage, count and date to its fact.
  Best Buy and the shipped About-usual answer remain valid; arbitrary advice,
  price predictions, free-text numbers, unsupported currencies and invisible
  format characters fail closed. Prompts carry canonical store/condition facts.
  Character/word caps precede regex work; optional preparation failures clear
  every take and allow publishing. The two explicitly requested `site.py`
  changes remove stale `last_run` replay and add that failure boundary.
  Fenced/truncated/deep/malformed replies salvage only complete top-level rows.
  Paid attempts, including rejections/interruption, persist before the request;
  never-attempted then oldest-attempted pages rotate first. One cache row per
  page retains its latest outcome plus four previous facts/version pairs;
  matching retained rejections are not paid for again. Claims expire after 30
  days; obsolete publish/per-facts cache rows are pruned. Batch sizing subtracts
  shared feature/global/day/run spend and pacing before the atomic reservation.
  Walmart may feed takes; eBay never does; Deal Check still requires history mode.
  Disabled baseline regenerated from untouched pre-task HEAD `87f21bcd2156`:
  **1,189 public files/four modes plus member payloads**, preserving current
  newsletter/Android/privacy changes rather than old task-245 snapshots.
  Initial AI-only run: **154 passed, 10 failed** (fixture assumptions/snapshots,
  corrected). Final focused regression: **364 passed, 0 failed (182.10s)**:
  `test_ai_take.py`, `test_e2e_offline.py`, `test_claude_client.py`,
  `test_publish_site.py`, `test_uncertain_reservations.py`, `test_lighter_state.py`.
  Both runs used `-q -n 1 -p no:cacheprovider` with owned `/tmp/task252b-*`
  basetemps; all owned scratch/baseline directories removed. `git diff --check`
  passed. Finding 1 remains assigned to parallel
  task 252a as the brief requires; `tests/test_public_copy.py` is untouched.
  Review/activation remain; no whole suite, live calls, commit or push.
- **Newsletter review fixes (Codex task 252c, 2026-10-16; uncommitted, OFF):**
  Shared customer-email exclusions unwrap tracker URLs, reject excluded store
  text and named product marks, and scan subject/plain/HTML before sending
  (including personalized links and confirmations). Display-name credits remove
  internal source IDs; newsletter credits deduplicate names. Remembered deal
  paths require a written page; unsupported exclusion keys fail config loading.
  Confirm GET is read-only with one POST button; POST confirms without TTL.
  Confirm/unsubscribe responses depend only on link syntax, never KV existence
  or token match; only matching tokens mutate records. Existing limits stay.
  The task's narrow workflow-harness exception changes only confirm GET/POST
  checks; the unsubscribe assertions remain unchanged. Legacy-record/TTL and
  dispatch-only deployment docs corrected. Scoped `-q -n 1` checks: initial
  **312 passed / 7 failed** across newsletter, policy, partner policies, members,
  digest and workflows (all seven are untouched Stripe localhost tests blocked
  by sandbox socket permissions); final affected checks plus both offline
  idempotency modes **266 passed / 0 failed**; expanded Fire-tablet policy
  regressions **153 passed / 0 failed**. Worker direct module: **77 passed /
  0 failed**; file and required directory runners each **1 passed / 0 failed**.
  Owned `/tmp/task252c-*` basetemps/logs removed. No protected workflow changes
  beyond the authorized confirm checks; the seven socket tests remain unverified.
  Review and manual Worker deployment remain before enabling either switch.
  No whole suite, external calls, email, commit, push or deployment.
- **Best-deals newsletter (Codex task 247, 2026-10-16; uncommitted, OFF):**
  Both switches default off; postal address required. Daily/ISO-week picks exclude
  Amazon/eBay before the model and link only to written wowpriced pages. Independent
  opt-in retains sign-up dates; legacy records are pending, expire 30 days after
  their one confirmation email, and lose the TTL when confirmed. Read-only local
  `newsletter-preview --out DIR`; no support-mode trial CTA or external thumbnails.
  Updated assertions in `test_newsletter.py`: retailer/source links → deal pages
  and plain credits; wall → `/best-deals-today/?ref=email`; image → permitted own
  asset; button/counts → “See the deal”; trial/membership links → `?ref=email#signup`;
  capped-batch equality excludes the current as-of line; pending rows add `ts`.
  Partner-policy assertions expect plain credits/dropped Amazon trackers; delivery
  and unconfirmed-price fixtures add the required address, daily cadence and pages.
  Final scoped run: **601 Python passed, 0 failed** (`-q -n 1`, both audit reruns);
  **75 Worker tests passed, 0 failed** via direct module; required directory runner
  also passed (one file). Initial newsletter run: 84 passed/7 body-fixture failures,
  all corrected. Owned `/tmp/task247-*` scratch removed after every run. Pre-task
  HEAD: **205 public files/four modes plus member payloads match**, without repins.
  Re-run deploy-subscribe-worker BEFORE setting confirmations_enabled or enabled;
  the old Worker treats legacy sign-ups as confirmed. Before launch: approve the
  local preview, deploy reviewed Worker, set address and Amazon SES `SMTP_*` secrets;
  check SPF/DKIM/DMARC alignment and DKIM `h=` includes List-Unsubscribe and
  List-Unsubscribe-Post; enable confirmations before issues. Amazon stays excluded;
  eBay needs written EPN email approval before removal. No extension edits, whole
  suite, live calls, upload, email, commit, push or deployment.
- **Android TWA + shared Deal Check (Codex task 246, 2026-10-16; uncommitted, OFF):**
  Validated config stays disabled, certificates empty, Amazon `limited`; app-only
  guards, untagged clicks, shares, asset links and price-free offline fallback.
  Preserves **1,352 HEAD files/eight modes**. Bubblewrap 1.27.0 manifest, manual
  build workflow, listing, feature graphic and local screenshot tools are ready.
  Regression: **432 passed/0 failed/2 skipped**; final Android checks:
  **53 passed/0 failed/1 skipped**, all `-n 1`; own scratch removed. Historical
  privacy fixture fixes four old comparisons without repinning. Protected tests
  and `extension/` unchanged. Chromium socket restrictions block browser QA/four
  screenshot PNGs; actual Android build unverified. Follow [operator steps](../android/README.md)
  before launch: review, QA/build, signing/certificates, app-link verification,
  manual Play upload and 12-tester/14-day testing; Amazon needs written app approval
  before `as_site`. No whole suite, external calls, email, upload, commit or push.
- **Store policy + bounded /p/ output (Codex task 250, 2026-10-16; uncommitted, ON):**
  All stores ship `history` (Walmart explicit); shared `live_only` gates histories,
  lowest-seen, badges and saved-price watches using original stored facts.
  Product output keeps 90 days, prunes state/admissions after 180, caps at 6,000
  product files and shares only written paths; private counts warn above 15,000
  site files. Pre-task HEAD matches all 324 public files/eight variants + members.
  **364 final scoped checks pass**, including byte pins and both rerun modes
  (`-n 1`); owned `/tmp/task250-*` basetemps/logs removed. Review/commit/push
  remain before Amazon-show deployment; no live calls, full suite, commit or push.
- **Deal Check page (Codex task 241, 2026-10-16; uncommitted, OFF):**
  `/check/` displays shipped verdicts through same-origin SHA256 shards, with
  prefill, dated results, short-link guidance and enabled-only search/navigation.
  Amazon `history` requires `amazon_history: show` and reuses the existing
  Eastern-time disclosure helper; watches stay off, Walmart/eBay live-only.
  The reader excludes tracking scripts. Disabled output matches untouched
  `7c202391bee4`: **141 public files/four modes plus member payloads**, no repins.
  Regression run: **615 passed, 1 failed, 1 skipped**; the new Node test's
  hardcoded date was corrected, final checker rerun **12 passed, 1 skipped**.
  Thus **616 distinct checks pass in their final state**, including both
  offline rerun modes, alias/shard parity and controller/error/race flows.
  All runs used `-n 1`; owned `/tmp/task241-*` scratch/logs removed. Chromium
  and its headless shell hit sandbox socket permissions: native browser QA
  and Lighthouse measurements remain, along with review/activation. No whole
  suite, workflow-test edits, live/paid calls, commit or push.
- **Checked AI price takes (Codex task 245, 2026-10-16; uncommitted, OFF):**
  `product_pages.ai_take` adds one budgeted batch, exact number/date and direction
  checks, facts-hash/prompt-version caching and immediate stale-take withdrawal.
  Verified summaries appear above `/p/` facts and in history-only Deal Check
  entries; Amazon follows its history switch, Walmart/eBay are excluded.
  Disabled output matches **1,185 public files across four access modes** plus
  member payloads. Focused regression: **649 passed, 3 failed**; all corrected
  (duplicate fixture and replay JSON key order). Final feature/rerun checks:
  **75 passed, 0 failed**, including both enabled audit modes with identical
  bytes, rows and calls. All runs used `-n 1`; owned `/tmp/task245-*` scratch
  was deleted. Review/activation remain; no whole suite, live calls or commit/push.
- **Browser extension (Codex tasks 244/244b, 2026-10-16; uncommitted, ON after install):**
  One plain-JS MV3 package for Chrome/Firefox; URL-only shared aliases/shards,
  30-minute session cache, popup checker and global/per-site controls. Review
  fixes refresh/clear cards on URL changes (popstate + a URL-only 1 s timer),
  prune expired cache keys and tolerate quota failures, and list hidden sites
  with Unhide buttons even on tabs without a content script. Firefox minimum
  is 140. `extension.amazon_mode: full` (Amazon OK, amz378_998) is validated
  and read by `build-extension`; `link` is the configurable fallback. This tree
  already permits Amazon checker history; cards show only item/link if the
  entry lacks price facts. Walmart history is supported by the extension;
  eBay remains live-only. Default-on wording agrees in popup, listing and privacy.
  Rules and the reproducible runtime-only zip were rebuilt. Reviewer steps,
  matching manifest/listing summary, navigation limitation, local promo source
  and an Amazon fixture without price facts are in the store kit.
  Final extension-only pytest: **64 passed, 0 failed, 1 skipped** (`-n 1`),
  including four modes with privacy-only site changes and **17/17 Node cases**.
  The browser network allowlist now requires an observed shard request; the
  real-browser script also checks default-on and SPA/Back navigation. Actual
  browser execution and rendering were attempted, but Chromium launch returned
  `Operation not permitted` (browser exit 77, renderer exit 1). The two existing
  1280×800 PNGs are retained; refreshed screenshots and the 440×280 promo PNG
  remain ungenerated, and browser assertions remain unverified. Rerun the local
  renderer and browser QA before listing; instructions are in
  [PUBLISH.md](../extension/store/PUBLISH.md). Owned `/tmp/task244b-*` scratch
  was deleted. Checker stays disabled; task 241's live page/data and a concrete
  indexed reviewer URL chosen at submission still precede listing. No whole
  suite, commit, push or deploy.
- **Photo review fixes (Codex task 253b, 2026-10-16; reviewed, shipped):**
  Restored pre-253 single-product Amazon body-ASIN dp click URLs and dedupe
  keys when the stricter identity agrees, preserving source trackers. Sale,
  store and search links cannot borrow an example product's identity/photo.
  Known identities no longer suppress existing short-link and empty-canonical
  Amazon post-link click repairs. Private recovered-identity counts exclude
  new direct-product/Keepa rows. DealMoon's fixture now matches observed opaque
  `d=` cards, with no inferred identity. Added regressions and restored the
  two pre-253 canonical/key assertions. Required focused validation:
  **349 passed, 0 failed** across `test_photo_identity.py`, `test_dedupe.py`,
  `test_photos_every_deal.py`, `test_e2e_offline.py`, `test_short_links.py`,
  `test_post_links.py`, `test_tracker_links.py` and `test_product_images.py`
  (`-q -n 1 -p no:cacheprovider`, 2m25s). Owned `/tmp/task253b-focused`
  basetemp deleted. Review follow-up: identity no longer skips the source-redirect
  resolver either (click routing stays exactly pre-253; regression test added).
- **Photo product facts (Codex task 253, 2026-10-16; shipped with 253b, ON):**
  Exact Amazon/Walmart/retailer facts now have private `product_identity_url`,
  separate from click routing. RSS/page readers retain transient product
  anchors independently of image rights and capped model/article text. Upsert
  reads those/body links, actual product keys in `PostPhotos.links`, in-link
  photo keys and completed short-link targets; one
  product only, with store/roundup/listing guards. Existing Amazon body-URL
  ASIN fallback already worked: a public Get deal URL pointing at a post is
  **not proof** that its stored canonical link lacks identity. Normalized photo
  keys were not URLs, and non-Amazon body URLs had no independent stored field.
  A one-time offline pass reads stored links and actual sighting rows, bounded
  by the existing 60/run identity allowance. It cannot reconstruct discarded
  post bodies. It changes no price, evidence, dedupe key, click or credit link.
  Granted exact-product photos run first; recovered non-Amazon URLs use the
  existing conditional og:image pass (40/run), while ASINs use capped Keepa.
  Private `photos` reports recovered identities by source and missing photos.

  Offline source fixtures cover Slickdeals official RSS `u2` (its existing
  body-ASIN path already worked), Deals of America's title anchor outside
  `more_details`, Hip2Save full-feed viglink `u` to Walmart and an enriched
  article beyond the text cap. The observed DealMoon cards have href-less
  brief anchors and opaque `d=` buy links, which recover no identity. Those
  links and Brad's `/go/d/...` carry no recoverable product fact; they stay
  unresolved without a new authorized observation. No new resolver, paid
  search or source-image permission.

  Estimate from the brief's counts, **eligibility ceilings, not measured
  recovery**: Amazon 189/256 (73.8%) can use Keepa after an exact ASIN is found;
  Walmart 16/256 (6.3%) and other stores 51/256 (19.9%) require an exact product
  plus a permitted source image or successful existing og:image read. Hip2Save's
  37/256 (14.5%) is a source-image ceiling overlapping those store buckets,
  not another share to add. Three supplied source-shaped single-product fixture
  cases recover identity; ambiguous roundups recover none. The live 256 rows
  are unavailable here, so actual backfill yield and browser photo loading
  remain unmeasured. At the quoted 2,427-card snapshot, 14 additional showing
  photos would cross 90%; image URLs alone do not establish browser success.
  Validation: **566 passed, 0 failed** across `test_photo_identity.py`,
  `test_photos_every_deal.py`, `test_product_images.py`, `test_dedupe.py`,
  `test_extractor.py`, `test_rss.py`, `test_pages.py`, `test_pipeline_sources.py`,
  `test_tracker_links.py`, `test_short_links.py`, `test_post_links.py` and the
  unchanged `test_e2e_offline.py`. Final identity/merge/photo guards:
  **164 passed, 0 failed**, including all **50 identity checks** and both
  existing offline rerun modes. Task 253b restores the pre-253 body-ASIN
  canonical/key assertions that task 253 incorrectly changed. All runs used
  `-q -n 1 -p no:cacheprovider`; owned
  `/tmp/task253-*` basetemps/logs are deleted. Whole suite and workflow tests
  were not run; `tests/test_workflows.py`, evidence verification, savings and
  `site_check.min_photo_pct` are unchanged. No live calls, commit or push.
  Review and live/browser coverage verification remain.
- **Deal Check index (Codex task 240, 2026-10-16; merged, OFF):**
  Validated `checker:` builds deterministic, bounded SHA256 lookup shards after
  product pages, with shared URL rules/40-row fixture, dated public facts and
  existing code verdicts. Amazon stays link-only; Walmart/eBay stay live-only;
  conditional, flagged, unconfirmed and private prices cannot feed history.
  Disabled output matches untouched `d28b551eebd4`: **138 public files across
  four access modes**, plus member payloads. **295 final index/identity/API
  checks pass**, plus the earlier **291 publisher/product/pipeline checks**;
  both enabled audit-mode reruns keep shard bytes, DB rows and sends stable.
  All runs used `-n 1`; owned `/tmp/task240-*` basetemps are deleted. Oldest
  ended aliases drop before the 64 KB cap; live-only overflow fails before writing shards.
  Unknown/deleted offer requirements cannot certify an ended plain price.
  Review and task 241's page remain. No whole suite, workflow-test edits,
  live/paid calls, commit or push.
- **Keyword volumes (Codex task 238, 2026-10-16; uncommitted):** manual-only
  CLI/workflow, 40 validated US/English phrases, exact Google URL allowlist,
  paced three-request ceiling and one 30-second retry. Sorted metrics save
  privately; existing growth-report copy defaults stay unchanged. **276 targeted
  checks pass** (`test_keyword_volumes.py`, unchanged `test_growth_report.py`
  and `test_ads_guard.py`, `-q -n 1`); own `/tmp/task238-*` basetemps removed.
  Review and the first real access test remain. No whole suite, workflow
  dispatch, live calls, workflow-test edits, commit or push.
- **Exact Back + off-screen cards (Codex task 237, 2026-10-16; ON, phones only):**
  Anchor corrections stop after six frames/500ms plus one late redraw check;
  user input cancels, with one restore event. `site.offscreen_cards: true` adds
  home-only CSS inside `@media (max-width:700px)` with real-card sizing and the
  first eight cards of each grid always rendered (desktop reload drifted
  ~1.5k px with multi-column rows, so desktop is excluded). Chromium checks
  (Oct 11 real snapshot, 199 deals; and the 180-offer test): Back and reload
  land 0 px off at 390x844 and 1280x800, on and off. Local Lighthouse mobile,
  3 alternating runs: off 70/69/65, on 80/91/81; TBT ~500 -> ~180 ms; CLS same.
  Byte comparison and pins: [offscreen-cards.md](offscreen-cards.md).
- **Fetch hosts side by side (Codex task 236, 2026-10-16; committed, OFF):**
  `fetch.parallel_sources: 1` (validated 1..6). Opt-in web readers hold
  registrable host sets, including discovered redirect/enrichment hosts;
  paid/shared/unknown adapters and their hooks remain exclusive on the main
  thread. SQLite calls return to its original thread/connection; host locks
  preserve robots, spacing, backoff and deadlines. Results retain source order;
  private summaries add `fetch_seconds`/`fetch_parallel`. Pre-edit `ca35c7c`
  posts/errors/fetch DB rows and **45 public files** match at 1 and 4 workers.
  Four fake hosts: **48 → 12 seconds**; live speedup unmeasured. **415 final
  targeted checks pass**, including both rerun modes; earlier 353-check set
  also passed. All used `-n 1`; owned `/tmp/task236-*` basetemps removed.
  Review remains; no whole suite, workflow-test edits, live calls, commit or push.
- **Keep parse pool full (Codex task 235, 2026-10-16; committed):**
  Refills unique reads at existing concurrency as slots finish; ordered
  recording, shared fingerprints and slot-free cached/structured/skipped posts.
  Deadline/cap cutoffs drain paid reads; backlog logs count remaining retries.
  Added private `parse_seconds`/`max_in_flight`; old-loop fixture preserves
  pairs, other stats, cache and spend. Clock/event and real-ledger guards pass:
  **262 focused checks (both offline rerun modes)** plus **69 final guards**.
  All `-n 1`; own `/tmp/task235-*` basetemps removed. Review/live throughput
  observation remain; no whole suite, workflow-test edits, commit or push.
- **Hands-off scorecard (Codex task 234, 2026-10-16; committed):**
  Records idempotent deadline/backlog counters; private report opens with
  OK/CHECK and UTC publication gaps, unfinished runs, per-lane backlog and
  lost AI calls for yesterday plus seven prior days. Missing data is unknown;
  no new requests or public fields. **379 focused checks pass**, including
  23 final scorecard checks and unchanged public bytes in four access modes.
  All runs used `-n 1`; owned `/tmp/task234-*` basetemps removed. Review remains;
  no whole suite, workflow-test edits, commit or push.
- **Bounded prefilter (Codex task 224, 2026-10-16; committed, OFF):**
  Indexed scan: 12,000 chars / 32 per number / 128 numbers / 256 savings pairs.
  Known fixtures match legacy; unknowns retain capped reads (+1 Haiku in the
  malformed fixture). Disabled: 196 public files/four modes plus calls,
  reservations, decisions and member payloads match. **371 final focused checks
  pass** (`-n 1`, own `/tmp/task224-*` removed), including reruns/recovery.
  Details and remaining fence/HTML regex risks: [bounded-prefilter.md](bounded-prefilter.md).
  Review/operator's whole suite remain; workflow tests untouched, no commit/push.
- **History from own checks (Codex task 233, 2026-10-16; uncommitted, ON):**
  Validated `seo.product_pages.record_own_checks`: trusted exact Woot, Best
  Buy/eBay and paid/feed Keepa readings use their own UTC hours, deduped with
  an atomic 24/offer/day cap (1..96). Failed/suspect/unconfirmed/mismatched
  readings add nothing. Gates, admissions and Amazon hiding stay unchanged;
  private summary adds exclusive eligibility reasons and today's check count.
  No existing price-history age prune was present: new `check:` rows expire
  after 90 days; post history stays. Offline hourly fixture with repeated feeds:
  300 Woot + 10 Best Buy + 10 eBay = 7,680 rows/day at 24/offer; shipped at **1/offer/day = 320 rows/day** (~29k over 90 days).
  Disabled: all five paths match untouched HEAD's full DB dumps and **162 public
  files across four modes**. Targeted regressions, both offline rerun modes and
  **82 final feature/chart checks pass**; three strengthened cases also reran
  green. All runs used `-n 1`; owned `/tmp/task233-*` basetemps removed. Review
  remains; no whole suite, live calls, workflow-test edits, commit or push.
- **Posted deal redirects (Codex task 231, 2026-10-16; uncommitted, ON):**
  `seo.posted_deal_redirects` enables 1,500 sorted 301s to matching category
  hubs (validated 1..1,900); private targets/dates survive pruning, published
  pages win and oldest rules drop first. Reads actual social success ledgers;
  first-send metadata is saved before reruns. **641 final targeted checks pass**,
  including both audit modes, meta row counts and disabled byte compatibility.
  The 129-file affected run passed 4,694 checks; two fixture failures were fixed
  and rerun, while seven Stripe socket-permission failures also reproduce on
  untouched HEAD `ad32e1a5f41d`. All runs used `-n 1`; owned `/tmp/task231-*`
  basetemps removed. Whole suite left to the operator; workflow tests and
  Pinterest posting unchanged. Review remains; no commit or push.
- **Phone publication budgets (Codex task 206, 2026-10-16; uncommitted, OFF):**
  Recovered the interrupted worktree; `tests/test_phone_budget.py` was complete,
  the compressed baseline decoded, and this handoff had no task-206 entry.
  `quality.phone_budget.enabled: false` in code/config; strict booleans and
  finite positive limits, with positive integer byte ceilings. Proposed lab
  limits are **LCP 2,500 ms / CLS 0.1 unitless**, separate from category scores.
  The existing Lighthouse summary receives resolved values through Python's
  config loader and `LH_PHONE_BUDGET`; OFF preserves prior output/status.
  ON retains accessibility/SEO gates and fails missing/error/non-finite or
  negative vitals as "not measured", with actual/limit/units for each page.
  The existing audit pages, schedule and tracker blocks stay unchanged.

  Read-only `python -m scripts.publication_budget OUTPUT --config config.yaml
  --expected-offers 1400` emits bounded JSON and a short stderr summary. It
  validates the local directory, offer count/unique keys, matching site version
  and home markup. Missing/unsafe assets fail with controlled labels; URLs,
  query strings and payload bodies stay out of diagnostics. Root/asset symlink
  escapes fail. Inline executable JS/styles plus unique local assets are
  concatenated in document order without separators; MIME types, query/path
  aliases, external-script bodies and preload boundaries are covered. External
  JS/CSS references get bounded labels/counts and are never fetched. JSON-LD
  remains separately reported markup. HTML includes its actual inline content;
  JS/CSS component measurements overlap HTML and must **never be summed into
  a transfer total**. Compression is gzip level 9, mtime 0.

  Frozen input: `tests/phone_budget_fixture.py`, **1,400 offers**, seed **206**,
  **2026-10-16 12:00 UTC**, stable keys, 11 categories, seven stores, seven
  current-price tuples, codes/steps, new/used conditions and 90% synthetic
  `images.example.invalid` URLs. Uses the existing publisher's `make_deal`,
  in-memory DB and fake/unavailable network; no real state or fetched images.
  Support/open/closed configs and full shipped config are captured in
  `tests/fixtures/phone_budget_baseline.json.xz`, pre-edit HEAD **755371a280d8**;
  the input digest is checked and configs cannot silently simplify. The
  reference measurement includes shipped `click-signal.js` and `traffic.js`.
  Before/after raw/gzip bytes are identical (each cell is **raw / gzip**):

  | Mode | Home HTML | Own home JS | Own home CSS | deals.json |
  |---|---:|---:|---:|---:|
  | Support | 256179 / 58767 | 130201 / 37567 | 54021 / 13257 | 1474106 / 91763 |
  | Open | 262050 / 62104 | 143711 / 42300 | 45925 / 11765 | 1474108 / 91761 |
  | Closed | 213772 / 59396 | 143711 / 42300 | 45925 / 11765 | 346386 / 29994 |
  | Shipped | 319136 / 73676 | 181972 / 51206 | 59606 / 14202 | 1509792 / 99024 |
  | Proposed gzip ceiling | 77360 | 53767 | 14913 | 103976 |

  Each ceiling is **ceil(1.05 × the largest baseline gzip size)**, measured
  before implementation; proposed headroom, not live-vitals observations.
  JSON-LD is 130 raw / 118 gzip bytes in every mode. Four public trees contain
  **4,448 files** (support 1,440; open 1,439; closed 37; shipped 1,532), plus
  eight adjacent IndexNow files. Tests compare filename/digest/raw/gzip with
  bounded file-delta diagnostics, versions, full state/meta, receipts and
  member payloads against the archive with the knob OFF and ON. Repeated
  analysis must be byte-identical and leave all file bytes/mtimes and DB
  contents unchanged. Existing snapshot tests/pins are untouched.
  Independent regeneration from untouched HEAD matches all four archived
  public trees, state/meta, receipts, member payloads and byte measurements.
  Final focused set: **619 passed, 7 failed**, including all **201 task checks**
  and both offline pipeline rerun modes. Main run: 611 passed / 7 failed
  (3m28s); final eight full-publication comparisons: 8 passed (3m15s).
  Ran only `test_phone_budget.py`, `test_workflows.py`, `test_publish_site.py`,
  `test_pwa.py`, `test_support.py`, `test_lighthouse_polish.py`,
  `test_e2e_offline.py`, `test_photos_every_deal.py`, `test_home_polish.py` and
  `test_safety_real_numbers.py`, always **-q -n 1 -p no:cacheprovider**, with
  isolated `/tmp/task206-*` basetemps deleted when each run ended. Seven Stripe
  workflow checks independently fail on untouched HEAD at localhost socket
  creation (`PermissionError: Operation not permitted`):
  `test_deploy_checks_the_yearly_price_with_stripe` change0–5 and
  `test_deploy_yearly_check_skips_without_a_price_and_fails_without_a_key`.
  No new failures; those seven checks outside this sandbox and review remain.

  No local real Lighthouse reports were available; only synthetic reports
  are exercised, including the workflow's actual loader/env/parser command.
  Real-user LCP, INP (including the ≤200 ms target), cache reuse, visual
  accessibility and retention remain **not measured**; existing lab LCP/CLS
  would be lab samples only. Review precedes activation. No product templates,
  shopper scripts, Worker or fast/hourly jobs changed; accepted 119–121 remain.
  `tests/test_workflows.py` is unchanged. No external calls, browser install,
  workflow dispatch, commit, push or deployment. Full suite is left to the
  operator; only owned `/tmp/task206-*` scratch is removed after each run.
- **Publish link facts (Codex task 225, 2026-10-16; uncommitted, OFF):**
  Validated `publish.reuse_link_facts.enabled: false`. Pure identity, credits,
  steps, final Get deal and earns live in `publish/link_facts.py`; source-file
  resolution stays outside. Ranking derives current link facts without a full
  public projection; enabled publication alone shares immutable results in one
  render-local map, keyed by every routing input. Labels, checkout/confirmation,
  images, scores and public/pick selection remain separate. Compatibility site
  imports/wrappers, tracker protection and Amazon routing/history/as-of rules stay.
  Pre-edit **755371a280d8**, fixed **2026-10-16 12:00 UTC**: **287 public files +
  8 adjacent IndexNow files** match with reuse off/on across four access modes,
  including versions, raw/gzip sizes, member JSON, link mix and selected order;
  no existing repins. **3,267 distinct targeted checks across 91 files are green**
  in their final context, including **71 feature checks**, both enabled offline
  pipeline rerun modes and complete DB dumps after reopen. All pytest runs use
  `-n 1` and dedicated `/tmp/task225-*` basetemps; the full suite remains for the
  operator. Per-file isolation exposed the registry fixture's fake RSS module
  missing `MAX_ENTRIES`: **the same failure reproduces on untouched HEAD**.
  Pages + registry together: **58 passed on HEAD and 58 on this change**, with no
  fixture edits. Shared disk exhaustion interrupted the first adjacent run;
  restarted checks cleared owned temporary outputs per test/file and completed.
  Fixed 2,300/5,000-deal profiles (all distinct routing keys): full projections
  **4,284/9,313 → 2,300/5,000**, off and on. Enabled link derivations fall by the
  same **1,984/4,313**. Separate uninstrumented CPU/peak-RSS samples show the map
  does **not** consistently beat the extracted off path; keep it OFF for review.
  Counts, CPU/RSS, bytes, costs and remaining coupling:
  [publish-link-facts.md](publish-link-facts.md). Own scratch/logs removed; existing
  workflow tests untouched. Review, the operator's full suite and representative
  measurements precede activation. No live/paid calls, commit, push or deploy.
- **Strict home browse slot (Codex task 205, 2026-10-16; uncommitted):**
  `dealradar/publish/template_slots.py` counts literal slots and reports safe,
  distinct missing/duplicate errors. `publish/site.py` validates the home slot
  and its selected variant before ranking, output-directory changes or state
  writes, then uses that same loaded home template for insertion and versions.
  One browse descriptor resolves presets/Saved once; membership's stripped
  input and support's marked input retain their exact historical hash rules.
  Existing page preprocessing was moved unchanged to allow this early check;
  PWA → traffic → profiles → inventory → tracking → click signal → sitemap/audit
  → IndexNow sealing stays unchanged. No active owners were present; accepted
  119–121/210/211 code and shipped Amazon hide are preserved. 201–204 are absent.
  Added `tests/test_template_slots.py` and the compressed pre-edit byte archive
  `tests/fixtures/template_slots.json.xz`: actual checkout **ac1355719ecb**,
  fixed **2026-10-16 12:00 UTC**, configs/facts/versions retained. All **24
  variants**, **1,302 public files**, **48 adjacent IndexNow files** and serialized
  member payloads match exactly, with filename/first-byte-area diagnostics.
  Covers support with/without links, open/closed/shipped modes, PWA off/on,
  Amazon hide/show fixtures, custom/empty presets and Saved off/on. Existing
  byte pins are untouched. **40 final task checks pass**, including literal
  nonrecursive insertion, unchanged trees/full DB dumps/receipts on failure,
  correction/rerun idempotency, single home/browse reads and the existing Node
  harness/syntax checks. Seven initial failure/read-consistency cases failed
  against the original publisher before the fix. **333 focused checks pass**,
  including both offline pipeline rerun modes. Full `.venv/bin/python -m pytest
  -q -n 2 -p no:cacheprovider --basetemp=/tmp/task205-full-90DhTm/pytest --tb=short`:
  **4,674 passed, 7 failed (19m28s)**. All seven independently reproduce on
  untouched HEAD: Stripe localhost socket creation, `Operation not permitted`,
  `test_workflows.py::test_deploy_checks_the_yearly_price_with_stripe` change0–5
  and `test_deploy_yearly_check_skips_without_a_price_and_fails_without_a_key`.
  That file is unchanged. Only publisher/helper, new tests/archive and this
  handoff changed; test logs and owned task205 scratch removed. Other markers
  and broader hash cleanup remain deferred. Review and the seven checks outside
  this sandbox remain; no network/browser/paid calls, commit, push or deploy.
  Shared Git metadata is read-only, so branch creation was unavailable.
- **Incremental validator maintenance (Codex task 222, 2026-10-16; uncommitted, OFF):**
  `retention.incremental_cache.enabled: false`; omitted/false retains bulk expiry
  and compaction. Enabled cleanup checkpoints at most 64 URL keys/transaction,
  256 examined/run (fresh/NULL rows count), with a 0.25 s cooperative budget and
  the existing monotonic deadline minus 90 s. Cursor/high-water, deletion-time
  expiry checks and durable run allowance commit together; replay retains the
  allowance and completed sweeps cannot wrap on the same key. Enabled fast
  runs skip VACUUM; hourly/manual compaction and all other prune predicates
  remain. Private aggregates only; encrypted restore/save scripts unchanged.
  Other table deletes/hourly VACUUM remain unsliced; no hard SQL/job deadline or
  remote recovery of unsaved paid calls is claimed. Manual prune honors the
  switch using existing bounded run bookkeeping. Offline 0/1,024/8,192-row
  fixtures, query plans/VM work, injected interruptions/deadlines, older-snapshot
  replay, protected facts/claims and enabled reruns pass: **114 preflight checks**.
  Untouched-HEAD comparisons match **146 public files across four access modes**
  and member payloads, retaining task-119/121 switches; no existing hash repins.
  Defaults, measured chunk CPU/elapsed and unsliced limits:
  [incremental-cache-maintenance.md](incremental-cache-maintenance.md).
  Restart recovery: initial focused `-q -n 1` checks passed (95); full suite
  is running with `-q -n 1 --basetemp=/tmp/task222-resume-full`.
  Review and measured live job-tail behavior precede activation. No production
  state/network/paid calls, commit, push or deployment; workflow tests untouched.
- **Price-free publish manifest (Codex task 223, 2026-10-16; uncommitted, OFF):**
  `publish.manifest_refresh.enabled: false` in code/config. On: one current
  `publish-manifest.json` contains only schema, exact public JSON SHA256/length,
  publish time and site version; emitted after final JSON consumers and before
  audit/sealing, with no-cache/noindex. Prices and member/API responses retain
  no-store. First load fetches the full public file; later existing triggers
  share one transport promise and may reuse only its validated page-memory
  snapshot. The 300 s wall-time backstop forces full fetches even behind stale
  manifests. Stream limits apply before JSON parsing without Content-Length;
  invalid/missing manifests/validators get one full fallback. Actual body hashes
  own race outcomes; older times and equal-time differing content are rejected.
  Without crypto, equal-time acceptance requires identical validated facts and every
  refresh still fetches in full. Auth/pagehide invalidate transport ownership;
  restored first-load/return requests, delayed member results and Saved merges
  cannot reclaim a newer snapshot. No durable price cache, history, Worker,
  tracking or schedule change. Task 202 remains a proposal in this checkout.
  All **16 disabled byte trees (584 public files)** and **16 exact member-upload
  bodies** independently match untouched HEAD, including Amazon show/hide/as-of;
  no existing byte pins changed. Enabled home markup/styles/image priorities
  also match disabled output after removing scripts. Own home cost: **+7,202 raw /
  +2,000 gzip bytes**. Actual phone geometry remains unmeasured.
  Offline 2,300-offer shipped fixture, five-run Node medians, gzip level 9:
  quiet refresh **2,325,165 → 176 raw / 36,926 → 158 gzip bytes**, one request
  either way; changed refresh **2,325,164 → 2,325,340 raw / 36,930 → 37,089 gzip**,
  **one → two round trips**. Process CPU quiet **39.685 → 0.839 ms**, changed
  **77.890 → 105.145 ms**; parse elapsed **9.302 → 0.014 / 10.426 → 13.002 ms**,
  plus **2.674 ms** changed-body hashing. Sampled heap increase quiet
  **6,484,240 → 32,448 bytes**, changed **18,506,200 → 18,505,040**; median
  post-GC retained deltas **312 → 1,784 / 624 → 704 bytes** (allocation noise,
  not a peak or production memory prediction). Fixture origin work is outside
  timing; network latency/headers are excluded and offers are repetitive.
  Changed publishes gain no claimed speedup. Reproduce via
  `tests/test_manifest_refresh.py` (Node completion watchdog guards async cases).
  **2,589 tests passed** across 71 affected files (`-q -n 1 -p no:cacheprovider`,
  own `/tmp/task223-*` basetemps; 22m47s), then **95 focused checks passed** after
  the final Saved guard, including enabled pipeline reruns in both audit modes
  and Saved/fallback/Back/clock harnesses (1m20s). The Saved regression fails
  against the prior generated page. The full suite was left to the operator.
  Review and separately authorized Pages
  validator/coalescing, actual transfer/freshness and phone verification remain.
  No live requests, activation, commit, push or deployment; workflows test file
  untouched.
- **Permanent event pages (Codex task 210, 2026-10-16; uncommitted, ON):**
  `/black-friday/` and `/cyber-monday/` reuse the hub layout/cards and current
  public facts. Stored event mentions qualify year-round; published picks join
  during the configured window and provide the brief's early/post-event fallback.
  Niche, locked, ended and price-unconfirmed offers are excluded; photos rank
  first, capped at 60. Ten visible offers gate indexing, sitemap and IndexNow.
  Home chips, hub related links and shared footers link both permanent pages;
  Amazon history hiding is inherited. Code defaults off; all **144 public files**
  across four disabled-output modes independently match untouched HEAD and their
  byte pins. Review fixed pending event URLs leaking into the final IndexNow
  manifest after a floor drop or disabling, with two regression checks. Updated
  file-count/navigation-size expectations; seven Stripe checks exercise the
  unchanged workflow step with a socket-free fake transport. **40 event checks**,
  both offline rerun modes and **45 byte/compatibility checks** pass. Required
  full suite (`-q -n 2 -p no:cacheprovider --basetemp=/tmp/task210-full`):
  **4,574 passed** (20m04s). Test logs and own scratch removed. No new production
  requests, images, scripts or LLM calls; no commit or push. Review/deployment remain.
- **Established look (Codex task 212, 2026-10-16; uncommitted):** audited all
  page families/templates and email builders; calmer empty/retry states,
  consistent How we verify links, email branding/grammar and readable dates.
  Every wording change and retained issue is listed in
  `docs/research/established-look-changes.md`. Prices, evidence, disclosures,
  checked-price chips and sign-up behavior unchanged. Added visible-copy/trust
  guards; refreshed intended byte pins. Three stale Node harness failures also
  reproduce on HEAD; harnesses now load the existing niche-order helper.
  Final full `-n 2` suite: **4,541 passed, 7 failed** (18m32s), including
  offline reruns; all seven Stripe workflow socket-permission failures reproduce
  on untouched HEAD (`test_workflows.py`, yearly change0–5 and no-price/no-key).
  **Zero new failures**; 84 byte/baseline checks and 829-file comparisons pass.
  Review and the seven checks outside this sandbox remain. Own test logs/temp
  removed; no LLM calls, outgoing email, commit or push.
- **Unique-ASIN rechecks (Codex task 221, 2026-10-16; uncommitted, OFF):**
  Validated `keepa.unique_asin_rechecks.enabled: false`; omitted/false preserves
  transport order, receipts, observations, verdicts and public/member output.
  Opt-in grouping happens before planner affordability and transport batch sizing,
  within existing compatible new/used, live/update, Buy Box and priority barriers.
  First-ASIN/consumer order stays stable; each offer keeps its own judgment,
  product copy, image, price correction and death state. Transport reservations
  and receipts remain once per call; costs split by ASIN then consumers. Shared
  observation identities dedupe private yield counts while each offer retains
  its own history/death clock. Only one consumer owns each whole-ASIN cost
  sample for scheduler learning; allocated consumer fractions cannot teach a
  cheaper scan or inflate sample support. Existing per-offer accuracy/cache
  dedupe stays.
  Private `unique_asin_rechecks` counts selected consumers, unique policy-group
  ASINs, avoided duplicate slots and actual product calls; no public fields.
  Pre-edit disabled transport/pipeline capture matches exactly. A fixture-date
  correction was independently checked against the original checkout sources:
  **177 nonempty public files across four access modes plus member payloads**
  match with the switch off/on; no existing byte pins changed. Offline fixture:
  **207 consumers / 103 ASINs: 3 → 2 requests, 207 → 103 mocked receipt tokens**,
  equal verdict coverage; all-unique 103-ASIN input remains two calls/103 tokens.
  These are synthetic receipts, not measured Keepa billing or live savings.
  All **50 feature checks pass**, covering failure/lost/429 receipts, mixed
  policies, reserves/used share, cost-sample support, cached clocks, two-reading
  deaths and both offline pipeline rerun access modes. Required full
  `.venv/bin/python -m pytest -q -n 2 -p no:cacheprovider
  --basetemp=/tmp/task221-suite --tb=line`: **4,583 passed, 7 failed** (21m38s).
  All seven are the known Stripe localhost-socket restrictions (`Operation not
  permitted`): yearly-price change0–5 and no-price/no-key in the untouched
  `tests/test_workflows.py`; the operator confirms they pass outside the sandbox.
  Review and actual request/token measurements remain before activation; switch
  stays OFF. Test logs and only this task's `/tmp/task221-*` scratch removed.
  No paid calls, network probe, commit, push or deployment.
- **Safety and real numbers (Codex task 121, 2026-10-16; uncommitted):** shared
  Pinterest rich-pin opt-out; phone audit blocks the hourly check's analytics/ad
  pattern, including Clarity. `?internal=1` remembers this browser, tags GA4 as
  internal and suppresses ads/Clarity; `?internal=0` clears it, preserving other
  URL parameters. Review 121b ships `amazon_history: hide`: suppresses Amazon
  charts/lowest summaries/watches and Amazon-only `/p/` pages, filters mixed
  histories, and keeps hearts/prices/as-of notes. `/p/` medians name the actual
  checks/window; "90-day median" now requires checks near the window start.
  Review fixes use `verify.is_amazon_deal`, including Amazon URL offers with
  other store names; mixed-store histories/lowest-seen exclude those offers by
  key. Saved entries carry the same Amazon boolean; Amazon history badges,
  headlines and member/digest alerts use plain verification wording. Amazon's
  checked chip has the full disclaimer in its title and hidden text; the footer
  sentence stays unchanged. Phone audit ignores only errors from its own
  aborted URLs and records other console errors with their URL.
  Operator decisions (2026-10-16): checkout-price labels ON; Pinterest 10/day;
  Facebook 1/day at 17 UTC; Instagram drafts only; product-page pilot capped at
  200 indexable, zero new admissions until score-ordered admission lands.
  Code defaults stay unchanged; legacy fixtures retain their former switches.
  Four-mode comparison with staged task 121: **140 show-mode files identical**
  with as-of off; as-of-on differences are only the requested chip disclaimer
  and derived versions. Refreshed published-byte pins; **49 preflight checks
  pass**, including seven Stripe workflow checks whose unmodified step now uses
  a socket-free fake transport. First full run: **4,526 passed, 7 failed**
  (17m02s); four photo pins and three legacy pace/preconnect assertions fixed.
  Final full suite running under `/tmp/task121c-final`.
  Staged task 121 preserved; no external requests/LLM calls, commit or push.
- **Home polish (Codex task 120, 2026-10-16; uncommitted, ON):** validated
  `publish.drop_clock` thresholds drive calm/warm/hot/final colors, labels,
  bounded pulses and a short burst; reduced motion disables animation and
  the timer always keeps `m:ss`. Brighter tagline/live dot, teal ticker,
  centered desktop tabs; the two retired filters and their URL/session state
  are gone (old links ignored). Exact support cost copy; support-ask unchanged.
  Four-mode comparison (184 files) found only intended home/meta/support
  changes; byte pins refreshed. Required full suite: **4,412 passed, 12 failed**
  (20m32s). Five affected hash/style checks are fixed and rerun: **5 passed**.
  The remaining seven Stripe socket-permission failures reproduce on untouched
  HEAD; those checks outside the sandbox and review remain. Test logs/scratch
  removed. No new requests, LLM calls, commit or push.
- **Product price answers (Codex task 118, 2026-10-15; merged, ON):**
  Same switch/gates; data-only heads, breadcrumbs, lifetime check facts,
  90-day median/deal statistics, buy answers and indexable-only related links.
  Adds grouped `/p/`, footer/hub discovery and a split sitemap under the
  existing `sitemap.xml` entry point; audit/site-check/Lighthouse follow it.
  Static reader: ~14 KB standard / 31 KB shipped; visible photo credits,
  AA contrast, 44px targets and bounded deal markup. No new pipeline requests
  or LLM calls. **99 focused checks pass** (28 new); four byte pins also pass
  on untouched HEAD. Required full suite: **4,386 passed, 7 failed** (28m13s).
  All seven reproduce on untouched HEAD: Stripe workflow local sockets are
  blocked (`Operation not permitted`); **no new failures**. Test logs/scratch
  removed. Reviewed 2026-10-15 (related() indexed, Get deal analytics kept on /p/); ships with the switch ON as in config.yaml.
- **Photos for every deal (Codex task 119, 2026-10-15; uncommitted, ON):**
  `publish.photoless_last: true` sinks photo-less cards within wall groups;
  confirmed prices stay first and search keeps its order. Omitted/false
  preserves all public bytes. Amazon link recovery reuses tracker decoders,
  with 60 existing deals/run and 10 additional HEAD-only short chains/run.
  `images.keepa_search_per_run: 10`, `keepa_search_per_day: 120`,
  `keepa_match_min: 0.85`: strict brand/title/runner-up/price checks, after
  rechecks and preserving Keepa reserves. Matches use private `photo_asin`
  plus `asin_source: title_match`, never price evidence, rechecks or links.
  Woot photos reuse the official feed; other stores use permitted sources
  or existing official APIs, with no retailer-page photo fetches. Watch
  `photo_coverage` (live public totals and six missing-photo causes),
  `photo_asins` and `photo_search`; live coverage remains unmeasured here.
  All **50 task checks**, including four complete disabled-output byte trees,
  pass. Required full suite: **4,399 passed, 19 failed** (27m07s); all 12
  task failures were corrected (legacy harnesses/pin, retailer expectations,
  no-fetcher guard). Final affected-module run: **284 passed**; Woot: **77 passed**.
  Only seven Stripe localhost-socket failures remain (`Operation not permitted`),
  all reproduced on untouched HEAD; run them outside this sandbox before merging.
  Review/live coverage remain. Test logs/scratch removed; no commit or push.
- **Task 119 review fixes (119b, 2026-10-16; uncommitted):** post-wide ASIN
  fallback now requires at most one distinct parsed product. Partial short-link
  resolution is discarded on quota/deadline cutoffs or remaining shorteners;
  deadline-deferred backfills stay retryable, including interrupted cached chains.
  Email posts/newsletter deals skip the new link fetches. Paid title search waits
  for `post_link_tried` (newsletter exception); both existing link resolvers can
  replace a title-match photo with a real ASIN, `asin_source: link`, cleared
  `photo_asin`/photo and reset image flags. Added regressions reproduced 15 failures
  before fixes; **189 focused checks pass**. Re-pinned all four task-119 disabled
  output trees against this worktree's HEAD: **141 public files, zero differences**.
  All **42 targeted byte-pin checks pass**. Required full `.venv/bin/python -m
  pytest -q -n 3 -p no:cacheprovider --basetemp=/tmp/task119b-full`:
  **4,443 passed, 7 failed** (23m31s). All seven are Stripe localhost-socket
  failures (`Operation not permitted`), independently reproduced on untouched
  HEAD: yearly-price change0–5 and no-price/no-key in `tests/test_workflows.py`.
  **Zero new failures.** Run those seven outside this sandbox before merging;
  review/live coverage remain. Test logs and all own `/tmp/task119b-*` scratch
  removed; staged task 119 preserved, no commit or push.
- **Permanent product price pages (Codex task 116, 2026-10-15; merged, OFF):**
  `seo.product_pages.enabled: false` in code and config. On: stable `/p/`
  pages reuse exact stored ASIN/GTIN/store IDs, public deal facts and our own
  checks only; paths and facts survive deal deletion. Six checks spanning
  seven days plus a published deal within 90 days gate indexing, capped at
  50 new/UTC day and 2,000 total; a 30-day hold prevents sparse-day churn.
  Includes the chart, code verdict, allowed photo, credited deal list,
  live-only Get deal/Offer, card/detail links and up to 12 history links per
  category hub. Sitemap, one-time receipt-backed IndexNow, schema audit and
  `product_pages` run counts are wired. Resumed after disk exhaustion; fixed
  history links on quarantined variants and floating-point verdict boundaries.
  **48 task checks pass**, including four independently verified untouched-HEAD
  byte trees and both offline pipeline rerun modes. Final required full suite:
  **4,236 passed, 26 failed** (21m19s); all 26 reproduce on untouched HEAD
  (18 old byte pins, one Join-copy assertion, seven Stripe socket-permission
  checks). **Zero new failures.** Temporary test logs/directories removed.
  No new request/LLM path, commit or push. Review and activation remain; switch OFF.
- **Tiered critic ON again (2026-10-15, operator: "you need to spend less
  per deal" under the $10/day hard cap):** `parse.critic_tiering.enabled:
  true` (Haiku first; Sonnet for kills, doubt and high prices). The first try
  (b20d1e1) was reverted because two fast-lane runs took 11+ min; since then
  task 109 bounds fast-lane parsing (150 s) and judging (deadline minus
  45 s). Test pins from b20d1e1 are restored (legacy pipeline fixtures keep it
  off; `tests/test_e2e_tiering.py` runs the shipped config). **Watch the next
  fast-lane runs:** pipeline "done in" must stay under ~480 s with deploy and
  IndexNow inside the 12-min job, and the unpublished tiered backlog small
  (`critic.tiering` in the run summary). If not, set `enabled: false`.
  Why it matters (2026-10-15 10:24 NY, ai_usage_day): Sonnet second
  opinions were $2.46 of $4.34 spent (48 calls) plus $0.84 Sonnet calls
  billed at worst case; Haiku extraction of 354 posts was $0.63. Tiering
  caps second opinions at 12/day and moves cold sources to Haiku, so the
  paced budget reaches more posts (hourly runs were aborting parse on the
  spend cap with ~415 posts held back, eDealinfo's 30 included).
- **Private accuracy scoreboard (Codex task 117, 2026-10-15; merged):**
  paid Keepa/Woot/Best Buy/eBay readings compare with a frozen public price,
  before corrections; private rows retain 35 days and dedupe run/cache repeats.
  The growth report opens with sourced growth totals; daily top30/top120/rest
  and overall accuracy include n, 95% Wilson ranges, misses and a seven-day line.
  Checkout-price kinds stay separate; empty samples say "not measured".
  `accuracy.extra_samples_per_day: 0` stays off; opt-in random Amazon checks
  preserve reserves, six-hour spacing, daily caps, publish time and first-429 stops;
  receipts and lost-response debits persist for the next run.
  **56 accuracy checks pass**. Full `.venv/bin/python -m pytest -q -n 3
  -p no:cacheprovider` (compact tracebacks): **4,244 passed, 26 failed** (22m16s).
  All 26 failures reproduce on untouched HEAD; **no new failures**. Independent
  renders match all **175 public files across four access modes** byte for byte.
  No LLM calls added. Test logs/scratch outputs removed; no commit or push.
  Review/deployment remain; live accuracy is not measured before publication.
- **Amazon as-of disclosure (Codex task 115, 2026-10-15; merged, ON):**
  `publish.amazon_as_of.enabled: true`; omitted/false preserves published
  bytes. Amazon cards add a title/hidden Eastern time from the existing
  confirmation clock; relative labels and their refreshers stay intact.
  Deal pages show the time and native Details with Amazon's exact disclaimer;
  every shared footer adds the approved sentence outside the refreshed
  disclosure span. **23 focused checks pass**, covering EDT/EST, missing
  clocks, other stores, four complete pre-change byte trees and offline reruns.
  Required full suite: **4,191 passed, 22 failed** (29m09s). One older
  timestamp assertion loaded before its update now passes in that final
  focused run. The other 21 reproduce on untouched HEAD: 14 old byte pins
  (checkout clarity/prices, critic, hub inventory, post-join ask, support)
  and seven Stripe workflow socket-permission failures. Those baseline
  repairs/checks outside the sandbox and review remain. No new requests/LLM
  calls, commit or push; Git metadata blocks branch creation.
- **Broad-appeal top picks (Codex task 114, 2026-10-15; merged):** operator:
  niche items (an OtterBox case for one phone) "should not be in the top
  25-30"; tilt slightly toward women shoppers. `publish.front_page.appeal`
  multiplies only `pick_score`: named-model device accessories, camera-system
  lenses/mounts, numbered-model replacement parts (everyday words like belts
  and rollers only with "replacement"/"compatible"; sizes/wattages are not
  models) and year/make/model vehicle parts get `niche_factor: 0.25`; they
  stay on the wall, hubs and search. Category factors: home/kitchen/beauty/
  fashion 1.15, toys_games/kids_baby/health/grocery 1.05, auto .85, others 1.
  Rules live in `rank.niche_reason` (when unsure, not niche); `enabled:
  false` restores prior scores and site bytes. Run summary `appeal` gives
  niche counts (wall, top 30) and three examples: watch it after deploys.
- **Checkout-price evidence (Codex task 113b, 2026-10-15; merged, OFF):**
  replaces `top_pick_max_age_h` with source/paid evidence. An active stated
  end (capped seven days from its statement), current-price repost within
  24 h, or paid page price unchanged from the first label reading within
  `keepa.pick_max_unconfirmed_h` supports picks. Expiry, existing source/gone
  verdicts and page-price movement in either direction beyond
  `keepa.reprice_min_pct` veto them. `max_silent_h: 72` caps observation
  silence; the brief's active-live-signal requirement also applies before
  that backstop (no extra grace after a signal expires). Existing wall/hide,
  labels and rolling-window rules stay. The first valid label reading and
  its observation time persist privately and are never overwritten; older
  responses cannot regress the latest reading. A private source-ended time
  survives generic Keepa confirmations; only a later backed live source
  post supersedes it. No new requests/LLM calls.
  Summary adds `skipped_expired`, `skipped_page_price_moved`, `skipped_silent`
  and `kept_by` counts for actually selected picks (live signals can overlap).
  Four stale task-113 byte pins also fail on untouched HEAD; recaptured its
  hashes and verify identical disabled output with private baselines present.
  All **71 checkout-price checks pass**, including signal precedence,
  paid/source verdict persistence, immutable baseline, four disabled byte
  trees and reruns. Full `.venv/bin/python -m pytest -q -n 3 -vv -p
  no:cacheprovider`: **4,209 passed, 17 failed** (27m30s). All failures are
  pre-existing: ten snapshot cases reproduced on untouched HEAD
  (`test_critic_tiering`, `test_checkout_clarity`, `test_hub_inventory`,
  three `test_post_join_support_ask`, four `test_support`), plus seven
  documented Stripe workflow localhost-socket failures (`Operation not
  permitted`, yearly-price change0–5 and no-price/no-key). Repair those
  unrelated pins and run the socket checks outside the sandbox before
  merging; review and operator-approved activation remain.
  `enabled: false` stays shipped. No commit or push.
- **Checkout-price deals (Codex task 113, 2026-10-15; merged, OFF):** Buy
  Box probe: all 18 top-pick price mismatches were deals whose price appears
  only at checkout (code, coupon, Subscribe & Save). Switch
  `publish.front_page.checkout_price_deals.enabled: false` (approval pending;
  task 113b above replaces the fixed age cut with evidence).
  On: evidence governs top-pick eligibility, and at most
  `top_pick_max_in_window` (1) of them sit in any
  `top_pick_window` (5) positions; skipped deals stay on the wall/search.
  Trending then keeps the published pick order (no browser reshuffle, so the
  window holds). Cards/deal pages say "Amazon shows $X until you enter the
  code" (or before the coupon / Subscribe & Save) only from a fresh paid
  reading above our price (`keepa.show_max_unconfirmed_h`); JSON adds only the
  number and kind. Watch `checkout_price_deals` in the run summary after
  turning it on (skips, picks, labelled, unlabelled_no_reading).
- **Buy Box top picks (Codex task 112, 2026-10-15; merged):**
  `keepa.buybox_top_n: 120` uses published wall order, with
  `buybox_interval_min: 60` between paid attempts (lost answers included).
  Plain new Amazon prices use qualified Buy Box totals, including shipping,
  only with a valid independent observation within 60 minutes and since the
  current price/confirmation. Corrections reuse evidence verification,
  `reprice_existing`, savings and price history; promo/used/missing/stale
  readings keep the existing path, and the outside-top rule stays shadow.
  `recheck_surplus: true` grants `min(predicted overflow, bucket - reserve)`:
  top Buy Box, used wall scans, then oldest live confirmations. The
  `recheck_surplus_reserve: 650` covers 140 discovery + 120 × 4 top tokens
  (620, rounded up); existing used-share, time and first-429 guards remain.
  `recheck_top_live_adaptive: true` sends update=0 with surplus, update=1
  otherwise; reserve 2 stats / 4 Buy Box tokens per live ASIN, degrading
  short Buy Box batches to stats-only. Existing missing-receipt estimates
  remain conservative. Summaries add `buybox_top`, `live_reads`,
  `tokens.overflow_estimate`, `surplus_granted` and category `surplus_spent`.
  Watch top and used-wall `within_1h`, stale/missing Buy Box counts inferred
  from requested/with_buybox, and unused tokens/overflow after deployment.
  Settings-off byte pins keep their hashes ("predates the Buy Box top
  picks" pins). Fast lane must stay well inside its 12-minute job.
- **Woot repricing (Codex task 110, 2026-10-15; merged):** single-price
  live feed offers correct the existing row through `reprice_existing` when
  drift exceeds max($0.01, 0.5%). Reuses Woot's mapper/ListPrice evidence and
  savings.py; keeps credits/critic, records price history, and labels the basis
  `List price on Woot`. **Both lower and higher prices correct, as Keepa does**;
  an unmappable higher price retains the two-reading suspect/dead path.
  Ranges never reprice and refresh `woot_listed_at` only within their endpoints
  (one-cent allowance); above-range prices age out. Promo/suspect correction
  guards and sold-out/end-date rules remain. `store_checks.woot` reports
  confirmed (listing refreshes, including corrections), repriced, range_skipped
  and above_range, using the existing requests only. Watch: live
  `store_checks.woot.repriced` > 0 once, then mostly `confirmed`.
- **Durable SEO pages (Codex task 111, 2026-10-15; merged):**
  `seo.deal_pages_indexable: false` keeps shopper pages, URLs, links and
  structured data; every deal page gets `noindex,follow` and leaves the
  sitemap. Omitted/true restores previous bytes. IndexNow receipts acknowledge
  each noindex transition once; failures retry, later churn/reminders/removal
  do not resend acknowledged deals while off. Other page rules stay intact;
  the HTML-based audit agrees. Old byte pins explicitly retain true.
  **216 focused checks pass**. Required `.venv/bin/python -m pytest -q -n 3
  -p no:cacheprovider`: **3,973 passed, 7 failed** (24m27s). All seven Stripe
  workflow localhost-socket failures reproduce on untouched HEAD (`Operation
  not permitted`); those checks outside the sandbox and review remain.
  In Search Console, watch indexed category/store/price hubs; **Excluded by
  noindex will rise by design**. No commit/push.
- **Fast-lane time budget (Codex task 109, 2026-10-15; merged with review fixes, tiering OFF):**
  `parse.fast_lane_parse_budget_s: 150` bounds parsing from its start, also
  respecting the existing tail reserve. Batches leave room for two request
  timeouts plus the 2 s transport backoff. Anthropic timeouts are not retried
  (second opinions unchanged); a failed read is still recorded as handled (as before), so a post that always times out cannot eat every run's budget. Other
  connection errors retain their retry and uncertain charges retain their
  full reserved cost. Fast-lane judging, Sonnet escalation/fallback/sampling
  and promoted-wall protection share the existing deadline minus 45 s, with
  the same request headroom; unfinished tiered deals remain pending/unpublished.
  `parse.critic_tiering.max_tokens_per_deal: 160` enlarges only enabled-tier
  model output; Sonnet and tiering-off requests retain their previous bytes.
  A repeated finished fast-lane key does not start fresh parsing/judging;
  deferred work resumes on the next key. Hourly scheduling stays unchanged.
  Fast checkout is depth 1; restore explicitly fetches only the current data
  snapshot for shallow checkouts. Full hourly checkout/restore is unchanged;
  encrypted restore/save/no-op works against a local shallow Git fixture.
  **151 focused checks pass**, including cached-read shadow safety and the
  tiering-off spend/verdict/site byte pin; the shallow-state guard also passes.
  The timing replay uses 150 new posts plus a 150-deal critic
  backlog, an eight-minute run budget, one extraction worker and one-deal
  critic batches: 6 s extracts, every third extract timing out after 20 s,
  3 s critic calls. It finishes within **450 fake seconds**, stops parse on
  its 150 s budget and critic on its deadline, keeps pending deals off the
  site, adds no rows/spend on the same key and resumes on the next key.
  Required `.venv/bin/python -m pytest -q -n 3 -p no:cacheprovider`:
  **3,924 passed, 7 failed** (15m47s). All seven fail on untouched HEAD too:
  Stripe localhost socket creation is blocked (`Operation not permitted`),
  `tests/test_workflows.py` yearly-price change0–5 and no-price/no-key.
  Those checks outside this sandbox and review remain before merging.
  **Before re-enabling tiering:** keep `enabled: false` through review, then
  check one controlled live tiering-on fast-lane run/replay before leaving
  the switch on: the pipeline's **"done in" must be under
  ~480 s**, with state commit, Pages deploy and IndexNow finishing inside
  the 12-minute job. The shipped 600 s overall budget and hourly behavior
  were not changed; the offline timing fixture is not a live-runtime claim.
  No commit/push; scratch logs are not part of the change.
- **Buy Box gap (Codex task 108, 2026-10-15; merged, SHADOW):**
  `price_source` in run summaries counts AMAZON/NEW confirmations (lowest
  slot, ties to AMAZON), Amazon prices above final × 1.10 + $0.01 and the
  five largest dollar gaps, before existing repricing. Its `buybox_rule`
  block counts currently public deals that `would_label`/`would_hide`.
  `keepa.buybox_rule: shadow` preserves all published bytes; flip to `on`
  for existing unconfirmed label/hide paths, or `off` to disable the rule
  and forecast. Measurement stays on; regular requests cost no extra tokens.
  Code/coupon/Subscribe & Save deals (verify._price_hidden_by_promo) are split
  out (`amazon_above_10pct_promo`, probe `above_10pct_promo`) and never
  unconfirmed by this rule: their price only shows at checkout. First live
  probe 2026-10-15 04:33Z: 18 of 30 top/wall deals above 10%, 12 within 2%,
  none missing; 90 tokens. Used/non-Amazon/death rules stay intact.
  Run **Keepa Buy Box probe (read-only)** (`keepa-buybox-probe.yml`, manual):
  read-only restored state, public picks then wall, ≤30 distinct ASINs, one
  request, ≤90 of the 100-token ceiling; no retries/redirects/state save.
  Read `tokensConsumed`/`tokensLeft`, nested within-2%/within-10%, above-10%,
  missing counts and ≤10 examples. Lower prices count as acceptable, with
  a one-cent allowance. [buybox=1](https://keepa.com/api-docs/product.html#buybox)
  exposes cached data, **not a fresh offer scan**; live measurements remain.
  **39 new / 119 final regression checks pass**, including four byte trees,
  mocked probe, stale-data guards and reruns. Required `.venv/bin/python -m
  pytest -q -n 3 -p no:cacheprovider`: **3,939 passed, 9 failed** (20m49s).
  Two fixtures loaded before correction now pass in that final regression;
  the remaining seven Stripe localhost-socket failures reproduce on untouched
  HEAD (`tests/test_workflows.py`, yearly-price change0–5 and no-price/no-key,
  `Operation not permitted`). Review, live probe and those checks outside the
  sandbox remain. No commit/push; shared Git metadata is read-only.
- **RSS feed trimmed (2026-10-15, operator chose "trim"):** `traffic.feed_max_items: 20`,
  `traffic.feed_details: false` — items keep name, price, condition, source credit and the
  link to our deal page; codes, steps and checkout notes stay on our pages. Byte pins set
  the old values ("predates the RSS trim"); their hashes changed only through
  traffic.site_version (the module's own bytes), verified by rerunning them on the old
  traffic.py.
- **Google Ads campaign builder (2026-10-15):** operator approved building the
  first campaign PAUSED. `ads-setup.yml` (manual; validate-only by default)
  creates `ads.setup` from config.yaml; see docs/ads-guard.md. The operator
  enables it in Google Ads, then says "ads live"; schedule day-3/day-7
  scorecards then (50% commission rule).
- **Tiered critic switched back OFF (2026-10-15 ~03:25Z):** with it ON the
  fast-lane pipeline step took 11+ minutes (was ~5.4) in two runs in a row
  (38672525750, 38674830502), so the 12-minute job limit cancelled the Pages
  deploy. Commit b20d1e1 was reverted (config + its test pins); the 106 code
  stays, switch OFF. Before switching on again: find where the time goes
  (likely high_visibility() / protect_promoted() recomputing rank over every
  alive deal, or many sequential Haiku+Sonnet calls) and prove a fast-lane
  replay stays well under 12 minutes. Original note follows.
- **Tiered critic ON (Codex task 107, 2026-10-15; merged after the Keepa volatility run checked out, then reverted):** shipped
  `parse.critic_tiering.enabled: true`, per operator 2026-10-14 "we can't
  afford Sonnet"; supersedes task 106's OFF note below. Legacy fixture/setup
  pins preserve every existing assertion and byte hash. Nine tiered sibling
  checks plus three shipped-config e2e checks cover Haiku publication, Sonnet
  kill review/reversal, lower spend, caps, sampling, reuse and rerun idempotency.
  Offline 362-public-deal comparison: **$0.23814 ON vs $0.25596 OFF**; live
  savings remain unmeasured. All **75 switch-related failures** pass on rerun.
  Full `.venv/bin/python -m pytest -q -n 3 -p no:cacheprovider`: **3,902 passed,
  7 failed** (18m01s). All seven Stripe localhost-socket failures reproduce on
  untouched HEAD (`Operation not permitted`):
  `tests/test_workflows.py::test_deploy_checks_the_yearly_price_with_stripe`
  (change0–change5), and
  `test_deploy_yearly_check_skips_without_a_price_and_fails_without_a_key`.
  Reviewed and merged 2026-10-15 after the first live Keepa volatility run
  (no out-of-time; top within 1h 0.975). With tiering on, rank requires a
  settled verdict for every regular-price deal; live backlog was ~6/run.
- **Woot coverage LIVE (2026-10-15):** probe run 38662421701 showed All caps at
  5,000 offers and every category feed adds new ones (Home 2305, Clearance
  1987, Tools 1131, Sports 1108, Electronics 372, Computers 343 new); page 2
  adds nothing. Shipped `woot.named_feeds` with those six and
  `named_feeds_rotation_h: 1`: each run reads All plus ONE category, rotating
  hourly, inside the 1,000-request/day key quota. First live run: 6,100 raw
  offers. Task 104 notes below are the original brief.
- **Woot coverage (Codex task 104, 2026-10-14; uncommitted, OFF):** manual
  `woot-probe.yml` reads only the official API (10 requests, hard ceiling 15,
  1/s, header-only key). Logs status/schema/paging/counts and overlap with All;
  no offer content. Electronics/Computers/Home/Tools/Sports/Clearance are explicitly
  **guesses**: no named feeds are documented in code/config. The live probe
  remains to run. If `page=2` returns new identities, raise `woot.feed_pages`
  from **1**; if named feeds add coverage, populate `woot.named_feeds` (now
  **[]**) with verified names. Other paging forms need adapter adjustment;
  a 200 with the same offers does not establish paging. Check the existing
  1,000-request/day quota before expanding. Combined feeds dedupe OfferId/URL,
  recheck before selection caps, stop on short/empty/repeated pages, and retain
  completed pages at the deadline. Absent offers never imply death.
  Per-offer lookup: **not available** (no documented endpoint in code/comments).
  **26 new offline checks pass**, including probe log scanning and identical
  default run summaries/all published bytes. Required `.venv/bin/python -m
  pytest -q -n 3 -p no:cacheprovider`: **3,807 passed, 7 failed** (14m01s).
  All seven Stripe localhost-socket failures reproduce on untouched HEAD:
  `test_workflows.py` yearly-price change0–5 and no-price/no-key, `Operation
  not permitted`. Those checks outside this sandbox, review and live probe
  remain. No commit/push; shared Git metadata is read-only.
- **eBay variations (Codex task 105, 2026-10-15; uncommitted):** a legacy
  lookup's 400 gets one paced, capped Browse group request. Valid in-stock
  fixed-price USD variations confirm the stored price or feed existing
  evidence-backed repricing; unavailable groups use the two-reading death
  path. Selected `?var=` links still skip; malformed groups have no opinion.
  Diagnostics allow at most three integer Browse/Access error IDs, and now
  cover malformed responses and exhausted run caps (previously silent).
  **61 new offline checks pass**; the requested full suite
  (`.venv/bin/python -m pytest -q -n 3 -p no:cacheprovider`) finished with
  **3,842 passed, 7 failed**. All seven are the documented Stripe workflow
  localhost-socket failures (`Operation not permitted`, yearly-price change0–5
  and no-price/no-key). Those checks outside the sandbox and review/live
  verification remain.
  Only store_prices.py, tests and this note changed; no commit or push.
- **AI under $6 (Codex task 106, 2026-10-15; uncommitted, OFF):**
  `parse.critic_tiering.enabled: false`. On: Haiku first; Sonnet confirms kills,
  invalid/low-confidence replies, ranked top/wall candidates (including later
  promotions), and prices >$250. Escalations fill existing 8-deal batches;
  capped judgments stay unpublished. Two Sonnet sample batches/day; >10%
  disagreement in the last 32 valid judgments (minimum 16) withdraws earlier
  Haiku approvals and persists Sonnet-for-all (`critic_tiering_fallback` meta;
  review before explicitly clearing). Second opinions use the same cached prompt:
  Sonnet above 10% backed-post/paid-call yield over seven days, minimum three
  calls, at most 12 calls/day including retries; otherwise Haiku. Existing
  caps/evidence/floors stay. Ledger kind/source/model/input/output/cache receipts
  feed UTC run/private technical reports; missing historical detail stays unknown.

  **Offline measurements, not live:** pre-change e2e: 8 calls, 12,000 tokens,
  $0.00729; OFF matches its 40 published files, verdicts and spend exactly.
  Day replay: 58 fast + 24 hourly runs, 240 parsed posts, 2,624 critic decisions,
  82 second opinions and 82 category calls. Extraction assumes 5,000 input/4,000
  output tokens (the brief's $0.025/10 posts); critic/category reuse the e2e's
  1,200/300 receipts. No cache hits assumed.

  | Kind | Calls before → tiered | Tokens before → tiered | USD before → tiered |
  |---|---:|---:|---:|
  | Parse | 240 → 240 | 2,160,000 → 2,160,000 | .60000 → .60000 |
  | Critic | 328 → 410 | 492,000 → 615,000 | 1.77120 → .53136 |
  | Second opinion | 82 → 82 | 738,000 → 738,000 | 4.10000 → .77500 |
  | Shadow | 12 → 2 | 18,000 → 3,000 | .00324 → .01080 |
  | Category | 82 → 82 | 123,000 → 123,000 | .02214 → .02214 |

  At 25% escalation: **$6.49658 → $1.93930/day** = 240×.0025
  + (328×.00027 + 82×.0054) + (12×.05 + 70×.0025) + 2×.0054 + 82×.00027.
  Escalating **every** critic decision: **$3.25690/day** (no ordinary approvals
  to sample), below $4.50 under the normal $6 cap/pacing. Reproduce with
  `tests/test_critic_tiering.py`. **21 new and 182 focused checks pass**.
  Full `.venv/bin/python -m pytest -q -n 3 -p no:cacheprovider`: **3,802 passed,
  7 failed**. All seven reproduce on untouched HEAD: Stripe localhost socket
  creation is blocked (`Operation not permitted`, `tests/test_workflows.py`,
  yearly-price change0–5 and no-price/no-key). Rerun those outside this sandbox
  before merging. Live volume/agreement and review remain; Git metadata is
  read-only, preventing branch creation. No commit/push.

- **Volatility batching (Codex task 101, 2026-10-14; uncommitted, OFF):**
  ranked selection fits tokens and remaining time before packing 20-offer /
  100-stats ASIN calls across labels; overdue top and discovery boundaries,
  reserves, used share, first-429 stop and honest clocks remain. Shared bounded
  hourly/fast transport receipts supply missing costs and p90 latency (+20%,
  5 s margin). The 80 s reproduction improves **14/85 → 85/85 used checks**
  in five offers calls. A 58-fast-run day at 60/min, 15–35 min gaps, 4–6 s
  offers / 1 s stats: **100% top and used-wall within 1 h**, before and after
  runs; max **41/80 s**, unused **73/hour (2.0%)**, versus legacy **942/hour**
  and 95.45% worst pre-run used coverage (post-run 100%). Fixture: 190 top,
  110 used wall, 980 other new, 13-token scans, fresh answers, no free matches.
  Cheaper 7-token scans / 85 used wall leave less eligible demand: **1,301/h**
  unused versus legacy **2,024/h**, both 100% coverage; no extra checks just
  to exhaust tokens. **14 new, 48 focused and 213 broader Keepa checks pass**
  (overlapping sets). Full `.venv/bin/python -m pytest -q`: **3,724 passed,
  8 failed, 6 deselected**. Seven are Stripe localhost-socket restrictions,
  all reproduced on untouched HEAD (`tests/test_workflows.py`). The eighth
  loaded the earlier low-demand fixture before its replacement; the final
  14-test run and a post-suite rerun of that case pass. Both capacity
  comparisons pass; the slow sweet-spot test stays marked slow.
  **Do not switch on yet:** review, the seven checks outside this sandbox,
  and live cached-clock/receipt validation remain. Task 102's store/
  source code untouched; no commit/push (shared Git metadata is read-only).
- **Non-Amazon freshness (Codex task 102, 2026-10-14; uncommitted):** shipped
  config enables 12 h partner rereads (20 posts/60 s per run), 24 h muted
  `Posted N days ago`/promotion exclusion, and 48 h hide-only limits with store
  overrides. Current-price evidence, source permissions/breakers/robots,
  conditional GETs and existing capped parsing govern refreshes; Slickdeals
  stays RSS-only, redirects/retailer pages are not fetched by this pass.
  Claude's review: rereads of the same post (changed bytes or 304) can end or
  reprice a deal but never extend its clock; eBay capped at 30 requests/run.
  eBay/Best Buy are due each run, with oldest-first rotation, persistent run/day
  caps and status-only token/HTTP diagnostics. Keepa scheduler, ads/Pinterest,
  stock rules and AI caps unchanged. Runtime/private before/after tables are
  wired; **live counts unavailable** (parent state has zero deals and its site
  file is an unrelated old fixture). Walmart adapter **not available**: the
  official index lists Product Lookup, but its current lookup/auth contract
  could not be retrieved; no guessed adapter or retailer fallback. Quota
  citations, setup and limits: [docs/non-amazon-freshness.md](non-amazon-freshness.md).
  Final regression run: **334 passed**, including all 43 freshness checks.
  Full `.venv/bin/python -m pytest -q`: **3,735 passed, 20 failed, 6 deselected**;
  all 13 implementation/fixture failures were corrected and pass in that final
  run. The remaining seven Stripe localhost-socket failures reproduce on
  untouched HEAD (`Operation not permitted`). Both 144-cycle wall simulations
  and the volatility simulation pass. Review, live measurements, the Walmart
  contract and socket checks outside this sandbox remain. No commit/push;
  shared Git metadata is read-only.

  Reproducible **offline fixture**, not live results:
  `test_non_amazon_freshness.py::test_documented_before_after_table`. Each store
  starts with five offers aged 6/18/36/60/120 h; a verified second source
  refreshes the 120 h offer, and the 60 h offer hides. All 25 stay alive.
  Bucket order: **<12 / 12–24 / 24–48 / 48–96 / >96 h**.

  | Store | Before | After refresh + hide | API-path / source-only, before → after |
  |---|---|---|---|
  | Walmart | 1 / 1 / 1 / 1 / 1 | 2 / 1 / 1 / 0 / 0 | 0 / 5 → 0 / 4 |
  | Target | 1 / 1 / 1 / 1 / 1 | 2 / 1 / 1 / 0 / 0 | 0 / 5 → 0 / 4 |
  | Best Buy | 1 / 1 / 1 / 1 / 1 | 2 / 1 / 1 / 0 / 0 | 5 / 0 → 4 / 0 |
  | eBay | 1 / 1 / 1 / 1 / 1 | 2 / 1 / 1 / 0 / 0 | 5 / 0 → 4 / 0 |
  | Macy's | 1 / 1 / 1 / 1 / 1 | 2 / 1 / 1 / 0 / 0 | 0 / 5 → 0 / 4 |

- **Pinterest growth (Codex task 098, 2026-10-14; uncommitted):** eight public
  category boards created lazily and cached per environment; catch-all stays.
  Factual brand/item/category copy, bounded alt text and final `#ad`; stock,
  image/link rules and dead/suspect/unconfirmed exclusions remain. Board streak
  cap 2 spans runs (waits without an eligible alternative; uncertain sends
  count). Pace stays **2/run, 20/UTC day**; dated ramp overrides remain off.
  Production analytics claims/checkpoints once per UTC day: **60 reads/90 s**
  by default, least recently checked pins first, prior 30 UTC days. READY
  counts upsert by environment/pin/day, retain 30 days, stop/back off on errors
  and honor Retry-After. Private report: coverage/partial totals, top boards/
  categories, outbound clicks/100 impressions, descriptive posting hours and
  category weights (**shadow only**). Legacy pins lacking an environment keep
  dedupe but skip analytics. [Pinterest's v5 contract](https://github.com/pinterest/api-description/blob/main/v5/openapi.yaml)
  disables sandbox analytics; bulk is beta, so these are per-pin reads.
  **42 new checks pass**, plus **231 focused checks** and **11 final guards**
  including offline reruns. Full suite: **3,640 passed, 7 failed, 1 deselected**;
  all seven Stripe localhost-socket failures reproduce on untouched HEAD
  (`Operation not permitted`). Five checks added after the full run started
  pass in the final 42-check run. Remaining: those seven checks outside this
  sandbox, review, reconnect and production board/count verification. No
  dependency, LLM call, scheduler/product/label change, live write, commit or
  push. Shared Git metadata blocks branch creation in this worktree.
- **Ads spend guard (Codex task 099, 2026-10-14; uncommitted):** authorization
  stays **$0**, hard ceiling **$10**. Pause-only guard runs before hourly intake
  and at :13/:43 in its own workflow, sharing the state lock. It checks account
  timezone spend, individual/combined budgets and 30 completed days of attributed
  GA4 outcomes; missing outcomes at the spending threshold pause conservatively.
  Click-table totals remain unattributed. Encrypted shadow/receipt records feed
  the private report; pauses and every unresolved API failure alert admin Telegram
  with manual steps. No enable/budget writes, email, LLM/Keepa or new dependency.
  **75 guard tests pass; 225 focused checks pass** (seven Stripe socket checks
  excluded). Full `.venv/bin/python -m pytest -q`: **3,646 passed, 9 failed,
  6 deselected**. All nine reproduce on untouched HEAD: seven Stripe localhost
  socket failures, the shipped product-shadow tree pin, and the 1,600-deal file
  count (1,772 vs 1,771). The 25 cases added after the full run started pass in
  the final 75-check run. Setup and timing/attribution limits: `docs/ads-guard.md`.
  Baseline test repairs, review/deployment and live OAuth/GA4/admin verification
  remain; polling cannot guarantee a real-time ceiling without Google-side budget
  protections. No live API action, commit or push.
- **Plain growth report (Codex task 100, 2026-10-14; uncommitted):** owner
  summary comes first in email and its own admin Telegram message; all prior
  details follow the skip-this heading, with missing-only rows listed once.
  Cloudflare totals never label people: its query includes robots but cannot
  identify a robot-only count, so the summary says robots are included.
  Source counts are labeled visits; GA4 sign-up actions supply both periods,
  never the all-time subscriber total. No day-before data exists in the current
  collector, so comparisons say "not connected yet"; incomplete combined ad
  spend does too. Collection, private delivery destinations and logs unchanged.
  **274 focused checks passed; final summary checks: 51 passed**, including
  the case added during the full run. Full `.venv/bin/python -m pytest -q`:
  **3,646 passed, 9 failed, 6 deselected**. All failures reproduce on untouched
  HEAD: the shipped tree hash in `test_product_shadow.py`, the 1,600-deal file
  count in `test_seo_deal_pages.py` (1,772 vs 1,771), and the seven Stripe
  localhost-socket checks in `test_workflows.py` (`Operation not permitted`).
  Review and those existing failures remain. No commit or push; shared Git
  metadata is read-only, preventing branch creation.
- **Also-at shadow + lowest seen (Codex task 096, 2026-10-14; uncommitted, OFF):**
  every publish records private, bounded per-run comparison samples/counts in
  encrypted-state meta and the private growth report. Shared publication
  guards reject pack/quantity, capacity, variant, condition/grade, model and
  extreme-price mismatches. The independent lowest-seen switch uses exact
  product/condition observations on 3+ UTC days within 30 days. Labeled,
  locked, ended and absent-page offers cannot contribute. Both switches stay
  off; all four disabled output trees match untouched HEAD with linked
  products and sufficient history. **45 new checks pass**, including private
  reporting, retention, idempotency and all four byte pins; existing product
  checks and both offline rerun modes pass; final product-page run: **62 passed**.
  Full `.venv/bin/python -m pytest -q`: **3,536 passed, 7 failed, 1 deselected**;
  only the existing Stripe localhost-socket checks failed (`Operation not
  permitted`), all reproduced on untouched HEAD. Seven cases added after the
  full run started pass in the final focused run. Readiness checklist/config lines:
  `docs/product-entities.md`. Real shadow sampling/review after deployment
  and the seven socket checks outside this sandbox remain. No scheduler,
  evidence gate, request, dependency, commit or push.
- **Click signal one-step setup (Codex task 097, 2026-10-14; uncommitted, OFF):**
  Worker deployment now finds/creates `wowpriced-clicks` only when enabled;
  an explicit `CLICK_SIGNAL_D1_ID` still wins. Missing-token and D1 permission
  errors are clear and keep API output private. The endpoint is prefilled
  with the existing subscribe Worker; both pipelines already use the same
  `CLICK_SIGNAL_TOKEN`. Operator/engineer activation steps are in
  `docs/first-party-clicks.md`. **51 focused checks passed**, including
  executable lookup/create/re-run/override/failure cases and byte-identical
  disabled output across four access modes. Full suite: **3,513 passed,
  7 failed, 1 deselected**; only the existing Stripe localhost-socket tests
  failed (`Operation not permitted`), all seven reproduced on untouched HEAD.
  Remaining: those checks outside this sandbox, review, then activation after
  the token exists and verification of the first ingestion summary. No commit,
  push or deployment; tasks 095/096 and the privacy contract are untouched.
- **Price not confirmed label (Codex task 094, 2026-10-14; uncommitted):**
  shipped switch admits otherwise publishable, non-suspect hidden prices under
  48 h with a muted AA label, after confirmed offers. No promotion/pushes or
  Product/Offer markup; confirmed offers alone meet SEO floors. Existing
  scheduler sets/lanes stay unchanged; private run/growth diagnostics include
  eligibility, shown/hidden new/used counts and named recovery reasons.
  **42 new tests pass; final focused run: 48 passed, 7 failed.** Full suite:
  **3,490 passed, 13 failed, 1 deselected**. Six legacy harness/fixture cases
  loaded before correction now pass; only seven existing Stripe localhost
  socket failures remain (`Operation not permitted`, all reproduced on HEAD).
  The two 144-run wall days, efficiency day and both offline idempotency modes
  pass. Disabled output matches untouched HEAD across all four access modes.
  Only the shipped PWA-off tree hash changed. The seven socket checks outside
  this sandbox and review/live-count observation remain.
  Details/live-count limitation: HANDOFF in
  `docs/codex-tasks/094-price-not-confirmed-label.md`. No commit/push;
  read-only shared Git metadata prevents branch creation.
- **Volatility tiers (Codex task 095, 2026-10-14; uncommitted):** supersedes
  interrupted 093. Retained its wall request-age measurements; replaced its
  idle scheduling with FAST/NORMAL/SLOW targets, a config-only plan-rate knob,
  observed receipt costs, SLOW-first capacity stretching, bounded opt-in click
  boosts, and per-tier request/Keepa-clock coverage. Overdue top deadlines,
  forecast top protection, discovery overflow, value-ranked bounded hidden
  recovery, exploration, reserves, used share, first-429 stop, honest clocks,
  death/evidence and social rules remain. Display code for 094 is untouched.
  The original visibility scheduler is available with `keepa.volatility.enabled:
  false`; legacy cadence fixtures explicitly select it. **150 tokens/min is the
  smallest tested rate meeting FAST top/wall ≤40 minutes and NORMAL top ≤1 hour**:
  all peak at 35 minutes; 100/min leaves FAST wall at 50 minutes. 250/min adds
  no freshness in this fixture. Config remains 20/min, which cannot meet the
  FAST targets at this workload. Full tier/visibility table and assumptions:
  [095 HANDOFF](codex-tasks/095-volatility-tiers.md#handoff--2026-10-14-working-tree-no-commit).
  Final **39 tier/093 checks**, **78 focused guards**, **71 legacy checks**, all
  five rate scenarios (100/min independently repeated), and the new 144-run
  NORMAL-top guard pass. The existing 144-run wall and irregular-gap regressions
  also pass. Full suite: **3,486 passed, 15 failed, 1 deselected**; eight failures
  were fixed/rechecked, and the remaining seven Stripe localhost-socket failures
  reproduce on untouched HEAD (`Operation not permitted`). Review, those seven
  tests outside the sandbox, and live receipt/coverage observation remain.
  No commit or push.
- **Top coverage regression (Codex task 092, 2026-10-14; uncommitted):**
  overdue top/wall checks now precede every lower class and bypass ordinary
  retry cooldowns; lost requests can retry next run, same-key retries cannot
  re-buy checks. Off-wall recovery caps, reserves, used share, first-429 stop,
  death/evidence clocks and social rules remain. Added private
  `confirmed_age.top_candidates.request_age`, including the final publish's
  candidate set. The 192-top/181-wall hourly → fast (+18 min) regression
  failed before: 63 inconclusive rows were excluded before deadline selection;
  after, all retry and two-hour top coverage is 100%. The cooldowns predate
  091; its larger early-check allowance exposes the older-cache path. Live
  aggregate logs cannot prove each ASIN's skip reason. All **16 new cases**
  pass; final guards: **18 passed**. Both 144-run wall days, the value/token
  comparison and irregular-gap targets pass. Full `.venv/bin/python -m
  pytest -q`: **3,448 passed, 11 failed, 1 deselected**. Four legacy
  expectations loaded before correction now pass (**26 review/price checks**);
  the final added transport case also passes. Only seven existing Stripe
  localhost-socket failures remain (`Operation not permitted`: yearly-price
  change0–5 and no-price/no-key in `tests/test_workflows.py`), all reproduced
  on untouched HEAD. Those seven checks outside this sandbox and review/live
  observation remain. `recheck_top_live` remains **off**: a 45-minute
  192-ASIN cadence would add ~256 tokens/hour; upgrading every 26.8-minute
  run would add ~430/hour and exceed the observed spare ~300/hour. Exact
  clock semantics, cost assumptions and transport sizing evidence:
  **HANDOFF** in `docs/codex-tasks/092-top-coverage-regression.md`.
  No commit/push; shared Git metadata blocks branch creation.
- **Real run gaps + hidden recovery (Codex task 091, 2026-10-14; uncommitted):**
  overflow discovery/idle checks share a bounded EMA of actual fast/hourly
  starts (12 gaps, alpha .3, +10 min slack, 45 min ceiling; 10 min floor).
  After due checks/discovery, pick-ranked hidden recovery allows 150 new
  stats tokens and 10 used offer scans/run, preserving the 300-token/deal
  reserves and used share. Free Warehouse matches avoid redundant scans;
  retry windows, observation clocks, hide/evidence rules and social stock
  gates stay intact. Summaries add before/after hidden reasons, recovery
  costs/estimates and paid recoveries. Same-key retries retain allowances
  and first-429 stops. Irregular 58-run day: unused refill **5,590 → 500**
  (1.74%); initial 263 hidden new recover within 15 min, ending hidden 0/0,
  top/new-wall 1h/2h coverage after each pass 100%. **All 36 new cases pass**.
  Full suite: **3,428 passed, 12 failed, 1 deselected**. Five legacy fixtures
  loaded before correction now pass (73 accuracy/recovery checks, 19 Keepa
  review checks, both 144-run wall days with recovery enabled). Final guard
  checks: **11 passed**. Only seven existing Stripe localhost-socket failures
  remain; all reproduce on untouched HEAD (`Operation not permitted`).
  Knobs, exact failure names, metric definitions and fixture
  limits: **HANDOFF** in `docs/codex-tasks/091-real-run-gaps-and-hidden-recovery.md`.
  Remaining: seven socket checks outside this sandbox, review and live
  observations. No commit/push; shared Git metadata is read-only, preventing
  branch creation.
- **First-party click signal (Codex task 090, 2026-10-14; uncommitted, OFF):**
  `first_party_clicks.enabled: false` and empty endpoint preserve every site
  byte and make no click requests. Optional passive Get deal beacons honor
  GPC/opt-out; the existing subscribe Worker aggregates only public deal keys
  per UTC hour in free D1, with atomic 1,000/hour default caps, anonymous
  abuse limits and no IP/cookie/raw-event storage. One token-protected read
  per run ingests absolute counts idempotently; missing collection hours stay
  unavailable. Publication-time proxies, documented internal features and
  top-120 shadow moves feed the private report; live ranking is unchanged.
  Untouched-HEAD comparisons match all files in four access modes. Worker
  checks: **74 passed**; final signal checks: **34 passed**; signal + offline
  rerun checks: **42 passed**. Full `.venv/bin/python -m pytest -q`: **3,395
  passed, 7 failed, 1 deselected**; only the Stripe localhost-socket tests
  failed (`Operation not permitted`), all reproduced on untouched HEAD. Six
  signal cases added after the full run began pass in the final focused run.
  Activation now needs only repository secret `CLICK_SIGNAL_TOKEN` (task 097);
  current setup, definitions/free-tier sources: `docs/first-party-clicks.md`.
  No dependency, model, paid service, email, headless browser, commit, push
  or deployment.
  Remaining: the seven socket checks outside this sandbox, review, and
  operator-approved activation followed by D1 quota/CPU and beacon observations. Git metadata
  is read-only in this worktree; changes are left for the owning reviewer.
- **Keepa/metrics/product merge (Codex task 087c, 2026-10-14; working tree only):**
  all five conflict files resolved, preserving the 087/087b and 088/089
  sections below. One `TokenUsage` tracks actual receipts and separate
  conservative estimated debits; product-batch allocations and overflow
  summaries read it, with no parallel receipt counters. Missing receipts
  leave actual efficiency ratios unavailable; 429s cost zero. The 087
  per-deal history owns `price_recheck.yield` for efficiency and private
  reports; photo-only calls are excluded, absent report periods stay
  unavailable, and stale/failed/unreadable used-offer prices cannot inflate
  useful counts. Due checks → bounded discovery → idle checks, honest
  observation times, reserves, first-429 stop and product attachment/backfill
  remain. Social stock rules are unchanged. Final focused validation:
  **167 passed**. Full `.venv/bin/python -m pytest -q`: **3,361 passed,
  7 failed, 1 deselected**; only the known Stripe socket checks failed with
  `Operation not permitted` (yearly-price change0–5 and no-price/no-key).
  Five merge-regression cases added after the full run started pass in the
  final focused run. Details: **087c HANDOFF** in
  `docs/codex-tasks/087c-merge-with-088-089.md`. The seven socket checks outside
  this sandbox, review/deployment and live yield observations remain.
  No commit or push. Git's index is outside this writable worktree, so its
  unmerged entries still need staging in the owning session after review.
- **Efficiency metrics (Codex task 088, 2026-10-14; uncommitted):** private
  UTC daily and per-run token/public-deal, useful-recheck, LLM/public-deal,
  avoided-critic and new/used shown-wall freshness metrics. Actual Keepa
  receipts exclude category/photo-only work; first-public events dedupe
  supply. Compact run-key rows retain retry costs and worst snapshots within
  existing retention. Missing history/receipts/clocks stay unavailable;
  partial coverage is labeled. Expired clicks stay unavailable because the
  existing GA4 data lacks deal/time attribution (`growth.expired_click_hours`).
  No new report requests, paid calls, dependencies, shopper button or email.
  Final focused validation: **80 passed**, including **27 new tests**.
  Full `.venv/bin/python -m pytest -q`: **3,200 passed, 1 deselected**;
  only the seven existing Stripe yearly-deploy checks in `test_workflows.py`
  failed at socket creation (`Operation not permitted`: change0–change5 and
  no-price/no-key). A green run outside this sandbox and live observations
  after deployment remain. Definitions are in the task's HANDOFF.
  Read-only Git metadata blocks branch creation; no commit or push.
- **Product entities (Codex task 089, 2026-10-14; uncommitted):** additive
  integer products, indexed exact keys and durable offer links expose internal
  `product_id` without changing public JSON, dedupe, ranking or social rules.
  Exact item/variant/condition guards deliberately leave uncertain matches
  separate; conflicting updates quarantine links, preserving original facts.
  Backfill is 200/run, resumable, transactional and repeat-run safe; existing
  observations/sightings provide product history even after deal pruning.
  `product_entities.also_at_enabled` defaults off; on compares only public
  live same-condition offers with emitted detail pages. Four disabled output
  trees match HEAD byte for byte. Full suite: **3,266 passed, 21 failed,
  1 deselected**; every failure reproduces on untouched HEAD: 14 social-stock
  fixtures (5 earning-links, 8 money-math, 1 quality-trio) expect sends without
  the required evidence, and 7 Stripe tests hit blocked localhost sockets.
  **Final 76 focused checks passed**, including both offline idempotency modes,
  backfill/identity guards and eBay/Woot re-listings. Synthetic 3,000-offer storage
  adds 112,675 gzip bytes; a 200-row backfill took 0.024 s. Model and limitations:
  `docs/product-entities.md`; byte hashes in the task brief. No requests,
  dependencies, commit or push. Read-only Git metadata blocks branch creation.
  Remaining: existing social-fixture repairs (stock rules stay), the seven
  socket checks outside this sandbox, and review. Comparison remains off.
- **Use Keepa overflow (Codex task 087b, 2026-10-14; working tree only):**
  due adaptive checks now precede up to eight extra Amazon/Warehouse /deal
  pages; remaining overflow buys value-ranked early checks. Existing post
  caps, page cursors, polite client, source deadline, token reserves, used
  share and first-429 stop remain. Extra posts enter the ordinary capped
  parser in the same run; the paid pass runs inside the Keepa pull and its
  summary is reused at the tail. New parsed rows wait for the next paid pass.
  Lost page responses retain a conservative debit; free feed confirmations
  cannot undo a just-corrected price or trigger a redundant idle check.
  Later photo backfill also retains a newer feed confirmation clock.
  Equal-time intake posts also retain that paid correction unless their
  actual observation is newer.
  A new four-hour new-wall cadence ceiling overrides stable lengthening;
  visible used deadlines also tighten to four hours. No new LLM call path,
  service, retailer fetch, arithmetic/evidence change or social behavior.
  The deterministic 144-run **085 → 087b** comparison gives **24,046 →
  20,755 recheck tokens**, **3,600 → 7,515 discovery tokens**, **1,440 →
  3,006 modeled new candidates**, **1,593 → 1,572 useful price readings**,
  **15.095 → 13.203 tokens/useful reading**, **594 → 85 unused refill**.
  Minimum top/new-wall 1/2-hour coverage stays **100%**; oldest visible
  wall price improves **4.17 → 3.67 h** without hiding any fixture rows.
  Discovery priority trades 21 useful rechecks for 1,566 more candidates;
  candidates are not claims of critic-approved publications. Full table,
  fixture assumptions and implementation notes: **087b HANDOFF** in
  `docs/codex-tasks/087-value-per-token-rechecks.md`. The comparison and both
  offline rerun-idempotency cases pass. Full `.venv/bin/python -m pytest -q`:
  **3,222 passed, 8 failed, 1 deselected**. The legacy six-hour fixture loaded
  adaptive defaults; it now explicitly selects fixed scheduling, and all
  **19 death-confirmation checks pass**. Final accuracy/feed/overflow checks:
  **100 passed**, including the photo-clock regression added after the full
  run started. Only the seven existing Stripe localhost-socket failures
  remain in `tests/test_workflows.py` (`test_deploy_checks_the_yearly_price_with_stripe`,
  change0–change5, and the no-price/no-key case): `Operation not permitted`.
  Run those outside the sandbox before merging. Review/deployment and live
  yield remain outstanding. No commit or push; branch creation is unavailable
  because Git metadata is read-only in this sandbox.
- **Value per Keepa token (Codex task 087, 2026-10-14; working tree only):**
  bounded 12-sample histories and daily class totals feed `price_recheck.yield`
  and the private growth report; repeated cached/known drift cannot inflate
  useful readings, and estimated charges are explicit. Adaptive cadence uses
  observed volatility and saved pick/wall position, with 10% oldest-visible
  exploration and age deadlines; 085 class order/budget gates remain. Paid
  clocks now use Keepa's own observation time, including reprices and the
  two-reading death interval. No new services, dependencies, LLM calls,
  retailer requests or price/evidence changes. The 144-run fixture improves
  **15.095 → 7.273 tokens/useful reading (51.8%)**, **24,046 → 10,393 recheck
  tokens**, **1,593 → 1,429 useful readings**; both runs keep top ≤1 h and
  wall-new ≤2 h at **100%**, all shown ages within limits (oldest wall
  **4.17 → 10.33 h**). Unused refill grows **594 → 14,102** in the fixture;
  saved capacity is available to existing discovery/photo lanes. Method and
  full numbers: `docs/codex-tasks/087-value-per-token-rechecks.md`.
  **29 task-specific checks pass**, including the day comparison; both
  offline rerun-idempotency cases pass. Full `.venv/bin/python -m pytest -q`:
  **3,196 passed, 9 failed, 1 deselected**. Preserved the existing zero-cadence
  switch and corrected a counter assertion for a skipped same-time retry;
  rerunning all nine failures gives **2 passed, 7 failed**. Only the existing
  Stripe localhost-socket failures remain in `tests/test_workflows.py`
  (`test_deploy_checks_the_yearly_price_with_stripe`, change0–change5, and
  `test_deploy_yearly_check_skips_without_a_price_and_fails_without_a_key`):
  `Operation not permitted`. Run those outside the sandbox before merging.
  The final focused accuracy/adaptive run also passed **35 checks**. No commit
  or push. Live yield/coverage after review and deployment remain unmeasured.
- **Wall confirmation coverage (Codex task 085, 2026-10-14; uncommitted):**
  saved wall keys round-trip correctly; top classification precedes wall,
  and the old aggregate mixed 2-hour new checks with 24-hour used checks.
  New cadences: top 45 min, wall 90 min, rest new 4/8 h, visible used 6 h;
  both conditions hide alive after 12 h. All new classes now precede used
  suspects/scans; off-wall used offers use free Warehouse confirmations only.
  Added condition-specific 1/2/3/12-hour coverage, overlap/cadence/retry
  counts, bucket start/end and predicted hourly overflow. The bucket now
  caps the *total* at an hour of refill (1,200 at 20/min); lost product
  responses retain a conservative debit. Predicted overflow buys older
  visible confirmations, top first, within the existing used share/reserve.
  No price/evidence rules, LLM calls, retailer requests or `history=0` changed.
  Regression demonstrated against HEAD: an 8-hour used wall price was
  planned/done 0/0; the new fixture requires 1/1. **15 new tests + 1 config
  check passed**, as did both offline idempotency cases. Two 144-run days
  (20/min refill, real scheduler, 13-token used scans, changing wall membership)
  kept top/new-wall coverage at **100% within 1/2 h** on every run. After
  recovery the oldest wall price was **2.5 h / 0.83 h** without/with free
  matches; hidden new ended at zero. Total tokens including feed pages:
  **27,365 / 28,185**, measured lost refill **879 / 65**, final buckets
  **1,006 / 1,000**. The free case deliberately supplies only ~195 new/58
  used matches per hour, much less than the supplied per-run live counts.
  Full `.venv/bin/python -m pytest -q`: **3,169 passed, 9 failed,
  1 deselected**. Two failures were test-only HTTP parameter coercion
  assertions already loaded by that run; corrected to accept `history=0`
  as the transport's string and both passed in the final 16-check run.
  Rerunning all nine failures yielded **2 passed, 7 failed**.
  The remaining seven are the existing Stripe localhost-socket failures
  (`Operation not permitted`) in `tests/test_workflows.py`: yearly-price
  change0–change5 and the no-price/no-key case. A full green run outside
  this sandbox and actual deployed coverage measurements remain.
  Branch creation is blocked by read-only Git metadata; no commit or push.

  Expected token plan (20/min = **1,200/hour**), deliberately before free
  confirmations. The live aggregate does **not** disclose condition counts:
  the reproducible 185-Amazon wall fixture assumes 100 new + 85 used; its
  new rows overlap top. Validate this mix with `wall_schedule` after deploy.

  | Class | Tokens/hour | Derivation / scope |
  |---|---:|---|
  | Deal lane | 600 | Ceiling for ordinary pages: (6 Amazon + 12 Warehouse + 2 focus) × 5 × 6 runs; short pages/caps/reserve usually reduce it |
  | Top | 252 | 189 live candidates / 0.75 h × 1; cached/free matches reduce it |
  | Wall new | 67 | 100 / 1.5 h, rounded up; conservative double-count of the fixture's top overlap |
  | Rest new | 16 | 62 / 4 h, rounded up; older rows use 8 h (8 tokens/hour) |
  | Visible used scans | 185 | 85 / 6 h × 13, rounded up; 535 off-wall used scans cost zero |
  | Photos | 0 | Expected steady state: reuse photos from those paid records; photo-only backfill uses the remaining bucket last |
  | **Total** | **1,120** | **80/hour headroom**, plus overlap/free-feed savings |

  This is a conditional workload estimate, not six independent allowances.
  Sweep/finder/category calls, a photo backlog, new arrivals and initial
  hidden-new recovery share that headroom and the same bucket/reserves.
  At the configured 200-top cap the estimate is 1,135/hour. A materially
  larger used wall or sustained API failures needs fresh live measurements;
  stale prices hide rather than claiming a confirmation. Removing the old
  535/24 h × 13 rest-used queue saves about 290 tokens/hour before feeds.
- **Hub inventory + product families (Codex task 086, 2026-10-14; uncommitted):**
  `traffic.hub_pagination_enabled` and `product_families_enabled` enable
  crawlable 60-offer hub pages and exact stored-identity `/products/` pages.
  Families require three distinct public live listings by default; repeated
  sources/promos do not inflate the floor. Member pages link to their family;
  all pages reuse wall cards, visible-offer floors and fact-based lastmod.
  Stale pages/links disappear on republish. GA4-only navigation counts join
  Product; Search Console includes paginated hubs and `product_family` rows.
  Both switches off restore every prior byte, including existing task 083
  features. **22 new pytest cases**, including **4 Node cases**, pass. Full
  suite: **3,180 passed, 8 failed, 1 deselected**; the old end-to-end family
  allow-list was fixed and reran **1 passed**. Only seven existing Stripe
  workflow socket failures remain (`Operation not permitted`, change0–5 and
  no-price/no-key). Remaining: those checks outside the sandbox and manual
  360–390px visual QA. No headless browser, external call, dependency, commit
  or push. Byte pins and details: `docs/codex-tasks/086-hub-pagination-product-families.md`.
- **Free traffic levers (Codex task 083, 2026-10-14; working tree only):**
  ranked ten missing opportunities and built the first three: public-live
  RSS with stable deal GUIDs, detail-page native/copy/manual sharing with
  fixed UTMs and text previews, and contextual matching store/smallest-budget
  navigation to emitted hubs with another live offer. `traffic:` enables
  them; omitted switches preserve existing bytes. All five action/arrival
  counts join the private Product report through the existing GA4 query;
  absent samples stay unavailable. No new external publish requests, paid
  services, LLM/Keepa calls, images, automated posts or email. Card markup
  and deal payloads remain unchanged; scoped controls wrap with 44px targets.
  Focused checks: **153 passed**, including **10 new pytest cases** and a
  **7-case Node harness**. Only the shipped PWA-off tree pin changed; all
  nine requested pins/measurement definitions are in the task brief.
  Full `.venv/bin/python -m pytest -q`: **2,976 passed, 1 deselected**;
  only seven existing Stripe yearly-deploy checks in `tests/test_workflows.py`
  failed at socket creation (`Operation not permitted`: change0–change5 and
  the no-price/no-key case). The brief forbids headless browsers, so actual
  360/390px visual/tap QA remains unmeasured. Git metadata is read-only;
  no branch creation, commit or push. Remaining: those seven checks outside
  the sandbox, review, manual phone QA and observed traffic after deployment.
- **Best Buy/eBay live prices (Codex task 084, 2026-10-14; uncommitted):**
  official Products/Browse clients share polite pacing and bounded backoff;
  OAuth stays in memory, request caps include retries/token calls, and API
  content never reaches state. Suspects/top candidates/wall/rest scheduling,
  evidence-verified same-row repricing through savings.py, one-hour agreeing
  deaths, `store_checks` and existing public check clocks are wired into both
  runs. Missing regular prices clear old discount claims; source credits,
  critic flags and EPN links remain. Both flags default off and missing secrets
  skip safely. Activation instructions are at the end of
  `docs/codex-tasks/084-bestbuy-ebay-live-prices.md`. Offline fixtures cover
  successful checks, failures, caps, cadences, evidence, timestamps and reruns.
  All **114 new store tests passed**. Full `.venv/bin/python -m pytest -q`:
  **3,076 passed, 9 failed, 1 deselected**. Two workflow secret-allowlist
  assertions were updated for the new env names and their rerun is **2 passed**.
  Only the seven existing Stripe yearly-deploy socket failures remain:
  `test_deploy_checks_the_yearly_price_with_stripe` (change0–5) and
  `test_deploy_yearly_check_skips_without_a_price_and_fails_without_a_key`
  in `tests/test_workflows.py` (`Operation not permitted`). A green full run
  outside the sandbox and live credential/API verification after the keys
  arrive remain. Git branch creation is blocked by read-only metadata;
  no commit or push.
- **Legitimacy audit (Codex task 082, 2026-10-14; uncommitted):** stored
  Amazon references/history can verify; unit/pack mismatches and an inflated
  claim above a stored reference reject; ambiguous claims keep the critic.
  `audit.mode: shadow` retains paid judging and records deduped agreement by
  reason. Enforce gates every store on current code support or a critic
  verdict. No new Keepa calls or budget/model changes. Private daily UTC
  counts and per-run actual/potential call/token savings estimates added;
  existing verdict reuse is excluded. Controlled fixture: 3 → 1 critic calls;
  live savings unknown. **65 audit tests and 112 focused checks passed**;
  final full suite: **3,030 passed, 1 deselected**; only seven existing
  Stripe checks in `tests/test_workflows.py` failed at local socket creation
  (`Operation not permitted`: yearly-price change0–5 and no-price/no-key).
  Run those outside the sandbox. Rules and shadow watchpoints:
  `docs/codex-tasks/082-legitimacy-audit.md`.
  No commit or push; review shadow evidence before enabling enforcement.
- **Search precision (Codex task 079, 2026-10-14; working tree only):**
  digit-bearing identifiers/sizes match whole tokens; ordinary words of 5+
  letters allow one edit. Current public inventory drives excluding-filter
  resets and up to six counted store/department choices, with wrapping 44px
  controls and focus returned to results. GA4-only apply/zero/recovery counts
  carry bounded methods only; missing samples stay unavailable. Node checks
  include 5,000-deal timings; all nine requested byte pins are listed in the
  task brief. Focused checks: **203 passed**, plus **4 legacy harness checks**.
  Full `.venv/bin/python -m pytest -q`: **2,835 passed, 8 failed, 1 deselected**;
  one stale engagement stub is fixed and its focused rerun passes. Only the
  seven existing Stripe yearly-deploy socket failures in `test_workflows.py`
  remain (change0–change5 and no-price/no-key); run those outside the sandbox.
  Chromium launch and branch creation are sandbox-blocked; actual 360px
  visual/tap/focus QA remains. No commit or push.
- **Checkout clarity (Codex task 078, 2026-10-14; working tree only):** audited
  flags/conditions/codes/steps/rebates/rewards/shipping and both renderers. Found
  four-step/90-character/three-code truncation, conditions lost behind steps,
  missing Prime/membership/shipping/reward terms and misleading rebate copy.
  Cards reuse the how box with a visible requirements summary and native expansion;
  deal pages show all terms. Checkout labels use savings.py's price and shipping
  knowledge; later rewards never lower it, and range/unit prices are qualified.
  Codes/rewards now need verbatim evidence; checkout conditions need supporting
  wording. Generic membership preserves its stated name in verified steps.
  Public-live `checkout.known_shipping_share`/`requirement_coverage` plus counts
  join run summaries; GA4-only requirement opens and outbound session rates join
  private Product. Rates need `requirements_deal_click` registered as a GA4 key
  event; absent data stays unavailable. Existing link routing stays intact.
  Snapshot cases: four support-off home modes, four PWA-off trees (including the
  restored-support check), and the no-link post-join tree's three variants.
  Full `.venv/bin/python -m pytest -q`: **2,883 passed, 1 deselected**; only seven
  existing Stripe socket checks in `tests/test_workflows.py` fail (`Operation
  not permitted`): `test_deploy_checks_the_yearly_price_with_stripe` (change0–5)
  and `test_deploy_yearly_check_skips_without_a_price_and_fails_without_a_key`.
  Latest focused checkout/link checks: **50 passed**. Plain static card markup
  matches its pre-change hash. Chromium fails at launch with `Operation not
  permitted`: 360/390px and desktop layout/focus checks remain unmeasured.
  Git metadata is read-only; no branch creation, commit or push. Full audit and
  metric definitions are in `docs/codex-tasks/078-checkout-clarity.md`.
- **Free Keepa confirmations (Codex task 081, 2026-10-14; uncommitted):**
  every fetched deal/sweep row can refresh an existing exact ASIN/condition/
  price match at its own data time, before post/hash caps; new cadence uses
  the latest confirmation, used cadence accepts only an exact Warehouse
  stamp or paid scan. Promo-hidden drift is separate. New classes precede
  ordinary used scans; all used scans share 35% of the pass's spendable
  bucket, preserving the 10-token deal reserve; a first 429 ends the pass.
  Summary: `feed_confirmed.{new,used}`, `drift.promo_hidden`,
  `used_skipped_budget`. Full suite: **2,872 passed, 1 deselected**; seven
  existing Stripe yearly-deploy checks in `tests/test_workflows.py` fail at
  socket creation (`Operation not permitted`; change0–change5 and the
  no-price/no-key case). Final task-specific checks: **40 passed**.
  Token estimates and remaining live validation are in
  `docs/codex-tasks/081-free-confirmations.md`. No commit or push.
- **Discreet public copy (Codex task 080, 2026-10-14; working tree only):**
  careers, information pages, privacy, disclosures, shared sign-up/footer copy,
  support and category introductions now use concise public wording. Removed
  internal names, team details and check schedules; retained partner credits,
  price facts, payment terms and sign-up behavior. Every category tab uses a
  circle with the existing active color. Added template/rendered-page guards
  across all access modes and PWA states; refreshed the nine legitimate pins
  in `test_pwa.py`, `test_support.py`, `test_post_join_support_ask.py`.
  Page-by-page before/after and hash list are in the task brief. New copy checks:
  **38 passed**. Full `.venv/bin/python -m pytest -q`: **2,873 passed, 1
  deselected**; only the seven known Stripe yearly-deploy checks in
  `tests/test_workflows.py` failed at local socket creation (`Operation not
  permitted`, change0–change5 and no-price/no-key). Chromium launch is also
  sandbox-blocked. Remaining: those seven checks and 360px visual/overflow QA
  outside this environment, then review. No commit or push.
- **Phone taps (Codex task 076, 2026-10-13; uncommitted):** shared invisible
  44px extensions, retained title clamps, neighbor guards and a probe-based
  audit (8 Node math checks). Only four support-off home/four PWA trees and
  the no-link support hash refreshed; PWA off compares its initial render.
  HTML/JS outside CSS/version unchanged. Pytest: **2,789 passed, 1 deselected**;
  only 7 known Stripe yearly-deploy tests fail on sandbox socket creation.
  Chromium EPERM leaves first-card before/after and visual/collision QA
  unmeasured; crowded credits may remain under 44px and are reported. No push.
- **Price accuracy (Codex task 077, 2026-10-13; working tree only):** paid
  Keepa product records now correct plain new Amazon prices in the same row
  through evidence verification and savings.py (both Amazon averages required).
  Existing critic verdicts/source credits stay; a correction adds no LLM call
  or Keepa request and keeps the photo harvested from the same paid record.
  Scored candidates (200) and the home wall (300) lead after suspects; new wall
  cadence 2 h, used cadence unchanged. Picks require a confirmation within 3 h;
  new/used prices older than 48/72 h are hidden until confirmed, never killed.
  Run summaries include drift, planned/done classes, hidden counts and the
  current publish's confirmation-age shares. Muted card clocks replace age;
  deal pages show ET. Requested PWA/support/post-join snapshots regenerated.
  Focused accuracy checks: **37 passed**, including the Node timestamp harness.
  Final full suite: **2,825 passed, 1 deselected**, only the seven existing
  Stripe yearly-deploy checks failed at socket creation (`Operation not permitted`):
  `test_deploy_checks_the_yearly_price_with_stripe` (change0–change5) and
  `test_deploy_yearly_check_skips_without_a_price_and_fails_without_a_key` in
  `tests/test_workflows.py`. A green full run needs those checks outside this
  sandbox. Token estimates are in `docs/codex-tasks/077-price-accuracy.md`.
  Git metadata is read-only, so
  branch creation is unavailable here. No commit or push.
- **Post-join support ask (Codex task 075, 2026-10-13):** card, `/join` and
  footer successes offer the existing Stripe choices/terms once; Not now/Escape/
  close return focus, remembered joins/supporters skip it, blocked storage allows
  one/page, and late responses keep the ask open. Disabled/no-link bytes preserved;
  three GA4-only actions feed private Product. Full suite: **2,747 passed, 1
  deselected**; only seven known socket-blocked Stripe yearly-deploy cases in
  `tests/test_workflows.py` (change0–change5 and no-price/no-key). Chromium
  `Operation not permitted` leaves 360px visual QA pending. Shipped after review.
- **Intake weights (Codex task 073, 2026-10-13; shadow, shipped after review):**
  baseline fast rotation/hourly slow-first + post round-robin retained in
  shadow; 90s/source, 400 posts, fetch/parse/tail reserves and paced AI/
  critic/Keepa caps unchanged. Seven-day
  deduped useful/USD weights add floors, exploration, six-hour fetch/post-read
  priority and source/store ceilings; forecasts and requested `intake.*`
  coverage plus private report (24h/20-post minimum). Shared AI costs and
  unparsed store mixes are estimates; structured stores have a hard ceiling.
  Small necessary fix in `ai_yield.record_public`: credit backed sightings
  instead of empty source names. Full pytest: **2,683 passed, 1 deselected**;
  only seven existing Stripe yearly-deploy socket checks in `test_workflows.py`
  failed (`Operation not permitted`). Live yield, those checks outside the
  sandbox and Claude review remain; 070/071 untouched. Shipped after review.
- **IndexNow receipts (Codex task 072, 2026-10-13; shipped after review):** found
  early "sent" stamps even on deploy/POST failure (retries delayed a week),
  no persisted receipt/202 key state, no batch cap, and lost ended/removal
  URLs. SQLite now separates pending versions from exact 200/202 receipts,
  with run/final-file/deploy gates, durable claims, 10,000/run cap, bounded
  15–360 min endpoint/URL retry and polite key rechecks. Both workflows save
  outcomes; five `indexnow.*` counts appear in run/private growth reports
  (receipt does not promise indexing). `seo.py` only: moved IndexNow
  helpers/imports, passed the output folder, added retained ended pages to
  discovery; sitemap/audit and task 071 UI untouched. Full
  `.venv/bin/python -m pytest -q`: **2,700 passed, 1 deselected**; only seven
  existing socket-creation failures (`Operation not permitted`) in
  `tests/test_workflows.py::test_deploy_checks_the_yearly_price_with_stripe`
  (six `change0`–`change5` cases) and
  `test_deploy_yearly_check_skips_without_a_price_and_fails_without_a_key`.
  Remaining: those checks outside this sandbox and live deploy/key/200–202
  verification. Shipped after review.
- **SEO audit (Codex task 070, 2026-10-13; shipped after review):** final HTML is
  audited on every publish; current visible offers gate indexing. Compact
  fact fingerprints (35-day absent-URL retention, 50k cap) stabilize lastmod;
  visible USD/condition/live-offer facts match Product/Offer. Private Search
  Console family/device rows paginate and keep missing samples unavailable.
  Clean fixture: zero issues, sitemap 35/35; fault fixture: one thin page,
  one schema mismatch, one broken link. Full suite: 2,650 passed, 1 deselected;
  seven existing Stripe socket tests blocked. Normal home bytes match all
  four original modes; Chromium blocks 360px/390x844 QA and first-card
  measurement. Live Search Console sample remains. Shipped after review.
- **Disclaimer (Codex task 074, 2026-10-13):** full `/disclosure.html`, config-only
  programs, shared footer/short-label links, sitemap and site-check; 360px
  structural checks pass. Only support-index/PWA tree hashes regenerated.
  Suite: **2,660 passed, 1 deselected**; seven known Stripe yearly-deploy
  tests blocked by local socket permissions. Remaining: those tests outside
  the sandbox, Claude review. No commit or push.
- **Price watches (Codex task 071, 2026-10-13; shipped after review):** Saved v2
  records published save-time prices/conditions and optional targets; exact
  public matches add a badge, drop-first ordering, was/now text and per-price
  notice deduplication. GA4-only actions feed Product counts; privacy updated.
  V1 baselines stay unknown; all four disabled modes match original bytes.
  Full suite: 2,636 passed, 1 deselected; only seven existing socket failures
  in `tests/test_workflows.py`: `test_deploy_checks_the_yearly_price_with_stripe`
  (change0–change5) and `test_deploy_yearly_check_skips_without_a_price_and_fails_without_a_key`.
  Two Saved-on PWA snapshots updated. Chromium EPERM leaves 360px QA and
  390x844 first-card top before/after unavailable; live GA4 counts await deployment.
  No commit or push.
- **Phone filters + Back state (Codex task 065, 2026-10-13; shipped after review):**
  configured `seo.price_pages` presets/custom ceiling, New condition and
  No extra steps; native phone dialog with 44px controls/16px inputs and
  inline desktop controls, disabled in Saved. Fixed store/category/sort/
  preset/eligibility selections share a URL (Amazon route retained);
  query, custom ceiling, min%, shown count, seed, Saved view and visible-card
  anchor stay in guarded tab session state. Back/Forward/reload/bfcache use
  current facts and nearby anchors when an offer disappears or no longer
  qualifies. GA4-only bounded-method `filter_apply`, `filter_empty` and
  `back_restore` counts join Product; device session rates use GA4's
  `sessionKeyEventRate:deal_click` (requires `deal_click` registered as a key
  event; unavailable data is reported without inference). Card title links
  and outbound counting (068) unchanged. Full suite: **2,554 passed, 1
  deselected**; only seven existing Stripe yearly-deploy tests failed at
  sandbox socket creation (`Operation not permitted`): six cases of
  `test_deploy_checks_the_yearly_price_with_stripe` and
  `test_deploy_yearly_check_skips_without_a_price_and_fails_without_a_key`
  in `tests/test_workflows.py`. Verify those outside the sandbox.
  Chromium launch is sandbox-blocked (`Operation not permitted`):
  own-site phone/desktop Back, focus/keyboard/Escape, dark mode and reduced
  motion QA remain pending. 390x844 first-card top: before **unavailable**,
  after **unavailable**; both measurements must be taken outside this sandbox.
  Branch creation also hit read-only Git metadata. No commit or push.
- **Deal pages from the wall (Codex task 068, 2026-10-13; shipped after review):**
  one page-selection pass before JSON adds remembered `deal_path` only for
  emitted public pages; live titles and the existing device-local Save use
  it. Get deal URLs and opaque locked stubs stay unchanged. Image-free
  detail pages have prominent prices, wrapping facts, 44px+ actions and
  accessible copy buttons beside always-selectable codes; task 062's chart
  and the spotlight/fallback/saved flows are retained. Explicit static
  store/category labels and one scoped delegate cover home fallback,
  listings, details and related offers without duplicating the dynamic
  listener; modified/middle clicks keep browser navigation. Detail views
  and successful copies are GA4-only. The private Product report adds
  device/page-family counts and actions/view (never purchases); absent or
  incomplete samples are unavailable, long Telegram reports split instead
  of truncating. A deterministic 1,400-deal path-only comparison measured
  JSON 1,073,979 → 1,149,869 bytes; gzip (level 9, mtime 0) 33,622 → 43,895
  (+10,273 bytes). Focused checks passed; final `.venv/bin/python -m
  pytest -q`: 2,560 passed, 1 deselected, only seven existing local-socket
  failures (`tests/test_workflows.py::test_deploy_checks_the_yearly_price_with_stripe`,
  six `change0`–`change5` cases, and
  `test_deploy_yearly_check_skips_without_a_price_and_fails_without_a_key`).
  Own generated-page QA was prepared for 360px, iPhone 14,
  Pixel 7 and desktop, light/dark and keyboard title/copy/Back journeys;
  Chromium fails before launch (`Operation not permitted`), so those
  visual/layout/focus checks remain. No task 062 owner is available in this
  session; its module is untouched and its tests pass. Branch creation is
  read-only here; no commit or push. Remaining: a full green run and browser
  QA outside the sandbox, then Claude review. Live GA4 counts await deployment
  and an observed event sample.
- **Installable app (Codex task 069, 2026-10-13; shipped after review; icons re-rendered from the header mark):** config-driven
  manifest/four existing-logo PNGs, every-page head tags, quiet 44px footer
  install/iOS Safari help, standalone/session tracking and GA4-only Product
  action counts; no service worker. Disabled output matches all four original
  mode baselines; no support hashes regenerated. Corrected the saved harness's
  stale media stub. Full suite: 2,552 passed, 1 deselected; only seven existing
  Stripe yearly-deploy tests blocked by local socket permissions.
  390x844 first-card top before/after unavailable (Chromium sandbox shutdown
  EPERM); `tests/pwa_layout.cjs` is ready for measurement, native install and
  status-bar QA remain. Branch creation read-only; no commit or push.
- **AI yield (Codex task 067, 2026-10-13; shipped after review):** intake uses the
  lower site floor; compact deduped supply/price-change facts, audit gaps and
  ledger-priced UTC reports. Observation starts with the first instrumented
  run (`ai_yield_since`; no live sample here). Shadow stays on: activation
  still needs 7 complete UTC days, 200 distinct evaluated candidates from
  3 independent sites, zero misses/gaps and a matching source/category replay.
  Checks: 2,520 passed; 7 existing Stripe deploy checks blocked by sandbox
  sockets; 1 deselected. No commit or push.
- **Saved deals (Codex task 066, 2026-10-13; shipped after review):** a heart in each
  photo's corner (cards keep their height); "♥ Saved (n)" leads the controls
  row only once something is saved; the device note shows only in the Saved
  view. Support-only,
  device-local identities/labels/store/path (100 default, 1–200 cap); current
  public cards, unknown/confirmed-ended states, remove/clear, cross-tab sync
  and tab-only fallback. GA4-only count/method events and private `saved`
  counts (report Product section) plus seven-day classified returning sessions; privacy copy updated.
  Full suite: 2,494 passed, 1 deselected; seven existing Stripe yearly-deploy
  cases blocked by local sockets. Chromium launch blocked 360px, iPhone/Pixel,
  keyboard, dark-mode and reduced-motion QA; branch creation is read-only
  here. No commit or push; tasks 061/062 untouched.
- **Static wall fallback (Codex task 064, 2026-10-13; uncommitted):** failed
  first JSON loads keep the static deals/links with an inline status and
  44px Retry; HTTP/JSON/shape validation precedes adoption, retry/timer/return
  requests share one first adoption, and an empty publish is successful.
  Four once-per-page GA4-only load events feed the private `product` counts;
  ad mappings/privacy gates unchanged. Full suite: 2,468 passed, 1 deselected;
  only 7 existing Stripe yearly-deploy tests failed at sandbox socket setup.
  Support/dormant membership checks pass. 360px/iPhone 14/Pixel 7/desktop
  visual checks with our JSON blocked/delayed remain pending: Chromium also
  fails with sandbox `Operation not permitted`. No commit or push.
- **Daily deal spotlight (Codex task 061, 2026-10-13):** one configured
  Meh/SideDeal/MorningSave offer first in static/live HTML, with a full phone
  row and DST-correct Meh clock. Price-only offers stay spotlight-only;
  missing dollar savings also allowed, known floors and rotation retained.
  Validation: 2,477 passed, 1 deselected; only 7 existing Stripe socket tests
  blocked by the sandbox. Remaining: verify live Meh's retained price/photo.
  No commit or push.
- **Price history (Codex task 062, 2026-10-13; uncommitted):** deal pages
  show our 90-day checks at 3+ observations spanning 48 hours: median,
  min/max, current-price marker, hidden UTC table and theme tokens. Dense
  histories show 24 real checks (stats/count use all); one indexed read/page,
  under 6 KB. Tests: 33 focused passed; full suite 2,460 passed, 1 deselected,
  7 existing Stripe yearly-deploy tests blocked by local sockets. Chromium
  360px visual review also blocked by the sandbox. No commit or push.
- **Lighter state (Codex task 060, finished by Claude after Codex hit
  "model at capacity", 2026-10-13):** `state.db.enc` was 52 MB, pushed every
  run, past GitHub's 50 MB warning (hard limit 100 MB). Now gzip (`-n -6`)
  before encryption; restore reads old and new snapshots and still fails
  closed; the first save after deploy migrates once. VACUUM after prune when
  free pages > 10% (a locked VACUUM logs and is skipped, never fails a run).
  Retention: settled LLM calls 35 d (pending and today/tomorrow kept), run
  stats 60 d, HTTP validators 30 d, REJECTED outgoing 60 d; sent delivery
  records are kept so a long-lived deal is never posted twice. Do not revert
  `scripts/state_crypt.sh` alone: old code cannot read a gzip snapshot.
- **AI value (Codex task 059 + review, 2026-10-13):** live 24 h sample: $7.32,
  Sonnet (critic + second opinion) 47% of spend on 8.5% of calls, three
  Slickdeals feeds 58% of extract spend, ~88% of extracted deals below floor.
  Changes: prompt caching on static prefixes (priced per model: write 1.25x,
  read 0.10x / Sonnet 0.05x; reservations price input at the write rate);
  identical post text is read once across sources (facts only, evidence
  re-verified per post; `retention.post_parses_days: 2` because every row
  rides the 52 MB encrypted state push); second opinion only when the text
  can clear the floor, with yield counters; below-floor prefilter ships
  `shadow` (structured posts never count); per-source deals/publishable.
  Watch in run summaries: `parse.reused_posts`, `second_opinion`,
  `prefilter`, cache fields in spend; flip prefilter to `on` only if
  `would_skip_publishable` stays ~0 for a few days.
- **Money/math correctness (Codex tasks 054/054b, 2026-10-13):** audit 051
  findings 9-13, 16, 17, 19, 20, 22, 23: math inputs need verified evidence;
  Keepa deals publish only with a valid critic verdict; distinct product links
  never merge; higher prices and ended posts retire stale offers; "2+ sources"
  needs independent sites supporting the active price; LLM spend is reserved
  (worst case) in the state ledger before each call and settled by UTC day;
  each run freezes its social batch; an uncertain delivery is never resent;
  one rounding rule; normalized used/open-box conditions. Workflows now save
  state even when the pipeline step fails, and check-point the encrypted
  state before each outgoing post/email (not before each LLM call: that
  serialized every call behind a git push; the ledger rides the end-of-run
  save). Live fix the same day: a raced checkpoint timed out at 60 s and
  crashed the Pinterest step; the limit is now 150 s, a timeout kills the
  script's git children, and a failed checkpoint skips that send (retried
  next run) and stops sends for the process instead of failing the step.
- **Phone feel polish (Codex task 058 + review, 2026-10-13):** 44px hit areas
  (small links keep their look; a transparent ::after extends the target),
  12px small-text floor, one chip row with a fade, readable source credits,
  native input hints (`enterkeyhint`, `inputmode`), no double-tap delay.
  Local phone audit (150 synthetic deals, iPhone 14): text under 12px 277 -> 0;
  card height 410 -> 373px; Get deal 37 -> 44px tall; no control steals
  another's tap. Desktop unchanged (pixel diff = clock/ticker only). Review
  dropped `viewport-fit=cover`: the safe-area padding lives in the <=640px
  rules, so a landscape iPhone (wider than 640px) would have put content under
  the notch. The env() paddings stay and read 0 until cover is added with
  landscape padding.
- **Top-of-page refresh (Codex task 057 + phone pass, 2026-10-13):** operator
  ("the top is sleepy; wake people up"): bright aqua header with a white
  search field, one amber accent (drop clock, Join free, active tab, % badges),
  five cards per row on wide screens, CSS-shape category tabs, and a
  text-only "Top Amazon deals right now · See deals" strip (no store logos:
  trademark rules) when 10+ Amazon deals are live. Phones: categories in one
  swipeable row with an edge fade (desktop keeps the wrap), filters at 16px
  (no iOS zoom). Headless-Chromium audit (scripts/mobile_audit.cjs): first
  deal at 362px on an iPhone 14 (was 474), no sideways overflow.

- **Partner newsletter inbox (Codex tasks 055/056, 2026-10-13):** email is
  a second channel for every permission partner; web readers keep running
  (we never get around their firewalls; operator will not re-contact them).
  An Email Worker (`cloudflare/newsletter-inbox/`) on
  deals@wowpriced.com forwards every original to the operator and stages
  capped bodies in KV for 7 days; the `newsletter` adapter reads facts only,
  keeps the partner's post as the credit link and evidence rules unchanged,
  and acks only handled messages. One enabled `partner_newsletters` source
  matches against all 64 permission entries, paused web feeds included, by
  exact `newsletter_from` addresses on the partner entry or registrable
  domain (bundled offline public suffix rules). Posts use the existing
  partner ID: feed + email cannot corroborate each other and publish one
  credit. Missing inbox URL/token stays `needs_key`, never a breaker failure.
  Unknown/ambiguous senders are counted in encrypted-state meta (address,
  count, last-seen date; delivery hashes prevent double-counting ack retries),
  then acknowledged. The admin digest is limited to once per UTC day, even
  if health warnings/counts change; the daily growth report reads the sender
  counts from its read-only snapshot. Addresses stay out of public logs.
  `docs/partner-newsletters.md` is the static checklist, generated by
  `python scripts/partner_newsletters.py` (preserves ticks/sender notes).
  A partner with several feeds on one site (Hip2Save, Southern Savers)
  matches its first enabled entry; an address listed in `newsletter_from`
  wins over a domain match; two partners listing one address stay unknown.
  Operator: **1)** repository secrets `NEWSLETTER_INBOX_TOKEN` (any long
  random string) and `NEWSLETTER_FORWARD_TO` (the verified Gmail
  destination), run **deploy newsletter inbox**, paste its URL into
  `partner_newsletters.inbox_url`; **2)** Cloudflare Email Routing → Routing
  Rules → Create: custom address **deals**, action **Send to a Worker**,
  destination **wowpriced-newsletter-inbox**; **3)** subscribe; senders match
  by domain. Use the checklist for every partner and confirm forwarded
  subscription emails; map off-domain senders with `newsletter_from` on the
  partner's own entry.

- **Partner credit + politeness (Codex task 053, 2026-10-13):** robots.txt
  now gates every request to a web page, HEAD and redirect hops included
  (robots.txt errors other than 404/410 disallow; the permission bypass is
  the granted source's own site host only, and lapses with a conditional
  grant when the monthly price reaches $19.99). Official keyed APIs (Keepa,
  Woot, Meh, Threads, Reddit) skip robots.txt by design: it governs crawling,
  and those hosts disallow the endpoints their API terms license (robots
  probe 2026-10-13); they keep our User-Agent, host pacing and status-only
  logs. A source-hosted image appears only with its grant and credit link,
  never on social; trackers on `competitor_tracker_hosts` are never
  rewritten; emails list every source credit and show no Amazon price;
  credentials never reach logs; state is never committed unencrypted.
- **Source coverage audit (Codex task 052, 2026-10-13):** `python -m
  dealradar source-audit` (workflow `source-audit.yml`, manual + Monday
  13:17 UTC, read-only state, never commits) probes each enabled source once
  and reports per source: verdict (OK / LOW YIELD / STALE / BROKEN / TRIPPED),
  likely cause, 24h/7d counts, drops by reason, live now, Get deal credit
  share. Counts and names only, to the job summary and the admin Telegram.
  The pipeline records idempotent discovery/drop/trip counters (history
  before 2026-10-13 shows n/a or partial). The Telegram reader is never
  probed (its one login session is shared with the fast lane).
- **Social copy polish (Codex task 050, 2026-10-13):** `social.social_title`
  (deterministic, 70 chars) drops model/part numbers, variants after the
  first comma and repeated brand words, keeps shopper specs (32MP, 1/4, 4K,
  2-Pack); Facebook, Instagram and Telegram lead with "🔥 NN% OFF: title".
  No prices anywhere, `#ad` last, Pinterest alt text keeps the full name.
- **Earning traffic (Codex task 049, 2026-10-13):** every published deal
  carries `earns` (its Get deal link credits our tag or Sovrn; a source post,
  competitor tracker in `affiliate.competitor_tracker_hosts` or untagged link
  does not). Non-earning deals rank lower in top picks
  (`top_picks.no_earn_factor: 0.5`) and social posts (Pinterest, Facebook,
  Instagram, Telegram) skip them (`social.require_earning_link`). The tracker
  repair is **off** (`affiliate.repair_competitor_trackers: false`, 2026-10-13):
  Deals of America, DealMoon and Southern Savers granted crawl permission on
  condition they keep their affiliate credit, and their tracker IS that
  credit. Each run puts back any tracker link the repair had replaced
  (`source_credit_restored`). Used / open-box / refurbished deals are never
  top picks or social posts (operator: one-unit items sell out to the first
  buyer). `link_mix` reports earning / not_earning.
- **Clarity + traffic report (Codex task 048, 2026-10-12):** Clarity
  (`tracking.clarity_project_id`) loads only without GPC/opt-out, like the ad
  pixels; every form, search box and account area carries
  `data-clarity-mask`, public deal content is unmasked; privacy.html lists it.
  The private growth report adds Cloudflare visitors/page views/requests
  (`CLOUDFLARE_OPS_TOKEN`) and Bing clicks/impressions
  (`BING_WEBMASTER_API_KEY`, a query-string key: httpx logging is muted
  around that call); logs say ok/skipped/error only.
- **Telegram channel picks (Codex task 047, 2026-10-12):** the public
  channel posts published picks only, once per deal, price-free (deal-page
  link, `#ad` last, link preview on); `telegram.channel` caps 1/run,
  24/New York day, 30-minute gap, 08:00-22:59 local, and `min_age_minutes:
  20` so the linked page is already live (the fast lane posts before its own
  deploy). Admin and email paths unchanged. Nothing posts until the operator
  adds `TELEGRAM_CHANNEL_ID` (bot token alone = admin alerts only).
- **Facebook/Instagram poster (Codex task 046, 2026-10-12):** `meta-post`
  runs after Pinterest in hourly.yml; one social-state commit covers both.
  `social.meta` in config.yaml: both networks start in **draft** (Facebook
  only fetches the Page token; Instagram creates and checks a container but
  never publishes; drafts are logged and kept in meta `meta_drafts`). Flip
  `mode: live` per network after the operator approves the drafts. Live:
  Facebook photo posts 4/day, Instagram 3/day at `hours_utc`; no prices,
  `#ad` last. A rejected image goes to `meta_failed`; throttle/permission
  codes never do. Token: secret `META_SYSTEM_TOKEN` (never-expiring system
  user); `.github/workflows/meta-probe.yml` checks it read-only.
- **Groupon-style footer + 300-card Trending (Codex task 045, 2026-10-12):**
  operator: "like groupon ... the other stuff tiny and in grey". One shared
  footer (`templates/site-footer.html/.css`) on every page: the sign-up box,
  one grey line "© year wowpriced · About · How it works · Deal hacks · Work
  with us · Contact · Join & support · Privacy", then 11px fine print (the
  search line + the config disclosure, unchanged words, AA contrast). Support
  terms live on the support page; the seller-link explanation on How it
  works. Trending: 120 picks then newest, auto-load to 300; other tabs
  endless. Reviewed in Chromium (desktop + phone).
- **Pinterest poster (Codex task 044, shipped 2026-10-12):** app "wowpriced
  deals" (ID 1623198, Trial access: sandbox only, Pins visible to us). Connect
  once: `python -m dealradar pinterest-auth-url` -> operator approves -> the
  code from `https://wowpriced.com/?code=...` goes into the "connect Pinterest"
  workflow (masks it, stores rotating tokens in encrypted state meta
  `pinterest_auth`). hourly.yml posts 2 top picks per run after deploy (20 per
  UTC day), https photo + live deal page only, "% off" and no prices, "#ad"
  last; `pinterest_posted` makes it idempotent. Standard access (public Pins)
  needs a screen recording; then `social.pinterest.environment: production`
  and connect again (sandbox tokens do not work in production).
- **Keepa fits the 20 token/min plan (2026-10-12):** operator: stay on the
  €49 plan; "amazon isnt like some bidding auction". Rechecks wanted ~23,000
  tokens a day at 3 h (new) / 6 h (used, ~13 tokens each) for every deal and
  starved the /deal feed (230 held back at 17:07). Cadence is now config
  (`keepa.recheck_fresh_h: 4` for deals first seen in the last 24 h,
  `recheck_new_h: 12`, `recheck_used_h: 24`); a suspect's confirming read
  goes first. Watch `price_recheck.tokens_short` fall and Keepa new posts rise.
- **Top picks landing (Codex task 042, shipped 2026-10-12):** config-driven
  quality/freshness score and strict caps select up to 120; public `pick`/`ps`
  drive All, static home, photo preconnects and best-today. Browser re-deals
  blocks of 12 with new arrivals leading; filters use `ps`, fresh drops and
  the 120-card auto-load limit remain. Operator: "the top 120 deals ...
  that will suck people in". Knobs: `publish.front_page.top_picks`.
- **Sure before removing (Codex task 043, shipped 2026-10-12):** operator:
  "make damn well sure that the deal is no longer there before removing
  it". Only a post's title or the line holding a deal's own final-price
  evidence quote can end it (before: "expired/dead/sold out" anywhere on
  an article page, sidebar and comments included, killed every deal in
  the post for good); bare "dead" no longer matches; `post_says_over`
  revives on a later backed post; a dead row is never re-killed (its
  verified GONE stays). Expiry and explicit online-only values: the most
  recently published statement wins; empty never clears. Summary
  `deaths_by_reason` + one log line per run. Keepa/Woot two-reading rules
  unchanged.
- **Simple code deal links (Codex task 040, shipped 2026-10-12):** code-only
  deals (codes, no rebates, steps only "use/enter/apply code X") at a store
  with a Sovrn wrap or override send Get deal to that store link. Every
  Amazon deal with our tag still goes to Amazon, steps or not (review fix:
  Codex had narrowed Amazon to simple deals). Other steps keep the post;
  codes, copy buttons and credit stay; emails and locked stubs unchanged.
- **Quiet sources (Codex task 041, shipped 2026-10-12):** the footer line
  "We search the top deal sites and stores for hours every day, so you
  don't have to." replaces the source wall; the "Found by" filter, per-deal
  `src` and `meta.source_sites` are gone. Keepa credits publish as "Amazon
  price drop" with no link (mapped at publish in `site._credit`, so stored
  sightings and the adapter keep Keepa's URL); deal pages say "90-day
  average price on Amazon"; about/hubs say Amazon. The site check fails on
  "keepa" anywhere in the home page or deals.json.
- **"Other" category repair (2026-10-12 09:45 UTC):** the 09:08 site check
  found 1,195 of 1,843 live deals (65%) under "other". Cause: a run reads at
  most 60 structured-deal titles, the rest were stored as "other", and the
  ASIN reuse map treated "other" as an answer, so they never got a read.
  Fix: "other" is never reused; `recategorize_other` gives live "other" deals
  the cached Amazon root, then one Haiku title read each
  (`parse.recategorize_other_per_run: 120`, inside the caps; only a real
  "other" answer is remembered as asked). Result: live "other" 1,195 (65%)
  at 09:08 -> 218 (11%) at 11:58 UTC; beauty 27 -> 54, fashion 274 -> 520,
  home 81 -> 319. Cut-off category replies are salvaged (claude_client
  `_closed_objects`); rules now name shoes/clothing/jewelry and
  lighting/mirrors/tools/plumbing; the asked-list key is `_v2` so old
  "other" answers get one more read. Per run: 240 titles (~$0.005).
- **Rich results + photo preconnect (Codex task 037, merged 2026-10-12):** live
  deal pages carry Product + Offer (price, InStock, seller = store,
  itemCondition from conditions; image only if https; brand if known);
  ended deal pages carry no Product. BreadcrumbList on deal, hub,
  best-today, store and price pages; ItemList (max 30, page order) where a
  page shows linked cards, none when empty. Home preconnects to
  m.media-amazon.com plus the top other image host of the first 24 cards.
  Review dropped priceValidUntil so deal-page bytes stay data-only (no
  midnight churn of every page through IndexNow).
- **Spend pacing + critic shadow (Codex task 036, merged 2026-10-12):** each
  run may spend at most `day cap x (minutes since 00:00 UTC + 2 h) / 1440`
  minus today's spend (`parse.spend_pacing`, `spend_pacing_headroom_hours`);
  the hard daily and per-run caps are unchanged and pacing only tightens
  them. Deferred posts retry next run. The critic keeps
  `parse.critic_reserve_share` (0.5) of each run's allowance so paced runs
  never publish deals unjudged because extraction spent it all. Up to 12
  Haiku shadow batches/day re-judge real critic batches after all real
  judging; verdicts are recorded (counts only), never used. The growth
  report restores a read-only state snapshot (continue-on-error) for
  "critic shadow: agree N/M". Why: 2026-10-12 spend ran ~$0.45/h; the $6 cap
  from Oct 14 would otherwise run out ~13:00 UTC. Decide on a Haiku critic
  from the shadow numbers.
- **Lighthouse polish (Codex task 035, merged 2026-10-12):** contrast and
  link underlines on every page (ratios in
  `docs/codex-tasks/035-lighthouse-polish-note.md`), hidden h2s for heading
  order, first four card images high-priority, ticker band reserved and
  built after paint. Review kept the fixed card media heights (150/118px:
  static and live walls share them, so no shift and no taller cards) and
  kept gone cards visibly greyed (grey surface, faded photo, muted text at
  >= 4.7:1) per the operator. Local Lighthouse (mobile): accessibility 100
  on home, best-today, hub, deal and /join; home performance 84 -> 88,
  CLS 0.053 -> 0.001.
- **Join card (Codex task 033 merged 2026-10-12; tasks 038/039 uncommitted):**
  `/join` and the desktop header popover keep the rounded two-step card:
  Google or email, then "What's your email?", email autofocus, Continue
  disabled until valid, Back, and the task 038 privacy line. The timed
  prompt is now a centred desktop modal over a soft dim, with the exact
  "Save up to / 90% OFF" headline, verified-deals copy, visible Email address
  label/input, a 56px "Get the deals" pill, configured Google below "or",
  and the task 039 email-consent/privacy line. Phone prompt: bottom sheet
  ≤45%. Successful email/Google joins show "You're on the list." plus the
  muted confirmation-email line; `/join` shows Back to deals immediately
  and redirects home after 1.5 s, while the prompt/popover closes after
  1.8 s. Desktop header (>640px): the same 360px popover beneath/right-aligned
  with Join free, no dim, Esc / ✕ / outside close, focus trapped and returned.
  Header clicks bypass frequency/storage limits (also available with
  `join.prompt: false`); phone and modified clicks retain `/join` navigation.
  Automatic rules unchanged: `join.prompt`, `join.prompt_delay_s` ≥15,
  `join.apple: false`; 15 s + one screen scrolled or a tab/filter used,
  once per 7 days, never on `/join`, after a join or without durable storage.
  The card takes the support banner's place for that page view (never both).
  Google = verified `/api/google` then `/subscribe` (no token kept), and
  `wp.join.joined` still remembers either method. The shared home/info/hub
  footer now has "Sign up for the latest deals", a 48px email field and
  round green arrow, and the exact privacy copy. It submits its own email
  to `/subscribe`, tracks `sign_up` with `method: "footer"`, replaces the
  form with "Thank you for signing up!", and hides for remembered joins
  (including joins elsewhere in the page or another tab). Its visible box
  suppresses the sticky asks; errors keep the form available for retry.
  Support-off page bytes remain unchanged. Site check step 6 checks
  step one on `/join` and walks the desktop popover/email/Back/Escape flow
  without submitting. Offline checks cover popup copy, single-step submit,
  configured Google/"or", steps, validity, errors, fake timers, focus,
  unchanged prompt rules, and footer submit/success/error/remembered joins
  on home and pages without a join card. Validation: 71 focused
  join/support/SEO/accessibility checks passed; `.venv/bin/python -m pytest -q`: 1,682 passed,
  1 deselected, only 7 existing Stripe tests failed on sandbox socket
  permissions. Chromium launch is blocked by the same restriction;
  desktop/phone visual verification and a full green run outside this
  sandbox remain. No commit or push.
- **Front-page mix (Codex task 034, merged 2026-10-12):** config-driven top 24 on
  All/fresh, static wall and best-today; other views, rotation and remaining
  order preserved. Sparse supply relaxes caps instead of hiding deals.
  `niche_after: 8` keeps the first screen everyday. Full suite green; live
  site check passed 2026-10-12 07:37 UTC (desktop + phone).
- **Search, filters and freshness (Codex task 031, merged 2026-10-12):** rounded header
  search, word matching across categories with tab restore, counted store/source
  display-name filters, cache headers and return-to-tab refresh. Offline harness
  covers search/refresh; hourly step 4 waits for the debounce. Chromium-
  verified ("best office" finds BestOffice; Walmart in the store list); full
  suite green; live site check passed 2026-10-12 07:37 UTC.
- **Sovrn links (Codex task 032, merged 2026-10-12; off until the redirect probe passes):** enabled retailer wrappers
  with tracking stripped and stable deal `cuid`; overrides win, Amazon/source
  links and emails keep their rules. Added Sovrn reporting and hourly guard.
  Validation: 134 focused checks (including idempotency) pass; full pytest:
  1,576 passed, 7 existing Stripe socket tests blocked by sandbox, 1 deselected.
  Full green run outside this sandbox and live Sovrn verification remain.
  No commit or push.
- **Professional buttons + Join page (Codex task 030, merged 2026-10-12):** support-mode
  pills and calm buttons in both themes; all Join links now go to `/join`,
  with old home hashes redirected and the email-only form on `join.html`.
  Sitemap/IndexNow and hourly step 6 include it; membership bytes unchanged.
  Merge resolution preserves task 029's header observer/scroll fallback and
  inline/footer suppression for plain `/join` links; both Node harness assertion
  sets run. Validation: 28 support checks passed; full suite: 1,521 passed,
  only 7 existing Stripe tests blocked by sandbox sockets, 1 deselected;
  all `tests/*.cjs` passed through pytest wrappers. Full green run and visual
  verification remain. Files resolved but unstaged (Git index is read-only in
  this sandbox). No commit or push.
- **Calmer wall (Codex task 029, merged 2026-10-12):** flames need 2+
  signals; phone Join yields to the visible header (observer + scroll fallback),
  banner and inline/footer asks; only the ticker loops. Tests: 1,463 passed,
  7 existing socket tests blocked by sandbox, 1 deselected. Chromium blocked too;
  full green run and visual check remain. No commit or push.
- **Greyed inline gone deals (Codex task 027, merged 2026-10-12):** post/expiry
  signals queue API rechecks; Woot reuses its feed, with no extra requests.
  Confirmed deaths alone publish as gone, inline and inert for 24 hours;
  live-only ticker/SEO lists. Updated bottom-section tests in `test_seo.py`
  and `test_publish_site.py`, plus shared-wall snapshots in `test_support.py`.
  Validation: 1,438 passed, 7 existing Stripe tests blocked by sandbox sockets,
  1 deselected; 52 focused checks (including idempotency) passed. No commit
  or push. Remaining: full green run outside this sandbox, review and merge.
- **Category sorting + Beauty/Health supply (Codex task 028, merged 2026-10-12):** cached
  Keepa root names override titles/reuse; indexed title answers retry only
  missing items; `recategorized_v2` repairs live categories once and resumes
  after spend caps. Named focus pages keep the token reserve and share the
  Amazon post cap. API shapes checked against official docs; full pytest:
  1,475 passed, 7 existing Stripe tests blocked by sandbox socket permissions,
  1 deselected. Live API verification remains. No commit or push.
- **Store and price pages (Codex task 023, merged 2026-10-12):**
  `/stores/<slug>/` ("Amazon deals today": stores with 15+ public live
  deals, top 12; one page per slug, so "Macy's"/"Macys" share a page) and
  `/deals/under-{10,25,50}/` (10+ deals), knobs in `seo:`, 60 cards each;
  "Stores" and "By price" link rows on hubs/deal pages; sitemap, IndexNow,
  stale-folder removal and the hourly check include them.
- **Get deal link mix (Codex task 024, merged 2026-10-12):** `link_mix`
  counts where each live public Get deal points (Amazon tagged/untagged/
  short link, source post, other store + top hosts, none) in the fast/hourly
  summary and the morning report's Money block; an untagged Amazon link is
  flagged.
- **Hourly screenshots + daily Lighthouse (Codex task 025, merged 2026-10-12):**
  desktop/phone home, deal, best-today and phone banner captures in `site-shots`;
  Lighthouse 12.6.1 daily 06:17 UTC, first sitemap hub/deal, accessibility/SEO
  `LH_MIN` 85, matching tracker blocks. Artifacts: 3 days. Node checks pass;
  pytest: 1,363 passed, 7 existing Stripe tests fail on sandbox socket
  permissions, 1 deselected. Chromium launch blocked here: live captures/scores
  need verification in Actions. No commit or push made.
- **Honest home engagement (Codex task 026, merged 2026-10-12):** five
  `engagement:` switches default on: browser-only visit NEW tags, weekly finds
  and streak; filtered top-50 Surprise; flames from published facts. Review fix:
  original header layout restored with no reserved slots; streak/finds share one
  muted 12.5px line above the grid in the cards' render pass (absent when empty
  or switched off); only click finds updates announce politely. NEW's qualifier
  is visually hidden; its tag sits below the price/condition flags. Updated
  offline Node checks and legacy page byte checks pass; full pytest: 1,380
  passed, 1 deselected, only 7 localhost-socket tests blocked by sandbox
  permissions. Chromium launch also blocked; desktop/phone height remeasurement
  remains. No commit or push.
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
- **Money report and Amazon guard (Codex task 022, merged 2026-10-12):** the
  morning report shows clicks out per store (GA4 `deal_click` by
  `customEvent:store`, top 10, yesterday and window; until the GA4 custom
  dimension `store` is registered it prints how to register it),
  `support_click`, and the Join free list (total, new yesterday) counted from
  the Worker's KV key metadata through the Cloudflare API (counts only, never
  an address). Hourly site check step 10 (desktop): every https amazon.com
  deal URL carries exactly one `tag=<affiliate.amazon_tag>` and the
  disclosure has the Amazon Associates line.
- **Deal pages + Best deals today (Codex task 021, merged 2026-10-12):**
  every public live deal gets `/deal/<slug>-<id8>/` (id8 = sha256 of the
  dedupe key; the path is remembered in db meta `seo_deal_paths`, so a
  renamed item keeps its URL; cap `seo.deal_pages_max` 3000, best rank
  first): facts only, no images, the regular price labeled by who stated it
  (`_basis_label`: Keepa's 90-day average, else "as stated by <source>"),
  Get deal with the deals.json URL (tagged) and the disclosure under it,
  6 related same-hub deals in key order (stable bytes while the wall
  re-ranks), JSON-LD Product/Offer. Ended deals keep a noindex page until
  their row is deleted. `/best-deals-today/` lists the top 30. Static card
  titles link to deal pages; the sitemap and IndexNow list alive pages; the
  hourly check reads one deal page and best-today. Verified in Chromium
  (desktop + phone, 0 errors).
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
  ticker click shows up to its card. Confirmed-gone cards now appear
  inline with spacing limits (task 027). Verified in Chromium: desktop 7,234 px at open (was
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
9. IndexNow's early sent accounting is addressed by task 072 above;
   review and deployment remain pending.
- **Real-visitor scoreboard + social links (Codex task 211, 2026-10-16; uncommitted):**
  Tagged Pinterest/Facebook/Instagram/Telegram links; dedup keys, alert tokens
  and canonicals unchanged. Validated profiles add footer/Join links and home
  `sameAs`; empty profiles preserve bytes. Private daily people/sessions plus
  Monday completed-week trends/channels, Search Console, Telegram subscribers
  and cached recent Pinterest clicks. Missing/partial numbers say not confirmed;
  early weeks flag October 16's filter boundary. GA4-only `credit_click` sends
  source names; weekly partner counts require an Event-scoped `partner` custom
  dimension. Four-mode comparison: **184 files**, only tracking/version changes;
  byte pins refreshed. Tracking documentation stays out of HTML to retain the
  40 KB guard; Stripe checks use an offline transport with the unchanged deploy
  step. Required full `-n 2` suite: **4,594 passed (19m57s)**. Test logs and own
  `/tmp/task211-*` scratch removed.
  Review/deployment remain; no live API/LLM calls, commit or push.
