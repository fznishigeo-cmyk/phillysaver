"""Analytics and ad tags (config.yaml tracking:): one head snippet that a
post-pass at the end of render_site puts into every published page, hub
pages included.

Only public IDs, each checked against a strict pattern: an invalid one is
skipped with a warning, so no arbitrary text reaches HTML or JS. With every
ID empty no page makes a third-party request. The shared head still carries
the rich-pin opt-out and the device-local internal-visit flag.

Consent (US audience): GA4 loads for everyone, but without Google signals or
ad personalization for a visitor whose browser sends Global Privacy Control
or who opted out on /privacy.html (localStorage wp.adsOptOut). Internal visits
send GA4's traffic_type filter key. For these visitors the
ad pixels (Google Ads, Meta, TikTok, Pinterest, Microsoft UET) and Clarity do not load
at all, so they make no request.
"""

from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path

from ..config import support_link

logger = logging.getLogger("dealradar.publish.tracking")

#: tracking: key -> the exact shape of a real value (ASCII only).
PATTERNS = {
    "ga4_measurement_id": r"G-[A-Z0-9]{4,20}",
    "meta_pixel_id": r"[0-9]{5,20}",
    "tiktok_pixel_id": r"[A-Za-z0-9]{8,32}",
    "pinterest_tag_id": r"[0-9]{5,20}",
    "google_ads_id": r"AW-[0-9]{5,15}",
    "google_ads_trial_label": r"[A-Za-z0-9_-]{4,64}",
    "bing_uet_id": r"[0-9]{4,15}",
    "clarity_project_id": r"[a-z0-9]{8,12}",
    "google_site_verification": r"[A-Za-z0-9_-]{10,100}",
    "bing_site_verification": r"[A-Fa-f0-9]{32}",
    "pinterest_domain_verify": r"[A-Fa-f0-9]{32}",
    "facebook_domain_verification": r"[A-Za-z0-9]{10,64}",
    "impact_site_verification": r"[A-Za-z0-9-]{8,64}",
    "impact_site_verification_2": r"[A-Za-z0-9-]{8,64}",
}

#: Site-ownership checks: config key -> meta tag name (every page carries them).
_VERIFY = (("google_site_verification", "google-site-verification"),
           ("bing_site_verification", "msvalidate.01"),
           ("pinterest_domain_verify", "p:domain_verify"),
           ("facebook_domain_verification", "facebook-domain-verification"),
           ("impact_site_verification", "impact-site-verification"),
           ("impact_site_verification_2", "impact-site-verification"))

_START, _END = "<!-- wp-tracking -->", "<!-- /wp-tracking -->"
_BLOCK = re.compile(re.escape(_START) + r".*?" + re.escape(_END) + r"\n?", re.S)

_INTERNAL = """\
  var internal = false;
  try { internal = localStorage.getItem("wp.internal") === "1"; } catch (e) { /* storage blocked */ }
  try {
    var params = new URLSearchParams(window.location.search), flag = params.get("internal");
    if (flag === "1" || flag === "0") {
      internal = flag === "1";
      if (internal) {
        try { localStorage.setItem("wp.internal", "1"); } catch (e) { /* page-local choice */ }
      } else {
        try { localStorage.removeItem("wp.internal"); } catch (e) { /* page-local choice */ }
      }
    }
    if (params.has("internal")) {
      params.delete("internal");
      var query = params.toString();
      history.replaceState(history.state, "", window.location.pathname + (query ? "?" + query : "") + (window.location.hash || ""));
    }
  } catch (e) { /* URL or history unavailable */ }
"""

_HEAD = """\
(function () {
  "use strict";
""" + _INTERNAL + """\
  // Ad pixels and Clarity load only when the browser sends no Global Privacy Control
  // and the visitor did not opt out on /privacy.html; GA4 loads for
  // everyone, without Google signals or ad personalization when either holds.
  var optOut = false;
  try { optOut = localStorage.getItem("wp.adsOptOut") === "1"; } catch (e) { /* storage blocked */ }
  var ads = !internal && !optOut && navigator.globalPrivacyControl !== true;
  var on = {};
  function load(src, onload) {
    var s = document.createElement("script");
    s.async = true;
    s.src = src;
    if (onload) s.onload = onload;
    document.head.appendChild(s);
  }
"""

_GTAG = """\
  window.dataLayer = window.dataLayer || [];
  window.gtag = function () { window.dataLayer.push(arguments); };
"""

