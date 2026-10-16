# wowpriced — Is this a good deal?

Short description:
Check product links against prices wowpriced has verified. No page scraping, browsing log or affiliate link changes.

Long description:

Is the price you're looking at a good deal? wowpriced checks the product
address against prices we've already verified. When we have a match, a small
card shows our answer, the verified price and when we checked it. One button
opens the item's price history or deal page on wowpriced.

Works with supported product links from Amazon, Best Buy, Target, Walmart,
eBay and Woot. Amazon and Walmart can show history verdicts when our published
data carries those facts; Amazon entries without price facts show the item and
history link. eBay shows live verified deals only, because listings end. If we
haven't verified the product, no card appears. Prices can change; the retailer's
checkout price applies.

Paste a product link into the popup to check it yourself. The extension starts
on after install. Dismiss any card, hide it on a site, or switch it off globally
whenever you want. Hidden sites are listed in the popup with an Unhide button.

No page scraping. No browsing log, account, cookies, analytics or tracking
pixels. No coupon testing, retailer link changes or affiliate tag replacement.
The extension does not read the page's text or price. The extension downloads
our public price list a few times a day and checks prices on your device; it
sends nothing about the pages you visit.
wowpriced pages opened with the button have their own privacy choices and
may contain clearly disclosed affiliate links.

Single purpose:
Help shoppers check a product link against wowpriced's verified price facts
and open the corresponding price history or deal page.

Permission justifications (one line each):

- `storage`: Remember the on/off and per-site choices and save the compact public price list locally for offline lookups. No unlimitedStorage permission is needed.
- `alarms`: Refresh the public price list every six hours, independently of page visits.
- `https://wowpriced.com/*`: Download only the public `/check/data/all.json` price list at install/browser startup and every six hours, with no cookies or referrer.
- Product-page content matches: Read only the current address and display a dismissible price-check card on supported product paths.

Data-use disclosures:

- Collects nothing; no personal information, browsing history, page content, cookies, analytics or advertising data is collected or sold.
- Product addresses and lookups stay in the browser. Visiting a product page or pasting a link makes no network request.
- Hosting receives ordinary connection information for scheduled public-list downloads; preferences may sync through the browser provider. The price list stays in local storage, never syncs, and is not used once its publication or download is 24 hours old. Missing/expired data shows no card until the next background refresh.
- No remote code, minification, external libraries, account or payment in the extension.
- Privacy policy: https://wowpriced.com/privacy.html
- Support: hello@wowpriced.com

Screenshots must be generated from the local synthetic fixture, clearly labeled
as an example; they are not a live retailer endorsement or a real price claim.
