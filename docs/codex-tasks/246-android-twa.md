# 246 — Android app as a Trusted Web Activity + share-to-Deal-Check (ships off; nothing goes to Play)

## Why

Operator, 2026-10-16: build apps that are good and that sell. The operator pays for Google Play ($25) when Claude says the
app is ready. The cheapest real Android app is a Trusted Web Activity (TWA) of wowpriced.com, built with Bubblewrap
1.27.0, which targets API 36 (required for new apps since 2026-08-31).

Four constraints shape this task:
- **Play policy.** "Webviews and Affiliate Spam" rejects apps whose primary purpose is affiliate or referral traffic,
  and minimum functionality rejects thin apps. So the app opens on the Deal Check tool (/check/, task 241) and takes
  product links shared from retailer apps.
- **Amazon.** Amazon Associates' Mobile Application Policy bans price tracking in apps that carry Associates links (we
  only have search excerpts; the operator's written ask, reference amz378_998, is pending). Inside the app, Amazon shows
  no history or verdict and carries no tag until a written yes arrives.
- **Play rules.** No Stripe payments inside the app (Payments policy), and no account creation inside the app (that
  would trigger the in-app account-deletion rule).
- **Build environment.** This container has no JDK 17 and no Android SDK; GitHub's ubuntu-24.04 runner has both.

## Read first

- CLAUDE.md, AGENTS.md, docs/HANDOFF.md ("Operator decisions"), brief 241 and the merged /check/ page code.
- `dealradar/publish/pwa.py`; templates `pwa.js`, `pwa.css`, `pwa-footer.html`, `support.js`, `support-ask.html`.
- `tracking.py` (Meta Pixel, Clarity loaders), `amazon_history.py` (task 250), `checker.js_rules()`, config.py `pwa`.
- tests/test_pwa.py: its exact-manifest and no-`serviceWorker` asserts must pass **unmodified**.
- `.github/workflows/lighthouse.yml` (house style); Bubblewrap 1.27.0 README, "twa-manifest.json reference".

**Prerequisite:** 241 is merged (the `site/check/index.html` writer exists). If it is not, stop and report.

## Task

1. **Config.** A validated `pwa.android:` block; with it off, every published byte equals HEAD. Ships as:
   ```yaml
   enabled: false
   package: "com.wowpriced.app"   # permanent once uploaded; regex ^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$
   sha256_cert_fingerprints: []   # Play "App signing key" cert first, then the upload cert; AA:..:FF, 32 bytes, uppercase
   amazon_in_app: limited         # limited | as_site; as_site only after Associates approves the app in writing
   ```
2. **Publisher** (`pwa.publish`, only when `pwa.android.enabled`):
   - **a. Asset links.** Only when fingerprints are set, write `/.well-known/assetlinks.json` as a regular file
     (wrangler skips symlinks), exactly:
     `[{"relation":["delegate_permission/common.handle_all_urls"],"target":{"namespace":"android_app","package_name":…,"sha256_cert_fingerprints":[…]}}]`.
     Append a `_headers` rule (`Content-Type: application/json`, `Cache-Control: public, max-age=300`). It must load
     with no redirect; robots.txt stays unchanged.
   - **b. App context.** A few lines of inline script in the pwa head block, in one small function a node test can
     call with stubbed `location`, `document` and `sessionStorage`:
     - Set `sessionStorage.wp_app = "android"` when `location.search` has `src=twa` or `document.referrer` starts with
       `android-app://<package>`. Share launches arrive as `/check/?title=&text=` with no `src`, so the referrer
       check is what catches them.
     - While the flag is set, add class `in-app` to `<html>`. Wrap all storage access in try/catch.
   - **c. Hide in the app** (CSS on existing ids/classes; add a `data-` attribute only where no stable selector
     exists): Install app / Add to Home Screen, the Support banner, every `https://buy.stripe.com/` link, Log in,
     sign-up and membership UI. Join free stays (email only). In `tracking.py`, the Meta Pixel and Clarity don't load
     in app context; GA4 loads with `app_context: "android"`.
   - **d. Amazon when `amazon_in_app: limited`** (app context only):
     - Hide every Amazon history element `amazon_history.hidden()` would remove under `hide`: chart, lowest-seen,
       history-checked line, and the verdict on /p/ and deal pages.
     - Amazon-only /p/ pages show one line, "Amazon price history is on wowpriced.com in your browser.", plus See the
       deal. Deal Check renders Amazon entries as `link_only`.
     - One delegated click handler removes `tag=` from Amazon URLs before navigation; hrefs in the HTML bytes stay unchanged.
   - **e. Offline page and service worker.**
     - `/offline.html`: brand header, "You're offline. Prices only show live: reconnect to check a deal.", a Retry
       button, no price.
     - `/sw.js`: handles **navigation requests only**, network-first. It precaches only `/offline.html`, serves it when
       the network fails, never caches any other response, and carries a version from `pwa.site_version`.
     - Registered **only in app context**, so ordinary visitors never get a service worker.
