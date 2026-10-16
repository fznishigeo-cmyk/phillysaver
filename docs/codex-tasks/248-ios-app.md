# 248 — iPhone app: native Deal Check + Share Extension + the 244 extension in Safari (nothing uploads without operator secrets)

## Why

Operator, 2026-10-16: build apps that are good and that sell. The operator pays Apple's $99/yr when Claude says the app
is ready.

**App Review.** Guidelines 4.2 and 4.2.2 reject repackaged websites and deal aggregators. Forum reports from 2025–26 show
even partly native apps being rejected. So v1 is a small native SwiftUI app whose first screen is the tool. It adds two
things a website cannot do:
- A Share Extension: share a product from the Walmart, Target or Best Buy app and get the verdict right in the share sheet.
- Task 244's extension as a Safari Web Extension. From search results, Keepa-style and camelizer-style extensions are
  Mac-only or desktop-only, so the iPhone is open ground.

v1 has no WebView, no login, no push, no in-app purchase, no Support or membership links, and no analytics.

**Amazon.** Amazon Associates' Mobile Application Policy bans price tracking in apps that carry Associates links (seen
only in search excerpts; the operator's written ask, reference amz378_998, is pending). Amazon entries are therefore
link-only (`amazon_mode: link`) until a written yes arrives.

**Builds.** They run on GitHub's macOS runners, which are free for public repos: `macos-26`, Xcode 26, which App Store
uploads have required since 2026-04-28. Swift cannot compile in this sandbox, so keep Swift small and conventional. All
URL logic is the extension's JavaScript, run in JavaScriptCore, so it is tested here in node.

## Read first

- CLAUDE.md, AGENTS.md, docs/HANDOFF.md, briefs 241 and 244, and the merged `extension/`.
- `dealradar/publish/checker.py`: entry fields, `js_rules()`, `meta.json` and `all.json`.
- tests/fixtures/checker_aliases.json, `dealradar/publish/templates/privacy.html`, `brand/wowpriced-app-icon-1024.png`.

**Prerequisites:** 244 is merged, `extension/lookup.js` exports a pure synchronous `aliases(url, rules)`, and checker
publishes `/check/data/all.json` (all shards merged; this is a 244 amendment). If any of these is missing, stop and report.

## Task

1. **`ios/project.yml` (XcodeGen).**
   - Targets: `wowpriced` (app, `com.wowpriced.app`), `ShareExtension` (`com.wowpriced.app.share`) and
     `SafariExtension` (`com.wowpriced.app.safari`).
   - Build settings: `TARGETED_DEVICE_FAMILY` 1 (iPhone only, so no iPad screenshots), iOS 17.0, Swift 5 language
     mode, `ITSAppUsesNonExemptEncryption` = NO. `MARKETING_VERSION` and `CURRENT_PROJECT_VERSION` come from workflow
     inputs, `DEVELOPMENT_TEAM` from an env var.
   - Each target gets a `PrivacyInfo.xcprivacy`: `NSPrivacyTracking` false, no tracking domains, no collected data
     types, the UserDefaults reason CA92.1, and the file-timestamp reason C617.1 only if you read cache file dates.
2. **`ios/DealCheckCore`** (a Swift package used by the app and the Share Extension):
   - **Index.** Fetch `GET https://wowpriced.com/check/data/all.json` with an ephemeral `URLSession` (no cookies).
     Cache it in Caches with its fetch time; refresh when older than 6 h, on app foreground or when the Share
     Extension opens. Offline, use the last cache; with none, show "Connect to the internet once to load prices."
   - **Aliases.** JavaScriptCore evaluates bundled copies of `extension/lookup.js` and `extension/rules.json` and calls
     `aliases(url, rules)`. There is no alias logic in Swift. The first alias found in the index wins.
   - **Entries.** Decode exactly the shipped entry fields and ignore unknown ones. Never compute a price or a verdict;
     the only thing computed is "checked X ago", from `at`.
   - **Shared text.** Take the first http(s) URL from shared text with `NSDataDetector`. Hosts in `rules.short_hosts`
     return `.shortLink` and are never resolved.
   - **Amazon.** `amazon_mode` comes from the bundled `ios/app-config.json` (`"link"` or `"full"`; ships `"link"`). In
     `link` mode an Amazon entry shows only the item and "See the deal".
3. **App** (SwiftUI, three tabs):
   - **Check:**
     - Heading "Is this a good deal?", a URL field, a SwiftUI `PasteButton` (no automatic clipboard reads, so no paste
       prompt), and a Check button.
     - **Result card:** the shipped `verdict` sentence verbatim, then price, store and "checked X ago". Buttons: "See
       the deal" (`deal_path`) and "Price history" (`p_path`), which open `https://wowpriced.com<path>?ref=ios` in
       Safari via `openURL`, never inside the app.
     - **Ended:** "Last verified price $X on <date> — this deal has ended". **Not found:** "We haven't verified a
       price for this product yet." **Short link:** "Open the link in Safari first, then share the full product page."
     - **"Try an example":** shows the newest live entry with a non-empty verdict (sort by `at` descending, then key),
       so a reviewer always sees a real answer.
     - **Recent checks:** the last 20, stored only on the device, with a Clear button.
   - **Safari:** numbered steps to turn the extension on and grant site access. Confirm the Settings path on iOS 26 and
     write it exactly.
   - **About:** how prices are verified (one paragraph from privacy.html wording), the privacy policy link
     https://wowpriced.com/privacy.html, hello@wowpriced.com, and the version.
   - **Everywhere:** Dynamic Type, VoiceOver labels, light and dark brand colors, 44 pt targets.
