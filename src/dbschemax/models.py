"""Core data structures for DBSchemaX.

Pure data models only: no algorithms, I/O, database, or UI code.
Attribute collections are stored as ``frozenset[str]`` so instances are
immutable, hashable, and order-insensitive (matching set semantics).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

AttributeSet = frozenset[str]


def _to_attribute_set(attributes: Iterable[str], what: str) -> AttributeSet:
    """Validate attribute names and return them as an immutable set.

    Attribute names are case-sensitive and surrounding whitespace is stripped.
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
    """Render an attribute set deterministically."""
    return ", ".join(sorted(attributes))


@dataclass(frozen=True)
class Relation:
    """A relation schema R(A1, ..., An)."""

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

    The RHS must be non-empty. The LHS may be empty because dependencies such
    as ``∅ -> A`` are mathematically valid. Membership in a particular
    relation is intentionally not checked by this model.
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
        """Return all attributes mentioned by this FD."""
        return self.lhs | self.rhs

    def is_trivial(self) -> bool:
        """Return True when RHS is a subset of LHS."""
        return self.rhs <= self.lhs

    def __str__(self) -> str:
        lhs = _format_attributes(self.lhs) if self.lhs else "∅"
        return f"{lhs} -> {_format_attributes(self.rhs)}"
