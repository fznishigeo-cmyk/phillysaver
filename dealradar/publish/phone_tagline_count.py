"""Task 324: shorten only the phone's live-count tagline; OFF preserves bytes."""

from .template_slots import replace_slot

CSS = '.tag-short{display:none}'
PHONE_CSS = ('.tag-full{display:none}.tag-short{display:inline}'
             '#tagline:has(.tag-short){min-width:200px}')


def enabled(cfg):
    return getattr(cfg, 'phone_tagline_count', False) is True


def template(html, cfg):
    if not enabled(cfg):
        return html

    def swap(old, new):
        nonlocal html
        html = replace_slot(html, old, new, template_name='index.html', slot_name='phone-tagline-count')

    swap('    tagline.textContent = "verified deals";',
         '    tagline.textContent = "";\n'
         '    var tagLead = document.createElement("span");\n'
         '    tagLead.textContent = "verified deals";\n'
         '    tagline.appendChild(tagLead);')
    for value in ('document.createTextNode(", ")', 'off', 'document.createTextNode(" · ")'):
        swap('tagline.appendChild(' + value + ');', 'tagLead.appendChild(' + value + ');')
    swap('    var liveDot = document.createElement("span");',
         '    tagLead.className = live ? "tag-full tag-lead" : "";\n'
         '    var liveDot = document.createElement("span");')
    old = ('    tagline.appendChild(document.createTextNode(live ? (live < 100 ? plural(live, "live deal") '
           ': roughCount(live) + " live deals") : "Offers checked before publication"));')
    swap(old,
         '    var tagCount = document.createElement("span");\n'
         '    tagCount.className = live ? "tag-full tag-count" : "";\n'
         '    tagCount.textContent = live ? (live < 100 ? plural(live, "live deal") '
         ': roughCount(live) + " live deals") : "Offers checked before publication";\n'
         '    tagline.appendChild(tagCount);\n'
         '    if (live) {\n'
         '      var tagShort = document.createElement("span");\n'
         '      tagShort.className = "tag-short";\n'
         '      tagShort.textContent = live < 100 ? plural(live, "verified deal") : roughCount(live) + " verified deals";\n'
         '      tagline.appendChild(tagShort);\n'
         '    }')
    # Reuse the existing phone media block to stay within the CSS gzip budget.
    swap('@media (max-width:899px){', CSS + '@media (max-width:899px){' + PHONE_CSS)
    if getattr(cfg, 'header_polish', False) is True:
        # The header asset already sets border:0;padding:0 on .hnav>.tag at
        # both breakpoints. Drop only those superseded tagline declarations
        # to pay for the phone count's width, leaving other headers untouched.
        swap('border-left:1px solid var(--line);padding-left:14px;min-height:20px', 'min-height:20px')
        swap('font-size:13px;border-left:0;padding-left:0;min-height:40px', 'font-size:13px;min-height:40px')
    return html
