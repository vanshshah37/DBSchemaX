"""Text input parsing shared by the CLI and the dashboard.

Pure functions with no CLI/UI imports. FD syntax is delegated to
:func:`dbschemax.parser.parse_fd`; this module only splits and validates the
surrounding text and reports errors in a user-friendly way.
"""

from __future__ import annotations

from typing import Iterable

from dbschemax.models import FunctionalDependency
from dbschemax.parser import EMPTY_SET_SYMBOL, FDParseError, parse_fd


class InputError(ValueError):
    """Raised for invalid user-supplied attribute or FD text."""


def parse_attribute_list(text: str) -> tuple[str, ...]:
    """Parse ``"A, B, C"`` into ``("A", "B", "C")`` (order preserved).

    Names are case-sensitive and may not contain whitespace, commas, arrows or
    the reserved ``∅`` symbol. Empty names and duplicates are rejected rather
    than silently repaired.

    Raises:
        TypeError: if ``text`` is not a string.
        InputError: for any invalid list.
    """
    if not isinstance(text, str):
        raise TypeError(f"attribute list must be a string, got {type(text).__name__}")
    if not text.strip():
        raise InputError("no attributes given")
    names = [part.strip() for part in text.split(",")]
    seen: set[str] = set()
    for name in names:
        if not name:
            raise InputError(f"empty attribute name in {text!r}")
        if any(ch.isspace() for ch in name):
            raise InputError(f"attribute {name!r} contains whitespace; missing comma?")
        if name == EMPTY_SET_SYMBOL or ">" in name or "→" in name:
            raise InputError(f"{name!r} is not a valid attribute name")
        if name in seen:
            raise InputError(f"duplicate attribute {name!r}")
        seen.add(name)
    return tuple(names)


def parse_fd_items(items: Iterable[str], label: str = "FD") -> tuple[FunctionalDependency, ...]:
    """Parse each string as one FD; errors name the offending item."""
    result = []
    for item in items:
        try:
            result.append(parse_fd(item))
        except FDParseError as exc:
            raise InputError(f"invalid {label} {item!r}: {exc}") from exc
    return tuple(result)


def parse_fd_lines(text: str) -> tuple[FunctionalDependency, ...]:
    """Parse one FD per line; blank lines and ``#`` comments are ignored.

    Raises:
        TypeError: if ``text`` is not a string.
        InputError: naming the 1-based line of the first invalid FD.
    """
    if not isinstance(text, str):
        raise TypeError(f"FD text must be a string, got {type(text).__name__}")
    result = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            result.append(parse_fd(line))
        except FDParseError as exc:
            raise InputError(f"line {number}: {exc}") from exc
    return tuple(result)
