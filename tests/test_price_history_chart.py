"""Own-history charts: honest stats, accessible bounded markup and indexed reads."""

import math
import re
import statistics
import xml.etree.ElementTree as ET
from datetime import timedelta, timezone

import pytest

from dealradar.publish.price_history import STYLE, chart_html, summary
from dealradar.util import iso, parse_iso
from tests.test_publish_site import NOW, make_deal
from tests.test_seo import ROOT, cfg, db, _deals, _render
from tests.test_seo_deal_pages import _page, _path


def _checks(prices):
    return [{"price": price, "observed_at": iso(NOW - timedelta(days=len(prices) - i - 1))}
            for i, price in enumerate(prices)]


def _seed(db, deal, checks):
    for i, row in enumerate(checks):
        assert db.record_price(deal["dedupe_key"], deal["store"], row["price"],
                               parse_iso(row["observed_at"]), f"check-{i}")


@pytest.mark.parametrize("offsets, visible", [
    ([], False), ([0, 2], False), ([0, 0.5, 1.999], False),
    ([0, 1, 2], True), ([0, 0, 2], True),
])
def test_chart_requires_three_checks_and_a_full_two_day_span(offsets, visible):
    checks = [{"price": 20, "observed_at": iso(NOW - timedelta(days=days))} for days in offsets]
    html = chart_html(checks, NOW, 10)
    assert bool(html) is visible
    if not visible:
        assert html == ""  # No wrapper, SVG, styles or table.


@pytest.mark.parametrize("prices, median", [([40, 10, 30], 30), ([50, 10, 30, 20], 25)])
def test_stats_include_every_check_and_have_correct_dates(prices, median):
    checks = _checks(prices)
    stats = summary(list(reversed(checks)), NOW)
    assert stats["median"] == median
    assert stats["min"] == 10 and stats["max"] == max(prices)
    assert stats["min_at"] == NOW - timedelta(days=len(prices) - 2)
    assert stats["max_at"] == NOW - timedelta(days=len(prices) - 1)
    assert [p[1] for p in stats["points"]] == prices
    html = chart_html(checks, NOW, 7)
    assert f'Typical (median) ${median:.2f}' in html
    assert f'Highest ${max(prices):.2f}' in html
    assert 'Now $7.00' in html  # Current deal price is excluded from the stats/count.
    assert f' · {len(prices)} checks' in html


def test_window_is_inclusive_normalized_to_utc_and_excludes_future_and_invalid_checks():
    start = NOW - timedelta(days=90)
    checks = [
        {"price": 20, "observed_at": iso(start)},
        {"price": 10, "observed_at": (NOW - timedelta(days=3)).astimezone(
            timezone(timedelta(hours=-7))).isoformat()},
        {"price": 30, "observed_at": iso(NOW)},
        {"price": 1000, "observed_at": iso(start - timedelta(seconds=1))},
        {"price": 2000, "observed_at": iso(NOW + timedelta(seconds=1))},
        {"price": 3000, "observed_at": "invalid date"},
        *[{"price": price, "observed_at": iso(NOW)}
          for price in (-1, float("inf"), float("nan"), None, "not a price")],
    ]
    stats = summary(checks, NOW.astimezone(timezone(timedelta(hours=8))))
    assert len(stats["points"]) == 3
    assert stats["median"] == 20 and stats["min"] == 10 and stats["max"] == 30
    assert stats["points"][0][0] == start
    assert stats["points"][-1][0] == NOW


def test_tied_extremes_use_the_earliest_observed_date():
    stats = summary(_checks([10, 50, 10, 50]), NOW)
    assert stats["min_at"] == NOW - timedelta(days=3)
    assert stats["max_at"] == NOW - timedelta(days=2)


