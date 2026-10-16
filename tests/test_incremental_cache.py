"""Bounded validator retention, transactional replay and legacy output parity."""

import argparse
import json
import logging
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from dealradar import cli, db as db_module, pipeline, util
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.util import iso
from tests.test_amazon_as_of import NOW, config, sample_deals
from tests.test_e2e_offline import env, test_rerun_is_idempotent as check_rerun
from tests.test_lighter_state import add_call
from tests.test_product_offer_pages import tree

OLD = NOW - timedelta(days=90)


@pytest.fixture
def db(tmp_path):
    state = DB(tmp_path / 'cache.db')
    yield state
    state.close()


def seed(db, names, at=OLD):
    db.conn.executemany('INSERT INTO http_cache(url,fetched_at) VALUES (?,?)',
                        [(name, iso(at) if at is not None else None) for name in names])
    db.conn.commit()


def keys(db):
    return [r[0] for r in db.conn.execute('SELECT url FROM http_cache ORDER BY url')]


def sweep(db, key='run', limit=256, **kwargs):
    db.start_run(key, 'fast', NOW)
    return db.prune_http_cache(NOW, run_key=key, max_examined_per_run=limit, **kwargs)


def progress(db):
    return db.meta_get('http_cache_sweep')


def counters(db, key='run'):
    return tuple(db.conn.execute('SELECT cache_examined,cache_deleted,cache_completed'
                                 ' FROM run_stats WHERE run_key=?', (key,)).fetchone())


class ConnectionHook:
    """Inject failures at real SQLite statement/commit boundaries."""
    def __init__(self, real, hook):
        self.real, self.hook = real, hook

    def execute(self, sql, *args):
        self.hook('before', sql)
        result = self.real.execute(sql, *args)
        self.hook('after', sql)
        return result

    def executemany(self, sql, args):
        self.hook('before', sql)
        result = self.real.executemany(sql, args)
        self.hook('after', sql)
        return result

    def __enter__(self):
        self.real.__enter__()
        return self

    def __exit__(self, *args):
        result = self.real.__exit__(*args)
        if args[0] is None:
            self.hook('committed', '')
        return result

    def __getattr__(self, name):
        return getattr(self.real, name)


def test_config_defaults_off_and_custom(tmp_path):
    assert not AppConfig().incremental_cache_enabled
    assert not load_config().incremental_cache_enabled
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert not load_config(path).incremental_cache_enabled
    for count, seconds in ((1, .05), (4096, 5)):
        path.write_text(yaml.safe_dump({'retention': {'incremental_cache': {
            'enabled': True, 'max_examined_per_run': count, 'budget_s': seconds}}}))
        cfg = load_config(path)
        assert cfg.incremental_cache_enabled
        assert cfg.incremental_cache_max_examined_per_run == count
        assert cfg.incremental_cache_budget_s == seconds


@pytest.mark.parametrize('option,value', [
    ('enabled', 'false'), ('enabled', 1),
    *[('max_examined_per_run', v) for v in (0, -1, 4097, 2.5, True, '256', None, float('inf'))],
    *[('budget_s', v) for v in (0, -.1, .049, 5.01, True, '1', None,
                              10**400, float('inf'), float('nan'))],
])
def test_invalid_config_is_rejected(tmp_path, option, value):
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump({'retention': {'incremental_cache': {option: value}}}))
    with pytest.raises(ValueError, match='retention.incremental_cache'):
        load_config(path)


@pytest.mark.parametrize('at', [OLD, NOW, None])
def test_every_row_is_eventually_examined_with_bounded_work(db, at):
    seed(db, [f'{i:05d}' for i in range(1031)], at)
    for i in range(5):
        result = sweep(db, str(i))
        assert result['examined'] == (256 if i < 4 else 7)
        assert result['deleted'] == (result['examined'] if at == OLD else 0)
        assert result['completed'] == (i == 4)
        assert result['deferred'] == (i != 4)
    assert len(keys(db)) == (0 if at == OLD else 1031)
    assert json.loads(progress(db)) == {'after': None, 'high_water': None}


