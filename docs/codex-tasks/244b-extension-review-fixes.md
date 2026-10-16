# 244b — Browser extension: fix the 15 confirmed review findings

## Why

Task 244 built the extension. An independent review (real Chromium probes) confirmed
the findings below. Fix all of them in this tree (it already contains tasks 240,
241, 244, 245 and 250). Decisions from the engineer of record (Claude):

- **The extension is ON after install.** The operator's users install it to see
  our answer; the global switch defaults to enabled. Keep the per-site "Hide on
  this site" and the global off switch, and list hidden sites in the popup with
  an "Unhide" button for each.
- **Amazon: full.** Amazon allowed the extension to show our verdict and checked
  price on Amazon pages (operator call, reference amz378_998). Make the Amazon
  mode a knob in config.yaml (`extension: {amazon_mode: full}`, values
  `full | link`, validated in config.py) read by `build-extension`; docs and
  comments must agree with it everywhere.
- **Walmart: history** (operator call 2026-10-16); eBay stays live-only because
  listings end.
- **SPA navigation:** watch `location.href` (popstate + a 1 s interval that only
  compares strings) and refresh/clear the card; never read page DOM.
- Store kit: generate the 440x280 small promo tile (and keep the 1280x800
  screenshots) from the local fixture with render.cjs; reviewer test
  instructions in PUBLISH.md; Chrome summary = manifest description, kept
  identical to listing.md's short description; no stale "blocked"/"link-only"
  statements anywhere.

Use the verifier's exact fixes below unless they conflict with a decision above.

## Rules

Only touch extension/, dealradar/build_extension.py, dealradar/cli.py,
dealradar/config.py + config.yaml (the `extension:` knob only), tests for the
extension (tests/test_browser_extension.py, tests/extension_*.cjs) and the
privacy section of the privacy template if a disclosure must change. Real
Chromium is available here at /opt/pw-browsers/chromium: run the real-browser
test too. Tests with -n 1 and your own basetemp /tmp/task244b-*; delete it
after. Do not run the whole suite. Do not commit. Rebuild rules.json and the zip
with `python -m dealradar build-extension` at the end.

## Confirmed findings (with verified fixes)

## [major] Card is never re-evaluated after an in-page URL change, so product A's price, verdict and link stay on product B | extension/content.js:3
FIX: extension/content.js line 3: `let href = location.href;`. Before the final `refresh().catch(remove);` add:
```js
  // Retailers switch products with history.pushState/replaceState; content scripts cannot hook those.
  function moved() {
    if (location.href === href) return;
    href = location.href; dismissed = false; refresh().catch(remove);
  }
  addEventListener("popstate", moved);
  setInterval(moved, 1000);
```
The harness must change too, or content.js throws in the vm. In tests/extension_logic.cjs: add `interval = () => {}` to the `let` on line 30. Add `addEventListener(){}, setInterval:fn => {interval = fn; return 0;}` to the vm.createContext globals (line 40). Change line 61 to `context.location = {href};`. Return `tickURL:async next => {context.location.href = next; interval(); await settle();}` from harness(). Then add a test: rows {bestbuy:6522738: entry, bestbuy:1234567: {...entry, verdict:"OTHER"}}, tickURL to /site/other/1234567.p?skuId=1234567 shows OTHER and not entry.verdict, and tickURL to /site/searchpage.jsp?st=x leaves documentElement with 0 children. In tests/test_browser_extension.py:148, update the expected pass count. I verified the new test fails on the original code and passes with the fix.

## [minor] Session shard cache never removes expired entries: it outlives the disclosed 30 minutes and breaks lookups once storage.session's 10 MB quota is full | extension/background.js:25
FIX: Replace background.js line 25 with:
```js
    if (storage) {
      // Best effort: session storage has a quota, so drop expired shards first and still answer if the write fails.
      try {
        const all = await Wowpriced.api(storage, "get", null);
        const stale = Object.keys(all).filter(k => k.startsWith("shard:") && !(all[k] && all[k].until > Date.now()));
        if (stale.length) await Wowpriced.api(storage, "remove", stale);
        await Wowpriced.api(storage, "set", {[key]: record});
      } catch (_) { /* cache only */ }
    } else memory[key] = record;
```
Harness changes (tests/extension_logic.cjs): add a `sessionFull = false` option. The storage `get` must start with `if (keys === null) return callback({...data});`, because `Object.keys(null)` throws today. Add `remove(keys, callback) {for (const key of [].concat(keys)) delete data[key]; callback();}`. `set` must start with `if (area === "session" && sessionFull) {runtime.lastError = {message:"quota"}; callback(); delete runtime.lastError; return;}`. Add a test: sessionData {"shard:zz":{until:epoch-1,rows:{}}}, then lookup() removes shard:zz and leaves 1 key; harness({sessionFull:true}).lookup() still returns entry.verdict. I verified the test passes with the fix and fails without it. Bump the pass count at test_browser_extension.py:148.

