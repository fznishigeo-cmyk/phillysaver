"""Price-evidence clocks and bounded reads of already credited partner posts."""

from dataclasses import replace
from datetime import timedelta
import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit

from . import util
from .models import RawPost
from .verify import is_amazon_deal

META = "non_amazon_freshness"
BUCKETS = ("<12h", "12-24h", "24-48h", "48-96h", ">96h")
STYLE = """.price-aged{color:#68717c;font-size:12.5px;font-weight:400;line-height:1.5;margin:2px 0 6px}
@media(prefers-color-scheme:dark){.price-aged{color:#a4adb7}}
"""


def evidence_at(deal, now):
    """Only successful evidence for the current price; attempts never count.

    A post counts once, when it first showed the current price: rereading the
    same unchanged post proves the partner has not ended it, not that the
    store still charges it (operator, 2026-10-14: stale Walmart prices)."""
    since = util.parse_iso(deal.get("price_since") or "")
    stamps = []
    if api_path(deal) and not deal.get("gone_suspect_at") and not deal.get("death"):
        stamps.append(util.parse_iso(deal.get("price_rechecked_at") or ""))
        stamps.append(util.parse_iso(deal.get("woot_listed_at") or ""))
    first = {}
    for sighting in deal.get("sightings") or []:
        if sighting.get("final_price") is not None and sighting["final_price"] == deal.get("final_price"):
            at = util.parse_iso(sighting.get("seen_at") or "")
            post = (sighting.get("source_name"), sighting.get("source_url"))
            if at is not None and (post not in first or at < first[post]):
                first[post] = at
    stamps += first.values()
    # Legacy rows with no sightings still have their original ingest evidence.
    stamps.append(util.parse_iso(deal.get("first_seen_at") or ""))
    return max((at for at in stamps if at is not None and at <= now
                and (since is None or at >= since)), default=None)


def age_h(deal, now):
    at = evidence_at(deal, now)
    return (now - at).total_seconds() / 3600 if at is not None else math.inf


def hide_hours(deal, cfg):
    key = re.sub(r"[^a-z0-9]", "", str(deal.get("store") or "").lower())
    for store, options in cfg.non_amazon_stores.items():
        if re.sub(r"[^a-z0-9]", "", str(store).lower()) == key and isinstance(options, dict):
            try:
                value = float(options.get("stale_hide_h", cfg.non_amazon_stale_hide_h))
                if math.isfinite(value) and value > 0:
                    return value
            except (TypeError, ValueError):
                pass
    return cfg.non_amazon_stale_hide_h


def applies(deal, cfg):
    return cfg.non_amazon_freshness_enabled and not is_amazon_deal(deal)


def hidden(deal, cfg, now):
    return bool(applies(deal, cfg) and (deal.get("gone_suspect_at") or deal.get("death")
                or age_h(deal, now) > hide_hours(deal, cfg)))


def promotion_excluded(deal, cfg, now):
    return bool(deal.get("price_aged") or (applies(deal, cfg) and
                (age_h(deal, now) >= cfg.non_amazon_stale_label_h or hidden(deal, cfg, now))))


def label(deal, now):
    at = util.parse_iso(deal.get("first_seen_at") or "")
    days = max(1, (now - at).days) if at is not None and at <= now else 1
    return f"Posted {days} day{'s' if days != 1 else ''} ago"


def api_path(deal):
    from .store_prices import plain_price, store_offer
    from .verify import is_used_deal, woot_offer_url
    store = store_offer(deal)[0]
    if store and plain_price(deal) and not (store == "bestbuy" and is_used_deal(deal)):
        return store
    return "woot" if woot_offer_url(deal) else ""


def measure(rows, cfg, now):
    result = {}
    for deal in rows:
        if is_amazon_deal(deal) or deal.get("locked") or deal.get("status", "alive") != "alive":
            continue
        name = str(deal.get("store") or "Unknown")
        row = result.setdefault(name, dict.fromkeys((*BUCKETS, "unknown", "api_path", "source_only", "total"), 0))
        hours = age_h(deal, now)
        bucket = ("unknown" if not math.isfinite(hours) else BUCKETS[0] if hours < 12 else
                  BUCKETS[1] if hours < 24 else BUCKETS[2] if hours < 48 else
                  BUCKETS[3] if hours <= 96 else BUCKETS[4])
        row[bucket] += 1
        row["api_path" if api_path(deal) else "source_only"] += 1
        row["total"] += 1
    return dict(sorted(result.items()))


