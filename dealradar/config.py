"""Configuration loading: config.yaml + sources.yaml + env-only secrets."""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .models import SourceConfig

ROOT = Path(__file__).resolve().parent.parent


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

    # -- identity / politeness --
    contact_email: str = "set-me@example.com"
    user_agent: str = "dealradar/0.1"
    timezone: str = "America/New_York"

    # -- fetch --
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
    front_page: dict = field(default_factory=dict)  # All top picks / fresh drops mix
    site_dir: str = "site"
    amazon_as_of_enabled: bool = False
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
    affiliate_ebay_campaign_id: str = ""  # eBay Partner Network, 10 digits ("" = off)
    # -- seo: category hubs and IndexNow (config.yaml seo:) --
    seo_hub_min_items: int = 10        # visible live offers for an indexable hub
    seo_home_min_items: int = 1
    seo_best_today_min_items: int = 1
    seo_deal_page_min_items: int = 1
    seo_deal_pages_max: int = 3000
    seo_deal_pages_indexable: bool = True
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
    join_prompt: bool = True
    join_prompt_delay_s: int = 15
    join_apple: bool = False          # reserved until Apple sign-in is supported
    # Loaded configs enable engagement; an empty programmatic config preserves
    # the legacy template (including the dormant membership snapshots).
    engagement: dict[str, bool] = field(default_factory=dict)
    engagement_saved_deals_limit: int = 100  # includes browser-only price watches
    # -- home-screen app: absent block preserves the existing site bytes --
    pwa_enabled: bool = False
    pwa_name: str = "wowpriced"
    pwa_short_name: str = "wowpriced"
    # -- newsletter: the free sign-up list's daily top N (Cloudflare Worker + KV) --
    newsletter_enabled: bool = True
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
    social_require_earning_link: bool = True
    social_exclude_conditions: list[str] = field(
        default_factory=lambda: ["used", "open_box", "refurbished"])
    social_exclude_categories: list[str] = field(default_factory=list)
    social_require_stock_evidence: bool = False
    social_extreme_pct: float = 80.0

    # -- keepa: /deal paging on the token plan (config.yaml keepa:) --
    keepa_amazon_pages_per_pull: int = 6
    keepa_warehouse_pages_per_pull: int = 12
    keepa_reserve_tokens: float = 300.0       # left untouched for product calls
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
    # The All tab's scored pick candidates: new-condition Amazon deals
    # re-checked at least this often, hours; 0 = no accelerated cadence.
    keepa_recheck_top_h: float = 0.0
    # Fallback kill tolerance when the paid record cannot re-price a deal.
    keepa_recheck_tolerance_pct: float = 10.0
    keepa_buybox_rule: str = "off"  # off | shadow | on (publication gate only)
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
    llm_calls_days: int = 35
    run_stats_days: int = 60
    http_cache_days: int = 30
    outgoing_days: int = 60
    feature_flags: dict[str, bool] = field(default_factory=dict)

    def resolved_user_agent(self) -> str:
        return self.user_agent.replace("{contact_email}", self.contact_email)


def load_config(path: str | Path | None = None) -> AppConfig:
    path = Path(path) if path else ROOT / "config.yaml"
    raw = yaml.safe_load(path.read_text()) or {}

    def g(*keys: str, default: Any = None) -> Any:
        cur: Any = raw
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    cfg = AppConfig(raw=raw)
    cfg.accuracy_extra_samples_per_day = max(0, int(
        (raw.get("accuracy") or {}).get("extra_samples_per_day", 0)))
    cfg.contact_email = g("contact_email", default=cfg.contact_email)
    cfg.user_agent = g("user_agent", default=cfg.user_agent)
    cfg.timezone = g("timezone", default=cfg.timezone)

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
    cfg.site_dir = g("publish", "site_dir", default=cfg.site_dir)
    cfg.amazon_as_of_enabled = bool(g("publish", "amazon_as_of", "enabled", default=False))
    cfg.unconfirmed_label_enabled = bool(g("site", "unconfirmed_label", "enabled", default=False))
    for condition in ("new", "used"):
        setattr(cfg, "unconfirmed_label_max_h_" + condition, max(0.0, float(g(
            "site", "unconfirmed_label", "max_h_" + condition, default=48.0))))
    cfg.front_page = g("publish", "front_page", default={}) or {}
    cfg.pinterest = g("social", "pinterest", default={}) or {}
    cfg.meta = g("social", "meta", default={}) or {}
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
    cfg.seo_home_min_items = max(1, int(g("seo", "home_min_items", default=cfg.seo_home_min_items)))
    cfg.seo_best_today_min_items = max(1, int(g("seo", "best_today_min_items", default=cfg.seo_best_today_min_items)))
    cfg.seo_deal_page_min_items = max(1, int(g("seo", "deal_page_min_items", default=cfg.seo_deal_page_min_items)))
    cfg.seo_deal_pages_max = max(0, int(g("seo", "deal_pages_max", default=cfg.seo_deal_pages_max)))
    cfg.seo_deal_pages_indexable = bool(g("seo", "deal_pages_indexable", default=cfg.seo_deal_pages_indexable))
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
    cfg.join_prompt = bool(g("join", "prompt", default=True))
    cfg.join_prompt_delay_s = max(15, int(g("join", "prompt_delay_s", default=15)))
    cfg.join_apple = bool(g("join", "apple", default=False))
    cfg.engagement = {name: bool(g("engagement", name, default=True)) for name in (
        "new_since_visit", "weekly_finds", "visit_streak", "surprise_me", "hot_meter", "saved_deals")}
    cfg.engagement_saved_deals_limit = max(1, min(200, int(g(
        "engagement", "saved_deals_limit", default=cfg.engagement_saved_deals_limit))))
    cfg.pwa_enabled = bool(g("pwa", "enabled", default=cfg.pwa_enabled))
    cfg.pwa_name = str(g("pwa", "name", default=cfg.pwa_name) or cfg.pwa_name).strip() or cfg.pwa_name
    cfg.pwa_short_name = str(g("pwa", "short_name", default=cfg.pwa_short_name) or cfg.pwa_short_name).strip() or cfg.pwa_short_name
    cfg.newsletter_enabled = bool(g("newsletter", "enabled", default=cfg.newsletter_enabled))
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

    cfg.keepa_amazon_pages_per_pull = max(1, int(g(
        "keepa", "amazon_pages_per_pull", default=cfg.keepa_amazon_pages_per_pull)))
    cfg.keepa_warehouse_pages_per_pull = max(1, int(g(
        "keepa", "warehouse_pages_per_pull", default=cfg.keepa_warehouse_pages_per_pull)))
    cfg.keepa_reserve_tokens = max(0.0, float(g(
        "keepa", "reserve_tokens", default=cfg.keepa_reserve_tokens)))
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
    cfg.keepa_recheck_tolerance_pct = max(0.0, float(g(
        "keepa", "recheck_tolerance_pct", default=cfg.keepa_recheck_tolerance_pct)))
    buybox = g("keepa", "buybox_rule", default=cfg.keepa_buybox_rule)
    cfg.keepa_buybox_rule = ("on" if buybox else "off") if isinstance(buybox, bool) else str(buybox)
    if cfg.keepa_buybox_rule not in {"off", "shadow", "on"}:
        raise ValueError("keepa.buybox_rule must be off, shadow, or on")
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
    cfg.outgoing_days = int(g("retention", "outgoing_days", default=cfg.outgoing_days))
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
    )


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