def test_many_fresh_rows_before_expired_rows_do_not_exhaust_unbounded_scan(db):
    seed(db, [f'a{i:04d}' for i in range(1024)], NOW)
    seed(db, [f'z{i:04d}' for i in range(100)])
    for i in range(4):
        result = sweep(db, str(i))
        assert result['examined'] == 256 and result['deleted'] == 0
        assert result['deferred']
    result = sweep(db, 'tail')
    assert result['examined'] == result['deleted'] == 100
    assert result['completed'] and len(keys(db)) == 1024


def test_expiry_boundary_and_null_match_legacy(db):
    seed(db, ['old'], NOW - timedelta(days=30, seconds=1))
    seed(db, ['boundary'], NOW - timedelta(days=30))
    seed(db, ['null'], None)
    seed(db, ['fresh'], NOW)
    result = sweep(db)
    assert keys(db) == ['boundary', 'fresh', 'null']
    assert (result['examined'], result['deleted'], result['completed']) == (4, 1, True)


def test_empty_completion_and_same_key_do_not_restart_sweep(db):
    assert sweep(db)['completed']
    seed(db, ['new'])
    assert sweep(db)['examined'] == 0
    assert keys(db) == ['new']
    assert sweep(db, 'next')['deleted'] == 1


def test_insertions_deleted_cursor_high_water_and_multiple_wraps(db):
    seed(db, ['b', 'd', 'f'])
    assert sweep(db, 'one', limit=1)['deleted'] == 1  # cursor b no longer exists
    seed(db, ['a', 'c', 'z'])
    # a is behind the cursor; z is beyond the frozen high-water f.
    assert sweep(db, 'two', limit=2)['examined'] == 2
    assert keys(db) == ['a', 'f', 'z']
    result = sweep(db, 'three', limit=8)
    assert result['completed'] and result['examined'] == 1
    assert keys(db) == ['a', 'z']
    assert sweep(db, 'three', limit=8)['examined'] == 1  # never wrap on same key
    assert keys(db) == ['a', 'z']
    assert sweep(db, 'four')['deleted'] == 2
    seed(db, ['second-wrap'])
    assert sweep(db, 'five')['deleted'] == 1
    assert sweep(db, 'six')['examined'] == 0


def test_deleted_high_water_does_not_stall_completion(db):
    seed(db, ['a', 'b', 'c'])
    assert not sweep(db, limit=1)['completed']
    db.conn.execute("DELETE FROM http_cache WHERE url='c'")
    db.conn.commit()
    assert sweep(db, 'next', limit=8)['completed']


def test_refreshed_validator_survives_delete_time_recheck(db):
    seed(db, ['a', 'b'])
    real = db.conn

    def hook(phase, sql):
        if phase == 'before' and sql.startswith('DELETE FROM http_cache'):
            real.execute("UPDATE http_cache SET fetched_at=?,etag='refreshed' WHERE url='a'", (iso(NOW),))
    db.conn = ConnectionHook(real, hook)
    try:
        result = sweep(db)
    finally:
        db.conn = real
    assert (result['examined'], result['deleted']) == (2, 1)
    assert db.get_validators('a') == ('refreshed', '')
    assert keys(db) == ['a']


def test_run_allowance_survives_reopen_and_does_not_reset_when_run_restarts(db):
    seed(db, [f'{i:04d}' for i in range(200)])
    assert sweep(db, limit=70)['examined'] == 70
    before = progress(db)
    with sqlite3.connect(db.path) as reopened:
        assert reopened.execute("SELECT cache_examined FROM run_stats WHERE run_key='run'").fetchone()[0] == 70
    for _ in range(3):
        assert sweep(db, limit=70)['examined'] == 70
        assert progress(db) == before
    assert len(keys(db)) == 130
    assert sweep(db, 'next', limit=70)['examined'] == 70


@pytest.mark.parametrize('point', ['before-transaction', 'during-delete', 'during-cursor',
                                   'during-allowance', 'after-commit', 'before-remote-save'])
