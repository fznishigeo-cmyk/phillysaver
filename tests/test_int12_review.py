"""Round 3 findings: exact shopper prices and public reference boundaries."""

from copy import deepcopy
from datetime import timedelta
import json
import re
import socket
import subprocess

import pytest

from dealradar import click_price, dedupe, rank, savings, util, verify
from dealradar.config import load_config
from dealradar.fetch.keepa import KeepaDealsAdapter
from dealradar.parse.extractor import verify_evidence
from dealradar.parse.schema import LLMDeal, to_extracted
from dealradar.publish import site
from tests.test_amazon_reference_price import offer, settings
from tests.test_buybox_top_picks import transport, run
from tests.test_click_price_known import adidas, record, cfg, db
from tests.test_price_accuracy_engine import NOW
from tests.test_front_page import daily_deal
from tests.test_free_confirmations import row as feed_row
from tests.test_keepa import SRC
from tests.test_static_photos import rows
from tests.test_wall_confirmation_coverage import remember


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("int12 network unavailable")
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(socket.socket, "connect", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


@pytest.mark.parametrize("averages", ["present", "missing", "partial", "invalid"])
@pytest.mark.parametrize("total", [5.00, 4.56, 4.54])
def test_paid_buybox_reprices_exactly_without_optional_averages(
        db, cfg, monkeypatch, tmp_path, averages, total):
    row = adidas(db, cfg)
    remember(db, top=[row], wall=[row])
    product = record(row, total)
    if averages in {"missing", "partial"}:
        del product["stats"]["avg30"]
    if averages == "missing":
        del product["stats"]["avg90"]
    if averages == "invalid":
        product["stats"].update(avg30=[-1], avg90=[0])
    calls, _, _ = transport(monkeypatch, db, {row["asin"]: product})
    posts, original_reprice = [], dedupe.reprice_existing

    def correction(*args, **kwargs):
        posts.append(args[2])
        return original_reprice(*args, **kwargs)

    monkeypatch.setattr(dedupe, "reprice_existing", correction)
    result = run(db, cfg)
    stored = db.get_deal(row["dedupe_key"])
    assert stored["final_price"] == total
    assert stored["status"] == "alive" and not stored.get("gone_suspect_at")
    assert click_price.status(stored, cfg, NOW) == "known_ok"
    assert stored["critic"] == row["critic"] and stored["sightings"] == row["sightings"]
    assert result["buybox_top"]["repriced_up" if total > 4.55 else "repriced_down"] == 1
    post = posts[0]
    extracted = to_extracted(LLMDeal.model_validate(post.structured), post, "UTC", NOW)
    assert verify_evidence(extracted, post.text)[0]
    assert extracted.evidence.final_price_quote == f"Buy Box total including shipping: ${total:.2f} at Amazon."
    computed = savings.compute_savings(extracted, allow_price_only=True)
    assert stored["pct_at_checkout"] == computed.pct_at_checkout
    assert stored["abs_savings"] == computed.abs_savings
    if averages != "present":
        assert stored["regular_price"] is stored["pct_at_checkout"] is stored["abs_savings"] is None
    publication = site.render_site(db, cfg, NOW, out_dir=tmp_path)
    for card in publication["public_deals"] + publication["full_payload"].get("spotlight", []):
        assert card["final_price"] == total
    snapshot = deepcopy(db.all_deals())
    run(db, cfg)
    assert len(calls) == 1 and db.all_deals() == snapshot


@pytest.mark.parametrize("total", [5.00, 4.56, 4.54])
def test_unapplied_buybox_difference_is_never_a_confirmed_home_price(db, cfg, tmp_path, total):
    row = adidas(db, cfg)
    click_price.remember(row, record(row, total), NOW, NOW)
    db.update_deal(row["dedupe_key"], payload=row)
    assert click_price.has_gap(row, NOW)
    assert click_price.excluded(row, cfg, NOW)
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path)
    assert not result["public_deals"] and not result["full_payload"].get("spotlight")
    assert "D4T Woven" not in (tmp_path / "index.html").read_text()


def test_rejected_buybox_price_evidence_keeps_the_mismatch_off_home(db, cfg, monkeypatch, tmp_path):
    row = adidas(db, cfg)
    remember(db, top=[row], wall=[row])
    transport(monkeypatch, db, {row["asin"]: record(row, 4.56)})
    monkeypatch.setattr("dealradar.parse.extractor.verify_evidence", lambda *a: (False, ["unbacked_price"]))
    run(db, cfg)
    stored = db.get_deal(row["dedupe_key"])
    assert stored["final_price"] == 4.55 and click_price.has_gap(stored, NOW)
    assert not site.render_site(db, cfg, NOW, out_dir=tmp_path)["public_deals"]


