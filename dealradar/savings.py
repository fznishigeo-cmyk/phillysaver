"""dealradar.savings — the pure-code savings engine.

The LLM (parse layer) extracts RAW VALUES ONLY: stated prices, stated
percentages, stack steps as text in stated order, shipping text, and
deal-type markers. Every percentage dealradar publishes is computed here,
deterministically, per SAVINGS_RULES.md. Nothing in this module guesses a
price: when there is no stated basis, the deal is excluded, never estimated.

Publication floors (min pct / min abs savings) are NOT applied here — the
engine reports the true numbers and the publish layer applies the floors.
"""

from __future__ import annotations

import logging
import math
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from . import models

logger = logging.getLogger("dealradar.savings")


def price_verdict(current: float | None, median: float | None,
                  lower_ratio: float = 0.95, higher_ratio: float = 1.05) -> str:
    """Compare a live price to our median; boundary prices are about usual."""
    if current is None or median is None or not all(
            math.isfinite(n) and n >= 0 for n in (current, median)):
        return ""
    lower, higher = median * lower_ratio, median * higher_ratio
    # Ignore floating-point noise at an exact monetary boundary.
    if current < lower and not math.isclose(current, lower, rel_tol=1e-12):
        return "Lower than usual"
    if current > higher and not math.isclose(current, higher, rel_tol=1e-12):
        return "Higher than usual"
    return "About usual"


def product_price_facts(observations: list[dict], offers: list[dict], now: datetime) -> dict:
    """Our own checks and distinct published deals; never infer a sale or a basis.

    Extremes/count/dates use all retained checks. The typical price and sale
    counts use 90 days; a median needs the chart's three checks over 48 hours.
    Older offer snapshots may lack a discount, so report its sample count too.
    """
    from .util import parse_iso

    now = now.astimezone(timezone.utc)
    since = now - timedelta(days=90)
    points = []
    for row in observations:
        at, price = parse_iso(row.get("observed_at")), row.get("price")
        if (at and at <= now and isinstance(price, (int, float))
                and math.isfinite(price) and price >= 0):
            points.append((at, price))
    points.sort()
    recent = [p for p in points if p[0] >= since]
    median = (float(statistics.median(p[1] for p in recent)) if len(recent) >= 3
              and recent[-1][0] - recent[0][0] >= timedelta(days=2) else None)
    sales = [o for o in offers if (at := parse_iso(o.get("date"))) and since <= at <= now]
    discounts = [o["discount"] for o in sales if isinstance(o.get("discount"), (int, float))
                 and math.isfinite(o["discount"]) and 0 <= o["discount"] <= 100]
    facts = {"checks": len(points), "median": median,
             "median_checks": len(recent), "median_since": recent[0][0] if recent else None,
             "median_90d": bool(recent and points[0][0] <= since and recent[0][0] <= since + timedelta(days=1)),
             "deals_90d": len(sales),
             "latest_deal": max((parse_iso(o["date"]) for o in sales), default=None),
             "average_discount": statistics.mean(discounts) if discounts else None,
             "discount_samples": len(discounts)}
    if points:
        low = min(points, key=lambda p: (p[1], p[0]))
        high = min(points, key=lambda p: (-p[1], p[0]))
        facts.update(low=low[1], low_at=low[0], high=high[1], high_at=high[0],
                     first=points[0][0], last=points[-1][0])
    return facts


def price_difference_pct(current: float | None, median: float | None) -> float | None:
    """Signed percentage below our median; a zero basis has no percentage."""
    if not price_verdict(current, median) or median <= 0:
        return None
    return (median - current) * 100 / median

# Rule 7: structural deal-type percentages (basis == the deal_type name).
DEAL_TYPE_PCT: dict[str, float] = {
    "bogo": 50.0,      # buy one get one free
    "b2g1": 33.3,      # buy two get one free
    "b2g3": 60.0,      # buy two get three free
    "b1g1_50": 25.0,   # buy one get one 50% off
}

