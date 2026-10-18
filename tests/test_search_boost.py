"""Operator 2026-10-17: big Amazon button first, adult cards hidden, phone retry."""

import re
import subprocess

import pytest

from dealradar.config import AppConfig, load_config, validated_search, validated_search_boost
from dealradar.publish import search_boost, site
from tests.test_publish_site import make_deal
from tests.test_search_amazon import run_node
from tests.test_support import NOW, render


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)


def config(mode="on", boost=True):
    return AppConfig(search_amazon=True, affiliate_amazon_tag="example-20",
                     search=validated_search({"amazon_results": mode}),
                     search_boost=validated_search_boost({"enabled": boost, "exclude_words": ["anal", "g spot", "sex toys",
                                                                                              "butt plug", "lubricant"]}))


def home(tmp_path, cfg):
    tmp_path.mkdir(parents=True, exist_ok=True)
    out, *_ = render(tmp_path, cfg, [make_deal(item="Steel kitchen kettle", codes=[])])
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def test_config_defaults_shipped_and_invalid():
    assert AppConfig().search_boost == {"enabled": False, "exclude_words": []}
    shipped = load_config().search_boost
    assert shipped["enabled"] is True
    assert {"anal", "dildo", "vibrator", "lube", "butt plug", "sex toy"} <= set(shipped["exclude_words"])
    for bad in ([], {"enabled": "yes"}, {"exclude_words": "sex"}, {"exclude_words": ["a.*"]}, {"other": 1}):
        with pytest.raises(ValueError, match="^search_boost"):
            validated_search_boost(bad)


@pytest.mark.parametrize("mode", ["off", "preview", "on"])
def test_off_or_search_off_is_byte_identical(tmp_path, mode):
    off = home(tmp_path / "off", config(mode, boost=False))
    on = home(tmp_path / "on", config(mode, boost=True))
    if mode == "off":
        assert on == off
    else:
        assert on != off and on["index.html"] != off["index.html"]


@pytest.mark.parametrize("mode", ["preview", "on"])
def test_emitted_page_and_logic(tmp_path, mode):
    page = home(tmp_path, config(mode))["index.html"].decode()
    assert search_boost.CSS in page and "search-amazon-top" in page
    for i, script in enumerate(re.findall(r"<script>(.*?)</script>", page, re.S)):
        path = tmp_path / f"script-{i}.js"
        path.write_text(script)
        check = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        assert check.returncode == 0, check.stderr
    (tmp_path / "index.html").write_text(page)
    result = run_node("search_boost_logic.cjs", tmp_path / "index.html")
    assert result.returncode == 0, result.stdout + result.stderr
