"""Current price-free publish identity and page-memory refresh, entirely offline."""

import hashlib
import gzip
import json
from pathlib import Path
import re
import subprocess

import pytest

from dealradar.config import AppConfig, load_config, validated_manifest_refresh
from dealradar.db import DB
from dealradar.publish import manifest_refresh, site, seo_audit, indexnow
from tests.test_amazon_as_of import NOW, config, sample_deals, digest
from tests.test_event_pages import tree
from tests.test_e2e_offline import env, test_rerun_is_idempotent as check_rerun


# Captured before any production edits, on this checkout (all names and bytes).
BASELINES = json.loads('''{
  "membership-show-False": "f91af2f68ed1e8a00c74263429a6a81e37d2a99b90f88975dfe887019f44f874",
  "membership-show-True": "066d3e892c05aee96b4f6968704e1651dcffb2f851e74cd4868af12506a13fea",
  "membership-hide-False": "9e2632ef69b1d66bdec1247f6d06c882e13ee57aec03ef6a3ad1b4d83e35d21b",
  "membership-hide-True": "4d050c5b9d26c361a17cd9074fccea64ea6a81ec7c03462475eb9e2a38abd093",
  "open-show-False": "c596de783ab3bbc88fde3e1353e21c685dfc54b7018f73f9802df87cd46518ac",
  "open-show-True": "ce2e1db5e69151f7d720e5a91ec7be86d038e77270ab82f0caa03c2640692bd5",
  "open-hide-False": "5bb1c24c71da9a7d6522bdb28666732af4f3014887ac2dc8b0aae6522c63a660",
  "open-hide-True": "678899cb0ca04e2aa97d2c0db2ece51a1fc807bb022224eaf6dbad1c25cb92c2",
  "support-show-False": "a2d160416d5a8ea467076826d88cdf1e6af269dc0ad2539a7487ed6dcec965d2",
  "support-show-True": "de288cfa356c9b6b0ddcbd3b713684820c994528ad769d7339c0c10a3e731bb4",
  "support-hide-False": "a03444d10b21d337b77b62141d9df6d6f2e950a73e196ea587bc0e78090bb554",
  "support-hide-True": "d29059c3991df269c7991bee609001ba654a83eaf68b9006ddbb6052d5eaa848",
  "shipped-show-False": "c2ff9d0ce110a373111759b64bcd32720c52017cda728719fa34d64fe0962d83",
  "shipped-show-True": "9bdb6ff343a737f31d51f69f13f045dbc9c1e14068e303fdebc3782f48c9ce69",
  "shipped-hide-False": "b03b35ab568c2e4c05ec45e029fa4489b60b6ff233760c463a4170ced45ab562",
  "shipped-hide-True": "6cd9a61a7ad38a02ec7d5f3ee3dc69e3fcf22790ccf5739a51308d2efa76104b"
}
''')


@pytest.mark.parametrize('mode', ['membership', 'open', 'support', 'shipped'])
@pytest.mark.parametrize('history', ['show', 'hide'])
@pytest.mark.parametrize('as_of', [False, True])
def test_disabled_preserves_all_files_versions_and_headers(tmp_path, monkeypatch, mode, history, as_of):
    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    cfg = config(mode)
    cfg.amazon_history, cfg.amazon_as_of_enabled = history, as_of
    db = DB(':memory:')
    try:
        result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=sample_deals(), last_run={'run_key':'manifest'})
        files = tree(tmp_path)
        assert digest(files) == BASELINES[f'{mode}-{history}-{as_of}']
        assert not (tmp_path / manifest_refresh.NAME).exists()
        assert b'MANIFEST_REFRESH' not in files['index.html']
        assert b'publish-manifest' not in files['_headers']
        assert 'manifest_refresh' not in result['full_payload']['meta']
        # Explicit false after an enabled render removes the sole current artifact.
        cfg.manifest_refresh['enabled'] = True
        site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=sample_deals())
        cfg.manifest_refresh['enabled'] = False
        fresh = DB(':memory:')
        try:
            site.render_site(fresh, cfg, NOW, out_dir=tmp_path, deals=sample_deals(), last_run={'run_key':'manifest'})
        finally:
            fresh.close()
        assert tree(tmp_path) == files
    finally:
        db.close()


