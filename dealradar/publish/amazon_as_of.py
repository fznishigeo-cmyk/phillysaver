"""Optional Amazon price disclosures, using only the existing check clock."""

import re
from zoneinfo import ZoneInfo

BLOCK = re.compile(r"/\*amazon-as-of\*/(.*?)/\*/amazon-as-of\*/", re.S)
FOOTER = ("Amazon prices are accurate as of the time shown and may change; "
          "the price on Amazon at checkout applies.")
DISCLAIMER = ("Product prices and availability are accurate as of the date/time indicated and are "
              "subject to change. Any price and availability information displayed on Amazon.com at the "
              "time of purchase will apply to the purchase of this product.")
STYLE = """.amazon-as-of,.amazon-price-details{color:var(--muted);font-size:12.5px;line-height:1.5}
.amazon-price-details summary{cursor:pointer;min-height:44px;align-content:center}
.amazon-price-details summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
"""


def template(text, enabled):
    return BLOCK.sub(lambda m: m[1] if enabled else "", text)


def time_text(checked):
    eastern = checked.astimezone(ZoneInfo("America/New_York"))
    return f'{eastern:%b} {eastern.day}, {eastern.hour % 12 or 12}:{eastern:%M %p %Z}'
