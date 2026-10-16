"""Manual Keyword Planner reads; results go only to the private reports repo."""

from __future__ import annotations

import logging
import re
import time as clock
from datetime import timezone
from decimal import Decimal

import httpx

from ..config import get_secret, validated_keyword_volumes
from ..util import now_utc
from . import google_ads, report

log = logging.getLogger("dealradar.growth")
METRICS_URL = "https://googleads.googleapis.com/{}/customers/{}:generateKeywordHistoricalMetrics"
_MONTHS = ("JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY",
           "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER")


def _allowed_urls(cfg) -> frozenset[str]:
    if (not re.fullmatch(r"v[0-9]+", cfg.growth_google_ads_api_version)
            or not re.fullmatch(r"[0-9]+", cfg.growth_google_ads_customer_id)
            or (cfg.growth_google_ads_login_customer_id
                and not re.fullmatch(r"[0-9]+", cfg.growth_google_ads_login_customer_id))):
        raise google_ads.GoogleAdsError("INVALID_CONFIG")
    return frozenset((google_ads.TOKEN_URL, METRICS_URL.format(
        cfg.growth_google_ads_api_version, cfg.growth_google_ads_customer_id)))


class _GoogleRequests:
    """Exact URL allowlist, no redirects, one shared retry and three sends total."""

    def __init__(self, client, cfg):
        self.client = client
        self.allowed_urls = _allowed_urls(cfg)
        self.user_agent = cfg.resolved_user_agent()
        self.last = None
        self.sent = 0
        self.retried = False

    def post(self, url, **kwargs):
        if url not in self.allowed_urls:
            raise google_ads.GoogleAdsError("URL_NOT_ALLOWED")
        headers = {**kwargs.pop("headers", {}), "User-Agent": self.user_agent}
        while self.sent < 3:
            if self.last is not None:
                clock.sleep(max(0, self.last + 1 - clock.monotonic()))
            self.last = clock.monotonic()
            self.sent += 1
            resp = self.client.post(url, headers=headers, follow_redirects=False, **kwargs)
            exhausted = (resp.status_code == 429 or "RESOURCE_EXHAUSTED" in
                         google_ads._codes(resp, codes_only=True).split())
            if exhausted and not self.retried and self.sent < 3:
                self.retried = True
                clock.sleep(30)
                continue
            google_ads._check(resp, codes_only=True)
            if exhausted:
                raise google_ads.GoogleAdsError("RESOURCE_EXHAUSTED")
            return resp
        raise google_ads.GoogleAdsError("REQUEST_LIMIT")


def _number(value):
    return None if value is None else google_ads.nonnegative_int(value)


def _text(value, *, required=False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise google_ads.GoogleAdsError("INVALID_RESPONSE")
    return value


def _month_range(metrics) -> tuple[str, str]:
    months = metrics.get("monthlySearchVolumes", [])
    if not isinstance(months, list):
        raise google_ads.GoogleAdsError("INVALID_RESPONSE")
    volumes = []
    for month in months:
        if not isinstance(month, dict):
            raise google_ads.GoogleAdsError("INVALID_RESPONSE")
        count = _number(month.get("monthlySearches"))
        year = _number(month.get("year"))
        name = _text(month.get("month"))
        if (year is not None and year == 0) or (name and name not in _MONTHS):
            raise google_ads.GoogleAdsError("INVALID_RESPONSE")
        if count is not None:
            label = (f"{year}-{_MONTHS.index(name) + 1:02d}: {count}"
                     if year is not None and name else str(count))
            volumes.append((count, label))
    if not volumes:
        return "", ""
    return min(volumes)[1], max(volumes)[1]


def _dollars(value) -> str:
    number = _number(value)
    return "" if number is None else f"${Decimal(number) / Decimal(1_000_000):.6f}"


def _table(doc, keywords) -> tuple[str, int]:
    if not isinstance(doc, dict) or "error" in doc:
        raise google_ads.GoogleAdsError("INVALID_RESPONSE")
    results = doc.get("results", [])  # Google may omit an empty repeated field.
    if not isinstance(results, list):
        raise google_ads.GoogleAdsError("INVALID_RESPONSE")
    rows, covered = [], set()
    for result in results:
        if not isinstance(result, dict):
            raise google_ads.GoogleAdsError("INVALID_RESPONSE")
        keyword = _text(result.get("text"), required=True)
        variants = result.get("closeVariants", [])
        metrics = result.get("keywordMetrics", {})
        if not isinstance(variants, list) or not isinstance(metrics, dict):
            raise google_ads.GoogleAdsError("INVALID_RESPONSE")
        variants = [_text(v, required=True) for v in variants]
        covered.update(v.strip().lower() for v in [keyword, *variants])
        avg = _number(metrics.get("avgMonthlySearches"))
        low, high = _month_range(metrics)
        rows.append((avg, [keyword, ", ".join(variants), "" if avg is None else str(avg),
                           low, high, _text(metrics.get("competition")),
                           _dollars(metrics.get("lowTopOfPageBidMicros")),
                           _dollars(metrics.get("highTopOfPageBidMicros")),
                           _dollars(metrics.get("averageCpcMicros")), ""]))
    rows.extend((None, [k, "", "", "", "", "", "", "", "", "merged or no data"])
                for k in keywords if k not in covered)
    rows.sort(key=lambda row: (-(row[0] if row[0] is not None else -1), row[1][0]))
    lines = ["| Keyword | Close variants | Avg monthly searches | Lowest month | Highest month | "
             "Competition | Low top of page bid (USD) | High top of page bid (USD) | Average CPC (USD) | Status |",
             "|---|---|---:|---|---|---|---:|---:|---:|---|"]
    for _, cells in rows:
        lines.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ").replace("\r", " ")
                                      for c in cells) + " |")
    return "\n".join(lines) + "\n", len(results)


