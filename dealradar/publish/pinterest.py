"""Pinterest OAuth and hourly Pins. Tokens stay in encrypted database state.

Only the deployed public picks are eligible. Each successful Pin is committed
locally before the next request; the workflow persists that state to `data`.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from ..persistence import delivery_run_key, prepare_delivery
from .. import util
from ..config import get_secret, redact_secrets
from .social import _candidates, _word_trim, _without_prices, social_title

log = logging.getLogger(__name__)
API_BASES = {
    "sandbox": "https://api-sandbox.pinterest.com/v5",
    "production": "https://api.pinterest.com/v5",
}
SCOPES = "boards:read,boards:write,pins:read,pins:write,user_accounts:read"


class _APIError(Exception):
    """Never retain a response body, request, OAuth code or credentials in an error.

    Only the HTTP status, Pinterest's numeric error code and the endpoint
    (method and path, never query or body) are kept, so a failure can be
    diagnosed from the public log.
    """

    def __init__(self, status: int | None = None, code: int | None = None, where: str = "",
                 reason: str = ""):
        self.status = status
        self.code = code
        text = f"HTTP {status}" if status is not None else "request failed"
        if code is not None:
            text += f" code {code}"
        if where:
            text += f" ({where})"
        if reason:
            text += f": {reason}"
        super().__init__(text)


def _meta(db, key: str) -> dict:
    value = json.loads(db.meta_get(key) or "{}")
    if not isinstance(value, dict):
        raise _APIError()
    return value


def _environment(cfg) -> str:
    environment = cfg.pinterest.get("environment", "sandbox")
    if environment not in API_BASES:
        raise _APIError()
    return environment


def auth_url(cfg) -> str | None:
    app_id = get_secret("PINTEREST_APP_ID")
    if not app_id:
        log.info("pinterest: no secrets")
        return None
    return "https://www.pinterest.com/oauth/?" + urlencode({
        "client_id": app_id,
        "redirect_uri": cfg.pinterest.get("redirect_uri", "https://wowpriced.com/"),
        "response_type": "code", "scope": SCOPES,
        "state": secrets.token_urlsafe(32),
    })


class _API:
    def __init__(self, cfg, *, transport=None):
        self.base = API_BASES[_environment(cfg)]
        self.interval = max(1.0, cfg.min_host_interval_s)
        self.last_request = None
        self.stopped = False
        self.client = httpx.Client(
            transport=transport, timeout=cfg.request_timeout_s,
            headers={"User-Agent": cfg.resolved_user_agent()},
        )

    def request(self, method: str, path: str, **kwargs) -> dict:
        if self.last_request is not None:
            time.sleep(max(0.0, self.interval - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        try:
            response = self.client.request(method, self.base + path, **kwargs)
        except httpx.HTTPError:
            raise _APIError() from None
        if not response.is_success:
            if response.status_code in {401, 403, 429}:
                self.stopped = True
            try:
                body = response.json()
                code, reason = body.get("code"), body.get("message")
            except (ValueError, AttributeError):
                code, reason = None, None
            where = f"{method} {path}" if re.fullmatch(r"/[a-z_/]+", path) else method
            # Pinterest's own short explanation ("Board name already exists"),
            # never for OAuth calls. Any long unbroken run (a token, code or
            # id) and any live secret is masked, so only plain words remain.
            if path.startswith("/oauth") or not isinstance(reason, str):
                reason = ""
            reason = re.sub(r"pina_\S*|\S{16,}", "<masked>",
                            redact_secrets(" ".join(reason.split())))[:120]
            error = _APIError(response.status_code, code if type(code) is int else None, where, reason)
            # Analytics defers retries, including a server-requested wait that
            # extends past midnight. Never retain the response or its headers.
            from email.utils import parsedate_to_datetime
            retry = response.headers.get("Retry-After", "")
            try:
                error.retry_after = max(0, int(retry))
            except ValueError:
                try:
                    error.retry_after = max(0, (parsedate_to_datetime(retry) - util.now_utc()).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    error.retry_after = 0
            raise error
        try:
            data = response.json()
        except ValueError:
            raise _APIError(response.status_code) from None
        if not isinstance(data, dict):
            raise _APIError(response.status_code)
        return data

    def token(self, data: dict) -> dict:
        return self.request("POST", "/oauth/token", data=data,
                            auth=httpx.BasicAuth(get_secret("PINTEREST_APP_ID"),
                                                 get_secret("PINTEREST_APP_SECRET")))


def _save_auth(db, cfg, data: dict, now: datetime, previous: dict | None = None) -> dict:
    auth = dict(previous or {})
    auth["access_token"] = data["access_token"]
    auth["expires_at"] = util.iso(now + timedelta(seconds=int(data["expires_in"])))
    if data.get("refresh_token"):
        auth["refresh_token"] = data["refresh_token"]
    if data.get("refresh_token_expires_at") is not None:
        auth["refresh_expires_at"] = util.iso(datetime.fromtimestamp(
            int(data["refresh_token_expires_at"]), timezone.utc))
    elif data.get("refresh_token_expires_in") is not None:
        auth["refresh_expires_at"] = util.iso(now + timedelta(
            seconds=int(data["refresh_token_expires_in"])))
    if not auth.get("access_token") or not auth.get("refresh_token") or not auth.get("refresh_expires_at"):
        raise _APIError()
    auth["environment"] = _environment(cfg)
    db.meta_set("pinterest_auth", json.dumps(auth))
    return auth


def _has_secrets() -> bool:
    return bool(get_secret("PINTEREST_APP_ID") and get_secret("PINTEREST_APP_SECRET"))


def connect(db, cfg, code: str, *, transport=None, now: datetime | None = None) -> int:
    if not _has_secrets():
        log.info("pinterest: no secrets")
        return 0
    api = None
    try:
        api = _API(cfg, transport=transport)
        data = api.token({
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": cfg.pinterest.get("redirect_uri", "https://wowpriced.com/"),
            "continuous_refresh": "true",
        })
        now = now or util.now_utc()
        auth = _save_auth(db, cfg, data, now)
        days = max(0, int((util.parse_iso(auth["expires_at"]) - now).total_seconds() / 86400))
        print(f"connected ({auth['environment']}), access token valid {days} days")
        return 0
    except _APIError as error:
        log.warning("pinterest: %s", error)
        return 1
    except (ValueError, KeyError, TypeError, OverflowError):
        log.warning("pinterest: invalid response")
        return 1
    finally:
        if api:
            api.client.close()


def _access_token(db, cfg, api: _API, now: datetime) -> str | None:
    auth = _meta(db, "pinterest_auth")
    if not auth or auth.get("environment") != _environment(cfg):
        log.info("pinterest: not connected (environment mismatch)" if auth
                 else "pinterest: not connected")
        return None
    expires = util.parse_iso(auth.get("expires_at"))
    if not expires or expires <= now + timedelta(days=3):
        refresh_expires = util.parse_iso(auth.get("refresh_expires_at"))
        if not auth.get("refresh_token") or not refresh_expires or refresh_expires <= now:
            log.info("pinterest: not connected")
            return None
        auth = _save_auth(db, cfg, api.token({
            "grant_type": "refresh_token", "refresh_token": auth["refresh_token"],
        }), now, auth)
    return auth.get("access_token")


_BOARD_NAME_TAKEN = 58  # "You already have a board with this name!"


def _same_name(a: str, b: str) -> bool:
    return " ".join(str(a or "").split()).casefold() == " ".join(str(b or "").split()).casefold()


def _find_board(api: _API, headers: dict, name: str, privacy: str = "") -> dict | None:
    bookmark = None
    seen = set()
    while True:
        params = {"bookmark": bookmark} if bookmark else {}
        if privacy:
            params["privacy"] = privacy
        try:
            data = api.request("GET", "/boards", headers=headers, params=params)
        except _APIError as error:
            if privacy and error.status not in {401, 403, 429}:
                return None  # the extra privacy filter is a best-effort second look
            raise
        board = next((b for b in data.get("items", []) if _same_name(b.get("name"), name)
                      and b.get("privacy", "PUBLIC") == "PUBLIC"), None)
        if board:
            return board
        bookmark = data.get("bookmark")
        if not bookmark:
            return None
        if bookmark in seen:
            raise _APIError()
        seen.add(bookmark)


def board_name(cfg, category: str = "other") -> str:
    return (cfg.pinterest.get("category_boards", {}).get(category)
            or cfg.pinterest.get("board_name", "Deals up to 90% off"))


def _board(db, cfg, api: _API, headers: dict, name: str | None = None) -> str:
    name = name or board_name(cfg)
    environment = _environment(cfg)
    cache = _meta(db, "pinterest_boards")
    boards = cache.setdefault(environment, {})
    legacy = _meta(db, "pinterest_board")
    if legacy.get("environment") == environment and legacy.get("id"):
        boards.setdefault(legacy["name"], legacy["id"])
    if boards.get(name):
        db.meta_set("pinterest_boards", json.dumps(cache))
        return boards[name]
    board = _find_board(api, headers, name)
    if not board:
        try:
            board = api.request("POST", "/boards", headers=headers,
                                json={"name": name, "privacy": "PUBLIC"})
        except _APIError as error:
            if error.code != _BOARD_NAME_TAKEN:
                raise
            # Pinterest says the name is taken but the default listing does not
            # show it (a secret board, or one made outside this environment):
            # look through every board, else use a clearly related name.
            board = _find_board(api, headers, name, privacy="ALL")
            if not board:
                alt = f"{name} (wowpriced)"
                board = _find_board(api, headers, alt) or api.request(
                    "POST", "/boards", headers=headers, json={"name": alt, "privacy": "PUBLIC"})
                log.info("pinterest: board name taken but not listed; using %r", alt)
    board_id = str(board["id"])
    boards[name] = board_id
    db.meta_set("pinterest_boards", json.dumps(cache))
    if name == board_name(cfg):
        db.meta_set("pinterest_board", json.dumps({
            "id": board_id, "name": name, "environment": environment,
        }))
    return board_id


def pin_payload(deal: dict, path: str, board_id: str, disclosure: str) -> dict:
    brand = _without_prices(str(deal.get("brand") or ""))
    original = _without_prices(str(deal.get("item") or ""))
    if brand and not re.search(r"\b" + re.escape(brand) + r"\b", original, re.I):
        original = f"{brand} {original}"
    item = social_title(original, brand)
    category = str(deal.get("category") or "other")
    from ..parse.schema import CATEGORIES
    category = category.replace("_", " & ") if category in CATEGORIES and category != "other" else ""
    # No discount/verification claims. Reserve the category and final #ad.
    suffix = f" | {category.title()} deal" if category else " | Deal"
    disclosure = _word_trim(re.sub(r"(?i)#ad\b", "", _without_prices(disclosure)), 96)
    description = f"{item}. {category.capitalize() + ' deal' if category else 'Deal'} on wowpriced. "
    description += (disclosure + " " if disclosure else "") + "#ad"
    return {
        "board_id": board_id,
        "title": _word_trim(item, 100 - len(suffix)) + suffix,
        "description": description,
        "link": "https://wowpriced.com" + path,
        "alt_text": _word_trim(_without_prices(str(deal.get("item") or "")), 500) or "Product photo",
        "media_source": {"source_type": "image_url", "url": deal["image_url"]},
    }


def _pin_candidates(db, cfg, posted):
    candidates = []
    for deal, path in _candidates(db, cfg, posted):
        row = db.get_deal(deal["dedupe_key"]) or {}
        if any(d.get("price_unconfirmed") or d.get("gone_suspect_at")
               or d.get("status", "alive") != "alive"
               or "suspect_msrp" in (d.get("flags") or []) for d in (deal, row)):
            continue
        candidates.append((dict(deal, brand=deal.get("brand") or row.get("brand") or ""), path))
    return candidates


def _spread(candidates, cfg, state, limit):
    """Preserve pick rank except when the board streak requires another board."""
    pending, chosen = list(candidates), []
    last, count = state.get("board"), state.get("count", 0)
    maximum = max(1, int(cfg.pinterest.get("max_consecutive_board", 2)))
    while pending and len(chosen) < limit:
        index = next((i for i, (deal, _) in enumerate(pending)
                      if count < maximum or board_name(cfg, deal.get("category")) != last), None)
        if index is None:
            break
        row = pending.pop(index)
        board = board_name(cfg, row[0].get("category"))
        count = count + 1 if board == last else 1
        last = board
        chosen.append(row)
    return chosen


def _pace(cfg, now):
    daily = max(0, int(cfg.pinterest.get("daily_max", 20)))
    per_run = max(0, int(cfg.pinterest.get("pins_per_run", 2)))
    ramp = cfg.pinterest.get("ramp") or {}
    if ramp.get("enabled", False):
        for step in sorted(ramp.get("schedule") or [], key=lambda s: str(s["from"])):
            if datetime.fromisoformat(str(step["from"])).date() <= now.date():
                daily = max(0, int(step.get("daily_max", daily)))
                per_run = max(0, int(step.get("pins_per_run", per_run)))
    return daily, per_run


def post(db, cfg, *, transport=None, now: datetime | None = None,
         run_key: str | None = None) -> int:
    if not cfg.pinterest.get("enabled", False):
        log.info("pinterest: disabled")
        return 0
    if not _has_secrets():
        log.info("pinterest: no secrets")
        return 0
    api = None
    headers = None
    try:
        api = _API(cfg, transport=transport)
        now = (now or util.now_utc()).astimezone(timezone.utc)
        token = _access_token(db, cfg, api, now)
        if not token:
            return 0
        headers = {"Authorization": f"Bearer {token}"}
        posted = _meta(db, "pinterest_posted")
        outgoing = db.outgoing_for("pinterest")
        for entry in outgoing:
            posted.setdefault(entry["dedup_token"], {"at": entry["started_at"]})
        today = sum(1 for entry in posted.values()
                    if (at := util.parse_iso(entry.get("at"))) and at.date() == now.date())
        daily_max, per_run = _pace(cfg, now)
        limit = min(per_run, max(0, daily_max - today))
        spread = _meta(db, "pinterest_spread")
        environment = _environment(cfg)
        streak = spread.get(environment, {})
        eligible = _pin_candidates(db, cfg, posted) if limit else []
        candidates = _spread(eligible, cfg, streak, limit)
        selected = db.delivery_batch("pinterest", delivery_run_key(now, run_key),
                                     [d["dedupe_key"] for d, _ in candidates])
        by_key = {d["dedupe_key"]: (d, path) for d, path in eligible}
        candidates = [by_key[key] for key in selected if key in by_key][:limit]
        if not candidates:
            log.info("pinterest: posted 0 (today %d/%d)", today, daily_max)
            return 0
        count = 0
        for deal, path in candidates:
            key = deal["dedupe_key"]
            if not _spread([(deal, path)], cfg, streak, 1):
                continue
            name = board_name(cfg, deal.get("category"))
            board_id = _board(db, cfg, api, headers, name)
            previous = streak
            streak = {"board": name, "count": previous.get("count", 0) + 1
                      if previous.get("board") == name else 1}
            spread[environment] = streak
            db.meta_set("pinterest_spread", json.dumps(spread))
            try:
                if not prepare_delivery(db, "pinterest", key, now, run_key):
                    streak = previous
                    spread[environment] = streak
                    db.meta_set("pinterest_spread", json.dumps(spread))
                    continue
                pin = api.request("POST", "/pins", headers=headers,
                                  json=pin_payload(deal, path, board_id,
                                                   cfg.pinterest.get("disclosure", "#ad")))
                posted[deal["dedupe_key"]] = {
                    "pin_id": str(pin["id"]), "at": util.iso(now), "environment": environment,
                    "board": name, "board_id": board_id, "category": deal.get("category") or "other",
                }
                db.meta_set("pinterest_posted", json.dumps(posted))
                db.finish_outgoing("pinterest", key, now)
                count += 1
            except _APIError as error:
                if error.status is not None and 400 <= error.status < 500:
                    db.finish_outgoing("pinterest", key, now, rejected=True)
                    streak = previous
                    spread[environment] = streak
                    db.meta_set("pinterest_spread", json.dumps(spread))
                log.warning("pinterest: %s%s", "not connected, " if error.status == 401 else "", error)
                if api.stopped:
                    break
        log.info("pinterest: posted %d (today %d/%d), board ok", count, today + count, daily_max)
    except _APIError as error:
        log.warning("pinterest: %s%s", "not connected, " if error.status == 401 else "", error)
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        log.warning("pinterest: invalid state or response")
    finally:
        if api:
            if headers and not api.stopped:
                from ..growth.pinterest import collect_daily
                collect_daily(db, cfg, api, headers, now)
            api.client.close()
    return 0