def test_interruption_reopen_and_same_key_replay(db, point):
    seed(db, [f'{i:04d}' for i in range(150)])
    db.start_run('run', 'fast', NOW)
    real = db.conn
    commits = 0

    def hook(phase, sql):
        nonlocal commits
        if phase == 'committed':
            commits += 1
        # During later chunks the first completed chunk must remain durable.
        if (point == 'before-transaction' and phase == 'before' and sql == 'BEGIN IMMEDIATE'
                or point == 'after-commit' and phase == 'committed'
                or commits == 1 and phase == 'after' and (
                    point == 'during-delete' and sql.startswith('DELETE FROM http_cache')
                    or point == 'during-cursor' and sql.startswith('INSERT INTO meta')
                    or point == 'during-allowance' and sql.startswith('UPDATE run_stats SET cache_examined'))):
            raise InterruptedError('fixture interruption')
    db.conn = ConnectionHook(real, hook)
    try:
        if point == 'before-remote-save':
            sweep(db, limit=100)
        else:
            with pytest.raises(InterruptedError):
                sweep(db, limit=100)
    finally:
        db.conn = real
    examined = 0 if point == 'before-transaction' else 100 if point == 'before-remote-save' else 64
    assert counters(db) == (examined, examined, 0)
    assert len(keys(db)) == 150 - examined
    db.close()
    db.conn = DB(db.path).conn
    assert sweep(db, limit=100)['examined'] == 100
    assert counters(db) == (100, 100, 0)
    assert len(keys(db)) == 50
    assert sweep(db, limit=100)['examined'] == 100
    assert sweep(db, 'next', limit=100)['completed']
    assert not keys(db)


def test_restored_older_snapshot_may_safely_repeat_cleanup(db, tmp_path):
    seed(db, [f'{i:04d}' for i in range(150)])
    db.start_run('run', 'fast', NOW)
    snapshot = tmp_path / 'snapshot.db'
    with sqlite3.connect(snapshot) as copy:
        db.conn.backup(copy)
    assert sweep(db, limit=100)['deleted'] == 100
    restored = DB(snapshot)
    try:
        assert sweep(restored, limit=100)['deleted'] == 100
        assert keys(restored) == keys(db)
        assert counters(restored) == counters(db)
    finally:
        restored.close()


@pytest.mark.parametrize('statement', ['DELETE FROM http_cache', 'INSERT INTO meta',
                                      'UPDATE run_stats SET cache_examined'])
def test_failed_final_chunk_never_marks_sweep_complete(db, statement):
    seed(db, [f'{i:04d}' for i in range(70)])
    assert not sweep(db, limit=64)['completed']
    before = progress(db)
    real = db.conn

    def hook(phase, sql):
        if phase == 'after' and sql.startswith(statement):
            raise InterruptedError('fixture final-chunk interruption')
    db.conn = ConnectionHook(real, hook)
    try:
        with pytest.raises(InterruptedError):
            sweep(db, limit=100)
    finally:
        db.conn = real
    db.close()
    db.conn = DB(db.path).conn
    assert counters(db) == (64, 64, 0)
    assert progress(db) == before and len(keys(db)) == 6
    assert sweep(db, limit=100)['completed']
    assert counters(db) == (70, 70, 1)
    seed(db, ['next-sweep'])
    assert sweep(db, limit=100)['examined'] == 70
    assert keys(db) == ['next-sweep']
    assert sweep(db, 'next')['deleted'] == 1


