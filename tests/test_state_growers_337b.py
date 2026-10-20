"""Retention exercises real readers, repeated runs, cutoffs and protected anchors."""
import json
from datetime import timedelta

import pytest

from dealradar import accuracy, clicks, state_growers, state_retention, util
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from tests.test_state_growers import NOW, prune
from tests.test_dedupe import make_post
from tests.test_product_entities import insert


@pytest.fixture
def db(tmp_path):
    result = DB(tmp_path / 'state.db')
    yield result
    result.close()


def config(**kw):
    return AppConfig(state_retention_337=True, state_maintenance_budget_s=5, **kw)


@pytest.mark.parametrize('field', ['audit_detail_days', 'audit_hot_days', 'click_raw_days', 'accuracy_detail_days', 'accuracy_last_per_deal'])
@pytest.mark.parametrize('value', [None, 0, True, '14', -1, 1.1, 3651])
def test_strict_knobs(tmp_path, field, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'retention': {field: value}}))
    with pytest.raises(ValueError, match=field):
        load_config(path)


def test_cold_receipts_live_deals_rollback_and_existing_window(db):
    old = NOW - timedelta(days=8)
    free, live = make_post(uid='free'), make_post(uid='live')
    for post in (free, live):
        db.record_post(post, old)
    deal = insert(db, 'live', now=old)
    db.add_sighting(deal['id'], source_name=live.source_name, source_url=live.url,
                    content_hash=live.content_hash, seen_at=old, final_price=10)
    prune(db, config())
    assert db.post_is_known(free.content_hash) and not db.record_post(free, NOW)
    cold = db.conn.execute('SELECT * FROM posts_seen_cold').fetchone()
    assert json.loads(cold['details'])[:2] == [free.uid, free.url]
    assert db.conn.execute('SELECT content_hash FROM posts_seen').fetchone()[0] == live.content_hash
    assert db.post_first_seen(free.content_hash) == old
    newer = make_post(uid='newer')
    db.record_post(newer, NOW)
    assert db.conn.execute(f'SELECT id FROM {state_growers.posts_relation(db)} WHERE content_hash=?', (newer.content_hash,)).fetchone()[0] > cold['id']
    prune(db, AppConfig(), run='off')
    assert db.post_is_known(free.content_hash)
    prune(db, config(), now=old + timedelta(days=31), run='expiry')
    assert not db.post_is_known(free.content_hash)
    assert db.get_deal('live') is not None


@pytest.mark.parametrize('compact', [False, True])
def test_accuracy_last_k_preserves_scoreboard_and_replay(db, compact):
    cfg = config(compact_accuracy_samples=compact, accuracy_last_per_deal=3)
    db._accuracy_published = {'deal': dict(at=util.iso(NOW-timedelta(days=10)), created_run='seed', final=10, store='Store', checkout=False, bucket='rest')}
    for n in range(10):
        at = NOW - timedelta(days=1, hours=n)
        accuracy.record(db, cfg, 'deal', at, source='keepa', price=10+n%2, run_key=str(n))
    before = accuracy.report_data(db, cfg, NOW)
    prune(db, cfg)
    assert accuracy.report_data(db, cfg, NOW) == before
    table = 'accuracy_samples_compact' if compact else 'accuracy_samples'
    assert db.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 3
    at = NOW - timedelta(days=1, hours=9)
    accuracy.record(db, cfg, 'deal', at, source='keepa', price=11, run_key='9')
    assert accuracy.report_data(db, cfg, NOW) == before
    # Even a newer reading cannot reopen a retired run after its detail went.
    accuracy.record(db, cfg, 'deal', NOW, source='keepa', price=10, run_key='9')
    assert accuracy.report_data(db, cfg, NOW) == before
    assert db.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 3
    prune(db, cfg, run='again')
    assert accuracy.report_data(db, cfg, NOW) == before
    assert db.conn.execute("SELECT SUM(n) FROM state_audit_counts WHERE kind='accuracy'").fetchone()[0] == 7


