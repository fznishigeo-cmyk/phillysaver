"""Accuracy-lite switches follow the strict, opt-in and historical-pin contract."""

import json

import pytest

from dealradar.config import AppConfig, load_config
from tests import history_pins


SWITCHES = (
    ("savings", "accuracy_lite", "savings_accuracy_lite"),
    ("woot", "condition_lock", "woot_condition_lock"),
    ("amazon", "renewed_title_lock", "amazon_renewed_title_lock"),
)


@pytest.mark.parametrize("section,name,attr", SWITCHES)
def test_accuracy_lite_switch_default_shipped_and_missing(tmp_path, section, name, attr):
    assert getattr(AppConfig(), attr) is False
    assert getattr(load_config(), attr) is True
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    assert getattr(load_config(path), attr) is False
    for value in (False, True):
        path.write_text(json.dumps({section: {name: value}}))
        assert getattr(load_config(path), attr) is value


@pytest.mark.parametrize("section,name,attr", SWITCHES)
@pytest.mark.parametrize("value", [1, 0, "true", "false", None, [], {}])
def test_accuracy_lite_switch_rejects_non_boolean(tmp_path, section, name, attr, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({section: {name: value}}))
    with pytest.raises(ValueError, match=rf"^{section}\.{name} must be a boolean$"):
        load_config(path)


@pytest.mark.parametrize("section,name,attr", SWITCHES)
def test_accuracy_lite_historical_pins(section, name, attr):
    cfg = load_config()
    assert getattr(history_pins.pre_accuracy_lite(cfg), attr) is False
    cfg = load_config()
    assert getattr(history_pins.pre_social_profiles(cfg), attr) is False
    # Copy the raw-pop entry points used by short_cards.
    for pin in (history_pins.pre_task_319_raw, history_pins.pre_hide_deal_site_links_raw,
                history_pins.pre_keepa_focus_finder_raw, history_pins.pre_keepa_starter_raw):
        raw = load_config().raw
        pin(raw)
        assert name not in raw.get(section, {})
