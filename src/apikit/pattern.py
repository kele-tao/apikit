r"""A small, dependency-free regex sampler used for JSON Schema 'pattern'.

Only the constructs that appear in real OpenAPI patterns are supported:

  literal characters      abc
  escaped classes         \d \D \w \W \s \S \. \\ \/ \+ \- \_
  character classes       [a-z0-9_] [^0-9]
  groups and alternation  (foo|bar) (?:x)
  quantifiers             * + ? {n} {n,m} {n,}
  anchors                 ^ and $ (ignored)

Anything else raises :class:PatternTooComplex and the caller falls back to a
documented canned value. The sampler is deterministic for a given RNG.
"""

from __future__ import annotations

import random

MAX_REPEAT = 64


class PatternTooComplex(ValueError):
    """The pattern uses a construct the sampler does not implement."""


_DIGITS = "0123456789"
_WORD = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
_SPACE = " \t"


class _Parser:
    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self.pos = 0

    def parse(self) -> list[tuple[str, object]]:
        items = self._sequence()
        if self.pos != len(self.pattern):
            raise PatternTooComplex(f"unparsed trailing input at {self.pos}: {self.pattern!r}")
        return items

    def _peek(self) -> str:
        return self.pattern[self.pos] if self.pos < len(self.pattern) else ""

    def _next(self) -> str:
        char = self.pattern[self.pos]
        self.pos += 1
        return char

    def _sequence(self) -> list[tuple[str, object]]:
        items: list[tuple[str, object]] = []
        while self.pos < len(self.pattern):
            char = self._peek()
            if char in "|)":
                break
            atom = self._atom()
            items.append(atom)
            quantifier = self._quantifier()
            if quantifier:
                items[-1] = ("repeat", (atom, quantifier))
        return items

    def _atom(self) -> tuple[str, object]:
        char = self._next()
        if char == "(":
            if self._peek() == "?":
                self._next()
                if self._peek() != ":":
                    raise PatternTooComplex("lookarounds and named groups are not supported")
                self._next()
            options = [self._sequence()]
            while self._peek() == "|":
                self._next()
                options.append(self._sequence())
            if self._peek() != ")":
                raise PatternTooComplex("unbalanced group")
            self._next()
            return ("group", options)
        if char == "[":
            return ("class", self._char_class())
        if char == "\\":
            return ("class", self._escape())
        if char == "^":
            return ("empty", "")
        if char == "$":
            return ("empty", "")
        if char == ".":
            return ("class", _WORD)
        return ("literal", char)

    def _char_class(self) -> str:
        negate = False
        if self._peek() == "^":
            self._next()
            negate = True
        chars: list[str] = []
        first = True
        while self.pos < len(self.pattern):
            char = self._next()
            if char == "]" and not first:
                break
            first = False
            if char == "\\":
                chars.extend(self._escape())
                continue
            has_range = (
                self._peek() == "-"
                and self.pos + 1 < len(self.pattern)
                and self.pattern[self.pos + 1] != "]"
            )
            if has_range:
                self._next()
                end = self._next()
                start_code = ord(char)
                end_code = ord(end)
                if end_code < start_code or end_code - start_code > 128:
                    raise PatternTooComplex("invalid character range")
                chars.extend(chr(code) for code in range(start_code, end_code + 1))
                continue
            chars.append(char)
        if not chars:
            raise PatternTooComplex("empty character class")
        if negate:
            universe = _WORD + _DIGITS + "-. @:/"
            chars = [item for item in universe if item not in chars]
        return "".join(chars)

    def _escape(self) -> str:
        char = self._next()
        mapping = {"d": _DIGITS, "w": _WORD, "s": _SPACE}
        inverted = {"D": _WORD + " ", "W": " -_.@", "S": _WORD + "0123456789"}
        if char in mapping:
            return mapping[char]
        if char in inverted:
            return inverted[char]
        if char in "nrt":
            return {"n": "\n", "r": "\r", "t": "\t"}[char]
        return char

    def _quantifier(self) -> tuple[int, int] | None:
        char = self._peek()
        if char == "*":
            self._next()
            return (0, 3)
        if char == "+":
            self._next()
            return (1, 3)
        if char == "?":
            self._next()
            return (0, 1)
        if char == "{":
            self._next()
            digits = ""
            while self.pos < len(self.pattern) and self._peek() != "}":
                digits += self._next()
            if self._peek() != "}":
                raise PatternTooComplex("unterminated quantifier")
            self._next()
            low, _, high = digits.partition(",")
            try:
                minimum = int(low)
                maximum = int(high) if high else minimum
            except ValueError as exc:
                raise PatternTooComplex(f"bad quantifier {{{digits}}}") from exc
            if minimum > MAX_REPEAT or maximum > MAX_REPEAT:
                raise PatternTooComplex("quantifier too large to sample")
            return (minimum, max(minimum, maximum))
        return None


def _render(items: list[tuple[str, object]], rng: random.Random) -> str:
    parts: list[str] = []
    for kind, payload in items:
        if kind == "literal":
            parts.append(str(payload))
        elif kind == "class":
            chars = str(payload)
            parts.append(rng.choice(chars))
        elif kind == "empty":
            continue
        elif kind == "group":
            parts.append(_render(rng.choice(payload), rng))  # type: ignore[arg-type]
        elif kind == "repeat":
            atom, limits = payload  # type: ignore[misc]
            low, high = limits
            for _ in range(rng.randint(low, high)):
                parts.append(_render([atom], rng))
    return "".join(parts)


def sample(pattern: str, rng: random.Random) -> str:
    """Return one deterministic string matching *pattern*.

    Raises :class:PatternTooComplex when the pattern uses unsupported syntax.
    """
    parsed = _Parser(pattern).parse()
    value = _render(parsed, rng)
    if not value:
        raise PatternTooComplex("pattern sampled the empty string")
    return value
