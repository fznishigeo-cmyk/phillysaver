"""Task 315: card_tidy hides header visit counts, retaining card NEW badges."""
import subprocess

import pytest

from dealradar.config import load_config
from tests.test_search_more import offline  # noqa: F401 (fixed clock, no network)
from tests.test_support import render


@pytest.mark.parametrize('engagement', [False, True])
@pytest.mark.parametrize('tidy', [False, True])
def test_published_visit_status_and_badges(tmp_path, engagement, tidy):
    cfg = load_config()
    cfg.card_tidy = tidy
    if not engagement:
        cfg.engagement = {}
    out, *_ = render(tmp_path, cfg)
    result = subprocess.run(
        ['node', 'tests/engagement_header_logic.cjs', str(out / 'index.html'),
         str(tidy).lower(), str(engagement).lower()],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
