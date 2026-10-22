"""Configuration loading: config.yaml + sources.yaml + env-only secrets."""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .models import SourceConfig

ROOT = Path(__file__).resolve().parent.parent

CHECKER_STORES = {"amazon": "link_only", "walmart": "history", "ebay": "live_only",
                  "bestbuy": "history", "target": "history", "woot": "history", "other": "history"}


def validated_keepa_focus_finder(value) -> dict:
    defaults = dict(enabled=False, idle_share=.7, max_pages_per_run=80,
                    max_new_deals_per_run=10, max_price=50, max_rank=30000,
                    min_rating=40, min_reviews=50, groups={})
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise ValueError("keepa_focus_finder must contain supported settings only")
    options = defaults | value
    if type(options["enabled"]) is not bool:
        raise ValueError("keepa_focus_finder.enabled must be a boolean")
    for key, low, high in (("idle_share", 0, 1), ("max_price", .01, 10000),
                           ("max_pages_per_run", 0, 1000), ("max_new_deals_per_run", 0, 100),
                           ("max_rank", 1, 1000000), ("min_rating", 0, 50), ("min_reviews", 0, 1000000)):
        v = options[key]
        integer = key not in ("idle_share", "max_price")
        if (type(v) not in ((int,) if integer else (int, float))
                or not math.isfinite(v) or not low <= v <= high):
            raise ValueError(f"keepa_focus_finder.{key} must be a finite {'integer' if integer else 'number'} in {low}..{high}")
    groups = options["groups"]
    if (not isinstance(groups, dict) or len(groups) > 20
            or any(not isinstance(g, str) or not g.strip() for g in groups)):
        raise ValueError("keepa_focus_finder.groups must map names to category paths")
    for paths in groups.values():
        if (not isinstance(paths, list) or not paths or len(paths) > 100
                or any(not isinstance(p, str) or any(not n.strip() for n in p.split(">")) for p in paths)
                or len(set(paths)) != len(paths)):
            raise ValueError("keepa_focus_finder.groups must contain unique nonempty category paths")
    return options


def validated_compare(value) -> dict:
    defaults = dict(refresh=False, publish="off", terms_file="data/compare_terms.yaml",
                    candidates_per_term=20, refresh_hours=12, finder_days=7,
                    min_rating=40, min_reviews=50, prime_only=True,
                    tokens_per_day=6000, tokens_per_run=300, finder_reserve_tokens=30, min_bucket_share=0.8,
                    max_seconds_per_run=20, request_timeout_s=5, max_price_age_h=24, max_terms=120)
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise ValueError("compare must contain supported settings only")
    settings = defaults | value
    for key in ("refresh", "prime_only"):
        if type(settings[key]) is not bool:
            raise ValueError(f"compare.{key} must be boolean")
    if not isinstance(settings["publish"], str) or settings["publish"] not in {"off", "preview", "on"}:
        raise ValueError("compare.publish must be off, preview or on")
    if not isinstance(settings["terms_file"], str) or not settings["terms_file"].strip():
        raise ValueError("compare.terms_file must be a nonempty path")
    for key, low, high in (("candidates_per_term", 1, 50), ("min_rating", 0, 50),
                           ("min_reviews", 0, 1000000), ("tokens_per_day", 0, 86400),
                           ("tokens_per_run", 0, 86400), ("finder_reserve_tokens", 1, 86400), ("max_terms", 1, 120)):
        if type(settings[key]) is not int or not low <= settings[key] <= high:
            raise ValueError(f"compare.{key} must be an integer in {low}..{high}")
    for key, low, high in (("refresh_hours", 0.01, 8760), ("finder_days", 0.01, 365),
                           ("max_seconds_per_run", 0.01, 20), ("request_timeout_s", 0.01, 20),
                           ("max_price_age_h", 0.01, 24),
                           ("min_bucket_share", 0, 1)):
        if (type(settings[key]) not in {int, float} or not math.isfinite(settings[key])
                or not low <= settings[key] <= high):
            raise ValueError(f"compare.{key} must be a finite number in {low}..{high}")
    if settings["refresh_hours"] >= settings["max_price_age_h"]:
        raise ValueError("compare.refresh_hours must be less than compare.max_price_age_h")
    return settings


def validated_extension(value) -> dict:
    if not isinstance(value, dict) or set(value) - {"amazon_mode"}:
        raise ValueError("extension must contain amazon_mode only")
    settings = {"amazon_mode": "full"} | value
    if not isinstance(settings["amazon_mode"], str) or settings["amazon_mode"] not in {"full", "link"}:
        raise ValueError("extension.amazon_mode must be full or link")
    return settings


def validated_android(value) -> dict:
    defaults = dict(enabled=False, package="com.wowpriced.app",
                    sha256_cert_fingerprints=[], amazon_in_app="limited")
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise ValueError("pwa.android must contain supported settings only")
    settings = defaults | value
    if type(settings["enabled"]) is not bool:
        raise ValueError("pwa.android.enabled must be boolean")
    package = settings["package"]
    if not isinstance(package, str) or not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", package):
        raise ValueError("pwa.android.package must be an Android package identifier")
    fingerprints = settings["sha256_cert_fingerprints"]
    if not isinstance(fingerprints, list) or any(
            not isinstance(f, str) or not re.fullmatch(r"(?:[0-9A-F]{2}:){31}[0-9A-F]{2}", f)
            for f in fingerprints):
        raise ValueError("pwa.android.sha256_cert_fingerprints must contain uppercase SHA-256 certificates")
    settings["sha256_cert_fingerprints"] = list(fingerprints)
    if settings["amazon_in_app"] not in ("limited", "as_site"):
        raise ValueError("pwa.android.amazon_in_app must be limited or as_site")
    return settings