# Rule 1: condition markers that mean the deal is not reliably available online.
_IN_STORE_MARKERS = ("in_store_only", "ymmv", "clearance_aisle")

_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_MONEY_RE = re.compile(r"\$\s*(\d+(?:\.\d+)?)")
_NUMBER_RE = re.compile(r"\$?\s*(\d+(?:\.\d+)?)")
_THRESH_WORD_RE = re.compile(r"\b(?:over|above|on)\b\s*\$?\s*(\d+(?:\.\d+)?)")
_THRESH_PLUS_RE = re.compile(r"\$?\s*(\d+(?:\.\d+)?)\s*\+")
_REBATE_RE = re.compile(r"\brebates?\b|\bmir\b|mail[\s-]?in")
# Rule 9: portal cashback / gift-card-back / store credit wording is DEFERRED
# value — it never reduces either percentage (a '10% cash back' step must not
# be mistaken for a 10%-off code).
_DEFERRED_RE = re.compile(
    r"cash[\s-]?back|gift[\s-]?card|store\s+credit|statement\s+credit|portal")
_SALE_RE = re.compile(r"\b(?:sale|now|price)\b")
_ABS_OFF_RE = re.compile(r"\boff\b|coupon|clip|discount|save")

_CHECKOUT_KINDS = ("pct_off", "abs_off", "sale_price")
_REBATE_KINDS = ("rebate_abs", "rebate_pct")


def display_pct(value: float) -> int:
    """Whole-percent display rounding, matching the browser's Math.round."""
    return math.floor(float(value) + .5)


def clears_reference_floor(final: float, reference: float, min_pct: float) -> bool:
    """Audit a stored checkout price against an independently supported basis."""
    return reference > 0 and final <= reference * max(0.0, 1 - min_pct / 100) + 1e-9


def exceeds_price_tolerance(price: float, final: float, tolerance_pct: float = 10) -> bool:
    """A higher click price, with the existing one-cent rounding allowance."""
    return price > final * (1 + tolerance_pct / 100) + 0.01 + 1e-9


def page_price_moved(price: float, first: float, threshold_pct: float) -> bool:
    """Checkout-code math loses its basis when the page price moves either way."""
    return abs(price - first) * 100 > first * threshold_pct + 1e-9


def accuracy_price_outcome(price: float, final: float, *, source="keepa", min_pct=1.0) -> str:
    """Use the repricer's existing drift boundary in either direction."""
    difference = abs(price - final)
    if source == "keepa":
        changed = difference > 1e-9 and (not final or difference * 100 / final + 1e-9 >= min_pct)
    elif source == "woot":
        changed = difference > max(0.01, final * 0.005) + 1e-9
    else:  # Best Buy/eBay correct any changed, evidence-backed final price.
        changed = price != final
    return ("higher" if price > final else "lower") if changed else "match"


def comparable_prices(price: float, other: float, minimum: float, maximum: float) -> bool:
    """Reject extreme ratios in either direction; zero only compares to zero."""
    if price == 0 or other == 0:
        return price == other == 0
    return (minimum <= other / price <= maximum
            and minimum <= price / other <= maximum)


def lowest_seen(observations: list[dict], condition: str, now: datetime) -> dict | None:
    """Minimum of evidence-checked product observations on 3+ UTC days in 30 days.

    The caller supplies DB.product_price_history for one exact product.
    Repeated checks on a day do not make thin history sufficient.
    """
    from .util import parse_iso

    now = now.astimezone(timezone.utc)
    points = []
    for row in observations:
        if row.get("condition") != condition:
            continue
        try:
            at = parse_iso(row.get("observed_at"))
            price = float(row["price"])
        except (KeyError, TypeError, ValueError):
            continue
        if at and now - timedelta(days=30) <= at <= now and math.isfinite(price) and price >= 0:
            points.append((price, at))
    if len({at.date() for _, at in points}) < 3:
        return None
    price, at = min(points)  # Earliest date wins equal minima, independent of row order.
    return {"price": price, "date": at.date().isoformat()}


