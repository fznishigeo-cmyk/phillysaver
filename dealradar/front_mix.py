"""Front mix (operator 2026-10-22): each run of front cards holds set shares of
named buckets, interleaved; the adult filter wins absolutely."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, deque
from pathlib import Path

from .appeal import _words, matches

REST = "rest"


def validate(options, groups):
    """Strict like the neighbouring knobs; absent stays OFF."""
    path = "publish.front_page.mix."
    if not isinstance(options, dict) or set(options) - {"enabled", "per", "buckets"}:
        raise ValueError("publish.front_page.mix must contain supported settings only")
    if type(options.get("enabled", False)) is not bool:
        raise ValueError(path + "enabled must be a boolean")
    per, buckets = options.get("per", 30), options.get("buckets", {})
    if type(per) is not int or per < 1:
        raise ValueError(path + "per must be a positive integer")
    if not isinstance(buckets, dict) or REST not in buckets:
        raise ValueError(path + "buckets must be a mapping that includes rest")
    for name, bucket in buckets.items():
        where = path + "buckets." + str(name)
        if not isinstance(name, str) or not name.strip() or not isinstance(bucket, dict) \
                or set(bucket) - {"share", "words", "groups", "categories", "exclude_words"}:
            raise ValueError(where + " must contain supported settings only")
        if type(bucket.get("share")) is not int or bucket["share"] < 1:
            raise ValueError(where + ".share must be a positive integer")
        if (name == REST) == bool(set(bucket) & {"words", "groups", "categories"}):
            raise ValueError(where + (" takes a share only" if name == REST else " needs words, groups or categories"))
        for key in ("words", "exclude_words"):
            if key in bucket:
                _words(bucket[key], where + "." + key)
        if not isinstance(bucket.get("groups", []), list) or any(g not in groups for g in bucket.get("groups", [])):
            raise ValueError(where + ".groups must name front_page.appeal.want groups")
        cats = bucket.get("categories", [])
        if not isinstance(cats, list) or any(not isinstance(c, str) or not c.strip() for c in cats):
            raise ValueError(where + ".categories must be a list of category names")
    if sum(b["share"] for b in buckets.values()) != per:
        raise ValueError(path + "bucket shares must add up to per")


def shares(front_page):
    """{bucket: share} in configured order (ties go to the earlier bucket), or None when OFF."""
    options = front_page.get("mix") or {}
    if not options.get("enabled"):
        return None
    return {name: bucket["share"] for name, bucket in options["buckets"].items()}


def bucket(deal, front_page):
    """The named bucket for one deal, or None (rest). Title words decide before
    categories; adult titles never join a bucket."""
    options = front_page.get("mix") or {}
    if not options.get("enabled"):
        return None
    want = (front_page.get("appeal") or {}).get("want") or {}
    title = deal.get("item")
    if matches(title, want.get("exclude_words", [])):
        return None
    named = [(name, b) for name, b in options["buckets"].items() if name != REST
             and not matches(title, b.get("exclude_words", []))]
    for name, b in named:
        words = b.get("words", []) + [w for g in b.get("groups", []) for w in want.get("groups", {}).get(g, [])]
        if matches(title, words):
            return name
    return next((name for name, b in named if deal.get("category") in b.get("categories", [])), None)


def label(row, found):
    return row.get("mx") if row.get("mx") in found else REST


def schedule(found, total, caps):
    """Largest deficit first: smooth weighted round robin, never past a cap."""
    weight, taken, out = sum(found.values()), dict.fromkeys(found, 0), []
    for pos in range(total):
        ready = [name for name in found if taken[name] < caps[name]]
        if not ready:
            break
        name = max(ready, key=lambda n: (pos + 1) * found[n] - weight * taken[n])
        taken[name] += 1
        out.append(name)
    return out


def lead(rows, front_page, n=30):
    """Best deals today: the first n top picks by bucket share, each bucket in
    rank order. A bucket short of picks yields its turns; the rest follow."""
    found = shares(front_page)
    if not found:
        return rows
    picks = [d for d in rows if d.get("pick") is not None]
    queues = {name: deque(d for d in picks if label(d, found) == name) for name in found}
    head = [queues[name].popleft() for name in schedule(
        found, min(n, len(picks)), {name: len(q) for name, q in queues.items()})]
    chosen = {id(d) for d in head}
    return head + [d for d in rows if id(d) not in chosen]


def enabled(cfg):
    from .publish import drop_fresh
    return bool(shares(cfg.front_page)) and drop_fresh.enabled(cfg)


def _slice(pool, mark, size, found):
    """Each bucket is its own circular queue giving its share of every drop
    (scarce buckets come round sooner); a short bucket yields its turns."""
    queues = {name: [d for d in pool if label(d, found) == name] for name in found}
    order = schedule(found, min(size, len(pool)), {name: len(q) for name, q in queues.items()})
    picked = {}
    for name, queue in queues.items():
        k = order.count(name)
        # A bucket that fits whole keeps the plain slice's turn per mark.
        start = mark * (size if k == len(queue) else k)
        picked[name] = iter([queue[(start + i) % len(queue)] for i in range(k)])
    return [next(picked[name]) for name in order]


def _base_page(pool, mark, options, policy, found):
    """drop_fresh._base_page over the mix slice; a bucket is never ahead of its
    share, and every spacing rule still wins over the mix (it gives way first).
    When no card fits every rule (live 2026-10-23: a drop of checkout-priced
    cards), checkout spacing gives way, then category runs; clothing and
    allure spacing hold instead of taking the next card blindly."""
    from .publish.drop_fresh import cheap
    if not pool:
        return []
    pending = _slice(pool, mark, options.get("fresh_page_size", 60), found)
    out = []
    target = min(19, sum(cheap(d) for d in pending), len(pending))
    def fits(d, level=0):
        pos = len(out)
        if d.get("allure") and policy["allure"] and (pos < policy["first"] or
                any(r.get("allure") for r in out[max(0, pos - policy["allure"] + 1):])):
            return False
        if d.get("cl") and policy["clothing"] and pos < policy["slots"] and sum(
                bool(r.get("cl")) for r in out[pos // policy["mix"] * policy["mix"]:]) >= policy["clothing"]:
            return False
        if level < 1 and d.get("pick_checkout") and sum(bool(r.get("pick_checkout")) for r in
                out[max(0, pos - policy["promo"] + 1):]) >= policy["cap"]:
            return False
        run = options.get("max_same_category_run", 2)
        return level > 1 or not (pos >= run and all(r.get("category") == d.get("category") for r in out[-run:]))
    def loose(d):
        return fits(d, 1)
    def looser(d):
        return fits(d, 2)
    categories = Counter(d.get("category") for d in pending)
    total = len(pending)
    taken, weight = Counter(), sum(found.values())
    def mixed(d):
        return weight * taken[label(d, found)] < (len(out) + 1) * found[label(d, found)]
    def room(d):
        pos = len(out)
        run = options.get("max_same_category_run", 2)
        for cat, count in categories.items():
            remaining = count - (d.get("category") == cat)
            other = total - pos - 1 - remaining
            streak = 0
            if d.get("category") == cat:
                streak = 1
                for r in reversed(out):
                    if r.get("category") != cat:
                        break
                    streak += 1
            if remaining > run * (other + 1) - streak:
                return False
        return True
    while pending:
        need = len(out) < 24 and 24 - len(out) <= target - sum(cheap(d) for d in out[:24])
        choices = sorted(range(len(pending)), key=lambda i: (
            -int(need and cheap(pending[i])),
            -int(bool(pending[i].get("allure"))) - int(bool(pending[i].get("pick_checkout"))),
            -int(len(out) < 24 and cheap(pending[i])), i))
        at = None
        for tests in ((fits, mixed, room), (fits, mixed), (fits, room), (fits,),
                      (loose, room), (loose,), (looser, room), (looser,)):
            if at is None:
                at = next((i for i in choices if all(test(pending[i]) for test in tests)), None)
        d = pending.pop(at or 0)
        out.append(d)
        categories[d.get("category")] -= 1
        taken[label(d, found)] += 1
    return out


def page(pool, mark, options, policy):
    """drop_fresh.page with the front mix; OFF is drop_fresh.page itself."""
    from .publish import drop_fresh
    found = shares(options)
    if not found:
        return drop_fresh.page(pool, mark, options, policy)
    out = _base_page(pool, mark, options, policy, found)
    size = options.get("fresh_page_size", 60)
    if len(pool) < 2 * size:
        return out
    boundary = size - size // 4
    previous = _base_page(pool, mark - 1, options, policy, found)
    unavailable = {d["key"] for d in out} | {d["key"] for d in _slice(pool, mark + 1, size, found)}
    def signature(d):
        return (d.get("category"), drop_fresh.women(d), drop_fresh.cheap(d), bool(d.get("cl")),
                bool(d.get("pick_checkout")), bool(d.get("allure")), d.get("mx"))
    candidates = sorted(enumerate(previous[:boundary]), key=lambda pair: (
        pair[1].get("pick", float("inf")), -pair[1].get("ps", 0),
        not pair[1].get("earns"), pair[1]["key"]))
    available = set(range(boundary, size))
    for old, row in candidates:
        if row["key"] in unavailable:
            continue
        at = next((i for i in sorted(available) if abs(i - old) >= 12
                   and signature(row) == signature(out[i])), None)
        if at is not None:
            out[at] = row
            available.remove(at)
    return out


# The browser's fresh drops (drop-fresh.js, after drop_fresh/fresh_more_plain
# publish): the same slice, share cap and winner signature as page() above.
SLICE = """  function freshLabel(d) { return d.mx in freshRules.buckets ? d.mx : 'rest'; }
  function freshSlice(pool, mark) {
    var size = freshSize(), found = freshRules.buckets, wrap = (n, m) => ((n % m) + m) % m;
    var names = Object.keys(found), weight = names.reduce((sum,n) => sum+found[n], 0), queues = {}, taken = {}, order = [];
    names.forEach(n => { queues[n] = []; taken[n] = 0; });
    pool.forEach(d => queues[freshLabel(d)].push(d));
    for (var pos = 0; pos < Math.min(size,pool.length); pos++) {
      var best = null, score = -Infinity;
      names.forEach(function (n) {
        var s = (pos+1)*found[n]-weight*taken[n];
        if (taken[n] < queues[n].length && s > score) { best = n; score = s; }
      });
      if (best === null) break;
      taken[best]++; order.push(best);
    }
    var at = {};
    names.forEach(n => { at[n] = mark * (taken[n] === queues[n].length ? size : taken[n]); });
    return order.map(n => queues[n][wrap(at[n]++, queues[n].length)]);
  }
