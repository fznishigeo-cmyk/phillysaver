"""Read-only publication measurements. Run with python -m scripts.publication_budget.

JS/CSS are exact UTF-8 element bodies and unique local assets concatenated in
document order, without separators. They overlap HTML transfer bytes: never sum
the component metrics into a transfer total. JSON-LD stays markup. Remote tag
references are labeled/countable, never fetched; URLs/queries are never logged.
"""
from __future__ import annotations

import argparse
import gzip
import json
import posixpath
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dealradar.config import PHONE_BYTE_METRICS, load_config, validated_phone_budget

JS_TYPES = {"", "module", "text/javascript", "application/javascript",
            "text/ecmascript", "application/ecmascript", "application/x-javascript",
            "text/jscript", "text/livescript", "text/x-javascript", "text/x-ecmascript",
            *(f"text/javascript1.{i}" for i in range(6))}
LABEL_LIMIT = 20


class AnalysisError(ValueError):
    """Messages contain only controlled path labels, never file contents."""


def sizes(data: bytes) -> dict:
    return {"raw": len(data), "gzip": len(gzip.compress(data, compresslevel=9, mtime=0))}


def _read(root: Path, relative: str, label: str) -> bytes:
    try:
        path = (root / relative).resolve(strict=True)
        path.relative_to(root)
        if not path.is_file():
            raise ValueError
        return path.read_bytes()
    except (OSError, ValueError, RuntimeError):
        raise AnalysisError(f"{label}: missing or unsafe file") from None


class HomeAssets(HTMLParser):
    def __init__(self, root: Path, domain: str):
        super().__init__(convert_charrefs=False)
        self.root = root
        self.origin = urlsplit(f"https://{domain}") if domain else None
        self.parts = {"js": [], "css": [], "json_ld": []}
        self.inline = {key: 0 for key in self.parts}
        self.local = {"js": set(), "css": set()}
        self.external = set()
        self.active = None
        self.body = self.content = False

    def asset(self, reference, kind):
        label = f"home {kind} asset"
        if not isinstance(reference, str) or not reference.strip() or re.search(r"[\s\\\x00-\x1f]", reference):
            raise AnalysisError(f"{label}: unsafe reference")
        try:
            url = urlsplit(reference)
            if url.scheme or url.netloc:
                if url.scheme not in ("", "https", "http") or not url.netloc or url.username or url.password:
                    raise ValueError
                scheme = url.scheme or "https"
                port = url.port or (443 if scheme == "https" else 80)
                same_origin = (self.origin and scheme == self.origin.scheme
                               and url.hostname == self.origin.hostname and port == (self.origin.port or 443))
                if not same_origin:
                    self.external.add((kind, reference))
                    return
            if re.search(r"%(?![0-9a-fA-F]{2})", url.path):
                raise ValueError
            decoded = unquote(url.path, errors="strict")
            if re.search(r"[\\\x00-\x1f]", decoded):
                raise ValueError
            # Normalize query-bearing and dot-segment aliases before deduping.
            depth = 0
            for segment in decoded.lstrip("/").split("/"):
                if segment == "..":
                    depth -= 1
                    if depth < 0:
                        raise ValueError
                elif segment not in ("", "."):
                    depth += 1
            relative = posixpath.normpath(decoded.lstrip("/"))
            if Path(relative).suffix.lower() not in ((".js", ".mjs") if kind == "js" else (".css",)):
                raise ValueError
        except (ValueError, UnicodeError):
            raise AnalysisError(f"{label}: unsafe reference") from None
        if relative not in self.local[kind]:
            self.parts[kind].append(_read(self.root, relative, label))
            self.local[kind].add(relative)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "base":
            raise AnalysisError("index.html: base URL is unsupported")
        self.body |= tag == "body"
        self.content |= attrs.get("id") == "content"
        mime = (attrs.get("type") or "").split(";", 1)[0].strip().lower()
        if tag == "script":
            self.active = None
            if mime in JS_TYPES:
                if "src" in attrs:
                    self.asset(attrs["src"], "js")
                else:
                    self.active = "js"
            elif mime == "application/ld+json":
                self.active = "json_ld"
        elif tag == "style":
            self.active = "css" if mime in ("", "text/css") else None
        elif tag == "link":
            rel = (attrs.get("rel") or "").lower().split()
            destination = (attrs.get("as") or "").lower()
            if "stylesheet" in rel and mime in ("", "text/css"):
                self.asset(attrs.get("href"), "css")
            elif mime in JS_TYPES and ("modulepreload" in rel or ("preload" in rel and destination == "script")):
                self.asset(attrs.get("href"), "js")
            elif mime in ("", "text/css") and "preload" in rel and destination == "style":
                self.asset(attrs.get("href"), "css")
        if self.active and tag in ("script", "style"):
            self.inline[self.active] += 1

    def handle_data(self, data):
        if self.active:
            self.parts[self.active].append(data.encode("utf-8"))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.active = None


