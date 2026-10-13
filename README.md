# dealradar

An online-deal aggregator that politely collects deal posts from ~35 public
sources (RSS feeds, official APIs, public Telegram channels), extracts the
facts with Claude (every price backed by a verbatim quote from the source
text), computes the real checkout savings in pure code, dedupes and verifies,
then publishes a static site, Telegram alerts, and a daily email digest.

**Pipeline:** fetch → parse (Claude, evidence-verified) → savings math →
dedupe/verify/rank → site/Telegram/email.

- The savings math is deterministic code, not the LLM — rules in
  [SAVINGS_RULES.md](SAVINGS_RULES.md). Publish floors (config.yaml):
  `min_pct: 40` percent off at checkout **and** `min_abs_savings: 5` dollars (used/open-box: `used_min_pct: 50`).
- Source quality is measured, not assumed: `python -m dealradar
  validate-sources` live-checks every source and writes
  [sources_report.md](sources_report.md) (committed to the `data` branch after
  the first run).

### Verification badges — what they honestly mean

| Badge | Meaning | What it does NOT mean |
|---|---|---|
| `corroborated` | Two or more independent sources reported the same deal within 24 h. | Not a price check against the store — the sources could all be wrong together. |
| `history-checked` | The price is below the median of ≥ 5 prices we've observed ourselves for this item/store. | Our history is short and sparse; it's a sanity check, not Keepa. |
| `unverified` | One source said so. | Nothing has confirmed it. Most deals start here. |

Deals that expire, or stop being seen, are **dead-marked** (kept in the DB,
dropped from the site and alerts) rather than deleted — re-runs stay
idempotent and a returning deal is recognized, not re-alerted.

## 1. Quick start

```bash
make install            # python3.12 venv + pip install -e ".[dev]"
# config.yaml is already committed — no .env file, no config copying.
# Secrets are ENVIRONMENT VARIABLES ONLY. Never commit a .env.
$EDITOR config.yaml     # set contact_email (required: it goes in the User-Agent)
make validate-sources   # live-checks all sources, writes sources_report.md
make run-once           # one full pipeline run
make test               # offline test suite
```

`python -m dealradar run` works identically on a laptop, a VPS, or GitHub
Actions: the only state is `state.db` (SQLite) plus the `site/` output
directory. CLI surface:

```
python -m dealradar run [--fast-lane] [--db PATH] [--config PATH] [--dry-run-telegram]
python -m dealradar validate-sources   # live-validate sources, write sources_report.md
python -m dealradar digest             # force the daily-digest check now
python -m dealradar prune              # retention cleanup (posts 30 d, deals 14 d)
python -m dealradar domains            # print outbound hostnames (allowlist helper)
python -m dealradar indexnow --deployed --run-key "<published run key>"  # after a successful deploy
python -m dealradar growth-report      # daily PRIVATE growth report to the operator (growth-report.yml)
```

The hourly pipeline runs source validation automatically on its first run
(when no validated source set is stored in `state.db` yet).

## 2. Secrets

All optional except the first — a missing secret disables its feature and the
source is reported as `needs_key`, it never crashes the run.

| Variable | What it is |
|---|---|
| `DEALS_CLAUDE_API_KEY` | Claude API key for the parse layer. **Deliberately NOT `ANTHROPIC_API_KEY`** — the app reads its own variable so a developer's personal key in the shell is never silently spent by a cron job. |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | A registered Reddit app (script type) — Reddit is read via the official OAuth API only. |
| `REDDIT_USER_AGENT` | Reddit's required UA format: `platform:app-id:version (by /u/yourname)`. |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION` | MTProto **user** session string, used to READ public deal channels. The Bot API cannot read channels it does not admin, so a user session is the only compliant way. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID` | Bot that POSTS alerts to your own channel. |
| `TELEGRAM_ADMIN_CHAT_ID` | Separate chat for low-volume admin warnings. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Provider-agnostic SMTP for the daily digest; the single recipient lives in config (`digest.recipient`). |
| `WOOT_API_KEY`, `MEH_API_KEY` | Optional official store APIs. |
| `KEEPA_KEY` | Optional. Unlocks BOTH the `keepa_amazon_drops` discovery source (Amazon products freshly >=35% below their average plus Amazon Warehouse open-box offers >=45% under the live new price — the official, legal alternative to scraping Amazon) and the Keepa price-history cross-check that can award the history-checked badge. Get it at keepa.com -> Data -> API (flat-fee subscription, token-throttled: it can never overbill). |
| `THREADS_TOKEN` | Optional, behind `feature_flags.threads` — off until app review passes. |

On GitHub Actions, set these as repository secrets; the workflows map each one
into the run's environment. Secrets are never written to files or logs.

## 3. Adding a source

Edit `sources.yaml`:

```yaml
- name: my_deal_blog          # unique snake_case id
  method: rss                 # rss | api | html
  url: https://example.com/feed/   # a guess is fine for rss (see below)
  region: US
  allowed_html: false         # see below
  fast_lane: false            # true = also fetched by the 15-min workflow
  # optional: adapter, enabled, tz (IANA, for feeds with naive dates), notes
```

- **rss**: a guessed URL is fine — first-run validation autodiscovers the real
  feed via `<link rel="alternate">` on the homepage and the common fallbacks
  `/feed/`, `/rss`, `/atom.xml`.
- **api**: needs an adapter module in `dealradar/fetch/` plus an entry in
  `dealradar/fetch/registry.py` (see `woot.py` / `meh.py` for the pattern).
- **allowed_html** means *permission to try* fetching the linked article page
  when the feed is summary-only — robots.txt is still checked at runtime
  regardless, and a disallow always wins. It is a courtesy flag, not a bypass.

## 4. Network access (cloud / remote sessions)

For a first live run from a sandboxed environment, allow these domains
(printable anytime via `python -m dealradar domains`):

Core: api.anthropic.com, pypi.org, files.pythonhosted.org, github.com.

Feeds: hip2save.com, thekrazycouponlady.com, slickdeals.net, www.dealnews.com,
www.bradsdeals.com, bensbargains.com, api.woot.com, meh.com,
www.techbargains.com, www.dealmoon.com, moneysavingmom.com,
passionatepennypincher.com, www.freebies2deals.com, www.southernsavers.com,
couponproblog.com, thefreebieguy.com, yeswecoupon.com, jungledealsblog.com,
clark.com, hunt4freebies.com, www.livingrichwithcoupons.com, 9to5toys.com,
www.cnet.com, www.tomsguide.com, www.dansdeals.com, www.doctorofcredit.com,
kinjadeals.theinventory.com, www.couponcabin.com, www.retailmenot.com,
www.gottadeal.com, www.offers.com, www.nytimes.com, www.freestufffinder.com, stlmommy.com, www.swaggrabber.com, www.couponmom.com, www.dealsplus.com, glitchndealz.com, www.dealcatcher.com.

APIs: www.reddit.com, oauth.reddit.com, api.telegram.org, graph.threads.net
(flag-gated). Telegram MTProto additionally needs raw TCP to Telegram data
centers (149.154.160.0/20 and 91.108.0.0/16) — not plain HTTPS.

Backups: dealspotr.com, www.dailysteals.com, www.tanga.com, www.1sale.com,
www.mojosavings.com, www.commonsensewithmoney.com, www.frugallivingnw.com,
savingdollarsandsense.com, consumerqueen.com.

Plus your SMTP host. Redirect/affiliate-chain resolution touches arbitrary
retailer domains (amzn.to, bit.ly, amazon.com, ...) and degrades gracefully
when blocked — allow-all egress is simplest for full functionality; the list
above is the minimum for collection.

## 5. Scheduling & state

All ten workflows in `.github/workflows/` (cron schedules are UTC; each also
supports manual dispatch):

| Workflow | Schedule (UTC) | Does |
|---|---|---|
| `hourly.yml` | `0 * * * *` | Runs the full pipeline, commits state, deploys the site to Cloudflare Pages, and submits changed URLs to IndexNow. |
| `fast.yml` | `*/10 * * * *` | Runs `run --fast-lane` for fast-lane sources, commits state, deploys the site to Cloudflare Pages, and submits changed URLs to IndexNow. |
| `squash-data.yml` | `17 3 1 * *` | Squashes the `data` branch to one commit on the first of each month. |
| `growth-report.yml` | `23 12 * * *` | Collects growth metrics and submits the sitemap, then sends a private report to the operator by email and/or the Telegram admin chat without touching state. |
| `deploy-subscribe-worker.yml` | manual | Deploys the subscribe and membership API Worker to Cloudflare, creates its KV namespace if needed, and installs its secrets and supplied settings. |
| `source-probe.yml` | manual | Probes selected sources with the pipeline's polite fetcher and reports failures and advertised feed URLs. |
| `site-probe.yml` | manual | Reports DNS answers and response headers for the site's hosts, copies, and sitemap. |
| `image-probe.yml` | manual | Fetches supplied photo URLs once each with the project User-Agent and no Referer to check whether image hosts refuse hotlinking. |
| `stripe-check.yml` | manual | Checks trial checkout against Stripe by creating and immediately expiring a Checkout Session, inspecting subscriptions and portal settings, and charging nothing. |
| `stripe-setup.yml` | manual | Idempotently creates the membership product, monthly price, Payment Link, and customer-portal configuration in Stripe. |

- Hourly, fast-lane, and daily squash workflows share the concurrency group
  `dealradar` with `cancel-in-progress: false`, so they queue and never overlap
  (state commits and maintenance cannot race); the growth report uses its
  own `growth-report` group.
