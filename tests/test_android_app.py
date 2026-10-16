"""Android stays off; app restrictions and builds are checked without external calls."""

import hashlib
import json
import lzma
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
import yaml

from dealradar.config import AppConfig, load_config, validated_android
from dealradar.db import DB
from dealradar.publish import android_app, checker, pwa, site, tracking
from tests.test_checker_index import add, data, files
from tests.test_product_price_pages import seed
from tests.test_publish_site import NOW

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).with_name("fixtures")


def baseline_config(mode, checking):
    cfg = load_config() if mode == "shipped" else AppConfig(custom_domain="wowpriced.com")
    if mode != "shipped":
        cfg.support_enabled = mode == "support"
        cfg.paywall_open_access = mode == "open"
    cfg.paywall_teaser_count = 1
    cfg.pwa_enabled = True
    cfg.checker["enabled"] = checking
    return cfg


def baseline_rows(db):
    return seed(db) + [add(db, "bestbuy", canonical_url="https://www.bestbuy.com/site/example/6505727.p")]


def app_config():
    cfg = baseline_config("support", True)
    cfg.pwa_android["enabled"] = True
    cfg.seo_product_pages_enabled = True
    cfg.seo_hub_min_items = 1
    cfg.affiliate_amazon_tag = "example-20"
    cfg.support_payment_link = "https://buy.stripe.com/test_support"
    cfg.support_monthly_link = "https://buy.stripe.com/test_monthly"
    cfg.tracking = {"ga4_measurement_id": "G-ABC123", "meta_pixel_id": "12345678", "clarity_project_id": "abc123def"}
    cfg.checker["stores"]["amazon"] = "history"
    cfg.amazon_watches = "off"
    cfg.amazon_as_of_enabled = True
    return cfg


def build_app(out, cfg=None):
    cfg = cfg or app_config()
    db = DB(":memory:")
    before = site.util_now
    site.util_now = lambda: NOW
    try:
        rows = baseline_rows(db)
        rendered = site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
    finally:
        site.util_now = before
        db.close()
    return rendered


@pytest.fixture
def app(tmp_path):
    out = tmp_path / "site"
    result = build_app(out)
    return out, result


@pytest.mark.parametrize("mode", ["membership", "open", "support", "shipped"])
@pytest.mark.parametrize("checking", [False, True])
def test_off_matches_every_untouched_head_byte_and_rerun(tmp_path, monkeypatch, mode, checking):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    # privacy.html (and check/data/all.json) re-recorded for tasks 244b/244c's extension disclosure.
    cfg = baseline_config(mode, checking)
    out = tmp_path / "site"
    baseline = json.loads(lzma.decompress((FIX / "android_disabled.json.xz").read_bytes()))["modes"][f"{mode}-{checking}"]
    db = DB(":memory:")
    try:
        rows = baseline_rows(db)
        # A raw membership render intentionally rotates Free 8; the existing
        # pipeline run-key tests cover closed-mode reruns. Open modes are stable.
        for _ in range(1 if mode == "membership" else 2):
            result = site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
            assert {name: hashlib.sha256(body).hexdigest() for name, body in files(out).items()} == baseline["files"]
            assert hashlib.sha256(json.dumps(result["full_payload"], sort_keys=True).encode()).hexdigest() == baseline["members"]
            assert not (out / "sw.js").exists() and not (out / "offline.html").exists()
            assert not (out / ".well-known").exists()
    finally:
        db.close()


def test_defaults_disabled_and_independent_lists(tmp_path):
    assert load_config().pwa_android == AppConfig().pwa_android == validated_android({})
    assert not load_config().pwa_android["enabled"]
    assert load_config().pwa_android["sha256_cert_fingerprints"] == []
    a, b = AppConfig(), AppConfig()
    a.pwa_android["sha256_cert_fingerprints"].append("test")
    assert b.pwa_android["sha256_cert_fingerprints"] == []
    path = tmp_path / "config.yaml"
    path.write_text("pwa:\n  android:\n    enabled: true\n    amazon_in_app: as_site\n")
    assert load_config(path).pwa_android == validated_android({"enabled": True, "amazon_in_app": "as_site"})


