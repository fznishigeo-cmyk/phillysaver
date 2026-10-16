"""Build the actual publisher's synthetic listing site, without network access."""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.test_android_app import build_app


def main():
    out = Path(sys.argv[1]).resolve()
    if not out.name.startswith("task246-") or out.parent != Path("/tmp"):
        raise SystemExit("Use a dedicated /tmp/task246-* directory")
    result = build_app(out)
    best = next(d for d in result["public_deals"] if d["store"] == "Bestbuy")
    for path in out.rglob("*.html"):
        page = path.read_text()
        label = ('<p style="padding:10px 16px;background:#fff4cc;color:#463300;font:16px system-ui">'
                 '<img src="/example-brand.png" width="24" height="24" alt=""> '
                 'EXAMPLE DATA · Local demonstration · No live retailer price</p>')
        page = page.replace('<body>', '<body>' + label, 1)
        path.write_text(page)
    # Reuse the extension's existing brand icon read-only; never modify its files.
    shutil.copyfile(ROOT / 'extension/icons/128.png', out / 'example-brand.png')
    (out / 'store-fixture.json').write_text(json.dumps({"deal": best["deal_path"],
        "url": "https://www.bestbuy.com/site/example/6505727.p"}))
    print("Built local example site; no external request.")


if __name__ == '__main__':
    main()
