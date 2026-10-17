"""Final home-only comment removal; literal contents and line terminators survive.

Marker audit: tracking, click_signal, inventory, profiles, traffic, _pwa_web
and android_app re-read their wp-* wrappers on subsequent passes; Android and
dark-mode tests also locate them. site/amazon_history/ai_take consume the
unspaced template markers before this pass. Retain all of those exact names.
"""

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path


MARKERS = frozenset((
    "wp-tracking", "wp-click-signal", "wp-inventory", "wp-profiles",
    "wp-traffic", "wp-pwa-head", "wp-pwa-footer", "wp-android",
    "standard", "support", "open", "paywall", "engagement", "saved", "watch",
    "history-shown", "ai-take",
))
_LINE = re.compile(r"\r\n|[\r\n\u2028\u2029]")
_BLANKS = re.compile(r"(\r?\n|\r(?!\n)|[\u2028\u2029])"
                     r"(?:[^\S\r\n\u2028\u2029]*(?:\r?\n|\r(?!\n)|[\u2028\u2029])){2,}")
_WORD = re.compile(r"[\w$]+")
_REGEX_PREFIX = frozenset(("return", "throw", "case", "delete", "void", "typeof",
                           "instanceof", "in", "of", "yield", "await", "else", "do", "new"))
_CONTROL = frozenset(("if", "while", "for", "with", "switch", "catch"))


def _blanks(text):
    return _BLANKS.sub(lambda m: m[1] * 2, text)


def _quoted(text, start):
    quote, at = text[start], start + 1
    while at < len(text):
        if text[at] == "\\":
            at += 2
        elif text[at] == quote:
            return at + 1
        else:
            at += 1
    return len(text)


def _regex(text, start):
    """A JS regex cannot cross a line terminator, including after a backslash."""
    at, bracket = start + 1, False
    while at < len(text) and text[at] not in "\r\n\u2028\u2029":
        char = text[at]
        if char == "\\":
            if at + 1 == len(text) or text[at + 1] in "\r\n\u2028\u2029":
                break
            at += 2
            continue
        if char == "/" and not bracket:
            at += 1
            while at < len(text) and (text[at].isalnum() or text[at] in "_$"):
                at += 1
            return at
        if char == "[":
            bracket = True
        elif char == "]":
            bracket = False
        at += 1
    return start + 1


def _template(text, start):
    # Keep the entire literal, including interpolation code. Nested templates
    # and quoted/regex braces in ${...} must not end its protected span early.
    at = start + 1
    while at < len(text):
        if text[at] == "\\":
            at += 2
        elif text[at] == "`":
            return at + 1
        elif text.startswith("${", at):
            at = _javascript(text, at + 2, interpolation=True)[1]
        else:
            at += 1
    return at


def _javascript(text, start=0, *, interpolation=False):
    edits, at, depth, expression, previous, parens = [], start, 0, True, "", []
    braces, function = [], None
    while at < len(text):
        begin, char = at, text[at]
        if char in "\"'`":
            at = _template(text, at) if char == "`" else _quoted(text, at)
            expression, previous = False, "literal"
            continue
        if text.startswith("//", at) or text.startswith("/*", at):
            line = text.startswith("//", at)
            newline = _LINE.search(text, at)
            end = (newline.start() if newline else len(text)) if line else text.find("*/", at + 2)
            if end < 0:
                end = len(text)
            elif not line:
                end += 2
            first = not text[max(text.rfind(c, 0, at) for c in "\r\n\u2028\u2029") + 1:at].strip()
            newline = _LINE.search(text, end)
            last = not text[end:newline.start() if newline else len(text)].strip()
            if first and (line or last):
                # Newlines keep automatic semicolon insertion unchanged.
                edits.append((at, end, "".join(m[0] for m in _LINE.finditer(text, at, end))))
            at = end
            continue
        if char == "/" and expression:
            at = _regex(text, at)
            if at > begin + 1:
                expression, previous = False, "literal"
                continue
        if char.isspace():
            while at < len(text) and text[at].isspace():
                at += 1
            edits.append((begin, at, _blanks(text[begin:at])))
            continue
        word = _WORD.match(text, at)
        if word:
            if word[0] == "function" and previous != ".":
                function = "function-decl" if previous in ("", ";", "{", "}") else "function-expr"
            previous = word[0] if previous != "." else "property"
            expression = previous in _REGEX_PREFIX
            at = word.end()
            continue
        if text.startswith("=>", at):
            previous, expression, at = "=>", True, at + 2
            continue
        if char == "(":
            parens.append(function or ("control" if previous in _CONTROL else "expression"))
            function = None
            expression = True
        elif char == ")":
            kind = parens.pop() if parens else "expression"
            expression = kind == "control"
            if kind.startswith("function-"):
                previous, at = kind, at + 1
                continue
        elif char == "{":
            depth += 1
            braces.append(previous in ("", ";", "{", "}", ")", "else", "try", "finally", "do", "function-decl")
                          or (not expression and previous != "function-expr"))
            expression = True
        elif char == "}":
            if interpolation and depth == 0:
                return edits, at + 1
            depth -= 1
            expression = braces.pop() if braces else True
        elif text[at:at + 2] in ("++", "--"):
            at += 1
        else:
            expression = char not in "]."
        previous = char
        at += 1
    return edits, at


