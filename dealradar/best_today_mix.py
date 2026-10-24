"""Best deals today's own mix; homepage rules and published labels stay intact."""

from collections import deque
from zoneinfo import ZoneInfo

from . import front_mix


def validate(options, groups):
    path = "publish.front_page.best_today_mix"
    if not isinstance(options, dict) or "checkout_spacing_exempt" in options:
        raise ValueError(path + " must contain supported settings only")
    translated = dict(options)
    buckets = options.get("buckets")
    if isinstance(buckets, dict):
        translated["buckets"] = {}
        for name, bucket in buckets.items():
            where = path + ".buckets." + str(name)
            if not isinstance(bucket, dict):
                translated["buckets"][name] = bucket
                continue
            if name == front_mix.REST and set(bucket) != {"share"}:
                raise ValueError(where + " takes a share only")
            if type(bucket.get("season", False)) is not bool:
                raise ValueError(where + ".season must be a boolean")
            prepared = dict(bucket)
            season = prepared.pop("season", False)
            if season and not set(prepared) & {"words", "groups", "categories"}:
                prepared["categories"] = []
            # The shared validator requires positive shares. Give zero-share
            # rest one validation-only slot; preserve every invalid input.
            if name == front_mix.REST and type(prepared.get("share")) is int and prepared["share"] == 0:
                prepared["share"] = 1
                per = options.get("per", 30)
                if type(per) is int and per > 0:
                    translated["per"] = per + 1
            translated["buckets"][name] = prepared
    try:
        front_mix.validate(translated, groups)
    except ValueError as exc:
        raise ValueError(str(exc).replace("publish.front_page.mix", path)) from None


def front_page(options, now=None):
    mix = options.get("best_today_mix") or {}
    if not mix.get("enabled"):
        return options
    today = now.astimezone(ZoneInfo("America/New_York")).strftime("%m-%d") if now is not None else None
    seasonal = []
    for window in (options.get("appeal") or {}).get("season", []) if today is not None else ():
        start, end = window["from"], window["to"]
        if start <= today <= end if start <= end else today >= start or today <= end:
            seasonal.extend(window["words"])
    buckets = {}
    for name, bucket in mix["buckets"].items():
        buckets[name] = prepared = dict(bucket)
        if prepared.pop("season", False):
            prepared["words"] = list(dict.fromkeys(prepared.get("words", []) + seasonal))
    return dict(options, mix=dict(mix, buckets=buckets))


def lead(rows, options, extra=(), n=30):
    """Ranked picks, then eligible extras; zero-share rest only fills shortages."""
    found = front_mix.shares(options)
    if not found:
        return rows
    order = {key: i for i, key in enumerate(extra)}
    picks = [d for d in rows if d.get("pick") is not None]
    picks += sorted((d for d in rows if d.get("pick") is None and d.get("dedupe_key") in order),
                    key=lambda d: order[d["dedupe_key"]])
    queues = {name: deque() for name in found}
    for deal in picks:
        name = front_mix.bucket(deal, options) or front_mix.REST
        queues[name].append(deal)
    total = min(n, len(picks))
    caps = {name: len(queue) for name, queue in queues.items()}
    if found[front_mix.REST] == 0:
        caps[front_mix.REST] = max(0, total - sum(count for name, count in caps.items() if name != front_mix.REST))
    head = [queues[name].popleft() for name in front_mix.schedule(found, total, caps)]
    chosen = {id(deal) for deal in head}
    return head + [deal for deal in rows if id(deal) not in chosen]