## [major] No 440x280 small promo tile in the listing kit; Chrome Web Store lists it as mandatory | extension/store/PUBLISH.md:34
FIX: Add extension/store/promo.html: the brand mark (same inline SVG as card.js) and 'Is this a good deal?' on the brand teal, with no retailer marks or prices. In render.cjs, after the screenshot loop, add: `await page.setViewportSize({width:440,height:280}); await page.goto(origin + "/extension/store/promo.html"); await page.screenshot({path:path.join(__dirname,"screenshots","promo-440x280.png")});`. Add to PUBLISH.md Chrome step 4: 'and upload screenshots/promo-440x280.png as the Small promo tile (required).' build_extension already excludes store/ from the zip.

## [minor] Firefox strict_min_version 121 is too low: 121-126 never inject the content script, and AMO validation shows data_collection_permissions warnings | extension/manifest.json:8
FIX: manifest.json: `"strict_min_version": "128.0"`, the first ESR where MV3 host permissions are granted at install. Use "140.0" instead if you also want to clear the data_collection_permissions linter warnings. Update tests/test_browser_extension.py:88 to the same value, and change 'Firefox 121'/'Firefox 121+' in PUBLISH.md:49 and :53 to match. Add gecko_android only if publishing for Android.

## [minor] The Amazon off-switch instructions don't work: the knob is hard-coded with a comment saying the opposite, and Amazon wording contradicts itself across docs | dealradar/build_extension.py:21
FIX: build_extension.py:21 → `# Amazon OK'd verdict + checked price in the extension (amz378_998 follow-up, 2026-10-16); set "link" here and in tests/test_browser_extension.py if Amazon objects.` PUBLISH.md:6-9 → 'Amazon allowed the extension to show our verdict and checked price (amz378_998 follow-up; HANDOFF Operator decisions), so `rules.json` exports `AMAZON_MODE: "full"`. Amazon shard entries carry no price until `checker.stores.amazon` may be `history` (config.py `validated_checker` rejects it today), so Amazon cards show name + link only. If Amazon objects, set "link" in dealradar/build_extension.py and the pin at tests/test_browser_extension.py:112, then rerun `python -m dealradar build-extension`.' In the HANDOFF entry, replace 'Amazon stays name/link-only.' with 'AMAZON_MODE full (Amazon OK); Amazon cards stay name/link until checker.stores.amazon allows history.'

## [minor] Chrome Web Store reviewers get no instructions for a default-off extension whose card appears only for indexed products | extension/store/PUBLISH.md:37
FIX: Add a Chrome step after step 5: "Open **Test instructions** and paste: 'The extension starts off. Click the toolbar icon (puzzle menu) and tick Turn on wowpriced. Then open <a product URL present in /check/data, chosen at submission time>; a card appears bottom-right. Or paste that URL into the popup and click Check.'" Put the same concrete URL in the Firefox step 7 reviewer notes.

## [minor] PUBLISH.md says to set the Chrome summary from listing.md, but Chrome takes it from the manifest description, which differs | extension/store/PUBLISH.md:34
FIX: Set listing.md's 'Short description' to the manifest description verbatim: 'Check product links against prices wowpriced has verified. No page scraping, browsing log or affiliate link changes.' Change PUBLISH.md:34-35 to: 'Name and summary come from the package manifest; set the detailed description from `listing.md`, choose…'.

## [minor] Screenshot README and PUBLISH.md 'Sandbox status' still say the PNGs are not generated, but both 1280x800 PNGs are already present | extension/store/screenshots/README.md:3
FIX: After re-rendering per finding 5, replace README.md lines 3-4 and the PUBLISH.md 'Sandbox status' paragraph with: 'price-check.png and amazon-check.png are rendered by render.cjs from the local fixture.html; rerun after any card change.' Keep the Pillow icon note. In the HANDOFF entry, replace 'Chromium launch is sandbox-blocked: real-browser QA and two 1280×800 screenshot PNGs remain' with 'Chromium launches here; tests/extension_browser.cjs passes and both 1280×800 PNGs are rendered', and drop '3 browser skips'.

## [major] SPA navigation leaves the previous product's card up (wrong verdict and price on a different product) | extension/content.js:3
FIX: Same as finding 2: the content.js change plus the harness changes (setInterval/addEventListener globals, mutable location, tickURL helper, new test, pass-count bump at test_browser_extension.py:148). Optionally add a browser step in tests/extension_browser.cjs: pushState to /site/other/1234567.p?skuId=1234567 (a miss), then poll the AX tree until entry.verdict is gone. Document entry by client routing from non-product pages as a known limitation in PUBLISH.md.

## [major] Extension is inert after install: default-off, with no install prompt or badge | extension/content.js:9
FIX: Keep opt-in, but open the switch once. In background.js add: `chrome.runtime.onInstalled.addListener(details => { if (details.reason === "install") chrome.tabs.create({url: chrome.runtime.getURL("popup.html")}); });`. tabs.create needs no tabs permission in Chrome or Firefox, and popup.js already tolerates a tab with no content script. In tests/extension_logic.cjs:33, add `onInstalled:{addListener(){}}` to the runtime stub, or background.js throws on load and every logic test fails. Defaulting to on (option A) is an operator decision. It would change privacy.html/listing.md/PUBLISH.md 'starts off' and the 'off by default' test.

