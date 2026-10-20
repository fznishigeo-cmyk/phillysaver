"""Task 340: rounded See all count without the rotation advertisement."""
import hashlib
from pathlib import Path

from . import drop_fresh
from .template_slots import replace_slot

SCRIPT = Path(__file__).with_name('templates') / 'fresh-more-plain.js'


def enabled(cfg):
    return getattr(cfg, 'fresh_more_plain', False) is True and drop_fresh.enabled(cfg)


def count_label(total):
    # Match index.html's roughCount(), including its steady 1,000+ ceiling.
    count = '1,000+' if total >= 1000 else f'{total // 100 * 100:,}+' if total >= 100 else str(total)
    return f'{count} deal{"" if total == 1 else "s"}'


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + SCRIPT.read_bytes()
                          + Path(__file__).read_bytes()).hexdigest()[:12]


def publish(out, cfg):
    if not enabled(cfg):
        return
    # Keep the legacy source/hash inputs intact for byte-exact OFF publication.
    asset = out / drop_fresh.ASSET
    script = asset.read_text(encoding='utf-8')
    old_version = hashlib.sha256(script.encode()).hexdigest()[:12]
    start, end = script.index('  function freshCycleText()'), script.index('  function updateFreshDrop(')
    script = replace_slot(script, script[start:end], SCRIPT.read_text(),
                          template_name=drop_fresh.ASSET, slot_name='fresh-more-plain')
    version = hashlib.sha256(script.encode()).hexdigest()[:12]
    home = out / 'index.html'
    html = replace_slot(home.read_text(encoding='utf-8'), '/drop-fresh.js?v=' + old_version,
                        '/drop-fresh.js?v=' + version, template_name='index.html', slot_name='fresh-more-version')
    asset.write_text(script, encoding='utf-8')
    home.write_text(html, encoding='utf-8')