3. **Share target on the 241 page.** When there is no `u`, take the first `http(s)://` URL in `text`, then `url`,
   then `title`, and run the normal lookup. Short links get the existing "open the link first" message; in app
   context add "Open it in Chrome, then share the product page from there." Never resolve a link; no new request.
4. **`android/`** (committed):
   - **`twa-manifest.json`**, hand-written for Bubblewrap 1.27.0 (no `bubblewrap init`):
     - packageId `com.wowpriced.app`; host `wowpriced.com`; name "wowpriced: Is it a good deal?"; launcherName "wowpriced".
     - startUrl `/check/?src=twa`; display `standalone`; fallbackType `customtabs`; enableNotifications `false`.
     - Colors from `pwa.colors()`; iconUrl `https://wowpriced.com/pwa-icons/icon-512.png`; maskableIconUrl `…/icon-maskable-512.png`.
     - shareTarget `{"action":"/check/","method":"GET","params":{"title":"title","text":"text","url":"u"}}`.
     - Two shortcuts, both with icon-192 and `?src=twa`: Check a price (`/check/`) and Today's best deals (`/best-deals-today/`).
     - signingKey `{"path":"./upload.keystore","alias":"upload"}`; appVersionCode 1; appVersionName "1.0.0".
   - **`android/README.md`**, the operator's numbered steps:
     1. Make the upload key with `keytool` (PKCS12, RSA 4096, 10000-day validity, alias `upload`).
     2. Store it as GitHub secrets, plus an offline backup.
     3. Upload the first build by hand to the **Internal testing** track.
     4. Copy the "App signing key certificate" SHA-256 from Play Console > App integrity into config.
     5. Verify with `https://digitalassetlinks.googleapis.com/v1/statements:list?source.web.site=https://wowpriced.com&relation=delegate_permission/common.handle_all_urls`.
     6. Run the 12-tester, 14-day closed test.
   - **`android/store/`**:
     - `listing.md`: title (30 characters or fewer, no retailer names); short description (80 or fewer); a full
       description leading with "Share any product link to see if it's a good deal"; Data safety answers copied from
       privacy.html (email for Join free, GA4 analytics, nothing sold); content-rating notes; target audience 18+; privacy URL.
     - A 1024x500 feature graphic (PNG, no alpha) rendered from `brand/`.
     - Four 1080x1920 screenshots rendered by Playwright from a locally built test site with `in-app` on: a /check/
       hit, a share result, a deal page, the offline page. No real retailer page is fetched.
