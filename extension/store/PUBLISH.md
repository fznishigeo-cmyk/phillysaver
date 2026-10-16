# Publish when ready

The extension starts enabled after install; the popup provides global and
per-site switches and lists hidden sites with an Unhide button. The build
command creates local files. Before listing, publish tasks 240/241's checker
data/page (`checker.enabled` is still false) and complete the reviewer checks.
Amazon allowed the extension to show our verdict and checked price (operator
call amz378_998, 2026-10-16; HANDOFF Operator decisions). `config.yaml`
`extension.amazon_mode: full` exports `AMAZON_MODE: "full"` in `rules.json`.
If Amazon objects, set `extension.amazon_mode: link` and rerun
`python -m dealradar build-extension`. Amazon history is already supported by
`checker.stores.amazon: history` with `amazon_history: show`; entries without
price facts display the item and link. Walmart history is allowed (operator
call 2026-10-16); cards can show it when supplied by the checker data. eBay
cards show live verified prices only, because listings end.

## Prepare the files

1. Run `python -m dealradar build-extension` from the repository root.
2. Find `dist/wowpriced-extension-0.1.0.zip`. The same zip goes to both stores;
   it contains readable source and icons, with no listing fixtures or scripts.
3. With Playwright already installed, run
   `node extension/store/render.cjs /tmp/wowpriced-store-render`.
   It uses local fixtures and creates two 1280×800 screenshots plus the
   440×280 promo tile in `extension/store/screenshots/`. Delete that temporary
   directory afterward.
4. Inspect all three images, the popup's keyboard flow, dark mode,
   dismiss/site/global switches, hidden-site Unhide buttons, Amazon's item/link
   card when price facts are absent, and unmatched products (no card).
   Run `node tests/extension_browser.cjs /tmp/wowpriced-browser-review` to exercise
   the loaded extension offline. Confirm product/SPA navigation makes zero
   extension network requests, the six-hour alarm downloads only `all.json`,
   an expired list shows no card, and popup paste works offline after download.
   Delete that temporary directory afterward.
5. Use `listing.md` for the text and disclosures. The extension privacy URL is
   `https://wowpriced.com/privacy.html`. The site must serve the new section.
6. At submission time, choose one full product URL currently present in the
   published `/check/data/` index. Open it with the loaded extension and confirm
   the card appears. Replace `<INDEXED_PRODUCT_URL>` below with that concrete
   URL in both stores' reviewer instructions. The local screenshot fixture is
   synthetic and is not a live indexed product.

## Chrome Web Store

1. Sign into the operator's Google account and open
   https://chrome.google.com/webstore/devconsole.
2. If registering for the first time, accept the developer agreement, pay the
   one-time **$5 developer registration** fee, and finish account/contact checks.
3. Click **New item**, choose the zip, and wait for validation.
4. Open **Store listing**. Name and summary come from the package manifest;
   set the detailed description from `listing.md`, choose the Shopping category
   and English language, and upload the 128px icon plus the two 1280×800 fixture
   screenshots. Upload `screenshots/promo-440x280.png` as the **Small promo tile
   (required)**. The short description in `listing.md` matches the manifest.
5. Open **Privacy** (privacy practices). Paste the single purpose and each permission
   justification. Declare no remote code and no collection/use of user data;
   fill in the privacy-policy URL and certify the disclosures accurately.
6. Open **Test instructions** and paste, after replacing the URL token:
   "The extension starts on after install. Open `<INDEXED_PRODUCT_URL>`; a card
   appears bottom-right. Or click the toolbar icon (puzzle menu), paste that URL
   and click Check. If previously switched off, tick Turn on wowpriced first.
   Unmatched products show no card. Hide on this site and the global switch
   remove the card; the popup lists hidden sites with Unhide buttons."
7. Set support contact/URL, distribution countries and public visibility.
8. Use the dashboard preview. Check all fields and validation messages. The
   operator then clicks **Submit for review** and follows the store's notices.

## Firefox Add-ons (free)

