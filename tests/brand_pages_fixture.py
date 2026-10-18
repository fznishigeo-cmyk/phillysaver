"""Small offline publication shared by task 289's byte and browser checks."""
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import site
from tests.history_pins import pre_task_290
from tests.phone_budget_fixture import NOW, fixture_offers

PAGES = ("about.html", "join.html", "contact.html", "careers.html", "hacks.html", "how-it-works.html")


def config(mode="support"):
    if mode == "current":
        # Pins were recorded on the 925d26d release: before search on, Analyze, install and plain credits.
        return pre_task_290(load_config())
    return AppConfig(custom_domain="wowpriced.com", support_enabled=mode == "support",
                     paywall_enabled=True, paywall_open_access=mode == "open",
                     paywall_api_url="https://account.example.invalid", contact_email="hello@wowpriced.com")


def offers():
    rows = fixture_offers()[:16]
    for row in rows:
        row["critic"] = {"plausible": True, "confidence": .9, "reason": "Backed basis"}
        if row["asin"]:
            row["click_price_check"] = dict(asin=row["asin"], final=row["final_price"],
                                            at=NOW.isoformat(), buybox=row["final_price"])
    return rows


def publish(root, cfg, rows=None, db=None):
    owned = db is None
    db = db or DB(":memory:")
    try:
        return site.render_site(db, cfg, NOW, out_dir=root, deals=offers() if rows is None else rows,
                                last_run={"run_key": "task289-fixed"})
    finally:
        if owned:
            db.close()