def run(cfg, *, http_client=None, now=None) -> int:
    credentials = [get_secret(name) for name in
                   ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN")]
    if not all(credentials):
        log.warning("keyword volumes: MISSING_CREDENTIALS")
        log.info("keyword volumes: not saved")
        return 1
    client = http_client if http_client is not None else httpx.Client(timeout=30)
    saved = False
    try:
        try:
            settings = validated_keyword_volumes(cfg.growth_keyword_volumes)
        except ValueError:
            raise google_ads.GoogleAdsError("INVALID_CONFIG") from None
        google = _GoogleRequests(client, cfg)
        token = google_ads.access_token(google, *credentials, codes_only=True)
        if not isinstance(token, str) or not token.strip():
            raise google_ads.GoogleAdsError("INVALID_RESPONSE")
        headers = {"Authorization": f"Bearer {token}"}
        if cfg.growth_google_ads_login_customer_id:
            headers["login-customer-id"] = cfg.growth_google_ads_login_customer_id
        log.info("keyword volumes: keywords sent %s", len(settings["keywords"]))
        resp = google.post(METRICS_URL.format(cfg.growth_google_ads_api_version,
                                            cfg.growth_google_ads_customer_id), headers=headers, json={
            "keywords": settings["keywords"],
            "geoTargetConstants": [f"geoTargetConstants/{settings['geo_target']}"],
            "language": f"languageConstants/{settings['language']}",
            "keywordPlanNetwork": "GOOGLE_SEARCH",
            "historicalMetricsOptions": {"includeAverageCpc": True},
        })
        table, count = _table(resp.json(), settings["keywords"])
        log.info("keyword volumes: results received %s", count)
        day = (now or now_utc()).astimezone(timezone.utc).date()
        subject = (f"Keyword volumes — {day.isoformat()} UTC; geo {settings['geo_target']}; "
                   f"language {settings['language']}; API {cfg.growth_google_ads_api_version}. "
                   "Google rounds these; accounts with little spend may get coarse values")
        clock.sleep(1)
        saved = report.save_copy(cfg, subject, table, day, client=client,
                                 path=f"keywords/{day.isoformat()}.md",
                                 message=f"Keyword volumes {day.isoformat()}")
    except google_ads.GoogleAdsError as exc:
        log.warning("keyword volumes: %s", exc)
    except httpx.HTTPError:
        log.warning("keyword volumes: HTTP_ERROR")
    except (ValueError, TypeError, KeyError, AttributeError):
        log.warning("keyword volumes: INVALID_RESPONSE")
    finally:
        if http_client is None:
            client.close()
    log.info("keyword volumes: %s", "saved" if saved else "not saved")
    return 0 if saved else 1