"""
SWAPS = (
    ("  function freshBasePage(pool, mark) {\n", SLICE + "  function freshBasePage(pool, mark) {\n"),
    ("""    var size = freshSize(), start = ((mark * size % pool.length) + pool.length) % pool.length;
    var pending = Array.from({length:Math.min(size,pool.length)}, (_,i) => pool[(start+i)%pool.length]), out = [];
""", """    var pending = freshSlice(pool, mark), out = [];
"""),
    ("""    function fits(d) {
      var pos = out.length, run = options.max_same_category_run || 2;
      if (d.allure && p.allure && (pos < p.first || out.slice(Math.max(0,pos-p.allure+1)).some(r => r.allure))) return false;
      if (d.cl && p.clothing && pos < p.slots && out.slice(Math.floor(pos/p.mix)*p.mix).filter(r => r.cl).length >= p.clothing) return false;
      if (d.pick_checkout && out.slice(Math.max(0,pos-p.promo+1)).filter(r => r.pick_checkout).length >= p.cap) return false;
      return !(pos >= run && out.slice(-run).every(r => r.category === d.category));
    }
""", """    function fits(d, level) {
      var pos = out.length, run = options.max_same_category_run || 2;
      if (d.allure && p.allure && (pos < p.first || out.slice(Math.max(0,pos-p.allure+1)).some(r => r.allure))) return false;
      if (d.cl && p.clothing && pos < p.slots && out.slice(Math.floor(pos/p.mix)*p.mix).filter(r => r.cl).length >= p.clothing) return false;
      if (!level && d.pick_checkout && out.slice(Math.max(0,pos-p.promo+1)).filter(r => r.pick_checkout).length >= p.cap) return false;
      return level > 1 || !(pos >= run && out.slice(-run).every(r => r.category === d.category));
    }
