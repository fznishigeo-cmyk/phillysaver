"""Shared helpers: defensive date parsing, HTML->text, item normalization, time."""

from __future__ import annotations

import calendar
import html as html_lib
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

#: Hosts whose ToS pin us to their OFFICIAL feeds: code-level policy, not
#: configuration. No HTML page of these hosts is ever fetched — no article
#: enrichment, no feed autodiscovery, no probing — regardless of what
#: sources.yaml says.
OFFICIAL_FEED_ONLY_HOSTS: tuple[str, ...] = ("slickdeals.net",)


def official_feed_only(url: str) -> bool:
    """True when `url` is on a host where only official feeds may be fetched."""
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h)
               for h in OFFICIAL_FEED_ONLY_HOSTS)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else ""


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_feed_date(value, source_tz: str, now: datetime) -> datetime | None:
    """Defensive feed-date parsing.

    Accepts a feedparser struct_time, an ISO/RFC string, or None.
    Missing tz -> interpreted in the source's configured tz.
    Future dates -> clamped to `now`. Unparseable -> None.
    """
    dt: datetime | None = None
    tzinfo = ZoneInfo(source_tz)

    if value is None:
        return None
    if hasattr(value, "tm_year"):  # time.struct_time from feedparser (already UTC)
        try:
            dt = datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
        except (OverflowError, ValueError, OSError):
            return None
    elif isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        s = value.strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(s)
            except (TypeError, ValueError):
                return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tzinfo)
    dt = dt.astimezone(timezone.utc)
    if dt > now:
        dt = now
    return dt


def end_of_day(tz_name: str, ref: datetime) -> datetime:
    """'ends today' -> end of day in the source's tz, returned as UTC."""
    local = ref.astimezone(ZoneInfo(tz_name))
    eod = local.replace(hour=23, minute=59, second=59, microsecond=0)
    return eod.astimezone(timezone.utc)


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "iframe"}
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
              "section", "article", "ul", "ol", "table", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")
        elif tag == "a" and not self._skip_depth:
            # Keep link targets: deal posts carry the PRODUCT URL in their
            # anchors, and dropping it leaves deals with nothing to link to.
            href = next((v for k, v in attrs if k == "href"), "") or ""
            href = href.strip()
            self._hrefs.append(
                href if href.startswith(("http://", "https://")) else "")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")
        elif tag == "a" and self._hrefs:
            href = self._hrefs.pop()
            if href:
                self.parts.append(f" ({href})")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    """Strip tags/scripts, unescape entities, collapse whitespace (newlines kept)."""
    if not markup:
        return ""
    if "<" not in markup:
        text = html_lib.unescape(markup)
    else:
        p = _TextExtractor()
        try:
            p.feed(markup)
            p.close()
        except Exception:
            text = re.sub(r"<[^>]+>", " ", markup)
        else:
            text = "".join(p.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


_SIZE_COLOR_TOKENS = re.compile(
    r"\b(\d+(\.\d+)?\s?(oz|ounce|lb|lbs|ct|count|pack|pk|pc|pcs|ml|l|g|kg|inch|in|ft|\"|')"
    r"|xxs|xs|s|m|l|xl|xxl|xxxl|small|medium|large"
    r"|black|white|red|blue|green|yellow|pink|purple|gray|grey|silver|gold|navy|beige|brown|teal)\b",
    re.IGNORECASE,
)


def normalize_item(name: str) -> str:
    """Lowercase, strip sizes/colors/punctuation, token-sort. Dedupe key material."""
    s = name.lower()
    s = _SIZE_COLOR_TOKENS.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    tokens = sorted(t for t in s.split() if t)
    return " ".join(tokens)


_ITEM_QUALIFIERS = frozenset("pro max plus ultra mini lite air se".split())
_ITEM_UNITS = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*-?\s*"
    r"(ct|count|packs?|pk|pcs?|pieces?|qt|quarts?|oz|ounces?|ml|l|liters?|"
    r"g|grams?|kg|lb|lbs|pounds?|inch(?:es)?|in|ft|feet|[kmgt]b)\b")


def _item_text(name: str) -> str:
    """Canonical unit spellings, without changing persisted name keys."""
    def unit(match):
        number, label = match.groups()
        if label in {"ct", "count", "pack", "packs", "pk", "pc", "pcs", "piece", "pieces"}:
            label = "ct"
        elif label in {"quart", "quarts"}:
            label = "qt"
        elif label in {"ounce", "ounces"}:
            label = "oz"
        elif label in {"liter", "liters"}:
            label = "l"
        elif label in {"gram", "grams"}:
            label = "g"
        elif label in {"lbs", "pound", "pounds"}:
            label = "lb"
        elif label in {"inch", "inches"}:
            label = "in"
        elif label == "feet":
            label = "ft"
        return number + label

    text = re.sub(r"\$\s?\d[\d,]*(?:\.\d+)?|\d+(?:\.\d+)?\s?%", " ", name.lower())
    text = re.sub(r"\b(?:packs?|pk)\s+of\s+(\d+)\b", r"\1ct", text)
    return _ITEM_UNITS.sub(unit, text)


def item_identity(name: str) -> frozenset[str]:
    """Stable suffix material for conflicting legacy name keys, not a veto."""
    words = re.findall(r"[a-z0-9]+(?:\.[a-z0-9]+)*", _item_text(name))
    return frozenset(word for word in words
                     if word in _ITEM_QUALIFIERS or any(char.isdigit() for char in word))