@pytest.mark.parametrize('point', ['before', 'after-select', 'after-delete', 'after-cursor', 'after-allowance', 'after-commit'])
def test_deadline_rolls_back_incomplete_chunk_and_keeps_committed_chunks(db, monkeypatch, point):
    seed(db, [f'{i:04d}' for i in range(150)])
    clock = [0.0]
    monkeypatch.setattr(db_module.time, 'monotonic', lambda: clock[0])
    real = db.conn

    def hook(phase, sql):
        if (point == 'after-select' and phase == 'after' and sql.startswith('SELECT url FROM http_cache WHERE')
                or point == 'after-delete' and phase == 'after' and sql.startswith('DELETE FROM http_cache')
                or point == 'after-cursor' and phase == 'after' and sql.startswith('INSERT INTO meta')
                or point == 'after-allowance' and phase == 'after' and sql.startswith('UPDATE run_stats SET cache_examined')
                or point == 'after-commit' and phase == 'committed'):
            clock[0] = 1.0
    db.start_run('run', 'fast', NOW)
    statements = []
    real.set_trace_callback(statements.append)
    db.conn = ConnectionHook(real, hook)
    try:
        result = db.prune_http_cache(NOW, run_key='run', max_examined_per_run=100,
                                    deadline=0.005 if point == 'before' else 1, budget_s=1)
    finally:
        db.conn = real
        real.set_trace_callback(None)
    if point == 'before':
        assert 'BEGIN IMMEDIATE' not in statements
    expected = 64 if point == 'after-commit' else 0
    assert result['examined'] == result['deleted'] == expected
    assert result['deferred'] and not result['completed']
    assert len(keys(db)) == 150 - expected
    assert progress(db) is None if not expected else json.loads(progress(db))['after'] == '0063'
    clock[0] = 0
    assert sweep(db, limit=100)['examined'] == 100


def test_budget_can_resume_same_key_without_extra_allowance(db, monkeypatch):
    seed(db, [f'{i:04d}' for i in range(150)])
    clock = [0.0]
    monkeypatch.setattr(db_module.time, 'monotonic', lambda: clock[0])
    real = db.conn

    def hook(phase, sql):
        if phase == 'committed':
            clock[0] += .15
    db.start_run('run', 'fast', NOW)
    db.conn = ConnectionHook(real, hook)
    try:
        first = sweep(db, limit=100, budget_s=.1)
        second = sweep(db, limit=100, budget_s=.1)
        third = sweep(db, limit=100, budget_s=.1)
    finally:
        db.conn = real
    assert [r['examined'] for r in (first, second, third)] == [64, 100, 100]
    assert all(r['deferred'] for r in (first, second, third))


def test_keyset_plans_use_existing_primary_key_without_sort_or_expiry_index(db):
    seed(db, [f'{i:05d}' for i in range(5000)], NOW)
    for sql, args in (
        ('SELECT url FROM http_cache ORDER BY url DESC LIMIT 1', ()),
        ('SELECT url FROM http_cache WHERE url <= ? ORDER BY url LIMIT ?', ('09999', 64)),
        ('SELECT url FROM http_cache WHERE url > ? AND url <= ? ORDER BY url LIMIT ?', ('01000', '09999', 64)),
        ('DELETE FROM http_cache WHERE url=? AND fetched_at < ?', ('01000', iso(NOW))),
    ):
        plan = ' '.join(row[3] for row in db.conn.execute('EXPLAIN QUERY PLAN ' + sql, args))
        assert 'sqlite_autoindex_http_cache_1' in plan
        assert 'TEMP B-TREE' not in plan
        if 'WHERE' in sql:
            assert 'SEARCH' in plan
    assert len(db.conn.execute('PRAGMA index_list(http_cache)').fetchall()) == 1
    # VM work must be bounded for fresh as well as expired rows, not just the Python list.
    steps = [0]
    def count_steps():
        steps[0] += 100
        return 0
    db.conn.set_progress_handler(count_steps, 100)
    try:
        result = sweep(db)
    finally:
        db.conn.set_progress_handler(None, 0)
    assert result['examined'] == 256 and result['deleted'] == 0
    assert steps[0] < 20000