def _apply(text, edits):
    parts, at = [], 0
    for start, end, replacement in edits:
        parts.extend((text[at:start], replacement))
        at = end
    return "".join(parts) + text[at:]


def javascript(text):
    return _apply(text, _javascript(text)[0])


def css(text):
    edits, at = [], 0
    while at < len(text):
        start = at
        if text[at] in "\"'":
            at = _quoted(text, at)
        elif text[at] == "\\":
            at += 2
        elif re.match(r"url\(", text[at:at + 4], re.I):
            at += 4
            while at < len(text) and text[at] != ")":
                if text[at] in "\"'":
                    at = _quoted(text, at)
                else:
                    at += 2 if text[at] == "\\" else 1
            at += 1
        elif text.startswith("/*", at):
            end = text.find("*/", at + 2)
            at = len(text) if end < 0 else end + 2
            # Adjacent identifiers must not become a different token.
            separator = (start > 0 and at < len(text)
                         and re.match(r"[\w-]", text[start - 1])
                         and re.match(r"[\w-]", text[at]))
            edits.append((start, at, " " if separator else ""))
        elif text[at].isspace():
            while at < len(text) and text[at].isspace():
                at += 1
            edits.append((start, at, _blanks(text[start:at])))
        else:
            at += 1
    return _apply(text, edits)


class _Home(HTMLParser):
    def __init__(self, source, *, blank_lines=False):
        super().__init__(convert_charrefs=False)
        self.source, self.blank_lines, self.edits = source, blank_lines, []
        self.lines = [0] + [m.end() for m in re.finditer("\n", source)]
        self.raw, self.preserve = None, []

    def _edit(self, length, value):
        line, column = self.getpos()
        start = self.lines[line - 1] + column
        self.edits.append((start, start + length, value))

    def handle_starttag(self, tag, attrs):
        if tag in ("pre", "textarea"):
            self.preserve.append(tag)
        if tag in ("style", "script"):
            attrs = dict(attrs)
            kind = (attrs.get("type") or "").lower().split(";")[0].strip()
            self.raw = ("css" if tag == "style" else "js" if "src" not in attrs and kind in (
                "", "module", "text/javascript", "application/javascript") else "data")

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self.raw = None
        if self.preserve and tag == self.preserve[-1]:
            self.preserve.pop()

    def handle_data(self, data):
        if self.blank_lines:
            if not self.raw and not self.preserve:
                self._edit(len(data), _blanks(data))
        elif self.raw in ("js", "css"):
            self._edit(len(data), (javascript if self.raw == "js" else css)(data))

    def handle_comment(self, data):
        marker = data.strip().removeprefix("/")
        if not self.blank_lines and not self.raw and not self.preserve and marker not in MARKERS and not re.search(
                r"\[if\b|\[endif\]", data, re.I):
            self._edit(len(data) + 7, "")

    def result(self):
        self.feed(self.source)
        self.close()
        return _apply(self.source, self.edits)


def strip(home):
    # The second lexical pass joins whitespace exposed by removed comments,
    # without collapsing newlines inside literals or preformatted HTML.
    return _Home(_Home(_Home(home).result()).result(), blank_lines=True).result()


def site_version(version, cfg):
    if not cfg.strip_comments:
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]


def publish(out, cfg):
    if cfg.strip_comments:
        path = out / "index.html"
        before = path.read_text(encoding="utf-8")
        after = strip(before)
        if after != before:
            path.write_text(after, encoding="utf-8")
