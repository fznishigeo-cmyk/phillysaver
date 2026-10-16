# Draft Play listing — operator review required

**Title:** wowpriced: Check a price

**Short description:** Share a product link. Check the price against deals we've verified.

## Full description

Share any product link to see if it's a good deal. Send a product page from
Chrome or a retailer app to wowpriced Deal Check, or paste its full address.
We look up only our own verified records and show the answer we've already
checked, with its date. If we have no verified price, we say so.

For supported products, see recorded price checks, the usual-price comparison
and the original credited deal. Browse today's verified deals when you want
ideas. Prices and availability can change: confirm with the store before buying.
Short links need opening in Chrome first, then share the full product address.

Amazon results in this app show the item and a link to the deal; Amazon price
history and verdicts are available on wowpriced.com in your browser. Amazon
links opened from this app do not carry our Associates tag. Other eligible
store links may earn us a commission; see our affiliate disclosure.

No account or subscription is required. Join free is an optional email-only
deals list. There are no in-app payments. Offline, we show a reconnect screen,
never a cached price. No notifications, retailer credentials or page scraping.

## Draft Data safety answers from privacy.html

Privacy URL: https://wowpriced.com/privacy.html
Deletion requests/contact: https://wowpriced.com/contact.html

- **Collected:** optional email address, sign-up time and confirmation status
  for Join free, used only to manage the deals email. No password/account is
  created inside the app. Ask for deletion from that email address.
- **Analytics:** GA4 measures visits, page views and actions such as deal
  clicks; it can receive the page address, browser information and online
  identifiers. Hosting receives IP address, browser type and page requests.
  Play categories to review: email address under Personal info; app interactions
  and page views under App activity/Web browsing; device or other IDs for
  analytics cookies/identifiers. Purposes: app functionality and analytics.
- **Shared:** service providers receive information needed to host the site,
  send the deals email and provide analytics. The operator must confirm which
  processors qualify for Play's service-provider exception. Other ad tags, if
  configured, follow the published privacy choices/GPC; Meta Pixel and Clarity
  do not load in app context. No claim that only GA4 can ever receive data.
- **Nothing sold:** wowpriced does not sell personal data. Retailer/affiliate
  attribution cookies follow the retailer/partner's policies after opening a
  deal. This is separate from Play's sharing definitions and opt-out choices.
- **Security and retention:** HTTPS in transit; email-list records retained
  until unsubscribe or deletion request. Browser preferences stay on the
  device until expiry or clearing site data. The saved list stays on device;
  analytics receives only a bounded action label/count, never the list.
- **Permissions:** no contacts, precise location, camera, microphone or photos.
  Notifications are disabled. Shared URLs are looked up using same-origin
  hash-shard filenames, without resolving or fetching retailer pages.

The operator must check these answers against the final enabled tracking IDs
and current privacy page before completing the Play Console form. Do not claim
no data collection or omit GA4. The Deal Check page carries no analytics, so
shared/pasted query strings are not passed to analytics loaders.

## Content rating and audience

Target audience **18 and over**; not directed to children. Shopping/price-check
utility, no user-generated content, chat, gambling or simulated gambling. No
violence, sexual content or controlled-substance promotion in this listing.
Complete the IARC questionnaire against the final app and linked catalog rather
than assuming this draft establishes a rating.

## Graphics

Feature graphic: `feature-graphic.png`, **1024×500**, RGB PNG (no alpha).
Screenshots: four **1080×1920** Playwright captures of the local synthetic test
site: a Deal Check hit, share result, deal page and offline page. Example prices
are labeled; no retailer page or retailer logo is fetched. Chromium is blocked
in the coding sandbox, so run the documented renderer and inspect these four
PNGs before submission. This draft has not been uploaded or submitted.