# ---------------------------------------------------------------------------
# public parsers
# ---------------------------------------------------------------------------

def parse_shipping(s: str) -> tuple[float | None, float | None]:
    """Parse the extracted shipping text into (stated_cost, free_threshold).

    Accepted forms (case-insensitive):
        ''                        -> (None, None)
        '5.99' / '$5.99'          -> (5.99, None)
        'free' / 'free shipping'  -> (0.0, None)
        'free over $35'           -> (None, 35.0)
        'free on $25+'            -> (None, 25.0)
        '$6.99 or free over $50'  -> (6.99, 50.0)
    """
    text = (s or "").strip().lower()
    if not text:
        return (None, None)

    threshold: float | None = None
    rest = text
    m = _THRESH_WORD_RE.search(text) or _THRESH_PLUS_RE.search(text)
    if m is not None:
        threshold = float(m.group(1))
        rest = (text[: m.start()] + " " + text[m.end():]).strip()

    cost: float | None = None
    cm = _NUMBER_RE.search(rest)
    if cm is not None:
        cost = float(cm.group(1))
    elif threshold is None and "free" in rest:
        cost = 0.0
    return (cost, threshold)


def parse_stack_step(s: str) -> dict:
    """Classify one stack step string into {'kind': ..., 'value': float | None}.

    kinds: 'pct_off' | 'abs_off' | 'rebate_abs' | 'rebate_pct' | 'sale_price'
           | 'unknown' (ignored for checkout math, logged).
    """
    t = (s or "").strip().lower()
    pct_m = _PCT_RE.search(t)
    money_m = _MONEY_RE.search(t)

    if _DEFERRED_RE.search(t):
        return {"kind": "deferred", "value": None}
    if _REBATE_RE.search(t):
        if pct_m:
            return {"kind": "rebate_pct", "value": float(pct_m.group(1))}
        if money_m:
            return {"kind": "rebate_abs", "value": float(money_m.group(1))}
        logger.debug("rebate step with no amount, ignored: %r", s)
        return {"kind": "unknown", "value": None}
    if pct_m:
        return {"kind": "pct_off", "value": float(pct_m.group(1))}
    if money_m and _SALE_RE.search(t) and "off" not in t:
        return {"kind": "sale_price", "value": float(money_m.group(1))}
    if money_m and _ABS_OFF_RE.search(t):
        return {"kind": "abs_off", "value": float(money_m.group(1))}
    logger.debug("unknown stack step, ignored for checkout math: %r", s)
    return {"kind": "unknown", "value": None}


def known_shipping(deal: dict) -> bool:
    """Whether the published savings result includes a known shipping cost.

    A threshold alone is insufficient below that threshold. No amount is
    added here: final_price already came from the savings engine.
    """
    if "unknown_shipping" in (deal.get("flags") or []):
        return False
    cost, threshold = parse_shipping(deal.get("shipping_cost_or_threshold") or "")
    final = deal.get("final_price")
    return cost is not None or (threshold is not None and final is not None and final >= threshold)


# ---------------------------------------------------------------------------
# public engine
# ---------------------------------------------------------------------------