def test_disabled_pwa_postpass_accepts_empty_output(tmp_path):
    pwa.publish(tmp_path, AppConfig())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("value", [None, [], False, {"extra": 1}, {"enabled": "false"},
    {"package": "Com.wowpriced.app"}, {"package": "app"}, {"package": "com..app"},
    {"package": "com.9app"}, {"package": "com.app\n"}, {"package": 123},
    {"sha256_cert_fingerprints": "AA:" * 31 + "AA"},
    {"sha256_cert_fingerprints": ["aa:" * 31 + "aa"]},
    {"sha256_cert_fingerprints": ["AA:" * 30 + "AA"]},
    {"sha256_cert_fingerprints": ["GG:" * 31 + "GG"]},
    {"sha256_cert_fingerprints": [123]}, {"sha256_cert_fingerprints": None},
    {"amazon_in_app": "full"}, {"amazon_in_app": []}])
def test_invalid_config_rejected_even_disabled(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"pwa": {"android": value}}))
    with pytest.raises(ValueError, match="pwa.android"):
        load_config(path)


def test_assetlinks_exact_bytes_headers_regular_file_and_empty_cleanup(app):
    out, _ = app
    assert not (out / ".well-known").exists()
    cfg = app_config()
    first, second = ":".join(["AA"] * 32), ":".join(["BB"] * 32)
    cfg.pwa_android["sha256_cert_fingerprints"] = [first, second]
    pwa.publish(out, cfg)
    asset = out / ".well-known/assetlinks.json"
    expected = ('[{"relation":["delegate_permission/common.handle_all_urls"],"target":'
                '{"namespace":"android_app","package_name":"com.wowpriced.app",'
                '"sha256_cert_fingerprints":["' + first + '","' + second + '"]}}]').encode()
    assert asset.read_bytes() == expected and asset.is_file() and not asset.is_symlink()
    assert (out / "_headers").read_text().count(android_app.HEADERS) == 1
    robots = (out / "robots.txt").read_bytes()
    once = files(out)
    pwa.publish(out, cfg)
    assert files(out) == once
    assert (out / "robots.txt").read_bytes() == robots
    cfg.pwa_android["sha256_cert_fingerprints"] = []
    pwa.publish(out, cfg)
    assert not (out / ".well-known").exists()
    assert android_app.HEADERS not in (out / "_headers").read_text()


def test_assetlinks_replaces_symlink_without_writing_target(app, tmp_path):
    out, _ = app
    cfg = app_config()
    cfg.pwa_android["sha256_cert_fingerprints"] = [":".join(["CC"] * 32)]
    target = tmp_path / "unchanged.txt"
    target.write_text("unchanged")
    asset = out / ".well-known/assetlinks.json"
    asset.parent.mkdir()
    asset.symlink_to(target)
    pwa.publish(out, cfg)
    assert not asset.is_symlink() and target.read_text() == "unchanged"


def test_enabled_rerender_off_cleanup_and_version_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = app_config()
    cfg.pwa_android["enabled"] = False
    db = DB(":memory:")
    out = tmp_path / "site"
    try:
        rows = baseline_rows(db)
        original = site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
        before = files(out)
        cfg.pwa_android["enabled"] = True
        first = site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
        enabled = files(out)
        assert first["full_payload"]["meta"]["site_version"] != original["full_payload"]["meta"]["site_version"]
        state = db.conn.iterdump()
        dump = list(state)
        site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
        assert files(out) == enabled and list(db.conn.iterdump()) == dump
        version = pwa.site_version("base", cfg)
        cfg.pwa_android["amazon_in_app"] = "as_site"
        assert pwa.site_version("base", cfg) != version
        cfg.pwa_android["enabled"] = False
        site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
        assert files(out) == before
    finally:
        db.close()


def test_offline_price_free_worker_navigation_only_and_versioned(app):
    out, result = app
    offline, sw = (out / "offline.html").read_text(), (out / "sw.js").read_text()
    assert "$" not in offline
    assert "You're offline." in offline and "Prices only show live: reconnect to check a deal." in offline
    assert '>Retry</button>' in offline and 'aria-label="wowpriced"' in offline
    assert "googletagmanager" not in offline and "connect.facebook.net" not in offline
    assert result["full_payload"]["meta"]["site_version"] in sw
    assert sw.count('addEventListener("fetch"') == 1
    assert 'event.request.mode === "navigate"' in sw
    assert 'cache.add(OFFLINE)' in sw and '/offline.html' in sw
    assert all(token not in sw for token in ('/check/data', 'deals.json', '/p/', '.put(', 'addAll'))
    assert 'wp-android' in sw and android_app.WORKER_HEADERS in (out / "_headers").read_text()


def node(script, *args):
    env = os.environ.copy()
    env["NODE_PATH"] = "/opt/node-tools/node_modules" + os.pathsep + env.get("NODE_PATH", "")
    result = subprocess.run(["node", str(ROOT / "tests" / script), *map(str, args)],
                            text=True, capture_output=True, env=env, timeout=90)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, (result.stdout + result.stderr)[-8000:]


def test_node_context_storage_tracking_clicks_shares_and_worker(app):
    cfg = app_config()
    cfg.pwa_android["amazon_in_app"] = "as_site"
    as_site = app[0].parent / "as-site.js"
    as_site.write_text(re.search(r'<script>(.*?)</script>', android_app.head(cfg), re.S)[1])
    node("android_logic.cjs", app[0], as_site)


def test_chromium_app_navigation_privacy_amazon_offline_and_share(app, tmp_path):
    out, result = app
    rows = result["public_deals"]
    (tmp_path / "browser.json").write_text(json.dumps(rows))
    node("android_browser.cjs", out, tmp_path / "browser.json")


def test_amazon_history_markup_prices_and_links_stay_in_html(app):
    out, result = app
    amazon = next(d for d in result["public_deals"] if d["store"] == "Amazon")
    detail = (out / amazon["deal_path"].strip("/") / "index.html").read_text()
    product = (out / amazon["product_path"].strip("/") / "index.html").read_text()
    assert 'data-app-amazon>' in detail and 'class="price-history"' in detail
    assert 'data-app-amazon-history>' in product and 'class="price-verdict"' in product
    assert 'class="product-facts"' in product
    assert 'Amazon price history is on wowpriced.com in your browser.' in product
    assert '>See the deal</a>' in product
    assert 'tag=example-20' in detail and 'tag=example-20' in product
    assert data(out)['amazon:B000000000']["price"] == 10  # app hides; published data stays the site's
    check = (out / 'check/index.html').read_text()
    assert check.index('function wpAndroidContext') < check.index('function show(entry, parts)')
    assert 'worker-src \'self\'' in check
    assert 'googletagmanager.com' not in check and 'clarity.ms' not in check
    for path in out.rglob('*.html'):
        if path.name == 'offline.html':
            continue
        content = path.read_text()
        assert content.count('function wpAndroidContext()') == 1
        assert content.index('<!-- wp-pwa-head -->') < content.index('function wpAndroidContext') < content.index('<!-- /wp-pwa-head -->')
        if '<!-- wp-tracking -->' in content:
            assert content.index('function wpAndroidContext') < content.index('<!-- wp-tracking -->')


def test_mixed_history_hides_amazon_aggregates_but_keeps_non_amazon_offer():
    cfg = app_config()
    content = ('<p>facts</p><p class="detail-store">Store: Best Buy</p><p>current price</p>'
               '<figure class="price-history">chart</figure><h2>Deals we found</h2><ul><li>past price</li></ul>')
    page = {"checks": [{"store": "Amazon"}, {"store": "Best Buy"}], "live": None}
    hidden = android_app.product_content(content, page, cfg, {})
    assert hidden.count('data-app-amazon-history') == 3
    assert '</div><p class="detail-store">Store: Best Buy</p><p>current price</p>' in hidden


def test_amazon_policy_uses_original_offer_facts_without_observations():
    cfg = app_config()
    content = ('<p>facts</p><p class="detail-store">Store: Best Buy</p><p>current price</p>'
               '<figure class="price-history">chart</figure><h2>Deals we found</h2><ul><li>past price</li></ul>')
    page = {"checks": [], "live": None, "offers": {"a": {}, "b": {}}}
    sources = {"a": {"store": "Other", "canonical_url": "https://www.amazon.com/dp/B000000000"},
               "b": {"store": "Best Buy"}}
    mixed = android_app.product_content(content, page, cfg, {}, sources)
    assert mixed.count('data-app-amazon-history') == 3
    assert '</div><p class="detail-store">Store: Best Buy</p><p>current price</p>' in mixed
    page["offers"] = {"a": {}}
    only = android_app.product_content(content, page, cfg, {}, sources)
    assert only.startswith('<div data-app-amazon-history>' + content + '</div>')
    assert 'Amazon price history is on wowpriced.com in your browser.' in only


def test_android_can_run_without_the_web_install_prompt(tmp_path):
    cfg = app_config()
    cfg.pwa_enabled = False
    result = build_app(tmp_path, cfg)
    assert not (tmp_path / 'manifest.webmanifest').exists()
    assert (tmp_path / 'pwa-icons/icon-512.png').is_file()
    assert 'function wpAndroidContext()' in (tmp_path / 'check/index.html').read_text()
    assert result['full_payload']['meta']['site_version'] in (tmp_path / 'sw.js').read_text()


def test_ended_amazon_note_uses_a_deterministic_deal_link():
    cfg = app_config()
    page = {"checks": [{"store": "Amazon"}], "live": None, "offers": {"b": {}, "a": {}}}
    paths = {hashlib.sha256(key.encode()).hexdigest()[:8]: '/deal/' + key + '/' for key in ('a', 'b')}
    first = android_app.product_content('recorded facts', page, cfg, paths)
    page['offers'] = dict(reversed(list(page['offers'].items())))
    assert android_app.product_content('recorded facts', page, cfg, paths) == first
    assert 'href="/deal/a/"' in first


def test_manifest_matches_config_brand_and_share_contract():
    manifest = json.loads((ROOT / 'android/twa-manifest.json').read_text())
    light, dark, background = pwa.colors()
    assert manifest['packageId'] == load_config().pwa_android['package'] == 'com.wowpriced.app'
    assert manifest['host'] == 'wowpriced.com'
    assert manifest['name'] == 'wowpriced: Is it a good deal?' and manifest['launcherName'] == 'wowpriced'
    assert manifest['startUrl'] == '/check/?src=twa'
    assert manifest['display'] == 'standalone' and manifest['fallbackType'] == 'customtabs'
    assert manifest['enableNotifications'] is False
    assert (manifest['themeColor'], manifest['themeColorDark'], manifest['backgroundColor']) == (light, dark, background)
    assert manifest['iconUrl'] == 'https://wowpriced.com/pwa-icons/icon-512.png'
    assert manifest['maskableIconUrl'] == 'https://wowpriced.com/pwa-icons/icon-maskable-512.png'
    assert manifest['shareTarget'] == {'action':'/check/', 'method':'GET', 'params':{'title':'title', 'text':'text', 'url':'u'}}
    assert manifest['signingKey'] == {'path':'./upload.keystore', 'alias':'upload'}
    assert (manifest['appVersionCode'], manifest['appVersionName']) == (1, '1.0.0')
    assert [s['name'] for s in manifest['shortcuts']] == ['Check a price', "Today's best deals"]
    assert [s['url'] for s in manifest['shortcuts']] == ['https://wowpriced.com/check/?src=twa', 'https://wowpriced.com/best-deals-today/?src=twa']
    for shortcut in manifest['shortcuts']:
        assert shortcut['chosenIconUrl'] == 'https://wowpriced.com/pwa-icons/icon-192.png'
    assert all(url.startswith('https://wowpriced.com/') for url in re.findall(r'https?://[^"\s]+', json.dumps(manifest)))


def workflow():
    return yaml.safe_load((ROOT / '.github/workflows/android.yml').read_text())


def test_workflow_manual_only_scoped_signing_artifacts_and_cleanup():
    flow = workflow()
    assert set(flow[True]) == {'workflow_dispatch'}
    assert set(flow[True]['workflow_dispatch']['inputs']) == {'version_code', 'version_name'}
    assert flow['permissions'] == {'contents':'read'} and flow['concurrency']['group'] == 'android'
    job = flow['jobs']['build']
    assert job['runs-on'] == 'ubuntu-24.04' and job['environment'] == 'android-signing'
    steps = job['steps']
    commands = '\n'.join(s.get('run', '') for s in steps)
    assert '@bubblewrap/cli@1.27.0' in commands
    assert 'bubblewrap update --skipVersionUpgrade' in commands and 'bubblewrap build --skipSigning' in commands
    assert not re.search(r'bubblewrap\s+play\b|androidpublisher|service.account', commands, re.I)
    assert not re.search(r'\$\{\{\s*secrets\.', commands)
    assert any(s.get('if') == 'always()' and 'rm -f "$RUNNER_TEMP/twa/upload.keystore"' in s.get('run','') for s in steps)
    assert any(s.get('with',{}).get('java-version') == '17' and s['with']['distribution'] == 'temurin' for s in steps)
    assert any(s.get('with',{}).get('node-version') == '22' for s in steps)
    upload = next(s['with'] for s in steps if 'actions/upload-artifact@' in s.get('uses',''))
    assert upload['retention-days'] == 7
    assert upload['path'].splitlines() == ['${{ runner.temp }}/twa/*.aab', '${{ runner.temp }}/twa/*.apk']
    signing = next(s for s in steps if s.get('name') == 'Generate and build')
    assert set(signing['env']) >= {'ANDROID_UPLOAD_KEYSTORE_B64', 'BUBBLEWRAP_KEYSTORE_PASSWORD', 'BUBBLEWRAP_KEY_PASSWORD'}
    assert 'keytool -list' in signing['run'] and 'SHA256:' in signing['run']


@pytest.mark.parametrize('signed', [False, True])
def test_workflow_build_branches_offline(tmp_path, signed):
    step = next(s for s in workflow()['jobs']['build']['steps'] if s.get('name') == 'Generate and build')
    project = tmp_path / 'runner temp/twa'
    project.mkdir(parents=True)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'bubblewrap').write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$RUNNER_TEMP/calls"\n')
    (bin_dir / 'keytool').write_text('#!/bin/bash\necho "SHA256: PUBLIC-CERTIFICATE"\n')
    for executable in bin_dir.iterdir():
        executable.chmod(0o755)
    env = os.environ | {'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'], 'RUNNER_TEMP':str(project.parent),
                       'ANDROID_UPLOAD_KEYSTORE_B64':'ZmFrZS1rZXk=' if signed else '',
                       'BUBBLEWRAP_KEYSTORE_PASSWORD':'not-a-real-password', 'BUBBLEWRAP_KEY_PASSWORD':'not-a-real-key-password'}
    result = subprocess.run(['bash','-euo','pipefail','-c',step['run']], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert (project.parent / 'calls').read_text().splitlines() == ['update --skipVersionUpgrade', 'build' if signed else 'build --skipSigning']
    assert 'not-a-real' not in result.stdout + result.stderr
    assert (project / 'upload.keystore').exists() is signed
    if signed:
        assert (project / 'upload.keystore').read_bytes() == b'fake-key'
        assert (project / 'upload.keystore').stat().st_mode & 0o777 == 0o600
        assert result.stdout.strip() == 'SHA256: PUBLIC-CERTIFICATE'
    cleanup = next(s['run'] for s in workflow()['jobs']['build']['steps'] if s.get('if') == 'always()')
    subprocess.run(['bash','-euo','pipefail','-c',cleanup],env=env,check=True)
    assert not (project / 'upload.keystore').exists()


@pytest.mark.parametrize('code,name,valid', [
    ('7', '1.2.3', True), ('2100000000', '2.0.0-rc1', True),
    ('0', '1.0.0', False), ('01', '1.0.0', False), ('2100000001', '1.0.0', False),
    ('999999999999999999999', '1.0.0', False), ('1; exit 0', '1.0.0', False),
    ('1', '$(touch unexpected)', False), ('1', '1.2', False)])
def test_workflow_versions_change_only_temporary_manifest(tmp_path, code, name, valid):
    step = next(s for s in workflow()['jobs']['build']['steps'] if s.get('name') == 'Prepare temporary project and version')
    source = tmp_path / 'android'
    source.mkdir()
    manifest = (ROOT / 'android/twa-manifest.json').read_bytes()
    (source / 'twa-manifest.json').write_bytes(manifest)
    runner = tmp_path / 'runner temp'
    runner.mkdir()
    env = os.environ | {'RUNNER_TEMP': str(runner), 'VERSION_CODE': code, 'VERSION_NAME': name}
    result = subprocess.run(['bash', '-euo', 'pipefail', '-c', step['run']], env=env,
                            cwd=tmp_path, text=True, capture_output=True)
    assert (result.returncode == 0) is valid, result.stderr
    assert (source / 'twa-manifest.json').read_bytes() == manifest
    if valid:
        changed = json.loads((runner / 'twa/twa-manifest.json').read_bytes())
        assert (changed.pop('appVersionCode'), changed.pop('appVersionName')) == (int(code), name)
        original = json.loads(manifest)
        original.pop('appVersionCode'); original.pop('appVersionName')
        assert changed == original
    else:
        assert not (runner / 'twa').exists() and not (tmp_path / 'unexpected').exists()


def test_store_copy_and_feature_graphic():
    listing = (ROOT / 'android/store/listing.md').read_text()
    title = re.search(r'\*\*Title:\*\* (.*)', listing)[1]
    short = re.search(r'\*\*Short description:\*\* (.*)', listing)[1]
    assert len(title) <= 30 and len(short) <= 80
    assert not re.search(r'amazon|walmart|best buy|ebay|target', title, re.I)
    assert 'Share any product link to see if it\'s a good deal' in listing
    assert 'GA4' in listing and '18 and over' in listing and 'Nothing sold' in listing
    png = (ROOT / 'android/store/feature-graphic.png').read_bytes()
    assert png[:8] == b'\x89PNG\r\n\x1a\n' and struct.unpack('>II', png[16:24]) == (1024, 500)
    assert png[25] == 2  # RGB, no alpha
