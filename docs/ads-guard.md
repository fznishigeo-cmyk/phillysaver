# Ads guard

Ad spending starts **unauthorized: $0/day**. The guard only pauses campaigns.
It cannot create, enable, delete, change a budget, or increase authorization.
Account: wowpriced **441-227-5803**, USD. No email, LLM or Keepa calls.

## Explicit authorization

The operator must type this exact format, with the desired dollar amount:

```text
AUTHORIZE ADS: $10 per day — Authorized by Arslan
```

(The account owner, Arslan, authorizes spend; the "— Authorized by <name>" part
is the format the operator agreed on 2026-10-14.) To go above $10 after a good
weekly scorecard, the operator types `RAISE AD LIMIT: $N per day — Authorized
by Arslan`; that is the only sentence that may raise `hard_max_daily_usd`.

Operator rule (2026-10-14): ads continue only while the affiliate commissions
they bring are at least 50% of ad spend (e.g. $35 a week at $10 a day). The
weekly scorecard compares real commissions with spend; the guard's daily
performance rules stop clearly losing campaigns earlier.

That line is an example, **not authorization**. Only the operator's own exact
typed sentence permits a human to update these three config.yaml values together:

```yaml
ads:
  authorized_daily_usd: 10
  authorized_on: "2026-10-14"  # date the operator actually typed the sentence
  authorized_quote: "AUTHORIZE ADS: $10 per day — Authorized by Arslan"
  hard_max_daily_usd: 10
```

Whole dollars or up to two decimal places are accepted. A missing, mismatched,
invalid or future-dated authorization means $0. Setting the amount to 0 revokes
authorization. The effective cap is the smaller of authorization and hard max;
even an edited hard max cannot exceed the standing $10 ceiling. An authorization
above the ceiling is clamped and raises an admin Telegram warning every run.
Code never edits these values. General encouragement, a payment card, or creation
of a campaign does not authorize spend. No campaign is automatically restarted,
including after midnight or a later authorization.

## Google settings and limits

Before enabling the manually created test campaign:

1. Keep it **Paused** until the operator has authorized spending and a guard run
   has succeeded with working admin Telegram alerts.
2. Use a USD account and a standard **daily budget at most half the effective
   cap**: at a $10 cap, at most $5/day. The **sum** of all enabled campaign budgets
   must also be at most $5. Shared budgets are conservatively counted once per
   campaign, so use one test campaign to avoid unnecessary pauses.
3. Use ordinary per-interaction billing (the API's `CLICKS` payment mode).
   Pay for conversions and lifetime/total
   budgets are rejected because the assumed daily limit does not apply. Unknown
   budget/payment modes also pause. Disable automated rules/recommendations that
   could raise budgets or re-enable campaigns; this guard never changes those
   settings for you. Avoid raising or moving budgets between campaigns mid-day.
4. Link this Ads account to the GA4 property and enable auto-tagging, or use the
   final URL suffix `utm_source=google&utm_medium=cpc&utm_id={campaignid}&utm_campaign={campaignid}`.
   Set the GA4 property's timezone to the Ads account timezone. Existing site
   `deal_click` and `sign_up` events provide the measured outcomes.

Google's [spending limits](https://support.google.com/google-ads/answer/10486637?hl=en)
are generally twice a campaign's average daily budget; pay-for-conversions has
no daily spending limit. These are **billing** limits; served cost can be higher.
Budget changes affect the limit. The guard's 80% stop leaves headroom but **cannot
guarantee a hard real-time account cap**: API metrics lag, campaigns may spend
between checks, Actions can be delayed or dropped, API/Telegram may be down, and
another account user can change settings. A total budget ceiling therefore needs
the Google settings above. Keep all campaigns paused when those protections
cannot be verified. This code does not claim otherwise.

## One-time campaign builder

The operator approved (2026-10-15, "Yes, build it paused") a separate builder:
`python -m dealradar ads-setup`, run only by hand from `ads-setup.yml`
(`validate_only` defaults to true). It reads `ads.setup` in config.yaml and, in
one atomic Google Ads mutate, creates a budget, a **PAUSED** Search campaign
(Google Search only, US presence, English, Maximize clicks with the max CPC,
the `{campaignid}` URL suffix above), broad negative keywords, one ad group
with phrase keywords and one responsive search ad. Google validates the whole
request first; a rejection creates nothing. It refuses a budget above half the
effective cap, never touches a campaign that already has the configured name,
and never enables anything: the operator clicks Enable in Google Ads, and the
guard watches the campaign from then on. Its log names only billing setup and
auto-apply recommendation status (enum names), so the operator knows whether
a payment method or an auto-apply switch needs attention.

Ad copy (operator 2026-10-23, "WHY NOT A+"): each ad needs exactly 15 unique
headlines (at most 30 characters) and 4 unique descriptions (at most 90), with no
Amazon mark or misspelling, other brand, price, number, "best", "!" or all-caps
word; the builder refuses anything else. The `update_ads` input (`--update-ads`)
changes only that copy in an existing campaign: for each configured ad group it
finds the group's one responsive search ad and updates it in place (AdService,
`updateMask` headlines and descriptions only), after Google validates the whole
request; `validate_only` stops there. It never writes a status, URL, keyword,
bid, budget or campaign, and an ad already showing the copy gets no write.