# The deal wall rewrites the address bar before gtag.js reads it, so GA4 gets
# the landing URL's campaign tags (utm_*, click IDs) as page_location.
_GA4 = """\
  // Google Analytics 4: for everyone.
  load("https://www.googletagmanager.com/gtag/js?id=" + %(ga4)s);
  window.gtag("js", new Date());
  var ga4Config = ads ? {} :
              { allow_google_signals: false, allow_ad_personalization_signals: false };
  if (internal) ga4Config.traffic_type = "internal";
  try {
    var landing = new URLSearchParams(window.location.search);
    landing.delete("internal");
    if (/(^|&)(utm_[a-z_]+|gclid|gbraid|wbraid|fbclid)=/.test(landing.toString()))
      ga4Config.page_location = window.location.origin + window.location.pathname + "?" + landing.toString();
  } catch (e) { /* URL unavailable */ }
  window.gtag("config", %(ga4)s, ga4Config);
  on.ga4 = %(ga4)s;
"""

_GADS = """\
  // Google Ads: remarketing, and the trial conversion when a label is set.
  if (ads) {
%(load)s    window.gtag("config", %(aw)s);
    on.gadsTrial = %(send_to)s;
  }
"""

# Google's tag for Ads alone (with GA4 it is on the page already).
_GADS_LOAD = """\
    load("https://www.googletagmanager.com/gtag/js?id=" + %(aw)s);
    window.gtag("js", new Date());
"""

_META = """\
  // Meta Pixel.
  if (ads) {
    var fbq = window.fbq = function () {
      if (fbq.callMethod) fbq.callMethod.apply(fbq, arguments); else fbq.queue.push(arguments);
    };
    if (!window._fbq) window._fbq = fbq;
    fbq.push = fbq; fbq.loaded = true; fbq.version = "2.0"; fbq.queue = [];
    load("https://connect.facebook.net/en_US/fbevents.js");
    // Automatic configuration off: it would report clicked buttons' text
    // and links (a member's deal names and product URLs) on its own.
    fbq("set", "autoConfig", false, %(id)s);
    fbq("init", %(id)s);
    fbq("track", "PageView");
    on.meta = true;
  }
"""

_TIKTOK = """\
  // TikTok Pixel.
  if (ads) {
    window.TiktokAnalyticsObject = "ttq";
    var ttq = window.ttq = window.ttq || [];
    ttq.methods = ["page", "track", "identify", "instances", "debug", "on", "off", "once", "ready",
                   "alias", "group", "enableCookie", "disableCookie", "holdConsent",
                   "revokeConsent", "grantConsent"];
    ttq.setAndDefer = function (t, e) {
      t[e] = function () { t.push([e].concat(Array.prototype.slice.call(arguments, 0))); };
    };
    for (var i = 0; i < ttq.methods.length; i++) ttq.setAndDefer(ttq, ttq.methods[i]);
    ttq.instance = function (t) {
      for (var e = ttq._i[t] || [], n = 0; n < ttq.methods.length; n++) ttq.setAndDefer(e, ttq.methods[n]);
      return e;
    };
    ttq.load = function (e, n) {
      var r = "https://analytics.tiktok.com/i18n/pixel/events.js";
      ttq._i = ttq._i || {}; ttq._i[e] = []; ttq._i[e]._u = r;
      ttq._t = ttq._t || {}; ttq._t[e] = +new Date();
      ttq._o = ttq._o || {}; ttq._o[e] = n || {};
      load(r + "?sdkid=" + e + "&lib=ttq");
    };
    ttq.load(%(id)s);
    ttq.page();
    on.tiktok = true;
  }
"""

_PINTEREST = """\
  // Pinterest Tag.
  if (ads) {
    window.pintrk = function () { window.pintrk.queue.push(Array.prototype.slice.call(arguments)); };
    window.pintrk.queue = [];
    window.pintrk.version = "3.0";
    load("https://s.pinimg.com/ct/core.js");
    window.pintrk("load", %(id)s);
    window.pintrk("page");
    on.pin = true;
  }
"""

_UET = """\
  // Microsoft Advertising UET.
  if (ads) {
    window.uetq = window.uetq || [];
    load("https://bat.bing.com/bat.js", function () {
      var o = { ti: %(id)s };
      o.q = window.uetq;
      window.uetq = new window.UET(o);
      window.uetq.push("pageLoad");
    });
    on.uet = true;
  }
"""

