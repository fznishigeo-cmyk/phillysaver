"""Saved-list publication, mode boundaries and storage configuration, offline."""
import re

import pytest

from dealradar.config import AppConfig, load_config
from tests.test_publish_site import make_deal
from tests.test_support import render


@pytest.mark.parametrize("value,expected", [(-1, 1), (0, 1), (1, 1), (37, 37), (200, 200), (99999, 200)])
def test_saved_limit_is_configured_and_bounded(tmp_path, value, expected):
    path = tmp_path / "config.yaml"
    path.write_text(f"engagement:\n  saved_deals: true\n  saved_deals_limit: {value}\n")
    cfg = load_config(path)
    assert cfg.engagement["saved_deals"]
    assert cfg.engagement_saved_deals_limit == expected
    cfg.support_enabled = True
    _, doc, _, _ = render(tmp_path, cfg)
    assert doc["meta"]["saved_deals_limit"] == expected


def test_saved_defaults_and_operator_switch(tmp_path):
    assert AppConfig().engagement == {}
    assert AppConfig().engagement_saved_deals_limit == 100
    cfg = load_config()
    assert cfg.engagement["saved_deals"] and cfg.engagement_saved_deals_limit == 100
    path = tmp_path / "config.yaml"
    path.write_text("engagement:\n  saved_deals: false\n")
    assert load_config(path).engagement["saved_deals"] is False


@pytest.mark.parametrize("support,enabled", [(True, True), (True, False), (False, True), (False, False)])
@pytest.mark.parametrize("open_access", [False, True])
def test_saved_output_privacy_and_dormant_modes(tmp_path, support, enabled, open_access):
    cfg = AppConfig(support_enabled=support, engagement={"saved_deals": enabled},
                    paywall_enabled=True, paywall_open_access=open_access, paywall_teaser_count=1)
    deals = [make_deal(dedupe_key=f"saved-{i}") for i in range(4)]
    out, doc, full, memory = render(tmp_path, cfg, deals)
    on = support and enabled
    assert doc["meta"]["engagement"]["saved_deals"] is on
    assert full["meta"] == doc["meta"]
    home, privacy = ((out / name).read_text() for name in ("index.html", "privacy.html"))
    assert ('id="savedview"' in home) is on
    assert ('var SAVED_KEY = "wp.savedDeals"' in home) is on
    assert ('function savedButtonClick' in home) is on
    assert ('function savedViewHtml' in home) is on
    assert ('.save-deal,#savedview,#savedall,#savedclear{min-width:44px;min-height:44px' in home) is on
    assert ('Saved on this device.' in privacy) is on
    assert ('no saved prices, photos, codes or outbound links' in privacy) is on
    for page in (home, privacy):
        assert "<!--saved-->" not in page and "{{" not in page
    if support or open_access:
        assert doc["meta"]["locked_count"] == 0 and memory == '["before"]'
    else:
        assert doc["meta"]["locked_count"] == 3
        assert sum(d.get("locked", False) for d in doc["deals"]) == 3
        assert 'id="authmodal"' in home
    if on:
        assert 'aria-controls="content"' in home
        assert 'id="savedstatus" role="status" aria-live="polite"' in home
        assert "content.removeAttribute(\"data-clarity-unmask\")" in home
        assert 'content.setAttribute("data-clarity-mask", "true")' in home
        # Buttons precede the ordinary outbound click handler and return before it.
        click = home[home.rindex('document.getElementById("content").addEventListener("click"'):]
        assert click.index("if (savedButtonClick(e)) return;") < click.index('track("deal_click"')
        assert 'e.preventDefault();' in home and 'e.stopPropagation();' in home


def test_successful_public_payload_only_and_version_switch(tmp_path):
    cfg = AppConfig(support_enabled=True, engagement={"saved_deals": True})
    out, doc, _, _ = render(tmp_path, cfg, [make_deal()])
    home = (out / "index.html").read_text()
    fetch = home.split("function fetchPublish()", 1)[1].split("function isNewPublish", 1)[0]
    assert "if (!r.ok) throw" in fetch and "Array.isArray(data.deals)" in fetch  # task 064 envelope check
    apply = home.split("function applyPublish(pub, deals)", 1)[1].split("function landed", 1)[0]
    assert apply.index("state.meta = pub.meta") < apply.index("reconcileSaved(pub)")
    assert "savedDeals.publicDeals = pub.deals" in home
    assert "savedDeals.publicDeals = state.deals" not in home
    cfg.engagement["saved_deals"] = False
    _, off, _, _ = render(tmp_path, cfg, [make_deal()])
    assert doc["meta"]["site_version"] != off["meta"]["site_version"]
    # No shortlist load/write, controls or hooks survive the disabled build.
    assert not re.search(r"wp\.savedDeals|savedButton|savedOutbound|savedViewHtml", (out / "index.html").read_text())
