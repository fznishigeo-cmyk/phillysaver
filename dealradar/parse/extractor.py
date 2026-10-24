"""Post cleaning, evidence verification, expiry resolution, and the parse loop.

Evidence is THE non-negotiable of the parse layer: a parse whose price quote is
not verbatim in the text sent to the model MUST be rejected — dropped and
counted, never guessed.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import math
import re
import threading
import time
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import util
from ..config import AppConfig
from ..errors import SpendCapReached
from ..models import ExtractedDeal, RawPost
from .schema import CATEGORIES, LLMDeal, to_extracted

log = logging.getLogger("dealradar.parse.extractor")

_WS_RUNS = re.compile(r"[ \t\r\f\v]+")
_NL_PAD = re.compile(r" ?\n ?")
_NL_RUNS = re.compile(r"\n{2,}")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_US_NUMERIC_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?\b")
_US_MONTH_DATE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b",
    re.IGNORECASE,
)


def clean_for_llm(post: RawPost, max_chars: int) -> str:
    """Whitespace-collapse post.text (preserving single newlines) and truncate
    at max_chars on a whitespace boundary (never mid-word)."""
    text = post.text or ""
    text = _WS_RUNS.sub(" ", text)
    text = _NL_PAD.sub("\n", text)
    text = _NL_RUNS.sub("\n", text)
    text = text.strip()
    if max_chars > 0 and len(text) > max_chars:
        cut = text[:max_chars]
        # Only backtrack when the cut would split a word in half.
        if not text[max_chars].isspace() and not cut[-1].isspace():
            last_space = max(cut.rfind(" "), cut.rfind("\n"))
            if last_space > 0:
                cut = cut[:last_space]
        text = cut.rstrip()
    return text


def _norm_ws(s: str) -> str:
    """Collapse all whitespace runs to single spaces — nothing else."""
    return " ".join(s.split())


def _number_candidates(v: float) -> set[str]:
    """Standard written forms of a number, with optional thousands commas."""
    cands = {f"{v:.2f}", f"{v:,.2f}", f"{v:g}", f"{v:,g}"}
    if float(v).is_integer():
        i = int(v)
        cands.add(str(i))
        cands.add(f"{i:,}")
    return cands


def _number_in_quote(value: float, nquote: str, text: str | None = None) -> bool:
    """True when `value` appears in the quote on DIGIT BOUNDARIES.

    A bare substring test would let 9.99 be "backed" by '19.99' (and 29 by
    '$29.99'), so each candidate form must not be preceded by a digit, '.' or
    ',' and must not be followed by a digit or a '.digit' continuation.
    """
    for cand in _number_candidates(value):
        pattern = re.compile(r"(?<![\d.,])" + re.escape(cand) + r"(?![.,]?\d)")
        if text is None:
            if pattern.search(nquote):
                return True
        else:
            for quote in re.finditer(re.escape(nquote), text):
                if any(match.end() <= quote.end() for match in
                       pattern.finditer(text, quote.start(), quote.end() + 2)):
                    return True
    return False


_NON_CHECKOUT_SUFFIX = re.compile(
    r"^\s*(?:%|percent\b|off\b|months?\b|"
    r"(?:/\s*|per\s+)(?:mo(?:nth)?s?|years?|weeks?|days?)\b|"
    r"kohl[’']?s\s+cash\b|gift[ -]?cards?\b|cash[ -]?back\b|"
    r"(?:store|statement)\s+credit\b|rewards?\b)", re.I)
_PER_UNIT_SUFFIX = re.compile(
    r"^\s*(?:/\s*|per\s+)(?:count|ct|units?|items?|oz|ounces?|lb|pounds?|"
    r"ml|g|kg|ea|packs?|pieces?|each)\b", re.I)
_NON_CHECKOUT_PREFIX = re.compile(
    r"\b(?:was|reg(?:ular(?:ly)?)?\.?|originally|original(?=\s+price\b|\s*[:=])|list|msrp)"
    r"(?:\s+price)?\s*[:=]?\s*\$?\s*$|"
    r"(?:^|[,;:(\[.!?–—-])\s*original\s*\$?\s*$|"
    r"\boff\s+(?:(?:on\s+)?(?:orders?|purchases?)\s+(?:(?:of|over)\s+)?)?\$?\s*$|"
    r"\b(?:spend|minimum|orders?\s+(?:of|over)|free\s+(?:shipping\s+)?over)"
    r"\s*\$?\s*$", re.I)
_RANGE_PREFIX = re.compile(
    r"\b(?:from|as\s+low\s+as|starting(?:\s+at)?|under|up\s+to)\s*\$?\s*$|"
    r"(?<![\w.,])\d[\d,.]*\s*(?:[-–—]|to)\s*\$?\s*$", re.I)
_RANGE_SUFFIX = re.compile(
    r"^\s*(?:[-–—]|to\b)\s*\$?\s*\d[\d,.]*(?![\w.,])", re.I)


def _checkout_number_in_quote(value: float, quote: str, text: str, *,
                              price_is_range: bool, per_unit: bool) -> bool:
    """Require one matching amount in checkout context, including outside a clipped quote."""
    for occurrence in re.finditer(re.escape(quote), text):
        for candidate in _number_candidates(value):
            pattern = re.compile(r"(?<![\d.,])" + re.escape(candidate) + r"(?![.,]?\d)")
            for match in pattern.finditer(text, occurrence.start(), occurrence.end() + 2):
                if match.end() > occurrence.end():
                    continue
                before, after = text[:match.start()], text[match.end():]
                if _NON_CHECKOUT_PREFIX.search(before) or _NON_CHECKOUT_SUFFIX.search(after):
                    continue
                # A quantity has no currency marker; "$25 for $50 worth" is a price.
                if not before.rstrip().endswith("$") and re.match(r"\s*for\s+\$", after, re.I):
                    continue
                # Bare "Original" may finish a product name. A following current
                # price label establishes comparison-price context instead.
                if re.search(r"\boriginal\s*\$?\s*$", before, re.I) and re.match(
                        r"\s*[,;:(\[]*\s*(?:now|sale(?:\s+price)?)\s*[:=]?\s*\$?\s*\d",
                        after, re.I):
                    continue
                if not per_unit and _PER_UNIT_SUFFIX.search(after):
                    continue
                if not price_is_range and (_RANGE_PREFIX.search(before) or _RANGE_SUFFIX.search(after)):
                    continue
                return True
    return False


def verify_evidence(deal: ExtractedDeal, text: str) -> tuple[bool, str]:
    """Verify every non-null price/pct is backed by a verbatim quote.

    Both text and quote are normalized ONLY by collapsing whitespace runs to
    single spaces. For each filled value, its quote must be non-empty, must
    appear as a substring of the normalized text, and must contain the number
    itself on digit boundaries. A pct quote must additionally carry a '%' (or
    the word 'percent'), so a dollar amount can never back a percentage. A
    range deal's upper bound is a published price and is checked like one.
    """
    ntext = _norm_ws(text)
    per_unit = all((unit or "").strip().lower() == "per_unit" for unit in
                   (deal.regular_price_unit, deal.final_price_unit))
    checks = [
        ("regular_price", deal.regular_price, deal.evidence.regular_price_quote),
        ("final_price", deal.final_price, deal.evidence.final_price_quote),
        ("pct_off", deal.pct_off, deal.evidence.pct_quote),
    ]
    if deal.price_is_range and deal.price_upper_bound is not None:
        checks.append(("price_upper_bound", deal.price_upper_bound,
                       deal.evidence.price_upper_bound_quote))
    for label, value, quote in checks:
        if value is None:
            continue
        if not math.isfinite(value):
            return False, f"{label}: value is not a finite number"
        nquote = _norm_ws(quote)
        if not nquote:
            return False, f"{label}={value:g}: evidence quote is empty"
        if nquote not in ntext:
            return False, f"{label}: quote {nquote!r} not found verbatim in text"
        if not _number_in_quote(value, nquote, ntext):
            return False, (f"{label}: quote {nquote!r} does not contain the "
                           f"number {value:g} on digit boundaries")
        if label in {"final_price", "price_upper_bound"} and not _checkout_number_in_quote(
                value, nquote, ntext, price_is_range=deal.price_is_range, per_unit=per_unit):
            return False, f"{label}: number has no checkout-price context (or needs a range flag)"
        if label == "pct_off" and "%" not in nquote and "percent" not in nquote.lower():
            return False, f"pct_off: quote {nquote!r} carries no %% sign"
    from ..savings import parse_shipping, parse_stack_step

    requirements = {
        "prime_only": r"\bprime\b",
        "membership_only": r"\bmember(?:ship|s)?\b|\b(?:join|enroll)\b",
        "subscribe_save": r"subscribe\s*(?:&|and|\+)\s*save|\bs\s*&\s*s\b",
        "in_cart": r"\bcart\b|at checkout",
        "new_customer": r"\bnew\s+(?:customers?|users?|members?|accounts?)\b|first[ -](?:order|purchase)",
        "targeted": r"targeted|select(?:ed)?\s+(?:accounts?|customers?|users?|members?)|check your account",
        "multi_buy": r"\b(?:buy|purchase)\s+(?:\d+|two|three|four|one)\b|\b(?:bogo|multi[ -]?buy)\b|on\s+\d+\s+select"
                     r"|\b\d+\s*(?:for|/)\s*\$",
    }
    for condition in deal.conditions:
        pattern = requirements.get(condition)
        if pattern and not re.search(pattern, ntext, re.I):
            return False, "conditions: requirement has no supporting wording"

    for code in deal.codes:
        # Checkout codes are case-insensitive; the code must still appear as written.
        if not code.strip() or not re.search(r"(?<![\w-])" + re.escape(code) + r"(?![\w-])", ntext, re.I):
            return False, "codes: code not found verbatim in text"
    if deal.deferred_value:
        quote = _norm_ws(deal.deferred_value)
        if (not quote or quote not in ntext or parse_stack_step(quote)["kind"] != "deferred"
                or any(not _number_in_quote(float(n.replace(',', '')), quote, ntext)
                       for n in re.findall(r"\d[\d,]*(?:\.\d+)?", quote))):
            return False, "deferred_value: missing verbatim reward wording"

    # These free-form facts are mathematical inputs too. Copying a number
    # from elsewhere in the post cannot establish a coupon or shipping cost.
    math_quotes = set()
    math_spans = []
    checkout_end = 0
    for label, values in (("stack_steps", deal.stack_steps), ("rebates", deal.rebates)):
        for value in values:
            quote = _norm_ws(value)
            if not quote or quote not in ntext:
                return False, f"{label}: input not found verbatim in text"
            step = parse_stack_step(quote)
            if step["value"] is not None:
                if any(not _number_in_quote(amount, quote, ntext) for amount in
                       (step["value"], step.get("min_spend")) if amount is not None):
                    return False, f"{label}: number is only part of the source amount"
                if quote in math_quotes:
                    return False, f"{label}: the same mathematical input is repeated"
                math_quotes.add(quote)
                checkout = label == "stack_steps" and step["kind"] in {"pct_off", "abs_off", "sale_price"}
                match = next((m for m in re.finditer(re.escape(quote), ntext)
                              if (not checkout or m.start() >= checkout_end)
                              and not any(m.start() < right and left < m.end()
                                          for left, right in math_spans)
                              and _number_in_quote(step["value"], quote,
                                                   ntext[max(0, m.start() - 1):m.end() + 2])), None)
                if match is None:
                    return False, f"{label}: overlapping evidence or unsupported step order"
                math_spans.append(match.span())
                if checkout:
                    checkout_end = match.end()
            if label == "rebates" and step["kind"] not in {"rebate_abs", "rebate_pct", "deferred"}:
                return False, "rebates: amount has no verbatim rebate wording"
            if step["kind"] == "pct_off" and not re.search(
                    r"\boff\b|discount|coupon|\bsave\b|subscribe|\bs&s\b", quote, re.I):
                return False, "stack_steps: percentage has no verbatim discount wording"
    if deal.shipping_cost_or_threshold:
        quote = _norm_ws(deal.evidence.shipping_quote or deal.shipping_cost_or_threshold)
        shipping = parse_shipping(quote)
        if (not quote or quote not in ntext
                or not re.search(r"shipping|delivery|postage", quote, re.I)
                or shipping != parse_shipping(deal.shipping_cost_or_threshold)
                or any(value is not None and not (value == 0 and re.search(r"\bfree\b", quote, re.I))
                       and not _number_in_quote(value, quote, ntext) for value in shipping)):
            return False, "shipping: missing or mismatched verbatim shipping evidence"
    if deal.deal_type:
        quote = _norm_ws(deal.evidence.deal_type_quote)
        patterns = {
            "bogo": r"\bbogo\s+free\b|buy\s+(?:1|one)\s+get\s+(?:1|one)\s+free",
            "b2g1": r"buy\s+(?:2|two)\s+get\s+(?:1|one)\s+free",
            "b2g3": r"buy\s+(?:2|two)\s+get\s+(?:3|three)\s+free",
            "b1g1_50": r"buy\s+(?:1|one)\s+get\s+(?:1|one)\s+50\s*%\s+off",
            "free_after_rebate": r"free\s+after\s+(?:mail[ -]?in\s+)?rebate",
            "moneymaker": r"\bmoney[ -]?maker\b",
            "penny": (r"\bpenny\s+(?:deal|price|sale)\b|"
                      r"\b(?:only|just|for|costs?)\s+(?:(?:a|one|1)\s+)?penny\b|"
                      r"\$0\.01(?!\d)|\b1\s*(?:cent|¢)"),
        }
        pattern = patterns.get(deal.deal_type)
        if not quote or quote not in ntext or not pattern or not re.search(pattern, quote, re.I):
            return False, "deal_type: missing or mismatched verbatim evidence"
    return True, ""


def resolve_expires(raw: str, tz_name: str, now: datetime) -> str:
    """Resolve expiry wording to an ISO UTC instant, or '' when unresolvable.

    '' => ''; 'today'/'tonight'/'ends today' => util.end_of_day(tz_name, now);
    a fully ISO-parseable string => that instant (naive => tz_name); common US
    dates (10/12, 10/12/2026, Oct 12) => end of that day in tz_name (a date
    without a year uses the current year in tz_name); anything else => ''.
    """
    s = (raw or "").strip()
    if not s:
        return ""

    low = s.lower()
    if "today" in low or "tonight" in low:
        return util.iso(util.end_of_day(tz_name, now))

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        # A date-only expiry means "through that day": resolve to END of day
        # in the source tz, consistent with the US-format date paths below.
        y, mo, d = (int(x) for x in s.split("-"))
        return _end_of_date(y, mo, d, ZoneInfo(tz_name))
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        pass
    else:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(tz_name))
        return util.iso(dt)

    tz = ZoneInfo(tz_name)
    m = _US_NUMERIC_DATE.search(s)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year_s = m.group(3)
        year = (2000 + int(year_s) if year_s and len(year_s) == 2
                else int(year_s) if year_s
                else now.astimezone(tz).year)
        return _end_of_date(year, month, day, tz)
    m = _US_MONTH_DATE.search(s)
    if m:
        month = _MONTHS[m.group(1).lower()]
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else now.astimezone(tz).year
        return _end_of_date(year, month, day, tz)
    return ""


def _end_of_date(year: int, month: int, day: int, tz: ZoneInfo) -> str:
    try:
        eod = datetime(year, month, day, 23, 59, 59, tzinfo=tz)
    except ValueError:
        return ""
    return util.iso(eod)


_DEAL_SMELL = re.compile(
    r"(\$\s?\d|\d+\s?(?:%|percent\b))", re.IGNORECASE)
_DEAL_WORDS = re.compile(
    r"\b(deal|sale|off|coupon|promo|clearance|discount|save|free|was|reg(?:ular(?:ly)?)?|list|msrp)\b",
    re.IGNORECASE)
_FREE_OFFER = re.compile(
    r"\bfree\b(?!\s+(?:shipping|delivery|returns?|trials?)\b)|\$\s*0(?:\.0+)?(?![\d,]|\.\d)",
    re.IGNORECASE)
_POST_MONEY = re.compile(
    r"\$\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?![\d,]|\.\d)")
_POST_PCT = re.compile(r"(?<![\d.,])(\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.I)
_REGULAR_LABEL = re.compile(
    r"\b(?:was|reg(?:ular(?:ly)?)?\.?|list|msrp)\s*(?:price\s*)?[:=]?\s*$", re.I)
_FINAL_LABEL = re.compile(r"\b(?:now|sale|only|just|for)\s*(?:price\s*)?[:=]?\s*$", re.I)
_EXTRA_SAVINGS = re.compile(
    r"\b(?:coupons?|codes?|promo|clip\w*|extra|stack\w*|rebates?|subscri\w*|s\s*&\s*s)\b", re.I)
_AMBIGUOUS_OFFER = re.compile(
    r"\b(?:range|roundup|bogo|bundles?|gift\s+cards?|up\s+to|as\s+low\s+as|"
    r"starting\s+(?:at|from)|buy\s+\d+|more\s+deals)\b", re.I)
_SENTENCE_BREAK = re.compile(r"\n|(?<!\breg)[.!?](?:\s+|$)", re.I)


def _smells_like_deal(text: str) -> bool:
    """Money/percent figures with deal vocabulary, or a free offer."""
    return ((bool(_DEAL_SMELL.search(text)) and bool(_DEAL_WORDS.search(text)))
            or bool(_FREE_OFFER.search(text)))


def _price_pair_pct(first: float, second: float) -> float | None:
    from ..savings import compute_savings

    if not all(math.isfinite(v) for v in (first, second)):
        return None
    return compute_savings(ExtractedDeal(
        item="", store="", source_name="", source_url="",
        regular_price=max(first, second), final_price=min(first, second),
    )).pct_at_checkout


def _can_clear_floor(text: str, floor: float) -> bool:
    """A stated signal for a useful second read; the publish gate still decides."""
    if _FREE_OFFER.search(text) or any(
            floor <= float(m.group(1)) <= 100 for m in _POST_PCT.finditer(text)):
        return True
    for sentence in _SENTENCE_BREAK.split(text):
        prices = [float(m.group(1).replace(",", "")) for m in _POST_MONEY.finditer(sentence)]
        for i, price in enumerate(prices):
            for other in prices[i + 1:]:
                pct = _price_pair_pct(price, other)
                if pct is not None and pct >= floor:
                    return True
    return False


def _below_floor_post(text: str, floor: float) -> bool:
    """Only an unambiguous, single now/regular pair without extra savings."""
    if (_EXTRA_SAVINGS.search(text) or _AMBIGUOUS_OFFER.search(text)
            or _POST_PCT.search(text) or _FREE_OFFER.search(text)):
        return False
    prices = list(_POST_MONEY.finditer(text))
    if len(prices) != 2:
        return False
    if any((m.start() and text[m.start() - 1] in "-−")
           or text[m.end():m.end() + 1] in {".", ","} for m in prices):
        return False
    regular = [m for m in prices if _REGULAR_LABEL.search(text[:m.start()])]
    if len(regular) != 1:
        return False
    reg = regular[0]
    final = next(m for m in prices if m is not reg)
    # The bare sale price in "$45 (reg $50)" is also an explicit pair.
    label = _REGULAR_LABEL.search(text[:reg.start()])
    bare_pair = final.end() <= label.start() and bool(re.fullmatch(
        r"[\s,;(\[]*", text[final.end():label.start()]))
    if not _FINAL_LABEL.search(text[:final.start()]) and not bare_pair:
        return False
    if _SENTENCE_BREAK.search(text[min(reg.end(), final.end()):max(reg.start(), final.start())]):
        return False
    now_price, reg_price = (float(m.group(1).replace(",", "")) for m in (final, reg))
    if now_price > reg_price:
        return False
    pct = _price_pair_pct(now_price, reg_price)
    return pct is not None and pct < floor - 10


def prefilter_post_counts(posts: list[RawPost], cfg: AppConfig,
                          deadline: float | None = None) -> dict:
    """Observe every candidate before caps/deadlines, without saving post text."""
    bounded = cfg.bounded_prefilter["enabled"]
    if cfg.prefilter_below_floor == "off" and not bounded:
        return {}
    floor = min(cfg.min_pct, cfg.site_show_from_pct)
    counts = {}
    for post in posts:
        if post.structured is not None or bounded and post.content_hash in counts:
            continue
        cleaned = clean_for_llm(post, cfg.max_chars_per_post)
        if bounded:
            from .bounded_prefilter import analyze

            analysis = analyze(cleaned, floor, cfg.bounded_prefilter, deadline)
            counts[post.content_hash] = {"bounded_prefilter": analysis.counters(),
                                         "bounded_second_opinion": analysis.second_opinion}
            below = analysis.state == "known" and analysis.below_floor
        else:
            below = _below_floor_post(cleaned, floor)
        if cfg.prefilter_below_floor != "off" and below:
            counts.setdefault(post.content_hash, {}).update({
                "extracted": 0, "evidence": 0, "prefilter_would_skip": True,
                "text_fingerprint": hashlib.sha256(cleaned.encode("utf-8")).hexdigest(),
                "evaluation_complete": False, "evaluation_gap": "not_parsed"})
    return counts


def prefilter_counts(posts: list[RawPost], post_counts: dict) -> dict:
    """Count candidate POSTS, including incomplete post-critic evaluations."""
    by_source = {}
    seen = set()
    bounded = Counter()
    for post in posts:
        if post.content_hash in seen:
            continue
        seen.add(post.content_hash)
        counts = post_counts.get(post.content_hash, {})
        bounded.update(counts.get("bounded_prefilter", {}))
        if not counts.get("prefilter_would_skip"):
            continue
        source = by_source.setdefault(post.source_name or "unknown", {
            "would_skip": 0, "would_skip_publishable": 0, "unevaluated": 0})
        source["would_skip"] += 1
        source["would_skip_publishable"] += int(bool(counts.get("prefilter_miss")))
        source["unevaluated"] += int(not counts.get("evaluation_complete", False))
    return {**{key: sum(s[key] for s in by_source.values()) for key in (
        "would_skip", "would_skip_publishable", "unevaluated")}, "by_source": by_source,
        **({"bounded_prefilter": dict(bounded)} if bounded else {})}


def _post_for_deals(post: RawPost, n_deals: int) -> RawPost:
    """A post-level image provably belongs to its deal only on a SINGLE-deal
    post; attached to a roundup it would paint one product's photo on every
    extracted deal. Multi-deal posts lose the image, nothing else."""
    if post.image_url and n_deals != 1:
        return dataclasses.replace(post, image_url="")
    return post


def parse_posts(
    posts: list[RawPost],
    parser,
    cfg: AppConfig,
    now: datetime,
    tz_by_source: dict[str, str],
    deadline: float | None = None,
    db=None,
    post_counts: dict | None = None,
) -> tuple[list[tuple[RawPost, ExtractedDeal]], dict]:
    """Parse API facts in code or read posts with the LLM. Verify evidence
    against the synthesized source text or the SAME cleaned model input.

    Stats: posts_sent (posts the parser was called on), deals_extracted (deals
    the LLM returned, pre-verification), deals_dropped_evidence (rejected by
    verify_evidence — dropped, never guessed), spend_usd, aborted_spend_cap,
    and processed_hashes — the content hashes of posts the parser actually
    handled. The PIPELINE records only those into posts_seen, so posts skipped
    by the max-posts cap, a spend-cap abort, or the run deadline stay
    unrecorded and are retried on the next run instead of being lost.
    ``deadline`` is a time.monotonic() value; crossing it stops the loop.
    ``structured_parse`` counts API posts, verified deals and evidence drops;
    ``reused_posts``/``reused_deals`` count cache reuse without paid calls.
    ``second_opinion`` measures paid calls and evidence yield; ``prefilter``
    measures candidates (the pipeline adds their post-gate publishable yield).
    ``parse_seconds`` times the loop, including settlement of submitted reads;
    ``max_in_flight`` counts the peak outstanding unique LLM reads.
    ``db`` supplies cached LLM facts and stored categories for ASIN reuse.
    """
    stats = {
        "posts_sent": 0,
        "deals_extracted": 0,
        "deals_dropped_evidence": 0,
        "second_opinions": 0,
        "second_opinion": {"calls": 0, "deals_extracted": 0, "deals_passed_evidence": 0},
        "reused_posts": 0,
        "reused_deals": 0,
        "prefilter": {},
        "spend_usd": 0.0,
        "aborted_spend_cap": False,
        "parse_seconds": 0.0,
        "max_in_flight": 0,
        "deadline_hit": False,
        "processed_hashes": [],
        "post_counts": (post_counts if post_counts is not None else prefilter_post_counts(posts, cfg, deadline)),
        "structured_parse": {"posts": 0, "deals": 0, "dropped_evidence": 0},
    }
    observed_posts = posts
    if len(posts) > cfg.max_posts_per_run:
        dropped = len(posts) - cfg.max_posts_per_run
        log.warning(
            "max_posts_per_run=%d: dropping %d of %d posts this run",
            cfg.max_posts_per_run, dropped, len(posts),
        )
        posts = posts[: cfg.max_posts_per_run]

    pairs: list[tuple[RawPost, ExtractedDeal]] = []
    pcfg = getattr(parser, "cfg", None)
    so_model = getattr(pcfg, "second_opinion_model", "")
    so_cap = int(getattr(pcfg, "max_second_opinions_per_run", 0))
    workers = max(1, int(getattr(cfg, "parse_concurrency", 1)))
    lock = threading.Lock()

    def _extract(post: RawPost, cleaned: str) -> tuple[list, bool, bool]:
        """Worker: one post's LLM read(s); runs on a pool thread."""
        meta = {
            "source_name": post.source_name,
            "source_url": post.url,
            "title": post.title,
        }
        llm_deals = parser.extract(cleaned, meta, kind="extract")
        second_opinion = False
        if cfg.bounded_prefilter["enabled"]:
            eligible = stats["post_counts"][post.content_hash]["bounded_second_opinion"]
            # The initial request can consume the remaining headroom. Unknown
            # never bypasses that guard or the parser's existing reservations.
            eligible &= deadline is None or time.monotonic() + 2 * cfg.request_timeout_s + 2 <= deadline
        else:
            eligible = (not llm_deals and so_model and _smells_like_deal(cleaned)
                        and _can_clear_floor(cleaned, min(cfg.min_pct, cfg.site_show_from_pct)))
        if not llm_deals and so_model and eligible:
            with lock:
                take = stats["second_opinions"] < so_cap
                if take:
                    stats["second_opinions"] += 1
            if take:
                llm_deals = parser.extract(cleaned, meta, model=so_model,
                                           kind="second_opinion")
                second_opinion = True
                with lock:
                    stats["second_opinion"]["calls"] += 1
        return llm_deals, second_opinion, getattr(llm_deals, "complete", True)

    def _record(post: RawPost, cleaned: str, llm_deals: list, *, fingerprint: str,
                reused: bool, second_opinion: bool, skipped: bool, would_skip: bool,
                complete: bool) -> None:
        """Main thread: evidence-verify and collect one parsed post."""
        structured = post.structured is not None
        if structured:
            stats["structured_parse"]["posts"] += 1
        elif reused:
            stats["reused_posts"] += 1
        elif not skipped:
            stats["posts_sent"] += 1
        source = None
        if hasattr(parser, "extract_by_source"):
            source = parser.extract_by_source.setdefault(
                post.source_name or "unknown",
                {"calls": 0, "usd": 0.0, "deals": 0, "publishable": 0})
        if second_opinion:
            stats["second_opinion"]["deals_extracted"] += len(llm_deals)
        stats["processed_hashes"].append(post.content_hash)
        tz_name = tz_by_source.get(post.source_name) or cfg.timezone
        post = _post_for_deals(post, len(llm_deals))
        deals = [to_extracted(d, post, tz_name, now) for d in llm_deals]
        counts = {"extracted": len(deals), "evidence": 0}
        if cfg.bounded_prefilter["enabled"] and not structured:
            prior = stats["post_counts"][post.content_hash]
            counts.update({key: prior[key] for key in ("bounded_prefilter", "bounded_second_opinion")})
        if would_skip:
            counts["text_fingerprint"] = stats["post_counts"][post.content_hash]["text_fingerprint"]
            counts["prefilter_would_skip"] = True
            counts["evaluation_complete"] = complete
            if not complete:
                prior_gap = stats["post_counts"][post.content_hash].get("evaluation_gap")
                counts["evaluation_gap"] = ("skipped" if skipped else
                                            "legacy_cache" if prior_gap == "legacy_cache" else "extraction")
        stats["post_counts"][post.content_hash] = counts
        # Every deal the LLM read, before evidence drops any: a dropped item
        # still names a photo and still shares a link (dedupe, pipeline).
        post.parsed = [(deal.item, deal.product_url) for deal in deals]
        for deal in deals:
            stats["deals_extracted"] += 1
            ok, reason = verify_evidence(deal, cleaned)
            if not ok:
                counts["evidence"] += 1
                if would_skip:
                    counts["evaluation_complete"] = False
                    counts["evaluation_gap"] = "evidence"
                stats["deals_dropped_evidence"] += 1
                if structured:
                    stats["structured_parse"]["dropped_evidence"] += 1
                log.warning(
                    "dropped deal %r from %s (evidence check failed: %s)",
                    deal.item, post.source_name, reason,
                )
                continue
            pairs.append((post, deal))
            if source is not None:
                source["deals"] += 1
            if reused:
                stats["reused_deals"] += 1
            if second_opinion:
                stats["second_opinion"]["deals_passed_evidence"] += 1
            if structured:
                stats["structured_parse"]["deals"] += 1
        if second_opinion and db is not None:
            from ..ai_usage import record_second_opinion

            from ..util import now_utc

            record_second_opinion(db, post, fingerprint, now_utc(), len(deals), len(deals) - counts["evidence"])
        if (db is not None and complete and not structured and not reused and not skipped
                and not counts["evidence"]):
            # Rejected output stays transient; only a complete evidence-backed
            # parse (including a genuine empty answer) is saved as facts.
            db.cache_post_parse(fingerprint, [d.model_dump(mode="json") for d in llm_deals], now)

    def _abort_spend_cap(error: SpendCapReached) -> None:
        if not stats["aborted_spend_cap"]:
            log.warning("spend cap reached, aborting parse: %s", error)
        stats["aborted_spend_cap"] = True

    def _past_deadline() -> bool:
        return (deadline is not None
                and time.monotonic() + 2 * cfg.request_timeout_s + 2 > deadline)

    # Refill on any completed read, independently of the ordered recording
    # queue. Only unique LLM reads take slots; SQLite stays on this thread.
    # A cap/deadline stops submissions but every submitted read still settles.
    idx = 0
    reads: dict[str, Future | tuple[list, bool, bool]] = {}
    jobs = deque()
    in_flight: set[Future] = set()
    stopped = False
    parse_started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while idx < len(posts) or jobs:
            done = {read for read in in_flight if read.done()}
            in_flight.difference_update(done)
            for read in done:
                error = read.exception()
                if isinstance(error, SpendCapReached):
                    _abort_spend_cap(error)
            while jobs:
                post, cleaned, fingerprint, read, reused, skipped, would_skip = jobs[0]
                if isinstance(read, Future) and not read.done():
                    break
                jobs.popleft()
                try:
                    llm_deals, second_opinion, complete = read.result() if isinstance(read, Future) else read
                    _record(post, cleaned, llm_deals, fingerprint=fingerprint,
                            reused=reused, second_opinion=second_opinion and not reused,
                            skipped=skipped, would_skip=would_skip, complete=complete)
                except SpendCapReached as e:
                    _abort_spend_cap(e)

            if idx < len(posts) and not stopped and not stats["aborted_spend_cap"]:
                stopped = _past_deadline()
            if idx < len(posts) and not stopped and not stats["aborted_spend_cap"]:
                post = posts[idx]
                fingerprint = ""
                reused = False
                cleaned = (post.text if post.structured is not None
                           else clean_for_llm(post, cfg.max_chars_per_post))
                # Structured (API) posts cost nothing to read: never skip them.
                would_skip = bool(stats["post_counts"].get(post.content_hash, {}).get("prefilter_would_skip"))
                skipped = would_skip and cfg.prefilter_below_floor == "on"
                if skipped:
                    read = ([], False, False)
                elif post.structured is not None:
                    read = ([LLMDeal.model_validate(post.structured)], False, True)
                else:
                    fingerprint = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
                    if fingerprint in reads:
                        read, reused = reads[fingerprint], True
                    else:
                        cached = (db.cached_post_parse(fingerprint, now, cfg.post_parses_days)
                                  if db is not None else None)
                        if cached is not None:
                            complete = True
                            since = db.meta_get("ai_yield_since")
                            if would_skip and since:
                                parsed_at = db.conn.execute(
                                    "SELECT parsed_at FROM post_parses WHERE text_fingerprint=?",
                                    (fingerprint,)).fetchone()[0]
                                complete = parsed_at >= since
                                if not complete:
                                    stats["post_counts"][post.content_hash]["evaluation_gap"] = "legacy_cache"
                            read, reused = ([LLMDeal.model_validate(d) for d in cached], False, complete), True
                        else:
                            # A read can finish during recording/cache lookup.
                            # Observe it (especially a cap) before any refill.
                            if any(read.done() for read in in_flight):
                                continue
                            if len(in_flight) >= workers:
                                wait(in_flight, return_when=FIRST_COMPLETED)
                                continue
                            stopped = _past_deadline()
                            if stopped:
                                continue
                            read = pool.submit(_extract, post, cleaned)
                            in_flight.add(read)
                            stats["max_in_flight"] = max(stats["max_in_flight"], len(in_flight))
                        reads[fingerprint] = read
                jobs.append((post, cleaned, fingerprint, read, reused, skipped, would_skip))
                idx += 1
                continue
            if in_flight:
                wait(in_flight, return_when=FIRST_COMPLETED)
            else:
                break
    stats["parse_seconds"] = time.monotonic() - parse_started
    stats["deadline_hit"] = stopped
    if stopped:
        log.warning("run deadline reached: stopping parse with %d posts left"
                    " (they stay unrecorded and retry next run)",
                    len(posts) - len(stats["processed_hashes"]))

    processed = set(stats["processed_hashes"])
    capped = {p.content_hash for p in observed_posts[len(posts):]}
    for post in observed_posts:
        h = post.content_hash
        if h not in processed:
            counts = stats["post_counts"].setdefault(h, {"extracted": 0, "evidence": 0})
            counts["evaluation_gap"] = ("max_posts" if h in capped else
                                        "spend_cap" if stats["aborted_spend_cap"] else "deadline")
    stats["prefilter"] = prefilter_counts(observed_posts, stats["post_counts"])

    # Category reuse is independent of status and condition; only verified
    # structured deals need a title read. At most 60 titles, plus one retry
    # of just the missing indexes.
    from ..http_client import extract_asin
    from ..keepa_categories import category_from_name
    from .claude_client import categorize_titles

    categories = {}
    for stored in db.all_deals() if db is not None else []:
        asin = str(stored.get("asin") or extract_asin(
            stored.get("canonical_url") or "")).upper()
        cat = stored.get("category")
        # "other" is no answer: reusing it would file the ASIN there forever.
        if asin and cat in CATEGORIES and cat != "other":
            categories.setdefault(asin, cat)
    pending = {}
    for post, deal in pairs:
        if post.structured is None:
            continue
        asin = str(post.structured.get("asin") or extract_asin(deal.product_url)).upper()
        if post.structured.get("category") in CATEGORIES and post.structured["category"] != "other":
            # Amazon's Home & Kitchen root still permits a known kitchen title.
            deal.category = category_from_name(
                post.structured.get("keepa_category_name", ""), categories.get(asin, "")) or deal.category
            continue
        if asin in categories:
            deal.category = categories[asin]
        else:
            # Multiple sightings of one ASIN share a single title read.
            key = asin or post.content_hash
            pending.setdefault(key, []).append(deal)
    groups = list(pending.values())[:60]
    if groups and (deadline is None or time.monotonic() < deadline):
        def apply_category(i: int, category: str) -> None:
            for deal in groups[i]:
                deal.category = category

        try:
            chosen = categorize_titles(parser, [g[0].item for g in groups], on_result=apply_category)
            for i, category in enumerate(chosen):
                apply_category(i, category)
        except SpendCapReached:
            stats["aborted_spend_cap"] = True
            log.warning("spend cap reached in category call; keeping accepted categories")
    stats["spend_usd"] = float(getattr(parser, "spend_usd", 0.0))
    paid = getattr(parser, "spend_by_kind", {}).get("second_opinion")
    if paid is not None:
        stats["second_opinion"]["calls"] = paid["calls"]
    return pairs, stats


