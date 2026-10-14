"""Offline collection contract, truthful coverage, shadow-only ranking and off bytes."""
import copy
import json
from pathlib import Path
import subprocess
from datetime import timedelta

import httpx
import pytest

from dealradar import clicks
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.growth import report
from dealradar.http_client import PoliteFetcher
from dealradar.publish import click_signal
from dealradar.publish.site import _serialize_deal
from tests.test_publish_site import NOW, make_deal
from tests.test_seo import cfg, db, _render

ROOT = Path(__file__).resolve().parents[1]


def enable(cfg):
    cfg.custom_domain = "wowpriced.com"
    cfg.raw["first_party_clicks"] = {"enabled": True, "endpoint": "https://click.wowpriced.com"}


def tree(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def published(n=3):
    return [_serialize_deal(make_deal(dedupe_key=f"click-{i}", conditions=[], codes=[]), NOW) for i in range(n)]


def test_default_off_and_config_caps():
    assert not clicks.enabled(AppConfig()) and clicks.enabled(load_config())  # shipped on 2026-10-14
    cfg = AppConfig(raw={"first_party_clicks": {"enabled": "true", "hourly_cap": 999999, "shadow_weight": float('nan')}})
    assert not clicks.enabled(cfg)
    assert clicks.settings(cfg)["hourly_cap"] == 3000
    assert clicks.settings(cfg)["shadow_weight"] == 10


@pytest.mark.parametrize("url", ["https://evil.test", "http://click.wowpriced.com", "https://click.wowpriced.com.evil.test",
    "https://wowpriced.com", "https://www.wowpriced.com", "https://user:secret@click.wowpriced.com", "https://click.wowpriced.com/?secret=x",
    "https://click.wowpriced.com/path", "https://click.wowpriced.com/#x", "https://click.wowpriced.com:443",
    "https://evil.test\\.wowpriced.com", "https://click.wowpriced.com\n"])
def test_endpoint_fail_closed(cfg, url):
    enable(cfg); cfg.raw["first_party_clicks"]["endpoint"] = url
    assert clicks.endpoint(cfg) == ""


@pytest.mark.parametrize("support,open_access", [(False,False),(False,True),(True,False),(True,True)])
def test_off_output_identical_and_on_then_off_removes_assets(db, cfg, tmp_path, monkeypatch, support, open_access):
    monkeypatch.setattr('dealradar.publish.site.util_now', lambda: NOW)
    cfg.support_enabled, cfg.paywall_open_access = support, open_access
    a, b = tmp_path / 'baseline', tmp_path / 'off'
    db.meta_set("teaser_recent", "[]"); _render(db,cfg,a)
    cfg.raw['first_party_clicks'] = copy.deepcopy(load_config().raw['first_party_clicks'])
    db.meta_set("teaser_recent", "[]"); _render(db,cfg,b)
    assert tree(a) == tree(b)
    enable(cfg); db.meta_set("teaser_recent", "[]"); _render(db,cfg,b)
    on = tree(b)
    assert 'click-signal.js' in on
    assert b'data-click-key=' in on['index.html']
    assert b'click-signal.js' in on['index.html']
    offdoc = json.loads(tree(a)['deals.json']); ondoc = json.loads(on['deals.json'])
    assert offdoc['deals'] == ondoc['deals']
    for row in ondoc['deals']:
        assert 'clicks' not in row and '_click_key' not in row and 'engagement_score' not in row
    assert b'click-signal.js' not in on['welcome.html']
    cfg.raw['first_party_clicks']['enabled'] = False; db.meta_set('teaser_recent', '[]'); _render(db,cfg,b)
    assert tree(a) == tree(b)


def test_static_keys_cover_details_hubs_and_locked_stubs_do_not_leak(db,cfg,tmp_path):
    enable(cfg)
    out=tmp_path/'site'; result=_render(db,cfg,out)
    rows=json.loads((out/'deals.json').read_text())['deals']
    for row in rows:
        if row.get('locked'):
            assert not any(('data-click-key="'+row['key']+'"') in p.read_text() for p in out.rglob('*.html'))
        elif row.get('deal_path'):
            page=(out/row['deal_path'].strip('/')/'index.html').read_text()
            assert 'data-click-key="'+row['key']+'"' in page
    assert all('_click_key' not in d for d in result['full_payload']['deals'])
    assert len(json.loads(db.meta_get('click_signal_shadow'))['features']) == 2
    before=tree(out); click_signal.publish(out,cfg); assert tree(out)==before


def test_browser_and_worker_harnesses():
    for command in [['node','--test','tests/click_signal_logic.cjs'], ['node','--test','cloudflare/subscribe-worker/clicks.test.mjs']]:
        proc=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=60)
        assert proc.returncode == 0, proc.stdout+proc.stderr


