"""scripts/stripe_setup.py against a fake Stripe (no network): the Payment
Link redirects to welcome.html with the Checkout Session id, and a link found
with another redirect is updated to it."""

from __future__ import annotations

import importlib.util
import urllib.parse
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "stripe_setup.py"
WELCOME = "https://wowpriced.com/welcome.html?session_id={CHECKOUT_SESSION_ID}"


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.delenv("WOWPRICED_SITE", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_FAKEKEY")
    spec = importlib.util.spec_from_file_location("stripe_setup", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeStripe:
    """The objects a previous run made, except the Payment Links given."""

    def __init__(self, links):
        self.links = links
        self.calls = []

    def __call__(self, method, path, data=None):
        self.calls.append((method, path, data))
        base = path.split("?")[0]
        if method == "GET":
            return {"data": {
                "/products": [{"id": "prod_1", "metadata": {"app": "wowpriced"}}],
                "/prices": [{"id": "price_1", "unit_amount": 999, "currency": "usd", "recurring": {"interval": "month"}}],
                "/payment_links": self.links,
                "/billing_portal/configurations": [{"id": "bpc_1", "metadata": {"app": "wowpriced"}}],
            }[base]}
        if base == "/payment_links" or base.startswith("/payment_links/"):
            link_id = base.rsplit("/", 1)[1] if base != "/payment_links" else "plink_new"
            return {"id": link_id, "url": "https://buy.stripe.com/test_x",
                    "after_completion": {"type": "redirect", "redirect": {"url": data["after_completion[redirect][url]"]}}}
        raise AssertionError(f"unexpected {method} {path}")

    def posts(self):
        return [(path, data) for method, path, data in self.calls if method == "POST"]


def test_a_new_link_redirects_with_the_session_id(setup, monkeypatch):
    fake = FakeStripe(links=[])
    monkeypatch.setattr(setup, "call", fake)
    setup.main()
    [(path, data)] = fake.posts()
    assert path == "/payment_links"
    assert data["after_completion[type]"] == "redirect"
    assert data["after_completion[redirect][url]"] == WELCOME
    # Form-encoded once: Stripe decodes the braces back into its placeholder.
    assert "session_id%3D%7BCHECKOUT_SESSION_ID%7D" in urllib.parse.urlencode(data)


@pytest.mark.parametrize("after", [
    {"type": "redirect", "redirect": {"url": "https://wowpriced.com/welcome.html"}},
    {"type": "hosted_confirmation", "redirect": None},
])
def test_an_existing_link_with_another_redirect_is_updated(setup, monkeypatch, after):
    fake = FakeStripe(links=[{"id": "plink_old", "url": "https://buy.stripe.com/test_x", "metadata": {"app": "wowpriced"},
                              "after_completion": after}])
    monkeypatch.setattr(setup, "call", fake)
    setup.main()
    assert fake.posts() == [("/payment_links/plink_old", {"after_completion[type]": "redirect",
                                                          "after_completion[redirect][url]": WELCOME})]


def test_an_existing_link_already_right_is_left_alone(setup, monkeypatch, capsys):
    fake = FakeStripe(links=[{"id": "plink_ok", "url": "https://buy.stripe.com/test_x", "metadata": {"app": "wowpriced"},
                              "after_completion": {"type": "redirect", "redirect": {"url": WELCOME}}}])
    monkeypatch.setattr(setup, "call", fake)
    setup.main()
    assert fake.posts() == []
    assert "FAKEKEY" not in capsys.readouterr().out