def test_config_ships_off_with_bounded_strict_controls(tmp_path):
    assert not AppConfig().manifest_refresh['enabled']
    assert not load_config().manifest_refresh['enabled']
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert not load_config(path).manifest_refresh['enabled']
    path.write_text(json.dumps({'publish': {'manifest_refresh': {'enabled': True, 'force_full_after_s': 60}}}))
    assert load_config(path).manifest_refresh == validated_manifest_refresh({'enabled': True, 'force_full_after_s': 60})


@pytest.mark.parametrize('value', [False, [], 'on', {'enabled':'false'}, {'enabled':1}, {'unexpected':1},
    {'max_bytes':511}, {'max_bytes':16385}, {'max_bytes':True}, {'force_full_after_s':29},
    {'force_full_after_s':901}, {'force_full_after_s':float('inf')}, {'force_full_after_s':300.0},
    {'max_deals_bytes':0}, {'max_deals_bytes':67108865}, {'max_future_s':-1}, {'max_future_s':901}])
def test_invalid_config_rejected(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'publish': {'manifest_refresh': value}}))
    with pytest.raises(ValueError, match='publish.manifest_refresh'):
        load_config(path)


def render_enabled(tmp_path, monkeypatch, mode='support'):
    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    cfg = config(mode)
    cfg.manifest_refresh['enabled'] = True
    db = DB(':memory:')
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=sample_deals(), last_run={'run_key':'manifest'})
    return cfg, db, result


@pytest.mark.parametrize('mode', ['membership', 'open', 'support', 'shipped'])
def test_exact_current_public_bytes_privacy_headers_and_sealing(tmp_path, monkeypatch, mode):
    calls = []
    original_audit, original_seal = seo_audit.audit, indexnow.seal_publish
    def audited(*args, **kwargs):
        assert (tmp_path / manifest_refresh.NAME).is_file()
        calls.append('audit')
        return original_audit(*args, **kwargs)
    def sealed(*args, **kwargs):
        calls.append('seal')
        return original_seal(*args, **kwargs)
    monkeypatch.setattr(seo_audit, 'audit', audited)
    monkeypatch.setattr(indexnow, 'seal_publish', sealed)
    cfg, db, result = render_enabled(tmp_path, monkeypatch, mode)
    try:
        raw = (tmp_path / 'deals.json').read_bytes()
        meta = json.loads(raw)['meta']
        manifest = (tmp_path / manifest_refresh.NAME).read_bytes()
        assert json.loads(manifest) == {'schema':1, 'sha256':hashlib.sha256(raw).hexdigest(), 'bytes':len(raw),
            'published_at':meta['published_at'], 'site_version':meta['site_version']}
        assert len(manifest) < cfg.manifest_refresh['max_bytes']
        headers = (tmp_path / '_headers').read_text()
        assert '/deals.json\n  X-Robots-Tag: noindex\n  Cache-Control: no-store' in headers
        assert manifest_refresh.HEADERS in headers
        assert 'immutable' not in headers and 'stale-while-revalidate' not in headers
        assert calls == ['audit', 'seal']
        assert not list(tmp_path.rglob('*patch*'))
        assert len(list(tmp_path.rglob(manifest_refresh.NAME))) == 1
        assert not any(k in json.loads(manifest) for k in ('deals','prices','keys','preferences','history'))
        assert 'publish-manifest' not in (tmp_path / 'sitemap.xml').read_text()
        home = (tmp_path / 'index.html').read_text()
        for i, script in enumerate(re.findall(r'<script>(.*?)</script>', home, re.S)):
            path = tmp_path / f'check-{i}.js'
            path.write_text(script)
            result = subprocess.run(['node','--check',str(path)], capture_output=True, text=True)
            assert result.returncode == 0, result.stderr
            path.unlink()
    finally:
        db.close()


