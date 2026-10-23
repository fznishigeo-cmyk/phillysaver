"""Operator 2026-10-23 ~20:05Z (screenshots of both analyzer boxes): "both products, no matter
if they search a keyword or a url should go through the analzyer process"; ~20:10Z (screenshots
of the "Shopping for headphones?" box): "no this will still come up. just make them wait for it".

On Fake Deal Spotter and Review Checker, typed words open the same analyzer box as a link, with
the same wait (10-25 s), and steps about what is searched (no price or review claim about one
product). At 100% the box closes into today's words box (tools_merge), unchanged. Typed words
still never call /api/search (operator rule 2026-10-22). Adult or unusable words get no box, so
no analyzer. Needs tools_merge; tool_words_analyzer false/missing = previous bytes.
"""

from pathlib import Path

from . import tools_merge
from .template_slots import replace_slot

TEMPLATES = Path(__file__).with_name("templates")
SPOTTER = (TEMPLATES / "tool-words-analyzer.js").read_text()
REVIEW = (TEMPLATES / "tool-words-analyzer-review.js").read_text()
SPOTTER_SUBMIT = "  function submit(raw, field, opener) {\n"
SPOTTER_WORDS = ("      $('input-error').textContent = ''; $('start').hidden = false; $('spotter-result').hidden = true; $('spotter-story').hidden = false;\n"
                 "      button.focus(); return;\n")
REVIEW_SUBMIT = "  function submit(value, knownTitle, field) {\n"
REVIEW_WORDS = ("      if (button) {input.removeAttribute('aria-invalid'); heroInput.removeAttribute('aria-invalid'); message.textContent = '';"
                " button.focus(); return;}\n")
# The landing copy promised the buttons at once.
LANDING = ("Typed words never start a price lookup, so the buttons appear right away. ",
           "Typed words never start a price lookup. ")


def enabled(cfg):
    return getattr(cfg, "tool_words_analyzer", False) is True and tools_merge.enabled(cfg)


def spotter_script(script, cfg):
    if not enabled(cfg):
        return script
    script = replace_slot(script, SPOTTER_SUBMIT, SPOTTER + SPOTTER_SUBMIT, template_name="deal-spotter.js", slot_name="words-analyzer")
    return replace_slot(script, SPOTTER_WORDS, SPOTTER_WORDS.replace("button.focus();", "analyzeWords(raw, field, opener);"),
                        template_name="deal-spotter.js", slot_name="words-start")


def review_script(script, cfg):
    if not enabled(cfg):
        return script
    script = replace_slot(script, REVIEW_SUBMIT, REVIEW + REVIEW_SUBMIT, template_name="review-checker.js", slot_name="words-analyzer")
    return replace_slot(script, REVIEW_WORDS, REVIEW_WORDS.replace("button.focus();", "analyzeWords(value);"),
                        template_name="review-checker.js", slot_name="words-start")


def landing_page(html, cfg):
    return html.replace(*LANDING) if enabled(cfg) else html
