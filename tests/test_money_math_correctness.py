"""Offline regressions for task 054 money and correctness contracts."""

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from dealradar import dedupe, pipeline, rank, verify
from dealradar.config import SECRET_NAMES, load_config, load_sources
from dealradar.db import DB
from dealradar.errors import SpendCapReached
from dealradar.models import Evidence, ExtractedDeal, RawPost
from dealradar.parse.claude_client import ClaudeParser
from dealradar.parse.extractor import verify_evidence
from dealradar.publish import email_digest, members, meta, newsletter, pinterest
from dealradar.publish import seo, site, social, telegram_pub
from dealradar.savings import compute_savings
from dealradar.util import iso
from tests.test_pinterest import candidate, published, seed_auth
from tests.test_publish_site import make_deal

AUDIT_NOW = datetime(2026, 10, 13, 14, tzinfo=timezone.utc)


@pytest.fixture
def audit_cfg(tmp_path, monkeypatch):
    for name in SECRET_NAMES + ["STATE_DB_KEY", "SOVRN_API_KEY", "SOVRN_API_KEY2",
                                "SOVRN_SECRET_KEY", "GITHUB_STEP_SUMMARY"]:
        monkeypatch.delenv(name, raising=False)
    cfg = load_config()
    cfg.site_dir = str(tmp_path / "site")
    cfg.critic_shadow_batches_per_day = 0
    cfg.feature_flags["keepa"] = False
    # Fixture deals carry no sightings; the stock rule is tests/test_social_stock.py.
    cfg.social_require_stock_evidence = False
    return cfg


@pytest.fixture
def audit_db(tmp_path):
    db = DB(tmp_path / "audit-state.db")
    yield db
    db.close()


def audit_post(source="hip2save", item="Acme widget", final=30.0, regular=100.0):
    primary, backup = load_sources()
    src = next(s for s in primary + backup if s.name == source)
    url = src.site.rstrip("/") + "/audit-widget/"
    text = f"{item}: regular ${regular:.2f}, now ${final:.2f}."
    return RawPost(source, f"{source}-{item}-{final}-{regular}", url, item,
                   text, AUDIT_NOW, AUDIT_NOW)


def audit_seed(db, cfg, *, item="Acme widget", url="https://walmart.com/ip/12345",
               final=30.0, regular=100.0, source="hip2save", conditions=(),
               image=""):
    post = audit_post(source, item, final, regular)
    extracted = ExtractedDeal(
        item=item, store="Amazon" if "amazon.com" in url else "Walmart",
        source_name=source, source_url=post.url, product_url=url,
        link_as_posted=url, online_only="true", regular_price=regular,
        final_price=final, category="home", conditions=list(conditions),
        evidence=Evidence(regular_price_quote=f"regular ${regular:.2f}",
                          final_price_quote=f"now ${final:.2f}"))
    assert verify_evidence(extracted, post.text)[0]
    result = dedupe.upsert_extracted(db, extracted, compute_savings(extracted),
                                     post, cfg, AUDIT_NOW)
    if image:
        row = db.get_deal(result["dedupe_key"])
        db.update_deal(result["dedupe_key"], payload={**row, "image_url": image})
    return db.get_deal(result["dedupe_key"])


@pytest.mark.parametrize("unsupported", [
    {"stack_steps": ["Use code FAKE for 90% off"]}, {"deal_type": "penny"}])
def test_invented_stack_cannot_make_a_publishable_price(audit_cfg, unsupported):
    d = ExtractedDeal(item="Widget", store="Walmart", source_name="hip2save",
        source_url="https://hip2save.com/widget/", online_only="true",
        regular_price=100.0, evidence=Evidence(regular_price_quote="regular $100"),
        **unsupported)
    ok, _ = verify_evidence(d, "Widget regular $100.")
    savings = compute_savings(d)
    publishable, _ = rank.is_publishable({
        **dataclasses.asdict(d), **dataclasses.asdict(savings), "status": "alive"}, audit_cfg)
    assert not (ok and publishable)


@pytest.mark.parametrize("reason", ["no_parser", "api_unavailable", "cap_exhausted"])
def test_unjudged_keepa_deal_waits_to_publish(audit_db, audit_cfg, reason):
    row = audit_seed(audit_db, audit_cfg, source="keepa_amazon_drops",
                     url="https://www.amazon.com/dp/B012345678")
    assert row.get("critic") is None
    parser = None if reason == "no_parser" else ClaudeParser(audit_cfg,
        client=SimpleNamespace(messages=SimpleNamespace()),
        run_cap_usd=0.0 if reason == "cap_exhausted" else 0.5)
    pipeline._run_critic(audit_db, audit_cfg, parser, AUDIT_NOW)
    assert not rank.published_deals(audit_db, audit_cfg, AUDIT_NOW)


def test_different_variant_does_not_reprice_existing_link(audit_db, audit_cfg):
    first = audit_seed(audit_db, audit_cfg,
        item="Samsung Crystal UHD 55 inch Television", url="https://walmart.com/ip/55",
        final=300.0, regular=800.0)
    audit_seed(audit_db, audit_cfg,
        item="Samsung Crystal UHD 65 inch Television", url="https://walmart.com/ip/65",
        final=200.0, regular=800.0)
    old = audit_db.get_deal(first["dedupe_key"])
    assert old["final_price"] == 300.0
    assert len(audit_db.all_deals()) == 2


def test_higher_current_price_does_not_publish_historical_minimum(audit_db, audit_cfg):
    old = audit_seed(audit_db, audit_cfg, final=30.0)
    audit_seed(audit_db, audit_cfg, final=45.0)
    rows = rank.published_deals(audit_db, audit_cfg, AUDIT_NOW)
    assert not any(r["dedupe_key"] == old["dedupe_key"] and r["final_price"] == 30.0
                   for r in rows)


