"""Task 342: native stretched Get deal links and protected card controls."""
import hashlib
import re
import subprocess
from pathlib import Path

from dealradar.config import AppConfig, load_config
from dealradar.publish import card_click_1020 as cards, seo
from tests.test_publish_site import make_deal
from tests.test_search_more import offline  # noqa: F401
from tests.test_support import render


def test_off_is_a_noop_even_for_non_bool(tmp_path):
    for value in (False, None, 1):
        cfg = AppConfig()
        cfg.card_click_1020 = value
        assert not cards.enabled(cfg)
        assert cards.site_version('old', cfg) == 'old'
        cards.publish(tmp_path, cfg)
        assert not list(tmp_path.iterdir())


def test_shared_css_and_unchanged_native_markup(tmp_path):
    cfg = load_config()
    cfg.non_amazon_freshness_enabled = False
    cfg.credit_links = True
    cfg.max_photoless_h = None
    deals = [make_deal(dedupe_key='live', item='Basket', store='Target', codes=['SAVE'],
                       conditions=['code'], category='home', image_url='https://images.example.invalid/basket.jpg'),
             make_deal(dedupe_key='other', item='Glass vase', brand='Acme', store='Target',
                       canonical_url='https://www.target.com/p/vase', category='home', codes=[],
                       conditions=[], image_url='https://images.example.invalid/vase.jpg')]
    cfg.card_click_1020 = False
    out, off, *_ = render(tmp_path, cfg, deals)
    before = {str(p.relative_to(out)): p.read_text() for p in out.rglob('*.html')}
    cfg.card_click_1020 = True
    out, on, *_ = render(tmp_path, cfg, deals)
    css = (out / 'card-click.css').read_text()
    assert css == cards.CSS
    version = hashlib.sha256(css.encode()).hexdigest()[:12]
    link = '<link rel="stylesheet" href="/card-click.css?v=' + version + '">'
    shared = []
    for path in out.rglob('*.html'):
        html = path.read_text()
        name = str(path.relative_to(out))
        if 'class="card' in html:
            shared.append(name)
            assert link in html
            assert html.index(link) < html.index('</head>')
        assert html.replace(link, '').replace(on['meta']['site_version'], off['meta']['site_version']) == before[name]
    assert 'index.html' in shared and 'best-deals-today/index.html' in shared
    assert any(name.startswith('deal/') for name in shared)
    assert cards.CARD + '{position:relative;cursor:pointer}' in css
    assert cards.CARD + ' .actions{isolation:auto}' in css
    assert cards.CARD + ' .actions>.getdeal{position:static;z-index:auto;filter:none;transform:none}' in css
    assert cards.CARD + ' .getdeal::after{content:"";position:absolute;inset:0;z-index:3;' in css
    fresh = Path('dealradar/publish/templates/drop-fresh.css').read_text()
    assert re.search(r'\.just-dropped\{[^}]*z-index:2', fresh)  # Below overlay, above the photo.
    assert ':is(.title,a:not(.getdeal),button,summary,input){position:relative;z-index:4}' in css
    assert cards.CARD + ' .media .save-deal{position:absolute}' in css
    assert cards.CARD + ' .title a{position:static}' in css
    assert not re.search('hover|animation|content-visibility|pointer-events|trace|transform:(?!none)', css)
    for dead, url in ((False, 'https://target.example/basket'), (True, 'https://target.example/basket'), (False, '')):
        markup = seo.card_html({'item':'Basket', 'url':url, 'status':'dead' if dead else 'alive',
                                'credits':[], 'codes':[], 'conditions':[]})
        assert ('class="getdeal"' in markup) is bool(url and not dead)
        if dead:
            assert 'class="card gone' in markup
    # The overlay is a pseudo-element: no duplicate link, card tabindex or card listener.
    assert 'tabindex' not in cards.CSS
    html = (out / 'index.html').read_text()
    assert not re.search(r'<article[^>]+(?:onclick|tabindex)', html)


def test_browser_harness_syntax():
    for name in ('search_box_glow_browser.cjs', 'page_polish_browser.cjs', 'search_discounts_button_logic.cjs',
                 'task342_browser_checks.cjs'):
        run = subprocess.run(['node', '--check', 'tests/' + name], capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
