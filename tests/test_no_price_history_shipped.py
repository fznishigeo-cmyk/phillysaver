"""Operator 2026-10-17: Amazon's email, then "drop price history" for every store.

The shipped config publishes no price-history page, link, chart or past-price
wording for any store; history stays a hidden backend signal.
"""

import re

from dealradar.config import load_config
from tests.test_publish_site import make_deal
from tests.test_support import render

HISTORY_WORDS = re.compile(r"price history|price-history|lowest we|highest we|number of our checks|"
                           r"sale pattern|90-day|in the last 90 days", re.I)
# Generic shopping advice ("Check price history") is not our price data, nor
# is the browser script's tier label, which no published deal can carry.
ADVICE = {"hacks.html"}
UNUSED_LABEL = '"history-checked": "✓ price history"'


def test_shipped_config_publishes_no_price_history(tmp_path):
    cfg = load_config()
    assert cfg.amazon_history == "hide" and not cfg.seo_product_pages_enabled
    assert set(cfg.store_policy.values()) == {"live_only"}
    deals = [make_deal(),
             make_deal(dedupe_key="amazon|socks", store="Amazon", item="Cotton Socks 6 Pack",
                       brand="Hanes", canonical_url="https://www.amazon.com/dp/B000000001",
                       asin="B000000001"),
             make_deal(dedupe_key="target|towels", store="Target", item="Bath Towels",
                       brand="Threshold", canonical_url="https://www.target.com/p/towels/-/A-1")]
    out, doc, full, _ = render(tmp_path, cfg, deals)
    assert not (out / "p").exists()
    assert all("product_path" not in d for d in doc["deals"])
    for path in out.rglob("*"):
        if path.is_file() and path.suffix in {".html", ".json", ".xml", ".txt"} and path.name not in ADVICE:
            assert not HISTORY_WORDS.search(path.read_text(errors="ignore").replace(UNUSED_LABEL, "")), path
    assert all(d.get("badge") != "history-checked" for d in doc["deals"])