def test_zero_extraction_end_update_hides_existing_deal(audit_db, audit_cfg, monkeypatch):
    old = audit_seed(audit_db, audit_cfg)
    update = dataclasses.replace(audit_post(), title="SOLD OUT — Acme widget",
                                  text="This Acme widget deal is sold out.")
    audit_cfg.critic_enabled = False
    monkeypatch.setattr(pipeline, "load_config", lambda _: audit_cfg)
    monkeypatch.setattr(pipeline, "_fetch_all", lambda *a, **kw: ([update], {}))
    monkeypatch.setattr(pipeline, "_parse_new_posts", lambda *a, **kw: ([], {
        "parser": "fixture", "processed_hashes": [update.content_hash],
        "deals_extracted": 0, "spend_usd": 0.0}))
    monkeypatch.setattr(pipeline, "_build_parser", lambda *a, **kw: None)
    pipeline.run(db_path=audit_db.path, sources_override=[], skip_validation=True,
                 now=AUDIT_NOW, dry_run_telegram=True)
    assert audit_db.get_deal(old["dedupe_key"])["status"] == "dead"


@pytest.mark.parametrize("second_source,second_price", [
    ("hip2save_amazon", 30.0), ("dealmoon_us", 50.0)])
def test_corroboration_requires_independent_matching_price(
        audit_db, audit_cfg, second_source, second_price):
    row = audit_seed(audit_db, audit_cfg, final=30.0)
    audit_seed(audit_db, audit_cfg, final=second_price, source=second_source)
    row = audit_db.get_deal(row["dedupe_key"])
    assert verify.assign_tier(audit_db, row, audit_cfg, AUDIT_NOW) != "corroborated"


def test_call_cannot_exceed_remaining_allowance(audit_cfg):
    response = SimpleNamespace(stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        content=[SimpleNamespace(type="text", text='{"deals": []}')])
    parser = ClaudeParser(audit_cfg,
        client=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response)),
        run_cap_usd=0.0001)
    try:
        parser.extract("No deal", {"source_name": "fixture"})
    except SpendCapReached:
        pass
    assert parser.spend_usd <= parser.run_cap_usd


def test_paid_failure_remains_in_daily_ledger(audit_db, audit_cfg, monkeypatch):
    monkeypatch.setattr(pipeline, "load_config", lambda _: audit_cfg)
    monkeypatch.setattr(pipeline, "_fetch_all", lambda *a, **kw: ([], {}))
    parser = SimpleNamespace(model="fixture", spend_usd=0.0, run_cap_usd=0.5,
                              spend_by_kind={}, extract_by_source={})
    monkeypatch.setattr(pipeline, "_build_parser", lambda *a, **kw: parser)
    def paid_then_failed(*a, **kw):
        parser.spend_usd = 0.2  # a response was billed before this error
        raise OSError("synthetic disk failure after a billed response")
    monkeypatch.setattr(pipeline, "_parse_new_posts", paid_then_failed)
    with pytest.raises(OSError):
        pipeline.run(db_path=audit_db.path, sources_override=[], skip_validation=True,
                     now=AUDIT_NOW)
    assert audit_db.spend_usd_for_day(AUDIT_NOW) >= 0.2


def test_midnight_paid_call_counts_against_current_day(audit_db, audit_cfg, monkeypatch):
    before = datetime(2026, 10, 13, 23, 59, tzinfo=timezone.utc)
    after = before + timedelta(minutes=2)
    monkeypatch.setattr("dealradar.util.now_utc", lambda: after)
    response = SimpleNamespace(stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        content=[SimpleNamespace(type="text", text='{"deals": []}')])
    parser = ClaudeParser(audit_cfg, client=SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kw: response)))
    key = pipeline._run_key(before, False)
    audit_db.start_run(key, "hourly", before)
    parser.extract("No deal", {"source_name": "fixture"})  # billed after midnight
    assert parser.spend_usd > 0
    audit_db.finish_run(key, now=after, items_fetched=0, posts_new=0,
                        deals_parsed=0, published=0, spend_usd=parser.spend_usd)
    assert audit_db.spend_usd_for_day(after) >= parser.spend_usd


def test_pinterest_rerun_does_not_select_another_batch(audit_db, audit_cfg, monkeypatch):
    monkeypatch.setenv("PINTEREST_APP_ID", "synthetic-app")
    monkeypatch.setenv("PINTEREST_APP_SECRET", "synthetic-credential")
    monkeypatch.setattr(pinterest.time, "sleep", lambda _: None)
    seed_auth(audit_db)
    published(audit_db, audit_cfg, [candidate(str(i), i + 1) for i in range(3)])
    requests = []
    def handle(req):
        if req.url.path.endswith("/boards"):
            return httpx.Response(200, json={"items": [{"id": "board",
                "name": audit_cfg.pinterest["board_name"]}]})
        requests.append(req)
        return httpx.Response(201, json={"id": f"pin-{len(requests)}"})
    transport = httpx.MockTransport(handle)
    pinterest.post(audit_db, audit_cfg, transport=transport, now=AUDIT_NOW)
    pinterest.post(audit_db, audit_cfg, transport=transport, now=AUDIT_NOW)
    assert len(requests) == audit_cfg.pinterest["pins_per_run"]


def test_accepted_telegram_send_is_not_replayed(audit_db, audit_cfg, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "synthetic-credential")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "synthetic-channel")
    audit_cfg.telegram_channel["min_age_minutes"] = 0
    published(audit_db, audit_cfg, [candidate("same-deal", 1)])
    deliveries = []
    def handle(req):
        deliveries.append(json.loads(req.content))  # remote accepted it
        if len(deliveries) == 1:
            raise httpx.ReadTimeout("synthetic lost response", request=req)
        return httpx.Response(200, json={"ok": True})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        try:
            telegram_pub.send_alerts(audit_db, audit_cfg, AUDIT_NOW, http_client=client)
        except httpx.ReadTimeout:
            pass
        telegram_pub.send_alerts(audit_db, audit_cfg, AUDIT_NOW, http_client=client)
    assert len(deliveries) == 1


def test_headline_and_card_round_percentage_consistently():
    d = make_deal(final_price=30.0, regular_price=80.0, abs_savings=50.0,
                  pct_at_checkout=62.5, codes=[])
    public = site._serialize_deal(d, AUDIT_NOW)
    assert f"{seo._pct(public['pct_at_checkout'])}% off" in public["headline"]


