"""Analytics and ad tags (config.yaml tracking:): one head snippet that a
post-pass at the end of render_site puts into every published page, hub
pages included.

Only public IDs, each checked against a strict pattern: an invalid one is
skipped with a warning, so no arbitrary text reaches HTML or JS. With every
ID empty nothing is added and no page makes a third-party request.

Consent (US audience): GA4 loads for everyone, but without Google signals or
ad personalization for a visitor whose browser sends Global Privacy Control
or who opted out on /privacy.html (localStorage wp.adsOptOut). For them the
ad pixels (Google Ads, Meta, TikTok, Pinterest, Microsoft UET) do not load
at all, so they make no request.
"""

from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path

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
    "google_site_verification": r"[A-Za-z0-9_-]{10,100}",
    "bing_site_verification": r"[A-Fa-f0-9]{32}",
    "pinterest_domain_verify": r"[A-Fa-f0-9]{32}",
    "facebook_domain_verification": r"[A-Za-z0-9]{10,64}",
}

#: Site-ownership checks: config key -> meta tag name (every page carries them).
_VERIFY = (("google_site_verification", "google-site-verification"),
           ("bing_site_verification", "msvalidate.01"),
           ("pinterest_domain_verify", "p:domain_verify"),
           ("facebook_domain_verification", "facebook-domain-verification"))

_START, _END = "<!-- wp-tracking -->", "<!-- /wp-tracking -->"
_BLOCK = re.compile(re.escape(_START) + r".*?" + re.escape(_END) + r"\n?", re.S)

_HEAD = """\
(function () {
  "use strict";
  // Ad pixels load only when the browser sends no Global Privacy Control
  // and the visitor did not opt out on /privacy.html; GA4 loads for
  // everyone, without Google signals or ad personalization when either holds.
  var optOut = false;
  try { optOut = localStorage.getItem("wp.adsOptOut") === "1"; } catch (e) { /* storage blocked */ }
  var ads = !optOut && navigator.globalPrivacyControl !== true;
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

_GA4 = """\
  // Google Analytics 4: for everyone.
  load("https://www.googletagmanager.com/gtag/js?id=" + %(ga4)s);
  window.gtag("js", new Date());
  window.gtag("config", %(ga4)s, ads ? {} :
              { allow_google_signals: false, allow_ad_personalization_signals: false });
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

_DISPATCH = """\
  // wpTrack(name, params): one site event to every tag that loaded, each
  // call in its own try/catch; a no-op when none loaded. Only the store,
  // category, method and checkout plan params pass (anything else is dropped here, so no
  // item name or price ever reaches a partner).
  //
  //   site event      GA4             Meta                  TikTok                Pinterest  Google Ads                Microsoft UET
  //   sign_up         sign_up         CompleteRegistration  CompleteRegistration  signup     -                         sign_up
  //   begin_checkout  begin_checkout  InitiateCheckout      InitiateCheckout      -          -                         begin_checkout
  //   start_trial     start_trial     StartTrial            Subscribe             checkout   conversion (trial label)  subscribe
  //   deal_click      deal_click      DealClick (custom)    ClickButton           -          -                         outbound_click
  //
  // start_trial is worth $0 today (value 0, USD); Meta's StartTrial also
  // carries predicted_ltv 9.99, one month of membership.
  var EVENTS = {
    sign_up: { meta: "CompleteRegistration", tiktok: "CompleteRegistration", pin: "signup", uet: "sign_up" },
    begin_checkout: { meta: "InitiateCheckout", tiktok: "InitiateCheckout", uet: "begin_checkout" },
    start_trial: { meta: "StartTrial", tiktok: "Subscribe", pin: "checkout", uet: "subscribe" },
    deal_click: { metaCustom: "DealClick", tiktok: "ClickButton", uet: "outbound_click" }
  };
  window.wpTrack = function (name, params) {
    var ev = EVENTS[name];
    if (!ev) return;
    var p = {};
    ["store", "category", "method"].forEach(function (k) {
      var v = params && params[k];
      if (typeof v === "string" && v) p[k] = v.slice(0, 60);
    });
    if (name === "begin_checkout" && params && (params.plan === "month" || params.plan === "year")) p.plan = params.plan;
    var trial = name === "start_trial";
    function plus(extra) { return Object.assign({}, p, trial ? { value: 0, currency: "USD" } : {}, extra || {}); }
    function send(fn) { try { fn(); } catch (e) { /* one tag's failure never stops the rest */ } }
    if (on.ga4) send(function () { window.gtag("event", name, plus({ send_to: on.ga4 })); });
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
})();
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
    and the tags' script; "" when nothing is configured."""
    ids = tracking_ids(cfg)
    js = lambda key: json.dumps(ids[key])  # validated [A-Za-z0-9_-] only
    metas = [f'<meta name="{name}" content="{html.escape(ids[key], quote=True)}">\n'
             for key, name in _VERIFY if key in ids]
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
                       ("pinterest_tag_id", _PINTEREST), ("bing_uet_id", _UET)):
        if key in ids:
            blocks.append(block % {"id": js(key)})
    dispatch = _DISPATCH
    if getattr(cfg, "engagement", {}).get("surprise_me", False):
        dispatch = dispatch.replace("  var EVENTS = {", "  var EVENTS = {\n"
                                    '    surprise_click: { metaCustom: "SurpriseClick", tiktok: "ClickButton", uet: "surprise_click" },')
    if getattr(cfg, "support_enabled", False):
        dispatch = dispatch.replace("  var EVENTS = {", "  var EVENTS = {\n"
                                    '    signup_prompt_shown: { metaCustom: "SignupPromptShown", tiktok: "SignupPromptShown", uet: "signup_prompt_shown" },\n'
                                    '    signup_prompt_closed: { metaCustom: "SignupPromptClosed", tiktok: "SignupPromptClosed", uet: "signup_prompt_closed" },\n'
                                    '    support_click: { metaCustom: "SupportClick", tiktok: "ClickButton", uet: "support_click" },')
    script = f"<script>\n{_HEAD}{''.join(blocks)}{dispatch}</script>\n" if blocks else ""
    if not metas and not script:
        return ""
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
        page = path.read_text(encoding="utf-8")
        new = inject(page, snippet)
        if new != page:
            path.write_text(new, encoding="utf-8")
            changed += 1
    return changed
