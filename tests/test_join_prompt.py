"""Shared sign-up card, conservative prompt rules and free Google sign-in."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from tests.test_support import render


def test_join_prompt_behavior(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    out, _, _, _ = render(tmp_path, AppConfig(support_enabled=True, paywall_api_url="https://api.test"))
    scripts = re.findall(r"<script>(.*?)</script>", (out / "index.html").read_text(), re.S)
    script = tmp_path / "support.js"
    script.write_text(next(s for s in scripts if "window.wpSupport =" in s))
    subprocess.run(["node", str(Path(__file__).with_name("join_prompt_logic.cjs")), str(script)],
                   check=True, capture_output=True, text=True)


def test_shared_card_and_phone_cap(tmp_path):
    out, _, _, _ = render(tmp_path, AppConfig(support_enabled=True))
    home, join = [(out / name).read_text() for name in ("index.html", "join.html")]
    def card(html):
        html = re.search(r'<section class="joincard".*?</section>', html, re.S)[0]
        html = re.sub(r'<button[^>]*data-join-close.*?</button>', '', html)
        return re.sub(r'\s+', ' ', html.replace('h2', 'h1')).strip()
    # Step one is identical on /join and in the prompt/header popover.
    assert card(home) == card(join)
    assert 'id="join-prompt" class="joinoverlay" hidden' in home
    assert 'role="dialog" aria-modal="true"' in home
    assert 'id="join-prompt"' not in join
    for html in (home, join):
        assert 'id="join-google" class="joingoogle" hidden' in html
        assert 'Continue with Apple' not in html
        assert '<div id="join-email-fields" hidden>' in html
        assert 'placeholder="Email"' in html and 'disabled>Continue</button>' in html
        assert 'id="join-back"' in html and "What's your email?" in html
        assert '<span aria-hidden="true">@</span> Continue with email' in html
        assert 'height:52px' in html and 'height:56px' in html and 'border-radius:999px' in html
        assert '.joingoogle{' in html and re.search(r'\.joingoogle\{[^}]*border-radius:12px', html)
        assert 'By continuing, you agree to wowpriced\'s <a href="/privacy.html">privacy policy</a>.' in html
        assert '<p id="join-description">The day\'s best deals in one email. Free.</p>' in html
        assert 'width:360px' in html and 'background:transparent' in html
    css = re.search(r'@media \(max-width:640px\)\{\s*\.joinoverlay.*?\n\}', home, re.S)[0]
    assert 'align-items:flex-end' in css
    assert 'max-height:45vh;max-height:45dvh;overflow:auto' in css
    assert 'body.style.overflow' not in home  # page scrolling is never locked


@pytest.mark.parametrize("prompt", [True, False])
def test_join_switches(tmp_path, prompt):
    config = tmp_path / "config.yaml"
    config.write_text(f'support:\n  enabled: true\njoin:\n  prompt: {str(prompt).lower()}\n  prompt_delay_s: 29\n  apple: false\n')
    cfg = load_config(config)
    assert cfg.join_prompt is prompt and cfg.join_prompt_delay_s == 29 and cfg.join_apple is False
    out, doc, _, _ = render(tmp_path, cfg)
    assert doc["meta"]["join"] == {"prompt": prompt, "prompt_delay_s": 29, "apple": False}
    # Disabling the automatic prompt leaves the manually opened header card available.
    assert 'id="join-prompt"' in (out / "index.html").read_text()
    assert 'Continue with email' in (out / "join.html").read_text()
    config.write_text('join:\n  prompt_delay_s: -1\n')
    assert load_config(config).join_prompt_delay_s == 15
    assert load_config().join_prompt and not load_config().join_apple


def test_prompt_events_reach_tracking(tmp_path):
    import json
    from dealradar.publish.tracking import head_snippet
    from tests.test_tracking import HARNESS, SAMPLE, _script

    if not shutil.which("node"):
        pytest.skip("node unavailable")
    snippet = head_snippet(AppConfig(support_enabled=True, tracking=SAMPLE))
    (tmp_path / "snippet.js").write_text(_script(snippet))
    (tmp_path / "harness.cjs").write_text(HARNESS)
    names = ["signup_prompt_shown", "signup_prompt_closed"]
    scenarios = {"normal": {"calls": [[name] for name in names]},
                 "gpc": {"gpc": True, "calls": [[name] for name in names]}}
    (tmp_path / "scenarios.json").write_text(json.dumps(scenarios))
    result = subprocess.run(["node", "harness.cjs", "snippet.js", "scenarios.json"],
                            cwd=tmp_path, check=True, capture_output=True, text=True)
    runs = json.loads(result.stdout)
    for run in runs.values():
        assert run["error"] is None
        assert [e[1] for e in run["dataLayer"] if e[0] == "event"] == names
    assert runs["gpc"]["fbq"] is None
    assert [e[1] for e in runs["normal"]["fbq"] if e[0] == "trackCustom"] == [
        "SignupPromptShown", "SignupPromptClosed"]