@pytest.mark.parametrize('enabled,fast', [(False, True), (False, False), (True, True), (True, False)])
def test_fast_only_compaction_policy_and_private_aggregate_summary(db, enabled, fast, caplog):
    db.conn.execute('CREATE TABLE fragmentation(id INTEGER PRIMARY KEY, payload BLOB)')
    db.conn.executemany('INSERT INTO fragmentation(payload) VALUES (?)', [(bytes(3000),)] * 200)
    db.conn.execute('DELETE FROM fragmentation WHERE id <= 160')
    db.conn.commit()
    seed(db, ['https://private.example/never-log-this'])
    db.start_run('run', 'fast' if fast else 'hourly', NOW)
    statements = []
    db.conn.set_trace_callback(statements.append)
    with caplog.at_level(logging.INFO, logger='dealradar.db'):
        result = db.prune(NOW, incremental_cache_enabled=enabled, fast_lane=fast, run_key='run')
    assert ('VACUUM' in statements) == (not enabled or not fast)
    assert 'PRAGMA auto_vacuum' not in ' '.join(statements)
    if enabled:
        assert result.keys() == {'examined', 'deleted', 'deferred', 'completed', 'elapsed_s', 'compaction_deferred'}
        assert result['compaction_deferred'] == fast
        assert result['examined'] == result['deleted'] == 1
        assert 'never-log-this' not in caplog.text
    else:
        assert result is None and not progress(db)
        assert 'DELETE FROM http_cache WHERE fetched_at' in ' '.join(statements)
    assert db.conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


@pytest.mark.parametrize('enabled', [False, True])
def test_other_retention_guards_and_watermarks_unchanged(db, enabled):
    from dealradar.dedupe import upsert_extracted
    from tests.test_dedupe import make_cfg, make_extracted, make_post, make_savings
    live = make_extracted(product_url='https://www.walmart.com/ip/12345')
    upsert_extracted(db, live, make_savings(), make_post(), make_cfg(), OLD)
    db.record_price('widget', 'Store', 12.5, OLD, 'old-price')
    db.record_alert(deal_key='widget', channel='test', dedup_token='sent-alert', now=OLD)
    for key, status, at in [('pending', 'reserved', OLD), ('uncertain', 'uncertain', NOW),
                             ('current', 'settled', NOW), ('expired', 'settled', OLD)]:
        add_call(db, key, at, status, billed=at if status != 'reserved' else None,
                 days=(NOW, NOW + timedelta(days=1)) if status in ('reserved', 'uncertain') else ())
    for key in ('sent', 'pending', 'uncertain'):
        assert db.claim_outgoing('test', key, key, OLD)
        if key == 'sent':
            db.finish_outgoing('test', key, OLD)
    db.conn.execute("INSERT INTO indexnow_pages(url,content_hash,pending_since) VALUES ('https://example.com/p','hash',?)", (iso(OLD),))
    db.conn.commit()
    db.meta_set('efficiency_since', iso(OLD))
    db.meta_set('ai_yield_since', iso(OLD))
    before = {t: [tuple(r) for r in db.conn.execute(f'SELECT * FROM {t}')]
              for t in ('deals', 'products', 'product_keys', 'product_offers', 'price_history',
                        'alerts_sent', 'outgoing', 'indexnow_pages')}
    spend = db.spend_usd_for_day(NOW)
    seed(db, ['a', 'b'])
    db.start_run('run', 'fast', NOW)
    db.prune(NOW, incremental_cache_enabled=enabled, run_key='run', fast_lane=True,
             cache_max_examined_per_run=1)
    assert {t: [tuple(r) for r in db.conn.execute(f'SELECT * FROM {t}')] for t in before} == before
    assert {r[0] for r in db.conn.execute('SELECT call_key FROM llm_calls')} == {'pending', 'uncertain', 'current'}
    assert db.spend_usd_for_day(NOW) == spend
    assert db.meta_get('efficiency_retained_since') == iso(NOW - timedelta(days=60))
    assert db.meta_get('ai_yield_retained_since') == iso(NOW - timedelta(days=60))
    assert not db.conn.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.parametrize('audit_mode', ['shadow', 'enforce'])