@pytest.mark.parametrize("condition", ["Used", "Open Box", "open-box", "Manufacturer Refurbished"])
@pytest.mark.parametrize("surface", ["pick", "social"])
def test_labeled_used_item_never_becomes_pick_or_social(
        audit_db, audit_cfg, condition, surface):
    from dealradar.parse.schema import LLMDeal, to_extracted
    item = f"Acme widget ({condition})"
    post = audit_post(item=item)
    post.image_url = "https://m.media-amazon.com/images/I/widget.jpg"
    model_deal = LLMDeal(item=item, store="Amazon", category="home",
        product_url="https://www.amazon.com/dp/B012345678", online_only="true",
        regular_price=100.0, final_price=30.0, conditions=[condition], evidence={
            "regular_price_quote": "regular $100.00", "final_price_quote": "now $30.00"})
    extracted = to_extracted(model_deal, post, audit_cfg.timezone, AUDIT_NOW)
    assert verify_evidence(extracted, post.text)[0]
    dedupe.upsert_extracted(audit_db, extracted, compute_savings(extracted),
                            post, audit_cfg, AUDIT_NOW)
    result = site.render_site(audit_db, audit_cfg, AUDIT_NOW)
    rows = result["full_payload"]["deals"]
    if surface == "pick":
        assert not any(d.get("pick") for d in rows)
    else:
        assert not social._candidates(audit_db, audit_cfg, {})


@pytest.mark.parametrize('facts', [
    {'shipping_cost_or_threshold': '$6 shipping'},
    {'rebates': ['$10 mail-in rebate']},
    {'stack_steps': ['Use code FAKE for 90% off']},
    {'deal_type': 'bogo', 'evidence': Evidence(regular_price_quote='regular $100',
                                            final_price_quote='now $30', deal_type_quote='BOGO')},
])
def test_all_mathematical_inputs_require_source_evidence(facts):
    inputs = dict(item='Widget', store='Walmart', source_name='hip2save', source_url='https://hip2save.com/x',
                  regular_price=100, final_price=30,
                  evidence=Evidence(regular_price_quote='regular $100', final_price_quote='now $30'))
    inputs.update(facts)
    assert not verify_evidence(ExtractedDeal(**inputs), 'Widget regular $100, now $30.')[0]


def test_verified_stack_shipping_rebates_and_type_still_compute():
    deal = ExtractedDeal(item='Widget', store='Walmart', source_name='hip2save', source_url='https://hip2save.com/x',
        regular_price=80, stack_steps=['sale $40', 'Use code SAVE25 for 25% off'],
        rebates=['$5 mail-in rebate'], shipping_cost_or_threshold='$6 shipping',
        evidence=Evidence(regular_price_quote='regular $80', shipping_quote='$6 shipping'))
    text = 'Widget regular $80, sale $40. Use code SAVE25 for 25% off. $6 shipping. $5 mail-in rebate.'
    assert verify_evidence(deal, text)[0]
    result = compute_savings(deal)
    assert result.final_price == 36 and result.pct_at_checkout == 55
    assert result.pct_with_rebates == 61.3
    bogo = dataclasses.replace(deal, stack_steps=[], rebates=[], shipping_cost_or_threshold='',
                              deal_type='bogo', evidence=Evidence(regular_price_quote='regular $80',
                                                                  deal_type_quote='Buy one get one free'))
    assert verify_evidence(bogo, 'regular $80. Buy one get one free.')[0]
    assert compute_savings(bogo).pct_at_checkout == 50


def test_shipping_evidence_must_support_the_same_amount():
    deal = ExtractedDeal(item='Widget', store='Walmart', source_name='hip2save', source_url='https://hip2save.com/x',
        regular_price=100, final_price=30, shipping_cost_or_threshold='$9.99',
        evidence=Evidence(regular_price_quote='regular $100', final_price_quote='now $30',
                          shipping_quote='$19.99 shipping'))
    assert not verify_evidence(deal, 'regular $100, now $30, $19.99 shipping')[0]


def test_higher_price_invalidates_critic_and_older_post_cannot_restore_low_price(audit_db, audit_cfg):
    row = audit_seed(audit_db, audit_cfg)
    audit_db.update_deal(row['dedupe_key'], payload={**row, 'critic': {'plausible': True, 'confidence': .9}})
    post = dataclasses.replace(audit_post(final=45), published_at=AUDIT_NOW + timedelta(hours=1))
    extracted = ExtractedDeal(item=post.title, store='Walmart', source_name=post.source_name,
        source_url=post.url, product_url=row['canonical_url'], regular_price=100, final_price=45, online_only='true')
    dedupe.upsert_extracted(audit_db, extracted, compute_savings(extracted), post, audit_cfg, post.published_at)
    audit_seed(audit_db, audit_cfg, source='dealmoon_us', final=20)
    current = audit_db.get_deal(row['dedupe_key'])
    assert current['final_price'] == 45 and current['critic'] is None


def test_missing_new_price_clears_historical_numbers(audit_db, audit_cfg):
    row = audit_seed(audit_db, audit_cfg)
    post = dataclasses.replace(audit_post(), text='80% off', published_at=AUDIT_NOW + timedelta(hours=1))
    extracted = ExtractedDeal(item=post.title, store='Walmart', source_name=post.source_name,
        source_url=post.url, product_url=row['canonical_url'], pct_off=80, online_only='true',
        evidence=Evidence(pct_quote='80% off'))
    dedupe.upsert_extracted(audit_db, extracted, compute_savings(extracted), post, audit_cfg, post.published_at)
    current = audit_db.get_deal(row['dedupe_key'])
    assert current['final_price'] is None and current['abs_savings'] is None
    assert not rank.published_deals(audit_db, audit_cfg, post.published_at)


