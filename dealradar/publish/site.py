"""Static site publisher: writes deals.json / health.json and copies templates.

Storage policy: only extracted facts, a product link, and credit to the source
are written — never full post text or images.
"""

from __future__ import annotations

import json
import hashlib
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import redact_secrets
from ..util import iso, parse_iso
from .paywall import split_teaser

logger = logging.getLogger("dealradar.publish.site")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Minutes between publishes per run kind (fast.yml: */10, hourly.yml: 0 * * * *).
# The page's "next drop" countdown reads meta.cadence_minutes instead of
# guessing; an unknown kind gets the fast lane's, the site's shortest cadence.
_CADENCE_MINUTES = {"fast": 10, "hourly": 60}


def util_now():
    from ..util import now_utc
    return now_utc()


def site_template_version() -> str:
    """Short hash of the home page template: changes with every template
    edit, so a visitor's cached page knows a newer one was published."""
    import hashlib
    data = (TEMPLATES_DIR / "index.html").read_bytes()
    return hashlib.sha256(data).hexdigest()[:12]


def rewrite_affiliate(url: str) -> str:
    """Return the product URL unchanged.

    Affiliate rewriting is intentionally NOT implemented. This function exists
    as the single injection point for it later: nothing else in the codebase
    may rewrite outbound links. When (if ever) affiliate tagging is added, it
    happens here and only here.
    """
    return url


def _headline(deal: dict[str, Any]) -> str:
    """One plain-English sentence or two per deal, for humans skimming the page."""
    item = deal.get("item") or "This item"
    store = deal.get("store") or "an online store"
    final = deal.get("final_price")
    reg = deal.get("regular_price")
    pct = deal.get("pct_at_checkout")
    flags = deal.get("flags") or []
    codes = deal.get("codes") or []

    if final is None:
        lead = f"{item} at {store}"
    elif final <= 0.01:
        lead = f"{item} is free (or a penny) at {store}"
    else:
        lead = f"{item} is ${final:,.2f} at {store}"
    if pct is not None:
        lead += f" — {pct:.0f}% off"
        if reg:
            lead += f" the usual ${reg:,.2f}"
        elif deal.get("basis") == "list_price_claim":
            lead += " the claimed list price"
    parts = [lead + "."]
    if codes:
        parts.append(("Code " if len(codes) == 1 else "Codes ") + ", ".join(codes) + ".")
    if "range" in flags:
        parts.append("Price varies by option; the discount holds even at the highest price.")
    if "rebate_dependent" in flags:
        parts.append("Needs a rebate to reach this price.")
    if "unknown_shipping" in flags:
        parts.append("Shipping cost unclear — check at checkout.")
    tier = deal.get("verify_tier", "unverified")
    if tier == "corroborated":
        parts.append("Confirmed by multiple independent sources.")
    elif tier == "history-checked":
        parts.append("Well below the usual price in our own price history.")
    else:
        parts.append("Reported by one source — not yet confirmed.")
    expires = parse_iso(deal.get("expires_at") or "")
    if expires is not None:
        parts.append(f"Expires {expires.strftime('%b %d')}.")
    return " ".join(parts)