def recategorize_live_deals(db, parser, deadline: float | None = None) -> None:
    """One resumable category-only repair; cached Amazon roots, then titles.
    Accepted title answers are persisted before a retry can hit the cap.
    """
    from ..http_client import extract_asin
    from ..keepa_categories import NAMES_META, ROOTS_META, cached_meta, category_from_name
    from .claude_client import categorize_titles

    if db.meta_get("recategorized_v2") == "1":
        return
    progress = cached_meta(db, "recategorized_v2")
    done = set(progress.get("keys", []))
    deals = sorted(db.all_deals(status="alive"), key=lambda d: d["dedupe_key"])
    before = dict(sorted(Counter(d.get("category", "other") for d in deals).items()))
    names = cached_meta(db, NAMES_META)
    roots = cached_meta(db, ROOTS_META)

    def save(deal: dict, category: str) -> None:
        # Read the original payload: the DB's expanded row adds SQL columns,
        # and a repair must not persist any of those or change other facts.
        key = deal["dedupe_key"]
        row = db.conn.execute("SELECT payload FROM deals WHERE dedupe_key=?", (key,)).fetchone()
        payload = json.loads(row["payload"])
        if payload.get("category") != category:
            payload["category"] = category
            db.update_deal(key, payload=payload)
        done.add(key)
        db.meta_set("recategorized_v2", json.dumps({"keys": sorted(done)}))

    pending = []
    for deal in deals:
        if deal["dedupe_key"] in done:
            continue
        asin = str(deal.get("asin") or extract_asin(deal.get("canonical_url") or "")).upper()
        category = category_from_name(names.get(roots.get(asin, ""), ""), deal.get("category", ""))
        if asin and category:
            save(deal, category)
        else:
            pending.append(deal)
    for start in range(0, len(pending), 60):
        if parser is None or (deadline is not None and time.monotonic() >= deadline):
            break
        group = pending[start:start + 60]
        try:
            chosen = categorize_titles(parser, [d["item"] for d in group],
                                       on_result=lambda i, cat: save(group[i], cat))
        except SpendCapReached:
            log.info("recategorize v2: spend cap; unfinished titles resume next run")
            break
        for deal, category in zip(group, chosen):
            if deal["dedupe_key"] not in done:
                save(deal, category)
    after = dict(sorted(Counter(d.get("category", "other") for d in
                               db.all_deals(status="alive")).items()))
    log.info("recategorize v2: before=%s after=%s", before, after)
    if all(d["dedupe_key"] in done for d in deals):
        db.meta_set("recategorized_v2", "1")


