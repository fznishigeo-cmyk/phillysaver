# Established-look copy review — task 212

Audited every file in `dealradar/publish/templates/` and the page/email builders
in `dealradar/publish/*.py`: home (including search, filters, Saved and sign-up
states), all 11 category hubs, deal pages, best-deals-today, store/price pages,
product families, `/p/` index/product histories, About, How it works, Contact,
Privacy, Disclosure, Careers, Deal hacks, Join, Subscribe, Welcome, 404, digest,
member, newsletter and confirmation emails. Included dormant membership/open
variants and the optional shared components. No launch dates, site age, audience
size or other unsupported claims were added.

In the table, `{N}` and other braces represent existing values, not new wording
or changed arithmetic. Link text is shown without HTML unless the link is added.
These are all customer-visible changes; other changed files update checks or
published-byte pins.

| Page / state | Before | After | Why |
| --- | --- | --- | --- |
| Every page: shared footer verification link | How it works | How we verify | Consistent trust link to the existing verification/help page; URL unchanged. |
| About: verification link | How it works | How we verify | Match the shared trust link. |
| Disclosure: verification link | How it works | How we verify | Match the shared trust link; disclosure commitments unchanged. |
| How-it-works: browser title | How to use wowpriced | How we verify deals — wowpriced | Explain the page's verification purpose and use the existing brand spelling. |
| How-it-works: main heading | How to use wowpriced | How we verify deals | Match the verification links. Browsing help and badge explanations remain. |
| Hubs, listings, deal and product pages: verification link | How we check deals | How we verify | Use the same label throughout the site. |
| Every support-mode footer: completed sign-up | Thank you for signing up! | Thank you for signing up. | Calm acknowledgement. |
| Home, Join and shared forms: unavailable sign-up (missing service / failed response) | Sign-up is unavailable right now. Please try again soon. | Sign-up is temporarily unavailable. Please try again. | Clear retry instruction without a vague promise. |
| Welcome: browser title | wowpriced — you're in | Welcome to wowpriced | Direct, consistent welcome. |
| Welcome: membership heading | You're in 🎉 | Welcome to wowpriced | Remove celebratory clutter. |
| Welcome: sign-in error | Sign-in is being set up on our side. Reload this page in a few minutes. | Sign-in is temporarily unavailable. Reload this page to try again. | Report the unavailable service without suggesting an unfinished setup or inventing a recovery time. |
| Home search field: one offer | Search 1 deals | Search 1 deal | Correct singular grammar. Larger counts retain their existing formatting. |
| Home search field: empty inventory | Search 0 deals | Search deals | Keep a useful field label without a zero-inventory pitch. |
| Home tagline: empty inventory | 0 live deals | Offers checked before publication | Explain the publication standard; positive counts stay factual and unchanged. |
| Home: empty static and browser-rendered wall | No qualifying deals right now | Verified offers appear here after passing our price checks. | Explain why offers are shown, without unfinished-site wording. |
| Home: empty filter result fallback | No deals match the current filters | Try changing your filters to explore more deals. | Give the shopper a next action. |
| Home: search/filter recovery heading | No matching live deals | Try a broader search or different filters | Give the shopper a next action; existing matching rules and recovery choices stay unchanged. |
| Home: no offers above the configured discount threshold | No deals above the {N}% alert bar right now | Browse more verified offers below. | Remove internal alert jargon and point to the existing offers. |
| Home: offers below the configured discount threshold | Strong deals — below the {N}% alert bar, still big savings | More verified deals — under {N}% off | Describe the same discount boundary in shopping language. If the threshold is unavailable, the suffix is “more savings” rather than “below the alert bar, still big savings.” |
| Saved: empty list heading | Saved deals (0) | Saved deals | Keep the existing “Save a deal to revisit it here” instruction without a redundant zero. |
| Category hubs: no qualifying seven-day history | No deal in this category passed our checks in the last 7 days. | We publish offers in this category only when they pass our price checks. | State the standard calmly; populated historical statistics remain unchanged. |
| Category hubs: empty open/free-site category | No deal in this category right now; new deals land at every drop on the live wall. | This category features offers that pass our price checks. Explore all live deals. | Remove an empty-category pitch and the promise of new inventory every drop; keep the home link. |
| Category hubs: empty free selection in membership mode | No free deal in this category right now; the free deals change at every drop on the live wall. | Browse the current free deals on the deal wall. They rotate at every drop. | Direct shoppers to the existing free selection; preserve rotation meaning. |
| Best-deals-today: empty selection, intro and description | The 0 best of {count} live deals right now, ranked like the live wall. | Our top picks are selected from offers that pass our price checks. | Avoid a zero-picks claim. Populated selection wording is unchanged. |
| Best-deals-today: empty card section | Empty grid beneath a hidden “Best live deals” heading | Explore current verified deals. (linked to `/`) | Replace the empty section with a useful route to the wall. |
| Product history: no current offer answer (also used in page descriptions) | No live deal right now. | A current offer is not listed for this product. | Clearly distinguish a historical record from a current offer. Existing lowest-seen facts follow unchanged. |
| Product history: insufficient chart observations | Not enough recorded checks to chart a price history yet. | Price charts require at least three recorded checks spanning two days. | Explain the existing chart requirement without suggesting an unfinished page. |
| Product history: absent current-offer action | No live deal currently published. | Explore current verified deals. (“current verified deals” linked to `/`) | Offer a next action; the answer above still explicitly states that this product has no listed current offer. |
| Product history: dates in offer history | 2026-10-09 (UTC), for example | Oct 9, 2026 (UTC) | Match the surrounding readable dates. Machine-readable timestamps and UTC meaning are unchanged. |
| `/p/` index: no indexable histories | No price histories available yet. | Price histories are published only when they meet our verification standards. Browse verified deals. (“Browse verified deals” linked to `/`) | Describe the existing publication gate and provide navigation. |
| Digest email subject: populated | dealradar digest — {N} deals >={floor}% — Oct 09 | wowpriced digest — {N} deal/deals at {floor}% off or more — Oct 9, 2026 | Consistent public branding, singular grammar and readable threshold/date. A configured custom-domain brand is respected. |
| Digest email subject: empty | dealradar digest — 0 deals >={floor}% — Oct 09 | wowpriced digest — price-check update — Oct 9, 2026 | Present a factual status update without a zero-deals headline. |
| Digest email: empty body, plain text and HTML | no qualifying deals in the last 24 h | Today's price-check update: no new offers met our deal criteria in the last 24 hours. | Complete, calm sentence; the same 24-hour result is reported. |
| Newsletter/member/confirmation email: display brand when no custom domain is configured | dealradar | wowpriced | Match the public site's name. Sender addresses and the shipped custom-domain brand are unchanged. |
| Newsletter: dated heading in plain text and HTML | The day's top {N} deals — Friday, Oct 09 | The day's top {N} deals — Friday, Oct 9, 2026 | Readable date with a year, matching other history/digest dates. Existing local timezone remains. |
| Newsletter plain text: absent non-Amazon price | $? | Price not listed | Replace a placeholder with an explicit missing fact. Amazon price suppression stays unchanged. |

