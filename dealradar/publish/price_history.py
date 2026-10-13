"""Small static charts of our recorded checks, never third-party history."""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any

from ..util import parse_iso


STYLE = """<style>
.price-history{max-width:520px;margin:20px 0;padding:12px;border:1px solid var(--line);
               border-radius:14px;background:var(--card)}
.price-history figcaption{font-size:14px;font-weight:600}
.price-history p{font-size:13px;margin:6px 0;color:var(--muted)}
.price-history svg{display:block;width:100%;height:auto}
.price-history polyline{fill:none;stroke:var(--hist);stroke-width:2}
.price-history circle{fill:var(--card);stroke:var(--hist);stroke-width:1.5}
.price-history .typical{stroke:var(--muted);stroke-width:1;stroke-dasharray:4 4}
.price-history .current{fill:var(--accent);stroke:var(--card);stroke-width:2}
.price-history .dates{display:flex;justify-content:space-between;gap:12px}
</style>"""


def summary(observations: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """Stats use every valid check in 90 days, with at least a 48-hour span."""
    now = now.astimezone(timezone.utc)
    since = now - timedelta(days=90)
    points = []
    for row in observations:
        observed = parse_iso(row.get("observed_at"))
        try:
            price = float(row["price"])
        except (KeyError, TypeError, ValueError):
            continue
        if observed and since <= observed <= now and math.isfinite(price) and price >= 0:
            points.append((observed, price))
    points.sort(key=lambda p: p[0])
    if len(points) < 3 or points[-1][0] - points[0][0] < timedelta(days=2):
        return None
    lowest = min(points, key=lambda p: p[1])
    highest = max(points, key=lambda p: p[1])
    return {"points": points, "median": float(statistics.median(p[1] for p in points)),
            "min": lowest[1], "min_at": lowest[0], "max": highest[1], "max_at": highest[0]}


def _sample(points: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    """Bound markup; keep real checks, including both extremes and endpoints."""
    limit = 24
    if len(points) <= limit:
        return points
    selected = {0, len(points) - 1, min(range(len(points)), key=lambda i: points[i][1]),
                max(range(len(points)), key=lambda i: points[i][1])}
    others = [i for i in range(len(points)) if i not in selected]
    slots = limit - len(selected)
    selected.update(others[i * (len(others) - 1) // (slots - 1)] for i in range(slots))
    return [points[i] for i in sorted(selected)]


def _date(dt: datetime) -> str:
    return f"{dt:%b} {dt.day}, {dt.year}"


def chart_html(observations: list[dict[str, Any]], now: datetime,
               current_price: float | None, *, ended: bool = False) -> str:
    history = summary(observations, now)
    if history is None:
        return ""
    now = now.astimezone(timezone.utc)
    points = _sample(history["points"])
    count = len(history["points"])
    current = None if ended or current_price is None else float(current_price)
    if current is not None and (not math.isfinite(current) or current < 0):
        current = None
    low = min(history["min"], current) if current is not None else history["min"]
    high = max(history["max"], current) if current is not None else history["max"]
    first = points[0][0]
    span = (now - first).total_seconds()
    x = lambda dt: 8 + 284 * (dt - first).total_seconds() / span
    y = lambda price: 72 if high == low else 136 - 128 * (price - low) / (high - low)
    coordinates = ' '.join(f"{x(dt):.1f},{y(price):.1f}" for dt, price in points)
    if current is not None:
        coordinates += f" 292.0,{y(current):.1f}"
    label = (f'Price checks: lowest ${history["min"]:.2f} on {_date(history["min_at"])}, '
             f'typical ${history["median"]:.2f}')
    if current is not None:
        label += f", now ${current:.2f}"
    elif ended:
        label += ", deal ended"
    content = (f'<figure class="price-history"><figcaption>Our price checks, last 90 days'
               f' · {count} checks</figcaption>'
               f'<p>Lowest ${history["min"]:.2f} · Highest ${history["max"]:.2f}'
               f' · Typical (median) ${history["median"]:.2f}'
               + (f' · Now ${current:.2f}' if current is not None else "") + '</p>')
    content += (f'<svg viewBox="0 0 300 144" role="img" aria-label="{escape(label)}">'
                f'<line class="typical" x1="8" x2="292" y1="{y(history["median"]):.1f}" '
                f'y2="{y(history["median"]):.1f}"/>'
                f'<polyline points="{coordinates}"/>')
    content += ''.join(f'<circle cx="{x(dt):.1f}" cy="{y(price):.1f}" r="2.5"/>'
                       for dt, price in points)
    if current is not None:
        content += f'<circle class="current" cx="292" cy="{y(current):.1f}" r="5"/>'
    content += (f'</svg><p class="dates"><span>{_date(first)}</span>'
                f'<span>{_date(now)} (UTC)</span></p>')
    if len(points) < count:
        content += f'<p>Showing {len(points)} of {count} checks; statistics use all checks.</p>'
    content += ('<table class="sr-only"><caption>Price checks shown (UTC)</caption><thead><tr>'
                '<th scope="col">Observed at</th><th scope="col">Price</th></tr></thead><tbody>')
    content += ''.join(f'<tr><td>{dt:%Y-%m-%d %H:%M:%S}</td><td>${price:.2f}</td></tr>'
                       for dt, price in points)
    content += '</tbody>'
    if current is not None:
        # The published deal price is not a new historical check.
        content += (f'<tfoot><tr><th scope="row">Current deal price</th>'
                    f'<td>${current:.2f}</td></tr></tfoot>')
    return content + '</table></figure>'
