"""Cooperative, bounded heuristics. No token here is evidence for extraction.

Unknown invalidates the WHOLE analysis: dropping a third bad number must never
leave an apparently unambiguous below-floor pair. See docs/bounded-prefilter.md.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time


@dataclass(frozen=True)
class Analysis:
    state: str  # known | unknown | exhausted
    reason: str = ""
    below_floor: bool = False
    second_opinion: bool = True
    visited_chars: int = 0
    numeric_tokens: int = 0
    price_pairs: int = 0

    def counters(self) -> dict:
        return {"posts": 1, "known": int(self.state == "known"),
                "unknown": int(self.state == "unknown"),
                "exhausted": int(self.state == "exhausted"),
                "visited_chars": self.visited_chars,
                "numeric_tokens": self.numeric_tokens, "price_pairs": self.price_pairs}


@dataclass(frozen=True)
class _Token:
    kind: str
    start: int
    end: int
    value: str
    sentence: int


class _Stopped(Exception):
    def __init__(self, state, reason):
        self.state, self.reason = state, reason


def analyze(text: str, floor: float, limits: dict, deadline: float | None = None) -> Analysis:
    """Visit at most max_chars forward positions, count all numeric runs, and
    evaluate at most max_price_pairs through savings.py. Check the supplied
    monotonic deadline between characters, tokens and pairs (no regex timeout).
    """
    visited = numeric = pairs = 0

    def check():
        if deadline is not None and time.monotonic() >= deadline:
            raise _Stopped("exhausted", "deadline")

    def advance():
        nonlocal visited
        check()
        if visited >= limits["max_chars"]:
            raise _Stopped("exhausted", "characters")
        visited += 1

    def unknown(reason="number"):
        raise _Stopped("unknown", reason)

    try:
        check()
        tokens = []
        i = sentence = 0
        while i < len(text):
            start, ch = i, text[i]
            if ch.isspace():
                advance()
                i += 1
                if ch == "\n":
                    sentence += 1
                continue
            if ch.isdigit():
                if numeric >= limits["max_numeric_tokens"]:
                    raise _Stopped("exhausted", "tokens")
                numeric += 1
                while i < len(text) and (text[i].isdigit() or text[i] in ".," and (
                        text[i] == "," or i + 1 < len(text) and (text[i + 1].isdigit() or text[i + 1] in ".,"))):
                    if i - start >= limits["max_numeric_chars"]:
                        raise _Stopped("exhausted", "token_length")
                    advance()
                    i += 1
                value = text[start:i]
                # No signs, scientific notation, Unicode digits, partial
                # decimals, malformed thousands groups or attached model IDs.
                percent_word = (text[i:i + 7].lower() == "percent" and (i + 7 == len(text)
                                or not (text[i + 7].isalnum() or text[i + 7] == "_")))
                bad_left = start and (text[start - 1].isalnum() or text[start - 1] in "_.,")
                bad_right = i < len(text) and (text[i].isalnum() and not percent_word or text[i] == "_")
                if not value.isascii() or bad_left or bad_right:
                    unknown()
                whole, dot, decimal = value.partition(".")
                groups = whole.split(",")
                if (not whole or not all(g.isdigit() for g in groups)
                        or len(groups) > 1 and (not 1 <= len(groups[0]) <= 3
                                               or any(len(g) != 3 for g in groups[1:]))
                        or dot and (not decimal.isdigit())):
                    unknown()
                if not math.isfinite(float(value.replace(",", ""))):
                    unknown()
                kind = "number"
            elif ch.isalpha() or ch == "_":
                while i < len(text) and (text[i].isalpha() or text[i] == "_"):
                    advance()
                    i += 1
                value, kind = text[start:i].lower(), "word"
            else:
                advance()
                i += 1
                value, kind = ch, "symbol"
            tokens.append(_Token(kind, start, i, value, sentence))
            if (ch in ".!?" and (i == len(text) or text[i].isspace())
                    and not (ch == "." and len(tokens) > 1 and tokens[-2].value == "reg"
                             and tokens[-2].end == start)):
                sentence += 1

        words = []
        range_sentences = set()
        deal_words = extra = ambiguous = False
        free = smell = pct_present = clear = False
        prices = []

        def label(index):
            j = index - 1
            if j >= 0 and tokens[j].value in {":", "="}:
                j -= 1
            if j >= 0 and tokens[j].value == "price":
                j -= 1
            dotted = j >= 0 and tokens[j].value == "."
            if dotted:
                if j == 0 or tokens[j - 1].end != tokens[j].start:
                    return "", 0
                j -= 1
            if j >= 0 and tokens[j].kind == "word":
                value = tokens[j].value
                if value in {"was", "reg", "regular", "regularly", "list", "msrp"}:
                    return "regular", tokens[j].start
                if not dotted and value in {"now", "sale", "only", "just", "for"}:
                    return "final", tokens[j].start
            return "", 0

        for j, token in enumerate(tokens):
            check()
            prev = tokens[j - 1] if j else None
            after = tokens[j + 1] if j + 1 < len(tokens) else None
            if token.kind == "word":
                w = token.value
                words.append(w)
                if w == "between":
                    range_sentences.add(token.sentence)
                deal_words |= w in {"deal", "sale", "off", "coupon", "promo", "clearance",
                                    "discount", "save", "free", "was", "reg", "regular",
                                    "regularly", "list", "msrp"}
                extra |= (w in {"coupon", "coupons", "code", "codes", "promo", "extra", "rebate", "rebates"}
                          or w.startswith(("clip", "stack", "subscri")))
                extra |= j >= 2 and [t.value for t in tokens[j - 2:j + 1]] == ["s", "&", "s"]
                ambiguous |= w in {"range", "roundup", "bogo", "bundle", "bundles"}
                ambiguous |= any(words[-len(phrase):] == phrase for phrase in (
                    ["gift", "card"], ["gift", "cards"], ["up", "to"], ["as", "low", "as"],
                    ["starting", "at"], ["starting", "from"], ["more", "deals"]))
            if token.value == "free":
                excluded = (after and after.value in {"shipping", "delivery", "return", "returns", "trial", "trials"}
                            and text[token.end:after.start].isspace())
                free |= not excluded
            if token.value == "$":
                if not after or after.kind != "number" or text[token.end:after.start].strip():
                    unknown("money")
                if prev and (prev.value in {"-", "−", "+", "–", "—", "/"}
                             or prev.end == token.start and (prev.kind == "word" or prev.kind == "number")):
                    unknown("money")
            if token.value in {"€", "£", "¥", "＄"}:
                unknown("currency")
            if token.value in {"nan", "inf", "infinity"} and after and after.value in {"%", "percent"}:
                unknown("number")
            if token.kind != "number":
                continue
            money = prev is not None and prev.value == "$"
            left = tokens[j - 2] if money and j >= 2 else prev
            if ((left and left.value in {"-", "−", "+", "–", "—", "/", "~"})
                    or after and after.value in {"-", "−", "+", "–", "—", "/", "~"}
                    or after and after.value == "$" and after.start == token.end):
                unknown("range_or_sign")
            if money and (left and left.value == "to" or after and after.value == "to"):
                unknown("range_or_sign")
            if money and token.sentence in range_sentences and after and after.value == "and":
                unknown("range_or_sign")
            if (money and after and after.kind == "number" and not (
                    j + 2 < len(tokens) and tokens[j + 2].value in {"%", "percent"})):
                unknown("number")
            pct = after is not None and after.value in {"%", "percent"}
            value = float(token.value.replace(",", ""))
            if pct:
                if money or not 0 <= value <= 100:
                    unknown("percent")
                pct_present = True
                clear |= value >= floor
                smell |= after.start - token.end <= 1
            if money:
                smell |= token.start - prev.end <= 1
                free |= value == 0
                role, label_start = label(j - 1)
                prices.append((token, value, role, label_start))
            if prev and prev.value in {"buy", "purchase"}:
                ambiguous = True

        # No known decision can hide an unscanned or invalid number.
        clear |= free
        pair_pct = None
        if not clear:
            from .extractor import _price_pair_pct

            by_sentence = {}
            for price in prices:
                check()
                by_sentence.setdefault(price[0].sentence, []).append(price)
            for group in by_sentence.values():
                for j, (first, value, _, _) in enumerate(group):
                    for k in range(j + 1, len(group)):
                        check()
                        second, other, _, _ = group[k]
                        if pairs >= limits["max_price_pairs"]:
                            raise _Stopped("exhausted", "pairs")
                        pairs += 1
                        pct = _price_pair_pct(value, other)
                        if pct is None or not math.isfinite(pct):
                            unknown("arithmetic")
                        if len(prices) == 2:
                            pair_pct = pct
                        if pct >= floor:
                            clear = True
                            break
                    if clear:
                        break
                if clear:
                    break
        below = False
        if not (extra or ambiguous or pct_present or free) and len(prices) == 2 and pair_pct is not None:
            regulars = [p for p in prices if p[2] == "regular"]
            if len(regulars) == 1:
                regular = regulars[0]
                final = next(p for p in prices if p is not regular)
                bare = (final[0].end <= regular[3]
                        and all(c.isspace() or c in ",;([" for c in text[final[0].end:regular[3]]))
                below = (final[2] == "final" or bare) and final[1] <= regular[1] and pair_pct < floor - 10
                below &= all(text[p[0].end:p[0].end + 1] not in {".", ","} for p in prices)
        check()
        return Analysis("known", below_floor=bool(below), second_opinion=bool((smell and deal_words or free) and clear),
                        visited_chars=visited, numeric_tokens=numeric, price_pairs=pairs)
    except _Stopped as stop:
        return Analysis(stop.state, stop.reason, visited_chars=visited, numeric_tokens=numeric, price_pairs=pairs)