def test_pipeline_enabled_offline_rerun_and_tail_deadline(env, capsys, monkeypatch, audit_mode):
    path = Path(env['cfg'])
    path.write_text(path.read_text().replace('incremental_cache:\n    enabled: false',
                                             'incremental_cache:\n    enabled: true'))
    original = DB.prune
    deadlines = []
    def prune(self, now, **kwargs):
        assert kwargs['incremental_cache_enabled']
        assert kwargs['deadline'] - pipeline.time.monotonic() <= (
            load_config(env['cfg']).run_budget_s - pipeline._RESERVE_FOR_TAIL_S)
        deadlines.append(kwargs['deadline'])
        return original(self, now, **kwargs)
    monkeypatch.setattr(DB, 'prune', prune)
    check_rerun(env, capsys, audit_mode)
    assert len(deadlines) == 2
    with sqlite3.connect(env['db']) as check:
        examined = check.execute('SELECT cache_examined FROM run_stats').fetchone()[0]
        assert 0 < examined <= 256


def test_cli_enabled_respects_bounded_cache_config(db, tmp_path, monkeypatch):
    seed(db, ['a', 'b', 'c'])
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump({'retention': {'incremental_cache': {
        'enabled': True, 'max_examined_per_run': 1}}}))
    monkeypatch.setattr(util, 'now_utc', lambda: NOW)
    assert cli.cmd_prune(argparse.Namespace(db=db.path, config=str(path))) == 0
    assert keys(db) == ['b', 'c']
    assert cli.cmd_prune(argparse.Namespace(db=db.path, config=str(path))) == 0
    assert keys(db) == ['b', 'c']


@pytest.mark.parametrize('mode,expected', [
    ('membership', '4981ab35e54901a8a2d2830cd61a9065fca8eeceb14c760a93d183ce6fa0ac40'),
    ('open', 'c95a3b25fa277f80ab70c10c08f8722948b6098b43352ffa1e6f6ce4fadcb5cb'),
    ('support', 'd671eb41721a3ffc331085123ffd33fa039e7dec853ed48cbb150a1a1347b545'),
    ('shipped', '79abffa3b4db3df20fec231d20e2e2d4ce91517fb0e4d0ba95061e0096cf05a3'),
])
def test_disabled_omitted_and_enabled_preserve_every_public_byte_and_member_payload(tmp_path, monkeypatch, mode, expected):
    import hashlib
    from dealradar.publish import site

    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    baseline_payload = None
    # Captured before any task-222 changes on this checkout. Retain 119/121.
    for setting in ('omitted', False, True):
        cfg = config(mode)
        cfg.photoless_last = True
        cfg.amazon_history = 'hide'
        cfg.amazon_as_of_enabled = True
        cfg.incremental_cache_enabled = setting is True
        with closing(DB(':memory:')) as db:
            seed(db, [f'https://private.example/{setting}'])
            db.start_run(str(setting), 'fast', NOW)
            kwargs = {} if setting == 'omitted' else {'incremental_cache_enabled': setting}
            db.prune(NOW, run_key=str(setting), fast_lane=True, **kwargs)
            out = tmp_path / str(setting)
            result = site.render_site(db, cfg, NOW, out_dir=out, deals=sample_deals(),
                                      last_run={'run_key': 'cache-baseline'})
            files = {name: hashlib.sha256(data).hexdigest() for name, data in tree(out).items()}
        assert hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest() == expected
        if baseline_payload is None:
            baseline_payload = result['full_payload']
        assert result['full_payload'] == baseline_payload
        assert 'cache_maintenance' not in (out / 'deals.json').read_text()


