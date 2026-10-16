# Publish link facts (task 225)

`publish.reuse_link_facts.enabled: false` is shipped in `config.yaml` and is
the validated default when omitted. Activation is deferred to review. This
changes no requests, prices, evidence, observations, spending or shopper UI.

`dealradar/publish/link_facts.py` owns the pure routing decision and the single
`rewrite_affiliate` implementation. Existing site imports/wrappers remain usable.
Source-file resolution stays in `publish/credits.py`; the helper receives an
immutable copy of source names, excluded source hosts, overrides, tracker hosts,
Amazon tag, Sovrn settings and eBay campaign. Its immutable result contains only
the opaque key, tuple credits, steps/simple-code booleans, final URL and final
earns. Preliminary store-link earns still determines the simple-code exception;
final earns is classified again after post fallback and protected routing.

With reuse enabled, one local map lives inside `render_site`. Its keys contain
the dedupe/product identity, canonical/as-posted URLs, ordered source names and
URLs, codes, steps, conditions, rebate presence and the relevant rebate flag.
Display-label copies can reuse a result. Changed routing inputs derive again.
The policy is resolved once for that render; a later render takes a new snapshot.
No map survives return, reaches public/member JSON or is written to SQLite.
Each serialization creates its own mutable JSON credit dictionaries.

The extraction also lets `rank.pick_score` derive just current link facts when
it needs earns outside a render, including with reuse off. Explicit earns inputs
keep their existing precedence. It no longer builds an entire public projection
to read earns. The switch specifically controls render-local
reuse; off still calculates each caller's facts independently. Candidate scores,
public/label/confirmation/checkout gates, paywall selection and top-pick caps
remain separate passes. No price, checkout arithmetic, image permission, rank
score or confirmation clock is cached.

## Same-checkout byte comparison

`tests/fixtures/publish_link_facts.json.xz` retains the actual output captured on
**755371a280d8**, before changing the publisher/ranker, at **2026-10-16 12:00 UTC**.
Only generated files and derived comparison facts are retained; SQLite/WAL/SHM
are excluded. `tests/test_publish_link_facts.py` defines the fixed 36-offer
fixture and four access configurations. It covers mixed/URL-only Amazon,
tagged checkout steps, Sovrn, eBay, overrides, protected trackers, direct/post
fallbacks, missing photos and used conditions, retaining the checkout-label,
Amazon-hide/as-of, photo, event and traffic features from this checkout.

Both off and on match **287 public files plus 8 adjacent IndexNow files** exactly,
along with serialized member payloads, versions, selected candidate/wall key order
and private link mix. Every file's raw and deterministic gzip sizes match.
No existing snapshots/hashes were repinned.

| Access mode | Public files | Adjacent files | Public JSON raw / gzip bytes |
|---|---:|---:|---:|
| Support | 83 | 2 | 42,647 / 4,027 |
| Open membership | 82 | 2 | 42,626 / 4,018 |
| Closed membership | 45 | 2 | 18,124 / 3,101 |
| Paywall disabled | 77 | 2 | 42,626 / 4,018 |

Additional checks exercise independent expected routes/credits, immutable policy
and credits, changed inputs during a render, changed configuration on the next
render, labeled copies, daily/price-only spotlight, distinct URL/condition
identities, separate eligibility passes and opaque locked stubs. Repeat publishes
after reopening SQLite compare complete logical dumps. Offline pipeline replays
exercise both open and closed access, including receipts, spend and send dedupe.

## Offline profiling

The reproducible driver is `tests/profile_publish_link_facts.py`. It uses fresh
processes, a common fixed time and source-policy warmup, and varied routing at
2,300/5,000 deals. One pattern repeats merchant item IDs; the other uses per-deal
IDs in patterned URLs. Post fallbacks and the configured override remain fixed.
Every opaque deal identity and complete routing key is unique in both fixtures,
so neither depends on cross-deal cache hits. It caps detail pages at
24 and disables product/event pages equally in all profiles, while retaining the
complete wall/JSON and other feature passes. The small four-mode comparison above
uses the existing enabled features without those profiling caps.

Run from the checkout, sequentially:

```sh
.venv/bin/python tests/profile_publish_link_facts.py baseline 755371a280d8
.venv/bin/python tests/profile_publish_link_facts.py off
.venv/bin/python tests/profile_publish_link_facts.py on
```

The baseline executes only the original rank/site owners read from that revision
with the same local fixtures, templates and options; it never checks out files.
Counts come from cProfile. Adding `--measure` runs the same fixtures without
enabling cProfile, to measure CPU separately from the counter overhead. Peak RSS
includes the interpreter, imports, fixture and render; the driver also reports
the pre-render process high-water mark. These are single offline samples on a
shared machine, not live latency or memory guarantees.

The two merchant-ID patterns have identical call counts at each size:

| Deals | Full projections, before / off / on | Link derivations, off / on | Rewriter calls, before / off / on |
|---:|---:|---:|---:|
| 2,300 | 4,284 / 2,300 / 2,300 | 4,284 / 2,300 | 3,911 / 3,911 / 2,108 |
| 5,000 | 9,313 / 5,000 / 5,000 | 9,313 / 5,000 | 8,503 / 8,503 / 4,583 |

Thus the extraction avoids **1,984 / 4,313 full public projections**. The enabled
map additionally avoids that many link derivations. Every deal still receives
its normal public projection, including when every routing key is unique. The
enabled key builder runs three times per ordinary grid deal; no score pass was
removed. Counter-run CPU is not used to infer an uninstrumented speedup.

| Deals | Merchant IDs | CPU seconds, before / off / on | Peak process RSS MiB, before / off / on |
|---:|---|---:|---:|
| 2,300 | Shared | 20.493 / 19.435 / 17.346 | 303.2 / 304.7 / 304.2 |
| 2,300 | Per-deal | 22.761 / 19.176 / 19.758 | 304.9 / 303.4 / 304.7 |
| 5,000 | Shared | 47.504 / 36.397 / 41.618 | 599.3 / 600.0 / 602.6 |
| 5,000 | Per-deal | 47.767 / 40.563 / 42.045 | 596.8 / 600.0 / 603.5 |

The extraction reduced ordinary render CPU relative to the original in these
fixtures. The enabled map reduced helper calls but did **not** consistently beat
the extracted off path on CPU; it also retains routing records until return.
These samples do not establish a general latency or memory win for reuse. Keep
the switch off pending review and representative live measurements. No additional
memoization was introduced.

**3,267 distinct targeted cases across 91 files pass in their final context**,
including all **71 new feature cases**, the named affiliate/rank/publish checks,
existing byte pins and adjacent consumers, and enabled/offline rerun checks in
both access modes. Every pytest command used `-q -n 1 -p no:cacheprovider` and a
private `/tmp/task225-*` basetemp. Tests ran sequentially with temporary output
cleanup after a shared-disk interruption; the interrupted run is not counted as
completed validation.

One extra registry check fails in per-file isolation because its fake RSS module
has no `MAX_ENTRIES` when the page adapter imports it. The unchanged test fails
identically against an untouched `git archive HEAD` export. Running
`tests/test_pages.py tests/test_registry.py` gives **58 passed** on both that
export and this change. No fixture was edited. Final distinct-case coverage uses
that green context for the registry; it does not count retries twice.

## Release boundary and remaining coupling

No live source, paid request or delivery was used. Synthetic pipeline spend and
receipt behavior retain their existing checks; no live cost saving is claimed.
The reference repositories supplied ownership/lifetime ideas only; none of their
implementation was copied. No dependencies, templates or browser code changed.

`rank` still depends on the small neutral publish helpers and source-policy
resolver. The serializer still owns checkout copy, money display, images, Amazon
history/as-of and price labels. Its headline can independently ask whether steps
are needed. SEO's retained-ended-page fallback uses its own untagged projection;
that different policy intentionally derives normally. Other pipeline imports of
site helpers remain compatibility imports. Broader publisher/pipeline extraction
and general memoization are outside this change.

The operator requested targeted tests only, all with `-n 1` and dedicated
`/tmp/task225-*` basetemps; the full suite remains for the operator. Workflow
tests and workflow files are unchanged. Review, the operator's full suite and
live operational measurements precede any activation or deployment. No commit,
push or deployment was made.