- `state.db` (and `sources_report.md`) live on the **`data` branch** — committed
  after every run by `scripts/commit_state.sh`, squashed daily, **never on
  main**. The commit messages carry `[skip ci]`. The repository is public, so
  with the `STATE_DB_KEY` repository secret set the branch holds only
  `state.db.enc`: deterministic gzip (`gzip -n -6`) before AES-256-CBC
  encryption with PBKDF2 (`scripts/state_crypt.sh`). The key stays in the
  environment. `scripts/restore_state.sh` also reads older snapshots that
  encrypted SQLite directly; the next save migrates them once. A missing or
  wrong key, corrupt gzip, or non-SQLite plaintext fails the run rather than
  start empty. Unchanged decompressed bytes add no commit. After retention
  cleanup only, SQLite is vacuumed when free pages exceed 10% of all pages,
  with a WAL checkpoint first. Saves log plaintext/encrypted byte counts;
  run summaries report `state_db_bytes` after SQLite closes.
- Keep hourly runs under ~3 min of runner time (the job has a hard
  `timeout-minutes: 15`; internal budget is 8 min). The run budget in
  `config.yaml` (`fetch.run_budget_s`) is the knob.
- **Hosting:** Cloudflare Pages (project `wowpriced`, secrets
  `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`) serves wowpriced.com and
  www. GitHub Pages is not used: its terms bar commercial sites.
- Honesty note: GitHub's cron is best-effort — under load, scheduled runs can
  be delayed or skipped entirely. The upgrade path is a small VPS cron with the
  identical command and state file:
  `7 * * * * cd ~/dealradar && .venv/bin/python -m dealradar run`

## 6. Cost per run

The parse model is chosen dynamically: the cheapest current model
(haiku-family) from the Claude models endpoint. At claude-haiku-5-5 prices
($0.10/M input, $0.50/M output), a worst-case 400-post hour at ~1.6k input +
400 output tokens per post is roughly **$0.06–0.15**. Typical hours (30–80 new
posts — only *new* content is parsed, re-runs cost nothing) are **$0.01–0.03**.
Hard ceilings: `parse.spend_cap_usd_per_run` (default $0.50) and
`parse.spend_cap_usd_per_day` (default $5.00 across ALL runs in a UTC day —
when the day's budget is spent, parsing pauses and unparsed posts simply
retry the next day); both in config.yaml.
Per-run detail: `parse.spend_cap_usd_per_run`;
parsing aborts gracefully at the cap and the remaining posts wait for the next
run.

## 7. Legal & politeness notes

Honored **by design**, not by configuration:

- robots.txt is checked before every HTML fetch; per-site ToS are encoded in
  `sources.yaml` (Slickdeals is RSS-only — `allowed_html` must stay false).
- No Cloudflare/Akamai/login-wall bypasses, no headless browsers, max 1
  request/sec per host, exponential backoff, ETag/Last-Modified conditional
  requests, descriptive User-Agent with a contact email — **set
  `contact_email` in config.yaml** before the first live run.
- We store and publish **only extracted facts** (prices, store, codes, dates)
  plus a link and credit to the source — never full post text or images, not
  in the DB, the site, or any alert.
- Reddit: official OAuth API under its API terms. Telegram: read via the
  MTProto user API under Telegram's ToS — public channels only.
- FTC: the site carries the affiliate-disclosure line
  (`publish.ftc_disclosure` in config.yaml). Keep it visible if you ever add
  affiliate links.
- Amazon Associates: before enabling Amazon affiliate links, prices may only
  be displayed per the Associates Program policies — use the PA-API for price
  display; scraped or stale prices with an Associates tag violate the
  agreement. The publisher has a single `rewrite_affiliate()` hook,
  intentionally inert, where tagging would go **after** a compliance review.

## 8. Operating notes

- `/health` page on the site lists every source's last success, items/hr,
  recent errors, and circuit-breaker trips.
- Admin warnings go to `TELEGRAM_ADMIN_CHAT_ID`: run volume below 50% of the
  7-day hourly average, or ≥ 5 sources circuit-tripped.
- "Last 24 h" freshness is measured on **our** `first_seen_at`, never on feed
  dates (feeds lie; our clock doesn't).
- Idempotent re-runs: posts dedupe on a content hash (`posts_seen`), alerts on
  UNIQUE tokens (`alerts_sent`) — running the same hour twice adds no rows and
  re-sends nothing.
- Retention (`config.yaml retention:`, also `python -m dealradar prune`):
  posts 30 days, cached parses 2 days, dead deals 14 days after death;
  completed LLM calls/reservations 35 days, run stats 60 days, HTTP validators
  30 days, failed (rejected) outgoing attempts 60 days. Sent delivery records,
  pending calls/deliveries and today's/tomorrow's UTC spend ledger survive. Price history and alert
  dedupe records are kept indefinitely; their row counts are logged.
