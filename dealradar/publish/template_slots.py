"""Counted literal slots; diagnostics contain only safe developer names."""


def _safe_name(name: str, fallback: str) -> str:
    return name if (0 < len(name) <= 64 and all(
        c.isascii() and (c.isalnum() or c in "._-") for c in name)) else fallback


def validate_slot(template: str, token: str, expected_count: int = 1, *,
                  template_name: str, slot_name: str) -> None:
    if not token or type(expected_count) is not int or expected_count < 1:
        raise ValueError("invalid template slot contract")
    count = template.count(token)
    if count != expected_count:
        reason = "missing" if count < expected_count else "duplicate"
        raise ValueError(f"{_safe_name(template_name, 'template')}: {reason} slot "
                         f"{_safe_name(slot_name, 'slot')}")


def replace_slot(template: str, token: str, replacement: str, expected_count: int = 1, *,
                 template_name: str, slot_name: str) -> str:
    """Validate before one literal replacement pass; never parse its content."""
    validate_slot(template, token, expected_count,
                  template_name=template_name, slot_name=slot_name)
    return template.replace(token, replacement)
