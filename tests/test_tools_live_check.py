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