_CLARITY = """\
  // Microsoft Clarity: typed text and account details are masked in the markup.
  if (ads) {
    window.clarity = window.clarity || function () {
      (window.clarity.q = window.clarity.q || []).push(arguments);
    };
    load("https://www.clarity.ms/tag/" + %(id)s);
  }
"""

# wpTrack(name, params): one site event to every tag that loaded, each call
# in its own try/catch; a no-op when none loaded. Only each event's allowed
# fields pass. credit_click sends only the source display name, to GA4.
#
# site event      GA4             Meta                  TikTok                Pinterest  Google Ads                Microsoft UET
# sign_up         sign_up         CompleteRegistration  CompleteRegistration  signup     -                         sign_up
# begin_checkout  begin_checkout  InitiateCheckout      InitiateCheckout      -          -                         begin_checkout
# start_trial     start_trial     StartTrial            Subscribe             checkout   conversion (trial label)  subscribe
# deal_click      deal_click      DealClick (custom)    ClickButton           -          -                         outbound_click
#
# start_trial is worth $0 today (value 0, USD); Meta's StartTrial also carries
# predicted_ltv 9.99, one month of membership. Home load events are GA4-only
# and carry no visitor-supplied params. Keep this table out of every page's bytes.
_DISPATCH = """\
  var EVENTS = {
    credit_click: { ga4Only: true },
    home_load_attempt: { ga4Only: true },
    first_load_failed: { ga4Only: true },
    static_fallback_shown: { ga4Only: true },
    first_load_recovered: { ga4Only: true },
    deal_detail_view: { ga4Only: true, methods: ["deal"] },
    deal_code_copy: { ga4Only: true, methods: ["home", "deal", "category", "store", "price", "best_today"] },
    requirements_open: { ga4Only: true, methods: ["home", "deal", "category", "store", "price", "best_today"] },
    requirements_deal_click: { ga4Only: true, methods: ["home", "deal", "category", "store", "price", "best_today"] },
    filter_apply: { ga4Only: true, methods: ["sheet", "desktop", "clear", "store", "minimum", "category", "amazon"] },
    filter_empty: { ga4Only: true, methods: ["sheet", "desktop", "clear", "store", "minimum", "category", "amazon"] },
    search_apply: { ga4Only: true, methods: ["query"] },
    search_zero: { ga4Only: true, methods: ["query", "filter"] },
    search_recovery: { ga4Only: true, methods: ["query", "store", "category", "minimum", "price", "new", "steps", "suggest_store", "suggest_category"] },
    back_restore: { ga4Only: true, methods: ["back", "reload", "history"] },
    sign_up: { meta: "CompleteRegistration", tiktok: "CompleteRegistration", pin: "signup", uet: "sign_up" },
    begin_checkout: { meta: "InitiateCheckout", tiktok: "InitiateCheckout", uet: "begin_checkout" },
    start_trial: { meta: "StartTrial", tiktok: "Subscribe", pin: "checkout", uet: "subscribe" },
    deal_click: { metaCustom: "DealClick", tiktok: "ClickButton", uet: "outbound_click" }
  };
  window.wpTrack = function (name, params) {
    var ev = EVENTS[name];
    if (!ev) return;
    var p = {};
    if (name === "credit_click") {
      var partner = params && params.partner;
      if (typeof partner !== "string" || !partner.trim() || /https?:|www\\.|[\\r\\n]/i.test(partner)) return;
      p.partner = partner.trim().slice(0, 100);
    }
    if (ev.methods) {
      if (!params || ev.methods.indexOf(params.method) === -1) return;
      p.method = params.method;
    }
    ["store", "category", "method"].forEach(function (k) {
      var v = params && params[k];
      if (!ev.ga4Only && typeof v === "string" && v) p[k] = v.slice(0, 60);
    });
    var pageMethods = ["home", "deal", "category", "store", "price", "best_today"];
    if (name === "deal_click" && pageMethods.indexOf(p.method) === -1) delete p.method;
    if (ev.methods) {
      if (!params || ev.methods.indexOf(params.method) === -1) return;
      p.method = params.method;
    }
    if (name === "begin_checkout" && params && (params.plan === "month" || params.plan === "year")) p.plan = params.plan;
    var trial = name === "start_trial";
    function plus(extra) { return Object.assign({}, p, trial ? { value: 0, currency: "USD" } : {}, extra || {}); }
    function send(fn) { try { fn(); } catch (e) { /* one tag's failure never stops the rest */ } }
    if (on.ga4) send(function () { window.gtag("event", name, plus({ send_to: on.ga4 })); });
    if (ev.ga4Only) return;
    if (on.gadsTrial && trial) send(function () {
      window.gtag("event", "conversion", { send_to: on.gadsTrial, value: 0, currency: "USD" });
    });
    if (on.meta) send(function () {
      if (ev.meta) window.fbq("track", ev.meta, plus(trial ? { predicted_ltv: 9.99 } : null));
      else window.fbq("trackCustom", ev.metaCustom, plus());
    });
    if (on.tiktok) send(function () { window.ttq.track(ev.tiktok, plus()); });
    if (on.pin && ev.pin) send(function () { window.pintrk("track", ev.pin, plus(trial ? { order_quantity: 1 } : null)); });
    if (on.uet) send(function () {
      window.uetq.push("event", ev.uet, trial ? { revenue_value: 0, currency: "USD" }
        : { event_category: p.category || "", event_label: p.store || p.method || "" });
    });
  };
  if (on.ga4 && document.addEventListener) document.addEventListener("click", function (event) {
    var target = event.target;
    var link = target && target.closest && target.closest(".credit a, .detail-sources a");
    if (link) window.wpTrack("credit_click", {partner: link.textContent});
  });
})();
"""

