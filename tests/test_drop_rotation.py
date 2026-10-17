"""Task 269: bounded, aligned per-drop picks with the checkout-price path on."""
import json
from pathlib import Path
import random
import shutil
import subprocess
from collections import Counter

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import drop_rotation, site
from dealradar.rank import top_picks
from tests.test_checkout_price_deals import enable, offer, remember
from tests.test_front_page import browser as run_home_logic
from tests.test_price_accuracy_engine import NOW
from tests.test_publish_site import make_deal
from tests.history_pins import pre_first_screen_mix

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("value", [0, 1, 3, 5])
def test_config(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {"drop_rotation_windows": value}}))
    assert load_config(path).drop_rotation_windows == value
    path.write_text("{}")
    assert load_config(path).drop_rotation_windows == AppConfig().drop_rotation_windows == 3
    assert load_config().drop_rotation_windows == 3


@pytest.mark.parametrize("value", [-1, 1.5, True, False, "3", None, [], {}])
def test_invalid_config(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {"drop_rotation_windows": value}}))
    with pytest.raises(ValueError, match="site.drop_rotation_windows must be a non-negative integer"):
        load_config(path)


@pytest.fixture
def rotation_site(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = pre_first_screen_mix(load_config())
    cfg.tracking = {}
    cfg.join_prompt = False
    cfg.manifest_refresh["enabled"] = False
    cfg.front_page["popular_affordable"]["shadow"] = False
    rows = [make_deal(dedupe_key=f"task269-{i}", item=f"Storage{i} kitchen set", brand=f"Brand {i}",
                      store=["Walmart", "Target", "Best Buy", "Woot", "eBay"][i % 5],
                      category=["home", "kitchen", "beauty", "fashion", "toys_games"][i % 5],
                      first_seen_at=NOW.isoformat(), price_rechecked_at=NOW.isoformat(),
                      image_url="https://images.example.invalid/card.png", codes=[], conditions=[])
            for i in range(120)]
    db = DB(":memory:")
    try:
        out = tmp_path / "site"
        def publish(windows=3, *, with_deals=True, with_spotlight=False, spotlight_price_only=False,
                    categories=None, promos=None, spotlight_checkout=True, spotlight_index=24, checkout_window=5):
            cfg.drop_rotation_windows = windows
            cfg.front_page["checkout_price_deals"]["top_pick_window"] = checkout_window
            selected = rows if categories is None else rows[:len(categories)]
            if categories is not None:
                for d, category in zip(selected, categories):
                    d["category"] = category
            if promos is not None:
                for i, d in enumerate(selected):
                    d["codes"] = ["SAVE"] if i in promos else []
                    for sighting in d["sightings"]:
                        sighting["seen_at"] = NOW.isoformat()
            if with_spotlight:
                spot = rows[spotlight_index]
                spot["critic"] = dict(plausible=True, confidence=0.9)
                spot["codes"] = ["SAVE"] if spotlight_checkout else []
                spot["sightings"] = [dict(source_name="meh", source_url="https://meh.com/deals/today",
                                        seen_at=NOW.isoformat(), final_price=spot["final_price"])]
                if spotlight_price_only:
                    spot.update(pct_at_checkout=None, regular_price=None, abs_savings=None)
            result = site.render_site(db, cfg, NOW, out_dir=out, deals=selected if with_deals else [])
            return out, result
        yield publish
    finally:
        db.close()


@pytest.mark.parametrize("case,windows", [(name, 3) for name in
    ("cycle", "caps", "new", "stable", "refresh", "partial", "sliding-options", "unlabelled", "fuzz")] +
    [("off", 0), ("bounds", 1), ("bounds", 2), ("bounds", 4)])
def test_published_logic(rotation_site, case, windows):
    if not shutil.which("node"):
        pytest.skip("Node unavailable")
    out, _ = rotation_site(windows, with_deals=False)
    result = subprocess.run(["node", str(ROOT / "tests/drop_rotation_logic.cjs"),
                             str(out / "index.html"), case, str(windows)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_arrivals_survive_saved_startup_refresh_and_back(rotation_site):
    out, _ = rotation_site(with_deals=False)
    result = subprocess.run(["node", str(ROOT / "tests/browse_logic.cjs"),
                             str(out / "index.html"), str(out / "deals.json"), "rotation"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("strip_comments", [False, True])
def test_shipped_ordering_after_rotation(tmp_path, monkeypatch, strip_comments):
    cfg = pre_first_screen_mix(load_config())
    cfg.tracking = {}
    cfg.strip_comments = strip_comments
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    rows = [dict(key=str(i), item=f"Storage set {i}", store=f"store{i % 5}",
                 category=f"cat{i % 7}", status="alive", image_url="photo", final_price=20,
                 pct_at_checkout=80, first_seen_at=NOW.isoformat(), conditions=[],
                 pick=i + 1, pick_checkout=i % 5 == 0, allure=i == 24)
            for i in range(120)]
    rows += [dict(rows[1], key="used", pick=None, conditions=["used"]),
             dict(rows[1], key="open-box", pick=None, conditions=["open_box"]),
             dict(rows[1], key="tail-allure", pick=None, allure=True)]
    rows += [dict(rows[1], key=f"gone-{i}", pick=None, status="dead", gone=True) for i in range(3)]
    rows += [dict(rows[1], key=f"tail-{i}", pick=None) for i in range(8)]
    rows += [dict(rows[1], key=f"unknown-{i}", pick=None, click_price_unknown=True) for i in range(5)]
    source = tmp_path / "shipped-rows.json"
    source.write_text(json.dumps(rows))
    db = DB(":memory:")
    try:
        site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=[])
    finally:
        db.close()
    result = subprocess.run(["node", str(ROOT / "tests/browse_logic.cjs"),
                             str(tmp_path / "site/index.html"), str(tmp_path / "site/deals.json"),
                             "shipped-rotation", str(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("case,windows", [(case, 3) for case in (
    "spotlight-cycle", "spotlight-spacing", "spotlight-fallback", "spotlight-extra", "spotlight-extra-fallback",
    "spotlight-extra-first-promo", "spotlight-extra-other", "spotlight-extra-auto",
    "spotlight-views", "spotlight-single", "spotlight-new", "spotlight-new-plain", "spotlight-paginated",
    "spotlight-picked-first-promo")]
    + [("spotlight-off", 0)])
def test_spotlight_untouched_while_ordinary_picks_rotate(rotation_site, tmp_path, case, windows):
    cfg = pre_first_screen_mix(load_config())
    promos = set(range(4, 120, 5)) if case.endswith("fallback") else {24, 49}
    if case in {"spotlight-extra-first-promo", "spotlight-picked-first-promo"}:
        promos = set(range(0, 120, 5))
    if case == "spotlight-single":
        promos = {0, 19}
    if case == "spotlight-new-plain":
        promos = {49}
    rows = [dict(key=str(i), dedupe_key=str(i), item=f"Rotation product {i}",
                 store=f"store{i % 5}", brand=f"brand{i // 3}", category=f"cat{i % 7}",
                 image_url="photo", final_price=20, codes=["SAVE"] if i in promos else [],
                 sightings=[dict(seen_at=NOW.isoformat(), final_price=20)], pick=i + 1,
                 pick_checkout=i in promos)
            for i in range(24 if case == "spotlight-single" else 120)]
    assert top_picks([(d, 120 - i) for i, d in enumerate(rows)], cfg.front_page, NOW, cfg=cfg) == rows
    source = tmp_path / "server-picks.json"
    source.write_text(json.dumps(rows))
    out, _ = rotation_site(0, with_deals=False)
    baseline = tmp_path / "rotation-off.html"
    baseline.write_bytes((out / "index.html").read_bytes())
    out, _ = rotation_site(windows, with_deals=False)
    result = subprocess.run(["node", str(ROOT / "tests/browse_logic.cjs"),
                             str(out / "index.html"), str(out / "deals.json"), case, str(source), str(baseline)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_standalone_checkout_spotlight_keeps_original_slot(rotation_site, monkeypatch):
    monkeypatch.setattr(site, "pick_score", lambda d, *a, **kw: 1000 - int(d["dedupe_key"].split("-")[-1]))
    out, _ = rotation_site(categories=[f"cat{i % 7}" for i in range(25)],
                           promos=set(range(0, 24, 5)), with_spotlight=True, spotlight_price_only=True)
    doc = json.loads((out / "deals.json").read_text())
    picks = sorted(doc["deals"], key=lambda d: d["pick"])
    assert len(picks) == 24 and len(doc["spotlight"]) == 1
    assert [i for i, d in enumerate(picks) if d.get("pick_checkout")] == [0, 5, 10, 15, 20]
    assert "pick_checkout" not in doc["spotlight"][0]
    assert doc["spotlight"][0]["key"] not in {d["key"] for d in picks}
    result = subprocess.run(["node", str(ROOT / "tests/browse_logic.cjs"),
                             str(out / "index.html"), str(out / "deals.json"), "spotlight-unplaced"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_server_prepares_diverse_rotating_windows(rotation_site, tmp_path, monkeypatch):
    categories = [f"cat{i % 6}" for i in range(24)] + ["home"] * 24 + ["beauty"] * 24
    accepted = []
    def capture(scored, *args, **kwargs):
        picked = top_picks(scored, *args, **kwargs)
        assert [d["category"] for d in picked] == categories
        accepted.extend(picked)
        return picked
    monkeypatch.setattr(site, "top_picks", capture)
    monkeypatch.setattr(site, "pick_score", lambda d, *a, **kw: 1000 - int(d["dedupe_key"].split("-")[-1]))
    out, result = rotation_site(categories=categories)
    picked = sorted((d for d in result["public_deals"] if d.get("pick")), key=lambda d: d["pick"])
    assert len(picked) == len(accepted) == 72
    assert {d["dedupe_key"] for d in picked} == {d["dedupe_key"] for d in accepted}
    source = tmp_path / "server-picks.json"
    source.write_text(json.dumps([dict(d, key=str(i)) for i, d in enumerate(picked)]))
    run = subprocess.run(["node", str(ROOT / "tests/drop_rotation_logic.cjs"),
                          str(out / "index.html"), "category-windows", str(source)],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize("varied", [False, True])
def test_window_preparation_retains_inventory_checkout_positions_boosts_and_tail(varied):
    cfg = pre_first_screen_mix(load_config())
    categories = [f"cat{i % 6}" for i in range(24)] + ["home"] * 24 + ["beauty"] * 24
    rows = [dict(offer(i, codes=["SAVE"] if i % 5 == 0 else [], stack_steps=[]),
                 category=categories[i] if i < 72 else "electronics",
                 store=f"store{i if varied else i % 5}", brand=f"brand{i}",
                 final_price=20 + i / 100 if varied else 20)
            for i in range(96)]
    boosted = {d["dedupe_key"] for i, d in enumerate(rows) if i % 24 < 6}
    before = json.dumps(rows, sort_keys=True)
    prepared = drop_rotation.prepare_picks(rows, cfg, boosted)
    assert json.dumps(rows, sort_keys=True) == before
    assert prepared[72:] == rows[72:]
    assert Counter(d["dedupe_key"] for d in prepared) == Counter(d["dedupe_key"] for d in rows)
    assert [bool(site.checkout_prices.kind(d)) for d in prepared] == [bool(site.checkout_prices.kind(d)) for d in rows]
    for i in range(0, 72, 24):
        page = prepared[i:i + 24]
        assert sum(d["dedupe_key"] in boosted for d in page) == 6
        assert max(Counter(d["category"] for d in page).values()) <= 22
        if not varied:
            assert Counter((d["store"], d["final_price"]) for d in page) == Counter(
                (d["store"], d["final_price"]) for d in rows[i:i + 24])
    assert drop_rotation.prepare_picks(rows, cfg, boosted) == prepared
    cfg.drop_rotation_windows = 0
    assert drop_rotation.prepare_picks(rows, cfg, boosted) is rows
    cfg.drop_rotation_windows = 3
    cfg.front_page["checkout_price_deals"]["enabled"] = False
    assert drop_rotation.prepare_picks(rows, cfg, boosted) is rows


@pytest.mark.parametrize("width", [3, 5])
def test_server_selects_and_spaces_ordinary_picks_without_spotlight(rotation_site, monkeypatch, width):
    candidates = []
    def capture(scored, *args, **kwargs):
        candidates.append({d["dedupe_key"] for d, _ in scored})
        return top_picks(scored, *args, **kwargs)
    monkeypatch.setattr(site, "top_picks", capture)
    options = dict(with_spotlight=True, spotlight_checkout=False, spotlight_index=1,
                   promos=set(range(0, 120, width)), checkout_window=width)
    out, _ = rotation_site(**options)
    doc = json.loads((out / "deals.json").read_text())
    spot, = doc["spotlight"]
    assert spot["dedupe_key"] == "task269-1"
    assert spot["dedupe_key"] not in candidates[-1]  # Exclude before selection, not after spacing.
    picked = sorted((d for d in doc["deals"] if d.get("pick")), key=lambda d: d["pick"])
    assert len(picked) >= 72
    assert all(d["key"] != spot["key"] for d in picked)
    assert any(d["key"] == spot["key"] for d in doc["deals"])  # Keep ordinary inventory/expiry behavior.
    for i in range(len(picked)):
        assert sum(bool(d.get("pick_checkout")) for d in picked[i:i + width]) <= 1
    out, _ = rotation_site(0, **options)
    assert spot["dedupe_key"] in candidates[-1]
    assert json.loads((out / "deals.json").read_text())["spotlight"] == [spot]


@pytest.mark.parametrize("price_only", [False, True])
def test_spotlight_payload_matches_off_render(rotation_site, price_only):
    out, _ = rotation_site(with_spotlight=True, spotlight_price_only=price_only)
    doc = json.loads((out / "deals.json").read_text())
    spot, = doc["spotlight"]
    assert "pick_checkout" not in spot and "checkout_price" not in spot
    assert any(d["key"] == spot["key"] for d in doc["deals"]) is not price_only
    out, _ = rotation_site(0, with_spotlight=True, spotlight_price_only=price_only)
    doc = json.loads((out / "deals.json").read_text())
    assert doc["spotlight"] == [spot]
    assert all("pick_checkout" not in d for d in doc["deals"] + doc["spotlight"])


def test_category_backtracking_keeps_server_accepted_rotation(rotation_site, tmp_path):
    cfg = pre_first_screen_mix(load_config())
    rows = [dict(key=str(i), dedupe_key=str(i), item=f"Rotation product {i}",
                 store=f"store{i % 5}", brand=f"brand{i // 3}",
                 category="kitchen" if 24 <= i < 26 else "home" if 26 <= i < 48 else f"cat{i % 7}",
                 image_url="photo", final_price=20)
            for i in range(120)]
    picked = top_picks([(d, 120 - i) for i, d in enumerate(rows)], cfg.front_page, NOW, cfg=cfg)
    assert picked == rows  # The adversarial window is valid server output.
    for i, d in enumerate(picked, 1):
        d["pick"] = i
    source = tmp_path / "server-picks.json"
    source.write_text(json.dumps(picked))
    out, _ = rotation_site(with_deals=False)
    result = subprocess.run(["node", str(ROOT / "tests/drop_rotation_logic.cjs"),
                             str(out / "index.html"), "category-backtrack", str(source)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("case", ["promo-pruning", "category-budget"])
def test_impossible_promo_prefixes_prune_category_search(rotation_site, tmp_path, case):
    cfg = pre_first_screen_mix(load_config())
    rng = random.Random(269)
    promos = set(range(0, 71, 5)) | set(range(76, 117, 5))
    rows = [dict(key=str(i), dedupe_key=str(i), item=f"Rotation product {i}",
                 store=f"store{i % 5}", brand=f"brand{i // 3}",
                 category=f"cat{i % 10 if i < 8 else rng.randrange(10)}",
                 image_url="photo", final_price=20, codes=["SAVE"] if i in promos else [],
                 sightings=[dict(seen_at=NOW.isoformat(), final_price=20)], pick=i + 1,
                 pick_checkout=i in promos)
            for i in range(120)]
    picked = top_picks([(d, 120 - i) for i, d in enumerate(rows)], cfg.front_page, NOW, cfg=cfg)
    assert picked == rows  # Ten categories, with server-accepted promo counts 5/5/5/4/5.
    assert [sum(d["pick_checkout"] for d in rows[i:i + 24]) for i in range(0, 120, 24)] == [5, 5, 5, 4, 5]
    source = tmp_path / "server-picks.json"
    source.write_text(json.dumps(picked))
    out, _ = rotation_site(with_deals=False)
    result = subprocess.run(["node", str(ROOT / "tests/drop_rotation_logic.cjs"),
                             str(out / "index.html"), case, str(source)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout.strip())


def test_enabled_shipped_phone_budget(tmp_path):
    from scripts.publication_budget import analyze
    from tests.phone_budget_fixture import publish_fixture, reference_sizes

    cfg = pre_first_screen_mix(load_config())  # All shipped features ON; historical fixtures stay frozen.
    cfg.max_photoless_h = load_config().max_photoless_h  # task 283's cutoff is a shipped feature too
    assert cfg.drop_rotation_windows == 3 and drop_rotation.enabled(cfg)
    cfg.phone_budget["enabled"] = True
    publish_fixture(tmp_path, cfg)
    report = analyze(tmp_path / "site", cfg, expected_offers=1393)  # task 283: seven older photo-less offers
    reference = reference_sizes(tmp_path / "site")
    for metric, values in report["metrics"].items():
        assert values["gzip"] == reference[metric]["gzip"]
        assert values["status"] == "pass", (metric, values)
    assert report["ok"]
    print("task269 enabled gzip bytes: " + json.dumps({
        metric: values["gzip"] for metric, values in report["metrics"].items()}, sort_keys=True))


def test_exact_server_promo_flags_with_and_without_paid_label(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = pre_first_screen_mix(load_config())
    enable(cfg)
    rows = [offer(i) if i % 5 == 0 else offer(i, codes=[], stack_steps=[]) for i in range(12)]
    for d in rows:
        d["click_price_check"] = dict(asin=d["asin"], final=d["final_price"],
                                     buybox=d["final_price"], at=NOW.isoformat())
    remember(rows[0], cfg)
    rows[5].update(codes=[], stack_steps=["Clip the coupon"])
    monkeypatch.setattr(site, "pick_score", lambda d, *a, **kw: 120 - int(d["dedupe_key"].split("-")[-1]))
    db = DB(":memory:")
    try:
        result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
        picked = [d for d in result["public_deals"] if d.get("pick")]
        assert len(picked) == 12
        assert {d["dedupe_key"] for d in picked if d.get("pick_checkout")} == {"offer-0", "offer-5", "offer-10"}
        assert next(d for d in picked if d["dedupe_key"] == "offer-0")["checkout_price"]
        assert "checkout_price" not in next(d for d in picked if d["dedupe_key"] == "offer-5")
        cfg.drop_rotation_windows = 0
        result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
        assert all("pick_checkout" not in d for d in result["public_deals"] + result["full_payload"]["deals"])
    finally:
        db.close()


def test_off_template_is_exact_and_checkout_disabled_unchanged():
    home = (site.TEMPLATES_DIR / "index.html").read_text()
    cfg = pre_first_screen_mix(load_config())
    cfg.drop_rotation_windows = 0
    assert drop_rotation.template(home, cfg) == home
    assert drop_rotation.final_order(home, cfg) == home
    cfg.drop_rotation_windows = 3
    cfg.front_page["checkout_price_deals"]["enabled"] = False
    assert drop_rotation.template(home, cfg) == home
    assert drop_rotation.final_order(home, cfg) == home


def test_rotation_leaves_card_and_spotlight_renderer_bytes_unchanged():
    home = (site.TEMPLATES_DIR / "index.html").read_text()
    changed = drop_rotation.template(home, load_config())
    start = "  function storeHue("
    actions = (site.TEMPLATES_DIR / "deal-actions.js").read_text().replace(
        'scope.querySelectorAll(".grid")', 'scope.querySelectorAll(".grid:not(.spotlight-grid)")')
    assert changed[changed.index(start):] == home[home.index(start):].replace("{{DEAL_ACTIONS_SCRIPT}}", actions)


def test_toggle_restores_every_byte_and_reruns(rotation_site):
    def capture(windows):
        out, result = rotation_site(windows)
        return {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}, result["full_payload"]
    before, payload = capture(0)
    enabled, enabled_payload = capture(3)
    assert before != enabled
    assert enabled_payload["meta"]["site_version"] != payload["meta"]["site_version"]
    assert capture(3) == (enabled, enabled_payload)
    assert capture(0) == (before, payload)


@pytest.mark.parametrize("view", [
    {"state": {"catTab": 1}}, {"state": {"catTab": 3}},
    {"state": {"sortKey": "fresh"}}, {"state": {"sortKey": "pct"}},
    {"state": {"sortKey": "price"}}, {"state": {"sortDir": 1}},
    {"controls": {"q": "storage"}}, {"controls": {"storesel": "Walmart"}},
    {"controls": {"minpct": "80"}},
])
def test_other_views_keep_their_order(tmp_path, view):
    cfg = pre_first_screen_mix(load_config())
    cfg.support_enabled = False
    cfg.engagement = {}
    rows = [dict(key=str(i), pick=i+1, category="home", store="Walmart",
                 ps=i, pct_at_checkout=80, image_url="photo", first_seen_at=NOW.isoformat())
            for i in range(96)]
    case = dict(rows=rows, options=cfg.front_page, **view)
    case["state"] = {"seed": 1, "sortKey": "top", "newKeys": {"31": True}, **view.get("state", {})}
    db = DB(":memory:")
    try:
        cfg.drop_rotation_windows = 0
        before = run_home_logic(tmp_path, cfg, db, [case])[0]
        cfg.drop_rotation_windows = 3
        after = run_home_logic(tmp_path, cfg, db, [case])[0]
        assert after == before
    finally:
        db.close()


def test_phone_drop_browser(rotation_site):
    if not shutil.which("node"):
        pytest.skip("Node unavailable")
    out, result = rotation_site(with_spotlight=True)
    assert sum(bool(d.get("pick")) for d in result["public_deals"]) >= 72
    assert len(json.loads((out / "deals.json").read_text())["spotlight"]) == 1
    run = subprocess.run(["node", str(ROOT / "tests/drop_rotation_browser.cjs"), str(out)],
                         capture_output=True, text=True, timeout=90)
    if run.returncode == 77:
        pytest.skip(run.stdout.strip())
    assert run.returncode == 0, run.stdout + run.stderr
    print(run.stdout)


def test_spotlight_dom_contains_only_published_spotlights_across_drops(rotation_site):
    from dealradar.publish.seo_audit import Page

    out, _ = rotation_site(with_spotlight=True)
    doc = json.loads((out / "deals.json").read_text())
    run = subprocess.run(["node", str(ROOT / "tests/browse_logic.cjs"),
                          str(out / "index.html"), str(out / "deals.json"), "spotlight-dom"],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    snapshots = json.loads(run.stdout)
    expected = [d["key"] for d in doc["spotlight"]]
    firsts = []
    for markup in snapshots:
        parser = Page(markup, "/")
        grids = [n for n in parser.root.nodes() if n.has("spotlight-grid")]
        assert len(grids) == 1
        assert [n.attrs["data-key"] for n in grids[0].nodes() if n.has("card")] == expected
        firsts.append(next(d.attrs["data-key"] for d in parser.cards if d.attrs["data-key"] not in expected))
    assert len(set(firsts[:3])) == 3
    assert firsts[3] == firsts[0]


@pytest.mark.parametrize("columns", [1, 2, 3, 5])
def test_row_filling_preserves_spotlight_and_ordinary_inventory_across_drops(rotation_site, columns):
    out, _ = rotation_site(with_spotlight=True)
    run = subprocess.run(["node", str(ROOT / "tests/browse_logic.cjs"),
                          str(out / "index.html"), str(out / "deals.json"), "spotlight-grid-fill", str(columns)],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize("case", ["rotation-sparse-promos", "rotation-repair-windows"])
def test_final_repair_respects_sparse_inventory_and_window_membership(rotation_site, case):
    out, _ = rotation_site(with_deals=False)
    result = subprocess.run(["node", str(ROOT / "tests/browse_logic.cjs"),
                             str(out / "index.html"), str(out / "deals.json"), case],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
