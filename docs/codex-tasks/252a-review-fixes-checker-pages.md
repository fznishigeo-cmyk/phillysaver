# 252a — Deal Check page, product-page cap, Walmart history, photos: review fixes (ships off)

## Why

This tree already contains tasks 240, 241 (Deal Check page), 244/244b (extension),
245 (AI take), 246 (Android), 247 (newsletter) and 250 (store policy + bounded /p/).
Independent reviews (real repro) confirmed the findings below for 241 and 250.
Fix them here. A parallel task (252b) fixes 245 (dealradar/publish/ai_take.py and its
tests) — do not touch ai_take.py or tests/test_ai_take.py.

## Decisions (engineer of record, Claude; operator instructions 2026-10-16)

1. **"AI" may appear in public copy.** Operator: "AI sells." Remove `AI` from the
   banned-words regex in tests/test_public_copy.py with a one-line comment
   (operator 2026-10-16). Keep every other banned word (Keepa, Claude, Codex, LLM,
   Python, Cloudflare, Worker, GitHub, ...). Reword the checker.js comments that say
   "Python" (finding 1).
2. **Walmart: history.** The operator spoke to Walmart (2026-10-16): price history,
   lowest price and alerts are fine. `CHECKER_STORES["walmart"] = "history"`; the
   checker validator restricts history only for eBay (listings end); ship
   `checker.stores.walmart: history` in config.yaml; the /check/ page copy says
   "eBay results cover live deals only, because listings end." Update tests that
   assert Walmart is live-only (test_checker_index.py, test_checker_page.py and
   any other) to the new rule; keep an equivalent eBay assertion.
