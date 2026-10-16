"""Offline fixed-fixture profile. Run from the checkout with its Python."""

import cProfile
import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dealradar.db import DB
from dealradar.publish import site
from dealradar import rank
from tests.test_publish_link_facts import NOW, config, facts


def profile(n, unique, enabled, *, counted=True):
    cfg = config("open")
    cfg.reuse_link_facts_enabled = enabled
    # Limit disk/page work equally so CPU/memory includes the complete wall,
    # without thousands of detail pages dominating this link-work comparison.
    cfg.seo_deal_pages_max = 24
    cfg.seo_product_pages_enabled = False
    cfg.seo_event_pages_enabled = False
    rows = facts(n, unique=unique)
    site.util_now = lambda: NOW
    # Warm source policy caches equally, outside the measured render.
    site._serialize_deal(rows[1], NOW, cfg.affiliate_amazon_tag,
                         sovrn_key=cfg.affiliate_sovrn_key, sovrn_enabled=True)
    profiler = cProfile.Profile()
    with tempfile.TemporaryDirectory(prefix="task225-profile-") as tmp:
        db = DB(":memory:")
        try:
            before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
            cpu = time.process_time()
            if counted:
                profiler.enable()
            result = site.render_site(db, cfg, NOW, out_dir=Path(tmp) / "site", deals=rows)
            if counted:
                profiler.disable()
            cpu = time.process_time() - cpu
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        finally:
            db.close()
    names = {"_serialize_deal", "derive_link_facts", "rewrite_affiliate", "source_sightings",
             "_credit", "_needs_steps", "_simple_code_deal", "routing_key"}
    calls = {f"{Path(s.code.co_filename).name}:{s.code.co_name}": s.callcount
             for s in profiler.getstats() if not isinstance(s.code, str) and s.code.co_name in names}
    return dict(n=n, unique=unique, enabled=enabled, counted=counted, cpu_s=round(cpu, 3),
                process_peak_rss_bytes=peak, before_render_peak_rss_bytes=before,
                calls=calls, published=result["published"])


if __name__ == "__main__":
    mode = sys.argv[1]
    if "--case" not in sys.argv:
        # Fresh process per case: process peak RSS cannot carry over a prior render.
        for n in (2300, 5000):
            for unique in (False, True):
                subprocess.run([sys.executable, __file__, *sys.argv[1:],
                                "--case", str(n), str(int(unique))], check=True)
    else:
        if mode == "baseline":
            revision = sys.argv[2]
            # Execute only this task's original owners, with the same templates,
            # configuration and fixture; never check out or change shared files.
            for module in (rank, site):
                relative = str(Path(module.__file__).relative_to(Path.cwd()))
                source = subprocess.check_output(["git", "show", f"{revision}:{relative}"])
                exec(compile(source, module.__file__, "exec"), module.__dict__)
        pos = sys.argv.index("--case")
        print(json.dumps(profile(int(sys.argv[pos+1]), bool(int(sys.argv[pos+2])), mode == "on",
                                 counted="--measure" not in sys.argv)), flush=True)
