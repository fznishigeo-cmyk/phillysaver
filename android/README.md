# wowpriced Android — disabled, not submitted

The handwritten `twa-manifest.json` targets Bubblewrap **1.27.0** (API 36).
The chosen build path is Bubblewrap `update --skipVersionUpgrade`, then `build`
(or `build --skipSigning` without secrets). It generates Gradle only in the
runner's temporary directory. No Gradle project, signing material or generated
Android binary belongs in this repository. The manual workflow builds artifacts;
it never uploads to Play. Do not dispatch it as part of this task.

## Operator steps, in order

1. On a trusted machine with JDK 17, make the upload key inside `android/`
   (ignored by Git). Keep the package
   `com.wowpriced.app`: it becomes permanent after the first Play upload.
   `keytool` asks for the password interactively; never paste it in a command:

   ```sh
   cd android
   keytool -genkeypair -keystore upload.keystore -storetype PKCS12 \
     -alias upload -keyalg RSA -keysize 4096 -validity 10000
   ```

2. Keep an encrypted offline backup of the key and password. In GitHub's
   repository Actions secrets, set `ANDROID_UPLOAD_KEYSTORE_B64` (base64 of
   the file), `ANDROID_KEYSTORE_PASSWORD`, and `ANDROID_KEY_PASSWORD` (PKCS12
   normally uses the same password). Create the `android-signing` environment
   and add an approval protection for the manual build.
   Do not commit or log them. Review this change, finish local browser QA and
   screenshots below, and run the relevant checks. Enable `checker.enabled`,
   `pwa.enabled` and `pwa.android.enabled` only for the reviewed app rollout;
   support mode must stay enabled so Join free remains email-only. Keep
   `amazon_in_app: limited`. Leave `amazon_watches: "off"` unchanged.
   Deploy the reviewed site through the normal operator process, then manually
   run **Android build** with an increasing version code and display version.
   A run without signing secrets proves only the unsigned build, not a Play
   upload. JDK/SDK and Bubblewrap generation cannot be exercised in this local
   container; the first manual runner build must confirm headless generation,
   API 36 and the embedded shortcuts/share target. If Bubblewrap prompts, stop:
   capture/review its generated Gradle project and switch to the brief's Gradle
   fallback before proceeding. No successful Android build is claimed here.

3. Register/verify the operator's Google Play developer account (the operator
   pays the registration fee when ready). Download the signed `.aab` from the
   manual workflow's 7-day artifact and upload the first build **by hand** to
   **Internal testing**. Opt into Play App Signing. Install it on a real phone.
   Do not use the Play API, a service account or an automatic submission.

4. In **Play Console → App integrity → App signing key certificate**, copy the
   **SHA-256** into `pwa.android.sha256_cert_fingerprints` first; put the upload
   certificate fingerprint second. Both must be uppercase colon-separated
   32-byte public fingerprints. The workflow prints only the upload certificate's
   SHA-256. Review and deploy the site configuration. Never use a generated or
   example fingerprint for launch. Confirm
   `https://wowpriced.com/.well-known/assetlinks.json` responds with JSON directly,
   HTTP 200 and **no redirect**. Empty fingerprints deliberately publish no file.

5. The operator verifies Digital Asset Links (this task never calls it):
   <https://digitalassetlinks.googleapis.com/v1/statements:list?source.web.site=https://wowpriced.com&relation=delegate_permission/common.handle_all_urls>.
   Check the Play signing certificate, package, trusted fullscreen launch,
   Custom Tabs fallback, shared retailer links (including short-link guidance),
   app-only hidden login/payments/Google sign-in, Amazon history suppression and
   untagged Amazon navigation. Test airplane-mode navigation and Retry. Confirm
   GA4 has `app_context: android` and Meta/Clarity make no requests. Clear site
   data and verify ordinary browser sessions never register a worker.

6. Complete the **12-tester, 14-day closed test**, then apply for production
   access if Play requires it for this developer account. Review the listing,
   final Data safety answers, content rating and **18+** target audience. Check
   current Play policies, particularly minimum functionality, affiliate traffic,
   Payments and account deletion: acceptance is not established by a build.
   Obtain **written Amazon Associates app approval** before considering
   `amazon_in_app: as_site`; web/extension permission is not app permission.
   The operator chooses whether and when to submit; nothing here publishes.

## Offline listing assets and QA

`store/listing.md` supplies draft copy and privacy-aligned Data safety notes.
`store/feature-graphic.png` is rendered locally from the existing `brand/` icon.
To reproduce it, run `python android/store/render_feature.py` with Pillow and
the DejaVu fonts installed; these are listing tools, not runtime dependencies.
Four real **1080×1920** screenshots must be rendered from an actual local test
build, using Playwright, before store submission:

Run these commands from the repository root:

```sh
.venv/bin/python android/store/build_fixture.py /tmp/task246-store-site
NODE_PATH=/opt/node-tools/node_modules node android/store/render.cjs /tmp/task246-store-site
rm -rf /tmp/task246-store-site
```

The fixture reuses repository publisher/test helpers and extension icons read-only;
it does not fetch a retailer page. The renderer permits loopback requests only,
adds `in-app`, labels synthetic prices as examples, and captures Deal Check,
a share result, a deal page and the offline fallback. It writes into
`android/store/screenshots/`. Inspect the PNGs before submission; do not substitute
mockups for a browser capture. This container blocks Chromium and its headless
shell before launch (`Operation not permitted`); screenshots and real-browser QA
remain pending on a machine that permits Chromium. No screenshots are fabricated.

References for the operator's review (not fetched during this offline task):
[Bubblewrap 1.27.0 README](https://github.com/GoogleChromeLabs/bubblewrap/blob/v1.27.0/packages/cli/README.md),
[TWA manifest reference](https://github.com/GoogleChromeLabs/bubblewrap/blob/v1.27.0/packages/core/src/lib/TwaManifest.ts).
