# 348 — Live checks for the tool pages and the homepage buttons (monitors only)

Repo: dealradar (wowpriced.com). Read CLAUDE.md first; its non-negotiables
apply (politeness, ≤1 request/s/host, our own site only, no retailer
requests, descriptive UA, small surgical diffs, match existing style).
This task changes MONITORING SCRIPTS AND THEIR TESTS ONLY — no site code,
no config.yaml knobs, no published bytes change.

## Why
Since 2026-10-20 the homepage "AI Analysis" (phone "Analyze") button is a
link to /amazon-deal-checker/?q=<words> and "All Discounts" (desktop
#search-discounts, phone #search-discounts-mobile, label "All Discounts →")
links to /amazon-discount-finder/?q=<words>. The tool pages fill the box
from ?q= and start one run (random 10–25 s, progress bar) and then show
#tool-result with a yellow Amazon button (`#tool-result .amazon-button`):
- Discount Finder: "See Amazon discounts →" = amazon.com/s?k=<words>&rh=p_n_deal_type:210906366011&tag=<our tag>
- Deal Checker: lead button "See similar deals on Amazon →" (discounts
  filter 23566065011, product name up to six words) with our tag.
Nothing live checks any of this. tests/buttons_to_tools_browser.cjs and
tests/tool_pages_browser.cjs cover it only on local fixtures.

## Do
1. scripts/site_check.js: add ONE new walk-through step "Tools" (next free
   step number; desktop and phone, same `step()` helper and reporting):
   - On the home page (already loaded by earlier steps; re-goto only if
     needed), fill #q with "cupping set", then assert the analyze link
     (#analyze-search) href === "/amazon-deal-checker/?q=cupping%20set"
     (use encodeURIComponent of the words, as the site does) and the
     visible All Discounts link (desktop #search-discounts, phone
     #search-discounts-mobile — pick the visible one) href ===
     "/amazon-discount-finder/?q=...". No target attribute. Clear #q after.
   - For each tool page: page.goto(SITE + "amazon-deal-checker/?q=cupping%20set")
     and the finder equivalent (one navigation each, same request guard
     and site request budget as other steps); wait for the run to finish
     (#tool-result visible; allow up to 40 s); check window.scrollY stays
     0 (no page jump); read `#tool-result .amazon-button` href and verify
     with a pure helper in scripts/site_check_logic.cjs: https, host
     amazon.com or www.amazon.com, path /s, k non-empty, exactly one tag
     equal to the config amazon tag (reuse amazonTag()), and for the
     finder rh contains p_n_deal_type:210906366011. NEVER click it and
     never request amazon.com.
   - Searches must use the existing robot search stub
     (scripts/robot_search.cjs routeRobotSearch(page, true) — the
     "cupping set" fixture) so no live /api/search call or Keepa token is
     spent. Check how live_first_screens.js uses it and do the same.
   - Keep the site check inside its 15-minute job limit: measure the
     added time locally and say it in your reply.
2. scripts/live_first_screens.js line ~53: `noPhoto: count(d => !d.image)`
   reports every deal as photoless (live: deals 1439, noPhoto 1439) —
   deals.json uses a different photo field. Count photos the same way
   site_check.js / site_check_logic.cjs photoRules/checkPhotos read them.
3. Tests: pure helper cases in tests/site_check_logic.cjs (good finder
   URL, good checker URL, wrong host, two tags, wrong tag, missing rh on
   finder, http). A local real-Chromium harness for the new step is
   welcome if it can reuse tests/tool_pages_browser.cjs buildFixture
   (operator runs Chromium: `NODE_PATH=/opt/node-tools/node_modules node
   tests/<x>.cjs /opt/pw-browsers/chromium`). If a pytest wrapper runs the
   .cjs logic tests, keep it green. Run the targeted tests; do NOT run the
   full suite (the operator does).

## Don't
- No site/template/config changes; no new dependencies; no headless
  logins; no requests to any host but our own site.
- Don't touch scripts/visitor_watch*.js (another change is in flight).

Reply with: files changed, what each check asserts, added site-check
seconds (measured), and test commands + results.