def validated_store_policy(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("store_policy must be a mapping")
    if "amazon" in value:
        raise ValueError("Amazon uses amazon_history / amazon_watches")
    if (set(value) - (set(CHECKER_STORES) - {"amazon"})
            or any(not isinstance(v, str) or v not in {"history", "live_only"} for v in value.values())):
        raise ValueError("store_policy must map known stores to history or live_only")
    return dict.fromkeys(sorted(set(CHECKER_STORES) - {"amazon"}), "history") | value


def validated_ai_take(value) -> dict:
    defaults = dict(enabled=False, model="claude-haiku-5-5", max_pages_per_run=40,
                    max_usd_per_run=0.05, max_usd_per_day=0.40, max_words=45)
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise ValueError("product_pages.ai_take must contain supported settings only")
    settings = defaults | value
    if type(settings["enabled"]) is not bool:
        raise ValueError("product_pages.ai_take.enabled must be boolean")
    if not isinstance(settings["model"], str) or not re.fullmatch(r"[a-zA-Z0-9._:-]+", settings["model"]):
        raise ValueError("product_pages.ai_take.model must be a model identifier")
    for key, maximum in (("max_pages_per_run", 40), ("max_words", 45)):
        if type(settings[key]) is not int or not 1 <= settings[key] <= maximum:
            raise ValueError(f"product_pages.ai_take.{key} must be an integer in 1..{maximum}")
    for key in ("max_usd_per_run", "max_usd_per_day"):
        if type(settings[key]) not in {int, float} or not math.isfinite(settings[key]) or settings[key] < 0:
            raise ValueError(f"product_pages.ai_take.{key} must be finite and nonnegative")
    return settings


def validated_checker(value, amazon_history="show") -> dict:
    if not isinstance(value, dict) or set(value) - {"enabled", "stores", "ended_days", "shards"}:
        raise ValueError("checker must contain supported settings only")
    settings = {"enabled": False, "stores": dict(CHECKER_STORES), "ended_days": 90, "shards": 256} | value
    if type(settings["enabled"]) is not bool:
        raise ValueError("checker.enabled must be boolean")
    stores = settings["stores"]
    if (not isinstance(stores, dict) or set(stores) - set(CHECKER_STORES)
            or any(not isinstance(v, str) or v not in {"link_only", "live_only", "history"}
                   for v in stores.values())):
        raise ValueError("checker.stores must map known stores to link_only, live_only or history")
    settings["stores"] = dict(CHECKER_STORES) | stores
    if (settings["stores"]["amazon"] not in {"link_only", "history"}
            or settings["stores"]["ebay"] == "history"):
        raise ValueError("checker.stores cannot enable restricted store prices or history")
    if settings["stores"]["amazon"] == "history" and amazon_history != "show":
        settings["stores"]["amazon"] = "link_only"
        logging.getLogger(__name__).info("checker.stores.amazon uses link_only while amazon_history: hide")
    for name, low, high in (("ended_days", 1, 365), ("shards", 16, 1024)):
        n = settings[name]
        if type(n) is not int or not low <= n <= high or (name == "shards" and n & (n - 1)):
            raise ValueError(f"checker.{name} must be an integer in {low}..{high}"
                             + (" and a power of two" if name == "shards" else ""))
    return settings


def social_profiles(value: dict) -> dict[str, str]:
    """Public profile URLs only; rejected values never enter HTML or logs."""
    hosts = {"pinterest": {"pinterest.com", "www.pinterest.com"},
             "facebook": {"facebook.com", "www.facebook.com"},
             "instagram": {"instagram.com", "www.instagram.com"},
             "telegram": {"t.me", "telegram.me"}}
    out = {}
    for network, allowed in hosts.items():
        raw = value.get(network, "")
        if not raw:
            continue
        try:
            url = urlsplit(raw) if isinstance(raw, str) else None
            if (url and url.scheme == "https" and url.netloc in allowed
                    and url.path.strip("/") and not re.search(r'[\s<>"\'\\]', raw)):
                out[network] = raw
                continue
        except ValueError:
            pass
        logging.getLogger(__name__).warning("social.profiles.%s invalid; skipped", network)
    return out


def support_link(value: str) -> str:
    """Accept only Stripe's hosted Payment Links; never echo a rejected URL."""
    value = str(value or "").strip()
    try:
        url = urlsplit(value)
        if (value.startswith("https://buy.stripe.com/") and url.netloc == "buy.stripe.com"
                and url.path.startswith("/") and len(url.path) > 1
                and not re.search(r'[\s<>"\'\\]', value)):
            return value
    except ValueError:
        pass
    if value:
        logging.getLogger(__name__).warning("support.payment_link invalid; Support buttons hidden")
    return ""


# Secret names (environment variables only; never read from files, never committed).
SECRET_NAMES = [
    "DEALS_CLAUDE_API_KEY",
    "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT",
    "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "TELEGRAM_ADMIN_CHAT_ID",
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
    "WOOT_API_KEY", "MEH_API_KEY", "KEEPA_KEY", "THREADS_TOKEN",
    "BESTBUY_API_KEY", "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET",
    "STRIPE_SECRET_KEY",  # read-only restricted key: member list for alerts
    "NEWSLETTER_SECRET",  # bearer token for the Worker: /list (newsletter) and /api/publish (paywall)
    "NEWSLETTER_INBOX_TOKEN", "NEWSLETTER_FORWARD_TO",
    "CLICK_SIGNAL_TOKEN",
    "PINTEREST_APP_ID", "PINTEREST_APP_SECRET", "META_SYSTEM_TOKEN",
]


def get_secret(name: str) -> str:
    """Read a secret from the environment. Empty string when unset."""
    return os.environ.get(name, "").strip()


def error_summary(exc: Exception) -> str:
    """A log-safe error type and HTTP status, without exception text."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return type(exc).__name__ + (f" HTTP {status}" if isinstance(status, int) else "")


def redact_secrets(text: str) -> str:
    """Scrub secrets from text that may be persisted or published.

    Strips URL query strings (API keys travel there in some error messages)
    and masks any live secret value. Applied to every recorded source error
    and again when health data is published — defense in depth.
    """
    out = re.sub(r"\?[^\s'\"<>]+", "?<redacted-query>", text)
    for name in SECRET_NAMES:
        val = os.environ.get(name, "").strip()
        if len(val) >= 6 and val in out:
            out = out.replace(val, f"<{name}>")
    return out


@dataclass
class AppConfig:
    """Typed view over config.yaml. `raw` keeps the full parsed document."""

    raw: dict[str, Any] = field(default_factory=dict)
    accuracy_extra_samples_per_day: int = 0
    checker: dict = field(default_factory=lambda: validated_checker({}))
    compare: dict = field(default_factory=lambda: validated_compare({}))
    extension: dict = field(default_factory=lambda: validated_extension({}))

    # -- identity / politeness --
    contact_email: str = "set-me@example.com"
    user_agent: str = "dealradar/0.1"
    timezone: str = "America/New_York"

    # -- fetch --
    parallel_sources: int = 1
    request_timeout_s: float = 20.0
    per_source_budget_s: float = 90.0
    run_budget_s: float = 480.0
    min_host_interval_s: float = 1.0
    max_retries: int = 3
    circuit_breaker_failures: int = 3
    circuit_breaker_cooldown_h: float = 6.0
    redirect_max_hops: int = 5

    # -- parse --
    parse_model: str = ""               # "" = cheapest-model heuristic
    appeal_score: dict = field(default_factory=dict)  # private landing ratings; absent = OFF
    max_posts_per_run: int = 400
    max_chars_per_post: int = 6000
    spend_cap_usd_per_run: float = 0.50
    spend_cap_usd_per_day: float = 5.00
    # A temporary, self-expiring raise of the daily cap (operator-approved):
    # used instead of spend_cap_usd_per_day on UTC days before
    # spend_cap_boost_until (an ISO date, exclusive); 0 / "" = none.
    spend_cap_boost_usd_per_day: float = 0.0
    spend_cap_boost_until: str = ""
    spend_pacing: bool = True
    spend_pacing_headroom_hours: float = 2.0
    retries_on_invalid_json: int = 2
    # Probe the structured messages.parse endpoint for extraction. Off by
    # default: our schema is rejected as "too complex" after a MINUTES-long
    # stall, so we go straight to the JSON create() path that handles it.
    parse_structured_output: bool = False
    # Extraction calls in flight at once. Parse throughput is bound by API
    # round-trip time, not tokens; a small pool multiplies posts-per-run.
    parse_concurrency: int = 1
    fast_lane_parse_budget_s: float = 150.0
    prefilter_below_floor: str = "off"  # off | shadow | on
    bounded_prefilter: dict = field(default_factory=lambda: validated_bounded_prefilter({}))
    prefilter_complete_days: int = 7
    prefilter_min_candidates: int = 200
    prefilter_min_source_sites: int = 3
    prefilter_max_gap_minutes: int = 90
    model_pricing_per_mtok: dict[str, dict[str, float]] = field(default_factory=dict)
    second_opinion_model: str = ""      # "" disables the second-opinion pass
    max_second_opinions_per_run: int = 8
    critic_model: str = ""              # "" disables the deal critic
    critic_enabled: bool = True
    critic_batch_size: int = 8
    critic_reserve_share: float = 0.5   # of each run's allowance, kept for the critic
    recategorize_other_per_run: int = 120  # live "other" titles re-read per run; 0 = off
    critic_shadow_model: str = "claude-haiku-5-5"
    critic_shadow_batches_per_day: int = 12
    critic_tiering: dict = field(default_factory=lambda: {
        "enabled": False, "model": "claude-haiku-5-5", "min_confidence": 0.8,
        "max_tokens_per_deal": 160,
        "high_price_usd": 250.0, "shadow_batches_per_day": 2,
        "window_deals": 32, "min_judged_deals": 16, "max_disagreement": 0.10,
        "second_opinion_calls_per_day": 12, "second_opinion_yield_floor": 0.10,
        "second_opinion_min_calls": 3, "second_opinion_window_days": 7,
    })
    exclude_suspect_msrp: bool = True
    audit_mode: str = "shadow"
    audit_near_price_pct: float = 10.0
    audit_history_min_near: int = 2
    audit_history_min_span_h: float = 48.0
    audit_extreme_pct: float = 85.0
    audit_round_price_multiple: float = 50.0
    audit_reference_max_excess_pct: float = 10.0

    # -- useful-yield intake (off preserves the original rotation/interleave) --
    intake_mode: str = "off"  # off | shadow | on
    intake_window_days: int = 7
    intake_min_history_hours: float = 24.0
    intake_min_posts: int = 20
    intake_floor_share: float = 0.2  # shared equally across sources
    intake_exploration_share: float = 0.2
    intake_max_source_share: float = 0.25
    intake_max_store_share: float = 0.5
    intake_max_wait_hours: float = 6.0

    # -- publish / ranking --
    min_pct: float = 70.0
    used_min_pct: float = 80.0        # absolute floor for used/open-box deals
    min_abs_savings: float = 5.0
    site_show_from_pct: float = 70.0  # site-only lower tier (= min_pct: off)
    show_dead_hours: float = 24.0      # dead deals linger on the site this long
    drop_minutes: int = 15            # the page's drop clock step (meta.cadence_minutes)
    drop_clock: dict = field(default_factory=lambda: {"warm_s": 300, "hot_s": 120, "final_s": 30})
    manifest_refresh: dict = field(default_factory=lambda: validated_manifest_refresh(None))
    front_page: dict = field(default_factory=dict)  # All top picks / fresh drops mix
    site_dir: str = "site"
    photoless_last: bool = False
    max_photoless_h: float | None = None
    hide_deal_site_links: bool = False
    reuse_link_facts_enabled: bool = False
    images_asin_backfill_per_run: int = 60
    images_post_ingest_keepa: bool = False
    images_short_links_per_run: int = 10
    images_keepa_search_per_run: int = 10
    images_keepa_search_per_day: int = 120
    images_keepa_match_min: float = 0.85
    amazon_as_of_enabled: bool = False
    amazon_history: str = "show"
    amazon_reference: str = "history"
    amazon_watches: str = ""  # "" follows amazon_history; on | off
    store_policy: dict = field(default_factory=lambda: validated_store_policy({}))
    offscreen_cards: bool = False
    home_link_resets: bool = False
    trending_auto_cards: int | None = None
    click_price_home_fill: bool = False
    credit_links: bool = True
    brand_pages: bool = False
    dark_polish: bool = False
    strip_comments: bool = False
    home_title: str = ""
    home_description: str = ""
    search_amazon: bool = False
    search: dict = field(default_factory=lambda: validated_search(None))
    review_checker: dict = field(default_factory=lambda: validated_review_checker({}))
    search_boost: dict = field(default_factory=lambda: validated_search_boost(None))
    search_box_glow: bool = False
    same_product_spacing: bool = False
    card_tidy: bool = False
    page_polish: bool = False
    header_polish: bool = False
    all_discounts_button: bool = False
    discounts_from_link: bool = False
    tool_pages: dict = field(default_factory=lambda: validated_tool_pages({}))
    deal_spotter: dict = field(default_factory=lambda: validated_deal_spotter({}))
    tool_pages_hero: bool = False
    tool_pages_1020: bool = False
    tool_pages_1020_phone_links: str = "chips"
    tool_pages_1020_home_links: bool = False
    search_polish_1019: bool = False
    search_buttons_1019: bool = False
    search_discounts_button: bool = False
    card_click_1020: bool = False
    buttons_to_tools: bool = False
    amazon_price_cap: bool = False
    tools_new_tab: bool = False
    tool_buttons_outside: bool = False
    checker_similar_heading: bool = False
    checker_similar_typed_words: bool = False
    price_age_minutes: bool = False
    tool_faq_wording: bool = False
    amazon_price_cap_usd: float = 50.0
    phone_tagline_count: bool = False
    timer_center: bool = False
    search_focus_ring_keyboard_only: bool = False
    hide_timer_ticker_confetti: bool = False
    sticky_buy_bar: bool = False
    tagline_full_row: bool = False
    price_shipping_note: bool = False
    timer_pill_ring: bool = False
    phone_timer_raise: bool = False
    fresh_more_plain: bool = False
    search_circle: bool = False
    search_analyze: dict = field(default_factory=lambda: validated_search_analyze(None))
    analyze_top_sellers: bool = False
    search_more: dict = field(default_factory=lambda: validated_search_more(None))
    static_photos: bool = False
    open_box_scarcity: bool = False
    open_box_counts: bool = False
    drop_rotation_windows: int = 3
    drop_fresh: bool = False
    drop_shuffle: bool = False
    unconfirmed_label_enabled: bool = False
    unconfirmed_label_max_h_new: float = 48.0
    unconfirmed_label_max_h_used: float = 48.0
    # The site's public domain: canonical URLs, sitemap, IndexNow. (Actions
    # Pages deploys ignore the CNAME file; Settings -> Pages holds the domain.)
    custom_domain: str = ""
    ftc_disclosure: str = ""
    affiliate_amazon_tag: str = ""     # Amazon Associates Store ID ("" = links untouched)
    affiliate_sovrn_enabled: bool = False
    affiliate_sovrn_key: str = ""      # public link key, invalid/empty = off
    affiliate_disclosure: str = ""     # the disclosure used once a Store ID is set
    # One store page -> one affiliate short link (network onboarding, e.g. the
    # Sovrn first-click check): {"walmart.com/222918738": "https://sovrn.co/..."}.
    affiliate_link_overrides: dict = field(default_factory=dict)
    affiliate_competitor_tracker_hosts: list[str] = field(default_factory=list)
    affiliate_repair_competitor_trackers: bool = False
    affiliate_exact_variant_links: bool = False  # size/colour children open as themselves
    affiliate_ebay_campaign_id: str = ""  # eBay Partner Network, 10 digits ("" = off)
    # -- seo: category hubs and IndexNow (config.yaml seo:) --
    seo_hub_min_items: int = 10        # visible live offers for an indexable hub
    seo_event_pages_enabled: bool = False
    seo_event_pages_events: list[dict[str, Any]] = field(default_factory=list)
    seo_event_pages_min_items: int = 10
    seo_event_pages_max_items: int = 60
    seo_event_pages_intro: dict[str, str] = field(default_factory=dict)
    seo_home_min_items: int = 1
    seo_best_today_min_items: int = 1
    seo_deal_page_min_items: int = 1
    seo_deal_pages_max: int = 3000
    seo_deal_pages_indexable: bool = True
    seo_posted_deal_redirects_enabled: bool = True
    seo_posted_deal_redirects_max_rules: int = 1500
    seo_product_pages_enabled: bool = False
    ai_take: dict = field(default_factory=lambda: validated_ai_take({}))
    seo_product_pages_record_own_checks_enabled: bool = True
    seo_product_pages_record_own_checks_max_per_offer_per_day: int = 24
    seo_product_pages_min_checks: int = 6
    seo_product_pages_min_span_days: float = 7
    seo_product_pages_max_new_indexable_per_day: int = 50
    seo_product_pages_max_indexable: int = 2000
    seo_product_pages_keep_days: int = 90
    seo_product_pages_prune_days: int = 180
    seo_product_pages_max_pages: int = 6000
    seo_product_pages_lower_ratio: float = 0.95
    seo_product_pages_higher_ratio: float = 1.05
    seo_product_pages_deal_history_limit: int = 20
    seo_store_page_min_deals: int = 15
    seo_store_pages_max: int = 12
    seo_price_page_min_deals: int = 10
    seo_price_pages: list[int] = field(default_factory=lambda: [10, 25, 50])
    seo_lastmod_retention_days: int = 35  # absent URLs; current URLs are retained
    traffic_feed_enabled: bool = False
    traffic_feed_max_items: int = 50
    traffic_feed_details: bool = True
    traffic_share_enabled: bool = False
    traffic_related_hubs_enabled: bool = False
    traffic_hub_pagination_enabled: bool = False
    traffic_product_families_enabled: bool = False
    traffic_product_family_min_offers: int = 3
    product_entities_backfill_per_run: int = 200
    product_entities_also_at_enabled: bool = False
    product_entities_lowest_seen_enabled: bool = False
    product_entities_min_price_ratio: float = 0.4
    product_entities_max_price_ratio: float = 2.5
    product_entities_shadow_retention_days: int = 7
    seo_indexnow_key: str = ""         # "" = no key file, no IndexNow
    seo_indexnow_min_hours: float = 6.0
    seo_indexnow_resubmit_days: float = 7.0  # unchanged received URLs (0 = never)
    seo_indexnow_batch_size: int = 10000  # protocol maximum
    seo_indexnow_retry_initial_minutes: float = 15.0
    seo_indexnow_retry_max_minutes: float = 360.0
    tier_boost: dict[str, float] = field(default_factory=dict)
    abs_savings_weight: float = 0.1
    condition_penalty: float = 2.0
    age_penalty_per_hour: float = 0.2

    # -- verify / dedupe --
    corroborate_window_h: float = 24.0
    history_min_observations: int = 5
    fuzzy_threshold: float = 0.90

    # -- telegram / digest / alerting --
    telegram_channels: list[str] = field(default_factory=list)
    telegram_channel_sources: dict[str, str] = field(default_factory=dict)
    telegram_messages_per_minute: int = 15
    resend_price_drop_pct: float = 10.0
    telegram_dry_run: bool = False
    telegram_channel: dict[str, Any] = field(default_factory=dict)
    digest_hour_local: int = 8
    digest_timezone: str = "America/New_York"
    digest_recipient: str = ""
    min_items_ratio: float = 0.5
    tripped_sources_warn: int = 5

    # -- growth: the daily private report (config.yaml growth:); "" skips a section --
    growth_report_days: int = 7
    growth_expired_click_hours: float = 3.0
    growth_ga4_property_id: str = ""
    growth_search_console_site: str = ""
    growth_sitemap_url: str = ""
    growth_meta_graph_version: str = "v24.0"
    growth_meta_ad_account_id: str = ""
    growth_meta_page_id: str = ""
    growth_instagram_user_id: str = ""
    growth_google_ads_api_version: str = "v26"
    growth_google_ads_customer_id: str = ""
    growth_google_ads_login_customer_id: str = ""
    growth_keyword_volumes: dict = field(default_factory=dict)
    growth_monthly_price_usd: float = 9.99

    # -- members: Stripe subscribers get every new verified deal by email --
    subscribe_url: str = ""            # Stripe Payment Link (site Subscribe button)
    portal_url: str = ""               # Stripe customer portal (manage / cancel)
    members_email_enabled: bool = True     # the members' deal email at all
    members_email_every_minutes: int = 60  # one bundled email per window, max
    members_email_hour_local: int = 7      # daily window: first run at/after this local hour
    members_email_timezone: str = "America/New_York"
    members_max_deals_per_email: int = 40  # the best N; the email says how many more

    # -- paywall: visitors see the top N deals, members the full payload --
    paywall_enabled: bool = True
    paywall_open_access: bool = False  # every deal in full for everyone; sign-up unchanged
    paywall_teaser_count: int = 8      # unlocked deals on the public site
    paywall_teaser_pool: int = 0       # each drop unlocks a new teaser_count of the top N (0 = every live deal)
    paywall_trial_days: int = 7
    paywall_yearly_price_usd: float = 0.0  # page copy only; 0 hides yearly
    paywall_api_url: str = ""          # Worker base URL ("" = no upload, no UI)
    paywall_google_client_id: str = "" # public OAuth client ID ("" hides Google)
    # -- free site: email sign-up and optional one-time support --
    support_enabled: bool = False     # absent block preserves membership pages
    support_payment_link: str = ""
    support_monthly_link: str = ""    # recurring Payment Link ($1 x quantity)
    support_panel_delay_s: int = 0
    support_cost_phrase: str = "hundreds of dollars"
    join_prompt: bool = True
    join_prompt_delay_s: int = 15
    join_apple: bool = False          # reserved until Apple sign-in is supported
    # Loaded configs enable engagement; an empty programmatic config preserves
    # the legacy template (including the dormant membership snapshots).
    engagement: dict[str, bool] = field(default_factory=dict)
    engagement_saved_deals_limit: int = 100  # includes browser-only price watches
    # -- home-screen app: absent block preserves the existing site bytes --
    pwa_enabled: bool = False
    pwa_install_button: bool = False
    pwa_install_phone_delay_s: int = 180
    pwa_name: str = "wowpriced"
    pwa_short_name: str = "wowpriced"
    pwa_android: dict = field(default_factory=lambda: validated_android({}))
    # -- newsletter: the free sign-up list's best deals (Cloudflare Worker + KV) --
    newsletter_enabled: bool = False
    newsletter_cadence: str = "weekly"
    newsletter_weekday: str = "tue"
    newsletter_mailing_address: str = ""
    newsletter_exclude_stores: list[str] = field(default_factory=lambda: ["amazon", "ebay"])
    newsletter_confirmations_enabled: bool = False
    newsletter_worker_url: str = ""    # https://wowpriced-subscribe.<sub>.workers.dev
    newsletter_hour_local: int = 8
    newsletter_timezone: str = "America/New_York"
    newsletter_max_per_run: int = 200  # messages per run; the rest ride the next run
    newsletter_top_n: int = 10         # deals per email, picked once a day by the parse model

    # -- incoming partner newsletters (independent of customer email sends) --
    newsletter_inbox_url: str = ""
    newsletter_inbox_max_messages: int = 50
    newsletter_inbox_max_response_bytes: int = 2097152
    # -- tracking: public analytics/ad tag IDs, validated in publish/tracking.py --
    tracking: dict[str, str] = field(default_factory=dict)
    pinterest: dict[str, Any] = field(default_factory=dict)  # social.pinterest
    meta: dict[str, Any] = field(default_factory=dict)  # social.meta
    social_profiles: dict[str, str] = field(default_factory=dict)
    social_follow_row: bool = False     # site-wide "Follow wowpriced" row + home sameAs
    social_require_earning_link: bool = True
    social_exclude_conditions: list[str] = field(
        default_factory=lambda: ["used", "open_box", "refurbished"])
    social_exclude_categories: list[str] = field(default_factory=list)
    social_require_stock_evidence: bool = False
    social_extreme_pct: float = 80.0

    amazon_used_enabled: bool = True
    keepa_focus_finder: dict[str, Any] = field(default_factory=lambda: validated_keepa_focus_finder({}))

    # -- keepa: /deal paging on the token plan (config.yaml keepa:) --
    keepa_amazon_pages_per_pull: int = 6
    keepa_popular_affordable_page: bool = False
    keepa_genuine_drops_only: bool = False
    keepa_focus_pages_per_category: int = 1
    keepa_warehouse_pages_per_pull: int = 12
    keepa_reserve_tokens: float = 300.0       # left untouched for product calls
    keepa_visitor_reserve_tokens: float = 0.0  # extra floor for every pipeline lane
    keepa_tokens_per_minute: float = 20.0
    keepa_volatility: dict[str, Any] = field(default_factory=dict)
    keepa_focus_categories: list[str] = field(default_factory=lambda: [
        "Beauty & Personal Care", "Health & Household"])
    # NEW (not yet content-hashed) posts per pull: the first-run burst bound
    keepa_amazon_max_new_posts_per_pull: int = 10
    keepa_warehouse_max_new_posts_per_pull: int = 20
    keepa_warehouse_min_drop_pct: float = 45.0  # used price vs honest new price
    keepa_warehouse_min_new_price: float = 25.0
    # Amazon price drops use the List Price when Amazon's 90-day average
    # reached this share of it (the item really sold near list).
    keepa_list_price_min_avg_ratio: float = 0.9
    # Live price re-check cadence, hours (config.yaml keepa.recheck_*):
    # deals first seen within recheck_fresh_window_h, older new-condition
    # deals, and used/open-box deals (an offers scan, ~13 tokens each).
    keepa_recheck_fresh_h: float = 3.0
    keepa_recheck_fresh_window_h: float = 24.0
    keepa_recheck_new_h: float = 3.0
    keepa_recheck_used_h: float = 6.0
    keepa_used_scan_budget_share: float = 0.35
    keepa_unique_asin_rechecks_enabled: bool = False
    # The All tab's scored pick candidates: new-condition Amazon deals
    # re-checked at least this often, hours; 0 = no accelerated cadence.
    keepa_recheck_top_h: float = 0.0
    # Fallback kill tolerance when the paid record cannot re-price a deal.
    keepa_recheck_tolerance_pct: float = 10.0
    keepa_buybox_rule: str = "off"  # off | shadow | on (publication gate only)
    keepa_click_price_rule: str = "off"  # off | shadow | on; qualified Buy Box required
    keepa_buybox_top_n: int = 0
    keepa_buybox_interval_min: float = 60.0
    keepa_recheck_surplus: bool = False
    keepa_recheck_surplus_reserve: float = 650.0
    keepa_recheck_top_live_adaptive: bool = False
    keepa_reprice_min_pct: float = 1.0
    keepa_recheck_top_n: int = 200
    keepa_recheck_wall_n: int = 300
    keepa_recheck_wall_h: float = 2.0
    keepa_recheck_interval_minutes: float = 10.0
    keepa_recheck_gap: dict[str, Any] = field(default_factory=dict)
    keepa_hidden_new_tokens_per_run: int = 150
    keepa_hidden_used_scans_per_run: int = 10
    keepa_recheck_idle_min_age_h: float = 0.5
    keepa_overflow_extra_pages_max: int = 8
    keepa_recheck_wall_max_age_h: float = 4.0
    keepa_recheck_adaptive: dict[str, Any] = field(default_factory=dict)
    keepa_pick_max_unconfirmed_h: float = 3.0
    keepa_show_max_unconfirmed_h: float = 48.0
    keepa_show_max_unconfirmed_used_h: float = 72.0
    keepa_recheck_top_live: bool = False
    keepa_sweep_enabled: bool = True          # warehouse sweep on idle tokens
    keepa_sweep_asins_per_pull: int = 100
    keepa_sweep_reserve_tokens: float = 400.0  # kept on top of reserve_tokens
    # -- woot --
    woot_max_items_per_run: int = 300
    woot_max_new_items_per_run: int = 20      # unseen posts per run
    woot_feed_pages: int = 1
    woot_named_feeds: list[str] = field(default_factory=list)
    woot_named_feeds_rotation_h: float = 0.0   # 0 = every named feed each run

    # -- official store price checks (flags and secrets required) --
    non_amazon_freshness_enabled: bool = False
    non_amazon_source_recheck_h: float = 12.0
    non_amazon_source_rechecks_per_run: int = 20
    non_amazon_source_recheck_budget_s: float = 60.0
    non_amazon_stale_label_h: float = 24.0
    non_amazon_stale_hide_h: float = 48.0
    non_amazon_stores: dict = field(default_factory=dict)
    bestbuy_tolerance_pct: float = 5.0
    bestbuy_recheck_h: float = 4.0
    bestbuy_requests_per_second: float = 5.0  # also bounded by the host gap
    bestbuy_max_requests_per_run: int = 5
    bestbuy_max_requests_per_day: int = 45000
    ebay_tolerance_pct: float = 5.0
    ebay_recheck_h: float = 4.0
    ebay_requests_per_second: float = 1.0
    ebay_max_requests_per_run: int = 25       # includes OAuth and retries
    ebay_max_requests_per_day: int = 4500

    # -- retention / flags --
    posts_seen_days: int = 30
    post_parses_days: int = 2
    deals_days: int = 14
    offsite_unseen_days: int | None = None
    llm_calls_days: int = 35
    run_stats_days: int = 60
    http_cache_days: int = 30
    vacuum_free_pct: float = 10
    incremental_cache_enabled: bool = False
    incremental_cache_max_examined_per_run: int = 256
    incremental_cache_budget_s: float = 0.25
    outgoing_days: int = 60
    indexnow_batch_versions_days: int | None = None
    appeal_scores_days: int | None = None
    accuracy_samples_days: int | None = None
    deal_click_shown_days: int | None = None
    source_audit_posts_days: int | None = None
    keepa_recheck_history_samples: int | None = None
    compact_deal_payloads: bool = False
    compact_accuracy_samples: bool = False
    compact_click_exposure: bool = False
    state_retention_337: bool = False
    api_cache_days: int = 1
    post_receipt_details_days: int = 7
    audit_detail_days: int = 14
    audit_hot_days: int = 1
    click_raw_days: int = 30
    accuracy_detail_days: int = 14
    accuracy_last_per_deal: int = 24
    terminal_indexnow_versions_days: int = 1
    state_maintenance_max_rows: int = 4096
    state_maintenance_budget_s: float = 1.0
    state_maintenance_after_deadline: bool = False
    state_warn_enc_mb: float | None = None
    state_max_enc_mb: float | None = None
    feature_flags: dict[str, bool] = field(default_factory=dict)
    phone_budget: dict = field(default_factory=lambda: phone_budget_defaults())

    def resolved_user_agent(self) -> str:
        return self.user_agent.replace("{contact_email}", self.contact_email)


def validated_search(value) -> dict:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("search must be a mapping")
    result = {"amazon_results": "off", "cache_hours": 6,
              "tokens_per_day": 3000, "max_per_minute": 20}
    result.update(value)
    # YAML 1.1 reads unquoted on/off as booleans.
    mode = result["amazon_results"]
    if type(mode) is bool:
        mode = "on" if mode else "off"
    if mode not in ("off", "preview", "on"):
        raise ValueError("search.amazon_results must be off, preview or on")
    result["amazon_results"] = mode
    for key in ("cache_hours", "tokens_per_day", "max_per_minute"):
        if type(result[key]) is not int or result[key] < 0:
            raise ValueError(f"search.{key} must be a non-negative integer")
    if result["cache_hours"] > 24:
        raise ValueError("search.cache_hours must be at most 24")
    return {key: result[key] for key in ("amazon_results", "cache_hours", "tokens_per_day", "max_per_minute")}


def validated_review_checker(value) -> dict:
    defaults = {"enabled": False, "min_reviews": 100, "min_rating": 4.3,
                "steady_rating_delta": 0.2, "pace_min_reviews": 50,
                "pace_max_fraction": 0.25, "popular_monthly_sold": 100,
                "grade_a_checks": 4, "grade_b_checks": 2}
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise ValueError("review_checker must contain only documented settings")
    result = defaults | value
    if type(result["enabled"]) is not bool:
        raise ValueError("review_checker.enabled must be a boolean")
    for key, low, high in (("min_reviews", 1, 1000000000), ("pace_min_reviews", 1, 1000000000),
                           ("popular_monthly_sold", 1, 1000000000),
                           ("grade_a_checks", 1, 5), ("grade_b_checks", 1, 5)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f"review_checker.{key} must be an integer in {low}..{high}")
    for key, low, high in (("min_rating", 1, 5), ("steady_rating_delta", 0, 4),
                           ("pace_max_fraction", 0, 1)):
        if type(result[key]) not in (int, float) or not low <= result[key] <= high:
            raise ValueError(f"review_checker.{key} must be a finite number in {low}..{high}")
    if result["grade_b_checks"] > result["grade_a_checks"]:
        raise ValueError("review_checker.grade_b_checks must not exceed grade_a_checks")
    return result


def validated_search_more(value) -> dict:
    if value is None or value is False:
        value = {}
    defaults = {"enabled": False, "pages": 3, "min_query_chars": 2}
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise ValueError("search_more must contain enabled, pages and min_query_chars only")
    result = defaults | value
    if type(result["enabled"]) is not bool:
        raise ValueError("search_more.enabled must be a boolean")
    for key, low, high in (("pages", 1, 10), ("min_query_chars", 2, 60)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f"search_more.{key} must be an integer in {low}..{high}")
    return result


def validated_search_analyze(value) -> dict:
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {"enabled", "lazy"}:
        raise ValueError("search_analyze must contain enabled and lazy only")
    result = {"enabled": False, "lazy": False} | value
    for key in result:
        if type(result[key]) is not bool:
            raise ValueError(f"search_analyze.{key} must be a boolean")
    return result


def validated_search_boost(value) -> dict:
    """Operator 2026-10-17: big Amazon button, adult cards hidden, phone retry."""
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {"enabled", "exclude_words"}:
        raise ValueError("search_boost must contain enabled and exclude_words only")
    enabled, words = value.get("enabled", False), value.get("exclude_words", [])
    if type(enabled) is not bool:
        raise ValueError("search_boost.enabled must be a boolean")
    if not isinstance(words, list) or not all(isinstance(w, str) and re.fullmatch(r"[a-z0-9]+(?:[ -][a-z0-9]+)*", w.strip().lower())
                                              for w in words):
        raise ValueError("search_boost.exclude_words must be a list of plain words")
    return {"enabled": enabled, "exclude_words": [w.strip().lower() for w in words]}


def validated_manifest_refresh(value) -> dict:
    defaults = {"enabled": False, "force_full_after_s": 300, "max_bytes": 1024,
                "max_deals_bytes": 33554432, "max_future_s": 300}
    if value is None:
        return defaults
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise ValueError("publish.manifest_refresh must contain supported settings only")
    settings = defaults | value
    if type(settings["enabled"]) is not bool:
        raise ValueError("publish.manifest_refresh.enabled must be boolean")
    for name, low, high in (("force_full_after_s", 30, 900), ("max_bytes", 512, 16384),
                            ("max_deals_bytes", 1024, 67108864), ("max_future_s", 0, 900)):
        if type(settings[name]) is not int or not low <= settings[name] <= high:
            raise ValueError(f"publish.manifest_refresh.{name} must be an integer in {low}..{high}")
    return settings


def validated_drop_clock(value, drop_minutes: int) -> dict:
    defaults = {"warm_s": 300, "hot_s": 120, "final_s": 30}
    if not isinstance(value, dict):
        return defaults
    clock = {name: value.get(name, default) for name, default in defaults.items()}
    if (all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            for v in clock.values())
            and 0 < clock["final_s"] < clock["hot_s"] < clock["warm_s"] < drop_minutes * 60):
        return clock
    return defaults


def validate_incremental_cache_limits(max_examined_per_run, budget_s) -> None:
    if (type(max_examined_per_run) is not int
            or not 1 <= max_examined_per_run <= 4096):
        raise ValueError("retention.incremental_cache.max_examined_per_run must be an integer in 1..4096")
    if (isinstance(budget_s, bool) or not isinstance(budget_s, (int, float))
            or not 0.05 <= budget_s <= 5 or not math.isfinite(budget_s)):
        raise ValueError("retention.incremental_cache.budget_s must be finite and in 0.05..5")


PHONE_BYTE_METRICS = ("home_html", "home_js", "home_css", "deals_json")


def phone_budget_defaults() -> dict:
    # ceil(1.05 * the largest fixed task-206 fixture), not measured live vitals.
    return {"enabled": False, "lab_lcp_max_ms": 2500, "lab_cls_max": 0.1,
            "home_html_gzip_max_bytes": 77360, "home_js_gzip_max_bytes": 53767,
            "home_css_gzip_max_bytes": 14913, "deals_json_gzip_max_bytes": 103976}


def validated_phone_budget(value) -> dict:
    defaults = phone_budget_defaults()
    if not isinstance(value, dict) or value.keys() - defaults.keys():
        raise ValueError("quality.phone_budget must be a mapping with known fields")
    limits = defaults | value
    if type(limits["enabled"]) is not bool:
        raise ValueError("quality.phone_budget.enabled must be a boolean")
    for key in defaults.keys() - {"enabled"}:
        number = limits[key]
        try:
            valid = (type(number) in (int, float) and math.isfinite(number) and number > 0)
        except OverflowError:
            valid = False
        if key.endswith("_bytes"):
            valid = valid and type(number) is int
        if not valid:
            kind = "a positive integer" if key.endswith("_bytes") else "finite and positive"
            raise ValueError(f"quality.phone_budget.{key} must be {kind}")
    return limits


def validated_bounded_prefilter(value: Any) -> dict:
    """Finite heuristic work only; these limits never truncate model evidence."""
    defaults = {"enabled": False, "max_chars": 12000, "max_numeric_chars": 32,
                "max_numeric_tokens": 128, "max_price_pairs": 256}
    if not isinstance(value, dict) or value.keys() - defaults.keys():
        raise ValueError("parse.bounded_prefilter must be a mapping of supported limits")
    limits = {**defaults, **value}
    if type(limits["enabled"]) is not bool:
        raise ValueError("parse.bounded_prefilter.enabled must be boolean")
    for name, ceiling in (("max_chars", 100000), ("max_numeric_chars", 128),
                          ("max_numeric_tokens", 4096), ("max_price_pairs", 65536)):
        if type(limits[name]) is not int or not 1 <= limits[name] <= ceiling:
            raise ValueError(f"parse.bounded_prefilter.{name} must be an integer in 1..{ceiling}")
    return limits


def validated_keyword_volumes(value) -> dict:
    if not isinstance(value, dict) or set(value) - {"keywords", "geo_target", "language"}:
        raise ValueError("growth.keyword_volumes must contain supported settings only")
    settings = {"geo_target": 2840, "language": 1000, **value}
    keywords = settings.get("keywords")
    if (not isinstance(keywords, list) or not 1 <= len(keywords) <= 100
            or any(not isinstance(k, str) or k != k.strip()
                   or not re.fullmatch(r"[a-z0-9 '-]{1,80}", k) for k in keywords)
            or len(set(keywords)) != len(keywords)):
        raise ValueError("growth.keyword_volumes.keywords must be 1..100 unique, stripped lowercase phrases (max 80 chars)")
    for name in ("geo_target", "language"):
        if type(settings[name]) is not int or settings[name] <= 0:
            raise ValueError(f"growth.keyword_volumes.{name} must be a positive integer")
    return settings


def validated_deal_spotter(value) -> dict:
    defaults = dict(enabled=False, a_max_avg_ratio=.85, b_max_avg_ratio=.97,
                    b_min_list_pct=20, verified_max_ratio=1.02, single_source_ok=False)
    if not isinstance(value, dict) or set(value) - defaults.keys():
        raise ValueError("deal_spotter must contain only supported settings")
    settings = {**defaults, **value}
    for key in ("enabled", "single_source_ok"):
        if type(settings[key]) is not bool:
            raise ValueError(f"deal_spotter.{key} must be a boolean")
    for key in defaults.keys() - {"enabled", "single_source_ok"}:
        if type(settings[key]) not in (int, float) or not math.isfinite(settings[key]):
            raise ValueError(f"deal_spotter.{key} must be finite")
    if not (0 < settings["a_max_avg_ratio"] <= settings["b_max_avg_ratio"] <= 1
            and 0 <= settings["b_min_list_pct"] <= 100
            and 1 <= settings["verified_max_ratio"] <= 1.1):
        raise ValueError("deal_spotter thresholds are out of range")
    return settings


def validated_tool_pages(value) -> dict:
    if not isinstance(value, dict) or set(value) - {"enabled", "public"}:
        raise ValueError("tool_pages must contain enabled and public only")
    settings = {"enabled": False, "public": False, **value}
    for key, flag in settings.items():
        if type(flag) is not bool:
            raise ValueError(f"tool_pages.{key} must be a boolean")
    if settings["public"] and not settings["enabled"]:
        raise ValueError("tool_pages.public requires tool_pages.enabled")
    return settings


def load_config(path: str | Path | None = None) -> AppConfig:
    path = Path(path) if path else ROOT / "config.yaml"
    raw = yaml.safe_load(path.read_text())
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("config must be a mapping")

    def g(*keys: str, default: Any = None) -> Any:
        cur: Any = raw
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    cfg = AppConfig(raw=raw)
    cfg.tool_pages = validated_tool_pages(raw.get("tool_pages", {}))
    cfg.deal_spotter = validated_deal_spotter(raw.get("deal_spotter", {}))
    cfg.review_checker = validated_review_checker(raw.get("review_checker", {}))
    from .appeal_score import validate as validate_appeal_score
    cfg.appeal_score = validate_appeal_score(raw.get("appeal_score", {}))
    cfg.compare = validated_compare(raw.get("compare", {}))
    cfg.extension = validated_extension(raw.get("extension", {}))
    cfg.checker = validated_checker(raw.get("checker", {}), raw.get("amazon_history", "show"))
    product_settings = raw.get("product_pages", {})
    if not isinstance(product_settings, dict) or set(product_settings) - {"ai_take"}:
        raise ValueError("product_pages must contain ai_take settings only")
    cfg.ai_take = validated_ai_take(product_settings.get("ai_take", {}))
    quality = raw.get("quality", {})
    if not isinstance(quality, dict):
        raise ValueError("quality must be a mapping")
    cfg.phone_budget = validated_phone_budget(quality.get("phone_budget", {}))
    cfg.accuracy_extra_samples_per_day = max(0, int(
        (raw.get("accuracy") or {}).get("extra_samples_per_day", 0)))
    cfg.contact_email = g("contact_email", default=cfg.contact_email)
    cfg.user_agent = g("user_agent", default=cfg.user_agent)
    cfg.timezone = g("timezone", default=cfg.timezone)

    cfg.parallel_sources = g("fetch", "parallel_sources", default=cfg.parallel_sources)
    if type(cfg.parallel_sources) is not int or not 1 <= cfg.parallel_sources <= 6:
        raise ValueError("fetch.parallel_sources must be an integer from 1 to 6")
    cfg.request_timeout_s = float(g("fetch", "request_timeout_s", default=cfg.request_timeout_s))
    cfg.per_source_budget_s = float(g("fetch", "per_source_budget_s", default=cfg.per_source_budget_s))
    cfg.run_budget_s = float(g("fetch", "run_budget_s", default=cfg.run_budget_s))
    cfg.min_host_interval_s = float(g("fetch", "min_host_interval_s", default=cfg.min_host_interval_s))
    cfg.max_retries = int(g("fetch", "max_retries", default=cfg.max_retries))
    cfg.circuit_breaker_failures = int(g("fetch", "circuit_breaker_failures", default=cfg.circuit_breaker_failures))
    cfg.circuit_breaker_cooldown_h = float(g("fetch", "circuit_breaker_cooldown_h", default=cfg.circuit_breaker_cooldown_h))
    cfg.redirect_max_hops = int(g("fetch", "redirect_max_hops", default=cfg.redirect_max_hops))

    cfg.parse_model = g("parse", "model", default="") or ""
    cfg.max_posts_per_run = int(g("parse", "max_posts_per_run", default=cfg.max_posts_per_run))
    cfg.max_chars_per_post = int(g("parse", "max_chars_per_post", default=cfg.max_chars_per_post))
    cfg.spend_cap_usd_per_run = float(g("parse", "spend_cap_usd_per_run", default=cfg.spend_cap_usd_per_run))
    cfg.spend_cap_usd_per_day = float(g("parse", "spend_cap_usd_per_day", default=cfg.spend_cap_usd_per_day))
    cfg.spend_cap_boost_usd_per_day = float(g("parse", "spend_cap_boost_usd_per_day",
                                              default=cfg.spend_cap_boost_usd_per_day) or 0.0)
    cfg.spend_cap_boost_until = str(g("parse", "spend_cap_boost_until",
                                      default=cfg.spend_cap_boost_until) or "")
    cfg.spend_pacing = bool(g("parse", "spend_pacing", default=cfg.spend_pacing))
    cfg.spend_pacing_headroom_hours = max(0.0, float(g(
        "parse", "spend_pacing_headroom_hours", default=cfg.spend_pacing_headroom_hours)))
    cfg.retries_on_invalid_json = int(g("parse", "retries_on_invalid_json", default=cfg.retries_on_invalid_json))
    cfg.parse_structured_output = bool(g("parse", "structured_output", default=cfg.parse_structured_output))
    cfg.parse_concurrency = max(1, int(g("parse", "concurrency", default=cfg.parse_concurrency)))
    cfg.fast_lane_parse_budget_s = float(g(
        "parse", "fast_lane_parse_budget_s", default=cfg.fast_lane_parse_budget_s))
    if not 0 < cfg.fast_lane_parse_budget_s < float("inf"):
        raise ValueError("parse.fast_lane_parse_budget_s must be positive and finite")
    prefilter = g("parse", "prefilter_below_floor", default=cfg.prefilter_below_floor)
    # YAML 1.1 reads unquoted on/off as booleans.
    if isinstance(prefilter, bool):
        prefilter = "on" if prefilter else "off"
    cfg.prefilter_below_floor = str(prefilter)
    if cfg.prefilter_below_floor not in {"off", "shadow", "on"}:
        raise ValueError("parse.prefilter_below_floor must be off, shadow, or on")
    cfg.bounded_prefilter = validated_bounded_prefilter(g("parse", "bounded_prefilter", default={}))
    for name in ("complete_days", "min_candidates", "min_source_sites", "max_gap_minutes"):
        field = "prefilter_" + name
        value = int(g("parse", "prefilter_readiness", name, default=getattr(cfg, field)))
        if value <= 0:
            raise ValueError("parse.prefilter_readiness values must be positive")
        setattr(cfg, field, value)
    cfg.second_opinion_model = g("parse", "second_opinion_model", default="") or ""
    cfg.max_second_opinions_per_run = int(g("parse", "max_second_opinions_per_run", default=cfg.max_second_opinions_per_run))
    cfg.critic_model = g("parse", "critic_model", default="") or ""
    cfg.critic_enabled = bool(g("parse", "critic_enabled", default=cfg.critic_enabled))
    cfg.critic_batch_size = max(1, int(g("parse", "critic_batch_size", default=8)))
    cfg.critic_reserve_share = min(0.9, max(0.0, float(g(
        "parse", "critic_reserve_share", default=cfg.critic_reserve_share))))
    cfg.recategorize_other_per_run = max(0, int(g(
        "parse", "recategorize_other_per_run", default=cfg.recategorize_other_per_run)))
    cfg.critic_shadow_model = g("parse", "critic_shadow_model", default=cfg.critic_shadow_model) or ""
    cfg.critic_shadow_batches_per_day = max(0, int(g(
        "parse", "critic_shadow_batches_per_day", default=cfg.critic_shadow_batches_per_day)))
    cfg.critic_tiering.update(g("parse", "critic_tiering", default={}) or {})
    tier = cfg.critic_tiering
    if not isinstance(tier["enabled"], bool) or not isinstance(tier["model"], str) or not tier["model"]:
        raise ValueError("critic_tiering needs a boolean enabled and a model")
    if tier["enabled"] and tier["model"] in {cfg.critic_model, cfg.second_opinion_model}:
        raise ValueError("critic_tiering.model must differ from the Sonnet models")
    for name in ("min_confidence", "max_disagreement", "second_opinion_yield_floor"):
        tier[name] = float(tier[name])
        if not 0 <= tier[name] <= 1:
            raise ValueError(f"critic_tiering.{name} must be between 0 and 1")
    tier["high_price_usd"] = float(tier["high_price_usd"])
    if not 0 < tier["high_price_usd"] < float("inf"):
        raise ValueError("critic_tiering.high_price_usd must be positive and finite")
    for name in ("max_tokens_per_deal", "shadow_batches_per_day", "window_deals", "min_judged_deals",
                 "second_opinion_calls_per_day", "second_opinion_min_calls", "second_opinion_window_days"):
        tier[name] = int(tier[name])
        if tier[name] < (0 if name.endswith("per_day") else 1):
            raise ValueError(f"invalid critic_tiering.{name}")
    if tier["min_judged_deals"] > tier["window_deals"]:
        raise ValueError("critic_tiering.min_judged_deals exceeds window_deals")
    cfg.exclude_suspect_msrp = bool(g("publish", "exclude_suspect_msrp", default=cfg.exclude_suspect_msrp))
    cfg.audit_mode = str(g("audit", "mode", default=cfg.audit_mode))
    if cfg.audit_mode not in {"shadow", "enforce"}:
        raise ValueError("audit.mode must be shadow or enforce")
    for name in ("near_price_pct", "history_min_near", "history_min_span_h",
                 "extreme_pct", "round_price_multiple", "reference_max_excess_pct"):
        field_name = "audit_" + name
        value = float(g("audit", name, default=getattr(cfg, field_name)))
        if not math.isfinite(value) or value <= 0 or (name.endswith("pct") and value >= 100):
            raise ValueError(f"audit.{name} must be positive and percentages below 100")
        if name == "history_min_near":
            if not value.is_integer():
                raise ValueError("audit.history_min_near must be an integer")
            value = int(value)
        setattr(cfg, field_name, value)
    cfg.model_pricing_per_mtok = g("parse", "model_pricing_per_mtok", default={}) or {}

    mode = g("intake", "mode", default=cfg.intake_mode)
    if isinstance(mode, bool):  # YAML 1.1 on/off
        mode = "on" if mode else "off"
    cfg.intake_mode = str(mode)
    if cfg.intake_mode not in {"off", "shadow", "on"}:
        raise ValueError("intake.mode must be off, shadow, or on")
    cfg.intake_window_days = int(g("intake", "window_days", default=cfg.intake_window_days))
    cfg.intake_min_posts = int(g("intake", "min_posts", default=cfg.intake_min_posts))
    for name in ("min_history_hours", "floor_share", "exploration_share",
                 "max_source_share", "max_store_share", "max_wait_hours"):
        setattr(cfg, "intake_" + name, float(g("intake", name, default=getattr(cfg, "intake_" + name))))
    if (not 1 <= cfg.intake_window_days <= 30 or cfg.intake_min_posts < 1
            or not 0 < cfg.intake_min_history_hours <= cfg.intake_window_days * 24
            or not math.isfinite(cfg.intake_max_wait_hours) or cfg.intake_max_wait_hours <= 0
            or any(not math.isfinite(getattr(cfg, "intake_" + name)) or
                   not 0 < getattr(cfg, "intake_" + name) <= 1
                   for name in ("floor_share", "exploration_share", "max_source_share", "max_store_share"))
            or cfg.intake_floor_share + cfg.intake_exploration_share > 1
            or cfg.intake_floor_share > cfg.intake_max_store_share):
        raise ValueError("invalid intake window, sample, wait, or share bounds")

    cfg.min_pct = float(g("publish", "min_pct", default=cfg.min_pct))
    cfg.used_min_pct = float(g("publish", "used_min_pct", default=cfg.used_min_pct))
    cfg.min_abs_savings = float(g("publish", "min_abs_savings", default=cfg.min_abs_savings))
    cfg.site_show_from_pct = float(g("publish", "site_show_from_pct",
                                     default=cfg.min_pct))
    cfg.show_dead_hours = float(g("publish", "show_dead_hours",
                                  default=cfg.show_dead_hours))
    cfg.drop_minutes = max(1, int(g("publish", "drop_minutes",
                                    default=cfg.drop_minutes)))
    cfg.drop_clock = validated_drop_clock(g("publish", "drop_clock"), cfg.drop_minutes)
    cfg.manifest_refresh = validated_manifest_refresh(g("publish", "manifest_refresh"))
    cfg.site_dir = g("publish", "site_dir", default=cfg.site_dir)
    cfg.photoless_last = bool(g("publish", "photoless_last", default=False))
    cfg.hide_deal_site_links = g("publish", "hide_deal_site_links", default=False)
    if type(cfg.hide_deal_site_links) is not bool:
        raise ValueError("publish.hide_deal_site_links must be a boolean")
    photos = g("publish", "photos", default={})
    if not isinstance(photos, dict):
        raise ValueError("publish.photos must be a mapping")
    if "max_photoless_h" in photos:
        value = photos["max_photoless_h"]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 1:
            raise ValueError("publish.photos.max_photoless_h must be a finite number >= 1")
        cfg.max_photoless_h = float(value)
    reuse = g("publish", "reuse_link_facts", default={})
    if not isinstance(reuse, dict):
        raise ValueError("publish.reuse_link_facts must be a mapping")
    cfg.reuse_link_facts_enabled = reuse.get("enabled", False)
    if type(cfg.reuse_link_facts_enabled) is not bool:
        raise ValueError("publish.reuse_link_facts.enabled must be a boolean")
    for name in ("asin_backfill_per_run", "short_links_per_run", "keepa_search_per_run", "keepa_search_per_day"):
        setattr(cfg, "images_" + name, max(0, int(g("images", name, default=getattr(cfg, "images_" + name)))))
    cfg.images_post_ingest_keepa = g("images", "post_ingest_keepa", default=False)
    if type(cfg.images_post_ingest_keepa) is not bool:
        raise ValueError("images.post_ingest_keepa must be a boolean")
    cfg.images_keepa_match_min = float(g("images", "keepa_match_min", default=0.85))
    if not 0.85 <= cfg.images_keepa_match_min <= 1:
        raise ValueError("images.keepa_match_min must be between 0.85 and 1")
    cfg.amazon_as_of_enabled = bool(g("publish", "amazon_as_of", "enabled", default=False))
    cfg.amazon_history = str(g("amazon_history", default="show"))
    cfg.amazon_reference = g("publish", "amazon_reference", default="history")
    if cfg.amazon_reference not in ("history", "list_price"):
        raise ValueError("publish.amazon_reference must be history or list_price")
    if cfg.amazon_history not in {"show", "hide"}:
        raise ValueError("amazon_history must be show or hide")
    watches = g("amazon_watches", default="")
    # YAML reads bare on/off as booleans.
    cfg.amazon_watches = ("on" if watches else "off") if type(watches) is bool else str(watches)
    if cfg.amazon_watches not in {"", "on", "off"}:
        raise ValueError("amazon_watches must be on or off")
    cfg.store_policy = validated_store_policy(g("store_policy", default={}))
    cfg.offscreen_cards = g("site", "offscreen_cards", default=False)
    if type(cfg.offscreen_cards) is not bool:
        raise ValueError("site.offscreen_cards must be a boolean")
    cfg.home_link_resets = g("site", "home_link_resets", default=False)
    cfg.trending_auto_cards = g("site", "trending_auto_cards", default=None)
    if cfg.trending_auto_cards is not None and (type(cfg.trending_auto_cards) is not int
                                                or not 60 <= cfg.trending_auto_cards <= 5000):
        raise ValueError("site.trending_auto_cards must be an integer from 60 to 5000")
    cfg.click_price_home_fill = g("site", "click_price_home_fill", default=False)
    if type(cfg.click_price_home_fill) is not bool:
        raise ValueError("site.click_price_home_fill must be a boolean")
    if type(cfg.home_link_resets) is not bool:
        raise ValueError("site.home_link_resets must be a boolean")
    cfg.credit_links = g("site", "credit_links", default=True)
    if type(cfg.credit_links) is not bool:
        raise ValueError("site.credit_links must be a boolean")
    cfg.brand_pages = g("site", "brand_pages", default=False)
    if type(cfg.brand_pages) is not bool:
        raise ValueError("site.brand_pages must be a boolean")
    cfg.dark_polish = g("site", "dark_polish", default=False)
    if type(cfg.dark_polish) is not bool:
        raise ValueError("site.dark_polish must be a boolean")
    cfg.strip_comments = g("site", "strip_comments", default=False)
    if type(cfg.strip_comments) is not bool:
        raise ValueError("site.strip_comments must be a boolean")
    for key in ("home_title", "home_description"):
        value = g("site", key, default="") or ""
        if not isinstance(value, str) or "\n" in value or len(value) > 200:
            raise ValueError(f"site.{key} must be one line of text")
        setattr(cfg, key, value.strip())
    cfg.search_amazon = g("site", "search_amazon", default=False)
    if type(cfg.search_amazon) is not bool:
        raise ValueError("site.search_amazon must be a boolean")
    cfg.search = validated_search(g("search", default=None))
    cfg.search_boost = validated_search_boost(g("search_boost", default=None))
    cfg.search_box_glow = g("search_box_glow", default=False)
    if type(cfg.search_box_glow) is not bool:
        raise ValueError("search_box_glow must be a boolean")
    cfg.same_product_spacing = g("same_product_spacing", default=False)
    if type(cfg.same_product_spacing) is not bool:
        raise ValueError("same_product_spacing must be a boolean")
    cfg.card_tidy = g("card_tidy", default=False)
    if type(cfg.card_tidy) is not bool:
        raise ValueError("card_tidy must be a boolean")
    cfg.page_polish = g("page_polish", default=False)
    if type(cfg.page_polish) is not bool:
        raise ValueError("page_polish must be a boolean")
    cfg.header_polish = g("header_polish", default=False)
    if type(cfg.header_polish) is not bool:
        raise ValueError("header_polish must be a boolean")
    cfg.all_discounts_button = g("all_discounts_button", default=False)
    if type(cfg.all_discounts_button) is not bool:
        raise ValueError("all_discounts_button must be a boolean")
    cfg.discounts_from_link = g("discounts_from_link", default=False)
    if type(cfg.discounts_from_link) is not bool:
        raise ValueError("discounts_from_link must be a boolean")
    cfg.search_polish_1019 = g("search_polish_1019", default=False)
    if type(cfg.search_polish_1019) is not bool:
        raise ValueError("search_polish_1019 must be a boolean")
    cfg.search_buttons_1019 = g("search_buttons_1019", default=False)
    if type(cfg.search_buttons_1019) is not bool:
        raise ValueError("search_buttons_1019 must be a boolean")
    cfg.search_discounts_button = g("search_discounts_button", default=False)
    if type(cfg.search_discounts_button) is not bool:
        raise ValueError("search_discounts_button must be a boolean")
    cfg.card_click_1020 = g("card_click_1020", default=False)
    if type(cfg.card_click_1020) is not bool:
        raise ValueError("card_click_1020 must be a boolean")
    cfg.buttons_to_tools = g("buttons_to_tools", default=False)
    if type(cfg.buttons_to_tools) is not bool:
        raise ValueError("buttons_to_tools must be a boolean")
    cfg.tool_faq_wording = g("tool_faq_wording", default=False)
    if type(cfg.tool_faq_wording) is not bool:
        raise ValueError("tool_faq_wording must be a boolean")
    cfg.checker_similar_heading = g("checker_similar_heading", default=False)
    if type(cfg.checker_similar_heading) is not bool:
        raise ValueError("checker_similar_heading must be a boolean")
    for name in ("checker_similar_typed_words", "price_age_minutes"):
        setattr(cfg, name, g(name, default=False))
        if type(getattr(cfg, name)) is not bool:
            raise ValueError(f"{name} must be a boolean")
    cfg.tools_new_tab = g("tools_new_tab", default=False)
    if type(cfg.tools_new_tab) is not bool:
        raise ValueError("tools_new_tab must be a boolean")
    cfg.tool_buttons_outside = g("tool_buttons_outside", default=False)
    if type(cfg.tool_buttons_outside) is not bool:
        raise ValueError("tool_buttons_outside must be a boolean")
    cfg.amazon_price_cap = g("amazon_price_cap", default=False)
    if type(cfg.amazon_price_cap) is not bool:
        raise ValueError("amazon_price_cap must be a boolean")
    cfg.amazon_price_cap_usd = g("amazon_price_cap_usd", default=50)
    if type(cfg.amazon_price_cap_usd) not in (int, float) or not 1 <= cfg.amazon_price_cap_usd <= 10000:
        raise ValueError("amazon_price_cap_usd must be a number from 1 to 10000")
    cfg.amazon_price_cap_usd = float(cfg.amazon_price_cap_usd)
    cfg.phone_tagline_count = g("phone_tagline_count", default=False)
    if type(cfg.phone_tagline_count) is not bool:
        raise ValueError("phone_tagline_count must be a boolean")
    cfg.timer_center = g("timer_center", default=False)
    if type(cfg.timer_center) is not bool:
        raise ValueError("timer_center must be a boolean")
    cfg.search_focus_ring_keyboard_only = g("search_focus_ring_keyboard_only", default=False)
    if type(cfg.search_focus_ring_keyboard_only) is not bool:
        raise ValueError("search_focus_ring_keyboard_only must be a boolean")
    cfg.hide_timer_ticker_confetti = g("hide_timer_ticker_confetti", default=False)
    if type(cfg.hide_timer_ticker_confetti) is not bool:
        raise ValueError("hide_timer_ticker_confetti must be a boolean")
    cfg.sticky_buy_bar = g("sticky_buy_bar", default=False)
    if type(cfg.sticky_buy_bar) is not bool:
        raise ValueError("sticky_buy_bar must be a boolean")
    cfg.tagline_full_row = g("tagline_full_row", default=False)
    if type(cfg.tagline_full_row) is not bool:
        raise ValueError("tagline_full_row must be a boolean")
    cfg.price_shipping_note = g("price_shipping_note", default=False)
    if type(cfg.price_shipping_note) is not bool:
        raise ValueError("price_shipping_note must be a boolean")
    cfg.timer_pill_ring = g("timer_pill_ring", default=False)
    if type(cfg.timer_pill_ring) is not bool:
        raise ValueError("timer_pill_ring must be a boolean")
    cfg.phone_timer_raise = g("phone_timer_raise", default=False)
    if type(cfg.phone_timer_raise) is not bool:
        raise ValueError("phone_timer_raise must be a boolean")
    cfg.tool_pages_hero = g("tool_pages_hero", default=False)
    if type(cfg.tool_pages_hero) is not bool:
        raise ValueError("tool_pages_hero must be a boolean")
    cfg.tool_pages_1020 = g("tool_pages_1020", default=False)
    if type(cfg.tool_pages_1020) is not bool:
        raise ValueError("tool_pages_1020 must be a boolean")
    cfg.tool_pages_1020_phone_links = g("tool_pages_1020_phone_links", default="chips")
    if type(cfg.tool_pages_1020_phone_links) is not str or cfg.tool_pages_1020_phone_links not in ("chips", "line"):
        raise ValueError('tool_pages_1020_phone_links must be "chips" or "line"')
    cfg.tool_pages_1020_home_links = g("tool_pages_1020_home_links", default=False)
    if type(cfg.tool_pages_1020_home_links) is not bool:
        raise ValueError("tool_pages_1020_home_links must be a boolean")
    cfg.fresh_more_plain = g("fresh_more_plain", default=False)
    if type(cfg.fresh_more_plain) is not bool:
        raise ValueError("fresh_more_plain must be a boolean")
    cfg.search_circle = g("search_circle", default=False)
    if type(cfg.search_circle) is not bool:
        raise ValueError("search_circle must be a boolean")
    cfg.search_analyze = validated_search_analyze(g("search_analyze", default=None))
    cfg.analyze_top_sellers = g("analyze_top_sellers", default=False)
    if type(cfg.analyze_top_sellers) is not bool:
        raise ValueError("analyze_top_sellers must be a boolean")
    cfg.search_more = validated_search_more(g("search_more", default=None))
    cfg.static_photos = g("site", "static_photos", default=False)
    if type(cfg.static_photos) is not bool:
        raise ValueError("site.static_photos must be a boolean")
    cfg.open_box_scarcity = g("site", "open_box_scarcity", default=False)
    if type(cfg.open_box_scarcity) is not bool:
        raise ValueError("site.open_box_scarcity must be a boolean")
    cfg.open_box_counts = g("site", "open_box_counts", default=False)
    if type(cfg.open_box_counts) is not bool:
        raise ValueError("site.open_box_counts must be a boolean")
    cfg.drop_rotation_windows = g("site", "drop_rotation_windows", default=3)
    if type(cfg.drop_rotation_windows) is not int or cfg.drop_rotation_windows < 0:
        raise ValueError("site.drop_rotation_windows must be a non-negative integer")
    cfg.drop_fresh = g("site", "drop_fresh", default=False)
    if type(cfg.drop_fresh) is not bool:
        raise ValueError("site.drop_fresh must be a boolean")
    cfg.drop_shuffle = g("site", "drop_shuffle", default=False)
    if type(cfg.drop_shuffle) is not bool:
        raise ValueError("site.drop_shuffle must be a boolean")
    cfg.unconfirmed_label_enabled = bool(g("site", "unconfirmed_label", "enabled", default=False))
    for condition in ("new", "used"):
        setattr(cfg, "unconfirmed_label_max_h_" + condition, max(0.0, float(g(
            "site", "unconfirmed_label", "max_h_" + condition, default=48.0))))
    cfg.front_page = g("publish", "front_page", default={}) or {}
    fresh_size = cfg.front_page.get("fresh_page_size", 60)
    if type(fresh_size) is not int or not 24 <= fresh_size <= 120:
        raise ValueError("publish.front_page.fresh_page_size must be an integer from 24 to 120")
    demote_words = cfg.front_page.get("demote_words", [])
    if not isinstance(demote_words, list) or any(not isinstance(word, str) or not word.strip()
                                              for word in demote_words):
        raise ValueError("publish.front_page.demote_words must be a list of nonempty strings")
    if type(cfg.front_page.get("used_tab_only", False)) is not bool:
        raise ValueError("publish.front_page.used_tab_only must be a boolean")
    from .appeal import validate as validate_appeal
    validate_appeal(cfg.front_page.get("appeal", {}))
    cfg.pinterest = g("social", "pinterest", default={}) or {}
    cfg.meta = g("social", "meta", default={}) or {}
    cfg.social_profiles = social_profiles(g("social", "profiles", default={}) or {})
    cfg.social_follow_row = g("social", "follow_row", default=False)
    if not isinstance(cfg.social_follow_row, bool):
        raise ValueError("social.follow_row must be true or false")
    cfg.social_require_earning_link = bool(g("social", "require_earning_link", default=True))
    cfg.social_exclude_conditions = [str(c) for c in (g("social", "exclude_conditions",
        default=cfg.social_exclude_conditions) or [])]
    cfg.social_exclude_categories = [str(c) for c in (g("social", "exclude_categories",
        default=cfg.social_exclude_categories) or [])]
    cfg.social_require_stock_evidence = bool(g("social", "require_stock_evidence",
                                               default=cfg.social_require_stock_evidence))
    cfg.social_extreme_pct = float(g("social", "extreme_pct", default=cfg.social_extreme_pct))
    cfg.custom_domain = str(g("publish", "custom_domain", default="") or "").strip().lower()
    cfg.ftc_disclosure = g("publish", "ftc_disclosure", default="")
    tag = str(g("affiliate", "amazon_tag", default="") or "").strip()
    if tag and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{1,48}", tag):
        logging.getLogger("dealradar.config").warning("affiliate.amazon_tag ignored: not a Store ID")
        tag = ""
    cfg.affiliate_amazon_tag = tag
    cfg.affiliate_competitor_tracker_hosts = [
        str(host).strip().lower().removeprefix("www.")
        for host in (g("affiliate", "competitor_tracker_hosts", default=[]) or [])
        if re.fullmatch(r"[a-z0-9.-]+", str(host).strip().lower())]
    cfg.affiliate_repair_competitor_trackers = bool(
        g("affiliate", "repair_competitor_trackers", default=False))
    cfg.affiliate_exact_variant_links = g("affiliate", "exact_variant_links", default=False)
    if type(cfg.affiliate_exact_variant_links) is not bool:
        raise ValueError("affiliate.exact_variant_links must be a boolean")
    campid = str(g("affiliate", "ebay_campaign_id", default="") or "").strip()
    if campid and not re.fullmatch(r"[0-9]{10}", campid):
        logging.getLogger("dealradar.config").warning("affiliate.ebay_campaign_id ignored: not 10 digits")
        campid = ""
    cfg.affiliate_ebay_campaign_id = campid
    cfg.affiliate_sovrn_enabled = bool(g("affiliate", "sovrn_enabled", default=False))
    # The link key lives in the GitHub secret SOVRN_API_KEY (operator's call);
    # config affiliate.sovrn_key is only a local fallback.
    key = get_secret("SOVRN_API_KEY") or str(g("affiliate", "sovrn_key", default="") or "")
    # Forgive a pasted key's stray spaces, quotes or capitals.
    key = key.strip().strip("\"'").strip().lower()
    if key and not re.fullmatch(r"[0-9a-f]{32}", key):
        logging.getLogger("dealradar.config").warning("affiliate.sovrn_key ignored: not a public link key")
        key = ""
    cfg.affiliate_sovrn_key = key
    cfg.affiliate_disclosure = str(g("affiliate", "disclosure", default="") or "").strip()
    cfg.affiliate_link_overrides = {}
    for key, link in (g("affiliate", "link_overrides", default={}) or {}).items():
        link = str(link or "").strip()
        if re.fullmatch(r"https://(sovrn\.co|redirect\.viglink\.com)/[A-Za-z0-9?=&%._/-]+", link) \
                and re.fullmatch(r"[a-z0-9.-]+/[A-Za-z0-9_-]+", str(key)):
            cfg.affiliate_link_overrides[str(key)] = link
        else:
            logging.getLogger("dealradar.config").warning(
                "affiliate.link_overrides entry ignored: not <host>/<id> -> a Sovrn link")
    cfg.seo_hub_min_items = max(1, int(g("seo", "hub_min_items", default=cfg.seo_hub_min_items)))
    cfg.seo_event_pages_enabled = g("seo", "event_pages", "enabled", default=False)
    if not isinstance(cfg.seo_event_pages_enabled, bool):
        raise ValueError("seo.event_pages.enabled must be a boolean")
    for name in ("min_items", "max_items"):
        value = g("seo", "event_pages", name, default=getattr(cfg, "seo_event_pages_" + name))
        if type(value) is not int or value < 1:
            raise ValueError("seo.event_pages." + name + " must be a positive integer")
        setattr(cfg, "seo_event_pages_" + name, value)
    if cfg.seo_event_pages_min_items > cfg.seo_event_pages_max_items:
        raise ValueError("seo.event_pages.min_items must not exceed max_items")
    events = g("seo", "event_pages", "events", default=[])
    if not isinstance(events, list):
        raise ValueError("seo.event_pages.events must be a list")
    slugs = set()
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("seo.event_pages.events entries must be mappings")
        slug = event.get("slug")
        if not isinstance(slug, str) or slug not in {"black-friday", "cyber-monday"} or slug in slugs:
            raise ValueError("seo.event_pages.events requires unique supported event slugs")
        name, keywords, window = event.get("name"), event.get("keywords"), event.get("window_days")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("seo.event_pages.events requires a name")
        try:
            stamp = date.fromisoformat(str(event.get("date")))
        except ValueError:
            raise ValueError("seo.event_pages.events requires an ISO date") from None
        if not (isinstance(window, list) and len(window) == 2
                and all(type(n) is int for n in window) and window[0] <= 0 <= window[1]):
            raise ValueError("seo.event_pages.events.window_days must bracket the event date")
        if not (isinstance(keywords, list) and keywords
                and all(isinstance(k, str) and k.strip() for k in keywords)):
            raise ValueError("seo.event_pages.events.keywords must contain nonempty strings")
        cfg.seo_event_pages_events.append(dict(slug=slug, name=name.strip(), date=stamp.isoformat(),
            window_days=window, keywords=[k.strip().casefold() for k in keywords]))
        slugs.add(slug)
    intro = g("seo", "event_pages", "intro", default={})
    if not isinstance(intro, dict) or any(k not in slugs or not isinstance(v, str) for k, v in intro.items()):
        raise ValueError("seo.event_pages.intro must map event slugs to text")
    cfg.seo_event_pages_intro = intro
    cfg.seo_home_min_items = max(1, int(g("seo", "home_min_items", default=cfg.seo_home_min_items)))
    cfg.seo_best_today_min_items = max(1, int(g("seo", "best_today_min_items", default=cfg.seo_best_today_min_items)))
    cfg.seo_deal_page_min_items = max(1, int(g("seo", "deal_page_min_items", default=cfg.seo_deal_page_min_items)))
    cfg.seo_deal_pages_max = max(0, int(g("seo", "deal_pages_max", default=cfg.seo_deal_pages_max)))
    cfg.seo_deal_pages_indexable = bool(g("seo", "deal_pages_indexable", default=cfg.seo_deal_pages_indexable))
    redirects = g("seo", "posted_deal_redirects", default={})
    if not isinstance(redirects, dict):
        raise ValueError("seo.posted_deal_redirects must be a mapping")
    cfg.seo_posted_deal_redirects_enabled = redirects.get("enabled", True)
    if type(cfg.seo_posted_deal_redirects_enabled) is not bool:
        raise ValueError("seo.posted_deal_redirects.enabled must be boolean")
    cfg.seo_posted_deal_redirects_max_rules = redirects.get("max_rules", 1500)
    if (type(cfg.seo_posted_deal_redirects_max_rules) is not int
            or not 1 <= cfg.seo_posted_deal_redirects_max_rules <= 1900):
        raise ValueError("seo.posted_deal_redirects.max_rules must be an integer in 1..1900")
    cfg.seo_product_pages_enabled = bool(g("seo", "product_pages", "enabled", default=False))
    own_checks = g("seo", "product_pages", "record_own_checks", default={})
    if not isinstance(own_checks, dict):
        raise ValueError("seo.product_pages.record_own_checks must be a mapping")
    cfg.seo_product_pages_record_own_checks_enabled = own_checks.get("enabled", True)
    if type(cfg.seo_product_pages_record_own_checks_enabled) is not bool:
        raise ValueError("seo.product_pages.record_own_checks.enabled must be boolean")
    cfg.seo_product_pages_record_own_checks_max_per_offer_per_day = own_checks.get("max_per_offer_per_day", 24)
    if (type(cfg.seo_product_pages_record_own_checks_max_per_offer_per_day) is not int
            or not 1 <= cfg.seo_product_pages_record_own_checks_max_per_offer_per_day <= 96):
        raise ValueError("seo.product_pages.record_own_checks.max_per_offer_per_day must be an integer in 1..96")
    for name in ("min_checks", "max_new_indexable_per_day"):
        attr = "seo_product_pages_" + name
        setattr(cfg, attr, max(1 if name == "min_checks" else 0, int(g(
            "seo", "product_pages", name, default=getattr(cfg, attr)))))
    for name in ("max_indexable", "max_pages", "keep_days", "prune_days"):
        attr = "seo_product_pages_" + name
        value = g("seo", "product_pages", name, default=getattr(cfg, attr))
        if type(value) is not int:
            raise ValueError(f"seo.product_pages.{name} must be an integer")
        setattr(cfg, attr, value)
    if not 1 <= cfg.seo_product_pages_max_indexable <= cfg.seo_product_pages_max_pages <= 20000:
        raise ValueError("seo.product_pages requires 1 <= max_indexable <= max_pages <= 20000")
    if not 1 <= cfg.seo_product_pages_keep_days <= cfg.seo_product_pages_prune_days <= 730:
        raise ValueError("seo.product_pages requires 1 <= keep_days <= prune_days <= 730")
    cfg.seo_product_pages_min_span_days = max(0.0, float(g(
        "seo", "product_pages", "min_span_days", default=7)))
    cfg.seo_product_pages_lower_ratio = max(0.0, min(1.0, float(g(
        "seo", "product_pages", "lower_ratio", default=0.95))))
    cfg.seo_product_pages_higher_ratio = max(1.0, float(g(
        "seo", "product_pages", "higher_ratio", default=1.05)))
    cfg.seo_product_pages_deal_history_limit = max(1, min(20, int(g(
        "seo", "product_pages", "deal_history_limit", default=20))))
    cfg.seo_store_page_min_deals = max(1, int(g(
        "seo", "store_page_min_deals", default=cfg.seo_store_page_min_deals)))
    cfg.seo_store_pages_max = max(0, int(g("seo", "store_pages_max", default=cfg.seo_store_pages_max)))
    cfg.seo_price_page_min_deals = max(1, int(g("seo", "price_page_min_deals", default=cfg.seo_price_page_min_deals)))
    cfg.seo_price_pages = sorted({int(n) for n in g(
        "seo", "price_pages", default=cfg.seo_price_pages) if int(n) > 0})
    cfg.seo_lastmod_retention_days = max(1, min(365, int(g(
        "seo", "lastmod_retention_days", default=cfg.seo_lastmod_retention_days))))
    cfg.traffic_feed_enabled = bool(g("traffic", "feed_enabled", default=False))
    cfg.traffic_feed_max_items = max(1, min(200, int(g("traffic", "feed_max_items", default=50))))
    cfg.traffic_feed_details = bool(g("traffic", "feed_details", default=True))
    cfg.traffic_share_enabled = bool(g("traffic", "share_enabled", default=False))
    cfg.traffic_related_hubs_enabled = bool(g("traffic", "related_hubs_enabled", default=False))
    cfg.traffic_hub_pagination_enabled = bool(g("traffic", "hub_pagination_enabled", default=False))
    cfg.traffic_product_families_enabled = bool(g("traffic", "product_families_enabled", default=False))
    cfg.traffic_product_family_min_offers = max(2, int(g("traffic", "product_family_min_offers", default=3)))
    cfg.product_entities_backfill_per_run = max(0, int(g("product_entities", "backfill_per_run", default=200)))
    cfg.product_entities_also_at_enabled = bool(g("product_entities", "also_at_enabled", default=False))
    cfg.product_entities_lowest_seen_enabled = bool(g("product_entities", "lowest_seen_enabled", default=False))
    cfg.product_entities_min_price_ratio = max(0.4, min(1.0, float(g(
        "product_entities", "min_price_ratio", default=0.4))))
    cfg.product_entities_max_price_ratio = max(1.0, min(2.5, float(g(
        "product_entities", "max_price_ratio", default=2.5))))
    cfg.product_entities_shadow_retention_days = max(3, min(30, int(g(
        "product_entities", "shadow_retention_days", default=7))))
    cfg.seo_indexnow_key = str(g("seo", "indexnow_key", default="") or "").strip()
    cfg.seo_indexnow_min_hours = max(0.0, float(g("seo", "indexnow_min_hours",
                                                  default=cfg.seo_indexnow_min_hours)))
    cfg.seo_indexnow_resubmit_days = max(0.0, float(g("seo", "indexnow_resubmit_days",
                                                      default=cfg.seo_indexnow_resubmit_days)))
    cfg.seo_indexnow_batch_size = max(1, min(10000, int(g(
        "seo", "indexnow_batch_size", default=cfg.seo_indexnow_batch_size))))
    cfg.seo_indexnow_retry_initial_minutes = max(1.0, float(g(
        "seo", "indexnow_retry_initial_minutes", default=cfg.seo_indexnow_retry_initial_minutes)))
    cfg.seo_indexnow_retry_max_minutes = max(cfg.seo_indexnow_retry_initial_minutes, float(g(
        "seo", "indexnow_retry_max_minutes", default=cfg.seo_indexnow_retry_max_minutes)))

    cfg.tier_boost = g("ranking", "tier_boost", default={}) or {}
    cfg.abs_savings_weight = float(g("ranking", "abs_savings_weight", default=cfg.abs_savings_weight))
    cfg.condition_penalty = float(g("ranking", "condition_penalty", default=cfg.condition_penalty))
    cfg.age_penalty_per_hour = float(g("ranking", "age_penalty_per_hour", default=cfg.age_penalty_per_hour))

    cfg.corroborate_window_h = float(g("verify", "corroborate_window_h", default=cfg.corroborate_window_h))
    cfg.history_min_observations = int(g("verify", "history_min_observations", default=cfg.history_min_observations))
    cfg.fuzzy_threshold = float(g("dedupe", "fuzzy_threshold", default=cfg.fuzzy_threshold))

    cfg.telegram_channels = g("telegram", "channels", default=[]) or []
    cfg.telegram_channel_sources = {str(k): str(v) for k, v in
                                    (g("telegram", "channel_sources", default={}) or {}).items()}
    cfg.telegram_messages_per_minute = int(g("telegram", "messages_per_minute", default=cfg.telegram_messages_per_minute))
    cfg.resend_price_drop_pct = float(g("telegram", "resend_price_drop_pct", default=cfg.resend_price_drop_pct))
    cfg.telegram_dry_run = bool(g("telegram", "dry_run", default=cfg.telegram_dry_run))
    cfg.telegram_channel = g("telegram", "channel", default={}) or {}

    cfg.digest_hour_local = int(g("digest", "hour_local", default=cfg.digest_hour_local))
    cfg.digest_timezone = g("digest", "timezone", default=cfg.digest_timezone)
    cfg.digest_recipient = g("digest", "recipient", default="") or ""

    cfg.min_items_ratio = float(g("alerting", "min_items_ratio", default=cfg.min_items_ratio))
    cfg.tripped_sources_warn = int(g("alerting", "tripped_sources_warn", default=cfg.tripped_sources_warn))

    def gs(key: str) -> str:
        return str(g("growth", key, default="") or "").strip()

    cfg.growth_report_days = max(1, int(g("growth", "report_days", default=cfg.growth_report_days)))
    cfg.growth_expired_click_hours = max(0.0, float(g(
        "growth", "expired_click_hours", default=cfg.growth_expired_click_hours)))
    cfg.growth_ga4_property_id = gs("ga4_property_id").removeprefix("properties/")
    cfg.growth_search_console_site = gs("search_console_site")
    cfg.growth_sitemap_url = gs("sitemap_url") or (
        f"https://{cfg.custom_domain}/sitemap.xml" if cfg.custom_domain else "")
    cfg.growth_meta_graph_version = gs("meta_graph_version") or cfg.growth_meta_graph_version
    cfg.growth_meta_ad_account_id = gs("meta_ad_account_id").removeprefix("act_")
    cfg.growth_meta_page_id = gs("meta_page_id")
    cfg.growth_instagram_user_id = gs("instagram_user_id")
    cfg.growth_google_ads_api_version = (gs("google_ads_api_version")
                                         or cfg.growth_google_ads_api_version)
    cfg.growth_google_ads_customer_id = re.sub(r"\D", "", gs("google_ads_customer_id"))
    cfg.growth_google_ads_login_customer_id = re.sub(r"\D", "", gs("google_ads_login_customer_id"))
    if "keyword_volumes" in (g("growth", default={}) or {}):
        cfg.growth_keyword_volumes = validated_keyword_volumes(g("growth", "keyword_volumes"))
    cfg.growth_monthly_price_usd = float(g("growth", "monthly_price_usd",
                                           default=cfg.growth_monthly_price_usd))

    cfg.subscribe_url = str(g("members", "subscribe_url", default="") or "").strip()
    cfg.portal_url = str(g("members", "portal_url", default="") or "").strip()
    cfg.members_email_enabled = bool(g("members", "email_enabled", default=cfg.members_email_enabled))
    cfg.members_email_every_minutes = max(10, int(g("members", "email_every_minutes",
                                                     default=cfg.members_email_every_minutes)))
    cfg.members_email_hour_local = min(23, max(0, int(g("members", "email_hour_local",
                                                         default=cfg.members_email_hour_local))))
    cfg.members_email_timezone = str(g("members", "timezone", default=cfg.members_email_timezone))
    cfg.members_max_deals_per_email = max(1, int(g("members", "max_deals_per_email",
                                                   default=cfg.members_max_deals_per_email)))

    cfg.paywall_enabled = bool(g("paywall", "enabled", default=cfg.paywall_enabled))
    cfg.paywall_open_access = bool(g("paywall", "open_access", default=cfg.paywall_open_access))
    cfg.paywall_teaser_count = max(0, int(g("paywall", "teaser_count",
                                            default=cfg.paywall_teaser_count)))
    pool = int(g("paywall", "teaser_pool", default=cfg.paywall_teaser_pool) or 0)
    cfg.paywall_teaser_pool = 0 if pool <= 0 else max(cfg.paywall_teaser_count, pool)
    cfg.paywall_trial_days = max(0, int(g("paywall", "trial_days",
                                          default=cfg.paywall_trial_days)))
    cfg.paywall_yearly_price_usd = max(0.0, float(g("paywall", "yearly_price_usd", default=0) or 0))
    cfg.paywall_api_url = str(g("paywall", "api_url", default="") or "").strip().rstrip("/")
    cfg.paywall_google_client_id = str(g("paywall", "google_client_id", default="") or "").strip()
    cfg.support_enabled = bool(g("support", "enabled", default=False))
    cfg.support_payment_link = support_link(g("support", "payment_link", default=""))
    cfg.support_monthly_link = support_link(g("support", "monthly_link", default=""))
    cfg.support_panel_delay_s = g("support", "panel_delay_s", default=0)
    if type(cfg.support_panel_delay_s) is not int or cfg.support_panel_delay_s < 0:
        raise ValueError("support.panel_delay_s must be an integer >= 0")
    cfg.support_cost_phrase = g("support", "cost_phrase", default="hundreds of dollars")
    if (not isinstance(cfg.support_cost_phrase, str) or not cfg.support_cost_phrase.strip()
            or len(cfg.support_cost_phrase) > 120
            or re.search(r"[<>\x00-\x1f\x7f-\x9f]", cfg.support_cost_phrase)):
        raise ValueError("support.cost_phrase must be nonempty plain text up to 120 characters")
    cfg.support_cost_phrase = cfg.support_cost_phrase.strip()
    cfg.join_prompt = bool(g("join", "prompt", default=True))
    cfg.join_prompt_delay_s = max(15, int(g("join", "prompt_delay_s", default=15)))
    cfg.join_apple = bool(g("join", "apple", default=False))
    cfg.engagement = {name: bool(g("engagement", name, default=True)) for name in (
        "new_since_visit", "weekly_finds", "visit_streak", "surprise_me", "hot_meter", "saved_deals")}
    cfg.engagement_saved_deals_limit = max(1, min(200, int(g(
        "engagement", "saved_deals_limit", default=cfg.engagement_saved_deals_limit))))
    cfg.pwa_enabled = bool(g("pwa", "enabled", default=cfg.pwa_enabled))
    cfg.pwa_install_button = g("pwa", "install_button", default=False)
    if type(cfg.pwa_install_button) is not bool:
        raise ValueError("pwa.install_button must be a boolean")
    cfg.pwa_install_phone_delay_s = g("pwa", "install_phone_delay_s", default=180)
    if type(cfg.pwa_install_phone_delay_s) is not int or not 0 <= cfg.pwa_install_phone_delay_s <= 3600:
        raise ValueError("pwa.install_phone_delay_s must be an integer from 0 to 3600")
    cfg.pwa_name = str(g("pwa", "name", default=cfg.pwa_name) or cfg.pwa_name).strip() or cfg.pwa_name
    cfg.pwa_short_name = str(g("pwa", "short_name", default=cfg.pwa_short_name) or cfg.pwa_short_name).strip() or cfg.pwa_short_name
    cfg.pwa_android = validated_android(g("pwa", "android", default={}))
    enabled = g("newsletter", "enabled", default=cfg.newsletter_enabled)
    if not isinstance(enabled, bool):
        raise ValueError("newsletter.enabled must be a boolean")
    cfg.newsletter_enabled = enabled
    cfg.newsletter_cadence = g("newsletter", "cadence", default=cfg.newsletter_cadence)
    if cfg.newsletter_cadence not in ("daily", "weekly"):
        raise ValueError("newsletter.cadence must be daily or weekly")
    cfg.newsletter_weekday = g("newsletter", "weekday", default=cfg.newsletter_weekday)
    if cfg.newsletter_weekday not in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
        raise ValueError("newsletter.weekday must be mon..sun")
    address = g("newsletter", "mailing_address", default="")
    if not isinstance(address, str):
        raise ValueError("newsletter.mailing_address must be text")
    cfg.newsletter_mailing_address = address.strip()
    excluded = g("newsletter", "exclude_stores", default=cfg.newsletter_exclude_stores)
    if not isinstance(excluded, list) or not all(isinstance(s, str) and s.strip() for s in excluded):
        raise ValueError("newsletter.exclude_stores must be a list of store names")
    cfg.newsletter_exclude_stores = [s.strip().casefold() for s in excluded]
    if "amazon" not in cfg.newsletter_exclude_stores:
        raise ValueError("newsletter.exclude_stores must include amazon")
    # The store keys store_of() can return (checker.HOSTS values); config stays import-free.
    known = set(CHECKER_STORES) - {"other"}
    unknown = sorted(set(cfg.newsletter_exclude_stores) - known)
    if unknown:
        raise ValueError(f"newsletter.exclude_stores has unknown store(s) {unknown}; "
                         f"use one of {sorted(known)}")
    confirmations = g("newsletter", "confirmations_enabled", default=False)
    if not isinstance(confirmations, bool):
        raise ValueError("newsletter.confirmations_enabled must be a boolean")
    cfg.newsletter_confirmations_enabled = confirmations
    cfg.newsletter_worker_url = str(g("newsletter", "worker_url", default="") or "").strip().rstrip("/")
    cfg.newsletter_hour_local = int(g("newsletter", "hour_local", default=cfg.newsletter_hour_local))
    cfg.newsletter_timezone = g("newsletter", "timezone", default=cfg.newsletter_timezone)
    cfg.newsletter_max_per_run = max(1, int(g("newsletter", "max_per_run",
                                              default=cfg.newsletter_max_per_run)))
    cfg.newsletter_top_n = max(1, int(g("newsletter", "top_n", default=cfg.newsletter_top_n)))
    cfg.newsletter_inbox_url = str(g("partner_newsletters", "inbox_url", default="") or "").strip().rstrip("/")
    cfg.newsletter_inbox_max_messages = max(1, int(g(
        "partner_newsletters", "max_messages", default=cfg.newsletter_inbox_max_messages)))
    cfg.newsletter_inbox_max_response_bytes = max(1024, int(g(
        "partner_newsletters", "max_response_bytes", default=cfg.newsletter_inbox_max_response_bytes)))
    tracking = g("tracking", default={})
    cfg.tracking = {str(k): str(v).strip() for k, v in tracking.items()
                    if v not in (None, "")} if isinstance(tracking, dict) else {}

    amazon_used = g("amazon_used", default={})
    if (not isinstance(amazon_used, dict) or set(amazon_used) - {"enabled"}
            or type(amazon_used.get("enabled", True)) is not bool):
        raise ValueError("amazon_used.enabled must be a boolean")
    cfg.amazon_used_enabled = amazon_used.get("enabled", True)
    cfg.keepa_focus_finder = validated_keepa_focus_finder(g("keepa_focus_finder", default={}))
    genuine = g("keepa", "genuine_drops_only", default=False)
    if type(genuine) is not bool:
        raise ValueError("keepa.genuine_drops_only must be a boolean")
    cfg.keepa_genuine_drops_only = genuine
    focus_pages = g("keepa", "focus_pages_per_category", default=1)
    if type(focus_pages) is not int or not 1 <= focus_pages <= 10:
        raise ValueError("keepa.focus_pages_per_category must be an integer in 1..10")
    cfg.keepa_focus_pages_per_category = focus_pages
    # Reject malformed cadence/discovery knobs instead of silently clamping them.
    limits = {"amazon_pages_per_pull": (0, 100, int),
              "amazon_max_new_posts_per_pull": (0, 1000, int),
              "buybox_interval_min": (.5, 1440, float),
              "recheck_top_h": (0, 24, float), "recheck_wall_h": (0, 24, float),
              "recheck_fresh_h": (.5, 24, float), "recheck_new_h": (.5, 24, float),
              "recheck_wall_max_age_h": (.5, 24, float), "recheck_idle_min_age_h": (.5, 24, float),
              "pick_max_unconfirmed_h": (0, 24, float), "show_max_unconfirmed_h": (0, 168, float)}
    for name, (low, high, kind) in limits.items():
        value = g("keepa", name, default=getattr(cfg, "keepa_" + name))
        if (type(value) not in ((int,) if kind is int else (int, float))
                or not math.isfinite(value) or not low <= value <= high):
            raise ValueError(f"keepa.{name} must be a finite {kind.__name__} in {low}..{high}")
    focus = g("keepa", "focus_categories", default=cfg.keepa_focus_categories)
    if (not isinstance(focus, list) or any(not isinstance(n, str) or not n.strip() for n in focus)
            or len({n.strip().casefold() for n in focus}) != len(focus)):
        raise ValueError("keepa.focus_categories must contain unique nonempty category names")
    for section in ("volatility", "recheck_adaptive"):
        options = g("keepa", section, default={})
        if not isinstance(options, dict):
            raise ValueError(f"keepa.{section} must be a mapping")
        if "enabled" in options and type(options["enabled"]) is not bool:
            raise ValueError(f"keepa.{section}.enabled must be a boolean")
        if section == "volatility":
            targets = options.get("targets", {})
            if not isinstance(targets, dict) or set(targets) - {"FAST", "NORMAL", "SLOW"}:
                raise ValueError("keepa.volatility.targets must contain supported tiers")
            for tier, values in targets.items():
                if not isinstance(values, dict) or set(values) - {"top", "wall", "rest", "hidden"}:
                    raise ValueError("keepa.volatility.targets must contain supported visibility classes")
                for value in values.values():
                    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 168:
                        raise ValueError("keepa.volatility.targets ages must be finite in (0,168]")
            limits = options.get("stretch_limits", {})
            if (not isinstance(limits, dict) or set(limits) - {"FAST", "NORMAL", "SLOW"}
                    or any(type(v) not in (int, float) or not math.isfinite(v) or v < 1 for v in limits.values())):
                raise ValueError("keepa.volatility.stretch_limits must contain finite factors >=1")
        else:
            for name in ("max_top_h", "max_wall_h", "max_rest_h"):
                if name in options:
                    value = options[name]
                    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 168:
                        raise ValueError(f"keepa.recheck_adaptive.{name} must be finite in (0,168]")

    cfg.keepa_amazon_pages_per_pull = max(1, int(g(
        "keepa", "amazon_pages_per_pull", default=cfg.keepa_amazon_pages_per_pull)))
    cfg.keepa_popular_affordable_page = bool(g(
        "keepa", "popular_affordable_page", default=cfg.keepa_popular_affordable_page))
    cfg.keepa_warehouse_pages_per_pull = max(1, int(g(
        "keepa", "warehouse_pages_per_pull", default=cfg.keepa_warehouse_pages_per_pull)))
    cfg.keepa_reserve_tokens = max(0.0, float(g(
        "keepa", "reserve_tokens", default=cfg.keepa_reserve_tokens)))
    visitor_reserve = g("keepa", "visitor_reserve_tokens", default=cfg.keepa_visitor_reserve_tokens)
    if (type(visitor_reserve) not in {int, float} or not math.isfinite(visitor_reserve)
            or visitor_reserve < 0):
        raise ValueError("keepa.visitor_reserve_tokens must be finite and nonnegative")
    cfg.keepa_visitor_reserve_tokens = float(visitor_reserve)
    cfg.keepa_focus_categories = list(g(
        "keepa", "focus_categories", default=cfg.keepa_focus_categories) or [])
    cfg.keepa_amazon_max_new_posts_per_pull = max(0, int(g(
        "keepa", "amazon_max_new_posts_per_pull",
        default=cfg.keepa_amazon_max_new_posts_per_pull)))
    cfg.keepa_warehouse_max_new_posts_per_pull = max(0, int(g(
        "keepa", "warehouse_max_new_posts_per_pull",
        default=cfg.keepa_warehouse_max_new_posts_per_pull)))
    cfg.keepa_warehouse_min_drop_pct = float(g(
        "keepa", "warehouse_min_drop_pct", default=cfg.keepa_warehouse_min_drop_pct))
    cfg.keepa_warehouse_min_new_price = float(g(
        "keepa", "warehouse_min_new_price", default=cfg.keepa_warehouse_min_new_price))
    for name in ("recheck_fresh_h", "recheck_fresh_window_h", "recheck_new_h", "recheck_used_h"):
        attr = "keepa_" + name
        setattr(cfg, attr, max(0.5, float(g("keepa", name, default=getattr(cfg, attr)))))
    cfg.keepa_list_price_min_avg_ratio = min(1.0, max(0.5, float(g(
        "keepa", "list_price_min_avg_ratio", default=cfg.keepa_list_price_min_avg_ratio))))
    cfg.keepa_recheck_top_h = max(0.0, float(g(
        "keepa", "recheck_top_h", default=cfg.keepa_recheck_top_h)))
    cfg.keepa_used_scan_budget_share = min(1.0, max(0.0, float(g(
        "keepa", "used_scan_budget_share", default=cfg.keepa_used_scan_budget_share))))
    unique_rechecks = g("keepa", "unique_asin_rechecks", default={})
    if (not isinstance(unique_rechecks, dict)
            or type(unique_rechecks.get("enabled", False)) is not bool):
        raise ValueError("keepa.unique_asin_rechecks.enabled must be a boolean")
    cfg.keepa_unique_asin_rechecks_enabled = unique_rechecks.get("enabled", False)
    cfg.keepa_recheck_tolerance_pct = max(0.0, float(g(
        "keepa", "recheck_tolerance_pct", default=cfg.keepa_recheck_tolerance_pct)))
    buybox = g("keepa", "buybox_rule", default=cfg.keepa_buybox_rule)
    cfg.keepa_buybox_rule = ("on" if buybox else "off") if isinstance(buybox, bool) else str(buybox)
    if cfg.keepa_buybox_rule not in {"off", "shadow", "on"}:
        raise ValueError("keepa.buybox_rule must be off, shadow, or on")
    click_price = g("keepa", "click_price_rule", default=cfg.keepa_click_price_rule)
    cfg.keepa_click_price_rule = ("on" if click_price else "off") if isinstance(click_price, bool) else str(click_price)
    if cfg.keepa_click_price_rule not in {"off", "shadow", "on"}:
        raise ValueError("keepa.click_price_rule must be off, shadow, or on")
    for name in ("reprice_min_pct", "recheck_wall_h", "pick_max_unconfirmed_h", "recheck_surplus_reserve",
                 "show_max_unconfirmed_h", "show_max_unconfirmed_used_h"):
        attr = "keepa_" + name
        setattr(cfg, attr, max(0.0, float(g("keepa", name, default=getattr(cfg, attr)))))
    for name in ("recheck_interval_minutes", "recheck_idle_min_age_h", "recheck_wall_max_age_h", "buybox_interval_min"):
        attr = "keepa_" + name
        setattr(cfg, attr, max(0.5, float(g("keepa", name, default=getattr(cfg, attr)))))
    for name in ("recheck_top_n", "recheck_wall_n", "overflow_extra_pages_max", "buybox_top_n",
                 "hidden_new_tokens_per_run", "hidden_used_scans_per_run"):
        attr = "keepa_" + name
        setattr(cfg, attr, max(0, int(g("keepa", name, default=getattr(cfg, attr)))))
    cfg.keepa_recheck_top_live = bool(g(
        "keepa", "recheck_top_live", default=cfg.keepa_recheck_top_live))
    for name in ("recheck_surplus", "recheck_top_live_adaptive"):
        setattr(cfg, "keepa_" + name, bool(g("keepa", name, default=False)))
    cfg.keepa_recheck_adaptive = g("keepa", "recheck_adaptive", default={}) or {}
    cfg.keepa_tokens_per_minute = max(1.0, float(g(
        "keepa", "tokens_per_minute", default=cfg.keepa_tokens_per_minute)))
    cfg.keepa_volatility = g("keepa", "volatility", default={}) or {}
    cfg.keepa_recheck_gap = g("keepa", "recheck_gap", default={}) or {}
    cfg.keepa_sweep_enabled = bool(g(
        "keepa", "sweep_enabled", default=cfg.keepa_sweep_enabled))
    cfg.keepa_sweep_asins_per_pull = max(1, int(g(
        "keepa", "sweep_asins_per_pull", default=cfg.keepa_sweep_asins_per_pull)))
    cfg.keepa_sweep_reserve_tokens = max(0.0, float(g(
        "keepa", "sweep_reserve_tokens", default=cfg.keepa_sweep_reserve_tokens)))
    cfg.woot_max_items_per_run = max(0, int(g(
        "woot", "max_items_per_run", default=cfg.woot_max_items_per_run)))
    cfg.woot_max_new_items_per_run = max(0, int(g(
        "woot", "max_new_items_per_run", default=cfg.woot_max_new_items_per_run)))
    cfg.woot_feed_pages = max(1, int(g("woot", "feed_pages", default=1)))
    feeds = g("woot", "named_feeds", default=[]) or []
    if not isinstance(feeds, list) or any(
            not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name)
            for name in feeds):
        raise ValueError("woot.named_feeds must be a list of feed names")
    cfg.woot_named_feeds = list(dict.fromkeys(feeds))
    cfg.woot_named_feeds_rotation_h = max(0.0, float(g("woot", "named_feeds_rotation_h", default=0)))

    cfg.non_amazon_freshness_enabled = bool(g("non_amazon_freshness", "enabled", default=False))
    for name in ("source_recheck_h", "source_recheck_budget_s", "stale_label_h", "stale_hide_h"):
        attr = "non_amazon_" + name
        value = float(g("non_amazon_freshness", name, default=getattr(cfg, attr)))
        if math.isfinite(value) and value > 0:
            setattr(cfg, attr, value)
    cfg.non_amazon_source_rechecks_per_run = max(0, int(g(
        "non_amazon_freshness", "source_rechecks_per_run", default=20)))
    cfg.non_amazon_stores = g("non_amazon_freshness", "stores", default={}) or {}
    for store in ("bestbuy", "ebay"):
        for name, floor in (("tolerance_pct", 0.0), ("recheck_h", 0.0),
                            ("requests_per_second", 0.01)):
            attr = store + "_" + name
            value = float(g(store, name, default=getattr(cfg, attr)))
            if math.isfinite(value):
                setattr(cfg, attr, max(floor, value))
        attr = store + "_max_requests_per_run"
        setattr(cfg, attr, max(0, int(g(store, "max_requests_per_run", default=getattr(cfg, attr)))))
        attr = store + "_max_requests_per_day"
        setattr(cfg, attr, max(0, int(g(store, "max_requests_per_day", default=getattr(cfg, attr)))))

    cfg.posts_seen_days = int(g("retention", "posts_seen_days", default=cfg.posts_seen_days))
    cfg.post_parses_days = int(g("retention", "post_parses_days", default=cfg.post_parses_days))
    cfg.deals_days = int(g("retention", "deals_days", default=cfg.deals_days))
    cfg.llm_calls_days = int(g("retention", "llm_calls_days", default=cfg.llm_calls_days))
    cfg.run_stats_days = int(g("retention", "run_stats_days", default=cfg.run_stats_days))
    cfg.http_cache_days = int(g("retention", "http_cache_days", default=cfg.http_cache_days))
    vacuum = g("retention", "vacuum_free_pct", default=cfg.vacuum_free_pct)
    if type(vacuum) not in (int, float) or not 1 <= vacuum <= 50:
        raise ValueError("retention.vacuum_free_pct must be a number from 1 to 50")
    cfg.vacuum_free_pct = vacuum
    cache = g("retention", "incremental_cache", default={})
    if not isinstance(cache, dict) or type(cache.get("enabled", False)) is not bool:
        raise ValueError("retention.incremental_cache must be a mapping with a boolean enabled")
    cfg.incremental_cache_enabled = cache.get("enabled", False)
    cfg.incremental_cache_max_examined_per_run = cache.get(
        "max_examined_per_run", cfg.incremental_cache_max_examined_per_run)
    cfg.incremental_cache_budget_s = cache.get("budget_s", cfg.incremental_cache_budget_s)
    validate_incremental_cache_limits(cfg.incremental_cache_max_examined_per_run,
                                      cfg.incremental_cache_budget_s)
    cfg.outgoing_days = int(g("retention", "outgoing_days", default=cfg.outgoing_days))
    for name in ("indexnow_batch_versions_days", "appeal_scores_days",
                 "accuracy_samples_days", "deal_click_shown_days", "source_audit_posts_days",
                 "keepa_recheck_history_samples", "offsite_unseen_days"):
        value = g("retention", name, default=None)
        if value is not None and (type(value) is not int or not 1 <= value <= 3650):
            raise ValueError(f"retention.{name} must be an integer in 1..3650 or null")
        setattr(cfg, name, value)
    for name in ("compact_deal_payloads", "compact_accuracy_samples", "compact_click_exposure"):
        value = g("retention", name, default=False)
        if type(value) is not bool:
            raise ValueError(f"retention.{name} must be a boolean")
        setattr(cfg, name, value)
    cfg.state_retention_337 = g("retention", "state_retention_337", default=False)
    if type(cfg.state_retention_337) is not bool:
        raise ValueError("retention.state_retention_337 must be a boolean")
    for name in ("api_cache_days", "post_receipt_details_days", "terminal_indexnow_versions_days",
                 "audit_detail_days", "audit_hot_days", "click_raw_days", "accuracy_detail_days", "accuracy_last_per_deal"):
        value = g("retention", name, default=getattr(cfg, name))
        if type(value) is not int or not 1 <= value <= 3650:
            raise ValueError(f"retention.{name} must be an integer in 1..3650")
        setattr(cfg, name, value)
    cfg.state_maintenance_max_rows = g("retention", "state_maintenance_max_rows", default=4096)
    cfg.state_maintenance_budget_s = g("retention", "state_maintenance_budget_s", default=1.0)
    from .state_retention import validate_limits as validate_state_maintenance
    validate_state_maintenance(cfg.state_maintenance_max_rows, cfg.state_maintenance_budget_s)
    cfg.state_maintenance_after_deadline = g("retention", "state_maintenance_after_deadline", default=False)
    if type(cfg.state_maintenance_after_deadline) is not bool:
        raise ValueError("retention.state_maintenance_after_deadline must be a boolean")
    from .state_size import validate_limits
    cfg.state_warn_enc_mb, cfg.state_max_enc_mb = validate_limits(g("state", default={}))
    cfg.feature_flags = g("feature_flags", default={}) or {}
    from .source_policy import grant_active
    primary, backup = load_sources()
    affected = sum(bool(s.crawl_permission) and not grant_active(s, cfg)
                   for s in primary + backup if s.enabled)
    if affected:
        logging.getLogger(__name__).warning(
            "monthly membership price invalidates %d source grants; "
            "robots bypass and source images disabled", affected)
    return cfg


def _source_from_dict(d: dict[str, Any]) -> SourceConfig:
    return SourceConfig(
        name=str(d["name"]),
        display_name=str(d.get("display_name") or d["name"]),
        site=str(d.get("site") or ""),
        method=str(d["method"]),
        url=str(d["url"]),
        region=str(d.get("region", "US")),
        allowed_html=bool(d.get("allowed_html", False)),
        fast_lane=bool(d.get("fast_lane", False)),
        adapter=str(d.get("adapter", "")),
        enabled=bool(d.get("enabled", True)),
        tz=str(d.get("tz", "America/New_York")),
        notes=str(d.get("notes", "")),
        crawl_permission=str(d.get("crawl_permission", "")),
        image_permission=bool(d.get("image_permission", False)),
        newsletter_from=[str(address).strip().lower() for address in
                         (d.get("newsletter_from") or [])],
        min_interval_minutes=_min_interval(d),
    )


def _min_interval(d: dict[str, Any]) -> int:
    value = d.get("min_interval_minutes", 0)
    if type(value) is not int or value < 0:
        raise ValueError(f"source {d.get('name')}: min_interval_minutes must be a whole number >= 0")
    return value


def load_sources(path: str | Path | None = None) -> tuple[list[SourceConfig], list[SourceConfig]]:
    """Return (primary_sources, backup_sources) from sources.yaml."""
    path = Path(path) if path else ROOT / "sources.yaml"
    raw = yaml.safe_load(path.read_text()) or {}
    primary = [_source_from_dict(d) for d in raw.get("sources", [])]
    backup = [_source_from_dict(d) for d in raw.get("backup_sources", [])]
    return primary, backup


def daily_spend_cap(cfg: "AppConfig", now) -> float:
    """The daily LLM spend cap in force for ``now``'s UTC day: the temporary
    boost while that day is before spend_cap_boost_until, else the base
    spend_cap_usd_per_day. The boost expires on its own, so spending falls
    back to the base cap even if nobody edits the config."""
    from datetime import date, timezone

    boost = float(getattr(cfg, "spend_cap_boost_usd_per_day", 0.0) or 0.0)
    until = str(getattr(cfg, "spend_cap_boost_until", "") or "")
    if boost > 0 and until:
        try:
            if now.astimezone(timezone.utc).date() < date.fromisoformat(until):
                return boost
        except ValueError:
            pass  # a malformed date never raises the cap
    return float(cfg.spend_cap_usd_per_day)


def spend_allowance(cfg: AppConfig, now, spent_today: float) -> tuple[float, float]:
    """(Run allowance, allowed today): pacing can only tighten the hard caps."""
    from datetime import timezone

    day_cap = daily_spend_cap(cfg, now)
    allowed = day_cap
    if cfg.spend_pacing:
        utc = now.astimezone(timezone.utc)
        minutes = utc.hour * 60 + utc.minute
        allowed *= min(1.0, (minutes + max(0, cfg.spend_pacing_headroom_hours) * 60) / 1440)
    return max(0.0, min(cfg.spend_cap_usd_per_run, allowed - spent_today)), allowed