def test_svg_summary_table_and_tokens_are_accessible_and_self_contained():
    html = chart_html(_checks([30, 20, 10]), NOW, 8)
    figure = ET.fromstring(html)
    svg = figure.find("svg")
    assert svg.attrib["role"] == "img"
    assert svg.attrib["aria-label"] == "Price checks: lowest $10.00 on Oct 9, 2026, typical $20.00, now $8.00"
    assert figure.find("figcaption").text == "Our price checks, last 90 days · 3 checks"
    table = figure.find("table[@class='sr-only']")
    assert table.find("caption").text == "Price checks shown (UTC)"
    assert [h.attrib["scope"] for h in table.findall("thead/tr/th")] == ["col", "col"]
    assert [[c.text for c in r.findall("td")] for r in table.findall("tbody/tr")] == [
        ["2026-10-07 14:00:00", "$30.00"], ["2026-10-08 14:00:00", "$20.00"],
        ["2026-10-09 14:00:00", "$10.00"],
    ]
    assert ''.join(table.find("tfoot").itertext()) == "Current deal price$8.00"
    assert svg.find("line[@class='typical']") is not None
    assert svg.find("circle[@class='current']").attrib["r"] == "5"
    assert len(svg.findall("circle")) == 4
    assert all(token in STYLE for token in ("var(--hist)", "var(--accent)", "var(--muted)", "var(--card)"))
    assert "stroke-dasharray" in STYLE
    template = (ROOT / "dealradar/publish/templates/hub.html").read_text()
    dark = template.split("@media (prefers-color-scheme: dark)", 1)[1].split("*{", 1)[0]
    assert all(token in dark for token in ("--hist:", "--accent:", "--muted:", "--card:"))
    assert '.sr-only{position:absolute;width:1px;height:1px;' in template
    assert 'width:100%;height:auto' in STYLE and 'max-width:520px' in STYLE
    assert not re.search(r"https?://|url\(|<(?:script|img|image|a)\b", html + STYLE)


@pytest.mark.parametrize("prices, current", [([20, 20, 20], 20), ([0, 0, 0], 0),
                                            ([10, 30, 20], 1), ([10, 30, 20], 100),
                                            ([10, 30, 20], None)])
def test_svg_is_well_formed_and_coordinates_stay_in_viewbox(prices, current):
    html = chart_html(_checks(prices), NOW, current)
    svg = ET.fromstring(re.search(r"<svg\b.*?</svg>", html).group())
    assert svg.attrib["viewBox"] == "0 0 300 144"
    points = [tuple(map(float, pair.split(','))) for pair in svg.find("polyline").attrib["points"].split()]
    assert len(points) == 3 + (current is not None)
    for x, y in points:
        assert math.isfinite(x) and 0 <= x <= 300
        assert math.isfinite(y) and 0 <= y <= 144
    for dot in svg.findall("circle"):
        assert 0 <= float(dot.attrib["cx"]) - float(dot.attrib["r"])
        assert float(dot.attrib["cx"]) + float(dot.attrib["r"]) <= 300
        assert 0 <= float(dot.attrib["cy"]) - float(dot.attrib["r"])
        assert float(dot.attrib["cy"]) + float(dot.attrib["r"]) <= 144
    median = svg.find("line")
    assert median.attrib["y1"] == median.attrib["y2"]
    assert 0 <= float(median.attrib["y1"]) <= 144
    assert len((STYLE + html).encode()) < 6000


def test_dense_history_is_under_six_kb_and_samples_real_checks_without_changing_stats():
    checks = [{"price": float(30 + i % 7),
               "observed_at": iso(NOW - timedelta(seconds=(9999 - i) * 600))}
              for i in range(10000)]
    checks[43]["price"], checks[9876]["price"] = 1.0, 1200.0
    html = chart_html(checks, NOW, 8)
    figure = ET.fromstring(html)
    rows = [[c.text for c in r.findall("td")] for r in figure.findall("table/tbody/tr")]
    assert len((STYLE + html).encode()) < 6000
    assert "10000 checks" in html and "Showing 24 of 10000 checks; statistics use all checks." in html
    assert len(rows) == 24 and len(figure.findall("svg/circle")) == 25
    assert rows[0][0] == "2026-08-01 03:30:00" and rows[-1][0] == "2026-10-09 14:00:00"
    assert sum(row[1] == "$1.00" for row in rows) == 1
    assert sum(row[1] == "$1200.00" for row in rows) == 1
    recorded = {(r["observed_at"][:19].replace('T', ' '), f'${r["price"]:.2f}') for r in checks}
    assert all(tuple(row) in recorded for row in rows)
    assert f'Typical (median) ${statistics.median(r["price"] for r in checks):.2f}' in html


