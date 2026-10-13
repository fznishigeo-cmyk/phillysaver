"""Generate the operator's static subscription checklist from sources.yaml."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dealradar.config import load_sources  # noqa: E402


def checklist(sources, existing: str = "") -> str:
    # Keep the operator's ticks and handwritten sender notes on regeneration.
    saved = {name: (tick, sender) for tick, name, sender in re.findall(
        r"^- \[([ xX])\] .*? \(`([^`]+)`\) — Matched sender: (.*)$", existing, re.M)}
    partners = {s.name: s for s in sources if s.crawl_permission and s.method != "email"}
    lines = ["# Partner newsletter subscriptions", "",
             "Subscribe **deals@wowpriced.com** to each permission partner's newsletter,",
             "then confirm the forwarded subscription email and tick its box.",
             "Senders match by registrable domain or the partner's optional",
             "`newsletter_from` list in sources.yaml. Shared-domain matches stay unknown;",
             "unmapped senders appear in the private daily reports.", "",
             "Record the matched sender here once known. The pipeline never edits this file.",
             "Regenerate with `python scripts/partner_newsletters.py`; ticks and sender notes",
             "are preserved. Paused web feeds are included; feed settings stay unchanged.", "",
             f"{len(partners)} permission entries:", ""]
    for name, partner in sorted(partners.items(), key=lambda p: ((p[1].display_name or p[0]).lower(), p[0])):
        tick, sender = saved.get(name, (" ", "—"))
        if partner.newsletter_from:
            sender = ", ".join(partner.newsletter_from)
        lines.append(f"- [{tick}] [{partner.display_name or name}]({partner.site or partner.url}) "
                     f"(`{name}`) — Matched sender: {sender}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=ROOT / "sources.yaml")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/partner-newsletters.md")
    args = parser.parse_args()
    primary, backup = load_sources(args.sources)
    existing = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
    args.output.write_text(checklist(primary + backup, existing), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