def compute_savings(d: models.ExtractedDeal, *, allow_price_only: bool = False) -> models.SavingsResult:
    """Turn raw extracted facts into a SavingsResult, per SAVINGS_RULES.md."""
    # ---- Rule 1: gates first ----
    if (d.currency or "").strip().upper() != "USD" or (d.region or "").strip().upper() != "US":
        logger.debug("excluded non_us: %s (%s/%s)", d.item, d.currency, d.region)
        return models.SavingsResult(computable=False, exclude_reason="non_us")
    if _has_in_store_condition(d.conditions):
        logger.debug("excluded in_store: %s %r", d.item, d.conditions)
        return models.SavingsResult(computable=False, exclude_reason="in_store")
    if ((d.regular_price is not None and d.regular_price <= 0)
            or (d.final_price is not None and d.final_price < 0)
            or (d.price_upper_bound is not None and d.price_upper_bound < 0)):
        logger.debug("excluded invalid_price: %s (reg=%s final=%s)",
                     d.item, d.regular_price, d.final_price)
        return models.SavingsResult(computable=False,
                                    exclude_reason="invalid_price")

    # ---- Rule 10: units ----
    ru = (d.regular_price_unit or "").strip().lower()
    fu = (d.final_price_unit or "").strip().lower()
    if ru and fu and ru != fu:
        logger.debug("excluded unit_mismatch: %s (%s vs %s)", d.item, ru, fu)
        return models.SavingsResult(computable=False, exclude_reason="unit_mismatch")

    flags: list[str] = []
    if ru == "per_unit" and fu == "per_unit":
        flags.append("per_unit")

    ship_cost, ship_threshold = parse_shipping(d.shipping_cost_or_threshold)

    checkout_steps: list[dict[str, Any]] = []
    rebate_steps: list[dict[str, Any]] = []
    for raw in d.stack_steps:
        step = parse_stack_step(raw)
        if step["kind"] in _CHECKOUT_KINDS:
            checkout_steps.append(step)
        elif step["kind"] in _REBATE_KINDS:
            rebate_steps.append(step)
        elif step["kind"] == "deferred" and "deferred_value" not in flags:
            flags.append("deferred_value")  # Rule 9: never enters any pct
    for raw in d.rebates:
        step = _parse_rebate(raw)
        if step["kind"] in _REBATE_KINDS:
            rebate_steps.append(step)

    # ---- Rules 7/8: deal-type driven math ----
    if d.deal_type in ("free_after_rebate", "moneymaker"):
        return _free_after_rebate(d, flags, rebate_steps, ship_cost, ship_threshold)
    if d.deal_type == "penny":
        return _penny(d, flags, ship_cost, ship_threshold)
    if d.deal_type in DEAL_TYPE_PCT:
        return _structural(d, flags, checkout_steps, rebate_steps, ship_cost, ship_threshold)

    # ---- Rules 2/3/4/5/6/11/12: price-basis math ----
    return _price_basis(d, flags, checkout_steps, rebate_steps, ship_cost, ship_threshold,
                        allow_price_only=allow_price_only)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _has_in_store_condition(conditions: list[str]) -> bool:
    for c in conditions:
        norm = re.sub(r"[\s\-]+", "_", (c or "").strip().lower())
        if any(marker in norm for marker in _IN_STORE_MARKERS):
            return True
    return False


def _parse_rebate(s: str) -> dict:
    """Parse one entry of ExtractedDeal.rebates (context: it IS a rebate)."""
    step = parse_stack_step(s)
    if step["kind"] in _REBATE_KINDS:
        return step
    t = (s or "").strip().lower()
    m = _PCT_RE.search(t)
    if m:
        return {"kind": "rebate_pct", "value": float(m.group(1))}
    m = _MONEY_RE.search(t)
    if m:
        return {"kind": "rebate_abs", "value": float(m.group(1))}
    logger.debug("unparseable rebate string, ignored: %r", s)
    return {"kind": "unknown", "value": None}


def _shipping_add(final: float, cost: float | None, threshold: float | None,
                  flags: list[str]) -> float:
    """Rule 4: dollars to add to the checkout price for shipping.

    May append the 'unknown_shipping' flag (threshold stated, final below it,
    no stated cost).
    """
    if cost is not None:
        if threshold is not None and final >= threshold:
            return 0.0  # free-shipping threshold reached
        return cost
    if threshold is not None and final < threshold and "unknown_shipping" not in flags:
        flags.append("unknown_shipping")
    return 0.0


