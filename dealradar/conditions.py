"""Canonical condition labels, including explicitly stated used-item synonyms."""

import re

_CANONICAL = {"new_customer", "prime_only", "membership_only", "targeted", "subscribe_save", "in_cart",
              "limited_qty", "pricing_error", "in_store_only", "ymmv", "clearance_aisle",
              "multi_buy", "used", "open_box", "refurbished"}


def normalize_conditions(conditions) -> list[str]:
    if isinstance(conditions, str):
        conditions = [conditions]
    result = []
    for condition in conditions or []:
        label = re.sub(r"[\W_]+", "_", str(condition).strip().casefold()).strip("_")
        label = re.sub(r"^(?:item_)?condition_", "", label)
        if re.search(r"(?:^|_)(?:refurb(?:ished)?|reconditioned|renewed)(?:_|$)", label):
            label = "refurbished"
        elif label.startswith(("open_box", "openbox")):
            label = "open_box"
        elif label.startswith(("used", "pre_owned", "preowned", "amazon_warehouse", "amazon_resale",
                               "like_new", "second_hand", "secondhand", "previously_owned",
                               "pre_loved", "preloved")) or label in {"warehouse", "resale"}:
            label = "used"
        # A grade first ("Excellent - Pre-Owned", "Grade A - Like New") is still
        # used; an earlier label above keeps its old reading.
        elif re.search(r"(?:^|_)open_?box(?:_|$)", label):
            label = "open_box"
        elif re.search(r"(?:^|_)(?:used|pre_?owned|like_new|second_?hand|previously_owned|pre_?loved)(?:_|$)",
                       label):
            label = "used"
        if label not in _CANONICAL:
            label = str(condition).strip()
        if label and label not in result:
            result.append(label)
    return result


USED_CONDITIONS = frozenset({"used", "open_box", "refurbished"})


def condition_class(conditions) -> str:
    labels = set(normalize_conditions(conditions))
    for label in ("open_box", "refurbished", "used"):
        if label in labels:
            return label
    return "new"
