"""Attribute closure X+ under a set of functional dependencies.

Pure set logic: no database or UI code.
"""

from __future__ import annotations

from typing import Iterable

from dbschemax.models import FunctionalDependency, _to_attribute_set


def attribute_closure(
    attributes: Iterable[str],
    fds: Iterable[FunctionalDependency],
) -> frozenset[str]:
    """Return X+, the closure of ``attributes`` (X) under ``fds`` (F).

    Starts with X and repeatedly adds ``fd.rhs`` for every FD whose ``fd.lhs``
    is a subset of the current closure, until a full pass adds nothing. The
    closure only grows and is bounded by the finite attributes mentioned in X
    and F, so the loop always terminates. The result does not depend on FD
    order. Empty-LHS FDs (``∅ -> A``) always fire. Inputs are never mutated.

    Attribute names are normalized like the models do (surrounding whitespace
    stripped, case-sensitive).

    Raises:
        TypeError: if ``attributes`` is a bare string or has non-string items,
            or if ``fds`` contains anything other than FunctionalDependency.
        ValueError: if an attribute name is empty.
    """
    closure = set(_to_attribute_set(attributes, "attributes"))
    fd_list = tuple(fds)  # materialize once so one-shot iterators work
    for fd in fd_list:
        if not isinstance(fd, FunctionalDependency):
            raise TypeError(f"fds must contain FunctionalDependency, got {fd!r}")

    changed = True
    while changed:
        changed = False
        for fd in fd_list:
            if fd.lhs <= closure and not fd.rhs <= closure:
                closure |= fd.rhs
                changed = True
    return frozenset(closure)
