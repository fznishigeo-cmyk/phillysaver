"""Optional beacon asset, injected after rendering; disabled pages stay identical."""
import re
from html import escape
from pathlib import Path

from .. import clicks

BLOCK = re.compile(r'<!-- wp-click-signal -->.*?<!-- /wp-click-signal -->\n?', re.S)


def publish(out, cfg):
    base = clicks.endpoint(cfg) if clicks.enabled(cfg) else ""
    asset = out / "click-signal.js"
    if base:
        asset.write_bytes((Path(__file__).parent / "templates" / asset.name).read_bytes())
    else:
        was_active = asset.exists()
        asset.unlink(missing_ok=True)
        if not was_active:
            return  # Normal disabled builds do not scan or read the HTML tree.
    for path in out.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        clean = BLOCK.sub("", text)
        if base and ('class="getdeal"' in clean or path == out / "index.html"):
            snippet = ('<!-- wp-click-signal --><script defer src="/click-signal.js" '
                       f'data-click-endpoint="{escape(base)}/c"></script><!-- /wp-click-signal -->\n')
            clean = clean.replace("</head>", snippet + "</head>", 1)
        if clean != text:
            path.write_text(clean, encoding="utf-8")
