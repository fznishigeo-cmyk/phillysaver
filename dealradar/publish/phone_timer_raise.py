"""Task 330: align the phone timer with the tagline's first line, without reflow."""

from .template_slots import replace_slot

CSS = '.hrow:has(.pwa-header-actions)>.drop{top:-10px}'


def enabled(cfg):
    return getattr(cfg, 'phone_timer_raise', False) is True


def template(html, cfg):
    if not enabled(cfg):
        return html
    # Below 641px the tagline's 19.5px line sits atop a 40px minimum box:
    # its line centre is (40 - 19.5) / 2 = 10.25px above the centred timer.
    # Relative top preserves the 44px grid track and clears the 2px header line.
    slot = '@media (max-width:640px){\n  .spotlight-grid'
    return replace_slot(html, slot,
                        '@media (max-width:640px){' + CSS + '\n  .spotlight-grid',
                        template_name='index.html', slot_name='phone-timer-raise')
