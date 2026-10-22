"""Facebook photos and Instagram media, with a draft-first rollout.

Credentials stay in memory. Successful posts and drafts are committed locally
before the next candidate; the hourly workflow persists the encrypted state.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx

from ..savings import display_pct
from ..persistence import delivery_run_key, prepare_delivery
from .. import util
from ..config import get_secret
from .social import _candidates, _word_trim, _without_prices, social_title

log = logging.getLogger(__name__)
STATUSES = {"FINISHED", "IN_PROGRESS", "ERROR", "EXPIRED", "PUBLISHED"}
# Graph error codes that are about the app, the account or throttling, never
# the image: rate limits (4, 9, 17, 32, 613, 80000-80099), permissions (10,
# 200-299), temporary blocks (368).
_NOT_THE_IMAGE = {4, 9, 10, 17, 32, 368, 613}


class _APIError(Exception):
    """Retain only numeric status/code, never a body, request or credentials."""

    def __init__(self, status: int | None = None, code: int | None = None,
                 *, rejected: bool = False):
        self.status = status
        self.code = code
        self.rejected = rejected
        super().__init__(f"HTTP {status}, code {code}")

    @property
    def disconnected(self) -> bool:
        return self.status == 401 or self.code == 190


class _API:
    def __init__(self, cfg, *, transport=None):
        self.base = cfg.meta["graph_base"].rstrip("/")
        if not re.fullmatch(r"https://graph\.facebook\.com/v\d+\.\d+", self.base):
            raise ValueError("invalid Graph base")
        self.interval = max(1.0, cfg.min_host_interval_s)
        self.last_request = None
        self.client = httpx.Client(
            transport=transport, timeout=cfg.request_timeout_s,
            headers={"User-Agent": cfg.resolved_user_agent()},
        )

    def request(self, method: str, path: str, token: str, **kwargs) -> dict:
        if self.last_request is not None:
            time.sleep(max(0.0, self.interval - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        try:
            response = self.client.request(method, self.base + path,
                                           headers={"Authorization": f"Bearer {token}"}, **kwargs)
        except httpx.HTTPError:
            raise _APIError() from None
        try:
            data = response.json()
        except ValueError:
            raise _APIError(response.status_code) from None
        if not isinstance(data, dict):
            raise _APIError(response.status_code)
        if not response.is_success or "error" in data:
            error = data.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            raise _APIError(response.status_code, code if type(code) is int else None)
        return data


def _meta(db, key: str) -> dict:
    value = json.loads(db.meta_get(key) or "{}")
    if not isinstance(value, dict):
        raise ValueError("invalid social state")
    for network in ("facebook", "instagram"):
        value.setdefault(network, {})
        if not isinstance(value[network], dict):
            raise ValueError("invalid network state")
    return value


def caption(deal: dict, path: str, cfg, network: str) -> str:
    from ..amazon_reference import project
    from ..util import now_utc
    from .traffic import tagged_url

    deal = project(deal, cfg, now_utc())
    item = social_title(str(deal.get("item") or ""), str(deal.get("brand") or ""))
    store = _word_trim(_without_prices(str(deal.get("store") or "")), 100)
    pct = deal.get("pct_at_checkout")
    lead = f"{display_pct(pct)}% OFF" if pct is not None else "Price drop"
    disclosure = _word_trim(_without_prices(str(cfg.meta.get("disclosure") or "#ad")), 100) or "#ad"
    url = tagged_url("https://wowpriced.com", path, network, "social")
    facts = f"🔥 {lead}: {item}\nAt {store}, verified by wowpriced: {url}"
    if network == "facebook":
        return f"{facts}\n{disclosure}"
    facts += "\nTap the link in our bio for today's deals."
    hashtags = _without_prices(str(cfg.meta["instagram"].get("hashtags") or ""))
    hashtags = _word_trim(hashtags, 2200 - len(facts) - len(disclosure) - 2)
    return f"{facts}\n{hashtags}\n{disclosure}"


def _image_request(api, method, path, token, **kwargs):
    try:
        return api.request(method, path, token, **kwargs)
    except _APIError as error:
        # Authentication, permission, throttling and server failures are not bad images.
        code = error.code
        error.rejected = (error.status is not None and 400 <= error.status < 500
                          and error.status not in (401, 403, 429)
                          and not (code in _NOT_THE_IMAGE or (code is not None and (
                              200 <= code < 300 or 80000 <= code < 80100))))
        raise


def _instagram(api, cfg, token, deal, text, mode) -> tuple[str, str]:
    container = _image_request(api, "POST", f"/{cfg.meta['ig_user_id']}/media", token,
                               data={"image_url": deal["image_url"], "caption": text})
    container_id = str(container["id"])
    status = container.get("status_code")
    for attempt in range(5):
        if status in ("FINISHED", "ERROR", "EXPIRED"):
            break
        if attempt:
            time.sleep(2)
        result = api.request("GET", f"/{container_id}", token, params={"fields": "status_code"})
        status = result.get("status_code")
    if status not in STATUSES:
        raise _APIError()
    if status in ("ERROR", "EXPIRED"):
        raise _APIError(rejected=True)
    if mode == "draft":
        return container_id, status
    if status != "FINISHED":
        raise _APIError()
    media = _image_request(api, "POST", f"/{cfg.meta['ig_user_id']}/media_publish", token,
                           data={"creation_id": container_id})
    return str(media["id"]), "PUBLISHED"


def _slot_open(now: datetime, settings: dict, attempts: dict) -> bool:
    """Live posts go out in an hours_utc slot, or up to slot_grace_hours after a
    slot that has no post yet: a queued hourly run can reach this step after
    its slot hour ended (hourly #323 posted at 14:02Z and missed 13Z)."""
    hours = settings.get("hours_utc", [])
    grace = max(0, int(settings.get("slot_grace_hours", 0)))
    for back in range(grace + 1):
        start = (now - timedelta(hours=back)).replace(minute=0, second=0, microsecond=0)
        if start.hour in hours:
            return back == 0 or not any(
                (at := util.parse_iso(entry.get("at"))) and at >= start
                for entry in attempts.values())
    return False


def post(db, cfg, *, transport=None, now: datetime | None = None,
         run_key: str | None = None) -> int:
    if not cfg.meta.get("enabled", False):
        log.info("meta: disabled")
        return 0
    token = get_secret("META_SYSTEM_TOKEN")
    if not token:
        log.info("meta: no secrets")
        return 0
    api = None
    try:
        now = (now or util.now_utc()).astimezone(timezone.utc)
        posted = _meta(db, "meta_posted")
        drafts = _meta(db, "meta_drafts")
        failed = _meta(db, "meta_failed")
        summaries = []
        for network in ("facebook", "instagram"):
            settings = cfg.meta[network]
            mode = settings.get("mode", "draft")
            if mode not in ("draft", "live"):
                raise ValueError("invalid posting mode")
            history = drafts if mode == "draft" else posted
            channel = f"meta:{network}:{mode}"
            outgoing = db.outgoing_for(channel)
            reserved = {entry["dedup_token"]: {"at": entry["started_at"]} for entry in outgoing}
            attempts = {**reserved, **history[network]}
            daily_max = max(0, int(settings["daily_max"]))
            today = sum(1 for entry in attempts.values()
                        if (at := util.parse_iso(entry.get("at"))) and at.date() == now.date())
            limit = min(max(0, int(settings["posts_per_run"])), max(0, daily_max - today))
            if mode == "live" and not _slot_open(now, settings, attempts):
                limit = 0
            count = 0
            excluded = {**posted[network], **failed[network], **reserved}
            if mode == "draft":
                excluded.update(drafts[network])
            candidates = _candidates(db, cfg, excluded) if limit else []
            selected = db.delivery_batch(channel, delivery_run_key(now, run_key),
                                         [d["dedupe_key"] for d, _ in candidates[:3]], quota=limit)
            candidates = [(d, path) for d, path in candidates if d["dedupe_key"] in selected]
            limit = min(limit, db.delivery_budget(channel, delivery_run_key(now, run_key)))
            if not limit:
                candidates = []
            if candidates:
                if api is None:
                    api = _API(cfg, transport=transport)
                page_token = None
                if network == "facebook":
                    page = api.request("GET", f"/{cfg.meta['page_id']}", token,
                                       params={"fields": "access_token"})
                    page_token = page["access_token"]
                    if not isinstance(page_token, str) or not page_token:
                        raise _APIError()
                for deal, path in candidates:
                    if count >= limit or not db.delivery_budget(channel, delivery_run_key(now, run_key)):
                        break
                    text = caption(deal, path, cfg, network)
                    try:
                        if not prepare_delivery(db, channel, deal["dedupe_key"], now, run_key):
                            continue
                        status = None
                        if network == "instagram":
                            post_id, status = _instagram(api, cfg, token, deal, text, mode)
                        elif mode == "live":
                            photo = _image_request(api, "POST", f"/{cfg.meta['page_id']}/photos",
                                                   page_token, data={"url": deal["image_url"],
                                                                     "caption": text, "published": "true"})
                            post_id = str(photo["id"])
                        else:
                            post_id = ""
                        entry = {"id": post_id, "at": util.iso(now)}
                        if mode == "draft":
                            entry.update({"caption": text, "image_host": urlsplit(deal["image_url"]).hostname,
                                          "path": path, "pick": deal["pick"]})
                            if status:
                                entry["status"] = status
                        history[network][deal["dedupe_key"]] = entry
                        db.meta_set("meta_drafts" if mode == "draft" else "meta_posted", json.dumps(history))
                        db.finish_outgoing(channel, deal["dedupe_key"], now)
                        count += 1
                        if mode == "draft":
                            log.info("meta: %s draft pick %s: %s; image host %s; deal path %s%s",
                                     network, deal["pick"], text.replace("\n", " "), entry["image_host"], path,
                                     f"; container status {status}" if status else "")
                    except _APIError as error:
                        if error.rejected or (error.status is not None and 400 <= error.status < 500):
                            db.finish_outgoing(channel, deal["dedupe_key"], now, rejected=True)
                        if error.disconnected:
                            raise
                        log.warning("meta: %s HTTP %s code %s", network, error.status, error.code)
                        if error.rejected and (network == "instagram" or error.status == 400):
                            failed[network][deal["dedupe_key"]] = {
                                "at": util.iso(now), "status": error.status, "code": error.code,
                            }
                            db.meta_set("meta_failed", json.dumps(failed))
            summaries.append(f"{network} posted {count} (today {today + count}/{daily_max}, {mode})")
        log.info("meta: %s", "; ".join(summaries))
    except _APIError as error:
        if error.disconnected:
            log.warning("meta: not connected")
        else:
            log.warning("meta: HTTP %s code %s", error.status, error.code)
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        log.warning("meta: invalid state or response")
    finally:
        if api:
            api.client.close()
    return 0
