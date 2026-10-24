"""The manual live check for Deal Spotter and Review Checker stays read-only."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_tools_live_check_is_manual_read_only_and_keeps_screenshots():
    doc = yaml.safe_load((ROOT / ".github/workflows/tools-live-check.yml").read_text())
    assert list(doc[True]) == ["workflow_dispatch"] and doc["permissions"] == {"contents": "read"}
    steps = doc["jobs"]["look"]["steps"]
    assert "secrets." not in yaml.safe_dump(doc)
    assert steps[-1]["uses"].startswith("actions/upload-artifact@") and steps[-1]["if"] == "always()"
    script = (ROOT / "scripts/tools_live_check.js").read_text()
    for banned in ("git push", "KEEPA", "process.env.KEY", "localStorage.setItem"):
        assert banned not in script
    assert "/deal-spotter/" in script and "/review-checker/" in script and "(?=.*\\d)" in script


def test_tools_live_check_logs_public_price_facts_from_pages_own_response():
    script = (ROOT / "scripts/tools_live_check.js").read_text()
    assert script.index("page.waitForResponse(") < script.index('page.press(tool.input, "Enter")')
    assert 'url.pathname === "/api/search"' in script
    assert 'url.searchParams.get("asin") === asin' in script
    assert 'url.searchParams.get("grade")' in script
    assert ".then(response => response.json())" in script
    assert 'data?.items?.[0] || {}' in script
    assert '${tool.name} api: price_cents=' in script
    for field in ("price_cents", "shipping_cents", "price_includes_shipping", "list_price_cents", "as_of"):
        assert f'{field}=${{item.{field} ?? "none"}}' in script
    assert 'fetch(SITE + "/api/search' not in script
    assert "page.request" not in script
