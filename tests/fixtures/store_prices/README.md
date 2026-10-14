Synthetic, recorded-shape fixtures for the official Best Buy Products and eBay
Browse/OAuth endpoints specified in task 084. IDs, amounts and the token are
offline test values, not live observations or credentials. No network capture
was made in this sandbox. Tests vary these fixtures for unavailable products,
price moves, malformed responses and HTTP failures.

Schema references: [Best Buy Products](https://bestbuyapis.github.io/api-documentation/)
and [eBay Browse](https://developer.ebay.com/api-docs/buy/static/api-browse.html).
The Products endpoint does not identify a specific open-box offer; those rows
are left alone. eBay auctions, variations without an exact offer, and checkout
conditions a listing cannot establish remain inconclusive. Shipping already
verified at ingest is retained and recalculated by savings.py, never inferred
from either API's listing price.
