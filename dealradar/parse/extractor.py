"""Post cleaning, evidence verification, expiry resolution, and the parse loop.

Evidence is THE non-negotiable of the parse layer: a parse whose price quote is
not verbatim in the text sent to the model MUST be rejected — dropped and
counted, never guessed.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import util
from ..config import AppConfig
from ..errors import SpendCapReached
from ..models import ExtractedDeal, RawPost
from .schema import to_extracted

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


def verify_evidence(deal: ExtractedDeal, text: str) -> tuple[bool, str]:
    """Verify every non-null price/pct is backed by a verbatim quote.

    Both text and quote are normalized ONLY by collapsing whitespace runs to
    single spaces. For each filled value, its quote must be non-empty, must
    appear as a substring of the normalized text, and must contain the number
    itself in a standard written form (pct with or without the % sign).
    """
    ntext = _norm_ws(text)
    checks = (
        ("regular_price", deal.regular_price, deal.evidence.regular_price_quote),
        ("final_price", deal.final_price, deal.evidence.final_price_quote),
        ("pct_off", deal.pct_off, deal.evidence.pct_quote),
    )
    for label, value, quote in checks:
        if value is None:
            continue
        nquote = _norm_ws(quote)
        if not nquote:
            return False, f"{label}={value:g}: evidence quote is empty"
        if nquote not in ntext:
            return False, f"{label}: quote {nquote!r} not found verbatim in text"
        if not any(c in nquote for c in _number_candidates(value)):
            return False, f"{label}: quote {nquote!r} does not contain the number {value:g}"
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


def parse_posts(
    posts: list[RawPost],
    parser,
    cfg: AppConfig,
    now: datetime,
    tz_by_source: dict[str, str],
) -> tuple[list[tuple[RawPost, ExtractedDeal]], dict]:
    """Run the LLM parse over posts, verifying evidence against the SAME
    cleaned text sent to the model.

    Stats: posts_sent (posts the parser was called on), deals_extracted (deals
    the LLM returned, pre-verification), deals_dropped_evidence (rejected by
    verify_evidence — dropped, never guessed), spend_usd, aborted_spend_cap.
    """
    stats = {
        "posts_sent": 0,
        "deals_extracted": 0,
        "deals_dropped_evidence": 0,
        "spend_usd": 0.0,
        "aborted_spend_cap": False,
    }
    if len(posts) > cfg.max_posts_per_run:
        dropped = len(posts) - cfg.max_posts_per_run
        log.warning(
            "max_posts_per_run=%d: dropping %d of %d posts this run",
            cfg.max_posts_per_run, dropped, len(posts),
        )
        posts = posts[: cfg.max_posts_per_run]

    pairs: list[tuple[RawPost, ExtractedDeal]] = []
    for post in posts:
        cleaned = clean_for_llm(post, cfg.max_chars_per_post)
        meta = {
            "source_name": post.source_name,
            "source_url": post.url,
            "title": post.title,
        }
        try:
            llm_deals = parser.extract(cleaned, meta)
        except SpendCapReached as e:
            log.warning("spend cap reached, aborting parse: %s", e)
            stats["aborted_spend_cap"] = True
            break
        stats["posts_sent"] += 1
        tz_name = tz_by_source.get(post.source_name) or cfg.timezone
        for d in llm_deals:
            stats["deals_extracted"] += 1
            deal = to_extracted(d, post, tz_name, now)
            ok, reason = verify_evidence(deal, cleaned)
            if not ok:
                stats["deals_dropped_evidence"] += 1
                log.warning(
                    "dropped deal %r from %s (evidence check failed: %s)",
                    deal.item, post.source_name, reason,
                )
                continue
            pairs.append((post, deal))

    stats["spend_usd"] = float(getattr(parser, "spend_usd", 0.0))
    return pairs, stats
