# 225 — Give pure link facts one owner and reuse them within a publish

## Why

`dealradar/rank.py:245` builds an entire public deal only to read earns.
`dealradar/publish/site.py:764` scores candidates, `:777` serializes, then `:780`
scores with earns available. This repeats identity/credit/URL work and couples
ranking to publisher internals (`dealradar/rank.py:190`). Redis SDS suggests carrying
stable facts once; nanoGPT/llm.c suggest making one idea visible in one unit.
Some repeated ranking uses different eligibility inputs and must remain.

## Read first

Read CLAUDE.md, AGENTS.md, HANDOFF and the
[study](../research/genius-code-study.md), R2/Q2/N1/L1/L2.
Read the complete serializer, _needs_steps/_simple_code_deal, affiliate rewrite,
earning_link, _credit/source_sightings, pick_score and render_site, with their
tests. Inspect task-121 Amazon and task-119 photo decisions before editing.
Read-only references: `/home/user/redis/redis/src/sds.h:98`,
`/home/user/karpathy/nanogpt/model.py:29` and
`/home/user/karpathy/llm.c/train_gpt2.c:821`.

## Task

1. Capture a baseline/call profile first. Add proposed
   `publish.reuse_link_facts.enabled: false` in config.yaml/validated config;
   omitted/false must preserve prior outputs and behavior. Ship disabled.
   If the profile shows no meaningful repeated link work, report that and keep
   the extraction small rather than expanding to a general memoization system.
2. Extract the serializer's pure identity/credit/Get-deal decision into one
   small neutral module (proposed `dealradar/publish/link_facts.py`). Explicit
   inputs are the deal facts and resolved affiliate/link policy. No DB, fetcher,
   environment reads, configuration mutation or file output inside the helper.
   Retain compatibility wrappers/imports for current callers as needed.
3. Return an immutable internal result containing only what routing/ranking
   actually needs: opaque key, credited sources, needs-steps/simple-code facts,
   final Get-deal URL and final earns. The final URL's earns differs from the
   preliminary store link's earns; preserve that distinction exactly. Avoid
   exposing a shared mutable credits list or adding internal fields to JSON.
   Do not move checkout/savings arithmetic or history/image eligibility here.
4. Enabled render_site creates one render-local mapping after its input facts
   and link options are fixed. Reuse the result for candidate earns checks and
   normal serialization within that render. Keep differing candidate/public
   confirmation, label, paywall and top-pick eligibility passes distinct.
   Ranking calls outside this render still derive current facts normally.
5. Include every routing input or resolve it once in this immutable scope:
   canonical/as-posted/product identity, sightings/credit order, steps/codes/
   conditions, protected tracker policy, Amazon tag, Sovrn, eBay and overrides.
   If any relevant input differs, derive again. Never cache solely by deal key
   across mutations, configurations, runs or wall-clock changes. Never persist
   this map. No rank score, verified price or observation is memoized.
6. Preserve rewrite_affiliate as the sole rewriting authority, protected source
   trackers and source-post credit, Amazon-with-tag routing even for steps,
   earning simple-code exceptions and unknown-link fallback. Preserve serializer
   field/value/order contracts, locked stubs, member payload, disclosures,
   as-of stamps, current-price labels, photos and all page selection/order.
   No template extraction or JS rewrite in this brief.
7. Count helper/serializer invocations in offline profiling only or existing
   private diagnostics. No public performance badge or new request. Remove the
   rank→site dependency only for the pure helpers actually extracted; do not
   refactor other imports or the entire pipeline as incidental cleanup.

## Tests and acceptance (later implementation session)

- Compare every generated file, versions, raw/gzip sizes, public/member JSON,
  link mix and selected key order against a same-checkout baseline, off and on,
  across four access modes with existing feature switches. This is a behavior
  preserving optimization: enabled output must match too. No repinning.
- Independent routing fixtures: ordinary direct link, post-only/absent link,
  steps, simple versus complex codes, Amazon tagged/untagged, URL-only or
  mislabeled Amazon, protected competitor tracker, override precedence, Sovrn,
  eBay, malformed URLs and duplicate/multi-feed credits. Check exact final URL
  and earns against existing expected behavior, not helper internals.
- Change relevant inputs while keeping the same deal key, then change config
  and rerender: no stale earns, credits or route. Two deals with similar titles
  but distinct URL/condition remain separate. No mutable aliasing. Confirm that
  differing freshness/checkout/paywall/confirmation passes still disagree where
  intended. No private mapping/fields in public JSON or locked stubs.
- Repeat run/publish, reopen the DB, and run offline pipeline idempotency in both
  access modes: no duplicate rows, observations, spend, IndexNow or messages.
- Compare repeated-call counts, render CPU/peak memory for fixed ~2,300/5,000
  deal fixtures with varied routing, including the all-unique case. Count
  avoided *full projections*, not a guessed speedup. No source/paid requests.
  Run focused affiliate/rank/publish checks then `python -m pytest -q`.

## Constraints and finish

CLAUDE.md and every study gate apply: robots/permission/1 request/s/host,
UA/backoff/no bypass, verified evidence and facts only, savings.py money math,
config LLM caps, Keepa reserves/observations/two-reading deaths, encrypted
data-only state and idempotency. No Amazon history/alerts/watches or price
cache; preserve hide mode, shared identity and current-price as-of/disclaimer,
including mixed stores. No third-party scripts, dependencies, tracking,
service worker, shopper UI, new image behavior or first-card/phone regression.
GPC/opt-outs and all operator decisions remain.

Keep the switch off and update HANDOFF in the later implementation session with
byte comparisons, call counts, cost observations and remaining coupling. No
activation, network probe, commit, push or deployment is authorized here.