def _rebate_total(rebate_steps: list[dict[str, Any]], base: float | None) -> float:
    """Dollar total of rebates. Percent rebates need a pre-shipping base price."""
    total = 0.0
    for step in rebate_steps:
        v = step["value"]
        if v is None:
            continue
        if step["kind"] == "rebate_abs":
            total += v
        elif step["kind"] == "rebate_pct" and base is not None:
            total += base * v / 100.0
    return total


def _compound_keep(initial_keep: float, steps: list[dict[str, Any]], kind: str) -> float:
    """Multiply the kept fraction through every percent step of `kind`."""
    keep = initial_keep
    for step in steps:
        if step["kind"] == kind and step["value"] is not None:
            keep *= 1.0 - step["value"] / 100.0
    return keep


def _round_pct(x: float | None) -> float | None:
    return None if x is None else round(x, 1)


def _abs_savings(d: models.ExtractedDeal, reg: float | None,
                 b_adj: float | None) -> float | None:
    """Rule 12: regular - shipping-adjusted final when both known, else None.

    The LLM's own abs_savings is deliberately NOT trusted: unlike the price
    fields it carries no verbatim-quote evidence, and it feeds a publish gate
    (min_abs_savings) and the ranking score. Derived-or-nothing.
    """
    if reg and b_adj is not None:
        return round(reg - b_adj, 2)
    return None


def _finish(d: models.ExtractedDeal, flags: list[str], *, final_adj: float | None,
            regular: float | None, pct_co: float | None, pct_wr: float | None,
            abs_s: float | None, basis: str) -> models.SavingsResult:
    # Rule 9: deferred value (gift card back / portal cashback / store credit)
    # never enters any percentage; it is only flagged.
    if (d.deferred_value or "").strip():
        flags.append("deferred_value")
    # Rule 8 scope: only free-after-rebate/moneymaker may report >100%.
    if (pct_wr is not None and pct_wr > 100.0 and basis != "free_after_rebate"):
        pct_wr = 100.0
    # Rule 6: rebates change the effective pct -> flag when the two pcts differ.
    if (pct_co is not None and pct_wr is not None
            and abs(pct_wr - pct_co) > 1e-9 and "rebate_dependent" not in flags):
        flags.append("rebate_dependent")
    return models.SavingsResult(
        computable=True,
        final_price=round(final_adj, 2) if final_adj is not None else None,
        regular_price=regular,
        pct_at_checkout=_round_pct(pct_co),
        pct_with_rebates=_round_pct(pct_wr),
        abs_savings=abs_s,
        basis=basis,
        flags=flags,
    )


def _structural(d: models.ExtractedDeal, flags: list[str],
                checkout_steps: list[dict[str, Any]],
                rebate_steps: list[dict[str, Any]],
                ship_cost: float | None, ship_threshold: float | None,
                ) -> models.SavingsResult:
    """Rule 7: bogo/b2g1/b2g3/b1g1_50; percent-off codes compound on top."""
    keep = _compound_keep(1.0 - DEAL_TYPE_PCT[d.deal_type] / 100.0,
                          checkout_steps, "pct_off")
    pct_co = (1.0 - keep) * 100.0
    # Percent rebates compound into the with-rebates figure (scale-free);
    # dollar rebates join below when the deal also states real prices.
    keep_wr = _compound_keep(keep, rebate_steps, "rebate_pct")
    pct_wr = (1.0 - keep_wr) * 100.0

    b_adj = None
    if d.final_price is not None:
        b_adj = d.final_price + _shipping_add(d.final_price, ship_cost,
                                              ship_threshold, flags)
    if d.regular_price and b_adj is not None:
        total = _rebate_total(rebate_steps, d.final_price)
        if total > 0:
            pct_wr = max(pct_wr,
                         (d.regular_price - (b_adj - total))
                         / d.regular_price * 100.0)
    return _finish(d, flags, final_adj=b_adj, regular=d.regular_price,
                   pct_co=pct_co, pct_wr=pct_wr,
                   abs_s=_abs_savings(d, d.regular_price, b_adj),
                   basis=d.deal_type)