## Every guard run

`python -m dealradar ads-guard` uses the existing OAuth secrets
`GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN`,
the pinned `growth.google_ads_api_version`, account and optional manager IDs.
The identity needs campaign update permission as well as reporting access.
`GOOGLE_SA_JSON` supplies read-only GA4 outcomes. `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_ADMIN_CHAT_ID` are the only alert destination credentials.

It reads the account timezone/currency, all campaign statuses (including
zero-traffic campaigns), today's account and campaign costs, and enabled daily
budgets. Today's spend includes paused/removed campaign costs in the account
total. All enabled campaigns are paused if any of these conditions holds:

- No valid authorization / effective cap $0.
- Any daily budget exceeds cap/2, or their combined budgets exceed cap/2.
- Today's account spend reaches **80%** of the effective cap.
- An enabled campaign fails either performance test below.
- Spending cannot be verified (read/state failure, non-USD account, unsupported
  budget/billing mode). If campaign IDs could not be read, manual action is needed.

Performance knobs in `ads.performance` default to the last **30 completed
account days**, ending yesterday. Google cost/clicks and GA4 outcomes use the
same absolute dates. Excluding the partial day reduces decisions based on GA4
ingestion lag; it also delays a newly crossed performance threshold until the
next account day. The daily spend check still includes today. These are activity
proxies, **not verified sales or revenue**:

- Cost ≥ **$15**, zero measured sign-ups, and fewer than **10 Get deal clicks**.
- Cost ≥ **$20** and cost per Get deal click **> $1.50** (zero clicks fails too).

Google ad clicks are recorded separately and never used as outbound clicks.
[GA4 session campaign dimensions](https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema)
join by linked Ads account/campaign ID, then explicit numeric `utm_id` or
`utm_campaign` with google/cpc attribution; each row counts once. Campaign names
and unrelated organic/site traffic cannot earn credit. Missing attributed sessions,
thresholded/sampled/incomplete data, API failure, or mismatched timezones means
**unavailable**, not zero. At the lower performance spending threshold (default
$15), missing outcomes also pause conservatively with that distinct reason.
GA4 privacy/consent and ingestion gaps may undercount outcomes.

When first-party click collection is on, the existing `deal_clicks` and coverage
tables supply observed site totals and coverage. They have no campaign/UTM
dimension; those totals remain explicitly **unattributed** and never get assigned
to a paid campaign or added to GA4 (which would double-count visitors). No new
tracking data, Worker changes or collection requests are introduced here.

## Scheduling, errors, private records

The hourly workflow runs the guard before the pipeline. `ads-guard.yml` also
runs at minutes **13 and 43** each hour and supports manual dispatch. It only
installs/runs the guard and restores/saves encrypted state. All state writers
share the `dealradar` concurrency group. That prevents state overwrite races;
it also means the 30-minute cron is a target, not a delivery guarantee.

Both workflows save guard state even after API failures. A failed restore runs
`--state-unavailable`: pause defensively, alert, exit red, and never write an
empty replacement snapshot. An hourly guard failure is surfaced after the site
pipeline finishes. No new secrets go in public logs; Google errors retain only
status/error codes. Requests have the existing descriptive User-Agent, at most
one request per second per host, and no redirects or in-run mutation retries.

Every confirmed pause triggers admin Telegram. Failed notifications remain
pending for the next run. Read/mutate failures alert **every run until resolved**,
with these manual steps: open Google Ads, select account 441-227-5803, go to
Campaigns → Campaigns, filter Status = Enabled, select all, Edit → Pause, verify
every status is Paused, and check today's Cost in the account timezone.
Missing/broken Telegram makes the run red; no email fallback is sent.

The only Ads write is a
[campaign status mutation](https://developers.google.com/google-ads/api/rest/common/mutate)
with `updateMask: status` and `status: PAUSED`. Already-paused campaigns receive
no write. A timeout is recorded as unconfirmed; the next successful status read
can confirm it without issuing the mutation again. Successful notifications are
not resent. An operator re-enabling an unsafe campaign triggers another pause.

Encrypted state meta `ads_guard` keeps pause receipts/reasons for 31 days
(unresolved notifications longer), the latest check, and up to 192 deduplicated
run samples over two days. The private growth report's **Ads guard** section
shows the effective cap, exact authorization/date, dated spend, budgets,
campaign cost/ad clicks/attributed outcomes, and pauses today with reasons.
Snapshots older than an hour or from another account day are marked stale.
The report only reads this state; it cannot mutate Ads. Guard runs never send
email; the existing growth report's independently configured private delivery
remains unchanged. No numbers, campaign facts or authorization quotes enter
public job summaries, site pages or artifacts.

Local checks use fake API transports. After review/deployment, verify a manual
guard run, its private report and admin delivery while the account has no enabled
campaigns. No live account calls or campaign changes are part of development.
