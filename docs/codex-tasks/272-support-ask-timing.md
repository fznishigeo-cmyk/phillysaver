# 272 — Support panel after 2 minutes; cost wording (operator)

Operator 2026-10-17 ~03:40Z: "support our work popup comes up after 2
minutes and instead of hundreds write thousands".
1. The homepage "Support our work" panel (templates/support.js refresh():
   `panel.hidden = …`; today it appears once the visitor scrolls past card 8)
   additionally waits until 120 s after page load: `support.panel_delay_s`
   (config, validated int ≥ 0, default 0 = today's behavior; shipped 120),
   published in meta.support like the join prompt delay; schedule one refresh
   when the delay elapses (no polling). The join pop-up rules (35 s +
   engagement, once a week) and "one ask per page view" stay.
2. SUPPORT_BODY (dealradar/publish/site.py): "costs us hundreds of dollars a
   month" → "costs us thousands of dollars a month" via a validated config
   string `support.cost_phrase` (default "hundreds of dollars", shipped
   "thousands of dollars") so recorded byte pins can pin the old value.
Tests: delay honored (logic test with fake clock), 0 = today's bytes,
recorded pins pin `panel_delay_s: 0` and the old phrase ("recorded before
task 272"; never re-pin), public copy test passes.
Rules: offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task272-*`
basetemp (delete after), targeted tests only. No network, commit or push.
