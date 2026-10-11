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
    "graph.threads.net",      # threads (feature-flagged)
    "api.woot.com",
    "meh.com",
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
        db.prune(util.now_utc(), posts_seen_days=cfg.posts_seen_days,
                 deals_days=cfg.deals_days)
        print("pruned")
        return 0
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

    sp = sub.add_parser("digest", help="send the daily digest if due")
    common(sp)
    sp.set_defaults(fn=cmd_digest)

    sp = sub.add_parser("prune", help="apply retention (posts 30 d, dead deals 14 d after they died)")
    common(sp)
    sp.set_defaults(fn=cmd_prune)

    sp = sub.add_parser("domains", help="print outbound hostnames for allowlists")
    sp.set_defaults(fn=cmd_domains)

    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    return args.fn(args)