_SAVED_DISPATCH = """\
    // Local shortlist events are GA4-only. No identity, label or list leaves
    // the browser; both the action labels and numeric counts are bounded.
    var savedMethods = {deal_save: ["save", "remove", "clear"], saved_view: ["button"],
                        saved_deal_click: ["outbound"]};
    if (Object.prototype.hasOwnProperty.call(savedMethods, name)) {
      var method = params && params.method, count = params && params.count;
      if (savedMethods[name].indexOf(method) === -1 || !Number.isSafeInteger(count) || count < 0 || count > 200) return;
      if (on.ga4) {
        try { window.gtag("event", name, {method: method, count: count, send_to: on.ga4}); }
        catch (e) { /* analytics never stops a save */ }
      }
      return;
    }
"""

_WATCH_EVENTS = """\
    watch_create: { ga4Only: true, methods: ["save", "target"] },
    watch_match: { ga4Only: true, methods: ["drop", "target"] },
    watch_deal_click: { ga4Only: true, methods: ["outbound"] },
"""


def tracking_ids(cfg) -> dict[str, str]:
    """The configured tracking values that pass their pattern; each invalid
    one is skipped with a warning (the key only, never the value)."""
    raw = getattr(cfg, "tracking", None) or {}
    ids: dict[str, str] = {}
    for key, pattern in PATTERNS.items():
        val = str(raw.get(key) or "").strip()
        if not val:
            continue
        if re.fullmatch(pattern, val, re.ASCII):
            ids[key] = val
        else:
            logger.warning("tracking.%s is not a valid value; skipped", key)
    return ids