"""),
    ("""      categories.set(d.category,(categories.get(d.category)||0)+1);
    });
""", """      categories.set(d.category,(categories.get(d.category)||0)+1);
    });
    var found = freshRules.buckets, taken = {}, left = {}, weight = 0;
    for (var name in found) { taken[name] = left[name] = 0; weight += found[name]; }
    pending.forEach(d => left[freshLabel(d)]++);
    function mixed(d) { return weight*taken[freshLabel(d)] < (out.length+1)*found[freshLabel(d)]; }
    // Skip the share-first pass when no bucket within its share has cards left (same result).
    function due() { for (var n in found) if (left[n] && weight*taken[n] < (out.length+1)*found[n]) return true; return false; }
"""),
    ("""    while (pending.length) {
      var need = out.length < 24 && 24-out.length <= target-out.slice(0,24).filter(freshCheap).length;
      // Select the same stable priority winner without sorting every remaining
      // row at every slot. Keep the best fitting fallback if no row leaves room.
      var at = -1, fallback = -1, best = -1, backup = -1;
      for (var i = 0; i < pending.length; i++) {
        var d = pending[i], cheap = freshCheap(d);
        var priority = 8*(need && cheap)+2*(!!d.allure + !!d.pick_checkout)+(out.length<24 && cheap);
        if (priority <= best || !fits(d)) continue;
        if (priority > backup) { fallback = i; backup = priority; }
        if (room(d)) { at = i; best = priority; }
      }
      if (at < 0) at = fallback < 0 ? 0 : fallback;
      var d = pending.splice(at,1)[0]; out.push(d);
      categories.set(d.category,categories.get(d.category)-1);
    }
