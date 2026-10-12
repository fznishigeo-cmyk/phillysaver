"""Deal-specific end evidence and publication-ordered merge facts."""

import dataclasses
from datetime import timedelta

import pytest

from dealradar import dedupe, verify
from dealradar.db import DB
from dealradar.models import Evidence
from dealradar.util import iso
from tests.test_dedupe import NOW, make_cfg, make_extracted, make_post, make_savings


@pytest.mark.parametrize("outside", [
    "Navigation: expired deals", "Sidebar: expired offers", "Comment: sold out",
    "Dead Sea salt", "This unrelated deal is dead", "Other item is OOS",
])
def test_unrelated_body_text_never_ends_a_deal(outside):
    post = make_post(text=f"{outside}\nWidget just $25, regularly $100\n{outside}")
    ext = make_extracted(evidence=Evidence(final_price_quote="Widget just $25"))
    assert verify.post_marks_dead(post, ext) is False


@pytest.mark.parametrize("title,expected", [
    ("EXPIRED: Widget $25", True), ("Dead Sea salt $25", False),
    ("dead", False), ("deal is dead", True), ("dead deal", True),
])
def test_title_end_wording_applies_without_body_evidence(title, expected):
    post = dataclasses.replace(make_post(text="Widget $25"), title=title)
    assert verify.post_marks_dead(post, make_extracted()) is expected


@pytest.mark.parametrize("quote", ["", "widget $25", "Widget  $25", "Not present $25"])
def test_end_line_requires_a_nonempty_verbatim_quote(quote):
    post = make_post(text="Widget $25 sold out")
    assert not verify.post_marks_dead(
        post, make_extracted(evidence=Evidence(final_price_quote=quote)))


def test_roundup_only_ends_the_deal_with_end_wording_on_its_evidence_line():
    post = make_post(text="Widget $25 — sold out\nKettle $10 still available\nExpired deals")
    first = make_extracted(evidence=Evidence(final_price_quote="Widget $25"))
    second = make_extracted(evidence=Evidence(final_price_quote="Kettle $10"))
    assert verify.post_marks_dead(post, first)
    assert not verify.post_marks_dead(post, second)


@pytest.fixture
def db(tmp_path):
    database = DB(tmp_path / "state.db")
    yield database
    database.close()


def up(db, uid, published, **values):
    post = dataclasses.replace(make_post(uid=uid), published_at=published)
    return dedupe.upsert_extracted(db, make_extracted(**values), make_savings(),
                                   post, make_cfg(), NOW + timedelta(hours=2))


def test_expiry_tracks_the_post_that_stated_it_not_later_empty_sightings(db):
    first, extended, stale = [iso(NOW + timedelta(days=d)) for d in (1, 2, 10)]
    key = up(db, "first", NOW, expires_at=first)["dedupe_key"]
    up(db, "extended", NOW + timedelta(minutes=10), expires_at=extended)
    assert db.get_deal(key)["expires_at"] == extended
    up(db, "empty", NOW + timedelta(minutes=30), expires_at="")
    assert db.get_deal(key)["expires_at"] == extended
    up(db, "older", NOW - timedelta(minutes=1), expires_at=stale)
    assert db.get_deal(key)["expires_at"] == extended
    # An empty newer sighting must not advance the explicit value's time.
    up(db, "middle", NOW + timedelta(minutes=20), expires_at=first)
    assert db.get_deal(key)["expires_at"] == first


def test_online_only_ignores_older_explicit_and_newer_unknown_sightings(db):
    key = up(db, "first", NOW, online_only="false")["dedupe_key"]
    up(db, "online", NOW + timedelta(minutes=10), online_only="true")
    assert db.get_deal(key)["online_only"] == "true"
    up(db, "older", NOW - timedelta(minutes=1), online_only="false")
    assert db.get_deal(key)["online_only"] == "true"
    up(db, "unknown", NOW + timedelta(minutes=30), online_only="unknown")
    assert db.get_deal(key)["online_only"] == "true"
    up(db, "middle", NOW + timedelta(minutes=20), online_only="false")
    assert db.get_deal(key)["online_only"] == "false"


@pytest.mark.parametrize("published,final,revived", [
    (NOW - timedelta(minutes=1), 25., False), (NOW, 25., False),
    (None, 25., False), (NOW + timedelta(minutes=1), 40., False),
    (NOW + timedelta(minutes=1), 25., True),
])
def test_post_says_over_revives_only_on_a_later_backed_post(db, published, final, revived):
    key = up(db, "first", NOW - timedelta(hours=1))["dedupe_key"]
    assert verify.mark_unconfirmed_dead(db, db.get_deal(key), "post_says_over", NOW)
    post = dataclasses.replace(make_post(uid="later"), published_at=published)
    result = dedupe.upsert_extracted(db, make_extracted(), make_savings(final=final),
                                     post, make_cfg(), NOW + timedelta(hours=1))
    assert result["revived"] is revived
    row = db.get_deal(key)
    assert row["status"] == ("alive" if revived else "dead")
    assert row["death"] == ("" if revived else "post_says_over")


def test_unconfirmed_marking_never_restamps_an_existing_death(db):
    key = up(db, "first", NOW)["dedupe_key"]
    assert verify.mark_unconfirmed_dead(db, db.get_deal(key), "post_says_over", NOW)
    before = db.get_deal(key)
    assert not verify.mark_unconfirmed_dead(db, before, "stated_expiry", NOW + timedelta(hours=1))
    assert db.get_deal(key) == before


def test_a_later_expiry_extension_can_revive_a_stated_expiry_death(db):
    key = up(db, "first", NOW - timedelta(hours=1),
             expires_at=iso(NOW - timedelta(minutes=1)))["dedupe_key"]
    assert verify.mark_expired_dead(db, make_cfg(), NOW) == 1
    result = up(db, "extended", NOW + timedelta(minutes=1),
                expires_at=iso(NOW + timedelta(days=1)))
    assert result["revived"]
    assert db.get_deal(key)["status"] == "alive"
