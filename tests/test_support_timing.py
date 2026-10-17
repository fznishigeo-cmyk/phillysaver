"""Task 272 support timing and configurable cost copy, entirely offline."""

import hashlib
import json
import socket
from html import escape

import httpx
import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import site
from tests.test_support import BODY, LINK, MONTHLY, NOW, render


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task272 network unavailable")
    monkeypatch.setattr(httpx.Client, "send", unavailable)
    monkeypatch.setattr(httpx.AsyncClient, "send", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


def configured(tmp_path, **support):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"support": support}))
    return load_config(path)


def test_defaults_and_shipped_operator_settings(tmp_path):
    for cfg in (AppConfig(), configured(tmp_path)):
        assert cfg.support_panel_delay_s == 0
        assert cfg.support_cost_phrase == "hundreds of dollars"
    cfg = load_config()
    assert cfg.support_panel_delay_s == 120
    assert cfg.support_cost_phrase == "thousands of dollars"
    assert cfg.join_prompt_delay_s == 35 and cfg.join_prompt  # current operator setting; support keeps its separate gate


@pytest.mark.parametrize("delay", [0, 1, 120])
def test_delay_published_in_public_and_member_metadata(tmp_path, delay):
    cfg = configured(tmp_path, enabled=True, panel_delay_s=delay)
    out, public, full, _ = render(tmp_path, cfg)
    assert public["meta"]["support"] == full["meta"]["support"] == {
        "enabled": True, "link": "", "monthly_link": "",
        **({"panel_delay_s": delay} if delay else {}),
    }
    for path in out.rglob("*.html"):
        html = path.read_text()
        assert "{{" not in html
        if "window.wpSupport =" in html:
            assert ("panelWaiting" in html) is bool(delay)


@pytest.mark.parametrize("value", [-1, True, False, 1.5, "120", None, [], {}])
def test_invalid_delay_rejected(tmp_path, value):
    with pytest.raises(ValueError, match=r"^support.panel_delay_s must be an integer >= 0$"):
        configured(tmp_path, panel_delay_s=value)


@pytest.mark.parametrize("value", ["", "   ", None, True, 120, [], {}, "x" * 121,
                                   "thousands\nof dollars", "money\x00", "money\x7f", "money\x85",
                                   "<b>thousands</b>"])
def test_invalid_cost_phrase_rejected(tmp_path, value):
    with pytest.raises(ValueError, match=r"^support.cost_phrase must be nonempty plain text"):
        configured(tmp_path, cost_phrase=value)


@pytest.mark.parametrize("phrase", ["thousands of dollars", "about $2,000 & upkeep"])
def test_cost_phrase_updates_both_panels_and_version(tmp_path, phrase):
    cfg = configured(tmp_path, enabled=True, payment_link=LINK, panel_delay_s=0,
                     cost_phrase="hundreds of dollars")
    out, before, _, _ = render(tmp_path, cfg)
    for name in ("index.html", "subscribe.html"):
        assert BODY in (out / name).read_text()
    cfg = configured(tmp_path, enabled=True, payment_link=LINK, panel_delay_s=0,
                     cost_phrase=" " + phrase + " ")
    assert cfg.support_cost_phrase == phrase
    out, after, _, _ = render(tmp_path, cfg)
    assert before["meta"]["site_version"] != after["meta"]["site_version"]
    for name in ("index.html", "subscribe.html"):
        html = (out / name).read_text()
        assert BODY.replace("hundreds of dollars", escape(phrase)) in html
        assert "costs us hundreds of dollars" not in html
    # A reload must also be triggered by the timing change alone.
    cfg.support_panel_delay_s = 120
    _, delayed, _, _ = render(tmp_path, cfg)
    assert delayed["meta"]["site_version"] != after["meta"]["site_version"]


@pytest.mark.parametrize("link,monthly,expected", [
    ("", "", "0e61428c8db9a1377749f0cb828face8bef0b532a66685ab7a48e14117d8c6be"),
    (LINK, "", "8a6ebfc6846911981a6655af6086d40056247b8c908da1fa005bfdfa002473ce"),
    (LINK, MONTHLY, "c0168324c48b35c032e4ed8c780efbafb641e26489655070311272a973969799"),
])
def test_zero_delay_matches_recorded_script_bytes(link, monthly, expected):
    # Recorded before task 272; never re-pin.
    script = site.support_script(link, monthly, panel_delay_s=0)
    assert hashlib.sha256(script.encode()).hexdigest() == expected
    assert script == site.support_script(link, monthly)


def test_disabled_support_keeps_every_published_byte(tmp_path):
    cfg = AppConfig()
    out, _, before, _ = render(tmp_path, cfg)
    original = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    cfg.support_panel_delay_s = 120
    cfg.support_cost_phrase = "thousands of dollars"
    out, _, after, _ = render(tmp_path, cfg)
    assert {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()} == original
    assert before == after