1. Sign into the operator's Mozilla account at https://addons.mozilla.org/developers/.
2. Click **Submit a New Add-on**, accept the agreement, and choose **On this site**.
3. Upload the same zip. Confirm ID `extension@wowpriced.com`, MV3 and minimum
   Firefox 140. The manifest declares `data_collection_permissions.required:
   ["none"]` for current Firefox/store disclosure requirements.
4. Review validation. Chrome uses `background.service_worker`; Firefox uses
   `background.scripts` in the same manifest. This is the documented pattern
   for Chrome 121+ and Firefox 140+.
5. No separate source upload is needed: the zip contains the actual readable,
   unminified JS and there is no bundler or generated executable code.
6. Fill name, summary, description, Shopping category, English, privacy-policy
   URL, support email and screenshot fields from the listing kit.
7. Paste the same Test instructions as Chrome, with the same concrete indexed
   product URL selected above. Explain URL-only reads, scheduled public-list downloads,
   device-only lookups and the 24-hour freshness limit, global/per-site controls, Amazon mode (full by default,
   item and link when price facts are absent), and synthetic screenshot labels.
8. Review the listing, then the operator clicks **Submit Version**. There is
   **no developer registration fee**. Respond to review requests without adding
   broader permissions or changing the no-collection disclosures.

## Compatibility references

- [Chrome: publish in the Web Store](https://developer.chrome.com/docs/webstore/publish)
- [Mozilla: submitting an add-on](https://extensionworkshop.com/documentation/publish/submitting-an-add-on/)
- [Mozilla: when source upload is required](https://extensionworkshop.com/documentation/publish/source-code-submission/)
- [Mozilla: cross-browser background manifest](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/manifest.json/background)
- [Mozilla: data collection consent, including no collection and older Firefox](https://blog.mozilla.org/addons/2025/10/23/data-collection-consent-changes-for-new-firefox-extensions/)

## Navigation and generated assets

On an injected product page, client-side URL changes refresh or clear the card
within one second; Back/Forward also trigger a refresh. Starting on a
non-product page and routing to a product without a full navigation does not
inject a content script. Reload the product page or paste its URL into the popup.

`render.cjs` renders `price-check.png` and `amazon-check.png` from the local
`fixture.html`; rerun after any card change. It also renders `promo-440x280.png`
from `promo.html`, using Chromium at `/opt/pw-browsers/chromium`.
The two existing 1280×800 PNGs predate this review. This run's renderer and
loaded-extension test could not launch Chromium (`Operation not permitted`);
regenerating all three images and completing browser QA remain before listing.
The four icon PNGs were resized once from `brand/wowpriced-icon-512.png` with
the machine's preinstalled Pillow; Pillow is not a project/runtime dependency.

## Local index and permissions

The extension downloads our public price list a few times a day and checks prices
on your device; it sends nothing about the pages you visit. Only installation,
browser startup and the six-hour `alarms` event fetch `/check/data/all.json`.
Missing or expired data shows nothing until a background refresh. Neither a page
lookup nor popup paste can fetch a shard or trigger a download.

- `storage`: local public-list cache plus on/off and per-site preferences. Only
  preferences may sync. The compact index is below 1.5 MB; no unlimitedStorage.
- `alarms`: refresh every six hours, independently of browsing activity.
- `https://wowpriced.com/*`: public price-list download only, without cookies or
  a referrer; no retailer requests or per-product requests.

`all.json` version 1 is shared with native apps. `generated_at` is the UTC publish
clock. `fields` lists the entry columns. `entries` maps each alias to column value
IDs, with `-1` meaning an absent field (different from null or an empty string).
`values` is a sorted dictionary of canonical ASCII JSON strings, front-coded as
`[prefix_length, suffix]` against the previous string (initially empty). Rebuild
each string, then JSON-decode it; this preserves all shard fields and nested
credits/takes. Reconstruct entries from the column IDs. Publication drops oldest
ended aliases from both outputs if needed; live overflow fails before replacing
any files. The same noindex/no-store headers cover shards and `all.json`.