def test_click_rollup_partial_days_report_and_replay(db):
    from zoneinfo import ZoneInfo
    from dealradar.growth.ads_guard import _click_counts
    cfg = config()
    cfg.raw['first_party_clicks'] = {'enabled': True}
    hour = int((NOW-timedelta(days=32)).timestamp()//3600)
    with db.conn:
        db.conn.executemany('INSERT INTO deal_clicks VALUES (?,?,?)', [('a', hour, 3), ('a', hour+1, 5), ('b', hour+1, 7)])
        db.conn.executemany('INSERT INTO deal_click_coverage VALUES (?,?)', [(hour, 0), (hour+1, 1)])
        db.conn.execute('INSERT INTO deal_click_runs VALUES (?,?,1,1)', ('old-run', hour))
    first, last = hour-2, hour+4
    before_rows = sorted(map(tuple, state_growers.click_rows(db, first, last)))
    before_coverage = state_growers.click_coverage(db, first, last)
    day = (NOW-timedelta(days=32)).date()
    before = _click_counts(db, cfg, day, day, ZoneInfo('America/New_York'))
    prune(db, cfg)
    assert sorted(state_growers.click_rows(db, first, last)) == before_rows
    assert state_growers.click_coverage(db, first, last) == before_coverage
    assert _click_counts(db, cfg, day, day, ZoneInfo('America/New_York')) == before
    assert list(state_growers.click_rows(db, hour+1, hour+2)) == [('a', hour+1, 5), ('b', hour+1, 7)]
    assert db.conn.execute('SELECT COUNT(*) FROM deal_clicks').fetchone()[0] == 0
    prune(db, cfg, run='again')
    assert sorted(state_growers.click_rows(db, first, last)) == before_rows
    assert db.conn.execute('SELECT run_key FROM deal_click_runs').fetchone()[0] == 'old-run'


def test_audits_pending_and_run_keys_survive_counts_only_once(db):
    old = util.iso(NOW-timedelta(days=20))
    with db.conn:
        db.conn.execute("INSERT INTO legitimacy_audits VALUES ('audit',?,'verified','reason','shadow',1,?)", (old, old))
        db.conn.execute("INSERT INTO legitimacy_batches VALUES ('batch',?,'shadow','model',3,2,.01)", (old,))
        db.conn.execute("INSERT INTO second_opinion_yield VALUES ('opinion',?,'source',4,3)", (old,))
        db.conn.execute("INSERT INTO efficiency_runs VALUES ('efficiency',?,?,0,?)", (old, old, json.dumps({'feed_tokens': 10})))
    db.start_run('ancient', 'hourly', NOW-timedelta(days=90))
    db.conn.execute("UPDATE run_stats SET finished_at=?,items_fetched=9 WHERE run_key='ancient'", (old,))
    db.conn.commit()
    prune(db, config())
    first = list(map(tuple, db.conn.execute('SELECT * FROM state_audit_counts ORDER BY kind,stamp,dimensions')))
    assert sum(r[3] for r in first if r[0]=='second_opinion_yield') == 8  # 4 extracted + 3 evidence + 1 successful post
    assert db.conn.execute('SELECT event_key,reason FROM legitimacy_audits').fetchone()[:] == ('audit','')
    assert db.conn.execute('SELECT batch_key,model FROM legitimacy_batches').fetchone()[:] == ('batch','')
    assert db.conn.execute("SELECT metrics FROM efficiency_runs WHERE run_key='efficiency'").fetchone()[0] is None
    assert db.conn.execute("SELECT state_maintenance FROM run_stats WHERE run_key='ancient'").fetchone()[0] == '{"retired":true}'
    prune(db, config(), run='again')
    assert list(map(tuple, db.conn.execute('SELECT * FROM state_audit_counts ORDER BY kind,stamp,dimensions'))) == first
    prune(db, config(), run='ancient')
    assert list(map(tuple, db.conn.execute('SELECT * FROM state_audit_counts ORDER BY kind,stamp,dimensions'))) == first


def test_old_audit_report_survives_rollup_and_recent_comparison_defers(db):
    from dealradar import legitimacy
    cfg = config()
    old = NOW - timedelta(days=20)
    observed = util.iso(old)
    recent = util.iso(NOW-timedelta(hours=1))
    with db.conn:
        db.conn.execute("INSERT INTO legitimacy_audits VALUES ('old',?,'verified','reason','shadow',1,?)", (observed, observed))
        db.conn.execute("INSERT INTO legitimacy_audits VALUES ('recent-comparison',?,'verified','reason','shadow',1,?)", (observed, recent))
        db.conn.execute("INSERT INTO legitimacy_batches VALUES ('batch',?,'shadow','model',3,2,.01)", (observed,))
    report_at = old + timedelta(days=1)
    before = legitimacy.daily_stats(db, cfg, report_at)
    prune(db, cfg)
    assert legitimacy.daily_stats(db, cfg, report_at) == before
    assert db.conn.execute("SELECT reason,compared_at FROM legitimacy_audits WHERE event_key='recent-comparison'").fetchone()[:] == ('reason',recent)


@pytest.mark.parametrize('table,insert_sql,params', [
    ('deal_clicks', 'INSERT INTO deal_clicks VALUES (?,?,?)', ('click',1,3)),
    ('source_audit_posts', 'INSERT INTO source_audit_posts(source_name,content_hash,first_seen_at,parsed_at) VALUES (?,?,?,?)', ('source','hash',util.iso(NOW-timedelta(days=20)),util.iso(NOW-timedelta(days=20)))),
    ('accuracy_samples', 'INSERT INTO accuracy_samples VALUES (?,?,?,?,?,?,?,?)', ('deal','old',util.iso(NOW-timedelta(days=20)),util.iso(NOW-timedelta(days=20)),'rest','Store','keepa','match')),
])
def test_aggregation_and_delete_roll_back_together_on_deadline(db,monkeypatch,table,insert_sql,params):
    from tests.test_incremental_cache import ConnectionHook
    db.conn.execute(insert_sql,params)
    db.conn.commit()
    db.meta_set('state_maintenance_next_table',table)
    clock=[0.]
    monkeypatch.setattr(state_retention.time,'monotonic',lambda:clock[0])
    real=db.conn
    def hook(when,sql):
        if when=='after' and sql.startswith('DELETE FROM '+table+' WHERE rowid'):
            clock[0]=10.
    db.conn=ConnectionHook(real,hook)
    try:
        assert prune(db,config(),deadline=1.)=={}
    finally:
        db.conn=real
    assert db.conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==1
    assert db.conn.execute('SELECT COUNT(*) FROM state_audit_counts').fetchone()[0]==0
    assert db.conn.execute('SELECT COUNT(*) FROM click_daily_counts').fetchone()[0]==0
    clock[0]=0.
    assert prune(db,config(),deadline=1.)[table]['deleted']==1


def test_all_old_run_guards_survive(db):
    old=NOW-timedelta(days=100)
    db.conn.execute('INSERT INTO accuracy_extra_runs VALUES (?,?)',('accuracy',util.iso(old)))
    db.conn.execute('INSERT INTO critic_tier_batches VALUES (?,?)',('critic',util.iso(old)[:10]))
    db.conn.execute("INSERT INTO ai_yield_runs VALUES (?,?,?,'policy','mode',0)",('yield',util.iso(old),util.iso(old)))
    db.meta_set('ai_take_run:old',util.iso(old))
    prune(db,config())
    assert db.meta_get('ai_take_run:old')==util.iso(old)
    for table in ['accuracy_extra_runs','critic_tier_batches','ai_yield_runs']:
        assert db.conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==1


def test_sighting_and_alert_inserts_do_not_change_post_ids_after_compaction(db):
    old = NOW-timedelta(days=8)
    cold, hot = make_post(uid='cold'), make_post(uid='hot')
    db.record_post(cold, old)
    db.record_post(hot, NOW)
    prune(db, config())
    before = list(map(tuple,db.conn.execute(f'SELECT * FROM {state_growers.posts_relation(db)} ORDER BY id')))
    deal=insert(db,'deal')
    assert db.add_sighting(deal['id'],source_name='source',source_url='https://source.example/post',content_hash='sighting',seen_at=NOW,final_price=10)
    assert db.record_alert(deal_key='deal',channel='test',dedup_token='token',now=NOW)
    assert list(map(tuple,db.conn.execute(f'SELECT * FROM {state_growers.posts_relation(db)} ORDER BY id')))==before


def test_click_archival_is_bounded_and_same_run_is_exact_replay(db):
    with db.conn:
        db.conn.executemany('INSERT INTO deal_clicks VALUES (?,?,?)', [('a', n, n+1) for n in range(5)])
    cfg=config(state_maintenance_max_rows=2)
    first=prune(db,cfg)
    assert first['deal_clicks']['examined']==first['deal_clicks']['deleted']==2
    before=list(db.conn.iterdump())
    assert prune(db,cfg)==first
    assert list(db.conn.iterdump())==before
    prune(db,cfg,run='next')
    prune(db,cfg,run='last')
    assert db.conn.execute('SELECT SUM(count) FROM click_daily_counts').fetchone()[0]==15
    assert db.conn.execute('SELECT COUNT(*) FROM deal_clicks').fetchone()[0]==0


def test_cold_reader_reopen_and_intake_do_not_recreate_aggregated_audits(db):
    post=make_post(uid='archived')
    old=NOW-timedelta(days=20)
    db.audit_seen([post],old)
    db.record_post(post,old)
    db.audit_parsed(post,dict(extracted=1,evidence=1),old)
    prune(db,config())
    assert not db.conn.execute('SELECT 1 FROM source_audit_posts').fetchone()
    db.source_audit_posts_days=None  # rollback/config omission after a migrated database
    db.audit_seen([post],NOW)
    assert not db.conn.execute('SELECT 1 FROM source_audit_posts').fetchone()
    reader=DB(db.path,read_only=True)
    try:
        assert reader.post_is_known(post.content_hash)
        assert reader.post_first_seen(post.content_hash)==old
        row=reader.conn.execute(f'SELECT content_hash FROM {state_growers.posts_relation(reader)}').fetchone()
        assert row[0]==post.content_hash
    finally:
        reader.close()


def test_source_audit_detail_is_lossless_and_handles_composite_cursor(db):
    from types import SimpleNamespace
    from dealradar import source_audit
    old=NOW-timedelta(days=2)
    key='a'*64
    names=['alpha','beta:中文']
    with db.conn:
        for name in names:
            db.conn.execute('INSERT INTO source_audit_posts VALUES (?,?,?,?,?,?,?,?,?,?,?)',(name,key,util.iso(old),util.iso(old),4,3,1,2,1,1,0))
        db.conn.execute('INSERT INTO source_audit_posts(source_name,content_hash,first_seen_at) VALUES (?,?,?)',('pending','b'*64,util.iso(NOW-timedelta(days=20))))
    before={name:source_audit._history(db,SimpleNamespace(name=name),NOW,7,NOW-timedelta(days=30)) for name in names}
    cfg=config(state_maintenance_max_rows=1)
    for n in range(4):
        prune(db,cfg,run=str(n))
    assert db.conn.execute('SELECT COUNT(*) FROM source_audit_posts_cold').fetchone()[0]==2
    assert db.conn.execute('SELECT source_name FROM source_audit_posts').fetchone()[0]=='pending'
    assert {name:source_audit._history(db,SimpleNamespace(name=name),NOW,7,NOW-timedelta(days=30)) for name in names}==before
    db.audit_seen([SimpleNamespace(source_name='alpha',content_hash=key)],NOW)
    assert db.conn.execute('SELECT COUNT(*) FROM source_audit_posts').fetchone()[0]==1
    for n in range(4,8):
        prune(db,cfg,run=str(n),now=NOW+timedelta(days=13))
    assert db.conn.execute('SELECT COUNT(*) FROM source_audit_posts_cold').fetchone()[0]==0
    assert db.conn.execute("SELECT SUM(n) FROM state_audit_counts WHERE kind='source_audit_posts' AND dimensions LIKE '%extracted%'").fetchone()[0]==8


def test_fresh_flagged_posts_stay_hot_until_compaction_without_losing_any_fields(db):
    db.state_retention_337=True
    post=make_post(uid='fresh')
    assert db.record_post(post,NOW)
    assert not db.record_post(post,NOW)
    row=dict(db.conn.execute('SELECT * FROM posts_seen').fetchone())
    assert row==dict(id=1,source_name=post.source_name,uid=post.uid,url=post.url,
                    content_hash=post.content_hash,published_at=util.iso(post.published_at),
                    first_seen_at=util.iso(NOW))
    assert db.post_is_known(post.content_hash) and db.post_first_seen(post.content_hash)==NOW
    prune(db,config())
    assert dict(db.conn.execute('SELECT * FROM posts_seen').fetchone())==row
    assert not db.conn.execute('SELECT 1 FROM posts_seen_cold').fetchone()
    prune(db,config(),run='older',now=NOW+timedelta(days=8))
    cold=db.conn.execute('SELECT * FROM posts_seen_cold').fetchone()
    assert cold['id']==row['id'] and cold['source_name']==post.source_name
    assert json.loads(cold['details'])==[post.uid,post.url,util.iso(post.published_at)]
    assert not db.conn.execute('SELECT 1 FROM posts_seen').fetchone()
    reader=DB(db.path,read_only=True)
    try:
        assert reader.post_is_known(post.content_hash) and reader.post_first_seen(post.content_hash)==NOW
    finally:
        reader.close()
    db.state_retention_337=False
    before=list(db.conn.iterdump())
    assert not db.record_post(post,NOW+timedelta(days=8))
    assert list(db.conn.iterdump())==before
