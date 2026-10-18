"""Operator 2026-10-17 (Verizon video): circling border + "Search all of Amazon" button."""

import json
import re
import subprocess

import pytest

from dealradar.config import AppConfig, load_config, validated_search, validated_search_boost
from dealradar.publish import search_boost, search_circle, site
from tests.test_publish_site import make_deal
from tests.test_search_amazon import run_node
from tests.test_support import NOW, render


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)


def config(circle=True, boost=True):
    return AppConfig(search_amazon=True, affiliate_amazon_tag="example-20",
                     search=validated_search({"amazon_results": "on"}), search_circle=circle,
                     search_boost=validated_search_boost({"enabled": boost, "exclude_words": [
                         "anal", "g spot", "sex toys", "butt plug", "lubricant"]}))


def home(tmp_path, cfg):
    tmp_path.mkdir(parents=True, exist_ok=True)
    out, *_ = render(tmp_path, cfg, [make_deal(item="Steel kitchen kettle", codes=[])])
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def test_config_defaults_and_invalid(tmp_path):
    assert AppConfig().search_circle is False
    assert load_config().search_circle is True
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"search_circle": "yes"}))
    with pytest.raises(ValueError, match="^search_circle"):
        load_config(path)


@pytest.mark.parametrize("boost", [True, False])
def test_off_or_boost_off_is_byte_identical(tmp_path, boost):
    off = home(tmp_path / "off", config(circle=False, boost=boost))
    on = home(tmp_path / "on", config(circle=True, boost=boost))
    assert (on != off) is boost


def test_emitted_page_and_logic(tmp_path):
    page = home(tmp_path, config())["index.html"].decode()
    assert search_circle.css() in page and search_boost.CSS not in page
    assert "conic-gradient(from var(--wpspin)" in page and "@keyframes wpspin" in page
    for i, script in enumerate(re.findall(r"<script>(.*?)</script>", page, re.S)):
        path = tmp_path / f"script-{i}.js"
        path.write_text(script)
        check = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        assert check.returncode == 0, check.stderr
    (tmp_path / "index.html").write_text(page)
    result = run_node("search_circle_logic.cjs", tmp_path / "index.html")
    assert result.returncode == 0, result.stdout + result.stderr
