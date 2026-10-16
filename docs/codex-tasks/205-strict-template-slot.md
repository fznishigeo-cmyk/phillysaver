# 205 — Give the home browse-script slot one strict assembly contract

## Why

Our publisher assembles feature fragments and separately computes versions.
Unchecked string replacements can silently omit a fragment after a template
edit. Plausible's named stages and Unleash's boundary validation suggest a
small strict boundary, starting with one slot. Preserve every published byte;
this is infrastructure work, not a template-engine rewrite. Background:
`docs/research/clone-wars-study.md`, PL1/U3/U4 and W1, rank 5.

## Task

Read CLAUDE.md, AGENTS.md and docs/HANDOFF.md. Coordinate with the owners of
119–121 and preserve their accepted changes. Independent of 201–204/206;
capture the actual checkout you start from, including any accepted additions.

Read `dealradar/publish/site.py:70` (version inputs), `:875` (support version),
`:1031` (BROWSE_SCRIPT insertion), `dealradar/publish/templates/index.html:3197`
(slot), `dealradar/publish/templates/browse.js:1` (price presets/markers),
`dealradar/publish/pwa.py:59`, `dealradar/publish/traffic.py:144`,
`dealradar/publish/tracking.py:407`,
`tests/test_support.py:27`, `tests/test_pwa.py:19`, and current publisher tests.
The migrated slot belongs only to the home template.

1. Capture pre-edit outputs at fixed time for support/open/closed membership/
   shipped config, support with/without payment links, PWA on/off and Amazon
   hide/show in isolated fixtures. Never change shipped Amazon hide. Keep
   filename→bytes manifests, config, input facts and site_version for comparison.
   Use owned scratch or pytest tmp_path, never other tasks' scratch directories.
2. Add a small proposed `dealradar/publish/template_slots.py` helper for a
   literal named slot. It takes template text, the exact expected token,
   replacement text and expected count (one for this migration). Count and
   validate before replacing; distinguish missing and duplicate slot errors
   using safe template/slot names only. Do not expose templates, facts, user
   content, secrets or exception bodies in diagnostics. No generic parser,
   regex framework, dependency or configuration platform.
3. Migrate only `{{BROWSE_SCRIPT}}` in index.html to that strict contract.
   Resolve the browse fragment once for the applicable variant, including
   existing price-preset substitutions and Saved marker handling. Reuse the
   resolved bytes/descriptor for insertion and the corresponding version
   inputs, but preserve the exact existing hash algorithm, byte concatenation
   and strip rules in every mode. If a broader hash unification is necessary
   to do this, keep the old hash behavior and report the follow-up instead.
   Do not migrate other markers or use a new generic version for the site.
4. Validate the home slot before render_site's first output-directory mutation
   or state/meta write. A malformed home template must fail without overwriting
   an existing published tree or advancing any publication receipts. Avoid a
   new all-site staging/promotion system: move this one preflight earlier in
   the current function. Validation and replacement must use the same loaded
   template instance, not re-read a potentially changed file between them.
5. Document the one slot's producer, expected count, allowed page/variant,
   preprocessing order and version dependency next to its descriptor/helper.
   The remaining postprocessing order stays PWA → traffic → inventory →
   tracking → click signal → sitemap/audit → IndexNow seal, as currently at
   `dealradar/publish/site.py:1153`. No changes to card ordering, public
   serialization, SEO admission, evidence or emitted event behavior.
6. One literal substitution is the contract. Replacement text containing
   backslashes, dollar signs, marker-like strings or a token-shaped string
   must be inserted literally and not recursively evaluated. A disabled feature
   inside the browse script must retain today's stripped bytes and version.
   Don't add strict counts to unrelated legacy markers in this session.
7. No shopper behavior changes, so no new config knob is needed. Valid output
   is byte-identical; the intended changed behavior is only a clear developer
   failure on a malformed required slot. Do not claim a remotely controlled
   kill switch. Keep the existing fallback available when JavaScript fails.

## Tests

Add meaningful offline helper/publisher cases:

- Exactly one slot substitutes literal content; zero/two slots fail with a
  safe diagnostic. Substitution does not recursively parse the replacement.
- Remove/duplicate BROWSE_SCRIPT in a local template fixture; assert the
  pre-existing output tree, state/meta and IndexNow records are unchanged.
  A corrected template then publishes successfully; rerunning is idempotent.
- Compare every pre-edit/candidate output filename and byte in the captured
  modes, including site_version, public/member JSON, canonical URLs, embedded
  JS, Amazon stamps/hide mode, PWA assets, share/RSS and all metadata.
  A failure must show the changed filename/first differing area, not just a
  new hash. No expected-hash refresh is permitted for this refactor.
- Include non-default price presets and Saved disabled/enabled; existing
  browse Node harness runs against the resulting script. Verify required
  tokens resolve exactly as before, no listener duplication and static-wall
  fallback remains. If 201–204 already exist, their off/on output must also
  stay equal to their captured baselines.
- Run focused publish/support/PWA/SEO/browse/Amazon tests, existing offline
  pipeline rerun-idempotency cases, then `python -m pytest -q`. Record exact
  failures; any claimed baseline failure needs an independent baseline check.

This change needs no live browser/network requests. Optional existing offline
JS syntax/behavior harnesses are sufficient for unchanged page bytes; do not
invent a new visual or LCP result. Confirm only intended publisher/helper/tests/
handoff files changed. Preserve existing tests and byte pins.

## Constraints

One slot, one helper, no rewrite or global flag registry. No framework,
dependency, server, login, Worker/workflow edit, source crawling, paid call,
model/cap change, new tracking or service worker. Keep robots/permissions/
1 req/s/host, evidence verification, facts-only storage, savings.py arithmetic,
config knobs, encrypted data-branch state and idempotent re-runs. Preserve all
operator decisions, affiliate/source credit, access modes, privacy/internal
gates and WCAG 2.2 AA markup. Keep `amazon_history: hide`, date/time-qualified
current Amazon prices/disclaimers and all Amazon tracking/history/alert
prohibitions; both hide/show fixtures test equality without activating show.
No emitted-byte/phone-performance regression or new LCP claim. No network,
commit, push or deploy. Never delete other /tmp directories. Update HANDOFF
with files, byte-comparison evidence, tests and any deferred broader cleanup;
end with a short plain summary.
