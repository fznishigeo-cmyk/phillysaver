"""Operator 2026-10-22: no countdown timer, "Live deals" banner or drop confetti, phone and desktop."""
import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import hide_timer_ticker, site
from tests.test_pwa import sample_deals
from tests.test_support import NOW, render


def test_shipped_on_default_off_strict(tmp_path):
    assert load_config().hide_timer_ticker_confetti is True
    assert AppConfig().hide_timer_ticker_confetti is False
    path = tmp_path / "config.yaml"
    path.write_text("hide_timer_ticker_confetti: 'yes'\n")
    with pytest.raises(ValueError, match="hide_timer_ticker_confetti"):
        load_config(path)


@pytest.mark.parametrize("on", [True, False])
def test_home_hides_all_three_only_when_on_and_is_idempotent(tmp_path, monkeypatch, on):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = load_config(); cfg.hide_timer_ticker_confetti = on
    out, *_ = render(tmp_path, cfg, sample_deals())
    html = (out / "index.html").read_text()
    # The elements stay (the page scripts use them); the rule hides them.
    assert 'id="drop"' in html and 'id="ticker"' in html
    assert html.count(hide_timer_ticker.SNIPPET) == (1 if on else 0)
    if on:
        for name in ("#ticker", "#drop", "#drop-confetti"):
            assert name in hide_timer_ticker.SNIPPET
        before = (out / "index.html").read_bytes()
        hide_timer_ticker.publish(out, cfg)
        assert (out / "index.html").read_bytes() == before


def test_tagline_full_row_shipped_on_default_off_strict(tmp_path):
    assert load_config().tagline_full_row is True
    assert AppConfig().tagline_full_row is False
    path = tmp_path / "config.yaml"
    path.write_text("tagline_full_row: 1\n")
    with pytest.raises(ValueError, match="tagline_full_row"):
        load_config(path)


@pytest.mark.parametrize("hide, full_row, expected", [
    (True, True, 1), (True, False, 0),
    (False, True, 0),  # the timer still shares the row: never span over it
])
def test_phone_tagline_spans_the_row_only_with_the_timer_hidden(tmp_path, monkeypatch, hide, full_row, expected):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = load_config(); cfg.hide_timer_ticker_confetti = hide; cfg.tagline_full_row = full_row
    out, *_ = render(tmp_path, cfg, sample_deals())
    html = (out / "index.html").read_text()
    assert html.count(hide_timer_ticker.TAGLINE) == expected
    assert "@media(max-width:899px)" in hide_timer_ticker.TAGLINE  # phones only
    if expected:
        before = (out / "index.html").read_bytes()
        hide_timer_ticker.publish(out, cfg)
        assert (out / "index.html").read_bytes() == before
    for other in out.rglob("*.html"):
        if other.name != "index.html" or other.parent != out:
            assert hide_timer_ticker.TAGLINE not in other.read_text()
