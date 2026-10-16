"""Niche categories (car parts, office supplies) after every other deal on the front page."""
import json
from pathlib import Path
import subprocess

from dealradar.config import load_config
from dealradar.publish import site
from dealradar.rank import pick_order
from tests.test_amazon_as_of import NOW, config, render

OPTIONS = {"top_picks": {"count": 2}, "niche_categories": ["auto", "office"]}
ROWS = [{"key": "axle", "category": "auto"}, {"key": "lamp", "category": "home", "pick": 0},
        {"key": "labels", "category": "office"}, {"key": "dress", "category": "fashion"},
        {"key": "stale", "category": "home", "price_unconfirmed": True}, {"key": "mug"}]


def keys(rows):
    return [d["key"] for d in rows]


def test_pick_order_sinks_niche_only_when_on():
    assert keys(pick_order(ROWS, OPTIONS)) == ["lamp", "axle", "labels", "dress", "mug", "stale"]
    on = dict(OPTIONS, niche_last=True)
    assert keys(pick_order(ROWS, on)) == ["lamp", "dress", "mug", "axle", "labels", "stale"]


def test_shipped_config_sinks_car_parts():
    front = load_config(Path(__file__).parents[1] / "config.yaml").front_page
    assert front["niche_last"] is True and "auto" in front["niche_categories"]


def test_front_page_order_in_the_published_page(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = config("shipped")
    render(tmp_path / "site", cfg)
    meta = json.loads((tmp_path / "site/deals.json").read_text())["meta"]
    assert meta["front_page"]["niche_last"] is True
    result = subprocess.run(["node", str(Path(__file__).with_name("niche_last.cjs")),
                             str(tmp_path / "site/index.html")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
