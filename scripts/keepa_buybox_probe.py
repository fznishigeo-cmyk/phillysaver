"""One read-only, bounded Keepa request; never prints arbitrary response text."""

import json
import logging
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dealradar import buybox
from dealradar.config import get_secret, load_config
from dealradar.db import DB
from dealradar.http_client import PoliteFetcher
from dealradar.keepa_budget import response_payload
from dealradar.publish.site import WALL_META
from dealradar.savings import exceeds_price_tolerance

# No prior Buy Box reference in the repo: the brief's buybox=1 assumption
# is confirmed by https://keepa.com/api-docs/product.html#buybox (2026-10-15).
# buybox=1 adds 2 tokens/product to the base 1. It exposes existing Buy Box
# data, NOT a fresh offers scan. No offers/rating/stock/update=0 or retries.
# Thirty ASINs therefore cost at most 90, under the hard 100-token ceiling.
MAX_TOKENS, MAX_ASINS, TOKENS_PER_ASIN = 100, 30, 3


def selected(db):
    def keys(name):
        try:
            rows = json.loads(db.meta_get(name) or "[]")
            return [k for k in rows if isinstance(k, str)] if isinstance(rows, list) else []
        except ValueError:
            return []

    # render_site saves WALL_META in actual published pick order, then the
    # rest of the wall. PICKS_META is a wider set of scheduler candidates,
    # including unpublished ones, so it cannot establish publication/order.
    order = list(dict.fromkeys(keys(WALL_META)))
    rows, asins = [], set()
    for key in order:
        deal = db.get_deal(key)
        if not deal or deal.get("status") != "alive" or not buybox.eligible(deal):
            continue
        asin = str(deal.get("asin") or "").upper()
        if not re.fullmatch(r"[A-Z0-9]{10}", asin) or asin in asins:
            continue
        asins.add(asin)
        rows.append(dict(deal, asin=asin))
        if len(rows) >= min(MAX_ASINS, MAX_TOKENS // TOKENS_PER_ASIN):
            break
    return rows


def probe(db, fetcher, key):
    rows = selected(db) if key else []
    report = {"requested": len(rows), "token_ceiling": MAX_TOKENS,
              "max_tokens_requested": len(rows) * TOKENS_PER_ASIN,
              "tokensConsumed": None, "tokensLeft": None,
              "within_2pct": 0, "within_10pct": 0, "above_10pct": 0,
              "missing_buybox": 0, "examples": []}
    if not key or not rows:
        return dict(report, status="missing_key" if not key else "no_published_candidates")
    # Silence HTTP libraries too: the required API key travels in the URL.
    disabled = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        response = fetcher.api_get("https://api.keepa.com/product", params={
            "key": key, "domain": 1, "asin": ",".join(d["asin"] for d in rows),
            "stats": 1, "history": 0, "buybox": 1}, max_attempts=1,
            respect_robots=False, conditional=False, follow_redirects=False)
        payload = response_payload(response)
        payload = payload if isinstance(payload, dict) else {}
        for name in ("tokensConsumed", "tokensLeft"):
            value = payload.get(name)
            if type(value) in (int, float) and math.isfinite(value) and (name == "tokensLeft" or value >= 0):
                report[name] = value
        report["http_status"] = response.status_code
        if response.status_code != 200:
            return dict(report, status="http_error")
        if report["tokensConsumed"] is not None and report["tokensConsumed"] > MAX_TOKENS:
            return dict(report, status="token_cost_changed")
        products = payload.get("products")
        if not isinstance(products, list):
            return dict(report, status="invalid_response")
        products = {p.get("asin"): p for p in products
                    if isinstance(p, dict) and isinstance(p.get("asin"), str)}
        for deal in rows:
            values = buybox.prices(products.get(deal["asin"], {}))
            total = values["buybox"]
            if total is None:
                report["missing_buybox"] += 1
            else:
                # Nested counts: within 2% is also within 10%; lower is OK.
                report["within_2pct"] += not exceeds_price_tolerance(total, deal["final_price"], 2)
                above = exceeds_price_tolerance(total, deal["final_price"])
                report["within_10pct"] += not above
                report["above_10pct"] += above
            if len(report["examples"]) < 10:
                report["examples"].append({"asin": deal["asin"], "final": deal["final_price"], **values})
        return dict(report, status="ok")
    except Exception:
        return dict(report, status="request_failed")  # never echo URL/exception/body
    finally:
        logging.disable(disabled)


def main():
    # State is genuinely read-only; HTTP housekeeping uses an ephemeral DB.
    cfg = load_config()
    cache = DB(":memory:")
    fetcher = PoliteFetcher(cache, cfg)
    try:
        db = DB("state.db", read_only=True)
        try:
            report = probe(db, fetcher, get_secret("KEEPA_KEY"))
        finally:
            db.close()
    except Exception:
        report = {"status": "state_unavailable"}
    finally:
        fetcher.close()
        cache.close()
    print(json.dumps(report, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