def _serialize_deal(deal: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Project a deal dict to the public JSON shape (facts + link + credit)."""
    first_seen = parse_iso(deal.get("first_seen_at") or "")
    age_hours: float | None = None
    if first_seen is not None:
        age_hours = round((now - first_seen).total_seconds() / 3600.0, 2)
    credits = [
        {"name": s.get("source_name", ""), "url": s.get("source_url", "")}
        for s in (deal.get("sightings") or [])
    ]
    status = deal.get("status", "alive")
    # No resolved product URL -> link the deal to the source post that
    # reported it (which also carries the credit) rather than nothing.
    canon = deal.get("canonical_url") or ""
    url = rewrite_affiliate(canon) if canon else \
        (credits[0]["url"] if credits else "")
    return {
        "dedupe_key": deal.get("dedupe_key", ""),
        # Opaque id (locked stubs carry only this: the dedupe key names the
        # product link or the item words, which a visitor must not see).
        "key": hashlib.sha256(str(deal.get("dedupe_key", "")).encode("utf-8")).hexdigest()[:16],
        "store": deal.get("store", ""),
        "item": deal.get("item", ""),
        "brand": deal.get("brand", ""),
        "url": url,
        "image_url": deal.get("image_url") or "",
        "final_price": deal.get("final_price"),
        "regular_price": deal.get("regular_price"),
        "pct_at_checkout": deal.get("pct_at_checkout"),
        "pct_with_rebates": deal.get("pct_with_rebates"),
        "abs_savings": deal.get("abs_savings"),
        "basis": deal.get("basis", ""),
        "flags": deal.get("flags") or [],
        "codes": deal.get("codes") or [],
        "stack_steps": deal.get("stack_steps") or [],
        "rebates": deal.get("rebates") or [],
        "deferred_value": deal.get("deferred_value", ""),
        "conditions": deal.get("conditions") or [],
        "online_only": deal.get("online_only", "unknown"),
        "shipping_cost_or_threshold": deal.get("shipping_cost_or_threshold", ""),
        "quantity_or_unit": deal.get("quantity_or_unit", ""),
        "region": deal.get("region", "US"),
        "currency": deal.get("currency", "USD"),
        "category": deal.get("category") or "other",
        "expires_at": deal.get("expires_at", ""),
        "badge": deal.get("verify_tier", "unverified"),
        "headline": _headline(deal),
        "status": status,
        "alive": status == "alive",
        "died_at": deal.get("died_at", ""),
        "first_seen_at": deal.get("first_seen_at", ""),
        "last_seen_at": deal.get("last_seen_at", ""),
        "confidence": deal.get("confidence"),
        "score": deal.get("score"),
        "age_hours": age_hours,
        "credits": credits,
    }


def render_site(db, cfg, now: datetime, *, out_dir: str | Path | None = None,
                deals: list[dict[str, Any]] | None = None,
                last_run: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write deals.json, health.json and the two HTML templates to out_dir."""
    if deals is None:
        from dealradar import rank  # lazy: avoid hard import cycle at module load

        # The site shows qualifying deals that recently died too (marked dead);
        # alerts and the digest use rank.published_deals (alive only).
        deals = rank.site_deals(db, cfg, now)

    out = Path(out_dir) if out_dir is not None else Path(cfg.site_dir)
    out.mkdir(parents=True, exist_ok=True)

    serialized = [_serialize_deal(d, now) for d in deals]
    # The public file is a teaser when the paywall is on: the top deals in
    # full, the rest as locked stubs (docs/paywall.md). The full payload is
    # returned for the member upload and never written to the site.
    teaser, locked_count = split_teaser(serialized, cfg)
    site_version = site_template_version()
    meta = {
        "site_version": site_version,
        "generated_at": iso(now),
        # The moment this file was built (now is the run's start; a long
        # run publishes minutes later and the page's countdown anchors here).
        "published_at": iso(util_now()),
        "last_run": last_run or {},
        "cadence_minutes": _CADENCE_MINUTES.get(
            (last_run or {}).get("kind", ""), _CADENCE_MINUTES["fast"]),
        "disclosure": cfg.ftc_disclosure,
        "count": len(serialized),
        # Membership links (empty until the operator pastes them in
        # config.yaml); the page hides the Subscribe button when empty.
        "subscribe_url": getattr(cfg, "subscribe_url", "") or "",
        "portal_url": getattr(cfg, "portal_url", "") or "",
        # Free daily email list: the subscribe Worker's URL (the forms
        # POST to it); empty until the Worker is deployed and pasted in.
        "newsletter_url": getattr(cfg, "newsletter_worker_url", "") or "",
        "floors": {
            "min_pct": cfg.min_pct,
            "min_abs_savings": cfg.min_abs_savings,
            "site_show_from_pct": min(
                cfg.min_pct,
                getattr(cfg, "site_show_from_pct", cfg.min_pct)),
        },
        # Paywall: the page draws the locked cards and the auth modal
        # from these; api_url empty = no paywall UI at all.
        "api_url": getattr(cfg, "paywall_api_url", "") or "",
        "google_client_id": getattr(cfg, "paywall_google_client_id", "") or "",
        "teaser_count": int(getattr(cfg, "paywall_teaser_count", 8)),
        "locked_count": locked_count,
        "trial_days": int(getattr(cfg, "paywall_trial_days", 7)),
    }
    full_doc = {"meta": meta, "deals": serialized}
    (out / "deals.json").write_text(
        json.dumps({"meta": meta, "deals": teaser}, indent=2), encoding="utf-8"
    )

    sources = []
    for row in db.all_source_health():
        entry = dict(row)
        # Defense in depth: last_error was already scrubbed when recorded, but
        # health.json is PUBLIC, so scrub again at the publishing boundary.
        entry["last_error"] = redact_secrets(entry.get("last_error") or "")
        until = parse_iso(row.get("tripped_until") or "")
        entry["tripped"] = until is not None and now < until
        sources.append(entry)
    health_doc = {
        "generated_at": iso(now),
        "sources": sources,
        "last_run": last_run or {},
    }
    (out / "health.json").write_text(
        json.dumps(health_doc, indent=2), encoding="utf-8"
    )

    pages = ("index.html", "health.html", "welcome.html", "about.html",
             "careers.html", "contact.html", "how-it-works.html", "hacks.html",
             "subscribe.html")
    for name in pages:
        shutil.copyfile(TEMPLATES_DIR / name, out / name)
    # The home page learns its own version so a cached copy reloads itself
    # when a newer publish (meta.site_version) appears.
    index = (out / "index.html").read_text(encoding="utf-8")
    (out / "index.html").write_text(index.replace("__SITE_VERSION__", site_version, 1),
                                    encoding="utf-8")

    # Every page carries the operator's email (the contact line, and "or
    # email ..." wherever cancelling is explained) only once it is really
    # configured; the unset placeholder must never reach the public site
    # (the pages drop their email wording when the token resolves to "").
    # It lands in HTML attributes and script strings, so only a plain
    # address is used.
    email = getattr(cfg, "contact_email", "") or ""
    if email == "set-me@example.com" or not re.fullmatch(r"[^\s@\"'<>\\&]+@[^\s@\"'<>\\&]+", email):
        email = ""
    for name in pages:
        html = (out / name).read_text(encoding="utf-8")
        if "{{CONTACT_EMAIL}}" in html:
            (out / name).write_text(html.replace("{{CONTACT_EMAIL}}", email),
                                    encoding="utf-8")

    # GitHub Pages keeps a custom domain only while the deployed site
    # carries this file; every run redeploys, so every run writes it.
    if getattr(cfg, "custom_domain", ""):
        (out / "CNAME").write_text(cfg.custom_domain + "\n", encoding="utf-8")

    logger.info("site rendered: %d deals (%d locked) -> %s",
                len(serialized), locked_count, out)
    return {"published": len(serialized), "out_dir": str(out),
            "full_payload": full_doc}