def before_snapshot(db, cfg, now):
    """The last actual public set; first deployment labels its eligible baseline."""
    from . import rank
    keys = db.meta_get("non_amazon_public_keys")
    if keys is not None:
        wanted = set(json.loads(keys))
        return measure([d for d in db.all_deals() if d["dedupe_key"] in wanted], cfg, now), "previous publish"
    try:
        rows = json.loads((Path(cfg.site_dir) / "deals.json").read_text())["deals"]
        wanted = {d["dedupe_key"] for d in rows if not d.get("locked") and d.get("status") == "alive"}
        return measure([d for d in db.all_deals() if d["dedupe_key"] in wanted], cfg, now), "previous publish"
    except (OSError, ValueError, KeyError, TypeError):
        return measure(rank.site_deals(db, replace(cfg, non_amazon_freshness_enabled=False), now),
                       cfg, now), "eligible baseline; previous publish unavailable"


def record_snapshot(db, cfg, now, before, published, checks, before_scope="previous publish"):
    # Use raw facts for the clocks; the public serializer omits evidence metadata.
    raw = {d["dedupe_key"]: d for d in db.all_deals(status="alive")}
    shown = [raw.get(d["dedupe_key"], d) for d in published
             if not d.get("locked") and d.get("status") == "alive"]
    before = {name: dict(row) for name, row in before.items()}
    after = measure(shown, cfg, now)
    for name in before.keys() | after.keys():
        empty = dict.fromkeys((*BUCKETS, "unknown", "api_path", "source_only", "total"), 0)
        before.setdefault(name, dict(empty))
        after.setdefault(name, dict(empty))
    values = {"at": util.iso(now), "before": dict(sorted(before.items())), "after": dict(sorted(after.items())),
              "before_scope": before_scope, "checks": checks}
    db.meta_set("non_amazon_public_keys", json.dumps([d["dedupe_key"] for d in shown if not is_amazon_deal(d)]))
    db.meta_set(META, json.dumps(values, sort_keys=True))
    return values


def report_data(db):
    try:
        return json.loads(db.meta_get(META) or "null") or {"unavailable": "no publish snapshot"}
    except (ValueError, TypeError):
        return {"unavailable": "invalid publish snapshot"}


def summary_lines(values):
    if not values or "unavailable" in values:
        return ["Non-Amazon price freshness: unavailable (no publish snapshot)"]
    lines = ["Non-Amazon price freshness (published offers; " + values["at"] + "):",
             "  Before: " + values.get("before_scope", "previous publish"),
             "  phase / store: <12h / 12-24h / 24-48h / 48-96h / >96h / unknown; API path / source only"]
    for phase in ("before", "after"):
        for store, row in values[phase].items():
            lines.append(f"  {phase} / {store}: " + " / ".join(str(row[k]) for k in (*BUCKETS, "unknown"))
                         + f"; {row['api_path']} / {row['source_only']}")
    return lines


def _post_allowed(source, url):
    from .source_policy import host_of
    from .parse.schema import store_from_url
    host = host_of(url)
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    # Source-owned posts only, never a product URL, tracker hop or credentials.
    return bool(source.allowed_html
                and parts.scheme in {"http", "https"} and not parts.username and not parts.password
                and host and host in {host_of(source.site), host_of(source.url)}
                and not store_from_url(url) and not util.official_feed_only(url))


class _PostReader:
    def __init__(self, url, deals):
        self.url, self.deals = url, deals

    def fetch_posts(self, source, fetcher, db, cfg, now, deadline):
        from .fetch.rss import _permission_host
        override = _permission_host(source, cfg)
        response = fetcher.get(self.url, conditional=True, deadline=deadline,
                               robots_override_host=override, follow_redirects=False)
        if response.status == 304:
            return []  # unchanged post: nothing ended, nothing re-evidenced
        if response.blocked_by_robots:
            raise RuntimeError("blocked_by_robots")
        if response.status != 200 or not response.body:
            raise RuntimeError(f"http_{response.status}")
        html = response.body.decode("utf-8", errors="replace")
        # Only the article, where present, so navigation/related-post prices do not pollute a check.
        article = re.search(r"<article\b[^>]*>(.*?)</article\s*>", html, re.I | re.S)
        body = article[1] if article else html
        title = re.search(r"<h1\b[^>]*>(.*?)</h1\s*>|<title\b[^>]*>(.*?)</title\s*>", body, re.I | re.S)
        title = util.html_to_text(next(s for s in title.groups() if s is not None)) if title else ""
        text = util.html_to_text(body)[:20000]
        # A standalone status at the start of a single-product article is scoped
        # to that offer. Arbitrary body/sidebar wording and roundup titles are not.
        if len(db.deals_for_post(source.name, self.url)) == 1:
            for line in text.splitlines()[:3]:
                if re.fullmatch(r"(?:(?:this )?(?:deal|offer|item) (?:is )?)?"
                                r"(?:expired|sold out|no longer available|price went up|deal is dead)[.!]?",
                                line.strip(), re.I):
                    title = line.strip() + " " + title
                    break
        return [RawPost(source.name, self.url, self.url, title, text, now, now,
                        price_recheck=True)]