_ITEM_ORDINAL = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b(?:\s+(?:generation|gen)\b)?")
_ITEM_GENERATION = re.compile(r"\b(?:gen(?:eration)?\s+(\d+)|(\d+)\s+gen(?:eration)?)\b")


def _item_facts(name: str):
    text = _item_text(re.sub(r"\b\d+-in-\d+\b", " ", name.lower()))
    units = {}
    for match in _ITEM_UNITS.finditer(text):
        number, unit = match.groups()
        kind = "ct" if unit == "ct" else "capacity" if unit.endswith("b") and unit != "lb" else "size"
        units.setdefault(kind, set()).add(number + unit)
        units.setdefault("unit:" + unit, set()).add(number)
    text = _ITEM_UNITS.sub(" ", text)
    generations = set(_ITEM_ORDINAL.findall(text))
    text = _ITEM_ORDINAL.sub(" ", text)
    generations.update(a or b for a, b in _ITEM_GENERATION.findall(text))
    text = _ITEM_GENERATION.sub(" ", text)
    # Only this known qualified line treats a bare number as a generation.
    # A base AirPods Pro title is distinct from Pro 2; ordinal/gen aliases
    # supply that generation too. Unattached bare numbers never veto.
    airpods = re.search(r"\bairpods\s+pro\b(?:\s+(\d{1,2})\b)?", text)
    if airpods and airpods[1]:
        generations.add(airpods[1])
    words = re.findall(r"[a-z0-9]+", text)
    models = {word for word in words if
              (any(c.isalpha() for c in word) and any(c.isdigit() for c in word))
              or (word.isdigit() and len(word) >= 4 and not 1900 <= int(word) <= 2099)}
    # iPhone's following number names its model, unlike an unattached number.
    models.update("iphone" + number for number in re.findall(r"\biphone\s+(\d{1,2})\b", text))
    qualifiers = {}
    for previous, word in zip(words, words[1:]):
        if word in _ITEM_QUALIFIERS:
            qualifiers.setdefault(previous, set()).add(word)
    return units, models, generations, bool(airpods), words, qualifiers


def item_identity_conflict(first: str, second: str) -> bool:
    """Veto conflicting stated facts; extra facts are normally unknowns.

    Units compare count/capacity/size facts, model families must agree, and
    added qualifiers compare only after a shared preceding token. Ordinals are
    generations; only AirPods Pro's known line treats generation absence as
    a base model. Years, marketing numbers and missing counts do not veto.
    """
    au, am, ag, ap, aw, aq = _item_facts(first)
    bu, bm, bg, bp, bw, bq = _item_facts(second)
    if any(au[k].isdisjoint(bu[k]) for k in au.keys() & bu.keys()):
        return True
    if am and bm and am.isdisjoint(bm):
        return True
    # A shared spec such as 1080p cannot hide C920 versus C922. Compare each
    # stated model family as well as the full set of alphanumeric tokens.
    def model_families(models):
        families = {}
        for model in models:
            families.setdefault(re.sub(r"\d+", "#", model), set()).add(model)
        return families

    af, bf = model_families(am), model_families(bm)
    if any(af[k].isdisjoint(bf[k]) for k in af.keys() & bf.keys()):
        return True
    if ag and bg and ag.isdisjoint(bg):
        return True
    if ap and bp and bool(ag) != bool(bg):
        return True
    changed = (set(aw) ^ set(bw)) & _ITEM_QUALIFIERS
    return any(anchor in aw and anchor in bw
               and (aq.get(anchor, set()) ^ bq.get(anchor, set())) & changed
               for anchor in aq.keys() | bq.keys())


def item_model_match(first: str, second: str) -> bool:
    """An expanded title can name the same brand/model and product type.

    This supplements fuzzy wording, never the conflict veto. A model in an
    accessory title is not the product itself; comma suffixes are specs.
    """
    _, am, ag, ap, aw, _ = _item_facts(first)
    _, bm, bg, bp, bw, _ = _item_facts(second)
    if not aw or not bw or aw[0] != bw[0]:
        return False
    accessories = {"case", "cover", "replacement", "adapter", "pads", "cushions", "bundle",
                   "for", "compatible", "fits", "display", "stand", "lighting", "lights"}
    if set(aw) & accessories != set(bw) & accessories:
        return False
    if ap and bp and ag and ag == bg:
        def generation_name(name):
            text = _ITEM_ORDINAL.sub(r"\1", name.lower())
            text = _ITEM_GENERATION.sub(lambda m: m[1] or m[2], text)
            text = re.sub(r"\bwith\s+usb[ -]?c\b", " ", text)
            return normalize_item(text)
        return generation_name(first) == generation_name(second)
    if not any(len(model) >= 4 for model in am & bm):
        return False
    # LEGO's set number identifies a set even when one title omits its name.
    if aw[0] == "lego" and any(model.isdigit() and len(model) == 5 for model in am & bm):
        return True

    def product_word(name):
        text = _ITEM_UNITS.sub(" ", _item_text(name.split(",", 1)[0]))
        text = re.sub(r"\b\d+(?:\.\d+)?[ -]?hours?\s+battery(?:\s+life)?\b", " ", text)
        text = _SIZE_COLOR_TOKENS.sub(" ", text)
        return [w for w in re.findall(r"[a-z0-9]+", text) if w.isalpha()]

    a, b = product_word(first), product_word(second)
    return bool(a and b and a[-1] == b[-1])


def hours_between(a: datetime, b: datetime) -> float:
    return abs((a - b).total_seconds()) / 3600.0


def clamp_window_24h(first_seen: datetime, now: datetime) -> bool:
    """True if first_seen is within the last 24 h."""
    return now - first_seen <= timedelta(hours=24)
