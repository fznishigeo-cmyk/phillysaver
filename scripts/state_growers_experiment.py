"""337b offline saturated model; real writers, bounded maintenance, owned scratch."""
import json
import tempfile
from datetime import timedelta
from pathlib import Path

from dealradar import accuracy, clicks, state_retention
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.util import iso
from scripts.state_growth import snapshot
from scripts.state_growth_experiment import receipts, maintenance
from scripts.state_size_experiment import measure
from scripts.state_write_experiment import LIVE, RECHECKS, START, inventory, retained_receipts, saturate

NOW = START + timedelta(days=14)


def drain(db, cfg, at, label):
    # Finish every frozen cohort, including samples newly migrated this pass.
    completed = set()
    for i in range(2000):
        key = f'{label}:{i}'
        maintenance(db, cfg, at, key)
        state = json.loads(db.conn.execute('SELECT state_maintenance FROM run_stats WHERE run_key=?', (key,)).fetchone()[0])
        completed.update(name for name, values in state.items() if values['completed'])
        if set(state) <= completed and {'posts_seen_details','posts_seen_cold','accuracy_samples_compact'} <= completed:
            return i+1
    raise RuntimeError('bounded fixture maintenance did not drain')


def samples(db, rows, at):
    with db.conn:
        for i in range(RECHECKS):
            row = rows[(i+int(at.timestamp()//3600)*79)%LIVE]
            accuracy.store_compact(db, row['dedupe_key'], iso(at)+'-hourly', at,
                                  at-timedelta(seconds=i%60), db._accuracy_published[row['dedupe_key']]['bucket'],
                                  'keepa', 'higher' if i%19==0 else 'lower' if i%29==0 else 'match')


def protected_history():
    """Separate full 90-day own-check envelope; no credit for existing own rows."""
    with tempfile.TemporaryDirectory(prefix='task337b-history-',dir='/tmp') as scratch:
        directory=Path(scratch)
        db=DB(directory/'state.db')
        try:
            before=measure(db,directory)
            with db.conn:
                for day in range(90):
                    at=NOW-timedelta(days=day)
                    values=[]
                    for i in range(LIVE):
                        stamp=at+timedelta(hours=i%24)
                        key=f'url|https://www.amazon.com/dp/B{i:09d}'
                        values.append((key,'Amazon',25+i%100,iso(stamp),f'check:keepa:{stamp:%Y-%m-%dT%H}'))
                    db.conn.executemany('INSERT INTO price_history(canonical_key,store,price,observed_at,content_hash) VALUES (?,?,?,?,?)',values)
            db.conn.execute('VACUUM')
            after=measure(db,directory)
            assert db.conn.execute('SELECT COUNT(*) FROM price_history').fetchone()[0]==LIVE*90
            assert db.conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            return dict(before=before,after=after,increment_enc_bytes=after['enc_bytes']-before['enc_bytes'])
        finally:
            db.close()


def main():
    cfg = load_config()
    with tempfile.TemporaryDirectory(prefix='task337b-model-', dir='/tmp') as scratch:
        directory = Path(scratch)
        db = DB(directory/'state.db')
        try:
            db.compact_deal_payloads = True
            rows = inventory(db)
            # Same 3,300 live offers/66k sightings/66k price facts as 337.
            # Saturate the actual 14-day audit window, 30-day receipt window,
            # and 14 days of accuracy arrivals (2,000/hour), not a young corpus.
            for h in range(24):
                samples(db, rows, START+timedelta(hours=h))
            clicks.compact_schema(db)
            db.conn.commit()
            saturate(db, cfg, rows)
            for h in range(9*24, 14*24):
                samples(db, rows, START+timedelta(hours=h))
            with db.conn:
                db.conn.execute('DELETE FROM deal_click_days')
                for day in range(6,14):
                    db.conn.executemany('INSERT INTO deal_click_days VALUES (?,?,?)',
                                        [(r['key'],int((START+timedelta(days=day)).timestamp()//86400), clicks.HOURS.pack(*([3600.]*24))) for r in rows])
            cohort = retained_receipts(db)
            with db.conn:
                for table, days in [('posts_seen',30),('source_audit_posts',14)]:
                    db.conn.execute(f"UPDATE {table} SET first_seen_at=strftime('%Y-%m-%dT%H:%M:%S',?, '-' || CAST((rowid-1)*?/ ? AS INTEGER) || ' seconds')||'+00:00'", (iso(NOW),days*86400,cohort[table]['rows']))
                db.conn.execute('UPDATE source_audit_posts SET parsed_at=first_seen_at')
            # Add the missing five days at the same 565/hour source arrival rate.
            for h in range(5*24):
                at = START+timedelta(hours=h)
                receipts(db,at,f'audit-window-{h}')
            # Extra post arrivals above are outside the retained 30-day receipt
            # target: remove only those synthetic extras before measurement.
            with db.conn:
                db.conn.execute('DELETE FROM posts_seen WHERE id>?', (cohort['posts_seen']['rows'],))
                total = db.conn.execute('SELECT COUNT(*) FROM source_audit_posts').fetchone()[0]
                db.conn.execute("UPDATE source_audit_posts SET first_seen_at=strftime('%Y-%m-%dT%H:%M:%S',?, '-' || CAST((rowid-1)*?/ ? AS INTEGER) || ' seconds')||'+00:00'", (iso(NOW),14*86400,total))
                db.conn.execute('UPDATE source_audit_posts SET parsed_at=first_seen_at')
            with db.conn:
                for hour in range(int((NOW-timedelta(days=45)).timestamp()//3600),int(NOW.timestamp()//3600)):
                    db.conn.executemany('INSERT INTO deal_clicks VALUES (?,?,?)',[(r['key'],hour,1+i%3) for i,r in enumerate(rows[:100])])
                    db.conn.execute('INSERT INTO deal_click_coverage VALUES (?,0)',(hour,))
            clicks.observe_publication(db,cfg,NOW,rows,run_key='seed')
            protected = {t:list(map(tuple,db.conn.execute(f'SELECT * FROM {t}'))) for t in ('deals','sightings','price_history','alerts_sent','keepa_recheck_history')}
            db.conn.execute('VACUUM')
            before_tables = snapshot(db.path)
            before = measure(db,directory)
            print(json.dumps({'phase':'built','before':before}),flush=True)
            calls = drain(db,cfg,NOW,'initial')
            db.conn.execute('VACUUM')
            after = measure(db,directory)
            after_tables = snapshot(db.path)
            print(json.dumps({'phase':'pruned','calls':calls,'after':after,'before_tables':before_tables['tables'],'after_tables':after_tables['tables']}),flush=True)
            # Project the format after the seven-day legacy backfill has aged
            # through: every fresh flagged writer already emits this encoding.
            # This is a lossless encoding projection, not accelerated expiry.
            with db.conn:
                from dealradar.state_growers import cold_post
                for row in db.conn.execute('SELECT * FROM posts_seen').fetchall():
                    assert cold_post(db,row)
                db.conn.execute('DELETE FROM posts_seen')
            db.conn.execute('VACUUM')
            steady=measure(db,directory)
            print(json.dumps({'phase':'steady_encoding','steady':steady,'tables':snapshot(db.path)['tables']}),flush=True)
            for h in range(1,25):
                at=NOW+timedelta(hours=h)
                receipts(db,at,f'next-{h}',compact_posts=True)
                samples(db,rows,at)
                with db.conn:
                    hour=int((at-timedelta(hours=1)).timestamp()//3600)
                    db.conn.executemany('INSERT INTO deal_clicks VALUES (?,?,?)',[(r['key'],hour,1+i%3) for i,r in enumerate(rows[:100])])
                    db.conn.execute('INSERT INTO deal_click_coverage VALUES (?,0)',(hour,))
                for minute in (0,1,16,31,46):
                    stamp=at+timedelta(minutes=minute)
                    clicks.observe_publication(db,cfg,stamp,rows,run_key=iso(stamp))
                    maintenance(db,cfg,stamp,'day:'+iso(stamp))
                with db.conn:
                    for i in range((h-1)*LIVE//24,h*LIVE//24):
                        r=rows[i]
                        db.conn.execute('INSERT INTO price_history(canonical_key,store,price,observed_at,content_hash) VALUES (?,?,?,?,?)', (r['dedupe_key'],'Amazon',r['final_price'],iso(at),'check:fixture:'+iso(at)[:10]))
            final_at=NOW+timedelta(hours=24,minutes=46)
            final_calls=drain(db,cfg,final_at,'final')
            db.conn.execute('VACUUM')
            final=measure(db,directory)
            assert all(list(map(tuple,db.conn.execute(f'SELECT * FROM {t}')))==values for t,values in protected.items() if t!='price_history')
            assert db.conn.execute('SELECT COUNT(*) FROM price_history').fetchone()[0]==len(protected['price_history'])+LIVE
            assert db.conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert not db.conn.execute('PRAGMA foreign_key_check').fetchall()
            print(json.dumps({'phase':'day','calls':final_calls,'final':final,'elapsed_hours':24+46/60,'enc_bytes_per_hour':(final['enc_bytes']-steady['enc_bytes'])/(24+46/60),'with_10mb_reserve':final['enc_bytes']+10_000_000,'final_tables':snapshot(db.path)['tables']}),flush=True)
        finally:
            db.close()
    history=protected_history()
    # Separate gzip streams are only a model; add 5% for layout/dictionary mix.
    projected=final['enc_bytes']+1.05*history['increment_enc_bytes']
    print(json.dumps({'phase':'full_history_projection','history':history,'projected_enc_bytes':projected,'with_10mb_reserve':projected+10_000_000}),flush=True)


if __name__=='__main__':
    import sys
    if sys.argv[1:]==['--history-only']:
        print(json.dumps(protected_history()),flush=True)
    else:
        main()
