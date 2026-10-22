"""Task 367: offline grading, reversible publication, current facts and browser QA."""
import json
import re
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config, validated_review_checker
from dealradar.publish import review_checker, site
from tests.test_support import NOW, render
from tests.test_publish_site import make_deal
from tests.test_pwa import tree_hash
from tests.test_home_comment_strip import files


def deals():
    return [make_deal(dedupe_key=f'asin|B0{i:08}', asin=f'B0{i:08}', store='Amazon', item=f'Everyday headphones {i}',
                     canonical_url=f'https://www.amazon.com/dp/B0{i:08}', verify_tier='verified',
                     image_url='https://m.media-amazon.com/images/I/test.jpg', codes=[], conditions=[],
                     click_price_check={'asin':f'B0{i:08}','final':10.0,'buybox':10.0,'at':NOW.isoformat()},
                     price_rechecked_at=NOW.isoformat(), keepa_price_seen_at=NOW.isoformat(), regular_price_basis='List price') for i in range(6)]


def test_defaults_and_shipped():
    assert load_config().review_checker['enabled'] is True
    assert AppConfig().review_checker['enabled'] is False
    assert validated_review_checker({}) == AppConfig().review_checker


@pytest.mark.parametrize('value', [None, False, True, [], 'true', {'enabled':1}, {'enabled':'true'},
    {'private':'SECRET'}, {'min_rating':0}, {'min_reviews':-1}, {'min_reviews':100.5},
    {'steady_rating_delta':float('nan')}, {'pace_max_fraction':float('inf')},
    {'pace_max_fraction':1.1}, {'popular_monthly_sold':True}, {'grade_a_checks':6},
    {'grade_a_checks':1,'grade_b_checks':2}])
def test_invalid_config(value):
    with pytest.raises(ValueError, match='^review_checker'):
        validated_review_checker(value)


def test_shipped_off_matches_pre_edit_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(site,'util_now',lambda:NOW)
    cfg=load_config();cfg.review_checker['enabled']=False
    out,*_=render(tmp_path,cfg)
    assert tree_hash(out)=='dcccf93f1d7c69045ed7492697dcde499dd5369372d8eb6adc697a9a6751b037'


@pytest.mark.parametrize('support,open_access',[(True,True),(False,True),(False,False)])
def test_off_missing_on_rerun_back_to_off_bytes(tmp_path,monkeypatch,support,open_access):
    monkeypatch.setattr(site,'util_now',lambda:NOW)
    cfg=load_config();cfg.support_enabled=support;cfg.paywall_open_access=open_access
    cfg.review_checker={}
    out,*_=render(tmp_path,cfg,deals());before=files(out)
    cfg.review_checker=validated_review_checker({})
    render(tmp_path,cfg,deals());assert files(out)==before
    cfg.review_checker['enabled']=True
    render(tmp_path,cfg,deals());on=files(out)
    assert before!=on
    render(tmp_path,cfg,deals());assert files(out)==on
    # Home is byte-identical except its required footer link, including version/scripts/cards.
    assert (out/'index.html').read_bytes().replace((' · '+review_checker.LINK).encode(),b'')==before['index.html']
    cfg.review_checker['enabled']=False
    render(tmp_path,cfg,deals());assert files(out)==before


def test_published_facts_links_and_schema(tmp_path,monkeypatch):
    monkeypatch.setattr(site,'util_now',lambda:NOW)
    cfg=load_config();out,doc,*_=render(tmp_path,cfg,deals())
    html=(out/'review-checker/index.html').read_text()
    assert html.count('data-check-asin=')==6
    assert '$10.00' in html and '80% off' in html
    assert '6</strong><span>live verified Amazon deals' in html
    assert '1</strong><span>stores in today' in html
    assert 'loading="lazy"' in html
    for row in doc['deals']:
        assert row['url'].replace('&','&amp;') in html
    for text in ['ReviewMeta.com','Adjusted','Original rating','As seen on','FAIL','WARN','avg180','monthlySold','COUNT_REVIEWS']:
        assert text not in html
    assert 'review_checker' in json.loads((out/'search-config.json').read_text())
    for url in review_checker.PATHS:assert url in (out/'sitemap.xml').read_text()
    for slug in ['amazon-deal-checker','amazon-discount-finder']:
        nav=re.search(r'<nav[^>]*aria-label="Tools">(.*?)</nav>',(out/slug/'index.html').read_text(),re.S)[1]
        assert review_checker.LINK in nav
    schema=[json.loads(s) for s in re.findall(r'<script type="application/ld\+json">(.*?)</script>',html,re.S)]
    assert len(next(s for s in schema if s['@type']=='FAQPage')['mainEntity'])==4
    assert 'ReviewMeta Alternative: Free Amazon Review Checker | wowpriced' in (out/'reviewmeta-alternative/index.html').read_text()
    assert '{{' not in html


def test_pure_logic_and_emitted_js(tmp_path):
    cfg=load_config();(tmp_path/'config.json').write_text(json.dumps(cfg.review_checker))
    script=tmp_path/'review.js';script.write_text(review_checker.script(cfg))
    for args in [['node','--check',str(script)],['node','tests/review_checker_logic.mjs',str(tmp_path/'config.json'),str(script)]]:
        result=subprocess.run(args,capture_output=True,text=True,timeout=60)
        assert result.returncode==0,result.stdout+result.stderr


def test_browser_all_sizes_and_states(tmp_path,monkeypatch):
    monkeypatch.setattr(site,'util_now',lambda:NOW)
    out,*_=render(tmp_path,load_config(),deals())
    result=subprocess.run(['node','tests/review_checker_browser.cjs',str(out),str(tmp_path)],capture_output=True,text=True,timeout=180)
    if result.returncode==77:pytest.skip(result.stdout.strip())
    assert result.returncode==0,result.stdout+result.stderr
