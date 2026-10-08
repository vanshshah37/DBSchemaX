"""Core data structures for DBSchemaX.

Pure data models only: no algorithms, no I/O, no database or UI code.
Attribute collections are stored as ``frozenset[str]`` so that instances are
immutable, hashable, and order-insensitive (matching set semantics in
relational theory).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

AttributeSet = frozenset[str]


def _to_attribute_set(attributes: Iterable[str], what: str) -> AttributeSet:
    """Validate ``attributes`` and return them as an immutable set.

    Attribute names are case-sensitive; surrounding whitespace is stripped.

    Raises:
        TypeError: if ``attributes`` is a bare string (which would silently be
            split into characters) or any element is not a string.
        ValueError: if any attribute name is empty after stripping.
    """
    if isinstance(attributes, str):
        raise TypeError(
            f"{what} must be an iterable of attribute names, not a single string"
        )
    cleaned: set[str] = set()
    for attr in attributes:
        if not isinstance(attr, str):
            raise TypeError(f"{what} must contain only strings, got {attr!r}")
        name = attr.strip()
        if not name:
            raise ValueError(f"{what} must not contain empty attribute names")
        cleaned.add(name)
    return frozenset(cleaned)


def _format_attributes(attributes: AttributeSet) -> str:
    """Render an attribute set deterministically, e.g. ``A, B, C``."""
    return ", ".join(sorted(attributes))


@dataclass(frozen=True)
class Relation:
    """A relation schema R(A1, ..., An).

    Attributes:
        name: Relation name (non-empty after stripping whitespace).
        attributes: Non-empty immutable set of attribute names.
    """

    name: str
    attributes: AttributeSet

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError("Relation name must be a string")
        name = self.name.strip()
        if not name:
            raise ValueError("Relation name must not be empty")
        attrs = _to_attribute_set(self.attributes, "Relation attributes")
        if not attrs:
            raise ValueError("Relation must have at least one attribute")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "attributes", attrs)

    def __str__(self) -> str:
        return f"{self.name}({_format_attributes(self.attributes)})"


@dataclass(frozen=True)
class FunctionalDependency:
    """A functional dependency ``lhs -> rhs``.

    ``rhs`` must be non-empty; ``lhs`` may be empty (e.g. ``∅ -> A``). Whether the attributes
    belong to a particular relation is NOT checked here; callers can compare
    ``fd.attributes`` against ``Relation.attributes``.

    Attributes:
        lhs: Determinant attribute set.
        rhs: Dependent attribute set.
    """

    lhs: AttributeSet
    rhs: AttributeSet

    def __post_init__(self) -> None:
        lhs = _to_attribute_set(self.lhs, "FD left-hand side")
        rhs = _to_attribute_set(self.rhs, "FD right-hand side")
        if not rhs:
            raise ValueError("FD right-hand side must not be empty")
        object.__setattr__(self, "lhs", lhs)
        object.__setattr__(self, "rhs", rhs)

    @property
    def attributes(self) -> AttributeSet:
        """All attributes mentioned by this FD (``lhs`` union ``rhs``)."""
        return self.lhs | self.rhs

    def is_trivial(self) -> bool:
        """Return True if ``rhs`` is a subset of ``lhs``."""
        return self.rhs <= self.lhs

    def __str__(self) -> str:
        lhs = _format_attributes(self.lhs) if self.lhs else "∅"
        return f"{lhs} -> {_format_attributes(self.rhs)}"
