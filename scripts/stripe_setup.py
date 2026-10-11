"""Idempotent Stripe setup for the membership: product, monthly price,
Payment Link, customer-portal configuration. Prints the links — never the key.

Runs in GitHub Actions (.github/workflows/stripe-setup.yml) with
STRIPE_SECRET_KEY from the repository secrets; the sandbox this project is
developed in cannot reach api.stripe.com, the runner can. Re-running finds
the objects it created before (metadata app=wowpriced) instead of
duplicating them, so it is safe to run again in live mode with a live key.
The Payment Link redirects after payment to
welcome.html?session_id={CHECKOUT_SESSION_ID} (the page claims the payment
by that id); a link found with another redirect is updated to it.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.stripe.com/v1"
APP_TAG = {"metadata[app]": "wowpriced"}
PRODUCT_NAME = os.environ.get("WOWPRICED_PRODUCT_NAME", "WowPriced Alerts")
PRICE_CENTS = int(os.environ.get("WOWPRICED_PRICE_CENTS", "999"))
SITE = os.environ.get("WOWPRICED_SITE", "https://wowpriced.com").rstrip("/")
# After payment: welcome.html claims the payment by its Checkout Session id,
# which Stripe fills into the placeholder (docs/paywall.md "Claiming a payment").
WELCOME_URL = SITE + "/welcome.html?session_id={CHECKOUT_SESSION_ID}"


def _key() -> str:
    key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not key:
        sys.exit("STRIPE_SECRET_KEY is not set (add it as a repository secret)")
    return key


def call(method: str, path: str, data: dict | None = None) -> dict:
    url = API + path
    body = urllib.parse.urlencode(data or {}).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    token = base64.b64encode((_key() + ":").encode()).decode()
    req.add_header("Authorization", "Basic " + token)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        sys.exit(f"Stripe {method} {path} -> HTTP {e.code}: {detail}")


def find(path: str, pred) -> dict | None:
    page = call("GET", path + ("&" if "?" in path else "?") + "limit=100")
    for obj in page.get("data", []):
        if pred(obj):
            return obj
    return None


def main() -> None:
    mode = "LIVE" if _key().startswith("sk_live") or _key().startswith("rk_live") else "TEST"
    print(f"Stripe mode: {mode}")

    product = find("/products?active=true",
                   lambda p: (p.get("metadata") or {}).get("app") == "wowpriced")
    if product is None:
        product = call("POST", "/products", {
            "name": PRODUCT_NAME,
            "description": "Every verified deal, 50-90% off, emailed to you the "
                           "hour it is found. Cancel any time.",
            **APP_TAG,
        })
        print(f"created product {product['id']}")
    else:
        print(f"found product {product['id']}")

    price = find(f"/prices?product={product['id']}&active=true",
                 lambda p: p.get("unit_amount") == PRICE_CENTS
                 and (p.get("recurring") or {}).get("interval") == "month"
                 and p.get("currency") == "usd")
    if price is None:
        price = call("POST", "/prices", {
            "product": product["id"], "currency": "usd",
            "unit_amount": str(PRICE_CENTS), "recurring[interval]": "month",
            **APP_TAG,
        })
        print(f"created price {price['id']}")
    else:
        print(f"found price {price['id']}")

    link = find("/payment_links?active=true",
                lambda l: (l.get("metadata") or {}).get("app") == "wowpriced")
    if link is None:
        link = call("POST", "/payment_links", {
            "line_items[0][price]": price["id"],
            "line_items[0][quantity]": "1",
            # Explicit: a fresh account has no "dynamic" methods configured
            # yet and rejects the link outright without this.
            "payment_method_types[0]": "card",
            "after_completion[type]": "redirect",
            "after_completion[redirect][url]": WELCOME_URL,
            "allow_promotion_codes": "true",
            **APP_TAG,
        })
        print(f"created payment link {link['id']}")
    else:
        print(f"found payment link {link['id']}")
        redirect = ((link.get("after_completion") or {}).get("redirect") or {}).get("url")
        if redirect != WELCOME_URL:  # e.g. a link made before the claim needed the session id
            link = call("POST", f"/payment_links/{link['id']}", {
                "after_completion[type]": "redirect",
                "after_completion[redirect][url]": WELCOME_URL,
            })
            print(f"updated payment link {link['id']}: after payment it redirects to {WELCOME_URL}")

    portal = find("/billing_portal/configurations?active=true",
                  lambda c: (c.get("metadata") or {}).get("app") == "wowpriced")
    if portal is None:
        portal = call("POST", "/billing_portal/configurations", {
            "business_profile[headline]": f"{PRODUCT_NAME} membership",
            "features[subscription_cancel][enabled]": "true",
            "features[subscription_cancel][mode]": "at_period_end",
            "features[invoice_history][enabled]": "true",
            "features[payment_method_update][enabled]": "true",
            "default_return_url": SITE + "/",
            **APP_TAG,
        })
        print(f"created customer-portal configuration {portal['id']}")
    else:
        print(f"found customer-portal configuration {portal['id']}")

    summary = (
        f"## Stripe setup ({mode})\n\n"
        f"- Product: `{product['id']}` — {PRODUCT_NAME}\n"
        f"- Price: `{price['id']}` — ${PRICE_CENTS / 100:.2f}/month\n"
        f"- **Payment Link (paste into config.yaml members.subscribe_url):** {link['url']}\n"
        f"- Customer portal configuration: `{portal['id']}` (the shareable "
        f"portal *login link* is a dashboard toggle: Settings → Billing → "
        f"Customer portal → Activate link)\n"
    )
    print(summary)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(summary)


if __name__ == "__main__":
    main()
