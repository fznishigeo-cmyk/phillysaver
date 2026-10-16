# Handoff — state of wowpriced.com (updated 2026-10-16)

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
