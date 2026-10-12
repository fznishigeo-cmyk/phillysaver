"""Yearly plan config, publication and browser logic, fully offline."""

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dealradar.config import load_config
from dealradar.db import DB
from dealradar.publish.site import render_site


@pytest.mark.parametrize("setting, expected", [("", 0), ("yearly_price_usd: 0", 0),
                                              ("yearly_price_usd: 49", 49)])
def test_yearly_config_and_public_member_meta(tmp_path, setting, expected):
    config = tmp_path / "config.yaml"
    config.write_text("paywall:\n  " + setting + "\n")
    cfg = load_config(config)
    assert cfg.paywall_yearly_price_usd == expected
    db = DB(tmp_path / "state.db")
    try:
        full = render_site(db, cfg, datetime(2026, 10, 12, tzinfo=timezone.utc),
                           out_dir=tmp_path / "site", deals=[])
    finally:
        db.close()
    public = json.loads((tmp_path / "site/deals.json").read_text())
    assert public["meta"]["yearly_price_usd"] == expected
    assert full["full_payload"]["meta"]["yearly_price_usd"] == expected
    html = (tmp_path / "site/index.html").read_text()
    assert "{{PLAN_CHOICES_" not in html
    if expected:
        for place in ("panel", "dialog"):
            assert f'<fieldset id="{place}plans" class="plans">' in html
            assert f'name="{place}plan" value="month" checked' in html
            assert f'name="{place}plan" value="year"' in html
        assert "Membership — $9.99/month or $49/year" in html
        assert ".plans label:focus-within" in html
        assert "min-height:48px" in html
    else:
        assert '<fieldset id="panelplans"' not in html
        assert '<fieldset id="dialogplans"' not in html
        assert "<strong>Membership — $9.99/month:</strong>" in html
        assert " or $49/year" not in html


def test_plan_choice_terms_checkout_and_monthly_off_behavior(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    html = (Path(__file__).resolve().parents[1] /
            "dealradar/publish/templates/index.html").read_text()

    def between(start, end):
        return html[html.index(start):html.index(end)]

    snippets = [between("  function yearlyPrice()", "  // While open"),
                between("  function cancelLine()", "  // What to do instead"),
                between("  function offer()", "  // Anyone who can start"),
                between("  function api(path", "  // GET /api/me"),
                between("  function checkout(ui)", "  // Checkout refused with a reason")]
    (tmp_path / "page.js").write_text("\n".join(snippets))
    script = r'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const groups = {};
for (const id of ["panelplans", "dialogplans"]) {
  const inputs = ["month", "year"].map(value => ({value, nextElementSibling: {}}));
  groups[id] = {hidden: false, inputs, querySelectorAll: () => inputs};
}
let response, request, event, destination, refreshed = false;
const ctx = {
  state: {meta: {yearly_price_usd: 49}, me: {trial_available: true}, plan: "month"},
  auth: {}, document: {activeElement: null, getElementById: id => groups[id]},
  num: v => v == null ? null : Number(v), contactEmail: () => "hello@wowpriced.com",
  trialDays: () => 7, token: () => "signed-session", apiUrl: () => "https://api.test",
  https: u => /^https:/.test(u || "") ? u : "", esc: s => s,
  fetch: (url, opts) => {request = {url, ...opts}; return Promise.resolve(response);},
  track: (name, params) => {event = {name, params};},
  location: {assign: url => {destination = url;}},
  memberDeals: () => {refreshed = true; return Promise.resolve(null);},
  AUTH_ERR: {http_429: "rate limited", network: "network error"},
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync("page.js", "utf8"), ctx);
const run = s => vm.runInContext(s, ctx);
const monthlyTrial = "Card required. Free for 7 days, then $9.99/month, renews monthly until you cancel. Cancel anytime online or email hello@wowpriced.com.";
const monthlyPlain = "Subscribe — $9.99/month, renews monthly until you cancel. Cancel anytime online or email hello@wowpriced.com.";
assert.equal(run("trialDisclosure(7)"), monthlyTrial);
assert.equal(run("plainDisclosure()"), monthlyPlain);
run("syncPlanChoices()");
for (const g of Object.values(groups)) {
  assert.equal(g.inputs[0].checked, true);
  assert.equal(g.inputs[1].nextElementSibling.textContent, "Yearly $49 · save 59%");
}
run('state.plan = "year"; syncPlanChoices()');
for (const g of Object.values(groups)) assert.equal(g.inputs[1].checked, true);
assert.equal(run("trialDisclosure(7)"), "Card required. Free for 7 days, then $49/year, renews yearly until you cancel. Cancel anytime online or email hello@wowpriced.com.");
assert.equal(run("plainDisclosure()"), "Subscribe — $49/year, renews yearly until you cancel. Cancel anytime online or email hello@wowpriced.com.");
assert.equal(run("prices()"), "$9.99/month or $49/year");
assert.equal(run("offer().btn"), "Start 7-day free trial");
assert.equal(run("shortNote(offer())"), "$0 today.");
ctx.state.me.trial_available = false;
assert.equal(run("offer().note"), run("plainDisclosure()"));
ctx.state.me.trial_available = true;
async function check(status, json) {
  destination = event = null;
  response = {status, ok: status === 200, json: () => Promise.resolve(json)};
  const ui = {btn: {}, err: {}, fb: {}};
  ctx.ui = ui;
  run("checkout(ui)");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(request.method, "POST");
  assert.equal(request.headers.Authorization, "Bearer signed-session");
  assert.deepEqual(JSON.parse(request.body), {plan: "year"});
  return ui;
}
(async () => {
  await check(200, {url: "https://checkout.stripe.com/year"});
  assert.equal(destination, "https://checkout.stripe.com/year");
  assert.equal(event.name, "begin_checkout");
  assert.equal(event.params.plan, "year");
  const ui = await check(409, {error: "plan_unavailable", me: ctx.state.me});
  assert.equal(ui.err.textContent, "Yearly isn't available right now. Choose monthly.");
  assert.equal(ui.err.hidden, false);
  assert.equal(refreshed, false);
  assert.equal(destination, null);
  for (const status of [404, 503]) {
    ctx.state.me.subscribe_url = "https://buy.stripe.com/month";
    const failed = await check(status, {fallback_url: "https://buy.stripe.com/month"});
    assert.equal(ctx.auth.fb, undefined);
    assert.equal(failed.err.hidden, false);
    assert.equal(destination, null);
  }
  // Off or missing: even a stale yearly choice produces the exact monthly copy.
  for (const price of [0, undefined]) {
    ctx.state.meta.yearly_price_usd = price;
    run("syncPlanChoices()");
    assert.equal(run("chosenPlan()"), "month");
    assert.equal(run("prices()"), "$9.99/month");
    assert.equal(run("trialDisclosure(7)"), monthlyTrial);
    assert.equal(run("plainDisclosure()"), monthlyPlain);
    assert.equal(run("shortNote({trial:true})"), "$0 today.");
    assert.equal(run("shortNote({trial:false})"), "$9.99/month, renews monthly.");
    for (const g of Object.values(groups)) assert.equal(g.hidden, true);
    response = {status: 200, ok: true, json: () => Promise.resolve({url: "https://checkout.stripe.com/month"})};
    run("checkout(ui)");
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(JSON.parse(request.body), {plan: "month"});
    assert.equal(event.params.plan, "month");
  }
})().catch(e => {console.error(e); process.exitCode = 1;});
'''
    result = subprocess.run(["node", "-e", script], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