def _free_after_rebate(d: models.ExtractedDeal, flags: list[str],
                       rebate_steps: list[dict[str, Any]],
                       ship_cost: float | None, ship_threshold: float | None,
                       ) -> models.SavingsResult:
    """Rule 8: free_after_rebate and moneymaker -> pct_WITH_REBATES is 100
    (or above, for a moneymaker with stated numbers), always flagged
    rebate_dependent. pct_AT_CHECKOUT is never 100 while money changes
    hands: it comes from the stated prices when both exist, else stays
    None (a rebate-only deal has no checkout discount to publish on).
    A "$254.98 phone, 100% off" is a contradiction, not a deal."""
    pct_wr = 100.0
    b_adj = None
    if d.final_price is not None:
        b_adj = d.final_price + _shipping_add(d.final_price, ship_cost,
                                              ship_threshold, flags)
    pct_co = None
    if d.regular_price and b_adj is not None:
        pct_co = (d.regular_price - b_adj) / d.regular_price * 100.0
    elif b_adj is not None and b_adj <= 0.01:
        pct_co = 100.0  # genuinely free at the register
    if d.deal_type == "moneymaker" and d.regular_price and b_adj is not None:
        total = _rebate_total(rebate_steps, d.final_price)
        if total > 0:
            candidate = (d.regular_price - (b_adj - total)) / d.regular_price * 100.0
            if candidate > 100.0:
                pct_wr = candidate
    flags.append("rebate_dependent")
    return _finish(d, flags, final_adj=b_adj, regular=d.regular_price,
                   pct_co=pct_co, pct_wr=pct_wr,
                   abs_s=_abs_savings(d, d.regular_price, b_adj),
                   basis="free_after_rebate")


def _penny(d: models.ExtractedDeal, flags: list[str],
           ship_cost: float | None, ship_threshold: float | None,
           ) -> models.SavingsResult:
    """Rule 8: penny deals ($0.01 outright). Regular known -> true math,
    else 100.0."""
    final = d.final_price if d.final_price is not None else 0.01
    b_adj = final + _shipping_add(final, ship_cost, ship_threshold, flags)
    reg = d.regular_price
    if reg:
        pct_co = (reg - b_adj) / reg * 100.0
    else:
        pct_co = 100.0
    return _finish(d, flags, final_adj=b_adj, regular=reg,
                   pct_co=pct_co, pct_wr=pct_co,
                   abs_s=_abs_savings(d, reg, b_adj), basis="penny")


