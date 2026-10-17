"""Small title-based landing lifts, using public facts and an injected clock."""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date
from functools import lru_cache
from zoneinfo import ZoneInfo

from .conditions import USED_CONDITIONS, normalize_conditions
from .util import parse_iso

_IRREGULAR_PLURALS = {"hoodies": "hoodie", "knives": "knife"}


def _words(value, path):
    if not isinstance(value, list) or not value or any(
            not isinstance(w, str) or not re.fullmatch(r"[\w -]+", w) or not w.strip() for w in value):
        raise ValueError(path + " must contain nonempty literal words or phrases")


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def validate(options):
    """Validate only the new sections; absent sections stay absent (OFF)."""
    if not isinstance(options, dict):
        raise ValueError("publish.front_page.appeal must be a mapping")
    path = "publish.front_page.appeal."
    if "want" in options:
        want = options["want"]
        if not isinstance(want, dict) or set(want) - {"enabled", "factor", "max_price", "groups", "exclude_words"}:
            raise ValueError(path + "want must contain supported settings only")
        if type(want.get("enabled", False)) is not bool:
            raise ValueError(path + "want.enabled must be a boolean")
        for key, default in (("factor", 1.2), ("max_price", 50)):
            value = want.get(key, default)
            if not _finite(value) or value < (1 if key == "factor" else 0):
                raise ValueError(path + "want." + key + " must be finite and in range")
        groups = want.get("groups", {})
        if not isinstance(groups, dict) or any(not isinstance(g, str) or not g.strip() for g in groups):
            raise ValueError(path + "want.groups must be a mapping of group names to words")
        for words in groups.values():
            _words(words, path + "want.groups")
        if "exclude_words" in want:
            _words(want["exclude_words"], path + "want.exclude_words")
    if "season" in options:
        windows = options["season"]
        if not isinstance(windows, list):
            raise ValueError(path + "season must be a list")
        names = set()
        for window in windows:
            if not isinstance(window, dict) or set(window) - {"name", "from", "to", "words", "factor"}:
                raise ValueError(path + "season windows must contain supported settings only")
            for key in ("from", "to"):
                stamp = window.get(key)
                try:
                    if not isinstance(stamp, str) or not re.fullmatch(r"\d{2}-\d{2}", stamp):
                        raise ValueError
                    date.fromisoformat("2000-" + stamp)
                except ValueError:
                    raise ValueError(path + "season dates must be MM-DD") from None
            name = window.get("name", window["from"] + ".." + window["to"])
            if not isinstance(name, str) or not name.strip() or name in names:
                raise ValueError(path + "season names must be nonempty and unique")
            names.add(name)
            _words(window.get("words"), path + "season.words")
            value = window.get("factor")
            if not _finite(value) or not 1 <= value <= 1.15:
                raise ValueError(path + "season.factor must be in 1..1.15")


def _tokens(text):
    return re.findall(r"\w+", str(text or "").casefold())


def _singular(word):
    # Literal tokens only: watchband never matches watch, brass never bra.
    if word in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[word]
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "xes", "sses")):
        return word[:-2]
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us")) else word


@lru_cache(maxsize=1024)
def _phrase(word):
    return tuple(_singular(w) for w in _tokens(word))


def matches(title, words):
    tokens = tuple(_singular(w) for w in _tokens(title))
    return any(phrase and any(tokens[i:i + len(phrase)] == phrase for i in range(len(tokens)))
               for phrase in map(_phrase, words))


def active(options):
    return bool((options.get("want") or {}).get("enabled") or options.get("season"))


def blocked(deal, options):
    return active(options) and matches(deal.get("item"), (options.get("want") or {}).get("exclude_words", []))


def groups(deal, options):
    want = options.get("want") or {}
    return [name for name, words in want.get("groups", {}).items() if matches(deal.get("item"), words)]


def is_allure(deal, options):
    want = options.get("want") or {}
    return bool(want.get("enabled") and matches(deal.get("item"), want.get("groups", {}).get("allure", [])))