@pytest.mark.parametrize('fast_lane', [False, True])
@pytest.mark.parametrize('tail_left', [0, 30])
def test_pipeline_starts_no_cache_slice_inside_tail_reserve(env, monkeypatch, fast_lane, tail_left):
    import httpx
    from tests import test_e2e_offline as offline

    path = Path(env['cfg'])
    path.write_text(path.read_text().replace('incremental_cache:\n    enabled: false',
                                             'incremental_cache:\n    enabled: true'))
    clock = [0.0]
    monkeypatch.setattr(pipeline.time, 'monotonic', lambda: clock[0])
    def fetch(*args, **kwargs):
        clock[0] = load_config(env['cfg']).run_budget_s - tail_left
        return [], {}
    monkeypatch.setattr(pipeline, '_fetch_all', fetch)
    with closing(DB(env['db'])) as database:
        seed(database, ['https://private.example/still-pending'])
    result = pipeline.run(db_path=env['db'], config_path=env['cfg'], fast_lane=fast_lane,
                          now=offline.NOW, sources_override=[], skip_validation=True,
                          transport=httpx.MockTransport(offline._handler),
                          claude_client=offline._FakeClaudeClient(), dry_run_telegram=True)
    assert result['cache_maintenance']['examined'] == 0
    assert result['cache_maintenance']['deferred']
    assert result['cache_maintenance']['compaction_deferred'] == fast_lane
    with closing(DB(env['db'])) as database:
        assert keys(database) == ['https://private.example/still-pending']
        assert progress(database) is None
    assert 'cache_maintenance' not in (env['site'] / 'deals.json').read_text()


def test_existing_run_stats_migration_is_idempotent(tmp_path):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE run_stats(run_key TEXT PRIMARY KEY, kind TEXT NOT NULL,'
                     ' started_at TEXT NOT NULL, finished_at TEXT, items_fetched INTEGER DEFAULT 0,'
                     ' posts_new INTEGER DEFAULT 0, deals_parsed INTEGER DEFAULT 0,'
                     ' published INTEGER DEFAULT 0, spend_usd REAL DEFAULT 0)')
        conn.execute('INSERT INTO run_stats(run_key,kind,started_at) VALUES (?,?,?)',
                     ('legacy', 'fast', iso(NOW)))
    for _ in range(2):
        with closing(DB(path)) as database:
            assert counters(database, 'legacy') == (0, 0, 0)
            assert sweep(database, 'legacy')['completed']
            # Reset only the fixture completion for the next migration check.
            database.conn.execute('UPDATE run_stats SET cache_completed=0')
            database.conn.commit()
            assert database.conn.execute('SELECT COUNT(*) FROM run_stats').fetchone()[0] == 1


def fixture_profile(path, size, expired):
    """Reproducible local counts/timings; time values are observations, never gates."""
    import time

    with closing(DB(path)) as database:
        seed(database, [f'{i:08d}' for i in range(size - expired)], NOW)
        seed(database, [f'{i:08d}' for i in range(size - expired, size)])
        chunks, runs = [], []
        real = database.conn
        start = [0, 0]
        previous = [0, 0]
        def hook(phase, sql):
            if phase == 'before' and sql == 'BEGIN IMMEDIATE':
                start[:] = [time.perf_counter(), time.process_time()]
            if phase == 'committed':
                elapsed, cpu = time.perf_counter() - start[0], time.process_time() - start[1]
                count = counters(database, str(len(runs)))[:2]
                chunks.append({'examined': count[0] - previous[0], 'deleted': count[1] - previous[1],
                               'elapsed_s': elapsed, 'cpu_s': cpu})
                previous[:] = count
        for index in range(size + 1):
            database.start_run(str(index), 'fast', NOW)
            previous[:] = [0, 0]
            database.conn = ConnectionHook(real, hook)
            try:
                result = database.prune_http_cache(NOW, run_key=str(index))
            finally:
                database.conn = real
            runs.append(result)
            if result['completed']:
                break
        return {'runs': runs, 'chunks': chunks, 'remaining': len(keys(database))}


@pytest.mark.parametrize('size', [0, 1024, 8192])
@pytest.mark.parametrize('expired_share', [0, .5, 1])
def test_fixed_fixture_work_profile(tmp_path, size, expired_share):
    expired = int(size * expired_share)
    result = fixture_profile(tmp_path / 'profile.db', size, expired)
    assert result['runs'][-1]['completed']
    assert all(run['examined'] <= 256 for run in result['runs'])
    assert all(chunk['examined'] <= 64 for chunk in result['chunks'])
    assert sum(chunk['examined'] for chunk in result['chunks']) == size
    assert sum(chunk['deleted'] for chunk in result['chunks']) == expired
    assert result['remaining'] == size - expired
