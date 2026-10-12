# Codex task 046: post our top deals to Facebook and Instagram

**Read first:** AGENTS.md, CLAUDE.md (idempotency; knobs in config.yaml;
never log or publish secrets). Model the code on task 044:
`dealradar/publish/pinterest.py` (`_candidates`, `_word_trim`,
`_without_prices`, `post`, error class that never keeps a body), its CLI
wiring in `dealradar/cli.py` (also `CORE_DOMAINS`), `dealradar/config.py`
(`cfg.pinterest`, `SECRET_NAMES`), `.github/workflows/hourly.yml` (the
Pinterest step and the state commit after it), `tests/test_pinterest*.py`,
`tests/test_workflows.py`.

Operator (2026-10-12): automated posts to the wowpriced Facebook Page and
the @wowpriced Instagram account. Everything on Meta's side is set up and
probed (`.github/workflows/meta-probe.yml` passed): GitHub secret
`META_SYSTEM_TOKEN` is a never-expiring system-user token with the posting
permissions; Graph API answers as v26.0.

## 1. Config (`config.yaml`, new `social.meta`)
```yaml
  meta:                           # Facebook Page + Instagram (operator, 2026-10-12)
    enabled: true
    graph_base: "https://graph.facebook.com/v26.0"
    page_id: "1382622794936174"   # Wowpriced (public id, not a secret)
    ig_user_id: "17841412621006331"  # @wowpriced
    disclosure: "#ad"
    facebook:
      mode: draft                 # draft | live
      posts_per_run: 1
      daily_max: 4                # per UTC day
      hours_utc: [13, 17, 21, 1]  # live only: 9am 1pm 5pm 9pm New York (EDT)
    instagram:
      mode: draft                 # draft | live
      posts_per_run: 1
      daily_max: 3
      hours_utc: [14, 19, 23]     # live only: 10am 3pm 7pm New York (EDT)
      hashtags: "#deals #savings #wowpriced"
```
`cfg.meta = g("social", "meta")`; `META_SYSTEM_TOKEN` joins `SECRET_NAMES`;
`graph.facebook.com` joins `CORE_DOMAINS`.

## 2. Shared candidates (`dealradar/publish/social.py`)
Move `_candidates`, `_word_trim`, `_without_prices` out of pinterest.py into
a small `social.py` (same behaviour, Pinterest imports them; its tests stay
green). Candidates: live published picks in pick order with an https
image and a live deal page, not already posted on that network.

## 3. Post (`dealradar/publish/meta.py`, CLI `python -m dealradar meta-post`)
- Disabled / no `META_SYSTEM_TOKEN`: one log line, exit 0.
- Each network separately: skip in live mode when the current UTC hour is
  not in `hours_utc`; at most `posts_per_run` per run and `daily_max` per
  UTC day (count from the posted log).
- **Facebook** (photo post, more reach than a link post): GET
  `{graph_base}/{page_id}?fields=access_token` with the system token to get
  the Page token (memory only, never stored), then POST
  `{graph_base}/{page_id}/photos` with `url` = deal image, `caption` =
  "{pct}% off {item} at {store}. Verified by wowpriced: {deal page URL}
  {disclosure}", `published=true`. Live mode only.
- **Instagram** (two calls): POST `{graph_base}/{ig_user_id}/media` with
  `image_url` and `caption` = "{pct}% off {item} at {store}. Verified by
  wowpriced. Tap the link in our bio for today's deals. {hashtags}
  {disclosure}" (max 2,200 chars; links are not clickable on Instagram),
  then POST `{graph_base}/{ig_user_id}/media_publish` with `creation_id`.
  If the container is not ready, GET `{container}?fields=status_code`
  up to 5 times, 2 s apart, before publishing.
- **No prices** in any caption (Amazon's rules for social posts), same as
  Pinterest. Item names trimmed at a word so captions stay short (item
  ≤ 120 chars).
- **Draft mode** (the first rollout, so the operator approves the look before
  anything is public): Facebook makes no write call (it still fetches the
  Page token, proving access); Instagram creates the media container (Meta
  checks the image and caption) but never publishes it. Log one line per
  draft: network, pick number, the caption text, the image host, the deal
  page path (public facts only), container status for Instagram. Drafts
  go to meta `meta_drafts` and never block a later live post. `hours_utc`
  is ignored in draft mode; caps still apply.
- Idempotency: meta `meta_posted` = `{"facebook": {dedupe_key: {id, at}},
  "instagram": {...}}`, saved after each success before the next call.
- Errors: log the HTTP status and Graph error `code` only (never a body,
  URL with a token, or the token). A deal whose image Meta rejects
  (Instagram error on container create/publish, Facebook 400 on the photo)
  goes to meta `meta_failed` and is skipped from then on; try the next
  candidate, at most 3 attempts per network per run. 401/190 (bad token)
  stops that run with one line "meta: not connected".
- Summary line: "meta: facebook posted N (today M/4, draft|live);
  instagram posted N (today M/3, draft|live)".

## 4. Workflow
In hourly.yml, a new step right after the Pinterest step: "Post top deals to
Facebook and Instagram", `id: meta`, `continue-on-error: true`, env
`META_SYSTEM_TOKEN: ${{ secrets.META_SYSTEM_TOKEN }}` only, run
`python -m dealradar meta-post`. The existing state-commit step stays last
and also runs when only the meta step ran (rename it "Commit social state
to data branch"; condition covers both steps). Update the workflow tests.

## 5. Tests (httpx MockTransport, no network)
Captions: no "$", no price, disclosure last, item trimmed at a word, IG
caption ≤ 2,200; Facebook flow uses the Page token (not the system token)
for the photo call; draft mode makes no Facebook write and no
`media_publish`; live mode publishes; hours gate (live only); per-run and
daily caps; re-run posts nothing twice; rejected image lands in
`meta_failed` and the next candidate is tried (max 3); 401 stops; no token
in any log line or published file (render the site after a run and grep);
Pinterest tests unchanged and green; workflow step order, env and commit
condition. One short HANDOFF bullet under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
