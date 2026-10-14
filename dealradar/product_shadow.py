"""Private product-page diagnostics. Never return these through publish results."""

import json
from datetime import timedelta

from .util import iso, parse_iso

META = "product_entities_shadow"


def record(db, cfg, now, run_key, report):
    runs = json.loads(db.meta_get(META) or "{}")
    cutoff = now - timedelta(days=cfg.product_entities_shadow_retention_days)
    runs = {key: row for key, row in runs.items()
            if (at := parse_iso(row.get("at"))) and cutoff <= at <= now}
    # Re-publishing a run replaces its facts, never appends another sample set.
    at = runs.get(run_key, {}).get("at", iso(now))
    runs[run_key] = {**report, "at": at, "run_key": run_key}
    db.meta_set(META, json.dumps(runs, sort_keys=True, separators=(",", ":")))


def report_data(db, cfg, now):
    runs = json.loads(db.meta_get(META) or "{}")
    cutoff = now - timedelta(days=cfg.product_entities_shadow_retention_days)
    rows = sorted((r for r in runs.values()
                   if (at := parse_iso(r.get("at"))) and cutoff <= at <= now),
                  key=lambda r: (r["at"], r["run_key"]))
    days = {}
    for row in rows:
        day = days.setdefault(row["at"][:10], {"runs": 0, "candidate_pairs": 0, "blocked_pairs": 0})
        day["runs"] += 1
        for field in ("candidate_pairs", "blocked_pairs"):
            day[field] += row[field]
    return {"latest": rows[-1] if rows else None, "days": days}


def report_lines(data):
    lines = ["== Product page shadow (private) =="]
    latest = (data or {}).get("latest")
    if not latest:
        return lines + ["Unavailable: no retained publish observations."]
    lines.append(f'Latest publish {latest["at"]}: {latest["published_pages"]} pages; '
                 f'Also at {latest["also_at_pages"]}; Lowest we\'ve seen {latest["lowest_seen_pages"]}.')
    lines.append("Also at pages by match: " + ", ".join(
        f"{kind} {n}" for kind, n in latest["also_at_pages_by_match"].items()))
    lines.append(f'Blocked pairs: {latest["blocked_pairs"]} / {latest["candidate_pairs"]}; reasons: '
                 + (", ".join(f"{k} {v}" for k, v in latest["blocked_by_reason"].items()) or "none"))
    for day, counts in sorted(data["days"].items()):
        lines.append(f'{day} UTC: {counts["runs"]} publishes, {counts["candidate_pairs"]} candidate pairs, '
                     f'{counts["blocked_pairs"]} blocked (repeat pairs count each publish).')
    for pair in latest["samples"]:
        def item(offer):
            # One private text line per pair, including untrusted source titles.
            return (f'{offer["title"]} | {offer["store"]} | {offer["condition"]} | '
                    f'${offer["price"]:.2f} USD').replace("\n", " ").replace("\r", " ")
        verdict = "BLOCKED: " + ", ".join(pair["flags"]) if pair["flags"] else "would show"
        lines.append(f'{pair["match"]}, {verdict}: {item(pair["page"])} -> {item(pair["other"])}')
    return lines
