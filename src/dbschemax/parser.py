"""Parser for textual functional dependencies.

Converts strings such as ``"A, B -> C"`` into :class:`FunctionalDependency`.
Pure text handling: no database or UI code.
"""

from __future__ import annotations

from dbschemax.models import FunctionalDependency

EMPTY_SET_SYMBOL = "∅"
_ARROWS = ("->", "→")


class FDParseError(ValueError):
    """Raised when a functional dependency string is malformed."""


def _parse_side(text: str, side: str) -> frozenset[str]:
    """Parse one comma-separated attribute list.

    ``∅`` alone (on the left side only) denotes the empty set.
    """
    text = text.strip()
    if side == "left" and text == EMPTY_SET_SYMBOL:
        return frozenset()
    if not text:
        if side == "right":
            raise FDParseError("right-hand side must not be empty")
        raise FDParseError(
            f"left-hand side is empty; write {EMPTY_SET_SYMBOL} for an empty set"
        )
    names: set[str] = set()
    for part in text.split(","):
        name = part.strip()
        if not name:
            raise FDParseError(f"empty attribute name in {side}-hand side: {text!r}")
        if name == EMPTY_SET_SYMBOL:
            raise FDParseError(
                f"{EMPTY_SET_SYMBOL} cannot be combined with other attributes "
                f"or used on the {side}-hand side"
            )
        if any(ch.isspace() for ch in name):
            raise FDParseError(
                f"attribute {name!r} contains whitespace; missing comma?"
            )
        if ">" in name or "→" in name:
            raise FDParseError(f"malformed arrow near attribute {name!r}")
        names.add(name)
    return frozenset(names)


def parse_fd(text: str) -> FunctionalDependency:
    """Parse ``"X -> Y"`` (ASCII ``->`` or ``→``) into a FunctionalDependency.

    Raises:
        TypeError: if ``text`` is not a string.
        FDParseError: (a ``ValueError``) for any malformed input.
    """
    if not isinstance(text, str):
        raise TypeError(f"FD text must be a string, got {type(text).__name__}")
    arrow_count = sum(text.count(a) for a in _ARROWS)
    if arrow_count == 0:
        raise FDParseError(f"missing arrow ('->' or '→') in {text!r}")
    if arrow_count > 1:
        raise FDParseError(f"multiple arrows in {text!r}")
    arrow = next(a for a in _ARROWS if a in text)
    left, right = text.split(arrow, 1)
    if left.rstrip().endswith("-"):
        raise FDParseError(f"malformed arrow in {text!r}")
    return FunctionalDependency(_parse_side(left, "left"), _parse_side(right, "right"))
