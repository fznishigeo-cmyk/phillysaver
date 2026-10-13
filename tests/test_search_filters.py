"""Search and returning-visitor behavior against the published page, offline."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig
from tests.test_publish_site import make_deal
from tests.test_support import NOW, render


@pytest.mark.parametrize('support,open_access', [(False, False), (False, True), (True, True)])
def test_search_filters_and_refresh(tmp_path, support, open_access):
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
    deals[2]['price_rechecked_at'] = NOW.isoformat()  # live Amazon price in the search fixture
    out, _, _, _ = render(tmp_path, AppConfig(support_enabled=support, paywall_open_access=open_access), deals)
    home = (out / 'index.html').read_text()
    assert home.count('id="q"') == 1
    header = home.split('<header>', 1)[1].split('</header>', 1)[0]
    assert home.index('id="q"') < home.index('</header>') < home.index('id="amazon-strip"') < home.index('id="cattabs"')
    assert 'aria-label="Clear search"' in header
    assert 'id="searchcount" role="status" aria-live="polite"' in header
    assert 'aria-label="Store"><option value="">All stores' in home
    assert "srcsel" not in home and "Found by:" not in home
    assert home.count('id="load-status" class="load-status" role="status" hidden') == 1
    assert home.index('id="load-status"') < home.index('id="content"')
    assert 'id="load-retry" type="button" class="pwbtn alt">Retry</button>' in home
    assert '.load-status .pwbtn{min-width:44px;min-height:44px;flex:0 0 auto}' in home
    wall = home.split('<div id="content"', 1)[1].split('<div id="paywall"', 1)[0]
    assert 'class="getdeal" href="https://' in wall and 'class="credit">via <a href="https://' in wall
    assert '<noscript>' in wall and 'id="load-status"' not in wall
    for i, script in enumerate(re.findall(r'<script>(.*?)</script>', home, re.S)):
        path = tmp_path / f'script-{i}.js'
        path.write_text(script)
        subprocess.run(['node', '--check', str(path)], check=True, capture_output=True, text=True)
    result = subprocess.run(['node', str(Path(__file__).with_name('search_filters_logic.cjs')),
                             str(out / 'index.html'), str(out / 'deals.json')], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("support", [True, False])
def test_static_and_live_deal_page_journeys_offline(tmp_path, support):
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    cfg = AppConfig(support_enabled=support, paywall_open_access=True, seo_store_page_min_deals=1,
                    affiliate_amazon_tag="example-20", affiliate_ebay_campaign_id="5339221823",
                    affiliate_sovrn_enabled=True, affiliate_sovrn_key="0123456789abcdef0123456789abcdef",
                    affiliate_competitor_tracker_hosts=["go.partner.test"])
    deals = [make_deal(dedupe_key=f"detail-{i}", item=f"Kitchen offer {i}", category="kitchen",
                       codes=['SAVE<&"\'10'], stack_steps=["Enter the code"]) for i in range(12)]
    deals[1].update(store="Amazon", canonical_url="https://www.amazon.com/dp/B012345678?tag=old-20")
    deals[2].update(store="eBay", canonical_url="https://www.ebay.com/itm/123456789012", codes=[], stack_steps=[])
    deals[3].update(codes=["SAVE10"], stack_steps=["Use code SAVE10"])
    deals[4].update(link_as_posted="https://go.partner.test/credit?aff=partner")
    out, _, _, _ = render(tmp_path, cfg, deals)
    result = subprocess.run(["node", str(Path(__file__).with_name("deal_pages_logic.cjs")), str(out)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('support,open_access', [(False, False), (False, True), (True, True)])
def test_price_eligibility_browse_history_and_return(tmp_path, support, open_access):
    cfg = AppConfig(support_enabled=support, paywall_open_access=open_access,
                    engagement={'saved_deals': True} if support else {}, seo_price_pages=[10, 25, 50],
                    custom_domain='wowpriced.com')
    out, _, _, _ = render(tmp_path, cfg, [make_deal()])
    home = (out / 'index.html').read_text()
    assert '<dialog id="filtersheet" aria-labelledby="filtertitle" data-clarity-mask="true">' in home
    assert 'aria-haspopup="dialog" aria-controls="filtersheet" aria-expanded="false"' in home
    assert '#filterform input,#filterform select{font-size:16px;min-height:44px}' in home
    assert 'filterSheet.showModal()' in home and 'filterSheet.addEventListener("close"' in home
    assert '<link rel="canonical" href="https://wowpriced.com/">' in home
    assert '?price=' not in (out / 'sitemap.xml').read_text()
    result = subprocess.run(['node', str(Path(__file__).with_name('browse_logic.cjs')),
                             str(out / 'index.html'), str(out / 'deals.json')], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_home_presets_reuse_price_page_configuration(tmp_path):
    cfg = AppConfig(seo_price_pages=[15, 35], support_enabled=True)
    out, doc, _, _ = render(tmp_path, cfg)
    home = (out / 'index.html').read_text()
    assert 'var PRICE_PRESETS = [15, 35];' in home
    assert '<option value="15">Under $15</option>' in home
    assert '<option value="35">Under $35</option>' in home
    assert '<option value="25">' not in home
    old_version = doc['meta']['site_version']
    cfg.seo_price_pages = []
    out, doc, _, _ = render(tmp_path, cfg)
    assert 'var PRICE_PRESETS = [];' in (out / 'index.html').read_text()
    assert doc['meta']['site_version'] != old_version
