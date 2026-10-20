"""Monitor walkthrough against emitted tools, with no browser or network."""
import shutil
import subprocess

import pytest


def test_tools_walkthrough_offline():
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    result = subprocess.run(["node", "tests/site_check_tools.cjs"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