5. **`.github/workflows/android.yml`**:
   - Settings: **`workflow_dispatch` only** (inputs `version_code`, `version_name`), `permissions: contents: read`,
     concurrency group `android`, `ubuntu-24.04`, environment `android-signing`.
   - Steps:
     1. Install tools: `actions/setup-java` (temurin 17), Node 22, `npm i -g @bubblewrap/cli@1.27.0`.
     2. Write `~/.bubblewrap/config.json` = `{"jdkPath": $JAVA_HOME, "androidSdkPath": $ANDROID_HOME}`. If
        `$ANDROID_HOME/tools` is missing, `ln -s cmdline-tools/latest $ANDROID_HOME/tools`.
     3. Copy `android/` to `$RUNNER_TEMP/twa` and set the version from the inputs with `jq` (the committed file never changes).
     4. If secret `ANDROID_UPLOAD_KEYSTORE_B64` exists: decode it to `$RUNNER_TEMP/twa/upload.keystore`, then run
        `bubblewrap update --skipVersionUpgrade` and `bubblewrap build` with `BUBBLEWRAP_KEYSTORE_PASSWORD` and
        `BUBBLEWRAP_KEY_PASSWORD` from secrets via `env:`. Print the upload certificate's SHA-256 (a public value).
     5. Otherwise run `bubblewrap build --skipSigning`, which proves the build.
     6. Upload only `*.aab` and `*.apk` as artifacts (7-day retention); an `if: always()` step deletes the keystore.
   - Forbidden: `bubblewrap play`, the Play API, a service account, any `schedule:`/`push:`/`pull_request:` trigger,
     printing a secret.
   - If Bubblewrap still prompts when headless, commit the generated Gradle project under `android/`, build with
     `./gradlew bundleRelease`, and sign with `apksigner` or `jarsigner` from the same secrets. Say which path you took.

## Tests (offline; new tests/test_android_app.py)

- **Off and rerun:** published bytes match HEAD (test_pwa's baseline pattern); tests/test_pwa.py passes **unmodified**;
  `tests/test_e2e_offline.py::test_rerun_is_idempotent` stays green.
- **Config:** bad package, bad fingerprint or bad `amazon_in_app` is rejected. Empty fingerprints mean no
  `.well-known/`. Two fingerprints give the exact `assetlinks.json` bytes, the `_headers` rule, and a regular file.
- **Service worker:** `sw.js` has one `fetch` handler gated on `request.mode === "navigate"`, caches only
  `/offline.html`, and never mentions `/check/data`, `deals.json` or `/p/`. `offline.html` contains no `$`.
- **Detection (node):** flag set for `?src=twa` and an `android-app://com.wowpriced.app/` referrer, never otherwise.
- **Browser** (Playwright, Chromium at /opt/pw-browsers, local static server over a test build with android enabled):
  - `/?src=twa` gives `in-app` on the next page too. Support, Stripe, Log in and Install are hidden.
  - No request reaches the Pixel or Clarity hosts (count with route aborts).
  - The Amazon chart is hidden; clicking an Amazon link navigates without `tag=` (intercept it).
  - An ordinary session has no `in-app` class and no `navigator.serviceWorker` registration.
  - After the worker installs, an offline navigation shows the offline page.
  - Share: `/check/?text=Look%20https%3A%2F%2Fwww.bestbuy.com%2Fsite%2F…%2F6505727.p` shows the fixture entry;
    `?title=` only shows not-found; an `a.co` link in `text` shows the open-in-Chrome message.
- **Manifest:** `twa-manifest.json` matches config, `pwa.colors()` and the share params; every URL is `https://wowpriced.com/…`.
- **Workflow:** triggers exactly `{workflow_dispatch}`; `@bubblewrap/cli@1.27.0` pinned; no `play` subcommand;
  secrets only in `env:`, never in `run:`; artifacts `.aab`/`.apk` only; an `always()` step removes the keystore.

## Rules

- **Ships off.** Prices and verdicts are the shipped text; app context only hides things. No new price math
  (savings.py untouched). No Keepa, LLM, retailer or Google Play calls.
- **Secrets:** never commit a keystore, a password, or a real key's fingerprint; config ships empty.
- **Protected files:** do not change tests/test_workflows.py or tests/test_pwa.py.
- **Testing:** targeted tests with `-n 1` and your own basetemp `/tmp/task246-*`, deleted after each run. Do not run
  the whole suite.
- **Finishing:** do not commit. Update docs/HANDOFF.md "In flight" with one short entry.