def source_rechecks(db, cfg, sources, posts, fetcher, now, run_key, deadline=None):
    """Return rereads to the existing capped parser; no separate LLM allowance."""
    from .fetch.base import run_source
    from .fetch.rss import RssAdapter
    from .errors import DeadlineExceeded
    from .parse.schema import store_from_url
    from .source_policy import host_of
    counts = dict.fromkeys(("attempted", "posts", "errors"), 0)
    if not cfg.non_amazon_freshness_enabled or not cfg.non_amazon_source_rechecks_per_run:
        return [], counts
    receipt_key = "source_recheck_budget"
    receipt = json.loads(db.meta_get(receipt_key) or "{}")
    used = receipt.get("attempted", 0) if receipt.get("run") == run_key else 0
    sources = {s.name: s for s in sources if s.enabled}
    available = {(p.source_name, p.url): p for p in posts if not p.via_email}
    fetched_feeds, rereads = set(), []
    seen_urls = {tuple(p) for p in receipt.get("posts", [])} if receipt.get("run") == run_key else set()
    due = sorted((d for d in db.all_deals(status="alive") if not is_amazon_deal(d)
                  and age_h(d, now) > cfg.non_amazon_source_recheck_h),
                 key=lambda d: (-age_h(d, now), d["dedupe_key"]))
    for deal in due:
        try:
            fetcher._check_deadline(deadline)
        except DeadlineExceeded:
            break
        tried = util.parse_iso(deal.get("source_recheck_tried_at") or "")
        if deal.get("source_recheck_run") == run_key or (tried is not None and
                now - tried < timedelta(hours=cfg.non_amazon_source_recheck_h)):
            continue
        if used + counts["attempted"] >= cfg.non_amazon_source_rechecks_per_run:
            break
        for sighting in reversed(deal.get("sightings") or []):
            name, url = sighting.get("source_name"), sighting.get("source_url")
            source = sources.get(name)
            if source is None or db.is_tripped(name, now) or not url:
                continue
            html_ok = (_post_allowed(source, url)
                       and host_of(url) != host_of(deal.get("canonical_url") or ""))
            if not html_ok and (source.method != "rss" or store_from_url(source.url)):
                continue
            pair = (name, url)
            if pair in seen_urls:
                break
            seen_urls.add(pair)
            deal.update(source_recheck_tried_at=util.iso(now), source_recheck_run=run_key)
            db.update_deal(deal["dedupe_key"], payload=deal)
            counts["attempted"] += 1
            db.meta_set(receipt_key, json.dumps({"run": run_key, "attempted": used + counts["attempted"],
                                                "posts": sorted(seen_urls)}))
            if html_ok:
                reader = _PostReader(url, [deal])
                found, ok, error = run_source(source, reader, fetcher, db, cfg, now, deadline)
                counts["errors"] += not ok
                if error == "deadline":
                    return rereads, dict(counts, posts=len(rereads))
            else:
                # RSS-only includes Slickdeals even if allowed_html is accidentally enabled.
                if pair not in available and name not in fetched_feeds:
                    fetched_feeds.add(name)
                    reader = RssAdapter()
                    reader.follow_redirects = False
                    feed, ok, error = run_source(replace(source, allowed_html=False), reader,
                                             fetcher, db, cfg, now, deadline)
                    available.update({(p.source_name, p.url): p for p in feed})
                    counts["errors"] += not ok
                    if error == "deadline":
                        return rereads, dict(counts, posts=len(rereads))
                found = [replace(available[pair], published_at=now, price_recheck=True)] if pair in available else []
            rereads.extend(found)
            break
    counts["posts"] = len(rereads)
    return rereads, counts
