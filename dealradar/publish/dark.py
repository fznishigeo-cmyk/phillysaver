"""Add the shared dark-only polish after every page writer. OFF adds no bytes."""

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent / "templates" / "dark.css").read_text(encoding="utf-8")
META = '<meta name="color-scheme" content="light dark">\n'
STYLE = '<style id="wp-dark">' + CSS.strip() + '</style>\n'
_VIEWPORT = re.compile(r'<meta\b[^>]*\bname=[\'"]viewport[\'"][^>]*>\n?', re.I)
_HEAD = re.compile(r'<head\b[^>]*>\n?', re.I)


def publish(out: Path, cfg) -> dict[str, int]:
    pages = skipped = 0
    for path in sorted(out.rglob("*.html")):
        original = path.read_bytes()
        page = original.decode("utf-8")
        if "</head>" not in page:
            skipped += 1
            continue
        page = page.replace(META, "").replace(STYLE, "")
        if cfg.dark_polish:
            anchor = _VIEWPORT.search(page) or _HEAD.search(page)
            if anchor:
                page = page[:anchor.end()] + META + page[anchor.end():]
            page = page.replace("</head>", STYLE + "</head>", 1)
        updated = page.encode("utf-8")
        if updated != original:
            path.write_bytes(updated)
        pages += 1
    return {"pages": pages, "skipped": skipped}
