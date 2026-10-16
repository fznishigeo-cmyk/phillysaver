"""Offline rules export and deterministic, dependency-free extension packaging."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from .config import load_config
from .publish import checker

ROOT = Path(__file__).resolve().parents[1]


def rules_bytes(extension: Path, *, shards: int = 256, amazon_mode: str = "full") -> bytes:
    manifest = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    if not re.fullmatch(r"\d+(?:\.\d+){0,3}", version):
        raise ValueError("invalid extension version")
    # Amazon OK'd verdict + checked price (amz378_998, 2026-10-16); config extension.amazon_mode can select "link".
    rules = checker.js_rules() | {"AMAZON_MODE": amazon_mode, "version": version, "shards": shards}
    return (json.dumps(rules, sort_keys=True, indent=2) + "\n").encode("utf-8")


def build(root: Path = ROOT) -> Path:
    extension = root / "extension"
    cfg = load_config()
    (extension / "rules.json").write_bytes(rules_bytes(
        extension, shards=cfg.checker["shards"], amazon_mode=cfg.extension["amazon_mode"]))
    version = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))["version"]
    destination = root / "dist" / f"wowpriced-extension-{version}.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Listing material, fixtures and developer tools are not runtime code.
    files = [p for p in extension.rglob("*") if p.is_file()
             and p.relative_to(extension).parts[0] != "store"
             and p.suffix in {".js", ".json", ".html", ".css", ".png"}]
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(files, key=lambda p: p.relative_to(extension).as_posix()):
            info = zipfile.ZipInfo(path.relative_to(extension).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    return destination
