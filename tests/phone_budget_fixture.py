"""Task 206 fixed publication input. Seed 206; no network or production state."""
import gzip
import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import indexnow, site
from dealradar.util import iso
from tests.test_publish_site import make_deal

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
MODES = ("support", "open", "closed", "shipped")


def fixture_config(mode):
    if mode == "shipped":
        cfg = load_config()
        cfg.dark_polish = False  # recorded before task 254
        return cfg
    return AppConfig(custom_domain="wowpriced.com", support_enabled=mode == "support",
                     support_payment_link="https://buy.stripe.com/test_task206",
                     paywall_enabled=True, paywall_open_access=mode == "open",
                     paywall_yearly_price_usd=49, paywall_api_url="https://account.example.invalid",
                     amazon_history="hide", amazon_as_of_enabled=True,
                     seo_indexnow_key="task206offlinefixture0001")


def fixture_offers():
    rng = random.Random(206)
    categories = ("electronics", "home", "kitchen", "beauty", "fashion", "toys_games",
                  "kids_baby", "health", "grocery", "auto", "sports_outdoors")
    names = ("Wireless headphones", "Cotton towels", "Steel kettle", "Skin care set",
             "Walking shoes", "Building blocks", "Baby blanket", "Electric toothbrush",
             "Coffee beans", "Car organizer", "Camping lantern")
    stores = (("Amazon", "www.amazon.com"), ("Walmart", "www.walmart.com"),
              ("Target", "www.target.com"), ("Best Buy", "www.bestbuy.com"),
              ("eBay", "www.ebay.com"), ("Macy's", "www.macys.com"), ("Woot", "www.woot.com"))
    prices = ((3.49, 17.45, 13.96), (7.99, 39.95, 31.96), (12.25, 61.25, 49.0),
              (19.95, 99.75, 79.8), (35.0, 175.0, 140.0), (59.99, 299.95, 239.96),
              (89.5, 447.5, 358.0))
    rows = []
    for i in range(1400):
        category = i % len(categories)
        store, host = stores[i % len(stores)]
        final, regular, savings = prices[rng.randrange(len(prices))]
        seen = iso(NOW - timedelta(minutes=15 + i % 180))
        code = f"SAVE{i % 19 + 1}" if i % 4 == 0 else ""
        asin = f"B0{i:08d}" if store == "Amazon" else ""
        rows.append(make_deal(
            dedupe_key=f"task206|{i:04d}", item=f"Fixture {names[category]} {i:04d}",
            brand=f"Fixture Brand {i % 17}", store=store, category=categories[category],
            canonical_url=f"https://{host}/dp/{asin}" if asin else f"https://{host}/item/{i:06d}",
            asin=asin, final_price=final, regular_price=regular, abs_savings=savings,
            pct_at_checkout=80.0, codes=[code] if code else [],
            stack_steps=[f"Enter code {code} at checkout"] if code else [],
            conditions=["used"] if i % 6 == 0 else ["new"],
            shipping_cost_or_threshold="free shipping", quantity_or_unit="2-pack" if i % 9 == 0 else "",
            image_url=f"https://images.example.invalid/task206/{i:04d}.jpg" if i % 10 else "",
            first_seen_at=seen, last_seen_at=seen, price_rechecked_at=iso(NOW - timedelta(minutes=10)),
            sightings=[{"source_name": "Fixture partner", "source_url": f"https://partner.example.invalid/deal/{i}",
                        "seen_at": seen, "final_price": final}]))
    return rows


def sizes(data):
    return {"raw": len(data), "gzip": len(gzip.compress(data, compresslevel=9, mtime=0))}


def manifest(root):
    return {p.relative_to(root).as_posix(): {**sizes(p.read_bytes()),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(root.rglob("*")) if p.is_file()}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def publish_fixture(root, cfg, *, inspect=None):
    db = DB(":memory:")
    out = root / "site"
    try:
        db.meta_set("teaser_recent", '["before"]')
        with patch.object(site, "util_now", lambda: NOW):
            result = site.render_site(db, cfg, NOW, out_dir=out, deals=fixture_offers(),
                                      last_run={"run_key": "task206-fixed"})
        versions = {url: row["content_hash"] for url, row in db.indexnow_pages().items()}
        assert versions
        assert db.indexnow_claim(indexnow.INDEXNOW_ENDPOINT, "task206-fixed", versions, NOW,
                                {url: iso(NOW) for url in versions})
        db.indexnow_finish(indexnow.INDEXNOW_ENDPOINT, "task206-fixed", NOW, 200)
        if inspect:
            inspect(db, out)
        return {"files": manifest(out), "state": digest(list(db.conn.iterdump())),
                "meta": digest([tuple(row) for row in db.conn.execute("SELECT * FROM meta ORDER BY k")]),
                "receipts": digest(db.indexnow_pages()), "member_payload": digest(result["full_payload"])}
    finally:
        db.close()


class Inline(HTMLParser):
    def __init__(self, out):
        super().__init__(convert_charrefs=False)
        self.out = out
        self.seen = set()
        self.active = None
        self.parts = {"js": [], "css": [], "json_ld": []}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "style":
            self.active = "css"
        if tag == "script":
            kind = attrs.get("type", "").split(";")[0].strip().lower()
            self.active = "json_ld" if kind == "application/ld+json" else "js" if kind in (
                "", "module", "text/javascript", "application/javascript") and "src" not in attrs else None
            if kind in ("", "module", "text/javascript", "application/javascript") and attrs.get("src"):
                # The frozen fixture uses root-relative JS only; keep this
                # reference calculation separate from the analyzer under test.
                url = urlsplit(attrs["src"])
                assert not url.scheme and not url.netloc
                name = url.path.lstrip("/")
                if name not in self.seen:
                    self.parts["js"].append((self.out / name).read_bytes())
                    self.seen.add(name)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.active = None

    def handle_data(self, data):
        if self.active:
            self.parts[self.active].append(data.encode())


def reference_sizes(out):
    parser = Inline(out)
    parser.feed((out / "index.html").read_text())
    return {"home_html": sizes((out / "index.html").read_bytes()),
            "home_js": sizes(b"".join(parser.parts["js"])),
            "home_css": sizes(b"".join(parser.parts["css"])),
            "deals_json": sizes((out / "deals.json").read_bytes()),
            "json_ld": sizes(b"".join(parser.parts["json_ld"]))}
