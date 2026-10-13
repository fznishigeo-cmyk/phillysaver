"""Effective phone hit areas, including clipping and neighboring controls."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_mobile_audit_hit_area_math_without_playwright():
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    subprocess.run(["node", str(Path(__file__).with_suffix(".cjs"))],
                   check=True, capture_output=True, text=True)
