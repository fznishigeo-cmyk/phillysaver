"""Command-line interface: python -m dealradar <command>.

Works identically on a laptop, a VPS cron, or GitHub Actions — the only state
is state.db (SQLite) plus the site/ output directory.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from urllib.parse import urlsplit

CORE_DOMAINS = [
    "api.anthropic.com",      # parse layer (models endpoint + messages)
    "www.reddit.com",         # reddit OAuth token
    "oauth.reddit.com",       # reddit listings
    "api.telegram.org",       # bot sends (channel + admin alerts)
    "graph.facebook.com",     # Facebook Page + Instagram posting
    "graph.threads.net",      # threads (feature-flagged)
    "api.woot.com",
    "meh.com",
    "api.indexnow.org",       # python -m dealradar indexnow
    "ssl.bing.com",           # private Bing Webmaster traffic report
    "api-sandbox.pinterest.com", "api.pinterest.com",
]


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def cmd_run(args: argparse.Namespace) -> int:
    from .pipeline import run

    summary = run(fast_lane=args.fast_lane, db_path=args.db,
                  config_path=args.config,
                  dry_run_telegram=True if args.dry_run_telegram else None)
    print(json.dumps({k: v for k, v in summary.items() if k != "source_errors"},
                     default=str, indent=2))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from .pipeline import validate_only

    res = validate_only(db_path=args.db, config_path=args.config)
    print(json.dumps(res, indent=2))
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    from . import util
    from .config import load_config
    from .db import DB
    from .publish.email_digest import maybe_send_digest

    cfg = load_config(args.config)
    db = DB(args.db)
    try:
        sent = maybe_send_digest(db, cfg, util.now_utc())
        print(f"digest_sent={sent}")
        return 0
    finally:
        db.close()


def cmd_prune(args: argparse.Namespace) -> int:
    from . import util
    from .config import load_config
    from .db import DB

    cfg = load_config(args.config)
    db = DB(args.db)
    try:
        now = util.now_utc()
        run_key = None
        if cfg.incremental_cache_enabled:
            run_key = "prune:" + util.iso(now)
            db.start_run(run_key, "prune", now)
        db.prune(now, posts_seen_days=cfg.posts_seen_days,
                 deals_days=cfg.deals_days, post_parses_days=cfg.post_parses_days,
                 llm_calls_days=cfg.llm_calls_days, run_stats_days=cfg.run_stats_days,
                 http_cache_days=cfg.http_cache_days, outgoing_days=cfg.outgoing_days,
                 incremental_cache_enabled=cfg.incremental_cache_enabled, run_key=run_key,
                 cache_max_examined_per_run=cfg.incremental_cache_max_examined_per_run,
                 cache_budget_s=cfg.incremental_cache_budget_s)
        if run_key is not None:
            db.finish_run(run_key, now=util.now_utc(), items_fetched=0, posts_new=0,
                          deals_parsed=0, published=0, spend_usd=0)
        print("pruned")
        return 0
    finally:
        db.close()


def cmd_indexnow(args: argparse.Namespace) -> int:
    """Record receipts for an explicitly successful deployment of this run."""
    from .config import load_config
    from .db import DB
    from .publish.indexnow import metrics, submit_indexnow, summary_lines
    from .util import now_utc
    import os

    cfg, db = load_config(args.config), DB(args.db)
    try:
        result = submit_indexnow(db, cfg, deployed=args.deployed, run_key=args.run_key)
        values = metrics(db, cfg, now_utc())
        print(json.dumps({"indexnow": values}))
        if path := os.environ.get("GITHUB_STEP_SUMMARY"):
            try:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write("\n" + "\n".join(summary_lines(values)) + "\n")
            except OSError:
                logging.getLogger(__name__).warning("indexnow: summary unavailable")
        return result
    finally:
        db.close()


def cmd_growth_report(args: argparse.Namespace) -> int:
    """The daily private growth report; logs section status only (public logs)."""
    from .config import load_config
    from .growth.report import run

    return run(load_config(args.config), db_path=args.db, copy_only=args.copy_only)


def cmd_ads_guard(args: argparse.Namespace) -> int:
    from .config import load_config
    from .growth.ads_guard import run

    return run(load_config(args.config), db_path=args.db,
               state_unavailable=args.state_unavailable)


def cmd_ads_setup(args: argparse.Namespace) -> int:
    from .config import load_config
    from .growth.ads_setup import run

    return run(load_config(args.config), validate_only=args.validate_only)


def cmd_source_audit(args: argparse.Namespace) -> int:
    from .config import load_config
    from .source_audit import run

    return run(load_config(args.config), db_path=args.db)


def cmd_pinterest(args: argparse.Namespace) -> int:
    from .config import load_config
    from .db import DB
    from .publish import pinterest

    cfg = load_config(args.config)
    if args.command == "pinterest-auth-url":
        url = pinterest.auth_url(cfg)
        if url:
            print(url)
        return 0
    db = DB(args.db)
    try:
        if args.command == "pinterest-connect":
            return pinterest.connect(db, cfg, args.code)
        return pinterest.post(db, cfg)
    finally:
        db.close()


def cmd_meta(args: argparse.Namespace) -> int:
    from .config import load_config
    from .db import DB
    from .publish import meta

    cfg = load_config(args.config)
    db = DB(args.db)
    try:
        return meta.post(db, cfg)
    finally:
        db.close()


def cmd_domains(args: argparse.Namespace) -> int:
    """Print the outbound hostnames the pipeline needs (network allowlist helper)."""
    from .config import load_sources

    primary, backup = load_sources()
    hosts: set[str] = set(CORE_DOMAINS)
    for s in primary + backup:
        parts = urlsplit(s.url)
        if parts.scheme in ("http", "https") and parts.netloc:
            hosts.add(parts.netloc)
    for h in sorted(hosts):
        print(h)
    print("# plus: pypi.org + files.pythonhosted.org (installs), github.com (state push),",
          file=sys.stderr)
    print("# your SMTP host, and Telegram MTProto TCP ranges 149.154.160.0/20, 91.108.0.0/16",
          file=sys.stderr)
    print("# redirect/affiliate resolution touches arbitrary retailer domains and degrades",
          file=sys.stderr)
    print("# gracefully when blocked; allow-all egress gives full functionality.",
          file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dealradar",
                                description="Polite online-deal aggregator.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--db", default="state.db")
        sp.add_argument("--config", default=None)

    sp = sub.add_parser("run", help="one pipeline run (hourly semantics)")
    common(sp)
    sp.add_argument("--fast-lane", action="store_true",
                    help="fetch only fast_lane sources (15-min workflow)")
    sp.add_argument("--dry-run-telegram", action="store_true",
                    help="force Telegram alerts to log-only")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("validate-sources",
                        help="live-validate all sources; write sources_report.md")
    common(sp)
    sp.set_defaults(fn=cmd_validate)

    sp = sub.add_parser("source-audit", help="read-only source coverage audit")
    common(sp)
    sp.set_defaults(fn=cmd_source_audit)

    sp = sub.add_parser("digest", help="send the daily digest if due")
    common(sp)
    sp.set_defaults(fn=cmd_digest)

    sp = sub.add_parser("prune", help="apply retention (posts 30 d, dead deals 14 d after they died)")
    common(sp)
    sp.set_defaults(fn=cmd_prune)

    sp = sub.add_parser("indexnow", help="submit the last publish's changed URLs to IndexNow")
    common(sp)
    sp.add_argument("--deployed", action="store_true", help="this run's site deployment succeeded")
    sp.add_argument("--run-key", default=None, help="published run key (or DEALRADAR_RUN_KEY)")
    sp.set_defaults(fn=cmd_indexnow)
    sp = sub.add_parser("growth-report",
                        help="daily private growth report (operator email / Telegram admin)")
    common(sp)
    sp.add_argument("--copy-only", action="store_true",
                    help="save the private copy only (no email, no Telegram)")
    sp.set_defaults(fn=cmd_growth_report)

    sp = sub.add_parser("ads-guard", help="pause-only Google Ads spend guard (admin Telegram)")
    common(sp)
    sp.add_argument("--state-unavailable", action="store_true",
                    help="state restore failed; pause defensively without writing state")
    sp.set_defaults(fn=cmd_ads_guard)

    sp = sub.add_parser("ads-setup", help="create the configured Search campaign, PAUSED (one time)")
    common(sp)
    sp.add_argument("--validate-only", action="store_true",
                    help="ask Google to check the campaign without creating it")
    sp.set_defaults(fn=cmd_ads_setup)

    for command, help_text in (
        ("pinterest-auth-url", "print the Pinterest authorization URL"),
        ("pinterest-connect", "connect Pinterest once using an authorization code"),
        ("pinterest-post", "post published top picks to Pinterest"),
    ):
        sp = sub.add_parser(command, help=help_text)
        common(sp)
        if command == "pinterest-connect":
            sp.add_argument("--code", required=True)
        sp.set_defaults(fn=cmd_pinterest)

    sp = sub.add_parser("meta-post", help="post published top picks to Facebook and Instagram")
    common(sp)
    sp.set_defaults(fn=cmd_meta)

    sp = sub.add_parser("domains", help="print outbound hostnames for allowlists")
    sp.set_defaults(fn=cmd_domains)

    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    return args.fn(args)
