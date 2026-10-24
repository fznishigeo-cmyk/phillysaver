"""Opt-in NEW discovery on the recheck scheduler's predicted overflow only.

Category searches/parent lookups are paid once and cached as names/IDs only.
No popularity facts enter posts or public output. All prices use the normal
Keepa mapper, evidence verifier, savings, critic and click-price gates.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time

import httpx

from .. import util
from ..appeal import matches
from ..errors import DeadlineExceeded
from ..http_client import extract_asin
from ..keepa_categories import NAMES_META, cached_meta
from ..savings import clears_reference_floor
from ..volatility import discovery_surplus

STATE_META = "keepa_focus_finder_state"
PATHS_META = "keepa_focus_category_paths"
SEARCH_META = "keepa_focus_category_searches"
TREE_META = "keepa_focus_category_tree"
SEARCH_URL = "https://api.keepa.com/search"
BUY_BOX_NEW = 18


def empty_summary(cfg):
    return {"pages": 0, "tokens": 0, "estimated_tokens": 0, "candidates": 0, "kept": 0,
            "dropped": dict.fromkeys(("adult", "price", "drop", "rank", "rating", "dup", "cap"), 0),
            "published_by_group": dict.fromkeys(cfg.keepa_focus_finder["groups"], 0),
            "idle_before_estimate": 0, "idle_after_estimate": 0}


def published_groups(deals, groups, cfg):
    result = dict.fromkeys(cfg.keepa_focus_finder["groups"], 0)
    for asin in {str(d.get("asin") or extract_asin(d.get("url") or d.get("canonical_url") or "")).upper()
                 for d in deals if d.get("status") == "alive"}:
        if asin in groups:
            result[groups[asin]] += 1
    return result


def _name(value):
    return " ".join(str(value or "").casefold().split())


def adult(row, cfg):
    excludes = (cfg.front_page.get("appeal", {}).get("want") or {}).get("exclude_words", [])
    return any(row.get(k) not in (None, False, 0, "false", "0", "") for k in
               ("isAdultProduct", "isAdult", "adult", "isErotic")) or matches(row.get("title"), excludes)


class Finder:
    def __init__(self, adapter, pull, cfg, source, seen, out, floors):
        self.adapter, self.pull, self.cfg = adapter, pull, cfg
        self.source, self.seen, self.out, self.floors = source, seen, out, floors
        self.options = cfg.keepa_focus_finder
        self.summary = adapter.focus_summary
        self.paths = cached_meta(pull.db, PATHS_META)
        self.searches = cached_meta(pull.db, SEARCH_META)
        self.tree = cached_meta(pull.db, TREE_META)
        self.state = cached_meta(pull.db, STATE_META)
        row = pull.db.conn.execute(
            "SELECT run_key FROM run_stats WHERE started_at=? AND kind IN ('fast','hourly') LIMIT 1",
            (util.iso(pull.now),)).fetchone() if pull.db is not None else None
        run = row["run_key"] if row else util.iso(pull.now)
        if self.state.get("run_key") != run:
            self.state = {"run_key": run, "next_group": self.state.get("next_group", 0),
                          "positions": self.state.get("positions", {}),
                          "query_pages": self.state.get("query_pages", {}),
                          "pages": 0, "tokens": 0, "new": 0}
        self.live = {str(d.get("asin") or extract_asin(d.get("canonical_url") or "")).upper()
                     for d in pull.db.all_deals(status="alive")} if pull.db is not None else set()

    def save(self):
        if self.pull.db is not None:
            self.pull.db.meta_set(STATE_META, json.dumps(self.state))

    def can_spend(self, cost):
        p = self.pull
        # The lane uses the same frozen run allowance after a restart, and
        # rechecks the shared forecast/balance before EVERY request.
        return (self.state["tokens"] + cost <= self.state["allowance"] + 1e-9
                and discovery_surplus(p.budget, self.cfg, self.gap, p.now) >= cost
                and (p.deadline is None or time.monotonic() + p.margin_s + p.interval_s < p.deadline))

    def paid(self, cost, request):
        before = self.adapter.token_usage.accounted
        estimated = self.adapter.token_usage.estimated
        self.state["tokens"] += cost
        self.save()  # lost responses/restarts cannot spend the balance twice
        try:
            return request()
        finally:
            actual = self.adapter.token_usage.accounted - before
            self.state["tokens"] += actual - cost
            self.summary["tokens"] += actual
            self.summary["estimated_tokens"] += self.adapter.token_usage.estimated - estimated
            self.save()

    def remember_tree(self, body):
        if not isinstance(body, dict) or not isinstance(body.get("categories"), dict):
            raise RuntimeError("keepa focus category map unavailable")
        for field in ("categories", "categoryParents"):
            for cid, cat in (body.get(field) or {}).items():
                if str(cid).isdigit() and isinstance(cat, dict) and isinstance(cat.get("name"), str):
                    self.tree[str(cid)] = {"name": cat["name"], "parent": cat.get("parent")}
                    self.pull.category_names[str(cid)] = cat["name"]
        if self.pull.db is not None:
            self.pull.db.meta_set(TREE_META, json.dumps(self.tree))
            self.pull.db.meta_set(NAMES_META, json.dumps(self.pull.category_names))

    def ancestry(self, cid):
        names, visited = [], set()
        while str(cid) != "0":
            cid = str(cid)
            if cid in visited or cid not in self.tree:
                return None
            visited.add(cid)
            cat = self.tree[cid]
            names.append(_name(cat["name"]))
            cid = cat["parent"]
        return names[::-1]

    def resolve(self, path):
        """Search a leaf, then verify the configured ancestors in order.

        /search?type=category costs 1; /category?parents=1 costs 1 per
        ten IDs. Both successful empty results and partial work are cached.
        https://keepa.com/api-docs/category-search.html
        https://keepa.com/api-docs/category-lookup.html
        """
        from .keepa import CATEGORY_URL, DEAL_PAGE_COST
        if path in self.paths:
            return self.paths[path]
        parts = [_name(n) for n in path.split(">")]
        if len(parts) == 1:
            ids = [int(cid) for cid, name in self.pull.category_names.items() if _name(name) == parts[0]]
            if ids:
                return ids
        leaf = parts[-1]
        if leaf not in self.searches:
            if not self.can_spend(1 + DEAL_PAGE_COST):
                return []
            term = " ".join(w for w in re.findall(r"\w+", leaf) if len(w) >= 3)
            body = self.paid(1, lambda: self.adapter._get(self.pull, SEARCH_URL, {
                "key": self.pull.key, "domain": 1, "type": "category", "term": term}, "focus categories", estimate=1))
            self.remember_tree(body)
            self.searches[leaf] = [cid for cid, cat in body["categories"].items()
                                   if isinstance(cat, dict) and _name(cat.get("name")) == leaf and str(cid).isdigit()]
            if self.pull.db is not None:
                self.pull.db.meta_set(SEARCH_META, json.dumps(self.searches))
        candidates = self.searches[leaf]
        missing = [cid for cid in candidates if self.ancestry(cid) is None]
        for i in range(0, len(missing), 10):
            if not self.can_spend(1 + DEAL_PAGE_COST):
                return []
            batch = missing[i:i + 10]
            body = self.paid(1, lambda: self.adapter._get(self.pull, CATEGORY_URL, {
                "key": self.pull.key, "domain": 1, "category": ",".join(batch), "parents": 1},
                "focus parents", estimate=1))
            self.remember_tree(body)
        ids = []
        for cid in candidates:
            ancestry = self.ancestry(cid)
            if ancestry is None:
                return []  # incomplete/malformed lookup retries; never cache a guessed ID
            pos = 0
            for name in ancestry:
                if pos < len(parts) and name == parts[pos]:
                    pos += 1
            if pos == len(parts) and ancestry[-1] == leaf:
                ids.append(int(cid))
        self.paths[path] = ids
        if self.pull.db is not None:
            self.pull.db.meta_set(PATHS_META, json.dumps(self.paths))
        return ids

    def drop_reason(self, row, price_type):
        from .keepa import _price_at, _avg90_price, _count_at, _dense_history
        if adult(row, self.cfg):
            return "adult"
        current = _price_at(row.get("current"), price_type)
        if current is None or not 0 < current <= self.options["max_price"]:
            return "price"
        avg = row.get("avg") or row.get("avg90")
        avg90 = _avg90_price(avg, price_type)
        if (avg90 is None or not _dense_history(avg, price_type)
                or not clears_reference_floor(current, avg90, self.cfg.min_pct)):
            return "drop"
        rank = _count_at(row.get("current"), 3)
        if rank is None or not 1 <= rank <= self.options["max_rank"]:
            return "rank"
        rating, reviews = _count_at(row.get("current"), 16), _count_at(row.get("current"), 17)
        if (rating is None or rating < self.options["min_rating"]
                or reviews is None or reviews < self.options["min_reviews"]):
            return "rating"
        return ""

    def run(self, allowed):
        from .keepa import (_TokensShort, DEAL_PAGE_COST, DEAL_PAGE_SIZE, _selection,
                            DATE_RANGE_90D, SORT_PCT_DROP)
        from ..keepa_budget import run_gap_forecast
        self.gap = run_gap_forecast(self.pull.db, self.cfg, self.pull.now)["minutes"]
        idle = discovery_surplus(self.pull.budget, self.cfg, self.gap, self.pull.now)
        self.summary["idle_before_estimate"] = idle
        self.summary["idle_after_estimate"] = idle
        if not allowed or not self.options["groups"] or not self.options["max_new_deals_per_run"]:
            return False
        self.state.setdefault("allowance", idle * self.options["idle_share"])
        self.save()
        names = list(self.options["groups"])
        slots = sum(2 * len(paths) for paths in self.options["groups"].values())
        ended, misses = set(), 0
        self.pull.overflow_only = self.pull.focus_finder = True
        try:
            while (self.state["pages"] < self.options["max_pages_per_run"]
                   and self.state["new"] < self.options["max_new_deals_per_run"]
                   and misses < slots and self.can_spend(DEAL_PAGE_COST)):
                group = names[self.state["next_group"] % len(names)]
                self.state["next_group"] = (self.state["next_group"] + 1) % len(names)
                paths = self.options["groups"][group]
                pos = self.state["positions"].get(group, 0) % (len(paths) * 2)
                self.state["positions"][group] = (pos + 1) % (len(paths) * 2)
                path, price_type = paths[pos // 2], (0, BUY_BOX_NEW)[pos % 2]
                self.save()
                marker = (path, price_type)
                if marker in ended:
                    misses += 1
                    continue
                ids = self.resolve(path)
                if not ids or not self.can_spend(DEAL_PAGE_COST):
                    ended.add(marker)
                    misses += 1
                    continue
                selection = {**_selection(price_type, (max(10, math.ceil(self.cfg.min_pct)), 100),
                                          DATE_RANGE_90D, SORT_PCT_DROP, self.options["max_rank"]),
                             "includeCategories": ids, "currentRange": [1, round(self.options["max_price"] * 100)],
                             "isFilterEnabled": True, "filterErotic": True,
                             "minRating": self.options["min_rating"], "hasReviews": True}
                query = hashlib.sha256(json.dumps(selection, sort_keys=True).encode()).hexdigest()[:16]
                page = self.state["query_pages"].get(query, 0)
                self.state["pages"] += 1
                self.summary["pages"] += 1
                self.save()
                rows = self.paid(DEAL_PAGE_COST, lambda: self.adapter._query_rows(
                    self.pull, dict(selection, page=page), "focus finder"))
                misses = 0
                held = False
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    self.summary["candidates"] += 1
                    asin = str(row.get("asin") or "").strip().upper()
                    reason = self.drop_reason(row, price_type)
                    if not reason and (not asin or asin in self.seen or asin in self.live):
                        reason = "dup"
                    self.seen.add(asin)
                    if not reason and self.state["new"] >= self.options["max_new_deals_per_run"]:
                        reason, held = "cap", True
                    post = None if reason else self.adapter._map_row(
                        row, self.source, self.pull.now, floors=self.floors,
                        category_names=self.pull.category_names,
                        list_ratio=self.cfg.keepa_list_price_min_avg_ratio, price_type=price_type,
                        renewed_title_lock=self.cfg.amazon_renewed_title_lock)
                    if not reason and post is None:
                        reason = "drop"
                    if not reason and self.pull.db is not None and self.pull.db.post_is_known(post.content_hash):
                        reason = "dup"
                    if reason:
                        self.summary["dropped"][reason] += 1
                        continue
                    self.state["new"] += 1
                    self.summary["kept"] += 1
                    self.adapter.focus_groups[asin] = group
                    self.out.append(post)
                    self.save()
                short = len(rows) < DEAL_PAGE_SIZE
                self.state["query_pages"][query] = page if held else (0 if short or page >= 66 else page + 1)
                self.save()
                if short:
                    ended.add(marker)
        except _TokensShort:
            return True
        except (DeadlineExceeded, RuntimeError, httpx.HTTPError):
            # Optional discovery must preserve the due checks and prior posts.
            pass
        finally:
            self.pull.overflow_only = self.pull.focus_finder = False
            self.pull.budget.save(self.pull.db)
            self.summary["idle_after_estimate"] = discovery_surplus(
                self.pull.budget, self.cfg, self.gap, self.pull.now)
        return False
