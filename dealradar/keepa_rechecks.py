"""Task 087: bounded recheck facts, useful readings/token, and simple scheduling.

No product bodies or engagement requests: saved pick/wall order supplies the
visibility weight. Costs are allocated equally within a paid API batch; a
missing charge receipt is explicitly a conservative estimate.
"""

from __future__ import annotations

import json
import math
from datetime import timedelta, timezone

from . import util, verify


COUNTS = ("tokens", "estimated_tokens", "readings", "changed", "deaths", "useful")


class CallHistory:
    """Bounded shared transport receipts, independent of per-deal history.

    Both hourly and fast runs write the same state. Missing/estimated receipts
    can teach latency, but never masquerade as measured token costs.
    """
    META = "keepa_product_calls"

    def __init__(self, db, now):
        self.db, self.now = db, now
        try:
            saved = json.loads(db.meta_get(self.META) or "[]") if db else []
        except ValueError:
            saved = []
        self.samples = [s for s in saved if isinstance(s, dict)
                        and (at := util.parse_iso(s.get("at") or "")) is not None
                        and now - timedelta(days=2) <= at <= now] if isinstance(saved, list) else []

    def record(self, *, used, live, count, seconds, tokens):
        if self.db is None or count <= 0:
            return
        self.samples.append(dict(at=util.iso(self.now), used=used, live=live,
                                 count=count, seconds=max(1, seconds), tokens=tokens))
        # At most 24 calls per transport mode, including errors; no product bodies.
        counts = {}
        kept = []
        for sample in reversed(self.samples):
            mode = sample["used"], sample["live"]
            counts[mode] = counts.get(mode, 0) + 1
            if counts[mode] <= 24:
                kept.append(sample)
        self.db.meta_set(self.META, json.dumps(list(reversed(kept)), separators=(",", ":")))


def empty_counts():
    return {**dict.fromkeys(COUNTS, 0), "tokens_per_useful_reading": None}


def add_counts(target, values):
    for key in COUNTS:
        target[key] += values[key]
    target["tokens_per_useful_reading"] = (
        round(target["tokens"] / target["useful"], 3) if target["useful"] else None)


