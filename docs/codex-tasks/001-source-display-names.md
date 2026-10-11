# Codex task 001: friendly source names on deal cards

**Read first:** AGENTS.md, CLAUDE.md, docs/HANDOFF.md.

## Why
Every deal card credits its source: "via krazy_coupon_lady", "via slickdeals_frontpage".
Those are the internal `name` IDs from sources.yaml. Shoppers should see the site's
real name ("via The Krazy Coupon Lady", "via Slickdeals").

## What to do
1. In sources.yaml, give every source (about 109 entries, enabled or not) two new
   optional fields:
   - `display_name`: the site's own brand spelling, e.g. "Hip2Save",
     "The Krazy Coupon Lady", "Slickdeals", "Brad's Deals", "r/buildapcsales".
     Several feeds of one site share one name (slickdeals_frontpage,
     slickdeals_popular and slickdeals_amazon are all "Slickdeals").
   - `site`: the site's home page URL (https://hip2save.com/).
   If your environment can open a home page, confirm the name there with one GET
   per site and nothing more. Otherwise derive it from the feed's domain, and list
   in the PR which names you could not confirm.
2. Loader (dealradar/config.py): the source config gains `display_name` and `site`.
   A missing `display_name` falls back to `name`.
3. Publishing: the credit's `name` in deals.json uses the display name; its `url`
   stays the source post URL. Keep the change small and local, e.g. a helper in a
   new dealradar/publish/credits.py called from `_serialize_deal`. Another branch is
   editing site.py at the same time, so don't touch the rest of it.
4. Tests:
   - every source has a non-empty `display_name`;
   - feeds of one site share it;
   - a serialized deal's credit uses it;
   - the whole suite stays green: `python -m pytest -q`.

## Don't
- Put source names in page templates. `test_placeholder_and_competitor_names_never_appear`
  bans some of them from the static pages. Names live in deals.json only.
- Change any other behaviour.

## Deliver
Commits on this pull request's branch. If you can't push here, open a new PR into
`claude/dealradar-aggregator-pyqirm`. Never push to `claude/dealradar-aggregator-pyqirm`
itself: it is live. Claude reviews and merges.