def test_chart_uses_only_this_products_90_day_recorded_checks_on_its_deal_page(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    d = make_deal(codes=[])
    _seed(db, d, _checks([30, 20, 10]))
    assert db.record_price(d["dedupe_key"], d["store"], 999, NOW - timedelta(days=91), "old")
    assert db.record_price(d["dedupe_key"], d["store"], 888, NOW + timedelta(days=1), "future")
    _seed(db, make_deal(dedupe_key="another-product"), _checks([777, 777, 777]))
    before = db.conn.execute("SELECT * FROM price_history ORDER BY id").fetchall()
    _render(db, cfg, tmp_path, [d])
    html = _page(tmp_path, _path(d)).read_text()
    assert 'Our price checks, last 90 days · 3 checks' in html
    assert 'Lowest $10.00 · Highest $30.00 · Typical (median) $20.00 · Now $10.00' in html
    assert all(f'${price:.2f}' not in html for price in (999, 888, 777))
    assert d["verify_tier"] == "corroborated" and d["final_price"] == 10
    assert db.conn.execute("SELECT * FROM price_history ORDER BY id").fetchall() == before
    assert 'price-history' not in (tmp_path / "deals/electronics/index.html").read_text()
    assert 'price-history' not in (tmp_path / "best-deals-today/index.html").read_text()
    _render(db, cfg, tmp_path, [d])
    assert _page(tmp_path, _path(d)).read_text() == html


def test_deal_page_with_insufficient_history_has_no_chart_or_styles(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    d = make_deal(codes=[])
    _seed(db, d, _checks([30, 20]))
    _render(db, cfg, tmp_path, [d])
    assert 'price-history' not in _page(tmp_path, _path(d)).read_text()


def test_ended_history_does_not_claim_a_current_price():
    html = chart_html(_checks([30, 20, 10]), NOW, 8, ended=True)
    figure = ET.fromstring(html)
    assert figure.find("svg").attrib["aria-label"].endswith(", deal ended")
    assert 'Now $' not in html and 'Current deal price' not in html
    assert figure.find("svg/circle[@class='current']") is None
    assert len(figure.findall("table/tbody/tr")) == 3


def test_build_reads_history_once_per_built_page_and_uses_existing_index(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    cfg.seo_deal_pages_max = 5
    deals = _deals(12)
    for d in deals:
        _seed(db, d, _checks([30, 20, 10]))
    queries = []
    db.conn.set_trace_callback(queries.append)
    try:
        _render(db, cfg, tmp_path, deals)
    finally:
        db.conn.set_trace_callback(None)
    reads = [q for q in queries if re.match(r"SELECT .* FROM price_history\b", q)]
    # The private product-page summary counts today's own checks once per build.
    totals = [q for q in reads if q.startswith("SELECT COUNT(*)")]
    assert len(totals) == 1 and "check:*" in totals[0]
    reads = [q for q in reads if q not in totals]
    pages = list((tmp_path / "deal").glob("*/index.html"))
    assert len(reads) == len(pages) == 5
    assert all('canonical_key=' in q and 'observed_at>=' in q and 'observed_at<=' in q for q in reads)
    plan = db.conn.execute("EXPLAIN QUERY PLAN " + reads[0]).fetchall()
    assert any('INDEX' in r["detail"] and 'canonical_key=?' in r["detail"] for r in plan)
    assert all('Our price checks, last 90 days · 3 checks' in p.read_text() for p in pages)