class History:
    def __init__(self, db, cfg, now):
        self.db, self.cfg, self.now = db, cfg, now
        run = db.conn.execute(
            "SELECT run_key FROM run_stats WHERE started_at=? AND kind IN ('fast','hourly') LIMIT 1",
            (util.iso(now),)).fetchone()
        self.run_key = run["run_key"] if run else None
        self.samples = {r["deal_key"]: json.loads(r["samples"]) for r in db.conn.execute(
            "SELECT deal_key, samples FROM keepa_recheck_history")}
        self.yield_stats = {"overall": empty_counts(), "classes": {}}

    def attempted(self, key):
        return any(s["requested_at"] == util.iso(self.now) or
                   (self.run_key is not None and s.get("run_key") == self.run_key)
                   for s in self.samples.get(key, []))

    def requested_recently(self, key):
        interval = timedelta(minutes=self.cfg.keepa_recheck_interval_minutes)
        latest = max(self.samples.get(key, []), key=lambda s: s["requested_at"], default=None)
        return bool(latest and latest.get("answered", latest["reading"]) and
                    self.now - util.parse_iso(latest["requested_at"]) < interval)

    def request_age(self, deals):
        """Paid price requests, including lost answers; never feed/photo clocks."""
        amazon = [d for d in deals if verify.is_amazon_deal(d) and d.get("status", "alive") == "alive"]
        ages = []
        for deal in amazon:
            stamps = [at for s in self.samples.get(deal["dedupe_key"], [])
                      if (at := util.parse_iso(s["requested_at"])) is not None and at <= self.now]
            if stamps:
                ages.append((self.now - max(stamps)).total_seconds() / 3600)
        return {"amazon": len(amazon), "unrequested": len(amazon) - len(ages),
                **{name: sum(age <= hours for age in ages) / len(amazon) if amazon else None
                   for name, hours in [("within_45m", .75), *((f"within_{h}h", h) for h in (1, 2, 3, 12))]},
                "oldest_h": round(max(ages), 2) if ages else None}

    def request_coverage(self, deals):
        """Whole-set and condition-specific paid request ages."""
        return {**self.request_age(deals),
                "new": self.request_age([d for d in deals if not verify.is_used_deal(d)]),
                "used": self.request_age([d for d in deals if verify.is_used_deal(d)])}

    def latest_request(self, key):
        return max((s for s in self.samples.get(key, [])
                    if (at := util.parse_iso(s["requested_at"])) is not None and at <= self.now),
                   key=lambda s: s["requested_at"], default=None)

    def record(self, deal, product, verdict, observed, label, tokens, estimated, *, dead=False, live_request=False):
        key = deal["dedupe_key"]
        samples = self.samples.setdefault(key, [])
        if self.attempted(key):
            return
        # A missing key/budget-gated call made no request and learned nothing.
        if not tokens and product is None and verdict is None:
            return
        live = live_price(deal, product) if product is not None else None
        shown = deal.get("final_price")
        stamp = util.iso(observed)
        fresh = not any(s.get("observed_at", "") >= stamp and s["reading"] for s in samples)
        reading = verdict is not None and fresh
        changed = bool(reading and not verify._price_hidden_by_promo(deal)
                       and live is not None and shown is not None and live != shown
                       and (not shown or abs(live - shown) * 100 / shown + 1e-9
                            >= self.cfg.keepa_reprice_min_pct))
        previous = next((s for s in reversed(samples) if s["reading"]), None)
        if previous and previous["live_price"] == live and previous["shown_price"] == shown:
            changed = False  # a newer timestamp alone is not a newly discovered drift
        death = bool(dead and reading)
        last_change = verify.keepa_time((product or {}).get("lastPriceChange"))
        sample = {"requested_at": util.iso(self.now), "run_key": self.run_key,
                  "answered": product is not None, "observed_at": stamp,
                  "shown_price": shown, "live_price": live, "reading": bool(reading),
                  "changed": changed, "death": death, "tokens": tokens,
                  "last_change_at": util.iso(min(last_change, observed)) if last_change else ""}
        # Per-request receipts must not be confused with folded cache costs.
        sample.update(request_tokens=tokens, estimated_tokens=estimated,
                      live=live_request, used=verify.is_used_deal(deal), regular_price=deal.get("regular_price"))
        own_change = (previous or {}).get("price_changed_at", "")
        if reading and live is not None:
            same = previous and previous.get("live_price") == sample["live_price"]
            sample["stable_since"] = (previous.get("stable_since", previous["observed_at"])
                                      if same else stamp)
            if ((previous and previous.get("live_price") is not None and not same)
                    or (not previous and shown is not None and live != shown
                        and not verify._price_hidden_by_promo(deal))):
                own_change = stamp  # any observed price change, including sub-1% changes
        sample["price_changed_at"] = own_change
        # Fold repeated cached observations without forgetting their original
        # volatility. Their additional cost still enters the daily totals.
        cached = next((s for s in samples if s["observed_at"] == stamp and s["reading"]), None)
        if cached is not None:
            sample.update(reading=True, changed=cached["changed"], death=cached["death"],
                          tokens=tokens + cached["tokens"],
                          stable_since=cached.get("stable_since", stamp),
                          price_changed_at=cached.get("price_changed_at", own_change))
            samples.remove(cached)
        samples.append(sample)
        limit = max(2, int(self.cfg.keepa_recheck_adaptive.get("history_samples", 12)))
        del samples[:-limit]
        values = {"tokens": tokens, "estimated_tokens": estimated, "readings": int(reading),
                  "changed": int(changed), "deaths": int(death), "useful": int(changed or death)}
        # Idle checks belong to their visibility class; photos remain separate.
        label = label.removeprefix("idle_")
        with self.db.conn:
            self.db.conn.execute("INSERT OR REPLACE INTO keepa_recheck_history VALUES (?,?)",
                                 (key, json.dumps(samples, separators=(",", ":"))))
            self.db.conn.execute(
                "INSERT INTO keepa_recheck_yield VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(day,class) DO UPDATE SET " + ",".join(
                    f"{k}={k}+excluded.{k}" for k in COUNTS),
                (self.now.astimezone(timezone.utc).date().isoformat(), label,
                 *(values[k] for k in COUNTS)))
        if not self.db.meta_get("keepa_recheck_yield_since"):
            self.db.meta_set("keepa_recheck_yield_since", util.iso(self.now))
        add_counts(self.yield_stats["overall"], values)
        add_counts(self.yield_stats["classes"].setdefault(label, empty_counts()), values)


