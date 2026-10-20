"""Operator 2026-10-20 FAQ wording on both tool pages: each page speaks only for itself."""

FREE = ("Yes. Both tools are free to use.", "Yes. This tool is free to use.")
NO_LIVE = (" The Discount Finder does not run a live price check.", "")
BEST = (" own discount results.", " best discount results.")


def enabled(cfg):
    return getattr(cfg, 'tool_faq_wording', False) is True


def text(value, cfg):
    if not enabled(cfg):
        return value
    for old, new in (FREE, NO_LIVE, BEST):
        value = value.replace(old, new)
    return value


def questions(items, cfg):
    return [(q, text(a, cfg)) for q, a in items]