def test_existing_condition_spellings_are_repaired_idempotently(audit_db, audit_cfg):
    row = audit_seed(audit_db, audit_cfg)
    audit_db.update_deal(row['dedupe_key'], payload={**row, 'conditions': [' Open—Box ', 'USED - Like New']})
    with_db = DB(audit_db.path)
    current = with_db.get_deal(row['dedupe_key'])
    assert current['conditions'] == ['open_box', 'used']
    assert verify.is_used_deal(current)
    saved = with_db.conn.execute('SELECT payload FROM deals').fetchone()[0]
    with_db.close()
    again = DB(audit_db.path)
    assert again.conn.execute('SELECT payload FROM deals').fetchone()[0] == saved
    again.close()


def test_spend_is_reserved_for_concurrent_calls_before_either_returns(audit_cfg):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    entered, release = threading.Event(), threading.Event()
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
                               content=[SimpleNamespace(type='text', text='{"deals": []}')], stop_reason='end_turn')
    parser = ClaudeParser(audit_cfg, client=SimpleNamespace(messages=SimpleNamespace(create=create)), run_cap_usd=.007)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(parser.extract, 'No deal', {'source_name': 'fixture'})
        assert entered.wait(5)
        try:
            with pytest.raises(SpendCapReached):
                parser.extract('No deal', {'source_name': 'fixture'})
        finally:
            release.set()
        assert first.result() == []
    assert len(calls) == 1 and parser.spend_usd <= parser.run_cap_usd


def test_daily_reservation_survives_restart_and_caps_another_parser(audit_db, audit_cfg, monkeypatch):
    audit_cfg.spend_pacing = False
    audit_cfg.spend_cap_boost_usd_per_day = 0
    audit_cfg.spend_cap_usd_per_day = .008
    monkeypatch.setattr('dealradar.util.now_utc', lambda: AUDIT_NOW)
    model = audit_cfg.parse_model
    audit_db.reserve_llm_call('pending', 'first', model, .0079, AUDIT_NOW,
                              {AUDIT_NOW.date().isoformat(): .008}, .5)
    reopened = DB(audit_db.path)
    parser = ClaudeParser(audit_cfg, client=SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kwargs: pytest.fail('daily cap must stop this call'))))
    parser.bind_ledger(reopened, 'second')
    with pytest.raises(SpendCapReached):
        parser.extract('No deal', {'source_name': 'fixture'})
    assert reopened.spend_usd_for_day(AUDIT_NOW) == pytest.approx(.0079)
    reopened.close()


def test_paid_call_finishing_after_midnight_is_charged_once_to_its_day(audit_db, audit_cfg, monkeypatch):
    before = AUDIT_NOW.replace(hour=23, minute=59)
    after = before + timedelta(minutes=2)
    clock = [before]
    monkeypatch.setattr('dealradar.util.now_utc', lambda: clock[0])
    def create(**kwargs):
        clock[0] = after
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
                               content=[SimpleNamespace(type='text', text='{"deals": []}')], stop_reason='end_turn')
    parser = ClaudeParser(audit_cfg, client=SimpleNamespace(messages=SimpleNamespace(create=create)))
    key = pipeline._run_key(before, False)
    audit_db.start_run(key, 'hourly', before)
    parser.bind_ledger(audit_db, key)
    parser.extract('No deal', {'source_name': 'fixture'})
    audit_db.finish_run(key, now=after, items_fetched=0, posts_new=0, deals_parsed=0, published=0,
                        spend_usd=parser.spend_usd, accounted_spend_usd=parser.ledger_spend_usd)
    assert audit_db.spend_usd_for_day(before) == 0
    assert audit_db.spend_usd_for_day(after) == pytest.approx(parser.spend_usd)


def test_paid_timeout_keeps_worst_case_in_daily_ledger(audit_db, audit_cfg, monkeypatch):
    import anthropic
    import httpx2
    monkeypatch.setattr('dealradar.util.now_utc', lambda: AUDIT_NOW)
    def create(**kwargs):
        raise anthropic.APITimeoutError(request=httpx2.Request('POST', 'https://api.anthropic.com/v1/messages'))
    parser = ClaudeParser(audit_cfg, client=SimpleNamespace(messages=SimpleNamespace(create=create)), sleep=lambda _: None)
    parser.bind_ledger(audit_db, 'timed-out')
    assert parser.extract('No deal', {'source_name': 'fixture'}) == []
    assert parser.spend_usd > 0
    assert audit_db.spend_usd_for_day(AUDIT_NOW) == pytest.approx(parser.spend_usd)
    assert all(row[0] == 'uncertain' for row in audit_db.conn.execute('SELECT status FROM llm_calls'))


def test_llm_call_is_ledgered_without_a_git_checkpoint(audit_db, audit_cfg, monkeypatch):
    # A per-call encrypted push serialized every call; the ledger rides the
    # end-of-run state save instead. Deliveries keep their own checkpoint.
    from dealradar import persistence
    monkeypatch.setattr(persistence, 'checkpoint_state', lambda _: pytest.fail('no git push per LLM call'))
    monkeypatch.setattr('dealradar.util.now_utc', lambda: AUDIT_NOW)
    reserved = []
    def create(**kwargs):
        reserved.extend(row[0] for row in audit_db.conn.execute('SELECT status FROM llm_calls'))
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
                               content=[SimpleNamespace(type='text', text='{"deals": []}')], stop_reason='end_turn')
    parser = ClaudeParser(audit_cfg, client=SimpleNamespace(messages=SimpleNamespace(create=create)))
    parser.bind_ledger(audit_db, 'no-checkpoint')
    parser.extract('No deal', {'source_name': 'fixture'})
    assert reserved == ['reserved']
    assert [row[0] for row in audit_db.conn.execute('SELECT status FROM llm_calls')] == ['settled']
    assert audit_db.spend_usd_for_day(AUDIT_NOW) == pytest.approx(parser.spend_usd)


