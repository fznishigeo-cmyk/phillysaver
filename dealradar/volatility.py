"""Request scheduling from stored facts only; never a stock/price assertion.

FAST evidence wins over SLOW. Stability requires observations, not listing
age. Targets describe paid request age; Keepa's observation age is reported
separately and is never advanced by this planner. No display/social policy
or API budget is changed here.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import timedelta

from . import clicks, rank, util, verify
from .conditions import normalize_conditions, USED_CONDITIONS

TIERS = ("FAST", "NORMAL", "SLOW")
VISIBILITY = ("top", "wall", "rest", "hidden")
DEFAULT_TARGETS = {
    "FAST": dict(top=.5, wall=.5, rest=6, hidden=48),
    "NORMAL": dict(top=.75, wall=2, rest=8, hidden=72),
    "SLOW": dict(top=2, wall=6, rest=12, hidden=72),
}
SCARCE = re.compile(r"\b(?:only\s+\d+\s+left|\d+\s+left\s+in\s+stock|last\s+one|"
                    r"limited\s+(?:quantity|quantities|stock)|"
                    r"(?:single|one|only|select)\s+(?:size|colou?r|style)s?|"
                    r"(?:size|colou?r|style)\s+\S+\s+only)\b", re.I)


def tier(deal, observations, now, *, plenty_of_stock=False):
    """Pure classifier returning a tier and all winning reasons.

    Observations are same-offer facts: price/observed_at, optionally
    regular_price and stable_since. A promo needs two distinct dated reads
    of its current regular price. Seven-day stability needs an unchanged
    observed price span (or a bounded-history stable_since anchor).
    """
    conditions = set(normalize_conditions(deal.get("conditions")))
    words = " ".join([str(deal.get("item") or ""), str(deal.get("store") or ""),
                      *map(str, conditions), *map(str, deal.get("flags") or []),
                      *map(str, deal.get("how") or []), *map(str, deal.get("stack_steps") or [])])
    words = words.replace("_", " ")
    fast = sorted(conditions & USED_CONDITIONS)
    if re.search(r"\bwarehouse\b", words, re.I):
        fast.append("warehouse")
    if "limited_qty" in conditions or SCARCE.search(words):
        fast.append("limited_quantity_or_variant")
    for label, pattern in (("lightning", r"\blightning\b"),
                           ("clearance", r"\b(?:clearance|closeout|liquidation)\b"),
                           ("pricing_error", r"\bpric(?:e|ing)\s+(?:error|mistake)\b")):
        if re.search(pattern, words, re.I):
            fast.append(label)
    points = sorted((dict(p, at=at) for p in observations
                     if (at := util.parse_iso(p.get("observed_at") or "")) is not None
                     and at <= now and isinstance(p.get("price"), (float, int))
                     and math.isfinite(p["price"]) and p["price"] >= 0), key=lambda p: p["at"])
    changes = [b["at"] for a, b in zip(points, points[1:]) if a["price"] != b["price"]]
    changes += [p["at"] for p in points if p.get("changed")]
    changes += [at for p in points if (at := util.parse_iso(p.get("price_changed_at") or ""))
                is not None and at <= now]
    if any(now - at <= timedelta(hours=48) for at in changes):
        fast.append("observed_price_change_48h")
    if fast:
        return {"tier": "FAST", "reasons": sorted(set(fast))}
    slow = []
    if plenty_of_stock:
        slow.append("plenty_of_stock")
    regular = deal.get("regular_price")
    bases = [p for p in points if p.get("regular_price") is not None]
    promo = bool(deal.get("codes") or re.search(
        r"\b(?:coupon|promo(?:tion)?|code)\b", words, re.I))
    if (promo and regular and regular > 0 and len({p["at"] for p in bases}) >= 2
            and all(p["regular_price"] == regular for p in bases)):
        slow.append("promo_stable_list_price")
    if points:
        recent = [p for p in points if not changes or p["at"] >= max(changes)]
        anchors = [p["at"] for p in recent]
        anchors += [at for p in recent if (at := util.parse_iso(p.get("stable_since") or ""))
                    is not None and at <= p["at"] and (not changes or at >= max(changes))]
        if (len({p["at"] for p in recent}) >= 2 or len(anchors) > len(recent)) \
                and points[-1]["at"] - min(anchors) >= timedelta(days=7):
            slow.append("observed_unchanged_7d")
    return {"tier": "SLOW" if slow else "NORMAL", "reasons": slow or ["no_fast_or_stable_evidence"]}


def stretch(demand, granted, limits):
    """Proportionally stretch each tier, SLOW then NORMAL then FAST.

    Configured SLOW/NORMAL ceilings preserve useful service for those tiers;
    FAST absorbs the remaining shortfall last. No target is shortened.
    """
    factors = dict.fromkeys(TIERS, 1.0)
    for name in reversed(TIERS):
        total = sum(demand[t] / factors[t] for t in TIERS)
        if total <= granted:
            break
        other = total - demand[name] / factors[name]
        available = max(0, granted - other)
        wanted = demand[name] / available if available else math.inf
        factors[name] = max(1, min(float(limits.get(name, math.inf)), wanted)) if demand[name] else 1
    remaining = sum(demand[t] / factors[t] for t in TIERS)
    if remaining > granted:
        # An exceptional workload can exhaust even the protected NORMAL
        # allocation. Report the unavoidable shortfall as longer targets.
        extra = remaining / granted if granted else math.inf
        factors = {t: f * extra if demand[t] else f for t, f in factors.items()}
    return factors


class Plan:
    def __init__(self, history, deals, picks, wall, gap, *, cadence_minutes=None):
        from .publish.social import plenty_of_stock
        self.history, self.cfg, self.now = history, history.cfg, history.now
        self.options = self.cfg.keepa_volatility
        self.picks, self.wall, self.gap = picks, wall, gap
        self.cadence_minutes = cadence_minutes if cadence_minutes is not None else gap
        self.rows = {d["dedupe_key"]: d for d in deals if d.get("status") == "alive"
                     and d.get("asin") and verify.is_amazon_deal(d)
                     and rank.is_publishable(d, self.cfg, require_critic=False,
                         min_pct=min(self.cfg.min_pct, self.cfg.site_show_from_pct))[0]}
        self.facts, self.visibility, self.costs = {}, {}, {}
        samples_by_mode = defaultdict(list)
        samples_by_deal_mode = defaultdict(list)
        for key, samples in history.samples.items():
            for s in samples:
                # Old folded samples lack per-request receipts; don't divide
                # their cumulative tokens by one and invent an average.
                if (s.get("request_tokens", -1) >= 0 and s.get("estimated_tokens") == 0
                        and (at := util.parse_iso(s.get("requested_at") or "")) is not None and at <= self.now):
                    mode = bool(s.get("used")), bool(s.get("live"))
                    samples_by_mode[mode].append(s["request_tokens"])
                    samples_by_deal_mode[key, *mode].append(s["request_tokens"])
        self.cost_samples = {f"{'used' if u else 'new'}{'_live' if l else ''}": len(v)
                             for (u, l), v in samples_by_mode.items()}
        for key, deal in self.rows.items():
            observations = history.db.price_observations(key, self.now - timedelta(days=90), self.now)
            observations += [dict(s, price=s.get("shown_price") if verify._price_hidden_by_promo(deal)
                                 else s.get("live_price")) for s in history.samples.get(key, [])
                             if s.get("reading")]
            self.facts[key] = tier(deal, observations, self.now,
                                  plenty_of_stock=plenty_of_stock(history.db, deal, self.cfg))
            self.visibility[key] = ("top" if key in picks else "wall" if key in wall else
                                    "hidden" if verify.price_unconfirmed(deal, self.cfg, self.now) else "rest")
            used = verify.is_used_deal(deal)
            live = bool(self.cfg.keepa_recheck_top_live and key in picks)
            receipts = samples_by_deal_mode[key, used, live] or samples_by_mode[used, live]
            # Offers=20: 1 + 6 per started block of ten offers, at most 13.
            self.costs[key] = (sum(receipts) / len(receipts) if receipts else
                               (verify.USED_RECHECK_TOKENS if used else 1) + live)
        self.targets = {t: {v: max(1 / 60, float(self.options.get("targets", {}).get(t, {}).get(v, h)))
                            for v, h in values.items()} for t, values in DEFAULT_TARGETS.items()}
        self.discovery, self.discovery_source = self.discovery_rate()
        self.granted = max(0, self.cfg.keepa_tokens_per_minute - self.discovery)
        self.demand = {t: sum(self.costs[k] / (60 * self.targets[t][self.visibility[k]])
                             for k in self.rows if self.facts[k]["tier"] == t) for t in TIERS}
        self.factors = stretch(self.demand, self.granted,
                              self.options.get("stretch_limits", {"SLOW": 4, "NORMAL": 4 / 3}))
        self.clicked = set()
        if clicks.enabled(self.cfg):
            end = int(self.now.timestamp() // 3600)
            hours = max(1, int(self.options.get("click_lookback_hours", 6)))
            public = dict(history.db.conn.execute(
                "SELECT c.deal_key,SUM(c.count) FROM deal_clicks c JOIN deal_click_coverage h USING(hour) "
                "WHERE c.hour>=? AND c.hour<? GROUP BY c.deal_key HAVING SUM(c.count)>0", (end - hours, end)))
            self.clicked = {k for k in self.rows if hashlib.sha256(k.encode()).hexdigest()[:16] in public}
        self.boosted = set()

    def prepare_clicks(self):
        """Freeze the allowance per run key, including process/retry restarts."""
        if not clicks.enabled(self.cfg):
            return
        run = self.history.run_key or util.iso(self.now)
        try:
            saved = json.loads(self.history.db.meta_get("keepa_click_boost") or "{}")
        except ValueError:
            saved = {}
        if isinstance(saved, dict) and saved.get("run_key") == run:
            self.boosted = set(saved.get("keys", [])) & self.clicked
        else:
            self.order(list(self.rows.values()), {}, select_clicks=True)
            self.history.db.meta_set("keepa_click_boost", json.dumps(
                {"run_key": run, "keys": sorted(self.boosted)}))

    def discovery_rate(self):
        count = max(2, int(self.options.get("discovery_history_runs", 12)))
        rows = list(self.history.db.conn.execute(
            "SELECT started_at,metrics FROM efficiency_runs WHERE pending=0 AND started_at<? "
            "ORDER BY started_at DESC LIMIT ?", (util.iso(self.now), count)))
        values = []
        for row in rows:
            try:
                value = json.loads(row["metrics"] or "{}").get("feed_tokens")
            except ValueError:
                continue
            if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
                values.append((util.parse_iso(row["started_at"]), value))
        if len(values) >= 2:
            minutes = (values[0][0] - values[-1][0]).total_seconds() / 60
            if minutes > 0:
                return sum(v for _, v in values[:-1]) / minutes, "observed_feed_receipts"
        from .fetch.keepa import DEAL_PAGE_COST
        pages = (self.cfg.keepa_amazon_pages_per_pull + self.cfg.keepa_warehouse_pages_per_pull
                 + len(self.cfg.keepa_focus_categories) + self.cfg.keepa_overflow_extra_pages_max)
        return pages * DEAL_PAGE_COST / self.cadence_minutes, "configured_feed_ceiling"

    def target(self, deal, *, full=False):
        key = deal["dedupe_key"]
        name = self.facts.get(key, {"tier": "NORMAL"})["tier"]
        return self.targets[name][self.visibility.get(key, "rest")] * (1 if full else self.factors[name])

    def last_check(self, deal):
        latest = self.history.latest_request(deal["dedupe_key"])
        if latest:
            return util.parse_iso(latest["requested_at"]) if latest.get("answered", latest["reading"]) else None
        # Legacy paid checks lack request history. Bootstrap from that clock,
        # never a feed/photo confirmation, and still report request age unknown.
        return util.parse_iso(deal.get("price_rechecked_at") or "")

    def order(self, deals, deadlines, *, select_clicks=False):
        def value(d):
            key = d["dedupe_key"]
            age = (self.now - (self.last_check(d) or util.parse_iso(d.get("first_seen_at")) or self.now)
                   ).total_seconds() / 3600
            positions = self.picks if key in self.picks else self.wall
            position = positions.get(key)
            boost = max(0, float(self.cfg.keepa_recheck_adaptive.get("visibility_boost", 1)))
            weight = 1 + (boost * (1 - position / len(positions)) if position is not None else 0)
            return age / self.target(d) * weight / max(1, self.costs.get(key, 1))
        # A bounded boost moves clicked deals ahead of their peers, FAST
        # clicks first. The absolute overdue-top deadline always wins.
        clicked = sorted((d for d in deals if d["dedupe_key"] in self.clicked),
                         key=lambda d: (TIERS.index(self.facts[d["dedupe_key"]]["tier"]), -value(d), d["dedupe_key"]))
        cap = max(0, int(self.options.get("click_boost_per_run", 20)))
        if select_clicks:
            self.boosted = {d["dedupe_key"] for d in clicked[:cap]}
        ordered = sorted(deals, key=lambda d: (deadlines.get(d["dedupe_key"]) != "top",
            d["dedupe_key"] not in self.boosted,
            TIERS.index(self.facts[d["dedupe_key"]]["tier"]) if d["dedupe_key"] in self.boosted else 0,
            -value(d), d["dedupe_key"]))
        # Retain oldest-visible exploration within each absolute priority.
        share = min(1, max(0, float(self.cfg.keepa_recheck_adaptive.get("exploration_share", .1))))
        if share:
            stride = max(1, round(1 / share))
            for i in range(stride - 1, len(ordered), stride):
                peers = [d for d in ordered[i:] if d["dedupe_key"] not in self.boosted
                         and (d["dedupe_key"] in self.picks or d["dedupe_key"] in self.wall)
                         and (deadlines.get(d["dedupe_key"]) == "top") ==
                             (deadlines.get(ordered[i]["dedupe_key"]) == "top")]
                if peers and ordered[i]["dedupe_key"] not in self.boosted:
                    oldest = min(peers, key=lambda d: (util.iso(self.last_check(d)), d["dedupe_key"]))
                    ordered.remove(oldest)
                    ordered.insert(i, oldest)
        return ordered

    def coverage(self, deals):
        result = {}
        for name in TIERS:
            rows = [d for d in deals if self.facts.get(d["dedupe_key"], {}).get("tier") == name]
            result[name] = {**verify.confirmation_coverage(rows, self.now),
                            "request_age": self.history.request_coverage(rows)}
        return result

    def report(self, deals):
        current = {d["dedupe_key"]: d for d in deals}
        result = {"needed_per_minute": sum(self.demand.values()) + self.discovery,
                  "granted_per_minute": self.cfg.keepa_tokens_per_minute,
                  "rechecks_granted_per_minute": self.granted,
                  "allocated_per_minute": sum(self.demand[t] / self.factors[t] for t in TIERS),
                  "discovery_per_minute": self.discovery, "discovery_source": self.discovery_source,
                  "cost_samples": self.cost_samples, "click_boosted": len(self.boosted), "tiers": {}}
        for name in TIERS:
            classes = {}
            for visibility in VISIBILITY:
                rows = [current[k] for k in self.rows if k in current and current[k].get("status") == "alive"
                        and self.facts[k]["tier"] == name and self.visibility[k] == visibility]
                full = self.targets[name][visibility]
                target = full * self.factors[name]
                def ages(request):
                    stamps = [(self.history.latest_request(d["dedupe_key"]) or {}).get("requested_at")
                              if request else util.iso(verify.price_confirmed_at(d)) for d in rows]
                    known = [(self.now - at).total_seconds() / 3600 for stamp in stamps
                             if (at := util.parse_iso(stamp or "")) is not None and at <= self.now]
                    return {"unknown": len(rows) - len(known), "oldest_h": max(known, default=None),
                            "within_target": sum(a <= target for a in known) / len(rows) if rows else None,
                            "within_full_target": sum(a <= full for a in known) / len(rows) if rows else None}
                keys = [d["dedupe_key"] for d in rows]
                classes[visibility] = {"count": len(rows), "target_h": full,
                    "effective_target_h": target if math.isfinite(target) else None,
                    "needed_per_minute": sum(self.costs[k] / (60 * full) for k in keys),
                    "mean_cost": sum(self.costs[k] for k in keys) / len(keys) if keys else None,
                    "reasons": dict(Counter(r for k in keys for r in self.facts[k]["reasons"])),
                    "request_age": ages(True), "keepa_age": ages(False)}
            result["tiers"][name] = {"stretch": self.factors[name] if math.isfinite(self.factors[name]) else None,
                                      "classes": classes}
        return result