def head_snippet(cfg) -> str:
    """The marked block for every page's <head>: the verification meta tags
    and the tags' script, including device-local internal-visit choices."""
    ids = tracking_ids(cfg)
    js = lambda key: json.dumps(ids[key])  # validated [A-Za-z0-9_-] only
    # Impact reads a `value` attribute; it rides beside `content`.
    metas = [f'<meta name="{name}"'
             + (f' value="{html.escape(ids[key], quote=True)}"' if name == "impact-site-verification" else "")
             + f' content="{html.escape(ids[key], quote=True)}">\n'
             for key, name in _VERIFY if key in ids]
    metas.insert(0, '<meta name="pinterest-rich-pin" content="false">\n')
    blocks = []
    ga4, aw = ids.get("ga4_measurement_id"), ids.get("google_ads_id")
    if ga4 or aw:
        blocks.append(_GTAG)
    if ga4:
        blocks.append(_GA4 % {"ga4": js("ga4_measurement_id")})
    if aw:
        label = ids.get("google_ads_trial_label")
        blocks.append(_GADS % {
            "aw": js("google_ads_id"),
            "load": "" if ga4 else _GADS_LOAD % {"aw": js("google_ads_id")},
            "send_to": json.dumps(f"{aw}/{label}" if label else "")})
    for key, block in (("meta_pixel_id", _META), ("tiktok_pixel_id", _TIKTOK),
                       ("pinterest_tag_id", _PINTEREST), ("bing_uet_id", _UET),
                       ("clarity_project_id", _CLARITY)):
        if key in ids:
            blocks.append(block % {"id": js(key)})
    from . import credits
    dispatch = credits.tracking_template(_DISPATCH, cfg)
    if cfg.home_link_resets:
        dispatch = dispatch.replace("  var EVENTS = {", "  var EVENTS = {\n"
                                    '    home_reset: { ga4Only: true, methods: ["logo", "ticker", "timer"] },')
    from .search_results import enabled as search_results_enabled
    from .search_analyze import enabled as search_analyze_enabled
    from .search_more import enabled as search_more_enabled
    if search_more_enabled(cfg):
        dispatch = dispatch.replace("    var ev = EVENTS[name];", """\
    if (name === "search_more_page" || name === "search_spelling") {
      var safe = {send_to: on.ga4};
      if (name === "search_more_page") {
        if (!params || !Number.isSafeInteger(params.page) || params.page < 0 || params.page >= SEARCH_MORE_PAGES) return;
        safe.page = params.page;
      } else {
        if (!params || typeof params.corrected !== "boolean") return;
        safe.corrected = params.corrected;
      }
      if (on.ga4) { try { window.gtag("event", name, safe); } catch (e) {} }
      return;
    }
    var ev = EVENTS[name];""".replace("SEARCH_MORE_PAGES", str(cfg.search_more.get("pages", 3))))
    if search_analyze_enabled(cfg):
        dispatch = dispatch.replace("    var ev = EVENTS[name];", """\
    if (["search_analyze", "paste_amazon_link", "analyze_result"].indexOf(name) !== -1) {
      var safe = {send_to: on.ga4};
      if (name === "analyze_result") {
        if (!params || !Number.isSafeInteger(params.count) || params.count < 0 || params.count > 16 ||
            typeof params.live !== "boolean" || typeof params.matched !== "boolean") return;
        safe.count = params.count; safe.live = params.live; safe.matched = params.matched;
      } else {
        if (!params || typeof params.link !== "boolean") return;
        safe.link = params.link;
      }
      if (on.ga4) { try { window.gtag("event", name, safe); } catch (e) {} }
      return;
    }
    var ev = EVENTS[name];""")
    if search_results_enabled(cfg):
        dispatch = dispatch.replace("    var ev = EVENTS[name];", """\
    if (name === "search_amazon_results") {
      var count = params && params.count;
      if (!Number.isSafeInteger(count) || count < 0 || count > 10) return;
      if (on.ga4) { try { window.gtag("event", name, {count: count, send_to: on.ga4}); } catch (e) {} }
      return;
    }
    var ev = EVENTS[name];""")
    if getattr(cfg, "all_discounts_button", False) is True:
        dispatch = dispatch.replace("  var EVENTS = {", "  var EVENTS = {\n"
                                    '    search_discounts: { ga4Only: true },')
    if cfg.search_amazon and cfg.affiliate_amazon_tag:
        dispatch = dispatch.replace("    var ev = EVENTS[name];", """\
    if (name === "search_amazon") {
      if (on.ga4) { try { window.gtag("event", name, {send_to: on.ga4}); } catch (e) {} }
      if (on.meta) { try { window.fbq("trackCustom", "SearchAmazon", {}); } catch (e) {} }
      return;
    }
    var ev = EVENTS[name];""")
    if cfg.traffic_hub_pagination_enabled or cfg.traffic_product_families_enabled:
        dispatch = dispatch.replace("  var EVENTS = {", "  var EVENTS = {\n"
                                    '    hub_page_click: { ga4Only: true, methods: ["category", "store", "price", "product_family"] },\n'
                                    '    product_family_click: { ga4Only: true, methods: ["deal"] },')
        dispatch = dispatch.replace('["home", "deal", "category", "store", "price", "best_today"]',
                                    '["home", "deal", "category", "store", "price", "best_today", "product_family"]')
    if any((getattr(cfg, "traffic_feed_enabled", False),
            getattr(cfg, "traffic_share_enabled", False),
            getattr(cfg, "traffic_related_hubs_enabled", False))):
        dispatch = dispatch.replace("  var EVENTS = {", "  var EVENTS = {\n"
                                    '    deal_share: { ga4Only: true, methods: ["native", "copy"] },\n'
                                    '    share_arrival: { ga4Only: true, methods: ["deal"] },\n'
                                    '    feed_open: { ga4Only: true, methods: ["footer"] },\n'
                                    '    feed_arrival: { ga4Only: true, methods: ["deal"] },\n'
                                    '    related_hub_click: { ga4Only: true, methods: ["store", "price"] },')
    if getattr(cfg, "pwa_enabled", False):
        dispatch = dispatch.replace("  var EVENTS = {", "  var EVENTS = {\n"
                                    '    install_offer_shown: { ga4Only: true },\n'
                                    '    install_accepted: { ga4Only: true },\n'
                                    '    standalone_session: { ga4Only: true },')
        if cfg.pwa_install_button:
            dispatch = dispatch.replace("  var EVENTS = {", "  var EVENTS = {\n"
                                        '    install_click: { ga4Only: true },\n'
                                        '    install_dismissed: { ga4Only: true },\n'
                                        '    installed: { ga4Only: true },')
    if getattr(cfg, "support_enabled", False) and getattr(cfg, "engagement", {}).get("saved_deals", False):
        dispatch = dispatch.replace("    var ev = EVENTS[name];", _SAVED_DISPATCH + "    var ev = EVENTS[name];")
        dispatch = dispatch.replace("  var EVENTS = {", "  var EVENTS = {\n" + _WATCH_EVENTS)
    if getattr(cfg, "engagement", {}).get("surprise_me", False):
        dispatch = dispatch.replace("  var EVENTS = {", "  var EVENTS = {\n"
                                    '    surprise_click: { metaCustom: "SurpriseClick", tiktok: "ClickButton", uet: "surprise_click" },')
    if getattr(cfg, "support_enabled", False):
        dispatch = dispatch.replace("  var EVENTS = {", "  var EVENTS = {\n"
                                    '    signup_prompt_shown: { metaCustom: "SignupPromptShown", tiktok: "SignupPromptShown", uet: "signup_prompt_shown" },\n'
                                    '    signup_prompt_closed: { metaCustom: "SignupPromptClosed", tiktok: "SignupPromptClosed", uet: "signup_prompt_closed" },\n'
                                    '    support_click: { metaCustom: "SupportClick", tiktok: "ClickButton", uet: "support_click" },')
        if support_link(getattr(cfg, "support_payment_link", "")):
            dispatch = dispatch.replace("  var EVENTS = {", "  var EVENTS = {\n"
                                        '    support_ask_shown: { ga4Only: true },\n'
                                        '    support_ask_click: { ga4Only: true, methods: ["once", "monthly"] },\n'
                                        '    support_ask_dismiss: { ga4Only: true },')
    if cfg.pwa_android["enabled"]:
        app = 'document.documentElement.classList.contains("in-app")'
        blocks = [block.replace('if (ads) {', f'if (ads && !{app}) {{')
                  if 'connect.facebook.net' in block or 'clarity.ms' in block else block
                  for block in blocks]
        blocks = [block.replace('  if (internal) ga4Config.traffic_type',
                                f'  if ({app}) ga4Config.app_context = "android";\n'
                                '  if (internal) ga4Config.traffic_type') for block in blocks]
    code = _HEAD + ''.join(blocks) + dispatch if blocks else '(function () {\n' + _INTERNAL + '})();\n'
    script = f"<script>\n{code}</script>\n"
    return f"{_START}\n{''.join(metas)}{script}{_END}\n"


def inject(page: str, snippet: str) -> str:
    """page with snippet right before </head>, replacing any block an earlier
    pass put there (so it is never doubled; an empty snippet removes it)."""
    page = _BLOCK.sub("", page)
    at = page.lower().find("</head>")
    if not snippet or at < 0:
        return page
    return page[:at] + snippet + page[at:]


def apply_tracking(out_dir: str | Path, cfg) -> int:
    """Put the head snippet into every *.html under out_dir, recursively;
    return how many files changed."""
    snippet = head_snippet(cfg)
    changed = 0
    for path in sorted(Path(out_dir).rglob("*.html")):
        if path.relative_to(out_dir).as_posix() == "check/index.html":
            continue
        page = path.read_text(encoding="utf-8")
        new = inject(page, snippet)
        if new != page:
            path.write_text(new, encoding="utf-8")
            changed += 1
    return changed
