"""Operator 2026-10-23 ~20:05Z/~20:10Z: typed words wait through the link's analyzer box (tool_words_analyzer)."""
import re
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import words_analyzer
from tests.history_pins import pre_social_profiles
from tests.test_publish_site import make_deal
from tests.test_support import NOW
from tests.test_tools_merge import build, files, rows as merge_rows

WORDS = ("Cordless stick vacuum cleaner, lightweight", "Robot vacuum with self-emptying base")
CLAIMS = re.compile(r"price|review|grad|rating|stars?\b", re.I)


def rows():
    return merge_rows() + [make_deal(dedupe_key=f"words-{i}", item=item, brand="", store="Amazon", category="home-kitchen",
                                     verify_tier="verified", codes=[], stack_steps=[], conditions=["new"], final_price=89.0 + i,
                                     regular_price=199.0, pct_at_checkout=55, abs_savings=110 - i, regular_price_basis="List price",
                                     asin=f"B0WORDS{i:03d}", canonical_url=f"https://www.amazon.com/dp/B0WORDS{i:03d}",
                                     first_seen_at=NOW.isoformat(), price_rechecked_at=NOW.isoformat(),
                                     critic={"plausible": True, "confidence": .99, "reason": "Fixture"},
                                     image_url=f"https://m.media-amazon.com/images/I/words{i}.jpg") for i, item in enumerate(WORDS)]


def test_switch_shipped_on_default_off_strict_and_pinned(tmp_path):
    assert load_config().tool_words_analyzer is True  # operator 2026-10-23 ~20:05Z
    assert AppConfig().tool_words_analyzer is False
    assert pre_social_profiles(AppConfig(tool_words_analyzer=True)).tool_words_analyzer is False
    path = tmp_path / "words.yaml"
    path.write_text("tool_words_analyzer: 1\n")
    with pytest.raises(ValueError, match="tool_words_analyzer"):
        load_config(path)


def test_off_changes_no_byte_and_needs_tools_merge(tmp_path, monkeypatch):
    off, _ = build(tmp_path, monkeypatch, True, "off", tool_words_analyzer=False)
    alone, _ = build(tmp_path, monkeypatch, False, "alone", tool_words_analyzer=True)  # no words box without the merge
    # The same builds with the feature removed altogether.
    for name in ("spotter_script", "review_script"):
        monkeypatch.setattr(words_analyzer, name, lambda script, cfg: script)
    monkeypatch.setattr(words_analyzer, "landing_page", lambda html, cfg: html)
    without, _ = build(tmp_path, monkeypatch, True, "without", tool_words_analyzer=False)
    assert files(off) == files(without)
    alone_without, _ = build(tmp_path, monkeypatch, False, "alone-without", tool_words_analyzer=True)
    assert files(alone) == files(alone_without)
    for name, data in files(off).items():
        for marker in (b"analyzeWords", b"Searching for deals", b"Searching our verified deals"):
            assert marker not in data, (name, marker)
    assert b"so the buttons appear right away" in files(off)["amazon-discount-finder/index.html"]


def test_scripts_same_wait_honest_steps_and_no_lookup(tmp_path, monkeypatch):
    out, _ = build(tmp_path, monkeypatch, True)
    templates = Path(words_analyzer.__file__).with_name("templates")
    for name, snippet, start in (("deal-spotter.js", "tool-words-analyzer.js", "analyzeWords(raw, field, opener); return;"),
                                 ("review-checker.js", "tool-words-analyzer-review.js", "analyzeWords(value); return;}")):
        result = subprocess.run(["node", "--check", str(out / name)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        script, words = (out / name).read_text(), (templates / snippet).read_text()
        assert words in script and start in script
        code = re.sub(r"//[^\n]*", "", words)
        assert "/api/" not in code and "fetch(" not in code and "tool_grade" not in code  # no lookup, no grade
        # Operator ~20:10Z: the same wait as a link (10-25 s).
        assert code.count("duration:10000 + Math.random() * 15000") == 1
        assert script.count("duration:10000 + Math.random() * 15000") == 2
        texts = re.findall(r"'([^']*please wait\.\.|[^']*\.\.)'", code)
        assert len(texts) == 4 and not any(CLAIMS.search(t) for t in texts), texts
    # The keyword page no longer promises the buttons at once.
    finder = (out / "amazon-discount-finder" / "index.html").read_text()
    assert "right away" not in finder and "Typed words never start a price lookup. When you find" in finder


def test_browser_phone_and_laptop(tmp_path):
    result = subprocess.run(["node", str(Path(__file__).with_name("words_analyzer_browser.cjs")), str(tmp_path)],
                            capture_output=True, text=True, timeout=600, cwd=Path(__file__).parent.parent)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    for width in (390, 1280):
        assert f"{width}: ok" in result.stdout, result.stdout[-2000:]
