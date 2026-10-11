# Codex task 008: the pages say "every 10 minutes"; the drops come every 15

**Read first:** AGENTS.md, CLAUDE.md ("Knobs live in config.yaml").

The drop clock steps by `config.yaml publish.drop_minutes` (15; it reaches
the page as `meta.cadence_minutes`), but about 23 places in
`dealradar/publish/templates/*.html` still say "every 10 minutes" /
"10-minute". That copy must follow the knob.

1. Add a `{{DROP_MINUTES}}` token, filled at publish in
   `dealradar/publish/site.py` the same way `{{CONTACT_EMAIL}}` is (every page
   in the `pages` loop) with `int(cfg.drop_minutes)`; also fill it in the hub
   pages and 404 (`dealradar/publish/seo.py`) if their templates state the
   cadence.
2. Replace each cadence statement in the templates with the token
   ("new deals every {{DROP_MINUTES}} minutes", "a {{DROP_MINUTES}}-minute
   cycle"). Change only cadence wording; leave everything else as is. Keep the
   `<!--paywall-->`/`<!--open-->` blocks intact (both variants get the token).
3. No published page may contain `{{DROP_MINUTES}}` or "10 minutes" /
   "10-minute" afterwards; add that check to tests, and update tests that
   assert the old wording to assert the configured value instead (e.g.
   tests/test_publish_site.py TestMembershipCopyIsTrue, TestOpenAccess).
4. Update docs/HANDOFF.md where it quotes "every 10 minutes".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push.
