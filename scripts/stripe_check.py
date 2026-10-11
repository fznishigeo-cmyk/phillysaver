"""Read-only Stripe check for the card-required trial checkout.

Runs in GitHub Actions (.github/workflows/stripe-check.yml) with
STRIPE_SECRET_KEY from the repository secrets, because the sandbox this
project is developed in cannot reach api.stripe.com. It repeats exactly what
the Worker's POST /api/checkout does (cloudflare/subscribe-worker/billing.js)
and prints Stripe's own answer at each step, never the key:
  1. find the active Payment Link whose URL is STRIPE_PAYMENT_LINK and the
     price on it (must be a recurring monthly price for mode=subscription);
  2. create a Checkout Session with the Worker's parameters (card only, card
     collected up front, 7-day trial) for a throwaway address, then expire it
     at once: an unpaid session charges nothing;
  3. try the subscription Search API query the membership check uses;
  4. list the billing-portal configurations (the "Manage or cancel" page).
Exit code 0 when the trial checkout works, 1 otherwise.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.stripe.com/v1"
SITE = os.environ.get("WOWPRICED_SITE", "https://wowpriced.com").rstrip("/")


def _key() -> str:
    key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not key:
        sys.exit("STRIPE_SECRET_KEY is not set")
    return key


def call(method: str, path: str, data: list[tuple[str, str]] | None = None):
    """(ok, body): Stripe's JSON body either way; never raises on HTTP errors."""
    body = urllib.parse.urlencode(data or []).encode() if data is not None else None
    req = urllib.request.Request(API + path, data=body, method=method)
    req.add_header("Authorization", "Basic " + base64.b64encode((_key() + ":").encode()).decode())
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return True, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return False, json.loads(e.read().decode("utf-8", errors="replace"))
        except ValueError:
            return False, {"error": {"message": f"HTTP {e.code}"}}


def err(body: dict) -> str:
    e = body.get("error") or {}
    return f"{e.get('type', '')} {e.get('code', '')} param={e.get('param', '')}: {e.get('message', '')}".strip()


def link_key(url: str) -> str:
    u = urllib.parse.urlsplit((url or "").strip())
    return f"{u.scheme.lower()}://{u.netloc.lower()}{u.path.rstrip('/')}"


def main() -> int:
    key = _key()
    mode = "live" if key.startswith(("sk_live_", "rk_live_")) else "test"
    print(f"Stripe key mode: {mode} ({'restricted' if key.startswith('rk_') else 'secret'} key)")
    want = link_key(os.environ.get("STRIPE_PAYMENT_LINK", ""))
    print(f"Payment Link to match: {want or '(none)'}")
    lines = []

    # 1. Payment Link -> price.
    price = ""
    ok, page = call("GET", "/payment_links?active=true&limit=100")
    if not ok:
        lines.append(f"FAIL listing payment links: {err(page)}")
    else:
        links = page.get("data", [])
        print(f"active payment links: {len(links)}")
        for pl in links:
            print(f"  - {pl.get('id')} url={pl.get('url')} app={(pl.get('metadata') or {}).get('app')}")
        hit = next((pl for pl in links if link_key(pl.get("url", "")) == want), None)
        if not hit:
            lines.append("FAIL no ACTIVE payment link has that URL (the Worker finds no price)")
        else:
            ok, items = call("GET", f"/payment_links/{hit['id']}/line_items?limit=1")
            p = (items.get("data") or [{}])[0].get("price") if ok else None
            if not ok or not p:
                lines.append(f"FAIL reading the link's line items: {err(items) if not ok else 'no items'}")
            else:
                price = p if isinstance(p, str) else p.get("id", "")
                rec = (p.get("recurring") or {}) if isinstance(p, dict) else {}
                desc = (f"{price}: {p.get('unit_amount')} {p.get('currency')} "
                        f"every {rec.get('interval_count', '?')} {rec.get('interval', 'ONE-TIME')}, "
                        f"active={p.get('active')}") if isinstance(p, dict) else price
                lines.append(f"OK price on the link: {desc}")
                if isinstance(p, dict) and not rec:
                    lines.append("FAIL the price is one-time; a subscription checkout needs a recurring price")

    # 2. The Worker's Checkout Session, created then expired at once.
    if price:
        params = [
            ("mode", "subscription"),
            ("line_items[0][price]", price), ("line_items[0][quantity]", "1"),
            ("payment_method_collection", "always"),
            ("client_reference_id", "wowpriced-stripe-check"),
            ("customer_email", "stripe-check@wowpriced.com"),
            ("subscription_data[trial_period_days]", "7"),
            ("subscription_data[metadata][wowpriced_ref]", "stripe-check"),
            ("success_url", SITE + "/welcome.html?session_id={CHECKOUT_SESSION_ID}"),
            ("cancel_url", SITE + "/?checkout=cancel"),
            ("expires_at", str(int(time.time()) + 1860)),
        ]
        ok, sess = call("POST", "/checkout/sessions", params)
        if ok:
            lines.append(f"OK trial checkout session created ({sess.get('id')}), payment methods "
                         f"{sess.get('payment_method_types')}, collection "
                         f"{sess.get('payment_method_collection')}; expiring it now")
            ok2, exp = call("POST", f"/checkout/sessions/{sess['id']}/expire", [])
            lines.append("OK session expired" if ok2 else f"WARN could not expire it: {err(exp)}")
        else:
            lines.append(f"FAIL creating the trial checkout session: {err(sess)}")

    # 3. Search API (membership by the account reference).
    q = urllib.parse.quote("metadata['wowpriced_ref']:'stripe-check'")
    ok, res = call("GET", f"/subscriptions/search?query={q}&limit=1")
    lines.append("OK subscription search works" if ok else f"FAIL subscription search: {err(res)}")

    # 4. Billing portal configurations.
    ok, cfgs = call("GET", "/billing_portal/configurations?active=true&limit=100")
    if ok:
        mine = [c for c in cfgs.get("data", []) if (c.get("metadata") or {}).get("app") == "wowpriced"]
        lines.append(f"OK portal configurations: {len(cfgs.get('data', []))} active, "
                     f"{len(mine)} tagged app=wowpriced"
                     + ("" if mine else " (run the 'stripe setup (one-off)' workflow to create it)"))
    else:
        lines.append(f"FAIL listing portal configurations: {err(cfgs)}")

    report = "\n".join(lines)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"## Stripe check ({mode})\n\n```\n{report}\n```\n")
    return 0 if price and any(l.startswith("OK trial checkout") for l in lines) else 1


if __name__ == "__main__":
    sys.exit(main())