""", """    function choose(need, due, level) {
      var at = -1, fallback = -1, best = -1, backup = -1;
      for (var i = 0; i < pending.length; i++) {
        var d = pending[i], cheap = freshCheap(d);
        var priority = 8*(need && cheap)+2*(!!d.allure + !!d.pick_checkout)+(out.length<24 && cheap);
        if (priority <= best || !fits(d, level) || due && !mixed(d)) continue;
        if (priority > backup) { fallback = i; backup = priority; }
        if (room(d)) { at = i; best = priority; }
      }
      return at < 0 ? fallback : at;
    }
    while (pending.length) {
      var need = out.length < 24 && 24-out.length <= target-out.slice(0,24).filter(freshCheap).length;
      var at = due() ? choose(need, true, 0) : -1;
      // No card fits every rule: checkout spacing gives way, then category runs.
      for (var level = 0; at < 0 && level < 3; level++) at = choose(need, false, level);
      var d = pending.splice(Math.max(at,0),1)[0]; out.push(d);
      categories.set(d.category,categories.get(d.category)-1);
      taken[freshLabel(d)]++; left[freshLabel(d)]--;
    }
"""),
    ("""    var unavailable = new Set(out.map(host.dealKey)), start = (((mark+1)*size%pool.length)+pool.length)%pool.length;
    for (var i=0;i<size;i++) unavailable.add(host.dealKey(pool[(start+i)%pool.length]));
    function signature(d) { return [d.category,freshWomen(d),freshCheap(d),!!d.cl,!!d.pick_checkout,!!d.allure].join(':'); }
""", """    var unavailable = new Set(out.map(host.dealKey));
    freshSlice(pool, mark+1).forEach(d => unavailable.add(host.dealKey(d)));
    function signature(d) { return [d.category,freshWomen(d),freshCheap(d),!!d.cl,!!d.pick_checkout,!!d.allure,d.mx].join(':'); }
"""),
)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]


def publish(out, cfg):
    """Patch the published drop-fresh.js and its versioned import; OFF keeps every byte."""
    from .publish import drop_fresh
    from .publish.template_slots import replace_slot
    if not enabled(cfg):
        return
    asset = out / drop_fresh.ASSET
    script = old = asset.read_text(encoding="utf-8")
    slot = dict(template_name=drop_fresh.ASSET, slot_name="front-mix")
    for token, new in SWAPS + ((", freshCache = null;\n", ", freshCache = null;\n  freshRules.buckets = "
                                + json.dumps(shares(cfg.front_page), separators=(",", ":")) + ";\n"),):
        script = replace_slot(script, token, new, **slot)
    home = out / "index.html"
    version = "/drop-fresh.js?v=" + hashlib.sha256(script.encode()).hexdigest()[:12]
    html = replace_slot(home.read_text(encoding="utf-8"), "/drop-fresh.js?v=" + hashlib.sha256(old.encode()).hexdigest()[:12],
                        version, template_name="index.html", slot_name="front-mix-version")
    asset.write_text(script, encoding="utf-8")
    home.write_text(html, encoding="utf-8")