def live_price(deal, product):
    if not verify.is_used_deal(deal):
        if verify._keepa_since_price(product.get("lastUpdate"), deal) is False:
            return None
        return verify._live_new_price(product)
    offers, order = product.get("offers"), product.get("liveOffersOrder")
    if isinstance(offers, list) and isinstance(order, list):
        stats = product.get("stats")
        stamp = stats.get("lastOffersUpdate") if isinstance(stats, dict) else None
        if product.get("offersSuccessful") is False or verify._keepa_since_price(stamp, deal) is not True:
            return None
        prices = [verify._offer_total(offers[i]) for i in order
                  if type(i) is int and 0 <= i < len(offers) and isinstance(offers[i], dict)
                  and offers[i].get("condition") in verify._USED_OFFER_CONDITIONS]
        return None if None in prices else min(prices, default=None)
    stats = product.get("stats")
    current = stats.get("current") if isinstance(stats, dict) else None
    return min((p for i in (9, 2) if (p := verify._current_price(current, i)) is not None), default=None)


class Schedule:
    def __init__(self, history, picks, wall):
        self.history = history
        self.cfg, self.now = history.cfg, history.now
        self.options = self.cfg.keepa_recheck_adaptive
        self.enabled = bool(self.options.get("enabled", False))
        self.picks, self.wall = picks, wall
        self.plan = None

    def probability(self, deal, base):
        prior = min(.99, max(.01, float(self.options.get("prior_probability", .15))))
        strength = max(1, float(self.options.get("prior_readings", 4)))
        samples = [s for s in self.history.samples.get(deal["dedupe_key"], []) if s["reading"]]
        p = (prior * strength + sum(s["changed"] or s["death"] for s in samples)) / (strength + len(samples))
        changes = [util.parse_iso(s["last_change_at"]) for s in samples if s["last_change_at"]]
        if changes and self.now - max(changes) <= timedelta(hours=base):
            p = max(p, prior)  # Keepa recently saw a change; don't call it stable yet.
        return p

    def max_age(self, deal, name):
        if self.plan is not None:
            return self.plan.target(deal)
        used = verify.is_used_deal(deal)
        hide = self.cfg.keepa_show_max_unconfirmed_used_h if used else self.cfg.keepa_show_max_unconfirmed_h
        limit = float(self.options.get("max_used_h" if used else "max_" + name + "_h",
                                       12 if used or name == "rest" else 1 if name == "top" else 2))
        if not used and name == "top":
            limit = min(limit, self.cfg.keepa_pick_max_unconfirmed_h)
        if not used and deal["dedupe_key"] in self.wall:
            limit = min(limit, self.cfg.keepa_recheck_wall_max_age_h)
        return min(hide, limit)

    def cadence(self, deal, name, base):
        if self.plan is not None and name != "suspect":
            # Leave one forecast gap to make the maximum age attainable.
            return max(self.cfg.keepa_recheck_interval_minutes / 60,
                       self.plan.target(deal) - self.plan.gap / 60)
        if not self.enabled or name == "suspect":
            return base
        if not verify.is_used_deal(deal) and (
                (name == "top" and not self.cfg.keepa_recheck_top_h) or
                (name == "wall" and not self.cfg.keepa_recheck_wall_h)):
            return min(base, self.max_age(deal, name) - self.cfg.keepa_recheck_interval_minutes / 60
                       ) if deal["dedupe_key"] in self.wall else base
        prior = min(.99, max(.01, float(self.options.get("prior_probability", .15))))
        low = min(1, max(0, float(self.options.get("volatile_multiplier", .67))))
        high = max(1, float(self.options.get("stable_multiplier", 2)))
        factor = min(high, max(low, prior / self.probability(deal, base)))
        minimum = max(self.cfg.keepa_recheck_interval_minutes / 60,
                      float(self.options.get("min_used_h" if verify.is_used_deal(deal) else "min_new_h",
                                             3 if verify.is_used_deal(deal) else .5)))
        margin = self.cfg.keepa_recheck_interval_minutes / 60
        if verify.is_used_deal(deal):
            margin = max(margin, float(self.options.get("used_deadline_margin_h", 2)))
        maximum = max(0, self.max_age(deal, name) - margin)
        return min(maximum, max(minimum, base * factor))

    def score(self, deal, base, last, cost):
        p = self.probability(deal, base)
        age = max(0, (self.now - last).total_seconds() / 3600) if last else base
        probability = 1 - (1 - p) ** (age / max(base, .01))
        positions = self.picks if deal["dedupe_key"] in self.picks else self.wall
        position = positions.get(deal["dedupe_key"])
        weight = 1 + (max(0, float(self.options.get("visibility_boost", 1)))
                      * (1 - position / len(positions)) if position is not None else 0)
        return probability * weight / cost

    def batch(self, deals, size, name, base_hours, last_check, cost):
        if not self.enabled or name == "suspect":
            return deals[:size]
        # Hard deadlines lead, then a reserved oldest-visible sample plus value.
        oldest = sorted(deals, key=lambda d: (util.iso(last_check(d)),
                        -self.score(d, base_hours[d["dedupe_key"]], last_check(d), cost)))
        urgent = [d for d in oldest if last_check(d) is None or
                  (self.now - last_check(d)).total_seconds() / 3600 >=
                  self.max_age(d, name) - self.cfg.keepa_recheck_interval_minutes / 60]
        selected = urgent[:size]
        share = min(1, max(0, float(self.options.get("exploration_share", .1))))
        visible = [d for d in oldest if d["dedupe_key"] in self.picks or d["dedupe_key"] in self.wall]
        quota = min(size - len(selected), math.ceil(size * share))
        exploration = [d for d in visible if d not in selected][:quota]
        values = sorted((d for d in deals if d not in selected and d not in exploration),
                        key=lambda d: (-self.score(d, base_hours[d["dedupe_key"]], last_check(d), cost),
                                       util.iso(last_check(d))))
        return selected + values[:size - len(selected) - len(exploration)] + exploration


def report_data(db, now):
    since = db.meta_get("keepa_recheck_yield_since")
    if not since:
        return {"unavailable": "no recheck observations yet"}
    today = now.astimezone(timezone.utc).date()
    result = {"observed_since": since}
    for name, days in (("yesterday", 1), ("week", 7)):
        values = {"overall": empty_counts(), "classes": {}}
        for row in db.conn.execute(
                "SELECT * FROM keepa_recheck_yield WHERE day>=? AND day<?",
                ((today - timedelta(days=days)).isoformat(), today.isoformat())):
            add_counts(values["overall"], row)
            add_counts(values["classes"].setdefault(row["class"], empty_counts()), row)
        result[name] = values if values["classes"] else {"unavailable": "no recheck observations in period"}
    return result