def analyze(output: str | Path, cfg, *, expected_offers: int) -> dict:
    limits = validated_phone_budget(cfg.phone_budget)
    try:
        if "://" in str(output) or Path(output).is_symlink():
            raise ValueError
        root = Path(output).resolve(strict=True)
        if not root.is_dir():
            raise ValueError
    except (ValueError, OSError, RuntimeError):
        raise AnalysisError("output: existing local publication directory required") from None
    html = _read(root, "index.html", "index.html")
    deals = _read(root, "deals.json", "deals.json")
    try:
        document = json.loads(deals)
        meta, rows = document["meta"], document["deals"]
        version = meta["site_version"]
        if (type(expected_offers) is not int or expected_offers < 0 or not isinstance(rows, list)
                or len(rows) != expected_offers or not isinstance(version, str) or not version
                or not all(isinstance(row, dict) and isinstance(row.get("key"), str) and row["key"] for row in rows)
                or len({row["key"] for row in rows}) != len(rows)):
            raise ValueError
        text = html.decode("utf-8")
        if version not in text:
            raise ValueError
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise AnalysisError("publication: invalid payload or fixture offer count/version mismatch") from None
    parser = HomeAssets(root, cfg.custom_domain)
    parser.feed(text)
    parser.close()
    if not parser.body or not parser.content or parser.active:
        raise AnalysisError("index.html: incomplete publication markup")
    data = {"home_html": html, "deals_json": deals,
            "home_js": b"".join(parser.parts["js"]), "home_css": b"".join(parser.parts["css"])}
    metrics = {}
    for name in PHONE_BYTE_METRICS:
        measurement = sizes(data[name])
        ceiling = limits[f"{name}_gzip_max_bytes"]
        metrics[name] = measurement | {"limit": ceiling, "status": "disabled" if not limits["enabled"] else
                                      "pass" if measurement["gzip"] <= ceiling else "fail"}
    external = [{"kind": kind, "label": f"external-{i + 1}"}
                for i, (kind, _) in enumerate(sorted(parser.external))][:LABEL_LIMIT]
    return {"enabled": limits["enabled"], "ok": all(m["status"] != "fail" for m in metrics.values()),
            "offers": len(rows), "metrics": metrics, "json_ld_markup": sizes(b"".join(parser.parts["json_ld"])),
            "inline_elements": parser.inline, "local_assets": {k: len(v) for k, v in parser.local.items()},
            "external_count": len(parser.external), "external": external,
            "note": "JS/CSS overlap HTML; do not sum. External references are not fetched. Not live vitals."}


def summary(report: dict) -> str:
    return (f"Phone budget: {'PASS' if report['ok'] else 'FAIL'}"
            + ("" if report["enabled"] else " (disabled; measurement only)") + "; "
            + "; ".join(f"{name} gzip {m['gzip']}/{m['limit']} bytes" for name, m in report["metrics"].items()))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--expected-offers", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        # Config/YAML exception text can contain secrets: never print it.
        cfg = load_config(args.config)
    except Exception:
        print('{"ok":false,"error":"quality config unavailable or invalid"}')
        print("Phone budget: FAIL (invalid config)", file=sys.stderr)
        return 1
    try:
        report = analyze(args.output, cfg, expected_offers=args.expected_offers)
    except AnalysisError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True, separators=(",", ":")))
        print("Phone budget: FAIL (publication/asset validation)", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError):
        print('{"ok":false,"error":"invalid publication or missing/unsafe asset"}')
        print("Phone budget: FAIL (publication/asset validation)", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False))
    print(summary(report), file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