@pytest.mark.parametrize("mode", ["history", "list_price"])
@pytest.mark.parametrize("single", [False, True])
def test_paid_promo_rechecks_do_not_log_hidden_reference(db, cfg, monkeypatch, caplog, mode, single):
    cfg.amazon_reference = mode
    row = adidas(db, cfg, conditions=["subscribe_save"])
    post = KeepaDealsAdapter._map_row(feed_row(asin=row["asin"], cents=455), SRC, NOW)
    extracted = to_extracted(LLMDeal.model_validate(post.structured), post, "UTC", NOW)
    assert verify_evidence(extracted, post.text)[0]
    merged = dedupe.upsert_extracted(db, extracted, savings.compute_savings(extracted), post, cfg, NOW)
    assert merged["dedupe_key"] == row["dedupe_key"]
    row = db.get_deal(row["dedupe_key"])
    assert row["regular_price"] == 100 and "average" in row["regular_price_basis"]
    assert "subscribe_save" in row["conditions"]
    product = record(row)
    product["stats"]["current"][0] = 8000
    product["stats"]["current"][1] = -1
    transport(monkeypatch, db, {row["asin"]: product})
    with caplog.at_level("INFO", logger="dealradar.verify"):
        verdict = (verify.recheck_amazon_price(row, cfg) if single else
                   verify.recheck_amazon_prices([row], cfg)[row["dedupe_key"]])
    assert verdict is True  # Redaction cannot change the private promo target.
    assert "price recheck" in caplog.text
    assert ("$100.00" in caplog.text) is (mode == "history")
    if mode == "list_price":
        assert all(100 not in event.args for event in caplog.records)
        assert "regular" not in caplog.text and "average" not in caplog.text


@pytest.mark.parametrize("mode", ["history", "list_price"])
def test_public_score_magnitude_is_private_but_score_order_and_ties_survive(db, tmp_path, mode):
    cfg = settings()
    cfg.amazon_reference = mode
    source = [offer(None, dedupe_key=f"ordinal-{i}", regular_price=regular, price_rechecked_at=util.iso(NOW),
                    pct_at_checkout=(regular - 10) / regular * 100, abs_savings=regular - 10)
              for i, regular in enumerate([50, 100, 100])]
    source.append(offer(None, dedupe_key="unscored", flags=["range"], price_rechecked_at=util.iso(NOW)))
    private = [round(value, 1) if (value := rank.pick_score(
        d, cfg, NOW, require_confirmation=False, popularity=False)) is not None else None for d in source]
    before = deepcopy(source)
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=source)
    public = result["full_payload"]["deals"]
    scores = {d["dedupe_key"]: d.get("ps") for d in public}
    expected = private if mode == "history" else [1, 2, 2, None]
    assert [scores[d["dedupe_key"]] for d in source] == expected
    assert source == before and private[0] < private[1] == private[2]
    assert {d["dedupe_key"]: d.get("ps") for d in result["public_deals"]} == scores
    assert "ps" not in next(d for d in public if d["dedupe_key"] == "unscored")
    (tmp_path / "private-scores.json").write_text(json.dumps({d["dedupe_key"]: v for d, v in zip(source, private)}))
    proc = subprocess.run(["node", "tests/int12_review_logic.cjs", str(tmp_path), "scores"],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("strip", [False, True])
def test_nonempty_open_box_inventory_stays_out_of_every_home_surface(db, tmp_path, enabled, strip):
    cfg = load_config()
    cfg.front_page["used_tab_only"], cfg.strip_comments = enabled, strip
    source = rows(3)
    source[0].update(item="Fixture open box", conditions=["open_box"],
                     image_url="https://open-box-images.invalid/item.jpg")
    source[1].update(item="Fixture used", conditions=["used"],
                     image_url="https://open-box-images.invalid/item2.jpg")
    source[2].update(item="Fixture new", conditions=["new"],
                     image_url="https://new-images.invalid/item.jpg")
    for d in source:
        d.update(price_rechecked_at=util.iso(NOW), first_seen_at=util.iso(NOW - timedelta(hours=1)),
                 critic={"plausible": True, "confidence": .9, "reason": "Backed basis"})
    cfg.front_page["spotlight"] = {"sources": ["meh"], "max_cards": 2}
    source.append(daily_deal(item="Fixture used spotlight", conditions=["used"],
                            first_seen_at=util.iso(NOW - timedelta(hours=1)),
                            price_rechecked_at=util.iso(NOW),
                            critic={"plausible": True, "confidence": .9, "reason": "Backed basis"},
                            image_url="https://open-box-images.invalid/spotlight.jpg"))
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=source)
    assert len(result["public_deals"]) == 4
    assert result["full_payload"]["spotlight"]
    home = (tmp_path / "index.html").read_text()
    static = home.split('<div id="content"')[1].split('<div id="paywall"')[0]
    assert ("Fixture open box" in static) is (not enabled)
    assert ("Fixture used" in static) is (not enabled)
    assert ("Fixture used spotlight" in static) is (not enabled)
    assert "Fixture new" in static
    assert ('rel="preconnect" href="https://open-box-images.invalid"' in home) is (not enabled)
    for match in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', home, re.S):
        doc = json.loads(match[1])
        if doc.get("@type") == "ItemList" and enabled:
            assert all(d["deal_path"] not in match[1] for d in result["public_deals"] if "open_box" in d["conditions"] or "used" in d["conditions"])
    proc = subprocess.run(["node", "tests/int12_review_logic.cjs", str(tmp_path), "ticker", str(int(enabled))],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