@pytest.mark.parametrize('surface', ['pinterest', 'facebook', 'instagram', 'telegram'])
def test_lost_social_response_is_not_replayed_after_reopening(audit_db, audit_cfg, monkeypatch, surface):
    monkeypatch.setenv('PINTEREST_APP_ID', 'synthetic-app')
    monkeypatch.setenv('PINTEREST_APP_SECRET', 'synthetic-secret')
    monkeypatch.setenv('META_SYSTEM_TOKEN', 'synthetic-secret')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'synthetic-secret')
    monkeypatch.setenv('TELEGRAM_CHANNEL_ID', 'synthetic-channel')
    monkeypatch.setattr(pinterest.time, 'sleep', lambda _: None)
    audit_cfg.telegram_channel['min_age_minutes'] = 0
    audit_cfg.meta['enabled'] = True
    for network in ('facebook', 'instagram'):
        audit_cfg.meta[network].update(mode='live', hours_utc=[AUDIT_NOW.hour])
    audit_cfg.meta['instagram' if surface == 'facebook' else 'facebook']['daily_max'] = 0
    seed_auth(audit_db)
    published(audit_db, audit_cfg, [candidate('same', 1)])
    accepted = []
    def handle(req):
        if req.url.path.endswith('/boards'):
            return httpx.Response(200, json={'items': [{'id': 'board', 'name': audit_cfg.pinterest['board_name']}]})
        if req.method == 'GET':
            return httpx.Response(200, json={'access_token': 'synthetic-page-token'})
        accepted.append(req)
        raise httpx.ReadTimeout('accepted, response lost', request=req)
    def send(database):
        if surface == 'pinterest':
            pinterest.post(database, audit_cfg, transport=httpx.MockTransport(handle), now=AUDIT_NOW)
        elif surface == 'telegram':
            with httpx.Client(transport=httpx.MockTransport(handle)) as client:
                try:
                    telegram_pub.send_alerts(database, audit_cfg, AUDIT_NOW, http_client=client)
                except httpx.ReadTimeout:
                    pass
        else:
            meta.post(database, audit_cfg, transport=httpx.MockTransport(handle), now=AUDIT_NOW)
    send(audit_db)
    reopened = DB(audit_db.path)
    send(reopened)
    reopened.close()
    assert len(accepted) == 1


def test_delivery_is_checkpointed_before_send_and_remains_claimed_after_save_failure(audit_db, audit_cfg, monkeypatch):
    from dealradar import persistence
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'synthetic-secret')
    monkeypatch.setenv('TELEGRAM_CHANNEL_ID', 'synthetic-channel')
    audit_cfg.telegram_channel['min_age_minutes'] = 0
    published(audit_db, audit_cfg, [candidate('same', 1)])
    sequence = []
    def checkpoint(database):
        assert database.outgoing_exists('telegram', 'same|telegram')
        sequence.append('checkpoint')
    monkeypatch.setattr(persistence, 'checkpoint_state', checkpoint)
    def handle(req):
        sequence.append('send')
        return httpx.Response(200, json={'ok': True})
    monkeypatch.setattr(audit_db, 'record_alert', lambda **kwargs: (_ for _ in ()).throw(OSError('state save failed')))
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(OSError):
            telegram_pub.send_alerts(audit_db, audit_cfg, AUDIT_NOW, http_client=client)
        reopened = DB(audit_db.path)
        telegram_pub.send_alerts(reopened, audit_cfg, AUDIT_NOW, http_client=client)
        reopened.close()
    assert sequence == ['checkpoint', 'send']


@pytest.mark.parametrize('surface', ['member', 'digest', 'newsletter', 'confirmation'])
@pytest.mark.parametrize('failure', ['lost_response', 'quit'])
def test_accepted_smtp_delivery_is_never_replayed(audit_db, audit_cfg, monkeypatch, surface, failure):
    import smtplib
    monkeypatch.setenv('SMTP_HOST', 'smtp.example')
    monkeypatch.setenv('NEWSLETTER_SECRET', 'synthetic-secret')
    audit_cfg.newsletter_enabled = audit_cfg.members_email_enabled = True
    audit_cfg.newsletter_hour_local = audit_cfg.digest_hour_local = audit_cfg.members_email_hour_local = 0
    audit_cfg.digest_recipient = 'synthetic@example.com'
    subscriber = {'email': 'synthetic@example.com', 'token': 'synthetic-token'}
    monkeypatch.setattr(members, 'member_list', lambda *args: [subscriber['email']])
    monkeypatch.setattr(newsletter, 'fetch_subscribers', lambda *args, **kwargs: [subscriber])
    monkeypatch.setattr(newsletter, 'post_confirm_sent', lambda *args, **kwargs: True)
    delivered = []
    class SMTP:
        def __init__(self, *args, **kwargs):
            pass
        def starttls(self):
            pass
        def login(self, *args):
            pass
        def send_message(self, msg, **kwargs):
            delivered.append(msg)
            if failure == 'lost_response':
                raise smtplib.SMTPServerDisconnected('accepted, response lost')
        def quit(self):
            if failure == 'quit':
                raise OSError('quit failed after acceptance')
    deals = [candidate('same', 1, first_seen_at=iso(AUDIT_NOW))]
    def send(database):
        if surface == 'member':
            return members.send_member_alerts(database, audit_cfg, AUDIT_NOW, deals=deals, smtp_factory=SMTP)
        if surface == 'digest':
            return email_digest.maybe_send_digest(database, audit_cfg, AUDIT_NOW, deals=deals, smtp_factory=SMTP)
        if surface == 'newsletter':
            return newsletter.send_daily_newsletter(database, audit_cfg, AUDIT_NOW, deals=deals, smtp_factory=SMTP)
        return newsletter.send_confirmations(database, audit_cfg, AUDIT_NOW, smtp_factory=SMTP)
    first = send(audit_db)
    if failure == 'quit':
        assert first is True if surface == 'digest' else first['sent'] == 1
    reopened = DB(audit_db.path)
    send(reopened)
    reopened.close()
    assert len(delivered) == 1
    assert delivered[0]['Message-ID']