# v2: rules gained jewelry/shoes/tools (2026-10-12); earlier "other" answers get one more read.
OTHER_TRIED_META = "recategorize_other_tried_v2"


def recategorize_other(db, parser, deadline: float | None = None, per_run: int = 120) -> int:
    """Live deals still filed under "other" get the cached Amazon root first,
    then one cheap title read each (at most per_run titles a run, inside the
    spend caps). A title is asked once: a real "other" answer stays put, and
    only accepted answers count as asked. Returns how many moved."""
    from ..http_client import extract_asin
    from ..keepa_categories import NAMES_META, ROOTS_META, cached_meta, category_from_name
    from .claude_client import categorize_titles

    if per_run <= 0:
        return 0
    deals = sorted((d for d in db.all_deals(status="alive")
                    if (d.get("category") or "other") == "other"),
                   key=lambda d: d["dedupe_key"])
    try:
        tried = set(json.loads(db.meta_get(OTHER_TRIED_META) or "[]"))
    except ValueError:
        tried = set()
    tried &= {d["dedupe_key"] for d in deals}  # keep the list small: live "other" only
    names, roots = cached_meta(db, NAMES_META), cached_meta(db, ROOTS_META)
    moved = 0

    def save(deal: dict, category: str) -> None:
        nonlocal moved
        key = deal["dedupe_key"]
        if category in CATEGORIES and category != "other":
            row = db.conn.execute("SELECT payload FROM deals WHERE dedupe_key=?", (key,)).fetchone()
            payload = json.loads(row["payload"])
            payload["category"] = category
            db.update_deal(key, payload=payload)
            moved += 1
        else:
            tried.add(key)  # a real "other": never asked again

    pending = []
    for deal in deals:
        if deal["dedupe_key"] in tried:
            continue
        asin = str(deal.get("asin") or extract_asin(deal.get("canonical_url") or "")).upper()
        category = category_from_name(names.get(roots.get(asin, ""), "")) if asin else ""
        if category:
            save(deal, category)
        else:
            pending.append(deal)
    pending = pending[:per_run]
    for start in range(0, len(pending), 60):
        if parser is None or (deadline is not None and time.monotonic() >= deadline):
            break
        group = pending[start:start + 60]
        try:
            categorize_titles(parser, [d["item"] for d in group],
                              on_result=lambda i, cat: save(group[i], cat))
        except SpendCapReached:
            log.info("recategorize other: spend cap; the rest resume next run")
            break
    db.meta_set(OTHER_TRIED_META, json.dumps(sorted(tried)))
    if moved:
        log.info("recategorize other: %d of %d live deals moved out of other", moved, len(deals))
    return moved