## Reviewed and left unchanged

- The operator's examples “Be first … when it launches” and “starting soon”
  were absent from this checkout's published copy. The new visible-text guard
  rejects `coming soon|launch|beta|starting soon|be first|lorem|TODO`, ignoring
  scripts, styles, comments, attributes and hidden content.
- Product titles, source instructions, niche car parts and ordering: these are
  source facts/ranking, outside this copy-only brief. The existing broad-appeal
  ranking rule remains in place; no inventory is invented or recategorized.
- Privacy, payment/renewal terms, exact Amazon disclaimer, FTC/affiliate text,
  checkout requirements, missing-price warnings and evidence qualifiers: retain
  their meanings and wording. Only Disclosure's help-link label changed.
- “Price checked Xm ago” chips and Amazon absolute times: untouched. Named ET
  and UTC timestamps retain their distinct contexts; machine-readable ISO dates
  and the chart's accessible UTC table retain their precision.
- Currency displays: existing USD amounts and numeric precision are unchanged.
  Compact card dollar amounts, explicit USD on detail pages, and payment-plan
  prices have distinct, useful contexts; no price formatting or math changed.
- Badge labels already use shopper language (“2+ sources”, “price history”,
  “verified”). Internal `tier`/`corroborated` identifiers are not visible copy.
  Evidence qualifiers, source credits, and genuine nonzero/zero statistical facts
  remain accurate rather than being embellished.
- Header, inline and footer sign-up/support prompts follow standing operator
  decisions and existing suppression rules. No new CTA, sign-up step, script,
  image, font or layout redesign was added. Existing privacy opt-outs and
  accessibility semantics remain.
- About, Contact, Careers, Deal hacks, Join, Subscribe, 404 and legal bodies were
  already concise; their substantive content remains. The footer already had
  every required trust destination and a copyright year computed from the render
  time; the verification label is now consistent. Tests retain next-year coverage.
- Customer email delivery remains disabled by the shipped configuration. Email
  copy was checked through offline builders; nothing was sent and no LLM was used.
- Unsent legacy email edge states: newsletter/member senders skip empty batches,
  and publication gates exclude missing-price deals. Their unused zero-count
  formatting and the shared message formatter's unknown-price/discount markers
  remain unchanged; operator-only diagnostics are outside public copy.

## Validation

- Ten fixed-time offline trees (membership, open, support, legacy shipped fixture
  and actual shipped configuration; populated and empty) retain identical file
  sets and identical deal facts/metadata except the derived `site_version`.
- Twelve additional product-history trees cover live, sparse and past-offer
  states across four modes. Across all 22 trees (829 public files), original
  links and machine-readable timestamps are preserved; only the documented
  home links were added. Privacy/Disclosure bodies match their originals apart
  from the documented shared footer and verification-link copy.
- The visible-text/trust-link guard covers empty/populated pages in four access
  modes, including product histories, product families and store/price pages.
- All 84 focused baseline/byte-preservation checks pass. Three older browser
  harness failures were reproduced on untouched HEAD and repaired by loading
  the existing niche-order helper; their behavioral assertions remain.
- Final full suite: `.venv/bin/python -m pytest -q -n 2 -p no:cacheprovider
  --basetemp=/tmp/task212-full` (compact tracebacks): **4,541 passed, 7 failed**
  in 18m32s. All seven are Stripe workflow localhost-socket permission failures
  reproduced on untouched HEAD; no new failures. Offline idempotent reruns pass.
  Review and those seven checks outside this sandbox remain; see `docs/HANDOFF.md`.
