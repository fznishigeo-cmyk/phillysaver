"""Instagram source adapter: stubbed, off by default.

The intended official flow (Instagram Graph API hashtag search, kept here as a
sketch for when the connector is actually built):

    1. Resolve the hashtag id:
       GET https://graph.facebook.com/v21.0/ig_hashtag_search
           ?user_id=<IG_USER_ID>&q=<hashtag>&access_token=<TOKEN>
    2. Read recent media for that hashtag:
       GET https://graph.facebook.com/v21.0/<hashtag_id>/recent_media
           ?user_id=<IG_USER_ID>&fields=id,caption,permalink,timestamp
           &access_token=<TOKEN>
    3. Map each media item -> RawPost:
       uid=item['id']; url=item['permalink'] (credit link);
       title=first line of caption; text=caption;
       published_at=util.parse_feed_date(item['timestamp'], source.tz, now).

Requires a Business/Creator account, app review, and an IG user id — none of
which exist yet, so ``fetch_posts`` always raises :class:`AdapterDisabled`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ..config import AppConfig
from ..db import DB
from ..errors import AdapterDisabled
from ..models import RawPost, SourceConfig
from .base import SourceAdapter

log = logging.getLogger("dealradar.fetch.instagram_source")


class InstagramAdapter(SourceAdapter):
    def fetch_posts(
        self,
        source: SourceConfig,
        fetcher: Any,
        db: DB,
        cfg: AppConfig,
        now: datetime,
        deadline: float | None,
    ) -> list[RawPost]:
        raise AdapterDisabled("instagram connector stubbed, off by default")
