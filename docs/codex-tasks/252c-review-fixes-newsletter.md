# 252c — Best-deals newsletter (task 247): fix the 12 confirmed review findings (ships off)

## Why

Task 247 built the newsletter and changed the subscribe Worker. An adversarial
review confirmed the findings below (probes on the real code). The newsletter is
off; it must be safe before it is ever switched on. Parallel tasks 252a/252b work
on the checker pages and the AI take — do not touch dealradar/publish/ai_take.py,
checker*.py, product_pages.py or their tests.

## Decisions (engineer of record, Claude)

- **No Amazon anywhere in any email** (operator's Amazon call, ref amz378_998;
  Associates rules on email): render source credits by display name; exclude
  deals for Amazon-owned products/marks sold elsewhere (Echo, Alexa, Kindle, Fire
  TV, Ring, Blink, eero, Fire tablet) when the item text names them; add a
  fail-closed scan of subject + text + html for every excluded store token
  (amazon, amzn., a.co/, ebay) that skips the send. The member digest/alerts
  follow the same rule.
- **eBay excluded** by unwrapped URL and by any excluded store token in item or
  store text; `exclude_stores` validation accepts only names the classifier can
  match.
- **Links only to written pages:** select deals whose `/deal/` page was written
  this publish (no remembered-but-unwritten paths).
- **Double opt-in must need the person:** GET /confirm shows a page with one POST
  button and writes nothing; POST /confirm confirms. You MAY change exactly the
  /confirm GET/POST assertions in tests/test_workflows.py (narrow, documented
  exception to the usual rule); change nothing else in that file.
- **Unsubscribe/confirm endpoints never reveal whether an address is
  subscribed** (same response either way) and keep the existing rate limits.
- Worker README and docs must state the real legacy-record behavior.

## Rules

The Worker deploy is dispatch-only: do not deploy. No external calls, no email
sent. Run tests you touch plus tests/test_newsletter.py,
tests/test_newsletter_policy.py, tests/test_partner_policies.py and the Worker's
node tests, with -n 1 and your own basetemp /tmp/task252c-*; delete it after. Do
not run the whole suite. Do not commit. Update docs/HANDOFF.md "In flight" with
one entry. A scratch diff from the reviewer exists at
/tmp/claude-0/-home-user/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/p247/fixes.diff
(read-only reference; verify before reusing).

## Confirmed findings — task 247

## [major] Newsletter credits print raw source IDs, so 'via slickdeals_amazon' / 'via kcl_wall_amazon' / 'via hip2save_amazon' puts the Amazon name into the email | dealradar/publish/newsletter.py:289
VERIFIER: Confirmed by a probe on HEAD. Sightings store the raw source id: fetchers set source_name=source.name and dedupe.py:327 passes it to add_sighting. newsletter.py:231 and :289 print it unchanged. A Walmart deal with a sighting from kcl_wall_amazon, hip2save_amazon or slickdeals_amazon passes eligible_deals, and the text contains '   via kcl_wall_amazon', so 'amazon' appears in the body. All of these ids have display names without 'amazon' (Slickdeals, Hip2Save, The Krazy Coupon Lady, Southern Savers). Side note: tests/test_e2e_offline.py::test_ai_take_rerun_is_idempotent already fails on HEAD fo
FIX: In newsletter.py, add `from .credits import source_display_name`. In _row_html and in the text loop, replace `for src_name, _ in email_credits(...)` with `for src_name in dict.fromkeys(source_display_name(n) for n, _ in email_credits(d)):`. Leave email_credits unchanged. This breaks tests/test_partner_policies.py:128, which must change to `"via Hip2Save" in body`. Add a regression test: a Target deal credited to hip2save_amazon, kcl_amazon and kcl_wall_amazon has no 'amazon' in subject, text or html, and 'via The Krazy Coupon Lady' appears once. I applied these edits in a scratch copy and the newsletter, newsletter_policy, partner_policies, money_math, unconfirmed_label and seo_deal_pages tests pass (303); the diff is at /tmp/claude-0/-home-user/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/p247/fixes.diff. The extra fail-closed body scan is optional: the shipped ftc_disclosure has no 'Amazon', so it would not block sends today.

## [major] GET /confirm confirms the address, so link-scanning mail gateways can complete double opt-in without the person | cloudflare/subscribe-worker/subscribers.js:77
VERIFIER: The behavior is real: subscribers.js:77 writes confirmed:true on a plain GET, while unsubscribe deliberately needs a POST. It is not a 247 regression, though. The code predates 247, and before 247 legacy records counted as confirmed with no gate at all. Brief item 7 keeps GET /confirm. The protected harness pins it: tests/test_workflows.py:1012-1013 and :1109 assert that GET /confirm returns {status:200, confirmed:true}. The suggested fix would therefore break a protected test that the brief forbids changing. Duplicate of the later 'Pre-existing: GET /confirm' finding; severity is lower than '
FIX: Do not change this in 247. Queue a follow-up that changes the protected harness: GET /confirm renders a page with one `<form method="post" action="/confirm?e=..&t=..">` button and makes no KV write. POST /confirm does the putSub(confirmed:true) with no TTL. Update worker.test.mjs and test_workflows.py so they POST.

## [minor] eBay exclusion relies only on the store_key host or an exact 'ebay' store name; variants behind kept trackers, and 'eBay' in item text, still reach the email | dealradar/publish/newsletter.py:142
VERIFIER: Confirmed by a probe on HEAD. store_of uses the raw canonical_url, and store_key of cc.dealsofamerica.com is 'other'. The store-name fallback only matches when the name normalizes to exactly 'ebay'. So an eBay deal behind a Deals of America tracker with store 'ebay.com' is kept, and so is 'eBay Daily Deals'. Trackers are deliberately kept as canonical_url (repair_competitor_trackers: false), and tracker_target already decodes cc.dealsofamerica.com. The text check looks only for 'amazon'.
FIX: In eligible_deals, `from ..http_client import tracker_target` (module level), then `url = deal.get("canonical_url") or ""` and `if (store_of(dict(deal, canonical_url=tracker_target(url) or url)) in excluded or email_deal(deal, cfg)["email_hide_price"]): continue`. Change the text test to `if any(s in text.casefold() for s in excluded): continue`. Add a parametrized test: ('ebay.com', a cc.dealsofamerica.com tracker to ebay.com) and ('eBay Daily Deals', https://www.example.com/x) both give eligible_deals == []. Verified in the scratch diff.

## [minor] exclude_stores values that store_of never returns are accepted and then silently ignored | dealradar/config.py:1110
VERIFIER: Confirmed by a probe on HEAD. With exclude_stores [amazon, ebay, 'best buy', costco], a bestbuy.com deal and a costco.com deal both pass eligible_deals. store_of only returns checker.HOSTS values (amazon, bestbuy, ebay, walmart, woot, target) or 'other'. An exclusion that silently does nothing is a fail-open config. Duplicate of the later config.py:1106 finding.
FIX: In config.py, right after the 'must include amazon' check, add: `from .publish.checker import HOSTS  # lazy: the store keys store_of() returns`, then `unknown = sorted(set(cfg.newsletter_exclude_stores) - set(HOSTS.values()))` and `if unknown: raise ValueError(f"newsletter.exclude_stores has unknown store(s) {unknown}; use one of {sorted(HOSTS.values())}")`. Add `({"exclude_stores": ["amazon", "Best Buy"]}, "exclude_stores")` and `({"exclude_stores": ["amazon", "costco"]}, "exclude_stores")` to test_invalid_config_rejected. Verified: the shipped config still loads as ['amazon','ebay'] and test_newsletter_policy passes (39).

## [minor] Amazon's own products and marks (Echo, Alexa, Kindle, Fire TV) sold by other retailers pass the filter | dealradar/publish/newsletter.py:144
VERIFIER: Confirmed by a probe: a Best Buy deal 'Echo Dot (5th Gen) Smart Speaker with Alexa' passes eligible_deals and would be emailed with price and percent. The code matches brief item 2 literally, since the only text test it specifies is 'amazon'. But it misses the brief's stated rule ('no Amazon product ... name, mark'), and Echo Dot is the brief's own example product. This is a policy gap, minor, and the operator should confirm the scope, because 'Alexa' also appears on third-party 'works with Alexa' devices.
FIX: Pending operator confirmation, extend the text test in eligible_deals with an anchored mark list instead of bare substrings, e.g. `AMAZON_MARKS = re.compile(r"\b(amazon|alexa|kindle|echo (?:dot|show|pop|spot|studio)|fire ?tv|fire (?:hd|max)|eero)\b", re.I)` and `if AMAZON_MARKS.search(text): continue`. Do not use bare 'echo', 'ring' or 'blink'. No current newsletter, partner-policy, money-math or unconfirmed-label fixture uses these words. Add a fixture 'Echo Dot ... at Best Buy' that is dropped.

## [minor] The digest (and member alerts) still email Amazon product, percent, store name and credit | dealradar/publish/email_digest.py:48
VERIFIER: Confirmed by reading the code: build_digest and build_member_email both use telegram_pub.format_message. For Amazon deals it hides only the price and keeps '(NN% off) at Amazon', the raw credit id and the article URL. This is the row format the brief cites. It is pre-existing and outside 247's task list, which covers the newsletter only. 247 deliberately kept it: test_partner_policies.py::test_email_keeps_source_tracker_exactly still asserts the Amazon tracker link appears in member and digest bodies. Both paths are off today (members.email_enabled: false, digest.recipient: ""), and the digest
FIX: Not in 247; applying it would contradict existing pinned tests. Queue a follow-up: move the newsletter exclusion into email_policy (e.g. `email_excluded(deal, cfg)`), apply it in send_member_alerts and maybe_send_digest before format_message, update test_email_keeps_source_tracker_exactly for member and digest, and add a no-'amazon' assertion for both.

## [minor] Legacy-record change is invisible to operators: the Worker README still says legacy records count as confirmed, and the live Worker keeps auto-confirming them until someone redeploys it by hand | cloudflare/subscribe-worker/README.md:41
VERIFIER: Confirmed. README.md is not in the 247 diff. README.md:41 ('Records written before double opt-in (no `confirmed` key) count as confirmed.') and :128 ('no `confirmed` key = confirmed (pre double opt-in)') are stale. So are docs/paywall.md:576-578 ('counts as confirmed') and :594-597, which the finding did not list. README.md:44 does not mention `ts` on pending rows, :45 does not mention the TTL, and :221 still says 'daily email'. deploy-subscribe-worker.yml is workflow_dispatch only. If the operator enables confirmations or the newsletter before redeploying, the old Worker's isConfirmed serves 
FIX: Docs only. README.md:41: replace the last sentence with 'Only `confirmed: true` counts; records written before double opt-in are pending and get one confirmation email.' README.md:128: replace the trailing clause with 'only `confirmed: true` is confirmed; a pending record expires CONFIRM_EXPIRE_DAYS (default 30) after confirm-sent'. README.md:44: append '`?pending=1` rows are `{email, token, ts}`'. README.md:45: append 'and re-puts the record with expirationTtl = CONFIRM_EXPIRE_DAYS×86400 (once; retries do not extend it)'. README.md:221: say the email needs `newsletter.enabled` and `mailing_address`. docs/paywall.md:576-578 and :594-597: same correction. In HANDOFF's 247 entry, add 'Re-run deploy-subscribe-worker BEFORE setting confirmations_enabled or enabled; the old Worker treats legacy sign-ups as confirmed.' Optionally add `CONFIRM_EXPIRE_DAYS = "30"` to wrangler.toml [vars]. Skip the optin code guard (scope creep).

## [minor] Pre-existing: GET/POST /unsubscribe reveals whether an address is subscribed, with no rate limit | cloudflare/subscribe-worker/subscribers.js:65
VERIFIER: Confirmed by reading the code, and the protected harness itself shows the oracle. fromLink returns {email} when there is no record, so an unknown address gets 200. A known address with a wrong token gets null, which is a 403. test_workflows.py:1118 asserts unsub_wrong 403 and :1123 asserts unsub_again 200 after deletion. This is pre-existing (fromLink is untouched by 247) and low impact. The proposed in-scope rate limit is unsafe: RFC 8058 one-click POSTs come from mail-provider servers (Gmail, Yahoo) on a few shared IPs, so overLimit(request, 'unsubscribe', 30) would return 429 for real unsub
FIX: Not in 247. Do not add overLimit to POST /unsubscribe. Queue a follow-up to make unknown-address and wrong-token responses identical (for example, tokens derived as HMAC(NEWSLETTER_SECRET, email)), which requires updating the protected harness assertion at test_workflows.py:1118.

## [minor] Pre-existing: GET /confirm changes state, so link-scanning mail gateways confirm addresses the recipient never confirmed | cloudflare/subscribe-worker/subscribers.js:77
VERIFIER: Same issue as the second finding, and correctly labeled here. subscribers.js:77 confirms on GET; the code predates 247 and 247 does not make it worse. The protected test_workflows.py (:1012-1013, :1109) asserts that GET /confirm sets confirmed:true, so it cannot change in 247.
FIX: Follow-up task only: GET /confirm renders a one-button POST form and makes no KV write; POST /confirm does the write with no TTL; update worker.test.mjs and the test_workflows harness to POST.

## [major] Deals with a remembered path but no written page (locked or rotated out) are selected and linked, so the email can point to 404 pages and send member-only deals | dealradar/publish/newsletter.py:148
VERIFIER: Reproduced with the tests/test_seo fixtures (paywall on, teaser_count 2): after render twice, eligible_deals returned k0 and k1 (the top two ranked deals) with /deal/zzitem-0-d1a5ac9a/ and /deal/zzitem-1-6ab9f1eb/, and neither page exists. seo._deal_pages:896-919 keeps `remembered` for every deal still in the DB but writes folders only for `rows` and deletes the rest. In production, published deals carry no deal_path, so every newsletter path comes from this remembered map. render_site always runs before send_daily_newsletter (pipeline.py:2690 vs :2792), so the written pages exist on disk at s
FIX: In eligible_deals, after the existing path validation, add: `# Remembered paths outlive their pages (locked or rotated out).` and `if not deal.get("deal_path") and not (Path(cfg.site_dir) / path.strip("/") / "index.html").is_file(): continue`. This mirrors social._candidates. Update test_links_credits_thumbnails_and_missing_pages to set `cfg.site_dir = str(tmp_path)` and write `tmp_path/deal/written-34567890/index.html`. Add a test where meta holds 'gone' and 'here' paths, only 'here' has an index.html, and eligible_deals returns only 'here'. Verified: the new tests fail on HEAD and pass with the fix; the 303 targeted tests pass; test_rerun_is_idempotent passes.

## [major] Source credits print the raw source id, so an email can contain the word "amazon" (e.g. "via hip2save_amazon") | dealradar/publish/newsletter.py:289
VERIFIER: Duplicate of the first finding; reproduced the same way. A Target deal with a hip2save_amazon sighting renders '   via hip2save_amazon'. site.py maps credits through _display_names, but the newsletter does not.
FIX: Same fix as the first finding: render `dict.fromkeys(source_display_name(n) for n, _ in email_credits(d))` in both _row_html and the text loop. Change tests/test_partner_policies.py:128 to 'via Hip2Save'. Add the hip2save_amazon/kcl_amazon/kcl_wall_amazon regression test. See /tmp/claude-0/-home-user/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/p247/fixes.diff.

## [minor] newsletter.exclude_stores accepts store names that can never match, so they exclude nothing | dealradar/config.py:1106
VERIFIER: Duplicate of the fifth finding; confirmed with a probe ('best buy' and 'costco' load without error but exclude nothing).
FIX: Same fix as the fifth finding: in load_config, lazily import checker.HOSTS and raise ValueError for any entry not in HOSTS.values(). Add the 'Best Buy' and 'costco' cases to test_invalid_config_rejected.

REJECTED:
- Confirmation emails have no unsubscribe link and no List-Unsubscribe headers :: No rule here requires this. The brief requires List-Unsubscribe only on newsletter messages ('Every newsletter message carries...'). For confirmations it asks only for cadence wording, the sign-up dat