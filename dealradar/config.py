"""Configuration loading: config.yaml + sources.yaml + env-only secrets."""

from __future__ import annotations

import logging
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
    "STRIPE_SECRET_KEY",  # read-only restricted key: member list for alerts
    "NEWSLETTER_SECRET",  # bearer token for the Worker: /list (newsletter) and /api/publish (paywall)
]


def get_secret(name: str) -> str:
    """Read a secret from the environment. Empty string when unset."""
    return os.environ.get(name, "").strip()


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
    retries_on_invalid_json: int = 2
    # Probe the structured messages.parse endpoint for extraction. Off by
    # default: our schema is rejected as "too complex" after a MINUTES-long
    # stall, so we go straight to the JSON create() path that handles it.
    parse_structured_output: bool = False
    # Extraction calls in flight at once. Parse throughput is bound by API
    # round-trip time, not tokens; a small pool multiplies posts-per-run.
    parse_concurrency: int = 1
    model_pricing_per_mtok: dict[str, dict[str, float]] = field(default_factory=dict)
    second_opinion_model: str = ""      # "" disables the second-opinion pass
    max_second_opinions_per_run: int = 8
    critic_model: str = ""              # "" disables the deal critic
    critic_enabled: bool = True
    critic_batch_size: int = 8
    exclude_suspect_msrp: bool = True

    # -- publish / ranking --
    min_pct: float = 70.0
    used_min_pct: float = 80.0        # absolute floor for used/open-box deals
    min_abs_savings: float = 5.0
    site_show_from_pct: float = 70.0  # site-only lower tier (= min_pct: off)
    show_dead_hours: float = 24.0      # dead deals linger on the site this long
    drop_minutes: int = 15            # the page's drop clock step (meta.cadence_minutes)
    site_dir: str = "site"
    # The site's public domain: canonical URLs, sitemap, IndexNow. (Actions
    # Pages deploys ignore the CNAME file; Settings -> Pages holds the domain.)
    custom_domain: str = ""
    ftc_disclosure: str = ""
    affiliate_amazon_tag: str = ""     # Amazon Associates Store ID ("" = links untouched)
    affiliate_disclosure: str = ""     # the disclosure used once a Store ID is set
    # -- seo: category hubs and IndexNow (config.yaml seo:) --
    seo_hub_min_items: int = 10        # 7-day verified deals for an indexable hub
    seo_deal_pages_max: int = 3000
    seo_store_page_min_deals: int = 15
    seo_store_pages_max: int = 12
    seo_price_pages: list[int] = field(default_factory=lambda: [10, 25, 50])
    seo_indexnow_key: str = ""         # "" = no key file, no IndexNow
    seo_indexnow_min_hours: float = 6.0
    seo_indexnow_resubmit_days: float = 7.0  # unchanged URLs listed again (0 = never)
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
    telegram_messages_per_minute: int = 15
    resend_price_drop_pct: float = 10.0
    telegram_dry_run: bool = False
    digest_hour_local: int = 8
    digest_timezone: str = "America/New_York"
    digest_recipient: str = ""
    min_items_ratio: float = 0.5
    tripped_sources_warn: int = 5

    # -- growth: the daily private report (config.yaml growth:); "" skips a section --
    growth_report_days: int = 7
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
    # Loaded configs enable all five; an empty programmatic config preserves
    # the legacy template (including the dormant membership snapshots).
    engagement: dict[str, bool] = field(default_factory=dict)
    # -- newsletter: the free sign-up list's daily top N (Cloudflare Worker + KV) --
    newsletter_enabled: bool = True
    newsletter_worker_url: str = ""    # https://wowpriced-subscribe.<sub>.workers.dev
    newsletter_hour_local: int = 8
    newsletter_timezone: str = "America/New_York"
    newsletter_max_per_run: int = 200  # messages per run; the rest ride the next run
    newsletter_top_n: int = 10         # deals per email, picked once a day by the parse model
    # -- tracking: public analytics/ad tag IDs, validated in publish/tracking.py --
    tracking: dict[str, str] = field(default_factory=dict)

    # -- keepa: /deal paging on the token plan (config.yaml keepa:) --
    keepa_amazon_pages_per_pull: int = 6
    keepa_warehouse_pages_per_pull: int = 12
    keepa_reserve_tokens: float = 300.0       # left untouched for product calls
    # NEW (not yet content-hashed) posts per pull: the first-run burst bound
    keepa_amazon_max_new_posts_per_pull: int = 10
    keepa_warehouse_max_new_posts_per_pull: int = 20
    keepa_warehouse_min_drop_pct: float = 45.0  # used price vs honest new price
    keepa_warehouse_min_new_price: float = 25.0
    keepa_sweep_enabled: bool = True          # warehouse sweep on idle tokens
    keepa_sweep_asins_per_pull: int = 100
    keepa_sweep_reserve_tokens: float = 400.0  # kept on top of reserve_tokens
    # -- woot --
    woot_max_items_per_run: int = 300
    woot_max_new_items_per_run: int = 20      # unseen posts per run

    # -- retention / flags --
    posts_seen_days: int = 30
    deals_days: int = 14
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
    cfg.retries_on_invalid_json = int(g("parse", "retries_on_invalid_json", default=cfg.retries_on_invalid_json))
    cfg.parse_structured_output = bool(g("parse", "structured_output", default=cfg.parse_structured_output))
    cfg.parse_concurrency = max(1, int(g("parse", "concurrency", default=cfg.parse_concurrency)))
    cfg.second_opinion_model = g("parse", "second_opinion_model", default="") or ""
    cfg.max_second_opinions_per_run = int(g("parse", "max_second_opinions_per_run", default=cfg.max_second_opinions_per_run))
    cfg.critic_model = g("parse", "critic_model", default="") or ""
    cfg.critic_enabled = bool(g("parse", "critic_enabled", default=cfg.critic_enabled))
    cfg.critic_batch_size = max(1, int(g("parse", "critic_batch_size", default=8)))
    cfg.exclude_suspect_msrp = bool(g("publish", "exclude_suspect_msrp", default=cfg.exclude_suspect_msrp))
    cfg.model_pricing_per_mtok = g("parse", "model_pricing_per_mtok", default={}) or {}

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
    cfg.custom_domain = str(g("publish", "custom_domain", default="") or "").strip().lower()
    cfg.ftc_disclosure = g("publish", "ftc_disclosure", default="")
    tag = str(g("affiliate", "amazon_tag", default="") or "").strip()
    if tag and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{1,48}", tag):
        logging.getLogger("dealradar.config").warning("affiliate.amazon_tag ignored: not a Store ID")
        tag = ""
    cfg.affiliate_amazon_tag = tag
    cfg.affiliate_disclosure = str(g("affiliate", "disclosure", default="") or "").strip()
    cfg.seo_hub_min_items = max(0, int(g("seo", "hub_min_items", default=cfg.seo_hub_min_items)))
    cfg.seo_deal_pages_max = max(0, int(g("seo", "deal_pages_max", default=cfg.seo_deal_pages_max)))
    cfg.seo_store_page_min_deals = max(1, int(g(
        "seo", "store_page_min_deals", default=cfg.seo_store_page_min_deals)))
    cfg.seo_store_pages_max = max(0, int(g("seo", "store_pages_max", default=cfg.seo_store_pages_max)))
    cfg.seo_price_pages = sorted({int(n) for n in g(
        "seo", "price_pages", default=cfg.seo_price_pages) if int(n) > 0})
    cfg.seo_indexnow_key = str(g("seo", "indexnow_key", default="") or "").strip()
    cfg.seo_indexnow_min_hours = max(0.0, float(g("seo", "indexnow_min_hours",
                                                  default=cfg.seo_indexnow_min_hours)))
    cfg.seo_indexnow_resubmit_days = max(0.0, float(g("seo", "indexnow_resubmit_days",
                                                      default=cfg.seo_indexnow_resubmit_days)))

    cfg.tier_boost = g("ranking", "tier_boost", default={}) or {}
    cfg.abs_savings_weight = float(g("ranking", "abs_savings_weight", default=cfg.abs_savings_weight))
    cfg.condition_penalty = float(g("ranking", "condition_penalty", default=cfg.condition_penalty))
    cfg.age_penalty_per_hour = float(g("ranking", "age_penalty_per_hour", default=cfg.age_penalty_per_hour))

    cfg.corroborate_window_h = float(g("verify", "corroborate_window_h", default=cfg.corroborate_window_h))
    cfg.history_min_observations = int(g("verify", "history_min_observations", default=cfg.history_min_observations))
    cfg.fuzzy_threshold = float(g("dedupe", "fuzzy_threshold", default=cfg.fuzzy_threshold))

    cfg.telegram_channels = g("telegram", "channels", default=[]) or []
    cfg.telegram_messages_per_minute = int(g("telegram", "messages_per_minute", default=cfg.telegram_messages_per_minute))
    cfg.resend_price_drop_pct = float(g("telegram", "resend_price_drop_pct", default=cfg.resend_price_drop_pct))
    cfg.telegram_dry_run = bool(g("telegram", "dry_run", default=cfg.telegram_dry_run))

    cfg.digest_hour_local = int(g("digest", "hour_local", default=cfg.digest_hour_local))
    cfg.digest_timezone = g("digest", "timezone", default=cfg.digest_timezone)
    cfg.digest_recipient = g("digest", "recipient", default="") or ""

    cfg.min_items_ratio = float(g("alerting", "min_items_ratio", default=cfg.min_items_ratio))
    cfg.tripped_sources_warn = int(g("alerting", "tripped_sources_warn", default=cfg.tripped_sources_warn))

    def gs(key: str) -> str:
        return str(g("growth", key, default="") or "").strip()

    cfg.growth_report_days = max(1, int(g("growth", "report_days", default=cfg.growth_report_days)))
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
    cfg.engagement = {name: bool(g("engagement", name, default=True)) for name in (
        "new_since_visit", "weekly_finds", "visit_streak", "surprise_me", "hot_meter")}
    cfg.newsletter_enabled = bool(g("newsletter", "enabled", default=cfg.newsletter_enabled))
    cfg.newsletter_worker_url = str(g("newsletter", "worker_url", default="") or "").strip().rstrip("/")
    cfg.newsletter_hour_local = int(g("newsletter", "hour_local", default=cfg.newsletter_hour_local))
    cfg.newsletter_timezone = g("newsletter", "timezone", default=cfg.newsletter_timezone)
    cfg.newsletter_max_per_run = max(1, int(g("newsletter", "max_per_run",
                                              default=cfg.newsletter_max_per_run)))
    cfg.newsletter_top_n = max(1, int(g("newsletter", "top_n", default=cfg.newsletter_top_n)))
    tracking = g("tracking", default={})
    cfg.tracking = {str(k): str(v).strip() for k, v in tracking.items()
                    if v not in (None, "")} if isinstance(tracking, dict) else {}

    cfg.keepa_amazon_pages_per_pull = max(1, int(g(
        "keepa", "amazon_pages_per_pull", default=cfg.keepa_amazon_pages_per_pull)))
    cfg.keepa_warehouse_pages_per_pull = max(1, int(g(
        "keepa", "warehouse_pages_per_pull", default=cfg.keepa_warehouse_pages_per_pull)))
    cfg.keepa_reserve_tokens = max(0.0, float(g(
        "keepa", "reserve_tokens", default=cfg.keepa_reserve_tokens)))
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

    cfg.posts_seen_days = int(g("retention", "posts_seen_days", default=cfg.posts_seen_days))
    cfg.deals_days = int(g("retention", "deals_days", default=cfg.deals_days))
    cfg.feature_flags = g("feature_flags", default={}) or {}
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
