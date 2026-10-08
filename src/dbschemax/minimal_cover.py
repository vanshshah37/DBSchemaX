"""Minimal (canonical) cover of a set of functional dependencies.

Pure set logic built on :func:`attribute_closure`; no database or UI code.
"""

from __future__ import annotations

from typing import Iterable

from dbschemax.closure import attribute_closure
from dbschemax.models import FunctionalDependency


def _fd_order(fd: FunctionalDependency) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Deterministic sort key for an FD."""
    return tuple(sorted(fd.lhs)), tuple(sorted(fd.rhs))


def _split_rhs(fds: Iterable[FunctionalDependency]) -> list[FunctionalDependency]:
    """Split ``X -> A,B`` into ``X -> A`` and ``X -> B``.

    Trivial parts (attributes already in X) and duplicates are dropped; the
    result is sorted so later steps do not depend on input order.
    """
    singles = {
        FunctionalDependency(fd.lhs, frozenset({attr}))
        for fd in fds
        for attr in fd.rhs - fd.lhs
    }
    return sorted(singles, key=_fd_order)


def _remove_extraneous_lhs(
    fds: list[FunctionalDependency],
) -> list[FunctionalDependency]:
    """Drop every LHS attribute ``b`` of ``X -> A`` with ``A`` in ``(X - b)+``.

    The test uses the current (equivalent) FD set, so each reduction keeps
    the set equivalent to the original. One pass per FD suffices: removing an
    attribute only shrinks ``X - c`` for the others, so an attribute that was
    needed stays needed.
    """
    current = list(fds)
    for index, fd in enumerate(fds):
        (target,) = fd.rhs
        lhs = fd.lhs
        for attr in sorted(fd.lhs):
            smaller = lhs - {attr}
            if target in attribute_closure(smaller, current):
                lhs = smaller
        current[index] = FunctionalDependency(lhs, fd.rhs)
    return sorted(set(current), key=_fd_order)


def _remove_redundant(fds: list[FunctionalDependency]) -> list[FunctionalDependency]:
    """Drop each FD implied by the others still kept.

    One pass suffices: an FD kept earlier is not implied by the others, and
    removing further FDs can only shrink that set.
    """
    kept = list(fds)
    for fd in fds:
        others = [other for other in kept if other != fd]
        if fd.rhs <= attribute_closure(fd.lhs, others):
            kept = others
    return kept


def minimal_cover(
    fds: Iterable[FunctionalDependency],
) -> tuple[FunctionalDependency, ...]:
    """Return a minimal (canonical) cover of ``fds``.

    Steps: (1) split right-hand sides to single attributes, (2) remove
    extraneous left-hand-side attributes, (3) remove redundant FDs. The result
    is equivalent to the input (same closure for every attribute set), every
    FD has one RHS attribute, none is trivial, none has an extraneous LHS
    attribute, and none is implied by the others. Empty LHS (``∅ -> A``) is
    supported.

    A minimal cover is NOT unique in general. This function returns one valid
    cover, chosen deterministically (the same set of FDs always gives the same
    tuple, in sorted order, regardless of input order or duplicates).

    Raises:
        TypeError: if ``fds`` contains anything other than FunctionalDependency.
    """
    fd_list = tuple(fds)  # materialize once so one-shot iterators work
    for fd in fd_list:
        if not isinstance(fd, FunctionalDependency):
            raise TypeError(f"fds must contain FunctionalDependency, got {fd!r}")
    split = _split_rhs(fd_list)
    reduced = _remove_extraneous_lhs(split)
    return tuple(_remove_redundant(reduced))
