# Codex task 043: a deal leaves the wall only when we are sure it is over

**Read first:** AGENTS.md, CLAUDE.md (idempotency; evidence). Files:
`dealradar/verify.py` (`_DEAD_RE`, `text_marks_dead`,
`mark_unconfirmed_dead`, `mark_expired_dead`), `dealradar/pipeline.py`
(the `text_marks_dead(post.text)` call after `upsert_extracted`, the
summary), `dealradar/dedupe.py` (`_merge_online_only`, `_revivable_death`,
`_merge_expires`, the revive block), `dealradar/fetch/rss.py` (post text
is the article page, up to 20,000 chars), tests.

Operator (2026-10-12): "make damn well sure that the deal is no longer
there before removing it ... we want volume, but we also want accuracy."
An audit found these one-observation removals; fix each, nothing else.

## 1. "The post says it's over" (the riskiest)
Today `text_marks_dead(post.text)` scans the WHOLE post text (for RSS:
the article page with nav, sidebar and comments) for
`expired|dead|sold out|out of stock|oos` and kills EVERY deal parsed from
that post, permanently for non-Amazon deals. "Dead Sea" salt or a sidebar
"expired deals" link is enough.
- Count a post as saying a deal is over only when: the post TITLE matches,
  or the line of `post.text` holding that deal's own final-price evidence
  quote (`evidence.final_price_quote`, matched verbatim) matches. Nothing
  else in the body counts.
- Drop the bare word `dead` from the pattern (keep "deal is dead" and
  "dead deal" as phrases), keep `expired`, `sold out`, `out of stock`,
  `oos` on word boundaries.
- `post_says_over` becomes revivable like the other unconfirmed deaths: a
  later post (published after the death) reporting the deal at a backed
  price brings it back.

## 2. Stated expiry
- `_merge_expires`: when two sightings state different expiries, a later
  one from a post published later wins (an extension); a missing value
  never clears a stated one. Keep "today" resolution as it is.
- A deal past its stated expiry is still removed quietly as today; only
  the merge rule changes.

## 3. In-store vs online
`_merge_online_only`: the most recent explicit value wins (today one
"false" from any sighting hides the deal forever).

## 4. Visibility
Run summary `deaths_by_reason` (counts per death reason this run:
confirmed, api_ended, stated_expiry, post_says_over, ...) and one log
line; counts only.

## 5. Tests
Sidebar/comment/nav text with "expired" or "Dead Sea" kills nothing;
title "EXPIRED: ..." kills its deals; in a roundup only the deal whose
evidence line says "sold out" dies; "dead" alone never matches, "deal is
dead" does; a post_says_over death revives on a later backed post;
expiry extension from a later post wins, an earlier post's later date
does not, empty never clears; online_only most-recent rule; Keepa/Woot
two-reading rules unchanged; `test_rerun_is_idempotent` passes. One
short HANDOFF bullet under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