@pytest.mark.parametrize('mode', ['membership', 'open', 'support', 'shipped'])
def test_browser_transport_drops_fallback_return_and_back_offline(tmp_path, monkeypatch, mode):
    cfg, db, _ = render_enabled(tmp_path, monkeypatch, mode)
    db.close()
    for harness in ('manifest_refresh_logic.cjs', 'browse_logic.cjs', 'home_polish_logic.cjs'):
        result = subprocess.run(['node', '--expose-gc', str(Path(__file__).with_name(harness)), str(tmp_path / 'index.html'),
                                 str(tmp_path / 'deals.json')], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        if harness.startswith('manifest'):
            assert json.loads(result.stdout)['deals'] == 2300
            (tmp_path / 'manifest-costs.json').write_text(result.stdout)


def test_same_publish_rerender_changes_no_manifest_or_rows(tmp_path, monkeypatch):
    cfg, db, _ = render_enabled(tmp_path, monkeypatch)
    try:
        before = tree(tmp_path)
        rows = db.conn.execute('SELECT count(*) FROM deals').fetchone()[0]
        site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=sample_deals(), last_run={'run_key':'manifest'})
        assert tree(tmp_path) == before
        assert db.conn.execute('SELECT count(*) FROM deals').fetchone()[0] == rows
    finally:
        db.close()


@pytest.mark.parametrize('audit_mode', ['shadow', 'enforce'])
def test_enabled_offline_pipeline_rerun_is_idempotent(env, capsys, audit_mode):
    path = Path(env['cfg'])
    path.write_text(path.read_text().replace('manifest_refresh:\n    enabled: false',
                                              'manifest_refresh:\n    enabled: true'))
    assert load_config(path).manifest_refresh['enabled']
    check_rerun(env, capsys, audit_mode)
    body = (env['site'] / 'deals.json').read_bytes()
    manifest = json.loads((env['site'] / manifest_refresh.NAME).read_bytes())
    assert manifest['sha256'] == hashlib.sha256(body).hexdigest()
    assert manifest['bytes'] == len(body)
    assert len(list(env['site'].rglob(manifest_refresh.NAME))) == 1


def test_enabled_home_preserves_markup_styles_and_image_priorities(tmp_path, monkeypatch):
    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    homes = []
    for enabled in (False, True):
        cfg = config('shipped')
        cfg.manifest_refresh['enabled'] = enabled
        db = DB(':memory:')
        try:
            site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=sample_deals(), last_run={'run_key':'manifest'})
            homes.append((tmp_path / 'index.html').read_bytes())
        finally:
            db.close()
    markup = lambda html: re.sub(rb'<script\b[^>]*>.*?</script>', b'', html, flags=re.S)
    assert markup(homes[0]) == markup(homes[1])
    (tmp_path / 'home-costs.json').write_text(json.dumps({'home_raw_added':len(homes[1])-len(homes[0]),
        'home_gzip_added':len(gzip.compress(homes[1], mtime=0))-len(gzip.compress(homes[0], mtime=0))}))


def test_interrupted_render_clears_previous_manifest(tmp_path, monkeypatch):
    cfg, db, _ = render_enabled(tmp_path, monkeypatch)
    def interrupted(*args, **kwargs):
        raise RuntimeError('interrupted before final JSON consumers')
    monkeypatch.setattr(site.seo, 'publish', interrupted)
    try:
        with pytest.raises(RuntimeError, match='interrupted'):
            site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=sample_deals())
        assert not (tmp_path / manifest_refresh.NAME).exists()
    finally:
        db.close()