def test_encrypted_checkpoint_contains_the_claim_before_remote_send(tmp_path, monkeypatch):
    import sqlite3
    from dealradar import persistence
    db = DB(tmp_path / 'state.db')
    monkeypatch.setenv('DEALRADAR_DURABLE_STATE', '1')
    monkeypatch.setenv('STATE_DB_KEY', 'synthetic-checkpoint-key')
    monkeypatch.setattr(persistence, 'ROOT', tmp_path)
    saved = []
    class Save:
        def __init__(self, command, **kwargs):
            assert command == ['bash', str(tmp_path / 'scripts/commit_state.sh')]
            assert kwargs['cwd'] == tmp_path and kwargs['start_new_session']
            with sqlite3.connect(tmp_path / 'state.db') as snapshot:
                saved.append(snapshot.execute("SELECT status FROM outgoing").fetchone()[0])
        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(persistence.subprocess, 'Popen', Save)
    assert persistence.prepare_delivery(db, 'telegram', 'same', AUDIT_NOW)
    assert saved == ['pending']
    assert not persistence.prepare_delivery(db, 'telegram', 'same', AUDIT_NOW)
    db.close()


def test_slow_checkpoint_skips_sends_kills_its_git_and_retries_next_run(tmp_path, monkeypatch):
    import subprocess
    from dealradar import persistence
    db = DB(tmp_path / 'state.db')
    monkeypatch.setenv('DEALRADAR_DURABLE_STATE', '1')
    monkeypatch.setenv('STATE_DB_KEY', 'synthetic-checkpoint-key')
    monkeypatch.setattr(persistence, 'ROOT', tmp_path)
    started, killed = [], []
    class Hang:
        pid = 4242
        def __init__(self, command, **kwargs):
            started.append(command)
        def wait(self, timeout=None):
            if timeout is not None and not killed:
                raise subprocess.TimeoutExpired('commit_state.sh', timeout)
            return -9
    monkeypatch.setattr(persistence.subprocess, 'Popen', Hang)
    monkeypatch.setattr(persistence.os, 'killpg', lambda pid, sig: killed.append(pid))
    # No exception reaches the sender, nothing is sent, the whole group dies.
    assert not persistence.prepare_delivery(db, 'pinterest', 'first', AUDIT_NOW)
    assert killed == [4242]
    assert db.conn.execute("SELECT status FROM outgoing WHERE dedup_token='first'").fetchone()[0] == 'rejected'
    # Later sends in the same process stop at once: no claim, no second wait.
    assert not persistence.prepare_delivery(db, 'pinterest', 'second', AUDIT_NOW)
    assert len(started) == 1
    assert db.conn.execute("SELECT COUNT(*) FROM outgoing WHERE dedup_token='second'").fetchone()[0] == 0
    db.close()
    # The next run may claim the rejected delivery again.
    reopened = DB(tmp_path / 'state.db')
    monkeypatch.setattr(persistence.subprocess, 'Popen',
                        lambda *a, **k: type('Done', (), {'wait': lambda self, timeout=None: 0})())
    assert persistence.prepare_delivery(reopened, 'pinterest', 'first', AUDIT_NOW)
    reopened.close()


def test_independent_matching_sources_can_still_corroborate(audit_db, audit_cfg):
    row = audit_seed(audit_db, audit_cfg, final=30)
    audit_seed(audit_db, audit_cfg, source='dealmoon_us', final=31)
    assert verify.assign_tier(audit_db, audit_db.get_deal(row['dedupe_key']), audit_cfg, AUDIT_NOW) == 'corroborated'


def test_pending_keepa_deal_gets_a_verdict_on_a_later_run(audit_db, audit_cfg):
    from tests.test_ai_spend import parser_for
    row = audit_seed(audit_db, audit_cfg, source='keepa_amazon_drops', url='https://www.amazon.com/dp/B012345678')
    pipeline._run_critic(audit_db, audit_cfg, None, AUDIT_NOW)
    assert not rank.published_deals(audit_db, audit_cfg, AUDIT_NOW)
    parser, _ = parser_for()
    assert pipeline._run_critic(audit_db, audit_cfg, parser, AUDIT_NOW)['judged'] == 1
    assert rank.published_deals(audit_db, audit_cfg, AUDIT_NOW)[0]['dedupe_key'] == row['dedupe_key']


def test_old_historical_minimum_is_hidden_when_existing_sightings_supersede_it(audit_db, audit_cfg):
    row = audit_seed(audit_db, audit_cfg, final=30)
    payload = dict(row)
    payload.pop('price_published_at')
    audit_db.update_deal(row['dedupe_key'], payload=payload)
    audit_db.add_sighting(row['id'], source_name='hip2save', source_url=row['sightings'][0]['source_url'],
                          content_hash='newer-price', seen_at=AUDIT_NOW + timedelta(hours=1), final_price=45)
    reopened = DB(audit_db.path)
    assert not rank.published_deals(reopened, audit_cfg, AUDIT_NOW + timedelta(hours=1))
    reopened.close()


@pytest.mark.parametrize('label,canonical', [
    ('Second Hand', 'used'), ('pre-owned', 'used'), ('Previously Owned', 'used'),
    ('Amazon Warehouse - Used Like New', 'used'), ('OpenBox', 'open_box'),
    ('FACTORY REFURB', 'refurbished'), ('Renewed', 'refurbished'),
    ('Condition: Used - Very Good', 'used'), ('OpenBox (Excellent)', 'open_box'),
    ('Second Hand - Excellent', 'used'),
])
def test_condition_synonyms_share_the_same_exclusions(label, canonical, audit_cfg):
    from dealradar.parse.schema import LLMDeal
    model = LLMDeal(item='Widget', store='Amazon', conditions=label)
    assert model.conditions == [canonical]
    row = make_deal(conditions=model.conditions, pct_at_checkout=55)
    audit_cfg.used_min_pct = 60
    assert not rank.is_publishable(row, audit_cfg)[0]
    assert verify.is_used_deal(row)