def signals(deal, cfg, now):
    """Return eligible groups/windows and their strongest lift, never a product."""
    from .rank import niche_reason
    from .verify import is_amazon_deal, price_confirmation_recent
    from . import checkout_prices

    options = cfg.front_page.get("appeal") or {}
    want = options.get("want") or {}
    empty = ([], [], 1.0)
    if not active(options) or blocked(deal, options) or niche_reason(deal) \
            or USED_CONDITIONS & set(normalize_conditions(deal.get("conditions"))) \
            or deal.get("status", "alive") != "alive" or deal.get("locked") \
            or deal.get("price_unconfirmed") or deal.get("price_aged"):
        return empty
    price = deal.get("final_price")
    if type(price) not in (int, float) or price < 0 or price > want.get("max_price", 50):
        return empty
    if not math.isfinite(price):
        return empty
    if is_amazon_deal(deal):
        if checkout_prices.enabled(cfg) and checkout_prices.kind(deal):
            if checkout_prices.pick_evidence(deal, cfg, now)[0]:
                return empty
        elif not price_confirmation_recent(deal, now, cfg.keepa_pick_max_unconfirmed_h):
            return empty
    else:
        stamps = [parse_iso(deal.get(k) or "") for k in ("price_published_at", "price_rechecked_at")]
        stamps = [at for at in stamps if at is not None] or [parse_iso(deal.get("first_seen_at") or "")]
        maximum = (cfg.front_page.get("top_picks") or {}).get("non_amazon_max_age_h", 24)
        if not any(at is not None and 0 <= (now - at).total_seconds() <= maximum * 3600 for at in stamps):
            return empty
    found = groups(deal, options) if want.get("enabled") else []
    factor = float(want.get("factor", 1.2)) if found else 1.0
    today = now.astimezone(ZoneInfo("America/New_York")).strftime("%m-%d")
    seasonal = []
    for window in options.get("season", []):
        start, end = window["from"], window["to"]
        within = start <= today <= end if start <= end else today >= start or today <= end
        if within and matches(deal.get("item"), window["words"]):
            if window["factor"] > 1:
                seasonal.append(window.get("name", start + ".." + end))
            factor = max(factor, window["factor"])
    return found, seasonal, factor


def capped_lift(factors, cap, *, demand=1.0):
    """Share headroom with other soft lifts; stronger sales/click signals win."""
    return min(math.prod(factors), max(cap, demand))


def space_allure(rows, options):
    """All only: six ordinary cards first, then at most one allure in six.

    Sparse overflow remains searchable/on its category page, without unsafe
    placements on the ad landing page. Never mutate rows or their pick numbers.
    """
    if not (options.get("want") or {}).get("enabled"):
        return rows
    out, pending, last, live = [], [], -6, 0
    for row in rows:
        if row.get("status") == "dead":
            out.append(row)
            continue
        if row.get("allure") or is_allure(row, options):
            pending.append(row)
            continue
        out.append(row)
        live += 1
        if pending and live >= 6 and live - last >= 6:
            out.append(pending.pop(0))
            last = live
            live += 1
    if pending and live >= 6 and live - last >= 6:
        out.append(pending[0])
    return out


def summary(rows, cfg, now):
    from .rank import everyday_price_factor, popular_affordable_factor

    options = cfg.front_page.get("appeal") or {}
    everyday = cfg.front_page.get("everyday_price") or {}
    popular = cfg.front_page.get("popular_affordable") or {}
    want, season = Counter(), Counter()
    for row in rows:
        found, windows, factor = signals(row, cfg, now)
        if factor > 1:
            demand = popular_affordable_factor(row, cfg, now) if popular.get("enabled") else 1.0
            price = everyday_price_factor(row, everyday) if everyday.get("enabled") else 1.0
            if capped_lift([demand, price, factor], (options.get("want") or {}).get("factor", 1.2),
                           demand=demand) <= demand * price:
                continue
            if (options.get("want") or {}).get("factor", 1.2) > 1:
                want.update(found)
            season.update(windows)
    return {"want": dict(sorted(want.items())), "season": dict(sorted(season.items()))}


ALLURE_JS = '''  function spaceAllure(rows) {
    var pending = [], out = [], last = -6, live = 0;
    rows.forEach(function(d) {
      if (d.status === "dead") { out.push(d); return; }
      if (d.allure) { pending.push(d); return; }
      out.push(d);
      live++;
      if (pending.length && live >= 6 && live - last >= 6) {
        out.push(pending.shift()); last = live; live++;
      }
    });
    if (pending.length && live >= 6 && live - last >= 6) out.push(pending[0]);
    var visible = [], lastAllure = -6, nextGone = 8;
    placeGone(out).forEach(function(d) {
      if (d.click_price_unknown && visible.length < 120) return;
      if (d.allure && (visible.length < 6 || visible.length - lastAllure < 6)) return;
      if (d.status === "dead" && visible.length < nextGone) return;
      if (d.allure) lastAllure = visible.length;
      if (d.status === "dead") nextGone = visible.length + 8;
      visible.push(d);
    });
    return visible;
  }
'''


def template(html, options):
    if not (options.get("want") or {}).get("enabled"):
        return html
    html = html.replace("  function storeHue(", ALLURE_JS + "\n  function storeHue(", 1)
    html = html.replace('return d.status === "alive" && Date.parse(d.spotlight.until)',
                        'return !(state.catTab < 2 && !searchText() && d.allure) && d.status === "alive" && Date.parse(d.spotlight.until)', 1)
    return html.replace("    browseRows = spotlight.concat(rows);",
                        '    if (state.catTab < 2 && !document.getElementById("q").value.trim()) rows = spaceAllure(rows);\n    browseRows = spotlight.concat(rows);', 1)
