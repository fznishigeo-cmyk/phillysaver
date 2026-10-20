"""Adversarial retention regressions: routing history, replay and vacuum time."""
from datetime import timedelta

import pytest

from dealradar import ai_usage, state_retention
from tests.test_dedupe import make_post
from tests.test_incremental_cache import ConnectionHook
from tests.test_lighter_state import add_call
from tests.test_state_growers import NOW, prune
from tests.test_state_growers_337b import config, db


@pytest.mark.parametrize('tiered', [False, True])
def test_retention_settings_do_not_invalidate_paid_critic_verdicts(db, monkeypatch, tiered):
    from dealradar import legitimacy, pipeline
    from dealradar.parse.claude_client import ClaudeParser
    from tests.test_legitimacy_audit import add
    from tests.test_quality_trio import _cfg, _FakeClient, _CriticMessages

    monkeypatch.setattr('dealradar.util.now_utc', lambda: NOW)
    cfg=_cfg(critic_shadow_model='',state_retention_337=False,
             keepa_recheck_top_n=0,keepa_recheck_wall_n=0)
    cfg.critic_tiering.update(enabled=tiered,shadow_batches_per_day=0)
    add(db)
    messages=_CriticMessages(True)
    parser=ClaudeParser(cfg,client=_FakeClient(messages))
    parser.bind_ledger(db,'before-retention')
    first=pipeline._run_critic(db,cfg,parser,NOW)
    assert first['judged']==first['calls']==len(messages.calls)==1
    fingerprint=legitimacy.fingerprint(db.get_deal('widget'),cfg)
    before=list(db.conn.iterdump())
    cfg.state_retention_337=True
    cfg.audit_detail_days=7
    cfg.audit_hot_days=2
    following=ClaudeParser(cfg,client=_FakeClient(messages))
    following.bind_ledger(db,'after-retention')
    assert legitimacy.fingerprint(db.get_deal('widget'),cfg)==fingerprint
    assert pipeline._run_critic(db,cfg,following,NOW)['calls']==0
    assert following.spend_usd==0 and len(messages.calls)==1
    assert list(db.conn.iterdump())==before  # no new audit, call or reservation
    cfg.audit_extreme_pct-=1
    assert legitimacy.fingerprint(db.get_deal('widget'),cfg)!=fingerprint


@pytest.mark.parametrize('audit_days,window,age', [(1, 7, 2), (14, 30, 20)])
def test_second_opinion_rollup_preserves_routing_history_and_event_guard(db, audit_days, window, age):
    post = make_post(source='partner')
    old = NOW - timedelta(days=age)
    cfg = config(audit_detail_days=audit_days)
    cfg.critic_tiering['second_opinion_window_days'] = window
    add_call(db, 'opinion', old, 'settled', billed=old)
    db.conn.execute("UPDATE llm_calls SET kind='second_opinion',source_name='partner'")
    db.conn.commit()
    ai_usage.record_second_opinion(db, post, 'fingerprint', old, 4, 3)
    before = ai_usage.second_opinion_sources(db.conn, NOW, window)
    assert before['partner']['yield'] == 1
    prune(db, cfg)
    assert ai_usage.second_opinion_sources(db.conn, NOW, window) == before
    # The post can be reread after its parse cache expires, even during rollback.
    db.state_retention_337 = False
    snapshot = list(db.conn.iterdump())
    ai_usage.record_second_opinion(db, post, 'fingerprint', NOW, 4, 3)
    assert list(db.conn.iterdump()) == snapshot
    prune(db, cfg, run='again')
    assert ai_usage.second_opinion_sources(db.conn, NOW, window) == before


@pytest.mark.parametrize('expires_during_vacuum', [False, True])
def test_flagged_vacuum_respects_hourly_deadline(db, monkeypatch, expires_during_vacuum):
    db.conn.execute('CREATE TABLE fragmentation(payload BLOB)')
    db.conn.executemany('INSERT INTO fragmentation VALUES (?)', [(bytes(3000),)] * 200)
    db.conn.execute('DELETE FROM fragmentation WHERE rowid<=150')
    db.conn.commit()
    clock = [0. if expires_during_vacuum else 2.]
    monkeypatch.setattr(state_retention.time, 'monotonic', lambda: clock[0])
    real, statements = db.conn, []

    def hook(when, sql):
        if when == 'before':
            statements.append(sql)
            if sql == 'VACUUM':
                clock[0] = 2.

    db.conn = ConnectionHook(real, hook)
    try:
        prune(db, config(), deadline=1.)
    finally:
        db.conn = real
    assert ('VACUUM' in statements) == expires_during_vacuum
    assert db.conn.execute('PRAGMA freelist_count').fetchone()[0] > 0
    assert db.conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    # An interrupted optimization must release its handler for later work.
    assert db.conn.execute('SELECT COUNT(*) FROM fragmentation').fetchone()[0] == 50