def response(since, until, key, count=3):
    return {'since':since,'until':until,'hours':[
        {'hour':h,'counts':{key:count} if h==until-1 else {},'capped':False,'available':True} for h in range(since,until)]}


def test_ingestion_once_per_run_and_absolute_hour_upserts(db,cfg,monkeypatch):
    enable(cfg); monkeypatch.setenv('CLICK_SIGNAL_TOKEN','fixture-secret-token')
    hour=int(NOW.timestamp()//3600); key=published()[0]['key']
    db.meta_set('click_signal_cursor',str(hour-1))
    requests=[]; count=3
    def handler(req):
        requests.append(req)
        assert req.headers['Authorization']=='Bearer fixture-secret-token'
        assert 'dealradar' in req.headers['User-Agent']
        assert req.url.path=='/c/counts'
        return httpx.Response(200,json=response(hour-2,hour,key,count))
    fetcher=PoliteFetcher(db,cfg,transport=httpx.MockTransport(handler),sleep_fn=lambda _:None)
    result=clicks.ingest(db,cfg,NOW,fetcher,'run-1')
    assert result['status']=='ok' and len(requests)==1
    assert clicks.ingest(db,cfg,NOW,fetcher,'run-1')['status']=='already_read'
    # Replay overlaps the most recent closed hour, permitting late D1 completion.
    def later(req):
        requests.append(req)
        return httpx.Response(200,json=response(hour-1,hour,key,count))
    fetcher.close(); fetcher=PoliteFetcher(db,cfg,transport=httpx.MockTransport(later),sleep_fn=lambda _:None)
    assert clicks.ingest(db,cfg,NOW,fetcher,'run-2')['status']=='ok'
    assert db.conn.execute('SELECT SUM(count) FROM deal_clicks').fetchone()[0]==3
    count=5
    clicks.ingest(db,cfg,NOW,fetcher,'run-3')
    assert db.conn.execute('SELECT SUM(count) FROM deal_clicks').fetchone()[0]==5
    count=2
    clicks.ingest(db,cfg,NOW,fetcher,'run-4')
    assert db.conn.execute('SELECT SUM(count) FROM deal_clicks').fetchone()[0]==5
    assert clicks.ingest(db,cfg,NOW,fetcher,'run-1')['status']=='already_read'
    fetcher.close()


@pytest.mark.parametrize('failure',['redirect','500','malformed','extra_hour','invalid_key','too_many'])
def test_ingestion_failures_never_advance_cursor_or_partially_write(db,cfg,monkeypatch,caplog,failure):
    enable(cfg); monkeypatch.setenv('CLICK_SIGNAL_TOKEN','fixture-secret-token')
    hour=int(NOW.timestamp()//3600); db.meta_set('click_signal_cursor',str(hour-1))
    body=response(hour-2,hour,published()[0]['key'])
    if failure=='malformed': body['hours'][1]['counts']={'private item':2}
    if failure=='extra_hour': body['until']=hour+1
    if failure=='invalid_key': body['hours'][1]['counts']={'secret':2}
    if failure=='too_many': body['hours'][1]['counts']={published()[0]['key']:3001}
    calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(302 if failure=='redirect' else 500 if failure=='500' else 200,
            headers={'Location':'https://evil.test/fixture-secret-token'},json=body)
    fetcher=PoliteFetcher(db,cfg,transport=httpx.MockTransport(handler),sleep_fn=lambda _:None)
    assert clicks.ingest(db,cfg,NOW,fetcher,'run')['status']=='unavailable'
    assert len(calls)==1 and db.meta_get('click_signal_cursor')==str(hour-1)
    assert db.conn.execute('SELECT COUNT(*) FROM deal_clicks').fetchone()[0]==0
    assert 'fixture-secret-token' not in caplog.text and 'private item' not in caplog.text
    fetcher.close()


def test_off_missing_config_no_network_or_observations(db,cfg,monkeypatch):
    class Never:
        def api_get(*args,**kwargs): pytest.fail('unexpected network')
    assert clicks.ingest(db,cfg,NOW,Never(),'run')['status']=='off'
    clicks.observe_publication(db,cfg,NOW,published())
    assert clicks.features(db,cfg,NOW,published())==[]
    assert db.meta_get('click_signal_publication') is None
    enable(cfg); monkeypatch.delenv('CLICK_SIGNAL_TOKEN',raising=False)
    assert clicks.ingest(db,cfg,NOW,Never(),'run')['status']=='needs_config'


def test_exposure_intervals_cross_hours_reruns_disappearance_gaps_and_retention(db,cfg):
    enable(cfg); rows=published(); start=NOW.replace(minute=50)
    clicks.observe_publication(db,cfg,start,rows)
    assert db.conn.execute('SELECT COUNT(*) FROM deal_click_shown').fetchone()[0]==0
    end=start+timedelta(minutes=20)
    clicks.observe_publication(db,cfg,end,rows[:1])
    saved=[tuple(r) for r in db.conn.execute('SELECT * FROM deal_click_shown')]
    assert len(saved)==6 and all(r[2]==600 for r in saved)
    clicks.observe_publication(db,cfg,end,rows)
    clicks.observe_publication(db,cfg,start,rows)
    assert saved==[tuple(r) for r in db.conn.execute('SELECT * FROM deal_click_shown')]
    clicks.observe_publication(db,cfg,end+timedelta(minutes=10),rows[:1])
    assert db.conn.execute('SELECT SUM(seconds) FROM deal_click_shown WHERE deal_key=?',(rows[0]['key'],)).fetchone()[0]==1800
    clicks.observe_publication(db,cfg,end+timedelta(hours=2),rows)
    assert db.conn.execute('SELECT SUM(seconds) FROM deal_click_shown').fetchone()[0]==4200
    clicks.observe_publication(db,cfg,end+timedelta(days=31),rows)
    assert db.conn.execute('SELECT COUNT(*) FROM deal_click_shown').fetchone()[0]==0


def test_features_copy_verified_facts_null_without_coverage_and_shadow_never_mutates(db,cfg,monkeypatch):
    enable(cfg); rows=published(125); original=copy.deepcopy(rows)
    assert all(r['clicks'] is None for r in clicks.features(db,cfg,NOW,rows))
    hour=int(NOW.timestamp()//3600)-1
    with db.conn:
        db.conn.execute('INSERT INTO deal_click_coverage VALUES (?,0)',(hour,))
        db.conn.executemany('INSERT INTO deal_click_shown VALUES (?,?,3600,?)',[(d['key'],hour,i) for i,d in enumerate(rows,1)])
        db.conn.execute('INSERT INTO deal_clicks VALUES (?,?,3000)',(rows[-1]['key'],hour))
    called=[]
    monkeypatch.setattr('dealradar.publish.social.plenty_of_stock',lambda db,d,cfg: called.append(d['key']) or True)
    features=clicks.features(db,cfg,NOW,rows)
    assert len(called)==125
    f=features[-1]; assert f['clicks']==3000 and f['shown_hours']==1 and f['clicks_per_hour_shown']==3000
    assert f['discount_pct']==rows[-1]['pct_at_checkout'] and f['savings_usd']==rows[-1]['abs_savings']
    assert f['corroboration_count'] is None and f['confirmation_age_hours'] is None
    assert f['stock_evidence'] is True and f['position']==125 and f['age_hours']==2
    result=clicks.shadow(features,cfg)
    assert rows[-1]['key'] in result['entered'] and result['changed_positions']>0
    assert original==rows
    zero=[dict(r,engagement_score=0) for r in features]
    assert clicks.shadow(zero,cfg)['changed_positions']==0
    clicks.record_shadow(db,cfg,NOW,rows)
    data=report._click_signal(db.path,cfg) if hasattr(db,'path') else clicks.report_data(db,cfg)
    assert data['status']=='ok'
    lines='\n'.join(clicks.report_lines(data))
    assert 'not impressions or people' in lines and 'Live ranking is unchanged' in lines
    _,text=report.build_report(cfg,{'click_signal':data},{'click_signal':'ok'},NOW.date())
    assert 'First-party clicks (shadow' in text


def test_deploy_config_does_nothing_off_requires_secrets_and_uses_same_knobs(cfg,tmp_path,monkeypatch):
    from scripts.click_signal_deploy import configure
    path=tmp_path/'worker.toml'; original=(ROOT/'cloudflare/subscribe-worker/wrangler.toml').read_text(); path.write_text(original)
    assert not configure(path,cfg) and path.read_text()==original
    enable(cfg)
    with pytest.raises(ValueError): configure(path,cfg)
    monkeypatch.setenv('CLICK_SIGNAL_D1_ID','11111111-1111-1111-1111-111111111111')
    monkeypatch.setenv('CLICK_SIGNAL_TOKEN','x'*40)
    assert configure(path,cfg)
    import tomllib
    values=tomllib.loads(path.read_text())
    assert values['vars']['CLICK_SIGNAL_ENABLED']=='true'
    assert values['vars']['CLICK_SIGNAL_HOURLY_CAP']=='1000'
    assert values['d1_databases'][0]['binding']=='CLICKS'
    assert 'x'*40 not in path.read_text()


def test_existing_worker_origin_is_allowed_but_other_workers_are_not(cfg):
    enable(cfg)
    cfg.newsletter_worker_url = 'https://wowpriced-subscribe.wowpriced.workers.dev'
    cfg.raw['first_party_clicks']['endpoint'] = cfg.newsletter_worker_url
    assert clicks.endpoint(cfg) == cfg.newsletter_worker_url
    cfg.raw['first_party_clicks']['endpoint'] = 'https://someone-else.workers.dev'
    assert not clicks.endpoint(cfg)


def test_unobserved_hours_stay_unavailable_and_long_backlog_is_bounded(db,cfg,monkeypatch):
    enable(cfg); monkeypatch.setenv('CLICK_SIGNAL_TOKEN','fixture-secret-token')
    hour=int(NOW.timestamp()//3600)
    db.meta_set('click_signal_cursor',str(hour-1000))
    calls=[]
    def handler(req):
        calls.append(req)
        assert req.url.params['since']==str(hour-168)
        body={'since':hour-168,'until':hour-162,'hours':[
            {'hour':h,'counts':{},'capped':False,'available':False} for h in range(hour-168,hour-162)]}
        return httpx.Response(200,json=body)
    fetcher=PoliteFetcher(db,cfg,transport=httpx.MockTransport(handler),sleep_fn=lambda _:None)
    result=clicks.ingest(db,cfg,NOW,fetcher,'backlog')
    assert result['status']=='ok' and result['unavailable_hours']==6 and len(calls)==1
    assert db.conn.execute('SELECT COUNT(*) FROM deal_click_coverage').fetchone()[0]==0
    assert all(r['clicks'] is None for r in clicks.features(db,cfg,NOW,published()))
    fetcher.close()


def test_disabling_resets_exposure_anchor_before_a_quick_reenable(db,cfg):
    enable(cfg); rows=published()
    clicks.observe_publication(db,cfg,NOW,rows)
    cfg.raw['first_party_clicks']['enabled']=False
    clicks.observe_publication(db,cfg,NOW+timedelta(minutes=5),[])
    assert db.meta_get('click_signal_publication') is None
    cfg.raw['first_party_clicks']['enabled']=True
    clicks.observe_publication(db,cfg,NOW+timedelta(minutes=10),rows)
    assert db.conn.execute('SELECT COUNT(*) FROM deal_click_shown').fetchone()[0]==0


def test_same_pipeline_run_cannot_add_exposure_rows_with_a_later_retry_clock(db,cfg):
    enable(cfg); rows=published()
    clicks.record_shadow(db,cfg,NOW,rows,run_key='run-one')
    snapshot=db.meta_get('click_signal_publication')
    clicks.record_shadow(db,cfg,NOW+timedelta(minutes=20),rows,run_key='run-one')
    assert db.meta_get('click_signal_publication')==snapshot
    assert db.conn.execute('SELECT COUNT(*) FROM deal_click_shown').fetchone()[0]==0
    clicks.record_shadow(db,cfg,NOW+timedelta(minutes=20),rows,run_key='run-two')
    assert db.conn.execute('SELECT SUM(seconds) FROM deal_click_shown').fetchone()[0]==3600

    before=[tuple(r) for r in db.conn.execute('SELECT * FROM deal_click_shown')]
    clicks.record_shadow(db,cfg,NOW+timedelta(minutes=25),rows,run_key='run-one')
    assert before==[tuple(r) for r in db.conn.execute('SELECT * FROM deal_click_shown')]