## [minor] Real-browser test's 'no request goes anywhere except wowpriced' check never sees the extension's own requests | tests/extension_browser.cjs:65
FIX: tests/extension_browser.cjs: before line 3 add `// Extension service-worker fetches reach context "request" events only with this flag.\nprocess.env.PW_EXPERIMENTAL_SERVICE_WORKER_NETWORK_EVENTS = "1";`. After line 65 add `assert(outgoing.some(address => /^https:\/\/wowpriced\.com\/check\/data\/[a-f0-9]{2}\.json$/.test(address)), JSON.stringify(outgoing));`, which makes the allowlist non-vacuous. Keep line 20's narrow resolver rules. Optionally add page.on("pageerror") and page.on("console") collectors and assert both are empty. tests/extension_logic.cjs: add a `status = 200` harness option, return `{ok: status === 200, text: …}` in the fetch stub, and add `{status:404}` to the options list at line 121.

## [minor] Session-cache write failure discards a successfully fetched shard; expired keys are never pruned | extension/background.js:25
FIX: Same as finding 3: prune expired shard:* keys, then set, all inside try/catch, and the cache stays best effort. Also add the harness get(null), remove and sessionFull support plus the new test. Prefer pruning over `clear` on failure, so live cache entries survive.

## [minor] 'Hide on this site' can only be undone from a product page of that site; hidden sites are listed nowhere | extension/popup.js:25
FIX: popup.html: after #site-row add `<div id="hidden-sites"></div>`. At the end of paint() in popup.js add:
```js
    const list = document.getElementById("hidden-sites"); list.replaceChildren();
    for (const host of Object.keys(settings.hiddenSites).filter(h => settings.hiddenSites[h] === true && h !== site)) {
      const b = document.createElement("button"); b.type = "button"; b.textContent = "Show on " + host;
      b.addEventListener("click", async () => { const latest = await api(chrome.storage.sync, "get", {hiddenSites: {}}); await api(chrome.storage.sync, "set", {hiddenSites: {...latest.hiddenSites, [host]: false}}); });
      list.appendChild(b);
    }
```
Extend popup test 13 with a harness whose tabs.sendMessage calls back with lastError set: a pre-hidden host renders a 'Show on <host>' button, and clicking it sets hiddenSites[host] to false.

## [minor] Store Amazon screenshot and listing promise a verdict and price that the shipped data never contains for Amazon | extension/store/fixture.html:7
FIX: Same as finding 5. Make the fixture.html:7 Amazon model {verdict:"", detail:"", at:""}, re-render amazon-check.png, and add the Amazon/Walmart/eBay sentence to listing.md. Re-render with the full Amazon card only after a separate reviewed change lets checker.stores.amazon be history.

## [minor] Stale or contradictory docs: Amazon 'link-only' and 'Chromium blocked / no screenshots' statements are false for this tree | extension/store/PUBLISH.md:59
FIX: Apply the finding 8 comment and PUBLISH.md:6-9 text. On PUBLISH.md:21 keep 'Amazon's name/link-only card (until the data carries Amazon prices)'. On PUBLISH.md:59 replace 'Amazon restriction' with 'Amazon mode (AMAZON_MODE full; Amazon data is name + link until checker.stores.amazon allows history)'. Replace the 'Sandbox status' section and the screenshots/README.md lines 3-4 with the render.cjs line from finding 13. Update the HANDOFF entry as in findings 8 and 13.

REJECTED:
- Extension ships AMAZON_MODE "full", which goes against the brief and a standing operator decision; Amazon prices and verdicts will appear on :: 
- Test and comment cite a Walmart history approval that HANDOFF says is still open; the extension has no live-only guard for Walmart :: Refuted. The live branch (90ae0ed) replaced the 'Walmart (open)' entry with 'Walmart price history and alerts (2026-10-16, operator spoke to Walmart): Walmart said it is fine to show Walmart price his
- AMAZON_MODE "full" never takes effect, so the Amazon screenshot shows a card the extension cannot produce (Walmart is in the same position) :: 
- listing.md says 'no minification', but popup.css and the card stylesheet are single long lines :: Not substantiated. Both are hand-written CSS with readable property names, and there is no minifier or build step. The project has no bundler, and the zip contains the exact authored files. AMO's sour
- The button's ?ref=ext attribution marker is not disclosed alongside the 'no analytics' claims :: Not a disclosure defect. The extension itself collects and sends nothing. ?ref=ext is required by the brief (task 244 line 41) and is only sent when the user clicks through to wowpriced. From there th
- Popup's paste-a-link check refuses to work unless the automatic page cards are also switched on :: The behavior is confirmed (popup.js:40-41, background.js:134), but it is a deliberate, tested and clearly messaged design, not a defect. extension_logic.cjs test 13, 'stays quiet while off', asserts i