3. **Amazon kill switch must keep working** (finding "Flipping the Amazon kill
   switch"): if `amazon_history: hide` while `checker.stores.amazon: history`, do not
   fail config load; treat Amazon as link_only in the checker instead and log a
   private note. Test both.
4. **Photos** (site check 2026-10-16 21:07Z: 89.97% of live deals show a photo,
   floor 90%): in `scripts/site_check_logic.cjs` format the percentage with
   two decimals so the message never reads "90.0% < 90%". In config.yaml raise
   `images.keepa_search_per_run` and `keepa_search_per_day` only as far as the
   Keepa token cost per search call allows within ~3,000 tokens/day (read the
   Keepa search code to find the per-call token cost; write the arithmetic in a
   config comment). Do not change `site_check.min_photo_pct`.

## Rules

Evidence or it doesn't exist; math stays in savings.py; no Keepa/LLM/retailer
calls in tests. Do not change tests/test_workflows.py. Run the test files you touch
plus tests/test_checker_index.py, tests/test_checker_page.py,
tests/test_store_policy_pages.py, tests/test_own_price_checks.py,
tests/test_public_copy.py, tests/test_product_price_pages.py with -n 1 and your own
basetemp /tmp/task252a-*; delete it after. Real Chromium is at
/opt/pw-browsers/chromium; the checker browser test must pass here. Do not run the
whole suite. Do not commit. Update docs/HANDOFF.md "In flight" with one entry.

## Confirmed findings — task 241 (Deal Check page)

## [blocker] The full test suite fails: checker.js mentions 'Python' (a banned word for public pages), and the enabled page shows both 'Python' and 'AI' | dealradar/publish/templates/checker.js:31
FIX: checker.js:13-14: change to `// WHATWG URLs normalize paths, Unicode and default ports; the publisher\n    // preserves those spellings. Use the raw components after validating the address.` checker.js:31: change to `// The publisher's \d accepts Unicode decimal digits; JavaScript's \d is ASCII.` For 'AI-powered', the brief's wording conflicts with the public-copy rule, so ask the operator which wins. If the wording stays, add a checker-enabled render to test_public_copy that removes only the escaped checker_page.INTRO text from check/index.html before the INTERNAL scan. Then run the full suite with -n 2 and put the real pass/fail line in the HANDOFF entry.

## [major] Results from any other retailer show the literal store label 'other' | dealradar/publish/templates/checker.js:115
FIX: In checker.js run(), call `show(rows[alias], parts)`. In show(entry, parts): `const label = stores[entry.store] || (entry.store === "other" && parts ? parts.host.replace(/^www\./, "") : ""); const meta = text("p", label ? label + " · " : "", "check-meta");`. Add an add(db,'other') row to test_checker_page.render(), and assert in checker_page_logic.cjs that the meta line starts with 'shop.example · checked'.

## [minor] Not-found search link searches for path keywords, tracking segments or ASINs instead of slug words | dealradar/publish/templates/checker.js:130
FIX: Replace the filter in notFound() with: `const skip = ["product","products","site","offers","itm","ip","dp","gp","ref","p","searchpage"]; words = decodeURIComponent(parts.path).split("/").filter(s => /^[\p{L}\p{N}]+(?:[-_+ ][\p{L}\p{N}]+)*$/u.test(s) && /[a-z]{3}/i.test(s) && !skip.includes(s.toLowerCase()) && !/^(?=.*\d)[a-z\d]+$/i.test(s)).sort((a, b) => /[-_+ ]/.test(b) - /[-_+ ]/.test(a) || b.length - a.length)[0] || "";`. I checked this filter: the eBay URL and the bare ASIN URL get no words (plain 'Search our deals'), the Amazon Echo URL gives 'Echo', the searchpage.jsp URL gives none, Some-Great-Blender gives 'Some Great Blender', and unverified-air-fryer and /products/widget give the same words as before. Add these URLs to checker_page_logic.cjs.

## [minor] Typing during a lookup leaves 'Checking our verified records…' on screen indefinitely | dealradar/publish/templates/checker.js:166
FIX: In checker.js, declare `let sequence = 0, pending = false;`. In run(), set `pending = false;` right after `const id = ++sequence`, and set `pending = true;` after showing 'Checking our verified records…'. Add `finally { if (id === sequence) pending = false; }` to the try. Change the input listener to `input.addEventListener("input", () => {++sequence; if (pending) {pending = false; result.replaceChildren();}});`. Add the type-during-lookup case to checker_page_logic.cjs.

## [minor] Result card shows prices without crediting the source, although 240 ships `credit` for this | dealradar/publish/templates/checker.js:113
FIX: In show(), after the meta line: `const credits = (Array.isArray(entry.credit) ? entry.credit : []).filter(c => c && typeof c.name === "string" && c.name); if (credits.length) { const via = text("p", "via ", "check-meta"); credits.forEach((c, i) => { if (i) via.append(text("span", ", ")); if (typeof c.url === "string" && /^https?:\/\//i.test(c.url)) { const a = text("a", c.name); a.href = c.url; a.rel = "noopener"; via.append(a); } else via.append(text("span", c.name)); }); result.append(via); }`. This uses elements rather than strings so the logic-test fake DOM works. Assert 'via Slickdeals' in the logic and browser tests.

## [minor] /check/ footer differs from every other page: the PWA install link and iOS help are missing | dealradar/publish/site.py:1063
FIX: In site.py, move the `if cfg.checker["enabled"]: checker_page.publish(...); seo_paths.append("/check/")` block to just before the `if support and link:` loop, so the support, pwa, profiles and inventory passes apply to it. In tracking.apply_tracking, skip only this page: `if path.relative_to(out_dir).as_posix() == "check/index.html": continue` (click_signal does not inject there because the page has no getdeal). Add `manifest-src 'self'` to the CSP in checker_page.publish and update the comment at checker_page.py:74-75. Assert that the check page's wp-pwa-footer count equals p/index.html's when pwa is enabled.

## [minor] Privacy line 'Your link stays in this browser' is false once the page writes the link into ?u= | dealradar/publish/checker_page.py:38
FIX: checker_page.py:38: change the line to `<p id="check-privacy" class="check-meta">We look up only our own verified records. Your link is never sent to stores or other sites.</p>`. That statement holds once the ?q= tracking finding is fixed. Keep incoming ?u= prefill, since the brief requires it.

## [major] Amazon 'as of' and 'checked' time can come from a third-party post, not from an Amazon price confirmation | dealradar/publish/checker.py:211
FIX: In checker.build(), right after the `if not live:` price_history block and before `if at is None or (not live and at < cutoff): continue`, add: `if store == "amazon" and policy != "link_only":\n            confirmed = price_confirmed_at(facts if live else dict(facts, death=""))\n            if confirmed is not None and confirmed <= until:\n                at, price = confirmed, facts["final_price"]\n            elif live:\n                policy = "link_only"  # no confirmation: no price or 'as of'\n            else:\n                continue`. price_confirmed_at is already imported. Add a test where an add(db,'amazon', key=..., price_rechecked_at='', keepa_price_seen_at='') entry has no price, amazon_as_of or verdict. The existing Amazon test still passes, because its at is NOW live and NOW-1d ended, with price 10.

## [major] Deal Check shows prices from partner posts without crediting the source | dealradar/publish/templates/checker.js:103
FIX: Same fix as the earlier credit finding: render 'via <a rel="noopener">Name</a>' from entry.credit in show(), with http(s) URLs only and span or a elements built with textContent. Assert it in checker_page_logic.cjs and checker_page_browser.cjs.

## [minor] Flipping the Amazon kill switch (amazon_history: hide) now fails config load for every run | config.yaml:144
FIX: Same fix as the earlier amazon_history finding: document 'hide also needs checker.stores.amazon: link_only' at config.yaml:144 and next to checker.stores.amazon (line 282). Optionally name both keys in the ValueError message.

## [major] The not-found link puts words from the pasted URL into the home page's ?q=, where GA4, Clarity and the Meta Pixel read them | dealradar/publish/checker_page.py:94
FIX: Hand off through sessionStorage instead of the URL. In checker.js notFound(), set `a.href = "/";` and when there are words add: `a.addEventListener("click", () => { try { let m = JSON.parse(sessionStorage.getItem("wp.browse") || "null"); if (!m || m.version !== 1 || !m.entries || typeof m.entries !== "object" || Array.isArray(m.entries)) m = {version: 1, last: "", entries: {}}; const id = "check-" + Date.now().toString(36); m.entries[id] = {query: words}; m.last = id; sessionStorage.setItem("wp.browse", JSON.stringify(m)); } catch (_) { /* storage blocked: plain home */ } });`. browse.js initBrowse already restores entries[last].query. Delete checker_page.search_entry and its call in site.py (lines 980-981). Update the href assertions in the logic and browser tests (they now expect '/'), and keep the browser check that #q becomes 'widget' after the click. Add a browser case with tracking IDs set and stubbed third-party scripts, asserting no script sees 'q='.

REJECTED:
- Home ?q= entry can silently stop working; no Python test covers it :: The marker does appear twice (browse.js:166 in initBrowse and :282 in the popstate handler), but replace(...,1) picks the initBrowse copy, and the rendered index.html shows the entry in the right plac
- Setting amazon_history: hide on its own now breaks config loading on every run :: no verdict
## Confirmed findings — task 250 (store policy + bounded /p/)

## [blocker] An existing test breaks: test_own_price_checks::test_not_eligible_counts_each_page_once_and_stays_private passes on HEAD 90ae0ed and fails on the worktree | tests/test_own_price_checks.py:271
FIX: In tests/test_own_price_checks.py, replace line 271
    assert sum(report["not_eligible"].values()) + report["eligible"] == len(pages) == 4
with
    assert sum(report["not_eligible"].values()) + report["eligible"] == report["state_pages"] == 4
    assert len(pages) == 3 and report["not_written_old"] == 1

## [minor] amazon_history: hide output changed: product pages with a hidden Amazon offer and no live deal now get a photo and item/category/brand/conditions from the raw row, where HEAD blanked the photo | dealradar/publish/product_pages.py:181
FIX: In dealradar/publish/product_pages.py, inside the `if filtered:` loop, limit both the new trigger and the raw refill to offers that store_policy hides. Amazon-hide then keeps HEAD's behavior. Replace
            if key not in current and (hidden_offers.intersection(page["offers"])
                    or amazon_history.hidden(cfg, page) or not any(
                    stores[offer]["store"] == page.get("store") for offer in offers)):
                latest = max(offers, key=lambda offer: (offers[offer]["date"], offer))
                visible[key].update(store=stores[latest]["store"], image_url="")
                if latest in raw:
with
            # Only store_policy offers refill from the visible store; Amazon hide keeps its blank photo.
            refill = key not in current and any(amazon_history.live_only(cfg, stores[offer])
                                                for offer in hidden_offers.intersection(page["offers"]))
            if refill or amazon_history.hidden(cfg, page) or not any(
                    stores[offer]["store"] == page.get("store") for offer in offers):
                latest = max(offers, key=lambda offer: (offers[offer]["date"], offer))
                visible[key].update(store=stores[latest]["store"], image_url="")
                if refill and latest in raw:
After this change, the hide-mode repro matches HEAD (image_url ''), show mode is unchanged, and the walmart live_only mixed-page refill still works. tests/test_store_policy_pages.py, tests/test_product_price_pages.py and tests/test_own_price_checks.py together give 188 passed. Optionally, add a hide-mode test (Amazon+Target product, Amazon dead, Target off the wall) that asserts the /p/ page has no image, which pins the HEAD behavior. If the Target photo under hide is wanted instead, keep the code, record the change in HANDOFF and pin the new bytes with a test.

REJECTED:
