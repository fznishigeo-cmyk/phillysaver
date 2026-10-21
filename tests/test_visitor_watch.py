"""Task 270: only local publication, mocked Actions and route-fulfilled Chromium."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
import pytest
import yaml

from dealradar.config import AppConfig, load_config
from dealradar.publish import site
from dealradar.util import iso
from tests.test_publish_site import make_deal
from tests.test_support import NOW, render

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/visitor-watch.yml'


def node(args, **kwargs):
    if not shutil.which('node'):
        pytest.skip('Node unavailable')
    kwargs.setdefault('env', {**os.environ, 'PYTHON': sys.executable})
    return subprocess.run(['node', *map(str, args)], cwd=ROOT, capture_output=True,
                          text=True, timeout=180, **kwargs)


def test_node_logic():
    result = node(['tests/visitor_watch_logic.cjs'])
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr


def test_brief_matches_operator_ten_minute_schedule():
    brief = (ROOT / 'docs/codex-tasks/270-visitor-eye-watch.md').read_text()
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    cron = workflow['on']['schedule'][0]['cron']
    assert cron == '*/10 * * * *'
    assert 'watch every 10 minutes' in brief.splitlines()[0]
    assert f'cron `{cron}`' in brief
    assert 'supersedes' in brief


def test_workflow_read_only_and_snapshot_always_saved():
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    assert workflow['on']['schedule'] == [{'cron': '*/10 * * * *'}]
    assert workflow['permissions'] == {'contents': 'read', 'actions': 'read'}
    assert workflow['concurrency'] == {'group': 'visitor-watch', 'cancel-in-progress': 'false'}
    job = workflow['jobs']['watch']
    assert job['timeout-minutes'] == '12'
    steps = job['steps']
    install = next(step['run'] for step in steps if step.get('name') == 'Install Playwright Chromium')
    site_check = yaml.load((ROOT / '.github/workflows/site-check.yml').read_text(), Loader=yaml.BaseLoader)
    assert install == next(step['run'] for step in site_check['jobs']['check']['steps']
                           if step.get('name') == 'Install Playwright Chromium')
    upload = next(step for step in steps if step.get('name') == 'Save visitor snapshot even when repeated flags fail')
    assert upload['if'] == 'always()'
    assert upload['with']['path'] == 'visitor-watch/snapshot.json'
    assert upload['with']['name'] == 'visitor-snapshot'
    health = steps[-1]
    assert health['if'] == 'always()'
    assert health['with']['name'] == 'visitor-health'
    assert health['with']['path'] == 'visitor-watch/health.json'
    assert 'git push' not in WORKFLOW.read_text()


@pytest.mark.parametrize('scenario', ['failed', 'first', 'missing', 'expired'])
def test_previous_artifact_uses_immediate_completed_run_even_when_failed(scenario):
    workflow = yaml.safe_load(WORKFLOW.read_text())
    script = next(step['with']['script'] for step in workflow['jobs']['watch']['steps']
                  if step.get('name') == 'Fetch the immediately previous run snapshot')
    harness = r'''
const assert = require('node:assert/strict');
const [source, scenario] = process.argv.slice(1);
let downloaded = [], written = [], listed = [];
const previous = {id: 20, run_number: 2, conclusion: 'failure'};
const github = {rest: {actions: {
  listWorkflowRuns: async args => {
    assert.equal(args.workflow_id, 'visitor-watch.yml'); assert.equal(args.status, 'completed');
    assert.equal(args.branch, 'feature/task270');
    return {data: {workflow_runs: scenario === 'first' ? [] :
      [{id: 30, run_number: 3}, {id: 10, run_number: 1, conclusion: 'success'}, previous]}};
  },
  listWorkflowRunArtifacts: () => {},
  downloadArtifact: async args => {downloaded.push(args.artifact_id); return {data: Buffer.from('zip')};}
}}, paginate: async (method, args) => {
  listed.push(args.run_id);
  return scenario === 'missing' ? [] : [{id: 99, name: 'visitor-snapshot', expired: scenario === 'expired'}];
}};
const context = {repo: {owner: 'test', repo: 'test'}, runId: 30, runNumber: 3, ref: 'refs/heads/feature/task270'};
const localRequire = name => {assert.equal(name, 'node:fs'); return {
  writeFileSync: (...args) => written.push(args)}};
const core = {info() {}, warning() {}};
new Function('github', 'context', 'require', 'core', 'return (async () => {' + source + '})();')(
  github, context, localRequire, core).then(() => {
    assert.deepEqual(listed, scenario === 'first' ? [] : [20]);
    assert.deepEqual(downloaded, scenario === 'failed' ? [99] : []);
    assert.equal(written.length, scenario === 'failed' ? 1 : 0);
    if (written.length) assert.equal(written[0][0], 'previous-visitor-snapshot.zip');
  }).catch(error => {console.error(error); process.exitCode = 1});
'''
    result = node(['-e', harness, script, scenario])
    assert result.returncode == 0, result.stdout + result.stderr


def test_setup_failure_is_saved_and_only_repeated_flags_fail(tmp_path):
    output = tmp_path / 'snapshot.json'
    args = ['scripts/visitor_watch.js', '--offline-site', tmp_path, '--config', tmp_path / 'missing.yaml',
            '--output', output, '--now', iso(NOW)]
    first = node(args)
    assert first.returncode == 0, first.stdout + first.stderr
    snapshot = json.loads(output.read_text())
    assert len(snapshot['scenes']) == 4
    assert all('watcher config:' in scene['errors'][0] for scene in snapshot['scenes'])
    assert not snapshot['repeated']
    health = json.loads((tmp_path / 'health.json').read_text())
    assert health['status'] == 'flags' and 'errors' in health['flags']
    assert len(health['flags']) == len(set(health['flags']))
    assert len(json.dumps(health)) < 1000
    previous = tmp_path / 'previous.json'
    previous.write_bytes(output.read_bytes())
    args[-1] = iso(NOW + timedelta(minutes=15))
    second = node([*args, '--previous', previous])
    assert second.returncode == 1, second.stdout + second.stderr
    assert 'phone-light:errors' in json.loads(output.read_text())['repeated']


def test_chromium_search_navigation_and_delayed_white_image():
    result = node(['tests/visitor_watch_browser.cjs'])
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def watch_build(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError('task270 network unavailable')
    monkeypatch.setattr(httpx.Client, 'send', no_network)
    monkeypatch.setattr(site, 'util_now', lambda: NOW)

    def build(*, working_search=True, item='Cotton socks', category=None):
        cfg = AppConfig(support_enabled=True, dark_polish=True, affiliate_amazon_tag='wowpriced-20',
                        search_amazon=working_search)
        rows = [make_deal(dedupe_key=f'task270-{i}', item=f'{item} set {i}',
                          brand=f'Brand {i}', category=category or ('beauty' if i % 2 else 'kitchen'), codes=[], conditions=[],
                          image_url=f'https://images.example.invalid/photo-{i}.png') for i in range(32)]
        rows[-1].update(status='dead', death='confirmed', died_at=iso(NOW - timedelta(minutes=5)))
        out, doc, *_ = render(tmp_path, cfg, rows)
        return out, doc

    return build


def test_clean_fixture_headlines_and_separate_forbidden_text(watch_build):
    _, doc = watch_build()
    headlines = [row['headline'] for row in doc['deals']]
    assert all(row['regular_price'] == 50 for row in doc['deals'])
    assert any('the usual $50.00' in headline for headline in headlines)
    result = node(['-e', '''
const assert = require('node:assert/strict');
const {textFindings} = require('./scripts/visitor_watch_logic.cjs');
const headlines = JSON.parse(process.argv[1]);
assert(headlines.length >= 24);
for (const headline of [...headlines, 'List Price $50.00', 'Price drop'])
  assert.deepEqual(textFindings(headline), {invalid: [], history: []});
assert.deepEqual(textFindings('Price history: lowest we have seen in 90-day history').history,
  ['Price history', 'lowest we', '90-day']);
''', json.dumps(headlines)])
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('item', ['Multivitamin 90-day supply', 'Electric toothbrush with 90-day warranty'])
def test_real_serializer_product_durations_do_not_claim_history(item):
    public = site._serialize_deal(make_deal(item=item, codes=[]), NOW)
    assert item in public['headline']
    result = node(['-e', '''
const assert = require('node:assert/strict');
const {textFindings} = require('./scripts/visitor_watch_logic.cjs');
assert.deepEqual(textFindings(process.argv[1]), {invalid: [], history: []});
''', public['headline']])
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('route', ['tracker', 'source-post', 'krazy_coupon_lady',
                                  'moneysavingmom', 'passionate_penny_pincher'])
def test_real_serializer_get_deal_routes_are_healthy(route):
    cfg = load_config()
    posts = {'source-post': ('hip2save', 'https://hip2save.com/deals/cotton-socks/'),
             'krazy_coupon_lady': ('krazy_coupon_lady', 'https://thekrazycouponlady.com/deals/cotton-socks'),
             'moneysavingmom': ('moneysavingmom', 'https://moneysavingmom.com/cotton-socks/'),
             'passionate_penny_pincher': ('passionate_penny_pincher',
                                        'https://passionatepennypincher.com/cotton-socks/')}
    source, post = posts.get(route, ('hip2save', ''))
    target = ('https://go.dealmoon.com/item?affid=source&tag=source-20' if route == 'tracker'
              else post)
    row = make_deal(store='Amazon' if route == 'tracker' else 'Walmart',
                    canonical_url=target if route == 'tracker' else 'https://walmart.com/ip/123',
                    link_as_posted=target if route == 'tracker' else '',
                    codes=['SAVE'], sightings=[{'source_name': source, 'source_url': target}])
    public = site._serialize_deal(row, NOW, cfg.affiliate_amazon_tag,
                                 competitor_tracker_hosts=cfg.affiliate_competitor_tracker_hosts)
    assert public['url'] == target
    result = node(['-e', '''
const assert = require('node:assert/strict');
const {rulesFromConfig, dealLinkProblem} = require('./scripts/visitor_watch_logic.cjs');
const {readConfig} = require('./scripts/visitor_watch.js');
const row = JSON.parse(process.argv[1]);
const card = {store: row.store, href: row.url, creditUrls: row.credits.map(credit => credit.url)};
readConfig('config.yaml').then(config => {
  const rules = rulesFromConfig(config);
  assert.equal(dealLinkProblem(card, rules), '');
  for (const store of ['Amazon', 'Walmart']) {
    assert.equal(dealLinkProblem({...card, store}, rules), '');
    assert.equal(dealLinkProblem({...card, store, creditUrls: []}, rules), '');
    assert(dealLinkProblem({...card, store, href: 'https://wowpriced.com/deal/socks/'}, rules));
  }
}).catch(error => {console.error(error); process.exitCode = 1;});
''', json.dumps(public)])
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('store,target', [
    ('MorningSave', 'https://morningsave.com/deals/cotton-socks'),
    ('Meh', 'https://meh.com/deals/cotton-socks'),
    ('Woot', 'https://www.woot.com/offers/cotton-socks'),
    ('Target', 'https://www.target.com/p/cotton-socks/-/A-12345678'),
])
@pytest.mark.parametrize('sovrn,source_credit', [(False, True), (True, True), (True, False)])
def test_real_serializer_supported_retailers_need_no_host_list(store, target, sovrn, source_credit):
    row = make_deal(store=store, canonical_url=target, codes=[], conditions=[],
                    sightings=[{'source_name': store.lower() if source_credit else 'hip2save',
                                'source_url': target if source_credit else 'https://hip2save.com/deals/socks/'}])
    public = site._serialize_deal(row, NOW, 'wowpriced-20', sovrn_enabled=sovrn,
                                 sovrn_key='a' * 32)
    if sovrn and not source_credit:
        assert urlsplit(public['url']).hostname == 'redirect.viglink.com'
    else:
        assert public['url'] == target
    result = node(['-e', '''
const assert = require('node:assert/strict');
const {rulesFromConfig, dealLinkProblem} = require('./scripts/visitor_watch_logic.cjs');
const {readConfig} = require('./scripts/visitor_watch.js');
const row = JSON.parse(process.argv[1]);
readConfig('config.yaml').then(config => {
  assert.equal(dealLinkProblem({store: row.store, href: row.url}, rulesFromConfig(config)), '');
}).catch(error => {console.error(error); process.exitCode = 1;});
''', json.dumps(public)])
    assert result.returncode == 0, result.stdout + result.stderr


def test_watch_reads_the_live_used_tab_only_switch():
    result = node(['-e', """
