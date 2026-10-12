"""Search and returning-visitor behavior against the published page, offline."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig
from tests.test_publish_site import make_deal
from tests.test_support import render


@pytest.mark.parametrize('support', [False, True])
def test_search_filters_and_refresh(tmp_path, support):
    if not shutil.which('node'):
        pytest.skip('node unavailable')
    deals = [make_deal(dedupe_key=f'search-{i}', store=store, item=item, category=cat)
             for i, (store, item, cat) in enumerate([
                 ('Walmart', 'BestOffice 4-Shelf Bookcase', 'home'),
                 ('walmart.com', 'Ninja Professional Plus Blender', 'kitchen'),
                 ('Amazon', 'Café & Thé Espresso', 'kitchen'),
                 ("Macy's", 'Office Desk', 'home'),
                 ('macys.com', 'Cotton Towel', 'home'),
             ])]
    deals[-1]['sightings'] = [{'source_name': 'dealnews', 'source_url': 'https://dealnews.com/123'}]
    out, _, _, _ = render(tmp_path, AppConfig(support_enabled=support, paywall_open_access=True), deals)
    home = (out / 'index.html').read_text()
    assert home.count('id="q"') == 1
    header = home.split('<header>', 1)[1].split('</header>', 1)[0]
    assert header.index('id="q"') < header.index('id="cattabs"')
    assert 'aria-label="Clear search"' in header
    assert 'id="searchcount" role="status" aria-live="polite"' in header
    assert 'aria-label="Store"><option value="">All stores' in home
    assert "srcsel" not in home and "Found by:" not in home
    for i, script in enumerate(re.findall(r'<script>(.*?)</script>', home, re.S)):
        path = tmp_path / f'script-{i}.js'
        path.write_text(script)
        subprocess.run(['node', '--check', str(path)], check=True, capture_output=True, text=True)
    result = subprocess.run(['node', str(Path(__file__).with_name('search_filters_logic.cjs')),
                             str(out / 'index.html'), str(out / 'deals.json')], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