def test_internal_sdk_error_with_usage_keeps_billed_spend(audit_db, audit_cfg, monkeypatch):
    monkeypatch.setattr('dealradar.util.now_utc', lambda: AUDIT_NOW)
    class PaidError(AttributeError):
        usage = SimpleNamespace(input_tokens=1000, output_tokens=100)
    def create(**kwargs):
        raise PaidError('SDK failed after a billed response')
    parser = ClaudeParser(audit_cfg, client=SimpleNamespace(messages=SimpleNamespace(create=create)))
    parser.bind_ledger(audit_db, 'paid-sdk-failure')
    with pytest.raises(PaidError):
        parser.extract('No deal', {'source_name': 'fixture'})
    assert parser.spend_usd == pytest.approx(.00015)
    assert audit_db.spend_usd_for_day(AUDIT_NOW) == pytest.approx(.00015)


@pytest.mark.parametrize('inputs,text', [
    ({'rebates': ['$30']}, 'Widget regular $100, now $30.'),
    ({'stack_steps': ['90% of shoppers recommend it']}, 'Widget regular $100. 90% of shoppers recommend it.'),
    ({'deal_type': 'bogo', 'evidence': Evidence(regular_price_quote='regular $100',
                                             deal_type_quote='BOGO 50% off')},
     'Widget regular $100. BOGO 50% off.'),
])
def test_verbatim_numbers_alone_cannot_establish_rebates_or_discount_steps(inputs, text):
    facts = dict(item='Widget', store='Walmart', source_name='hip2save', source_url='https://hip2save.com/x',
                 regular_price=100, evidence=Evidence(regular_price_quote='regular $100'))
    facts.update(inputs)
    assert not verify_evidence(ExtractedDeal(**facts), text)[0]


@pytest.mark.parametrize('value,quote,source', [
    (9.99, '9.99', 'now $19.99'),
    (999, 'now $999', 'now $999,999'),
    (float('nan'), 'now nan', 'now nan'),
])
def test_partial_numeric_quotes_cannot_verify_a_different_price(value, quote, source):
    deal = ExtractedDeal(item='Widget', store='Walmart', source_name='hip2save', source_url='https://hip2save.com/x',
                         final_price=value, evidence=Evidence(final_price_quote=quote))
    assert not verify_evidence(deal, source)[0]


def test_later_known_product_identity_still_prevents_a_variant_merge(audit_db, audit_cfg):
    first = audit_seed(audit_db, audit_cfg, item='Samsung Crystal UHD 55 inch Television', url='', final=300, regular=800)
    product = audit_seed(audit_db, audit_cfg, item=first['item'], url='https://walmart.com/ip/55', final=300, regular=800)
    assert first['dedupe_key'] == product['dedupe_key']
    audit_seed(audit_db, audit_cfg, item='Samsung Crystal UHD 65 inch Television',
               url='https://walmart.com/ip/65', final=200, regular=800)
    current = audit_db.get_deal(first['dedupe_key'])
    assert current['canonical_url'] == 'https://walmart.com/ip/55' and current['final_price'] == 300
    assert len(audit_db.all_deals()) == 2


def test_keepa_source_capitalization_cannot_bypass_critic(audit_cfg):
    row = make_deal(sightings=[{'source_name': 'KEEPA_AMAZON_DROPS'}], critic=None)
    assert not rank.is_publishable(row, audit_cfg)[0]


@pytest.mark.parametrize('prior', [{'reason': 'incomplete'}, {'plausible': 'true'}])
def test_invalid_keepa_verdict_is_retried(audit_db, audit_cfg, prior):
    from tests.test_ai_spend import parser_for

    row = audit_seed(audit_db, audit_cfg, source='keepa_amazon_drops',
                     url='https://www.amazon.com/dp/B012345678')
    audit_db.update_deal(row['dedupe_key'], payload={**row, 'critic': prior})
    assert not rank.published_deals(audit_db, audit_cfg, AUDIT_NOW)
    parser, _ = parser_for()
    assert pipeline._run_critic(audit_db, audit_cfg, parser, AUDIT_NOW)['judged'] == 1
    assert rank.published_deals(audit_db, audit_cfg, AUDIT_NOW)


@pytest.mark.parametrize('urls', [
    ('https://www.ebay.com/itm/1234567890', 'https://www.ebay.com/itm/1234567891'),
    ('https://store.example/products/widget-55', 'https://store.example/products/widget-65'),
])
def test_variant_urls_without_a_known_retailer_parser_stay_separate(audit_db, audit_cfg, urls):
    first = audit_seed(audit_db, audit_cfg, item='Samsung Crystal UHD 55 inch Television',
                       url=urls[0], final=300, regular=800)
    audit_seed(audit_db, audit_cfg, item='Samsung Crystal UHD 65 inch Television',
               url=urls[1], final=200, regular=800)
    assert len(audit_db.all_deals()) == 2
    assert audit_db.get_deal(first['dedupe_key'])['final_price'] == 300


@pytest.mark.parametrize('second_source,second_price', [
    ('hip2save_amazon', 30), ('dealmoon_us', 50),
])
def test_unconfirmed_price_has_no_independent_source_badge_or_headline(
        audit_db, audit_cfg, second_source, second_price):
    row = audit_seed(audit_db, audit_cfg, final=30)
    audit_seed(audit_db, audit_cfg, source=second_source, final=second_price)
    audit_db.update_deal(row['dedupe_key'], verify_tier='corroborated')
    verify.refresh_tiers(audit_db, audit_cfg, AUDIT_NOW)
    rendered = site.render_site(audit_db, audit_cfg, AUDIT_NOW)['full_payload']['deals']
    assert len(rendered) == 1
    public = rendered[0]
    assert public['badge'] != 'corroborated'
    assert 'independent sources' not in public['headline']
    assert '2+ sources' not in seo.card_html(public, now=AUDIT_NOW)
    pages = list((Path(audit_cfg.site_dir) / 'deal').glob('*/index.html'))
    assert len(pages) == 1
    assert 'independent sources confirmed this price' not in pages[0].read_text()


