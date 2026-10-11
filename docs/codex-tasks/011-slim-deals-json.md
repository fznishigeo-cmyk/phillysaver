# Codex task 011: slim the public deals.json (phones download it on every visit)

**Read first:** AGENTS.md, CLAUDE.md, docs/paywall.md.

With open access (config.yaml `paywall.open_access: true`) the public
`deals.json` carries every deal in full (~1,400), about 4.6x the old size.
Most visitors are on phones. `dealradar/publish/site.py _serialize_deal`
emits fields the page never reads.

1. Measure first: render a realistic site (see tests/test_publish_site.py
   `make_deal` with codes, steps, credits) at 1,400 deals and report the
   deals.json size raw and gzip, before and after.
2. List every field of a serialized deal and, for each, where it is read:
   `dealradar/publish/templates/index.html` (search for `d.<field>`,
   `x.<field>`, string keys like `"pct_at_checkout"` in sort/filter code),
   `dealradar/publish/seo.py` (static wall, hubs), `paywall.py` (STUB_KEYS,
   rotation uses `key`/`status`), the Worker (`cloudflare/subscribe-worker`
   serves the members' payload), tests. Put that table in your summary.
3. Drop from the serialized deal only the fields nothing reads. Keep the
   members' full payload identical in shape to the public one (same
   serializer). Do not change any value of a kept field. Locked stubs keep
   exactly STUB_KEYS.
4. Write compact JSON for the published deals.json (no indent, separators
   `(",", ":")`); keep it valid JSON.
5. Tests: a test that fails if the serializer emits a field that no consumer
   reads (a maintained allow-list with a comment per field is fine); all
   existing tests green; the e2e offline test still passes.

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push.
