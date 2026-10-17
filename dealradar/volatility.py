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
    def __init__(self, history, deals, picks, wall, gap, *, cadence_minutes=None, surplus=0):
        from .publish.social import plenty_of_stock
        self.history, self.cfg, self.now = history, history.cfg, history.now
        self.options = self.cfg.keepa_volatility
        self.picks, self.wall, self.gap = picks, wall, gap
        from . import click_price
        self.published = set()
        click_visible = set()
        if self.cfg.keepa_click_price_rule == "on":
            for name, fallback in ((click_price.PICKS_META, picks), (click_price.WALL_META, wall)):
                try:
                    saved = json.loads(history.db.meta_get(name) or "null")
                except (TypeError, ValueError):
                    saved = None
                positions = enumerate(saved) if isinstance(saved, list) else ((i, k) for k, i in fallback.items())
                self.published.update(k for i, k in positions if isinstance(k, str)
                                      and (name == click_price.PICKS_META or i < click_price.WALL_CHECKS))
            try:
                saved = json.loads(history.db.meta_get(click_price.VISIBLE_META) or "[]")
            except (TypeError, ValueError):
                saved = []
            click_visible = set(picks) | {k for k, i in wall.items() if i < click_price.WALL_CHECKS}
            click_visible.update(self.published)
            if isinstance(saved, list):
                click_visible.update(k for k in saved if isinstance(k, str))
        self.cadence_minutes = cadence_minutes if cadence_minutes is not None else gap
        self.rows = {d["dedupe_key"]: d for d in deals if d.get("status") == "alive"
                     and d.get("asin") and verify.is_amazon_deal(d)
                     and rank.is_publishable(d, self.cfg, require_critic=False,
                         min_pct=min(self.cfg.min_pct, self.cfg.site_show_from_pct))[0]}
        self.facts, self.visibility, self.costs = {}, {}, {}
        from .keepa_rechecks import CallHistory
        self.calls = CallHistory(history.db, self.now)
        samples_by_mode = defaultdict(list)
        samples_by_deal_mode = defaultdict(list)
        for key, samples in history.samples.items():
            for s in samples:
                # Old folded samples lack per-request receipts; don't divide
                # their cumulative tokens by one and invent an average.
                if (not s.get("buybox") and s.get("request_tokens", -1) >= 0 and s.get("estimated_tokens") == 0
                        and (at := util.parse_iso(s.get("requested_at") or "")) is not None and at <= self.now):
                    mode = bool(s.get("used")), bool(s.get("live"))
                    samples_by_mode[mode].append(s["request_tokens"])
                    samples_by_deal_mode[key, *mode].append(s["request_tokens"])
        self.cost_sources = {}
        for used in (False, True):
            for live in (False, True):
                mode = used, live
                name = f"{'used' if used else 'new'}{'_live' if live else ''}"
                self.cost_sources[name] = "deal_receipts" if samples_by_mode[mode] else "ceiling"
                if not samples_by_mode[mode]:
                    for s in self.calls.samples:
                        if not s.get("buybox") and (s.get("used"), s.get("live")) == mode and s.get("tokens") is not None:
                            samples_by_mode[mode].extend([s["tokens"] / s["count"]] * s["count"])
                    if samples_by_mode[mode]:
                        self.cost_sources[name] = "shared_call_receipts"
        self.cost_samples = {f"{'used' if u else 'new'}{'_live' if l else ''}": len(v)
                             for (u, l), v in samples_by_mode.items() if v}
        for key, deal in self.rows.items():
            observations = history.db.price_observations(key, self.now - timedelta(days=90), self.now)
            observations += [dict(s, price=s.get("shown_price") if verify._price_hidden_by_promo(deal)
                                 else s.get("live_price")) for s in history.samples.get(key, [])
                             if s.get("reading")]
            self.facts[key] = tier(deal, observations, self.now,
                                  plenty_of_stock=plenty_of_stock(history.db, deal, self.cfg))
            self.visibility[key] = ("top" if key in picks or (key in click_visible and key not in wall) else "wall" if key in wall else
                                    "hidden" if verify.price_unconfirmed(deal, self.cfg, self.now) else "rest")
            used = verify.is_used_deal(deal)
            box = ((wall.get(key, self.cfg.keepa_buybox_top_n) < self.cfg.keepa_buybox_top_n or key in click_visible)
                   and not used and not verify._price_hidden_by_promo(deal))
            live = bool(self.cfg.keepa_recheck_top_live and (key in picks or box))
            receipts = samples_by_deal_mode[key, used, live] or samples_by_mode[used, live]
            # Offers=20: 1 + 6 per started block of ten offers, at most 13.
            self.costs[key] = (sum(receipts) / len(receipts) if receipts else
                               (verify.USED_RECHECK_TOKENS if used else 1) + live)
            if box:
                self.costs[key] += 2
        self.targets = {t: {v: max(1 / 60, float(self.options.get("targets", {}).get(t, {}).get(v, h)))
                            for v, h in values.items()} for t, values in DEFAULT_TARGETS.items()}
        self.discovery, self.discovery_source = self.discovery_rate()
        self.surplus = surplus
        self.granted = max(0, self.cfg.keepa_tokens_per_minute - self.discovery) + surplus / gap
        self.demand = {t: sum(self.costs[k] / (60 * self.targets[t][self.visibility[k]])
                             for k in self.rows if self.facts[k]["tier"] == t) for t in TIERS}
        self.factors = stretch(self.demand, self.granted,
                              self.options.get("stretch_limits", {"SLOW": 4, "NORMAL": 4 / 3}))
        self.clicked = set()
        self.click_price_extra_per_minute = sum(2 / (60 * self.targets[self.facts[k]["tier"]][self.visibility[k]])
            for k in click_visible if k in self.rows and not verify.is_used_deal(self.rows[k])
            and not verify._price_hidden_by_promo(self.rows[k]))
        if clicks.enabled(self.cfg):
            end = int(self.now.timestamp() // 3600)
            hours = max(1, int(self.options.get("click_lookback_hours", 6)))
            public = dict(history.db.conn.execute(
                "SELECT c.deal_key,SUM(c.count) FROM deal_clicks c JOIN deal_click_coverage h USING(hour) "
                "WHERE c.hour>=? AND c.hour<? GROUP BY c.deal_key HAVING SUM(c.count)>0", (end - hours, end)))
            self.clicked = {k for k in self.rows if hashlib.sha256(k.encode()).hexdigest()[:16] in public}
        self.boosted = set()

    def seconds_per_call(self, used, live):
        options = self.options.get("batching", {})
        samples = sorted(s["seconds"] for s in self.calls.samples
                         if (s.get("used"), s.get("live")) == (used, live))
        observed = samples[math.ceil(len(samples) * .9) - 1] if samples else 0
        fallback = float(options.get("offers_seconds" if used else "stats_seconds", 6 if used else 1))
        return max(1, fallback, observed) * max(1, float(options.get("latency_margin", 1.2)))

    def batches(self, entries, deadlines, *, available, used_left, seconds, overflow=None,
                recovery_left=None):
        """Select by rank first; then pack selected rows by API mode.

        Each entry carries the existing lane's gates/label. Call packing may
        cross labels, but never the overdue-top or discovery/recovery barriers.
        Estimates size the plan; the caller still enforces every actual gate.
        """
        from .keepa_budget import MIN_FOR_DEAL_QUERY
        margin = max(0, float(self.options.get("batching", {}).get("time_margin_seconds", 5)))
        seconds = max(0, seconds - margin)
        left = available
        groups, selected, requests = {}, set(), set()
        for deal, options in entries:
            need, ceiling, size, label, live, used, idle = options
            key = deal["dedupe_key"]
            request = ((str(deal["asin"]).strip().upper(), used, live, label == "buybox_top_new",
                        key in self.published, deadlines.get(key) == "top")
                       if self.cfg.keepa_unique_asin_rechecks_enabled else key)
            if request in requests:
                continue
            hidden = label.startswith("hidden_")
            surplus = label.startswith("surplus_")
            reserve = max(MIN_FOR_DEAL_QUERY, self.cfg.keepa_reserve_tokens) if hidden else MIN_FOR_DEAL_QUERY
            cost = min(ceiling, max(0, self.costs.get(key, ceiling)))
            mode = (key in self.published, deadlines.get(key) == "top", used, live,
                    label == "buybox_top_new", surplus)
            group = groups.get(mode)
            # The final published call may take compatible following rows;
            # never let hidden rows hitch ahead of another published mode.
            if group is None and not mode[0] and groups:
                previous = next(reversed(groups))
                if previous[0] and previous[1:] == mode[1:]:
                    mode, group = previous, groups[previous]
            starts_call = group is None or len(group[0]) % size == 0
            if left is not None and ((left < need and (
                    self.cfg.keepa_click_price_rule != "on" or starts_call)) or left - cost < reserve):
                continue
            if used and not surplus and used_left < ceiling:
                continue
            if hidden and recovery_left[used] <= 0:
                continue
            if (idle or surplus) and (overflow is None or overflow < ceiling):
                continue
            # No non-deadline work can hitch a ride ahead of another mode's
            # overdue top batch. Within that boundary, labels do not split calls.
            extra = self.seconds_per_call(used, live) if starts_call else 0
            if extra > seconds:
                continue
            seconds -= extra
            if left is not None:
                left -= cost
            if used and not surplus:
                used_left -= ceiling
            if hidden:
                recovery_left[used] -= 1
            if overflow is not None and (self.cfg.keepa_click_price_rule == "on" or idle or surplus):
                overflow = max(0, overflow - cost)
            if group is None:
                group = groups[mode] = ([], *options)
            group[0].append(deal)
            selected.add(key)
            requests.add(request)
        return list(groups.values()), len({d["dedupe_key"] for d, _ in entries} - selected)

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
                  "cost_samples": self.cost_samples, "cost_sources": self.cost_sources,
                  "click_boosted": len(self.boosted), "tiers": {}}
        if self.cfg.keepa_recheck_surplus:
            result["surplus_granted"] = self.surplus
        if self.cfg.keepa_click_price_rule != "off":
            result["click_price_extra_per_minute"] = self.click_price_extra_per_minute
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
