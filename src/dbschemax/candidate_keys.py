"""Candidate key detection by exhaustive subset search.

Pure set logic built on :func:`attribute_closure`; no database or UI code.
"""

from __future__ import annotations

from itertools import combinations
from typing import Iterable

from dbschemax.closure import attribute_closure
from dbschemax.models import FunctionalDependency, _to_attribute_set


def find_candidate_keys(
    attributes: Iterable[str],
    fds: Iterable[FunctionalDependency],
) -> tuple[frozenset[str], ...]:
    """Return all candidate keys (minimal superkeys) of relation R.

    ``attributes`` is R. A set K is a superkey when ``K+ = R``; it is a
    candidate key when no proper subset of K is also a superkey.

    Subsets of R are tried by increasing size (size 0 first, so ``∅`` is
    returned alone when ``∅+ = R``). A subset is skipped if it contains an
    already-found key, so only minimal superkeys are accepted. Every size up
    to ``|R|`` is searched, because a larger set may be a key that does not
    contain any smaller key.

    Result order is deterministic: by size, then lexicographically by sorted
    attribute names. Inputs are not mutated.

    Raises:
        TypeError: invalid ``attributes`` (bare string, non-string items) or
            ``fds`` items that are not FunctionalDependency.
        ValueError: empty attribute name, or an FD that mentions an attribute
            outside R (its meaning for R would be ambiguous, so it is rejected
            rather than guessed).
    """
    relation = _to_attribute_set(attributes, "attributes")
    fd_list = tuple(fds)  # materialize once so one-shot iterators work
    for fd in fd_list:
        if not isinstance(fd, FunctionalDependency):
            raise TypeError(f"fds must contain FunctionalDependency, got {fd!r}")
        if not fd.attributes <= relation:
            extra = ", ".join(sorted(fd.attributes - relation))
            raise ValueError(f"FD {fd} mentions attributes outside the relation: {extra}")

    ordered = sorted(relation)
    keys: list[frozenset[str]] = []
    for size in range(len(ordered) + 1):
        for combo in combinations(ordered, size):
            candidate = frozenset(combo)
            if any(key <= candidate for key in keys):
                continue  # contains a smaller key, so not minimal
            if attribute_closure(candidate, fd_list) == relation:
                keys.append(candidate)
    return tuple(keys)