@pytest.mark.parametrize('inputs,text', [
    ({'stack_steps': ['50% off', '50% off']}, 'Widget regular $100. 50% off.'),
    ({'stack_steps': ['Use code SAVE50 for 50% off', '50% off']},
     'Widget regular $100. Use code SAVE50 for 50% off.'),
    ({'stack_steps': ['50% off', 'sale $80']}, 'Widget regular $100. sale $80, then 50% off.'),
    ({'stack_steps': ['$10 mail-in rebate'], 'rebates': ['$10 mail-in rebate']},
     'Widget regular $100. $10 mail-in rebate.'),
    ({'stack_steps': ['5% off']}, 'Widget regular $100. 25% off.'),
    ({'shipping_cost_or_threshold': '$6', 'evidence': Evidence(
        regular_price_quote='regular $100', shipping_quote='6 shipping')},
     'Widget regular $100. $16 shipping.'),
    ({'deal_type': 'penny', 'evidence': Evidence(
        regular_price_quote='regular $100', deal_type_quote='Penny Arcade')},
     'Penny Arcade regular $100.'),
])
def test_repeated_or_partial_math_evidence_cannot_invent_numbers(inputs, text):
    facts = dict(item='Widget', store='Walmart', source_name='hip2save',
                 source_url='https://hip2save.com/x', regular_price=100,
                 evidence=Evidence(regular_price_quote='regular $100'))
    facts.update(inputs)
    assert not verify_evidence(ExtractedDeal(**facts), text)[0]


@pytest.mark.parametrize('condition', ['Used', 'Open Box', 'open-box', 'Manufacturer Refurbished'])
def test_social_selector_excludes_condition_spellings_independently_of_pick_score(
        audit_db, audit_cfg, condition):
    published(audit_db, audit_cfg, [candidate('used', 1, conditions=[condition])])
    assert not social._candidates(audit_db, audit_cfg, {})


@pytest.mark.parametrize('first_source,second_source', [
    ('hip2save', 'hip2save_amazon'),
    ('southernsavers', 'southernsavers_online'),
    ('krazy_coupon_lady', 'kcl_amazon'),
    ('slickdeals_frontpage', 'slickdeals_amazon'),
])
def test_two_feeds_of_one_site_never_corroborate(audit_db, audit_cfg, first_source, second_source):
    row = audit_seed(audit_db, audit_cfg, source=first_source)
    audit_seed(audit_db, audit_cfg, source=second_source)
    assert audit_db.distinct_sources_within(row['id'], AUDIT_NOW - timedelta(hours=24),
                                           now=AUDIT_NOW) == 1
    assert verify.assign_tier(audit_db, audit_db.get_deal(row['dedupe_key']),
                              audit_cfg, AUDIT_NOW) != 'corroborated'


def test_accepted_instagram_publish_with_lost_response_is_never_replayed(audit_db, audit_cfg, monkeypatch):
    monkeypatch.setenv('META_SYSTEM_TOKEN', 'synthetic-secret')
    monkeypatch.setattr(meta.time, 'sleep', lambda _: None)
    audit_cfg.meta['enabled'] = True
    audit_cfg.meta['facebook']['daily_max'] = 0
    audit_cfg.meta['instagram'].update(mode='live', hours_utc=[AUDIT_NOW.hour])
    published(audit_db, audit_cfg, [candidate('same', 1)])
    deliveries = []
    def handle(req):
        if req.url.path.endswith('/media_publish'):
            deliveries.append(req)
            raise httpx.ReadTimeout('published, response lost', request=req)
        assert req.url.path.endswith('/media')
        return httpx.Response(200, json={'id': 'synthetic-container', 'status_code': 'FINISHED'})
    meta.post(audit_db, audit_cfg, transport=httpx.MockTransport(handle), now=AUDIT_NOW)
    reopened = DB(audit_db.path)
    meta.post(reopened, audit_cfg, transport=httpx.MockTransport(handle), now=AUDIT_NOW)
    reopened.close()
    assert len(deliveries) == 1


@pytest.mark.parametrize('surface', ['newsletter', 'digest'])
def test_finished_email_run_does_not_send_a_new_day_batch(audit_db, audit_cfg, monkeypatch, surface):
    monkeypatch.setenv('DEALRADAR_RUN_KEY', 'same-workflow-run')
    monkeypatch.setenv('SMTP_HOST', 'smtp.example')
    monkeypatch.setenv('NEWSLETTER_SECRET', 'synthetic-secret')
    audit_cfg.newsletter_enabled = True
    audit_cfg.newsletter_max_per_run = 1
    audit_cfg.newsletter_hour_local = audit_cfg.digest_hour_local = 0
    audit_cfg.digest_recipient = 'synthetic@example.com'
    monkeypatch.setattr(newsletter, 'fetch_subscribers', lambda *args, **kwargs: [
        {'email': f'subscriber{i}@example.com', 'token': f'token{i}'} for i in range(2)])
    delivered = []
    class SMTP:
        def __init__(self, *args, **kwargs): pass
        def starttls(self): pass
        def login(self, *args): pass
        def send_message(self, msg, **kwargs): delivered.append(msg)
        def quit(self): pass
    def send(at):
        deals = [candidate('same', 1, first_seen_at=iso(at))]
        if surface == 'newsletter':
            return newsletter.send_daily_newsletter(audit_db, audit_cfg, at, deals=deals, smtp_factory=SMTP)
        return email_digest.maybe_send_digest(audit_db, audit_cfg, at, deals=deals, smtp_factory=SMTP)
    send(AUDIT_NOW)
    assert len(delivered) == 1
    tomorrow = AUDIT_NOW + timedelta(days=1)
    send(tomorrow)
    assert len(delivered) == 1
    monkeypatch.setenv('DEALRADAR_RUN_KEY', 'next-workflow-run')
    send(tomorrow)
    assert len(delivered) == 2


def test_delivery_run_quota_is_atomic_across_connections(audit_db):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    audit_db.delivery_batch('telegram', 'same-run', ['first', 'second'], quota=1)
    ready = threading.Barrier(2)
    def claim(key):
        connection = DB(audit_db.path)
        try:
            ready.wait(5)
            return connection.claim_outgoing('telegram', key, 'same-run', AUDIT_NOW)
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(claim, ['first', 'second']))
    assert sorted(results) == [False, True]
    assert len(audit_db.outgoing_for('telegram')) == 1