const {readConfig} = require('./scripts/visitor_watch.js');
const {rulesFromConfig} = require('./scripts/visitor_watch_logic.cjs');
readConfig('config.yaml').then(config => console.log(JSON.stringify(rulesFromConfig(config).usedTabOnly)));
"""])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) is load_config().front_page['used_tab_only'] is True


def test_configured_image_hosts_cover_published_photo_fixtures():
    from dealradar.db import DB
    from dealradar.fetch.keepa import _image_url
    from dealradar.fetch.woot import _item_photo
    from tests.test_photo_identity import source_post

    db = DB(':memory:')
    try:
        post = source_post('hip2save', db)
    finally:
        db.close()
    images = [
        _image_url({'image': 'product.jpg'}),
        _item_photo({'Photos': [{'Url': 'https://d3gqasl9vmjfy8.cloudfront.net/robovac.jpg'}]}),
        post.image_url,
        'https://i5.walmartimages.com/skirt.jpg',
        'https://pisces.bbystatic.com/bestbuy.jpg',
        'https://t.scene7.com/G1',
        'https://dks.scene7.com/is/image/GolfGalaxy/X?qlt=70&wid=600',
        'https://static.nike.com/a/images/t_default/abc/air-force-1.png',
    ]
    assert all(images)
    configured = yaml.safe_load((ROOT / 'config.yaml').read_text())['quality']['visitor_watch']['image_hosts']
    public_images = []
    for image in images:
        row = make_deal(image_url=image, sightings=[{'source_name': post.source_name, 'source_url': post.url}])
        public = site._serialize_deal(row, NOW)
        assert public['image_url'] == image
        assert urlsplit(public['image_url']).hostname in configured
        public_images.append(public['image_url'])
    result = node(['-e', '''
const assert = require('node:assert/strict');
const {guardedRoute, readConfig} = require('./scripts/visitor_watch.js');
const {rulesFromConfig} = require('./scripts/visitor_watch_logic.cjs');
(async () => {
  const rules = rulesFromConfig(await readConfig('config.yaml'));
  for (const url of JSON.parse(process.argv[1])) {
    let fetched = false, fulfilled = false;
    await guardedRoute({
      request: () => ({url: () => url, method: () => 'GET', resourceType: () => 'image'}),
      fetch: async options => {assert.equal(options.url, url); fetched = true;
        return {headers: () => ({}), status: () => 200, dispose: async () => {}};},
      fulfill: async () => {fulfilled = true;}, abort: async () => assert.fail('published photo refused')
    }, {origin: 'https://wowpriced.com', rules, pace: async () => true,
      active: () => true, ignored: new Set(), countOwnRequest: () => assert.fail('external photo')});
    assert(fetched && fulfilled);
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
''', json.dumps(public_images)])
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('scenario', ['missing-search', 'clean', 'derived-hosts', 'ignored-input', 'sample-error',
                                      'missing-main-button', 'wrong-main-link', 'no-socks',
                                      'category-invalid', 'deal-invalid', 'both-invalid', 'category-history',
                                      'empty-department', 'identical-department'])
def test_chromium_local_static_build(tmp_path, watch_build, scenario):
    working_search = scenario != 'missing-search'
    out, doc = watch_build(working_search=working_search,
                          item='Kitchen kettle' if scenario == 'no-socks' else 'Cotton socks',
                          category={'empty-department': 'kitchen', 'identical-department': 'beauty'}.get(scenario))
    assert len(doc['deals']) >= 24
    if scenario == 'no-socks':
        assert all('socks' not in row['item'].lower() for row in doc['deals'])
    if scenario == 'ignored-input':
        home = out / 'index.html'
        home.write_text(home.read_text().replace('</body>', '''<script>
          document.getElementById('q').addEventListener('input', event => {
            if (event.target.value === 'cotton') event.stopImmediatePropagation();
          }, true);
        </script></body>'''))
    if scenario == 'sample-error':
        for sample in (out / 'deal').glob('*/index.html'):
            sample.write_text(sample.read_text().replace('</body>',
                '<script>throw new Error("sampled deal exception");</script></body>'))
    invalid_pages = ({'category', 'deal'} if scenario == 'both-invalid' else
                     {scenario.removesuffix('-invalid')} if scenario.endswith('-invalid') else set())
    samples = {'category': [out / 'deals/beauty-personal-care/index.html'],
               'deal': list((out / 'deal').glob('*/index.html'))}
    for page in invalid_pages | ({'category'} if scenario == 'category-history' else set()):
        text = 'Price history' if scenario == 'category-history' else '$NaN undefined null [object Object]'
        for sample in samples[page]:
            html = sample.read_text()
            assert '</main>' in html
            sample.write_text(html.replace('</main>', f'<p>{text}</p></main>', 1))
    if scenario in {'missing-main-button', 'wrong-main-link'}:
        import re
        for sample in (out / 'deal').glob('*/index.html'):
            html = sample.read_text()
            buttons = re.findall(r'<a class="getdeal"[^>]*>Get deal</a>', html)
            if not buttons:
                continue  # Ended pages are deliberately inert.
            assert len(buttons) > 1, 'the regression must retain related Get deal links'
            replacement = '' if scenario == 'missing-main-button' else buttons[0].replace(
                'https://www.walmart.com/ip/12345', 'https://www.walmart.com/ip/wrong-item')
            assert replacement != buttons[0]
            sample.write_text(html.replace(buttons[0], replacement, 1))
    config = tmp_path / 'watch.yaml'
    # derived-hosts: no configured photo host; the published deals.json supplies it.
    hosts = [] if scenario == 'derived-hosts' else ['images.example.invalid']
    config.write_text(yaml.safe_dump({'affiliate': {'amazon_tag': 'wowpriced-20'},
                                    'quality': {'visitor_watch': {'image_hosts': hosts}}}))
    output, summary = tmp_path / 'snapshot.json', tmp_path / 'summary.md'
    args = ['scripts/visitor_watch.js', '--offline-site', out, '--site', 'https://wowpriced.test/',
            '--config', config, '--output', output, '--summary', summary,
            '--now', iso(NOW + timedelta(minutes=5))]
    result = node(args)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
    snapshot = json.loads(output.read_text())
    assert len(snapshot['scenes']) == 4
    for scene in snapshot['scenes']:
        assert len(scene['cards']) == 24
        assert all(card['hasPhoto'] for card in scene['cards'])
        assert all(card['title'] and card['store'] and card['price'] == 10 for card in scene['cards'])
        assert any(card['ended'] for card in scene['cards'])
        assert all((not card['href'] and not card['target']) if card['ended'] else
                   (card['hrefHost'] and card['target'] == '_blank') for card in scene['cards'])
        assert scene['countdown'] == '10:00'
        assert scene['nextDropAt'] == iso(NOW + timedelta(minutes=15)).replace('+00:00', '.000Z')
        assert scene['errors'] == (['page: sampled deal exception'] if scenario == 'sample-error' else []), scene
        if working_search:
            assert scene['search']['clickedHref'].startswith('https://www.amazon.com/s?')
    flagged = {check['name'] for check in snapshot['checks'] if check['flag']}
    expected = {'missing-search': {'search'}, 'clean': set(), 'derived-hosts': set(), 'ignored-input': {'working'},
                'sample-error': {'errors'}, 'missing-main-button': {'working'}, 'wrong-main-link': {'working'},
                'no-socks': set(), 'category-invalid': {'text'}, 'deal-invalid': {'text'},
                'both-invalid': {'text'}, 'category-history': {'text'},
                'empty-department': set(), 'identical-department': set()}[scenario]
    assert flagged == expected, result.stdout
    assert summary.read_text().startswith('health: ok\n' if not expected else 'health: 1 flags ('), result.stdout
    health = json.loads((tmp_path / 'health.json').read_text())
    assert set(health['flags']) == flagged
    for scene in snapshot['scenes']:
        assert scene['photosDeep']['home'] and all(card['photo'] == 'loaded' for card in scene['photosDeep']['home'])
        assert scene['optical']['boxes']
        if scenario == 'ignored-input':
            assert 'query was not applied' in scene['working']['error']
        else:
            assert scene['working']['inventorySearch']['query'] == ('kitchen' if scenario == 'no-socks' else 'cotton')
            assert scene['working']['inventorySearch']['matches'] > 0
        assert scene['working']['deal']['status'] == 200
        assert scene['working']['deal']['price'] is True
        assert scene['working']['deal']['getDeal'] is (scenario not in {'missing-main-button', 'wrong-main-link'})
        assert scene['text'] == {
            'invalid': [f'{page}: {value}' for page in ['category', 'deal'] if page in invalid_pages
                        for value in ['$NaN', 'undefined', 'null', '[object']],
            'history': ['category: Price history'] if scenario == 'category-history' else [], 'used': []}
        assert scene['links'] and all(200 <= link['status'] < 400 for link in scene['links'])
        if scenario in {'empty-department', 'identical-department'}:
            department = scene['working']['department']
            eligible = {row['key'] for row in doc['deals'] if row['status'] != 'dead' and not row.get('locked')}
            assert department['active'] is True
            assert set(department['after']) == set(department['expected']) == eligible
            assert set(department['categories']) == ({'kitchen'} if scenario == 'empty-department' else {'beauty'})
    if not working_search or scenario in {'no-socks', 'category-invalid', 'deal-invalid',
                                           'both-invalid', 'category-history', 'empty-department', 'identical-department'}:
        previous = tmp_path / 'previous.json'
        previous.write_bytes(output.read_bytes())
        args[-1] = iso(NOW + timedelta(minutes=6))
        second = node([*args, '--previous', previous])
        clean_repeat = scenario in {'no-socks', 'empty-department', 'identical-department'}
        assert second.returncode == (0 if clean_repeat else 1), second.stdout + second.stderr
        second_snapshot = json.loads(output.read_text())
        assert {check['name'] for check in second_snapshot['checks'] if check['flag']} == expected
        repeated = second_snapshot['repeated']
        if clean_repeat:
            assert repeated == []
            if scenario != 'no-socks':
                assert [scene['working']['department']['after'] for scene in second_snapshot['scenes']] == [
                    scene['working']['department']['after'] for scene in snapshot['scenes']]
        else:
            assert len(repeated) == 4
            assert all(name.endswith(':search' if not working_search else ':text') for name in repeated)