4. **Share Extension.**
   - Activation rule: a predicate accepting one `public.url` OR one `public.plain-text`. Text is required because some
     apps share text that contains the link.
   - It runs the same lookup and shows the compact card plus Done.
   - Share extensions cannot reliably open other apps or URLs, so there are no link buttons here.
5. **Safari Extension target.**
   - **Resources.** A build-phase script copies `extension/` (without `store/` and tests) into the target's resources,
     so 244 stays the single source.
   - **Amazon mode.** The same script sets `AMAZON_MODE` in the copied `rules.json` from `app-config.json`.
   - **Handler.** `SafariWebExtensionHandler.swift` is the minimal echo handler; native messaging is unused.
   - **Site access.** Exactly 244's matches (guideline 4.4.2). No Join, Support or promo in the card (guideline 4.4).
6. **Swift tests (`ios/DealCheckCoreTests`).**
   - **Parity.** On every row of tests/fixtures/checker_aliases.json (copied into the test bundle by a build phase),
     the aliases from JavaScriptCore must equal the fixture.
   - **Lookup.** A small fixture `all.json` covers live, ended, link_only, not found, short link and Amazon in link
     mode. Also test URL extraction from "Look at this https://…".
   - **UI test.** Launch argument `-demoIndex` loads the bundled fixture index, with no network. It saves 3 screenshots
     as test attachments: a check result, "Try an example", and the Safari steps.
7. **`.github/workflows/ios.yml`.**
   - Trigger: **`workflow_dispatch` only**, with inputs `version`, `build_number`, `upload` (boolean, default false).
     `permissions: contents: read`, concurrency group `ios`, `runs-on: macos-26`.
   - **Job `build` (needs no secrets):**
     1. `brew install xcodegen`, then `xcodegen --spec ios/project.yml`.
     2. `xcodebuild test` on the largest iPhone simulator on the image, with `CODE_SIGNING_ALLOWED=NO`.
     3. Export the screenshots from the `.xcresult` with `xcrun xcresulttool` and upload them as artifacts.
   - **Job `testflight`:**
     - Runs only if `inputs.upload` is true and the secrets `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8` and
       `APPLE_TEAM_ID` exist. Environment `ios-release`.
     - Write the `.p8` to `$RUNNER_TEMP`.
     - `xcodebuild archive -allowProvisioningUpdates` with the `-authenticationKey*` flags.
     - `-exportArchive` with an `ExportOptions.plist`: method `app-store-connect`, destination `upload`, signing automatic.
     - Delete the key in an `if: always()` step.
   - It uploads to TestFlight processing only. Never submit for App Review, and never echo a secret.
8. **`ios/store/listing.md` and `ios/README.md`.**
   - **Listing:**
     - Name "wowpriced: Is it a good deal?" (30 characters or fewer); subtitle 30 or fewer with no retailer names
       (guideline 2.3.7); keywords 100 or fewer, no retailer or competitor trademarks.
     - Description leads with Share and Safari. App Privacy "Data Not Collected", with reasons. Age 4+; US storefront only.
     - A review-notes template: numbered steps, a placeholder for 3 demo URLs taken from that day's index, and a
       30-second screen-recording note.
   - **README:** the operator's steps: (1) enroll as an Individual; (2) create the app record with these bundle IDs;
     (3) create an API key with the Admin role; (4) add the secrets to the `ios-release` environment; (5) install
     through TestFlight internal testing; (6) declare non-trader (US-only distribution).

## Tests (offline, here; new tests/test_ios_app.py)

- **`project.yml`:** the bundle IDs, iPhone only, iOS 17.0, and the three targets.
- **No WebView, http or other hosts:** in `ios/` code (Swift, plist, yml, json; not the README or store docs) grep
  finds no `WKWebView`, `WebKit` or `SFSafariViewController`, no `http://` URL, and no host but `wowpriced.com`.
- **Plists and config:** every `PrivacyInfo.xcprivacy` parses with `plistlib` with tracking false; `app-config.json`
  `amazon_mode` is `link` or `full` and ships `link`; the Share Extension's activation rule accepts URL and text.
- **Workflow:** triggers are exactly `{workflow_dispatch}`. The upload job requires `inputs.upload` and the
  `ios-release` environment. Secrets appear only in `env:`. An `always()` step deletes the key, and nothing submits
  for review.
- **Node:** `extension/lookup.js` `aliases()` equals the fixture on every row (reuse 244's test and do not duplicate it).
- **Swift:** the Swift tests run only in the workflow. Claude dispatches it and sends back any compile or test failures.

## Rules

- **Ships nothing:** no upload without the operator's secrets and `upload: true`. Every verdict and price is the
  shipped text; no new math (savings.py untouched). No Keepa, LLM or retailer calls; no host but `wowpriced.com`.
- **Protected files.** Do not change tests/test_workflows.py or anything in `extension/` except an export-only
  refactor of `lookup.js` that keeps 244's tests green.
- **Testing.** Run targeted tests with `-n 1` and your own basetemp `/tmp/task248-*`, and delete it after each run. Do
  not run the whole suite.
- **Finishing.** Do not commit. Update docs/HANDOFF.md "In flight" with one short entry.
