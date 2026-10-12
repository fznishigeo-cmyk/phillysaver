"""Pinterest OAuth and hourly Pins. Tokens stay in encrypted database state.

Only the deployed public picks are eligible. Each successful Pin is committed
locally before the next request; the workflow persists that state to `data`.
"""

from __future__ import annotations

import json
import logging
import math
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx

from .. import util
from ..config import get_secret

log = logging.getLogger(__name__)
API_BASES = {
    "sandbox": "https://api-sandbox.pinterest.com/v5",
    "production": "https://api.pinterest.com/v5",
}
SCOPES = "boards:read,boards:write,pins:read,pins:write,user_accounts:read"


class _APIError(Exception):
    """Never retain a response body, request, code or credentials in an error."""

    def __init__(self, status: int | None = None):
        self.status = status
        super().__init__(f"HTTP {status}" if status is not None else "request failed")


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
            raise _APIError(response.status_code)
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


def _board(db, cfg, api: _API, headers: dict) -> str:
    name = cfg.pinterest.get("board_name", "Deals up to 90% off")
    environment = _environment(cfg)
    board = _meta(db, "pinterest_board")
    if board.get("name") == name and board.get("environment") == environment and board.get("id"):
        return board["id"]
    bookmark = None
    seen = set()
    while True:
        data = api.request("GET", "/boards", headers=headers,
                           params={"bookmark": bookmark} if bookmark else {})
        board = next((b for b in data.get("items", []) if b.get("name") == name), None)
        if board:
            break
        bookmark = data.get("bookmark")
        if not bookmark:
            board = api.request("POST", "/boards", headers=headers,
                                json={"name": name, "privacy": "PUBLIC"})
            break
        if bookmark in seen:
            raise _APIError()
        seen.add(bookmark)
    board_id = str(board["id"])
    db.meta_set("pinterest_board", json.dumps({
        "id": board_id, "name": name, "environment": environment,
    }))
    return board_id


def _word_trim(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit + 1].rsplit(" ", 1)[0] if " " in text[:limit + 1] else ""


def _without_prices(text: str) -> str:
    # Item names can contain a quoted price or a gift-card denomination.
    text = re.sub(r"(?:[$£€]\s*[\d,.]+|\bUSD\s*[\d,.]+|[\d,.]+\s*USD\b)", "", text,
                  flags=re.IGNORECASE)
    return " ".join(text.replace("$", "").split())


def pin_payload(deal: dict, path: str, board_id: str, disclosure: str) -> dict:
    item = _without_prices(str(deal.get("item") or ""))
    store = _without_prices(str(deal.get("store") or ""))
    pct = math.floor(float(deal["pct_at_checkout"]) + 0.5)
    # Reserve the facts and disclosure before trimming a long item name.
    disclosure = _word_trim(_without_prices(disclosure), 100) or "#ad"
    suffix = f". {pct}% off at {_word_trim(store, 100)}, verified by wowpriced. {disclosure}"
    description = _word_trim(item, 500 - len(suffix)) + suffix
    return {
        "board_id": board_id,
        "title": _word_trim(f"{pct}% off {item}", 100),
        "description": description,
        "link": "https://wowpriced.com" + path,
        "alt_text": str(deal.get("item") or ""),
        "media_source": {"source_type": "image_url", "url": deal["image_url"]},
    }


def _candidates(db, cfg, posted: dict) -> list[tuple[dict, str]]:
    out = Path(cfg.site_dir)
    deals = json.loads((out / "deals.json").read_text())["deals"]
    paths = _meta(db, "seo_deal_paths")
    candidates = []
    for deal in deals:
        if (deal.get("locked") or deal.get("status") != "alive" or not deal.get("pick")
                or not deal.get("dedupe_key") or deal["dedupe_key"] in posted):
            continue
        image = urlsplit(deal.get("image_url") or "")
        path = paths.get(str(deal.get("key") or "")[:8], "")
        if (image.scheme != "https" or not image.hostname
                or not re.fullmatch(r"/deal/[a-z0-9-]+/", path)
                or not (out / path.strip("/") / "index.html").is_file()):
            continue
        candidates.append((deal, path))
    return sorted(candidates, key=lambda row: row[0]["pick"])


def post(db, cfg, *, transport=None, now: datetime | None = None) -> int:
    if not cfg.pinterest.get("enabled", False):
        log.info("pinterest: disabled")
        return 0
    if not _has_secrets():
        log.info("pinterest: no secrets")
        return 0
    api = None
    try:
        api = _API(cfg, transport=transport)
        now = (now or util.now_utc()).astimezone(timezone.utc)
        token = _access_token(db, cfg, api, now)
        if not token:
            return 0
        posted = _meta(db, "pinterest_posted")
        today = sum(1 for entry in posted.values()
                    if (at := util.parse_iso(entry.get("at"))) and at.date() == now.date())
        daily_max = max(0, int(cfg.pinterest.get("daily_max", 20)))
        limit = min(max(0, int(cfg.pinterest.get("pins_per_run", 2))), max(0, daily_max - today))
        candidates = _candidates(db, cfg, posted)[:limit] if limit else []
        if not candidates:
            log.info("pinterest: posted 0 (today %d/%d)", today, daily_max)
            return 0
        headers = {"Authorization": f"Bearer {token}"}
        board_id = _board(db, cfg, api, headers)
        count = 0
        for deal, path in candidates:
            try:
                pin = api.request("POST", "/pins", headers=headers,
                                  json=pin_payload(deal, path, board_id,
                                                   cfg.pinterest.get("disclosure", "#ad")))
                posted[deal["dedupe_key"]] = {"pin_id": str(pin["id"]), "at": util.iso(now)}
                db.meta_set("pinterest_posted", json.dumps(posted))
                count += 1
            except _APIError as error:
                log.warning("pinterest: %s%s", "not connected, " if error.status == 401 else "", error)
                if error.status == 401:
                    break
        log.info("pinterest: posted %d (today %d/%d), board ok", count, today + count, daily_max)
    except _APIError as error:
        log.warning("pinterest: %s%s", "not connected, " if error.status == 401 else "", error)
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        log.warning("pinterest: invalid state or response")
    finally:
        if api:
            api.client.close()
    return 0
