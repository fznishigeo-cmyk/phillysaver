"""Get deal destination counts, entirely offline."""

from copy import deepcopy

import pytest

from dealradar.publish.site import link_mix

TAG = "our-store-20"
BUCKETS = ("amazon_tagged", "amazon_untagged", "amazon_short",
           "source_post", "other_store", "none")


def deal(url="", **extra):
    return {"status": "alive", "url": url, **extra}


@pytest.mark.parametrize("url, bucket", [
    (f"https://{host}/dp/X?tag={TAG}", "amazon_tagged")
    for host in ("amazon.com", "www.amazon.com", "smile.amazon.com")
] + [
    ("https://amazon.com/dp/X", "amazon_untagged"),
    ("https://amazon.com/dp/X?tag=someone-20", "amazon_untagged"),
    ("https://amazon.com/dp/X?tag=", "amazon_untagged"),
    (f"http://amazon.com/dp/X?tag={TAG}", "amazon_untagged"),
    ("https://amzn.to/abc", "amazon_short"),
    ("https://a.co/abc", "amazon_short"),
    ("https://link.amazon/abc", "amazon_short"),
    ("https://www.walmart.com/ip/1", "other_store"),
    ("https://amazon.com.example/1", "other_store"),
    ("", "none"), (None, "none"), ("/relative", "none"),
    ("https://[invalid", "none"),
])
def test_each_destination_and_exact_amazon_hosts(url, bucket):
    mix = link_mix([deal(url)], TAG)
    assert mix[bucket] == 1
    assert sum(mix[k] for k in BUCKETS) == 1
    assert mix["amazon_untagged_flag"] == (bucket == "amazon_untagged")


def test_source_posts_match_credits_including_fallback_without_steps():
    url = "https://source.example/post?code=SAVE"
    mix = link_mix([
        deal(url, needs_steps=True, credits=[{"url": "https://other.example/post"}, {"url": url}]),
        deal(url, needs_steps=False, credits=[{"url": url}]),
        deal("https://source.example/product", credits=[{"url": url}]),
    ], TAG)
    assert mix["source_post"] == 2 and mix["other_store"] == 1
    assert mix["by_store"] == {"source.example": 1}


def test_folding_ranking_top_ten_totals_and_no_mutation():
    deals = [deal("https://WWW.Walmart.com:443/ip/1"), deal("https://walmart.com/ip/2")]
    deals += [deal(f"https://store{i:02}.example/item?private=value") for i in range(12)]
    deals += [deal(status="dead"), deal(locked=True),
              deal(alive=False), deal("https://a.co/x", status="dead", alive=True)]
    before = deepcopy(deals)
    mix = link_mix(deals, TAG)
    assert list(mix["by_store"].items()) == [("walmart.com", 2)] + [
        (f"store{i:02}.example", 1) for i in range(9)]
    assert mix["other_store"] == 14 and mix["amazon_short"] == 1
    assert sum(mix[k] for k in BUCKETS) == 15
    assert "private" not in repr(mix) and deals == before


def test_tag_is_configured_and_warning_only_when_set():
    deals = [deal(f"https://amazon.com/dp/X?tag={TAG}")]
    assert link_mix(deals, TAG)["amazon_tagged"] == 1
    assert link_mix(deals, "different-20")["amazon_untagged_flag"] is True
    mix = link_mix(deals)
    assert mix["amazon_untagged"] == 1 and mix["amazon_untagged_flag"] is False
    assert sum(link_mix([])[k] for k in BUCKETS) == 0
