"""3NF synthesis from a minimal cover.

Pure set logic built on :func:`minimal_cover` and :func:`find_candidate_keys`;
no database or UI code.
"""

from __future__ import annotations

from typing import Iterable

from dbschemax.candidate_keys import find_candidate_keys
from dbschemax.minimal_cover import minimal_cover
from dbschemax.models import FunctionalDependency, Relation
from dbschemax.normalization import resolve_attributes


def _schemas_from_cover(
    cover: Iterable[FunctionalDependency],
) -> set[frozenset[str]]:
    """Build one schema ``X ∪ A1 ∪ ... ∪ Ak`` per distinct LHS ``X`` of the cover.

    FDs sharing a left-hand side are combined, as in the textbook version of
    the algorithm (equivalent to taking ``X ∪ {A}`` for each FD and merging
    the schemas that share ``X``).
    """
    grouped: dict[frozenset[str], set[str]] = {}
    for fd in cover:
        grouped.setdefault(fd.lhs, set()).update(fd.rhs)
    return {lhs | frozenset(rhs) for lhs, rhs in grouped.items()}


def _drop_subsumed(schemas: set[frozenset[str]]) -> tuple[frozenset[str], ...]:
    """Remove schemas that are proper subsets of another; sort deterministically."""
    kept = [s for s in schemas if not any(s < other for other in schemas)]
    return tuple(sorted(kept, key=lambda s: (len(s), tuple(sorted(s)))))


def synthesize_3nf(
    relation: Relation | Iterable[str],
    fds: Iterable[FunctionalDependency],
) -> tuple[frozenset[str], ...]:
    """Synthesize 3NF relation schemas for ``relation`` under ``fds``.

    Algorithm: compute a minimal cover; create one schema per distinct LHS
    (``X`` plus all attributes it determines in the cover); if no schema
    contains a candidate key of the original relation, add the first candidate
    key (size, then alphabetical) as its own schema; drop schemas that are
    proper subsets of another. Output is sorted by size, then alphabetically.

    Theory (standard 3NF synthesis, e.g. Bernstein; Silberschatz et al.):
    every attribute of the relation appears in some schema; every FD of the
    cover is embedded in a single schema, so the decomposition is
    dependency-preserving; each schema is in 3NF with respect to the
    projection of F+. The textbook algorithm, including the candidate-key
    step, is also known to give a lossless-join decomposition, but this
    function does not check lossless join, and being in 3NF alone does not
    imply it. Do not treat the result as certified lossless by this module.

    Raises:
        TypeError: invalid attributes or non-FunctionalDependency items.
        ValueError: empty relation, empty names, or an FD mentioning
            attributes outside the relation.
    """
    attributes = resolve_attributes(relation)
    fd_list = tuple(fds)  # materialize once so one-shot iterators work
    keys = find_candidate_keys(attributes, fd_list)  # also validates the FDs
    schemas = _schemas_from_cover(minimal_cover(fd_list))
    if not any(key <= schema for schema in schemas for key in keys):
        schemas.add(keys[0])
    return _drop_subsumed(schemas)
