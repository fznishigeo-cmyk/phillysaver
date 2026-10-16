"""The optional support ask stays within a successful free join."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig
from dealradar.publish import site
from dealradar.publish.tracking import head_snippet
from tests.test_pwa import sample_deals, tree_hash
from tests.test_support import LINK, MONTHLY, NOW, render
from tests.test_tracking import HARNESS, SAMPLE, _script


@pytest.mark.parametrize("monthly", ["", MONTHLY])
def test_post_join_support_ask_behavior(tmp_path, monthly):
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    cfg = AppConfig(support_enabled=True, support_payment_link=LINK, support_monthly_link=monthly,
                    paywall_api_url="https://api.test")
    out, *_ = render(tmp_path, cfg, sample_deals())
    scripts = set()
    for path in out.rglob("*.html"):
        html = path.read_text()
        if '<footer>' in html:
            scripts.add(next(s for s in re.findall(r"<script>(.*?)</script>", html, re.S)
                             if "window.wpSupport =" in s))
    assert len(scripts) == 1  # home, /join, information, deal and listing footers
    script = tmp_path / "support.js"
    script.write_text(scripts.pop())
    subprocess.run(["node", "--check", str(script)], check=True, capture_output=True, text=True)
    subprocess.run(["node", str(Path(__file__).with_name("join_prompt_logic.cjs")), str(script)],
                   check=True, capture_output=True, text=True)


@pytest.mark.parametrize("link,monthly", [("", ""), ("", MONTHLY), ("https://invalid.example/stripe", "")])
def test_missing_support_link_preserves_every_published_byte(tmp_path, monkeypatch, link, monthly):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = AppConfig(support_enabled=True, support_payment_link=link,
                    support_monthly_link=monthly, tracking=SAMPLE)
    cfg.seo_product_pages_enabled = False  # predates product pages
    out, *_ = render(tmp_path, cfg, sample_deals())
    # Task 079 refresh: shared home search/recovery, tracking and version.
    # The missing-link variants still contain no post-join support ask.
    assert tree_hash(out) == "8f8d5cf267c594c6b514d645c0049015a833df48ed276a7eb51e92e8f4b800a3"
    for path in out.rglob("*.html"):
        assert "support_ask_" not in path.read_text()


def test_support_ask_has_phone_targets_theme_tokens_and_no_motion():
    css = (site.TEMPLATES_DIR / "support-ask.css").read_text()
    assert "display:flex;flex-wrap:wrap;gap:8px" in css
    assert "min-height:44px;max-width:100%" in css
    assert "16px/1.4" in css and "overflow-wrap:anywhere" in css
    assert "width:44px;height:44px;font-size:16px" in css
    assert "color:var(--accent)" in css and "color:var(--muted)" in css
    assert "transition:none;animation:none" in css and ":focus-visible" in css


@pytest.mark.parametrize("ga4", [False, True])
def test_support_ask_events_are_ga4_only_and_drop_private_params(tmp_path, ga4):
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    ids = dict(SAMPLE)
    if not ga4:
        ids.pop("ga4_measurement_id")
    cfg = AppConfig(support_enabled=True, support_payment_link=LINK, tracking=ids)
    private = {"email": "PRIVATE@example.com", "amount": 23.45, "store": "PRIVATE-STORE",
               "category": "PRIVATE-CATEGORY", "plan": "PRIVATE-PLAN", "url": "https://private.test/"}
    calls = [["support_ask_shown", {**private, "method": "PRIVATE-METHOD"}],
             ["support_ask_click", {**private, "method": "once"}],
             ["support_ask_click", {**private, "method": "monthly"}],
             ["support_ask_dismiss", {**private, "method": "PRIVATE-METHOD"}]]
    invalid = [["support_ask_click", params] for params in
               ({}, {"method": "year"}, {"method": "constructor"}, {"method": 5}, {"method": ["once"]})]
    (tmp_path / "snippet.js").write_text(_script(head_snippet(cfg)))
    (tmp_path / "harness.cjs").write_text(HARNESS)
    scenarios = {"baseline": {}, "open": {"calls": calls + invalid}, "gpc": {"gpc": True, "calls": calls},
                 "out": {"storage": {"wp.adsOptOut": "1"}, "calls": calls},
                 "blocked": {"blocked": True, "calls": calls}}
    (tmp_path / "scenarios.json").write_text(json.dumps(scenarios))
    result = subprocess.run(["node", "harness.cjs", "snippet.js", "scenarios.json"], cwd=tmp_path,
                            check=True, capture_output=True, text=True)
    runs = json.loads(result.stdout)
    for name in ("open", "gpc", "out", "blocked"):
        assert runs[name]["error"] is None
        sent = [e for e in runs[name]["dataLayer"] if e[0] == "event"]
        assert sent == ([["event", event, {**({"method": params["method"]} if event == "support_ask_click" else {}),
                                           "send_to": ids["ga4_measurement_id"]}]
                         for event, params in calls] if ga4 else [])
        for tag in ("fbq", "ttq", "pintrk", "uetq"):
            assert runs[name][tag] == (runs["baseline"][tag] if name in ("open", "blocked") else None)
    assert "PRIVATE" not in result.stdout and "23.45" not in result.stdout and "private.test" not in result.stdout


@pytest.mark.parametrize("enabled,link", [(False, LINK), (True, ""), (True, "https://invalid.example/stripe")])
def test_support_ask_tracking_is_absent_without_an_enabled_link(enabled, link):
    assert "support_ask_" not in head_snippet(AppConfig(support_enabled=enabled, support_payment_link=link,
                                                     tracking=SAMPLE))
