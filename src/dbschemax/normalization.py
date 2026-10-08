"""FD-based normal form analysis (2NF, 3NF, BCNF) with optional 1NF input.

Functional dependencies say nothing about whether values are atomic, so 1NF is
NOT derived from FDs. It is reported as ``None`` (not assessable) unless the
caller supplies atomicity information. The 2NF/3NF/BCNF results are purely
FD-based and assume the relation already has atomic values.

Pure set logic built on :func:`attribute_closure` and
:func:`find_candidate_keys`; no database or UI code.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Iterable

from dbschemax.candidate_keys import find_candidate_keys
from dbschemax.closure import attribute_closure
from dbschemax.models import FunctionalDependency, Relation, _to_attribute_set


def _set_order(attributes: frozenset[str]) -> tuple[int, tuple[str, ...]]:
    """Deterministic sort key for an attribute set: size, then names."""
    return len(attributes), tuple(sorted(attributes))


def _fd_order(fd: FunctionalDependency) -> tuple:
    return _set_order(fd.lhs), _set_order(fd.rhs)


def resolve_attributes(relation: Relation | Iterable[str]) -> frozenset[str]:
    """Return the attribute set of a Relation or an iterable of names.

    Raises:
        TypeError / ValueError: invalid attribute names (see models).
        ValueError: if the relation has no attributes.
    """
    if isinstance(relation, Relation):
        return relation.attributes
    attributes = _to_attribute_set(relation, "attributes")
    if not attributes:
        raise ValueError("relation must have at least one attribute")
    return attributes


@dataclass(frozen=True)
class PartialDependency:
    """A non-prime ``attribute`` determined by a proper subset of ``key``.

    ``determinant`` is minimal: no smaller proper subset of ``key``
    determines ``attribute``. The empty set counts as a proper subset.
    """

    key: frozenset[str]
    determinant: frozenset[str]
    attribute: str


@dataclass(frozen=True)
class NormalizationAnalysis:
    """Result of :func:`analyze_normalization`.

    Attributes:
        attributes: The relation's attributes.
        candidate_keys: All candidate keys (size, then alphabetical order).
        prime_attributes: Attributes belonging to some candidate key.
        first_nf: ``True``/``False`` only if the caller supplied atomicity
            information; ``None`` means not assessable from FD information.
        partial_dependencies: 2NF violations (FD-based).
        violations_3nf: Single-attribute FDs ``X -> A`` (given FDs, ``A`` not in
            ``X``) with ``X`` not a superkey and ``A`` non-prime.
        violations_bcnf: Single-attribute FDs ``X -> A`` (given FDs, ``A`` not
            in ``X``) with ``X`` not a superkey.
    """

    attributes: frozenset[str]
    candidate_keys: tuple[frozenset[str], ...]
    prime_attributes: frozenset[str]
    first_nf: bool | None
    partial_dependencies: tuple[PartialDependency, ...]
    violations_3nf: tuple[FunctionalDependency, ...]
    violations_bcnf: tuple[FunctionalDependency, ...]

    @property
    def non_prime_attributes(self) -> frozenset[str]:
        """Attributes that belong to no candidate key."""
        return self.attributes - self.prime_attributes

    @property
    def satisfies_2nf(self) -> bool:
        """FD-based 2NF (assumes atomic values)."""
        return not self.partial_dependencies

    @property
    def satisfies_3nf(self) -> bool:
        """FD-based 3NF (assumes atomic values)."""
        return not self.violations_3nf

    @property
    def satisfies_bcnf(self) -> bool:
        """FD-based BCNF (assumes atomic values)."""
        return not self.violations_bcnf

    @property
    def highest_fd_normal_form(self) -> str:
        """Strongest of BCNF / 3NF / 2NF satisfied, judged from FDs only.

        Says nothing about 1NF; "below 2NF" means 2NF fails.
        """
        if self.satisfies_bcnf:
            return "BCNF"
        if self.satisfies_3nf:
            return "3NF"
        if self.satisfies_2nf:
            return "2NF"
        return "below 2NF"


def _first_nf_status(
    attributes: frozenset[str], non_atomic_attributes: Iterable[str] | None
) -> bool | None:
    """Map caller-supplied atomicity information to a 1NF status."""
    if non_atomic_attributes is None:
        return None
    flagged = _to_attribute_set(non_atomic_attributes, "non_atomic_attributes")
    outside = flagged - attributes
    if outside:
        raise ValueError(
            "non_atomic_attributes outside the relation: " + ", ".join(sorted(outside))
        )
    return not flagged


def _partial_dependencies(
    keys: tuple[frozenset[str], ...],
    non_prime: frozenset[str],
    fds: tuple[FunctionalDependency, ...],
) -> tuple[PartialDependency, ...]:
    """Find non-prime attributes determined by proper subsets of composite keys."""
    found: set[PartialDependency] = set()
    for key in keys:
        if len(key) < 2:
            continue
        ordered = sorted(key)
        determined: dict[frozenset[str], frozenset[str]] = {}
        for size in range(len(ordered)):  # proper subsets only, including ∅
            for combo in combinations(ordered, size):
                subset = frozenset(combo)
                determined[subset] = attribute_closure(subset, fds) & non_prime
        for subset, dependents in determined.items():
            for attr in dependents:
                if not any(
                    smaller < subset and attr in determined[smaller]
                    for smaller in determined
                ):
                    found.add(PartialDependency(key, subset, attr))
    return tuple(
        sorted(
            found,
            key=lambda p: (_set_order(p.key), _set_order(p.determinant), p.attribute),
        )
    )


def _fd_violations(
    attributes: frozenset[str],
    prime: frozenset[str],
    fds: tuple[FunctionalDependency, ...],
) -> tuple[tuple[FunctionalDependency, ...], tuple[FunctionalDependency, ...]]:
    """Return (3NF violations, BCNF violations) among the given FDs.

    Checking the given FDs (not all of F+) is sufficient for the relation
    itself. Trivial parts of an FD (attributes already in its LHS) are ignored.
    """
    superkey_cache: dict[frozenset[str], bool] = {}

    def is_superkey(lhs: frozenset[str]) -> bool:
        if lhs not in superkey_cache:
            superkey_cache[lhs] = attribute_closure(lhs, fds) == attributes
        return superkey_cache[lhs]

    bcnf: set[FunctionalDependency] = set()
    third: set[FunctionalDependency] = set()
    for fd in fds:
        if is_superkey(fd.lhs):
            continue
        for attr in fd.rhs - fd.lhs:
            single = FunctionalDependency(fd.lhs, frozenset({attr}))
            bcnf.add(single)
            if attr not in prime:
                third.add(single)
    return (
        tuple(sorted(third, key=_fd_order)),
        tuple(sorted(bcnf, key=_fd_order)),
    )


def analyze_normalization(
    relation: Relation | Iterable[str],
    fds: Iterable[FunctionalDependency],
    non_atomic_attributes: Iterable[str] | None = None,
) -> NormalizationAnalysis:
    """Analyze 2NF, 3NF and BCNF of a relation from its FDs.

    ``non_atomic_attributes`` is optional atomicity information, kept separate
    from the FD analysis: ``None`` (default) leaves 1NF as ``None``
    (not assessable); an empty iterable asserts every attribute is atomic
    (1NF ``True``); a non-empty one names non-atomic attributes (1NF
    ``False``). It never changes the FD-based results.

    Definitions used: 2NF fails if a non-prime attribute depends on a proper
    subset (``∅`` included) of a composite candidate key; 3NF fails for a
    non-trivial ``X -> A`` with ``X`` not a superkey and ``A`` non-prime; BCNF
    fails for a non-trivial ``X -> A`` with ``X`` not a superkey.

    Raises:
        TypeError: invalid attributes or non-FunctionalDependency items.
        ValueError: empty relation, empty names, an FD mentioning attributes
            outside the relation, or ``non_atomic_attributes`` outside it.
    """
    attributes = resolve_attributes(relation)
    fd_list = tuple(fds)  # materialize once so one-shot iterators work
    keys = find_candidate_keys(attributes, fd_list)  # also validates the FDs
    prime = frozenset().union(*keys)
    first_nf = _first_nf_status(attributes, non_atomic_attributes)
    violations_3nf, violations_bcnf = _fd_violations(attributes, prime, fd_list)
    return NormalizationAnalysis(
        attributes=attributes,
        candidate_keys=keys,
        prime_attributes=prime,
        first_nf=first_nf,
        partial_dependencies=_partial_dependencies(
            keys, attributes - prime, fd_list
        ),
        violations_3nf=violations_3nf,
        violations_bcnf=violations_bcnf,
    )
