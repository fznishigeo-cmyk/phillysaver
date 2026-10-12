# Codex task 041: stop listing our sources; say "Amazon" instead of "Keepa"

**Read first:** AGENTS.md, CLAUDE.md ("credit to the source" stays: each
deal's "via ..." credit and the deal page's "Sources:" line remain).
Files: `dealradar/publish/templates/index.html` (footer `#srcwall` and
`paintSources`, the `#srcsel` "Found by" filter and its JS),
`dealradar/publish/site.py` (`meta.source_sites`, the per-deal `src` used by
the filter, credits), `dealradar/publish/seo.py` (`_basis_label`),
`dealradar/fetch/keepa.py` (the credit name/url near
`https://keepa.com/#!product/`), `templates/about.html`,
`templates/hubs.yaml`, `scripts/site_check.js`, tests.

Operator (2026-10-12, approved): competitors should not be able to see
every site we pull from or that Amazon data comes from Keepa.

1. **Footer:** remove the "Deals found across" chip cloud. In its place, one
   centred muted line: "We search the top deal sites and stores for hours
   every day, so you don't have to." Remove `meta.source_sites` from
   deals.json if nothing else needs it.
2. **Filter:** remove the "Found by: any site" select and its code, and the
   per-deal `src` field if only the filter uses it. Saved filter state
   that names a source is ignored safely.
3. **Keepa, publicly:** a Keepa deal's credit shows "Amazon price drop"
   with no link (no keepa.com URL anywhere in the published site or
   deals.json). Deal pages: "90-day average price on Amazon" (used: "new
   price on Amazon"). about.html and hubs.yaml: say "Amazon price history"
   / "re-checked against Amazon" instead of Keepa. Internal code, config,
   logs and docs keep the Keepa name.
4. Tests: no "keepa" (any case) in any published file or deals.json; no
   srcwall/srcsel; the new footer line present; credits on non-Keepa deals
   unchanged; the existing view tests that used `srcsel` updated to the
   remaining filters. One short HANDOFF bullet under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
