"""Page discovery and IndexNow receipts. A receipt never promises indexing.

The publish manifest stays beside the site, binds final files to a run, and
is usable only after that run's successful deploy. SQLite holds the queue
and the exact, durable batch claim; the workflows save the response too.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..http_client import PoliteFetcher
from ..persistence import checkpoint_state, delivery_run_key
from ..util import iso, now_utc, parse_iso

log = logging.getLogger(__name__)
INDEXNOW_FILE = "indexnow_urls.json"
MANIFEST_FILE = "indexnow_publish.json"
INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"
KEY_RE = re.compile(r"[A-Za-z0-9-]{8,128}")
METRICS = ("pending", "received", "key_pending", "failed", "oldest_pending_hours")
_DEAL_NOINDEX_HASH = hashlib.sha256(b"deal_pages_indexable:false").hexdigest()


def due_list_path(site_dir: str | Path) -> Path:
    return Path(site_dir).resolve().parent / INDEXNOW_FILE


def manifest_path(site_dir: str | Path) -> Path:
    return Path(site_dir).resolve().parent / MANIFEST_FILE


def enabled(cfg) -> bool:
    return bool(cfg.custom_domain and KEY_RE.fullmatch(cfg.seo_indexnow_key or ""))


def _page_path(out: Path, url: str, host: str) -> Path:
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.netloc != host or parts.query or parts.fragment
            or "\\" in parts.path or any(p in (".", "..") for p in parts.path.split("/"))):
        raise ValueError("invalid page URL")
    relative = parts.path.lstrip("/")
    return out / (relative + "index.html" if parts.path.endswith("/") else relative)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_hash(path: Path) -> str | None:
    return _hash(path.read_bytes()) if path.is_file() else None


def _ready(row: dict, cfg, now: datetime) -> bool:
    if row["pending_since"] is None:
        return False
    retry = parse_iso(row["retry_at"] or "")
    received = parse_iso(row["received_at"] or "")
    return ((retry is None or now >= retry) and
            (received is None or now - received >= timedelta(hours=cfg.seo_indexnow_min_hours)))


def _one_time_deal(cfg, url: str) -> bool:
    return not cfg.seo_deal_pages_indexable and urlsplit(url).path.startswith("/deal/")


def indexnow_due(db, cfg, now: datetime, pages: dict[str, str], *,
                 site_dir: str | Path | None = None, excluded: set[str] | None = None) -> list[str]:
    """Discover versions without acknowledging them. Keep failed work pending
    through content churn; noindex/removed formerly public pages are changes too."""
    identity = _hash(f"{cfg.custom_domain}|{cfg.seo_indexnow_key}".encode())
    previous_identity = db.meta_get("indexnow_identity")
    known_urls = set(db.indexnow_pages())
    if previous_identity is None:
        try:
            legacy = json.loads(db.meta_get("indexnow_sent") or "{}")
        except ValueError:
            legacy = {}
        if isinstance(legacy, dict):
            known_urls.update(legacy)
    if previous_identity != identity:
        # Legacy indexnow_sent records discovery, not receipt. Key rotation
        # likewise cannot inherit the previous key's validation or receipts.
        with db.conn:
            db.conn.execute("DELETE FROM indexnow_pages")
            db.conn.execute("DELETE FROM meta WHERE k='indexnow_key_check'")
        db.meta_set("indexnow_identity", identity)
    if db.meta_get("indexnow_since") is None:
        db.meta_set("indexnow_since", iso(now))
    excluded = excluded or set()
    versions = {url: _hash(content.encode("utf-8")) for url, content in pages.items() if url not in excluded}
    out = Path(site_dir) if site_dir is not None else None
    for url in known_urls:
        if url in versions or url in excluded:
            continue
        try:
            # A changed domain does not turn the old host's URLs into work.
            path = _page_path(out or Path("."), url, cfg.custom_domain)
        except ValueError:
            continue
        path = path if out is not None else None
        versions[url] = _hash(path.read_bytes()) if path is not None and path.is_file() else "removed"
    # Off: a deal's noindex transition is one logical version, irrespective
    # of later price/title/related-offer churn or removal. Final file hashes
    # in the deployment manifest still bind the receipt to deployed bytes.
    previous = db.indexnow_pages()
    one_time = {}
    for url in list(versions):
        if _one_time_deal(cfg, url):
            version = versions.pop(url)
            received = previous.get(url, {}).get("received_hash")
            one_time[url] = (received if received in (_DEAL_NOINDEX_HASH, "removed") else
                             "removed" if version == "removed" else _DEAL_NOINDEX_HASH)
    db.indexnow_discover(versions, now, cfg.seo_indexnow_resubmit_days)
    if one_time:
        db.indexnow_discover(one_time, now, 0)
    return [url for url, row in sorted(db.indexnow_pages().items()) if url not in excluded and _ready(row, cfg, now)]


def seal_publish(db, cfg, now: datetime, out: Path, run_key: str | None = None, *,
                 excluded: set[str] | None = None) -> None:
    """Called after the last page writer, so partial/failed publishes have no
    usable manifest. Hash final bytes separately from the stable content hashes."""
    if not enabled(cfg):
        return
    rows = {url: row for url, row in db.indexnow_pages().items() if url not in (excluded or set())
            and not (_one_time_deal(cfg, url) and row["pending_since"] is None)}
    manifest = {"run_key": delivery_run_key(now, run_key), "host": cfg.custom_domain,
                "identity": db.meta_get("indexnow_identity"),
                "versions": {url: row["content_hash"] for url, row in rows.items()},
                "files": {url: _file_hash(_page_path(out, url, cfg.custom_domain)) for url in rows},
                "key_hash": _file_hash(out / f"{cfg.seo_indexnow_key}.txt")}
    manifest_path(out).write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")


def _deployed_versions(cfg, run_key: str) -> dict[str, str] | None:
    try:
        manifest = json.loads(manifest_path(cfg.site_dir).read_text(encoding="utf-8"))
        identity = _hash(f"{cfg.custom_domain}|{cfg.seo_indexnow_key}".encode())
        if (manifest["run_key"] != run_key or manifest["host"] != cfg.custom_domain
                or manifest["identity"] != identity
                or not isinstance(manifest["versions"], dict)
                or set(manifest["versions"]) != set(manifest["files"])):
            return None
        out = Path(cfg.site_dir)
        key_file = out / f"{cfg.seo_indexnow_key}.txt"
        if (key_file.read_text(encoding="utf-8").strip() != cfg.seo_indexnow_key
                or _file_hash(key_file) != manifest["key_hash"]):
            return None
        for url, version in manifest["versions"].items():
            if not isinstance(version, str) or not (version == "removed" or re.fullmatch(r"[0-9a-f]{64}", version)):
                return None
            actual = _file_hash(_page_path(out, url, cfg.custom_domain))
            if actual != manifest["files"][url] or (actual is None) != (version == "removed"):
                return None
        return manifest["versions"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _retry_minutes(cfg, attempts: int, now: datetime, retry_after: str = "") -> float:
    delay = cfg.seo_indexnow_retry_initial_minutes * 2 ** min(max(0, attempts), 32)
    try:
        seconds = float(retry_after)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(retry_after) - now).total_seconds()
        except (ValueError, TypeError, OverflowError):
            seconds = 0
    return min(cfg.seo_indexnow_retry_max_minutes, max(delay, seconds / 60))


def _endpoint_retry(db) -> dict:
    try:
        value = json.loads(db.meta_get("indexnow_retry:" + INDEXNOW_ENDPOINT) or "{}")
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def _check_key(db, cfg, now: datetime, client: Any) -> None:
    try:
        previous = json.loads(db.meta_get("indexnow_key_check") or "{}")
    except ValueError:
        previous = {}
    previous = previous if isinstance(previous, dict) else {}
    next_check = parse_iso(previous.get("retry_at", ""))
    if next_check and now < next_check:
        return
    # The key is publicly hosted, but neither its path nor response body needs
    # to appear in logs. This GET honors robots, host pacing and bot walls.
    muted = [logging.getLogger(n) for n in ("httpx", "httpcore", "dealradar.http_client")]
    levels = [logger.level for logger in muted]
    fetcher = PoliteFetcher(db, replace(cfg, max_retries=0,
                                     min_host_interval_s=max(1.0, cfg.min_host_interval_s)),
                            http_client=client)
    published = False
    try:
        for logger in muted:
            logger.setLevel(logging.CRITICAL)
        result = fetcher.get(f"https://{cfg.custom_domain}/{cfg.seo_indexnow_key}.txt", conditional=False)
        published = (result.status == 200 and result.body is not None
                     and result.body.decode("utf-8").strip() == cfg.seo_indexnow_key
                     and result.final_url == result.url)
    except Exception:
        pass  # an unavailable key never turns a 202 into a 200
    finally:
        fetcher.close()
        for logger, level in zip(muted, levels):
            logger.setLevel(level)
    attempts = 0 if published else int(previous.get("attempts", 0)) + 1
    delay = (max(cfg.seo_indexnow_min_hours * 60, cfg.seo_indexnow_retry_initial_minutes)
             if published else _retry_minutes(cfg, attempts - 1, now))
    db.meta_set("indexnow_key_check", json.dumps({"checked_at": iso(now), "published": published,
                "attempts": attempts, "retry_at": iso(now + timedelta(minutes=delay))}))
    log.info("indexnow: key file %s; engine key validation remains pending",
             "published" if published else "unavailable")


def submit_indexnow(db, cfg, http_client: Any = None, *, deployed: bool = False,
                    run_key: str | None = None, now: datetime | None = None) -> int:
    """At most one POST per run/endpoint, with no inline POST retries. Only
    200/202 receipt acknowledges the exact deployed versions in that batch."""
    now = now or now_utc()
    run_key = run_key or os.environ.get("DEALRADAR_RUN_KEY")
    if not (enabled(cfg) and deployed and run_key):
        log.info("indexnow: no successful deployment supplied")
        return 0
    previous = db.indexnow_batch(INDEXNOW_ENDPOINT, run_key)
    if previous:
        log.info("indexnow: this run's batch already attempted")
        return 0 if previous["status"] == "received" else 1
    deployed_versions = _deployed_versions(cfg, run_key)
    if deployed_versions is None:
        log.warning("indexnow: missing or mismatched publish manifest; no POST")
        return 1
    endpoint_retry = _endpoint_retry(db)
    retry_at = parse_iso(endpoint_retry.get("retry_at") or "")
    if retry_at and now < retry_at:
        log.info("indexnow: endpoint retry is not due")
        return 0
    rows = db.indexnow_pages()
    versions = {url: row["content_hash"] for url, row in sorted(
        rows.items(), key=lambda item: (item[1]["pending_since"] or "", item[0]))
        if deployed_versions.get(url) == row["content_hash"] and _ready(row, cfg, now)}
    limit = max(1, min(10000, cfg.seo_indexnow_batch_size))
    versions = dict(list(versions.items())[:limit])
    needs_key_check = any(row["key_pending"] for row in rows.values())
    if not versions and not needs_key_check:
        log.info("indexnow: nothing due for receipt")
        return 0
    client = http_client or httpx.Client(timeout=20)
    try:
        if needs_key_check:
            _check_key(db, cfg, now, client)
        if not versions:
            return 0
        attempts = int(endpoint_retry.get("attempts", 0))
        retries = {url: iso(now + timedelta(minutes=_retry_minutes(
            cfg, max(attempts, rows[url]["attempts"]), now)))
                   for url in versions}
        if not db.indexnow_claim(INDEXNOW_ENDPOINT, run_key, versions, now, retries):
            return 0
        # New arrivals cannot bypass an endpoint's throttle or a lost response.
        db.meta_set("indexnow_retry:" + INDEXNOW_ENDPOINT, json.dumps({
            "attempts": attempts + 1, "retry_at": max(retries.values())}))
        try:
            checkpoint_state(db)
        except Exception as exc:
            db.indexnow_finish(INDEXNOW_ENDPOINT, run_key, now, None)
            log.warning("indexnow: state checkpoint failed (%s); no POST", type(exc).__name__)
            return 1
        body = {"host": cfg.custom_domain, "key": cfg.seo_indexnow_key,
                "keyLocation": f"https://{cfg.custom_domain}/{cfg.seo_indexnow_key}.txt",
                "urlList": list(versions)}
        try:
            # Official submission API, not a crawl. The durable claim bounds
            # the host to one request/run; redirects and inline retries are off.
            response = client.post(INDEXNOW_ENDPOINT, content=json.dumps(body).encode("utf-8"),
                                   headers={"Content-Type": "application/json; charset=utf-8",
                                            "User-Agent": cfg.resolved_user_agent()},
                                   follow_redirects=False)
        except httpx.HTTPError as exc:
            db.indexnow_finish(INDEXNOW_ENDPOINT, run_key, now, None)
            log.warning("indexnow: %d URL(s) failed (%s)", len(versions), type(exc).__name__)
            return 1
        retries = {url: iso(now + timedelta(minutes=_retry_minutes(
            cfg, max(attempts, rows[url]["attempts"]), now,
            response.headers.get("Retry-After", "")))) for url in versions}
        db.indexnow_finish(INDEXNOW_ENDPOINT, run_key, now, response.status_code, retries)
        accepted = response.status_code in (200, 202)
        db.meta_set("indexnow_retry:" + INDEXNOW_ENDPOINT, json.dumps({
            "attempts": 0 if accepted else attempts + 1,
            "retry_at": (iso(now + timedelta(seconds=max(1.0, cfg.min_host_interval_s)))
                         if accepted else max(retries.values()))}))
        log.info("indexnow: %d URL(s), HTTP %d (%s)", len(versions), response.status_code,
                 "receipt" if response.status_code in (200, 202) else "failed")
        if response.status_code == 202:
            _check_key(db, cfg, now, client)
        return 0 if response.status_code in (200, 202) else 1
    finally:
        if http_client is None:
            client.close()


def metrics(db, cfg, now: datetime) -> dict[str, Any]:
    """Current URL-version counts: key_pending is a subset of received;
    failed is a subset of pending, including work waiting for its retry."""
    reason = "IndexNow disabled" if not enabled(cfg) else "receipt history unavailable"
    unavailable = {**dict.fromkeys(METRICS), "unavailable": reason}
    try:
        if not enabled(cfg) or db.meta_get("indexnow_since") is None:
            return unavailable
        rows = [r for url, r in db.indexnow_pages().items() if urlsplit(url).netloc == cfg.custom_domain]
    except sqlite3.OperationalError:
        return unavailable  # old read-only snapshots are never upgraded here
    pending = [r for r in rows if r["pending_since"] is not None]
    received = [r for r in rows if r["pending_since"] is None and r["received_hash"] == r["content_hash"]]
    ages = [(now - parse_iso(r["pending_since"])).total_seconds() / 3600 for r in pending]
    return {"pending": len(pending), "received": len(received),
            "key_pending": sum(r["key_pending"] for r in received),
            "failed": sum(r["failed"] for r in pending),
            "oldest_pending_hours": round(max([0.0, *ages]), 2)}


def summary_lines(values: dict[str, Any]) -> list[str]:
    rows = ["IndexNow receipts (current URL versions; receipt does not promise indexing):"]
    for name in METRICS:
        value = values.get(name)
        rows.append(f"indexnow.{name}: {value if value is not None else 'unavailable'}")
    if values.get("unavailable"):
        rows.append(values["unavailable"])
    rows.append("key_pending is included in received; failed is included in pending.")
    return rows
