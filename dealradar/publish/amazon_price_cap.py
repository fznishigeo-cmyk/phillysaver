"""Operator 2026-10-20: optional price cap on the yellow Amazon discount exits."""

import hashlib
import re
from pathlib import Path

# Amazon's discounts (23566065011) and coupons (210906366011) filters, as the
# search-results, Discount Finder and Deal Checker exits write them. A capped
# URL is followed by %2C, so applying the cap twice changes nothing.
FILTER = re.compile(r'(p_n_deal_type%3A(?:23566065011|210906366011))&')


def enabled(cfg):
    return getattr(cfg, 'amazon_price_cap', False) is True


def cap(text, cfg):
    if not enabled(cfg):
        return text
    cents = round(cfg.amazon_price_cap_usd * 100)
    return FILTER.sub(lambda m: m[1] + '%2Cp_36%3A-' + str(cents) + '&', text)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256((version + str(cfg.amazon_price_cap_usd)).encode()
                          + Path(__file__).read_bytes()).hexdigest()[:12]


def publish(out, cfg):
    if not enabled(cfg):
        return
    for path in out.rglob('*.html'):
        html = path.read_text()
        capped = cap(html, cfg)
        if capped != html:
            path.write_text(capped)