def _price_basis(d: models.ExtractedDeal, flags: list[str],
                 checkout_steps: list[dict[str, Any]],
                 rebate_steps: list[dict[str, Any]],
                 ship_cost: float | None, ship_threshold: float | None, *,
                 allow_price_only: bool = False,
                 ) -> models.SavingsResult:
    """Rules 2/3/4/5/6/11/12: the main price-basis flow."""
    reg = d.regular_price

    # Rule 3: ranges — the conservative upper bound is the final price. A
    # range with NO verified upper bound cannot follow the rule: computing
    # from the low "as low as" price would overstate the discount, so exclude.
    final_for_pct = d.final_price
    if d.price_is_range:
        if d.price_upper_bound is None:
            logger.debug("excluded range_no_upper: %s", d.item)
            return models.SavingsResult(computable=False,
                                        exclude_reason="range_no_upper")
        final_for_pct = d.price_upper_bound
        flags.append("range")

    pct_co: float | None = None
    pct_wr: float | None = None
    b_adj: float | None = None
    checkout_base: float | None = None  # pre-shipping checkout price (rebate base)
    basis = ""

    if final_for_pct is not None:
        # Rule 4: stated shipping cost joins the checkout price BEFORE the pct.
        b_adj = final_for_pct + _shipping_add(final_for_pct, ship_cost,
                                              ship_threshold, flags)
        checkout_base = final_for_pct
        if reg:
            # Rule 2: both prices stated -> prices win, even over a stated pct.
            pct_co = (reg - b_adj) / reg * 100.0
            basis = "quoted_regular"
            if d.pct_off is not None and abs(d.pct_off - pct_co) > 5.0:
                logger.debug("stated pct %.1f disagrees with computed %.1f: %s",
                             d.pct_off, pct_co, d.item)
        elif d.pct_off is not None:
            # Rule 2: pct claim with no regular -> list_price_claim.
            pct_co = float(d.pct_off)
            basis = "list_price_claim"
        else:
            if allow_price_only and not d.price_is_range:
                # A daily spotlight may state a verified checkout price without
                # a discount basis. Preserve the price; invent no savings.
                return _finish(d, flags, final_adj=b_adj, regular=None,
                               pct_co=None, pct_wr=None, abs_s=None, basis="price_only")
            logger.debug("excluded no_basis (final only): %s", d.item)
            return models.SavingsResult(computable=False, exclude_reason="no_basis")
    elif reg:
        if checkout_steps:
            # Rule 5: stack math from the quoted regular, in stated order.
            # Rebate-only "stacks" don't belong here: a rebate never changes
            # the CHECKOUT price, so 'reg $A, X% off, plus a rebate' must take
            # the stated-pct path below and keep its X% at checkout.
            running = reg
            for step in checkout_steps:
                v = step["value"]
                if v is None:
                    continue
                if step["kind"] == "sale_price":
                    running = v
                elif step["kind"] == "abs_off":
                    running -= v
                else:  # pct_off multiplies the RUNNING total
                    running *= 1.0 - v / 100.0
            running = max(running, 0.0)
            b_adj = running + _shipping_add(running, ship_cost, ship_threshold, flags)
            checkout_base = running
            pct_co = (reg - b_adj) / reg * 100.0
            basis = "stack"
        elif d.pct_off is not None:
            # Stated pct applied to a quoted regular (arithmetic on stated
            # numbers only, never a guess).
            derived = reg * (1.0 - d.pct_off / 100.0)
            b_adj = derived + _shipping_add(derived, ship_cost, ship_threshold, flags)
            checkout_base = derived
            pct_co = (reg - b_adj) / reg * 100.0
            basis = "stated_pct"
        else:
            logger.debug("excluded no_basis (regular only): %s", d.item)
            return models.SavingsResult(computable=False, exclude_reason="no_basis")
    else:
        if d.pct_off is not None:
            # Rule 2 + rule 7's compounding: '60% off + extra 25% off code'.
            keep = _compound_keep(1.0 - d.pct_off / 100.0, checkout_steps, "pct_off")
            pct_co = (1.0 - keep) * 100.0
            basis = "list_price_claim"
            keep_wr = _compound_keep(keep, rebate_steps, "rebate_pct")
            pct_wr = (1.0 - keep_wr) * 100.0
        else:
            # Rule 11: no regular, no stated pct, no deal_type -> never guess.
            logger.debug("excluded no_basis: %s", d.item)
            return models.SavingsResult(computable=False, exclude_reason="no_basis")

    # Rule 6: pct_at_checkout excludes ALL rebates; pct_with_rebates includes them.
    if pct_wr is None:
        if reg and b_adj is not None:
            total = _rebate_total(rebate_steps, checkout_base)
            if total > 0:
                pct_wr = (reg - (b_adj - total)) / reg * 100.0
            else:
                pct_wr = pct_co
        elif basis == "list_price_claim" and pct_co is not None:
            keep_wr = _compound_keep(1.0 - pct_co / 100.0, rebate_steps, "rebate_pct")
            pct_wr = (1.0 - keep_wr) * 100.0
        else:
            pct_wr = pct_co

    return _finish(d, flags, final_adj=b_adj, regular=reg,
                   pct_co=pct_co, pct_wr=pct_wr,
                   abs_s=_abs_savings(d, reg, b_adj), basis=basis)
