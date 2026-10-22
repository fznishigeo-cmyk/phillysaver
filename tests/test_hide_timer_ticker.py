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
