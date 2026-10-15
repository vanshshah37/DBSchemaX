"""Explanation helpers: real, verifiable traces of the core algorithms.

Each helper replays the corresponding algorithm step by step so a UI can show
*why* a result holds, and then verifies its final answer against the existing
engine (:func:`attribute_closure`, :func:`find_candidate_keys`,
:func:`minimal_cover`, :func:`analyze_normalization`, :func:`synthesize_3nf`).
If a replay ever disagrees with the engine, ``RuntimeError`` is raised instead
of showing a fabricated explanation.

Pure set logic; no Streamlit, CLI or database code.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import TYPE_CHECKING, Iterable

from dbschemax.candidate_keys import find_candidate_keys
from dbschemax.closure import attribute_closure
from dbschemax.minimal_cover import _fd_order as _cover_order
from dbschemax.minimal_cover import minimal_cover
from dbschemax.models import FunctionalDependency, _to_attribute_set
from dbschemax.normalization import (
    _fd_order,
    _set_order,
    analyze_normalization,
    resolve_attributes,
)
from dbschemax.synthesis import synthesize_3nf

if TYPE_CHECKING:  # pragma: no cover
    from dbschemax.reporting import AnalysisReport

MAX_SEARCH_ATTRIBUTES = 12


def _checked(fds: Iterable[FunctionalDependency]) -> tuple[FunctionalDependency, ...]:
    items = tuple(fds)
    for fd in items:
        if not isinstance(fd, FunctionalDependency):
            raise TypeError(f"fds must contain FunctionalDependency, got {fd!r}")
    return items


def _mismatch(what: str) -> RuntimeError:
    return RuntimeError(f"explanation of {what} disagrees with the analysis engine")


# --- Attribute closure -----------------------------------------------------

@dataclass(frozen=True)
class ClosureApplication:
    """One FD applied during a pass, and the attributes it newly added."""

    fd: FunctionalDependency
    added: frozenset[str]


@dataclass(frozen=True)
class ClosureStep:
    """One productive pass over the FD list (1-based ``iteration``)."""

    iteration: int
    applied: tuple[ClosureApplication, ...]
    before: frozenset[str]
    after: frozenset[str]


@dataclass(frozen=True)
class ClosureTrace:
    """The real replay of closure computation for ``start``.

    After the last listed step one more pass adds nothing: that is the fixed
    point, so ``closure`` is X⁺.
    """

    start: frozenset[str]
    steps: tuple[ClosureStep, ...]
    closure: frozenset[str]


def closure_trace(
    attributes: Iterable[str], fds: Iterable[FunctionalDependency]
) -> ClosureTrace:
    """Replay :func:`attribute_closure` (same FD order, same in-pass updates).

    Raises:
        RuntimeError: if the replayed closure differs from ``attribute_closure``.
    """
    start = _to_attribute_set(attributes, "attributes")
    fd_list = _checked(fds)
    closure = set(start)
    steps: list[ClosureStep] = []
    changed = True
    while changed:
        changed = False
        before = frozenset(closure)
        applied: list[ClosureApplication] = []
        for fd in fd_list:
            if fd.lhs <= closure and not fd.rhs <= closure:
                added = frozenset(fd.rhs - closure)
                closure |= fd.rhs
                applied.append(ClosureApplication(fd, added))
                changed = True
        if applied:
            steps.append(ClosureStep(len(steps) + 1, tuple(applied), before, frozenset(closure)))
    result = frozenset(closure)
    if result != attribute_closure(start, fd_list):
        raise _mismatch("attribute closure")
    return ClosureTrace(start, tuple(steps), result)


@dataclass(frozen=True)
class SuperkeyCheck:
    """Whether X is a superkey of R: X⁺ = R."""

    subset: frozenset[str]
    closure: frozenset[str]
    relation: frozenset[str]
    is_superkey: bool
    missing: frozenset[str]


def superkey_check(
    subset: Iterable[str],
    relation: Iterable[str],
    fds: Iterable[FunctionalDependency],
) -> SuperkeyCheck:
    """Compute X⁺ and compare it with the relation."""
    x = _to_attribute_set(subset, "subset")
    r = resolve_attributes(relation)
    closure = attribute_closure(x, _checked(fds))
    return SuperkeyCheck(x, closure, r, r <= closure, r - closure)


# --- Candidate keys --------------------------------------------------------

@dataclass(frozen=True)
class RemovalCheck:
    """Closure of ``key - {removed}``; a key is minimal if none is a superkey."""

    removed: str
    remaining: frozenset[str]
    closure: frozenset[str]
    still_superkey: bool


@dataclass(frozen=True)
class KeyEvidence:
    """Evidence that ``key`` is a candidate key (superkey + minimal).

    Checking single-attribute removals suffices for minimality: superkeys are
    upward closed, so if any proper subset were a superkey, some subset
    missing exactly one attribute would be too.
    """

    key: frozenset[str]
    closure: frozenset[str]
    is_superkey: bool
    removals: tuple[RemovalCheck, ...]
    is_minimal: bool

    @property
    def is_candidate_key(self) -> bool:
        return self.is_superkey and self.is_minimal


def key_evidence(
    relation: Iterable[str],
    fds: Iterable[FunctionalDependency],
    key: Iterable[str],
) -> KeyEvidence:
    """Compute K⁺ and the effect of removing each single attribute of K."""
    r = resolve_attributes(relation)
    fd_list = _checked(fds)
    k = _to_attribute_set(key, "key")
    if not k <= r:
        raise ValueError("key contains attributes outside the relation")
    closure = attribute_closure(k, fd_list)
    removals = []
    for attr in sorted(k):
        remaining = k - {attr}
        rest_closure = attribute_closure(remaining, fd_list)
        removals.append(RemovalCheck(attr, remaining, rest_closure, r <= rest_closure))
    return KeyEvidence(
        key=k,
        closure=closure,
        is_superkey=r <= closure,
        removals=tuple(removals),
        is_minimal=not any(item.still_superkey for item in removals),
    )


@dataclass(frozen=True)
class SubsetRow:
    """One subset of R examined by exhaustive key search."""

    subset: frozenset[str]
    closure: frozenset[str]
    is_superkey: bool
    is_candidate_key: bool
    contains_key: frozenset[str] | None  # a candidate key inside a non-minimal superkey


def key_search_table(
    relation: Iterable[str],
    fds: Iterable[FunctionalDependency],
    max_attributes: int = MAX_SEARCH_ATTRIBUTES,
) -> tuple[SubsetRow, ...]:
    """List every subset of R (by size, then name) with its closure.

    The 2^n rows are cross-checked against :func:`find_candidate_keys`.

    Raises:
        ValueError: if R has more than ``max_attributes`` attributes.
        RuntimeError: if the minimal superkeys differ from the engine's keys.
    """
    r = resolve_attributes(relation)
    fd_list = _checked(fds)
    if len(r) > max_attributes:
        raise ValueError(
            f"{len(r)} attributes means {2 ** len(r)} subsets; "
            f"the explanation table is limited to {max_attributes} attributes"
        )
    keys = find_candidate_keys(r, fd_list)
    ordered = sorted(r)
    rows: list[SubsetRow] = []
    for size in range(len(ordered) + 1):
        for combo in combinations(ordered, size):
            subset = frozenset(combo)
            closure = attribute_closure(subset, fd_list)
            is_super = r <= closure
            inside = next((k for k in keys if k < subset), None) if is_super else None
            rows.append(SubsetRow(subset, closure, is_super, subset in keys, inside))
    minimal = {row.subset for row in rows if row.is_superkey and row.contains_key is None}
    if minimal != set(keys):
        raise _mismatch("candidate keys")
    return tuple(rows)


def subset_counts(rows: Iterable[SubsetRow]) -> tuple[tuple[int, int, int, int], ...]:
    """Per subset size: (size, subsets, superkeys, candidate keys)."""
    table: dict[int, list[int]] = {}
    for row in rows:
        entry = table.setdefault(len(row.subset), [0, 0, 0])
        entry[0] += 1
        entry[1] += row.is_superkey
        entry[2] += row.is_candidate_key
    return tuple((size, *counts) for size, counts in sorted(table.items()))


# --- Minimal cover ---------------------------------------------------------

@dataclass(frozen=True)
class SplitStep:
    """``original`` split into singleton-RHS ``parts`` (trivial attributes dropped)."""

    original: FunctionalDependency
    parts: tuple[FunctionalDependency, ...]
    dropped_trivial: frozenset[str]


@dataclass(frozen=True)
class ExtraneousStep:
    """``attribute`` was removed from ``before``'s LHS because the RHS attribute
    is in the closure of the smaller LHS (``evidence_closure``)."""

    before: FunctionalDependency
    attribute: str
    after: FunctionalDependency
    evidence_closure: frozenset[str]


@dataclass(frozen=True)
class RedundantStep:
    """``removed`` is implied by the remaining FDs: its LHS closure under them
    (``closure_without``) contains its RHS."""

    removed: FunctionalDependency
    closure_without: frozenset[str]


@dataclass(frozen=True)
class CoverTrace:
    """Real replay of the three minimal-cover steps."""

    original: tuple[FunctionalDependency, ...]
    split_steps: tuple[SplitStep, ...]
    after_split: tuple[FunctionalDependency, ...]
    duplicates_merged: int
    extraneous_steps: tuple[ExtraneousStep, ...]
    after_lhs_reduction: tuple[FunctionalDependency, ...]
    redundant_steps: tuple[RedundantStep, ...]
    cover: tuple[FunctionalDependency, ...]


def minimal_cover_trace(fds: Iterable[FunctionalDependency]) -> CoverTrace:
    """Replay :func:`minimal_cover` and record each real transformation.

    Raises:
        RuntimeError: if the replayed cover differs from ``minimal_cover``.
    """
    original = _checked(fds)
    distinct = sorted(set(original), key=_cover_order)  # same order as minimal_cover
    split_steps: list[SplitStep] = []
    generated = 0
    for fd in distinct:
        parts = tuple(
            FunctionalDependency(fd.lhs, frozenset({a})) for a in sorted(fd.rhs - fd.lhs)
        )
        generated += len(parts)
        split_steps.append(SplitStep(fd, parts, fd.rhs & fd.lhs))
    singles = sorted({p for s in split_steps for p in s.parts}, key=_cover_order)

    current = list(singles)
    extraneous: list[ExtraneousStep] = []
    for index, fd in enumerate(singles):
        (target,) = fd.rhs
        lhs = fd.lhs
        for attr in sorted(fd.lhs):
            smaller = lhs - {attr}
            evidence = attribute_closure(smaller, current)
            if target in evidence:
                extraneous.append(
                    ExtraneousStep(
                        FunctionalDependency(lhs, fd.rhs),
                        attr,
                        FunctionalDependency(smaller, fd.rhs),
                        evidence,
                    )
                )
                lhs = smaller
        current[index] = FunctionalDependency(lhs, fd.rhs)
    reduced = sorted(set(current), key=_cover_order)

    kept = list(reduced)
    redundant: list[RedundantStep] = []
    for fd in reduced:
        others = [o for o in kept if o != fd]
        evidence = attribute_closure(fd.lhs, others)
        if fd.rhs <= evidence:
            redundant.append(RedundantStep(fd, evidence))
            kept = others
    cover = tuple(kept)
    if cover != minimal_cover(original):
        raise _mismatch("minimal cover")
    return CoverTrace(
        original=original,
        split_steps=tuple(split_steps),
        after_split=tuple(singles),
        duplicates_merged=(len(original) - len(distinct)) + (generated - len(singles)),
        extraneous_steps=tuple(extraneous),
        after_lhs_reduction=tuple(reduced),
        redundant_steps=tuple(redundant),
        cover=cover,
    )


@dataclass(frozen=True)
class ImplicationEvidence:
    """``fd`` is implied by a set F iff its RHS ⊆ (its LHS)⁺ under F."""

    fd: FunctionalDependency
    lhs_closure: frozenset[str]
    implied: bool


@dataclass(frozen=True)
class EquivalenceCheck:
    """Mutual implication of two FD sets (F ≡ G)."""

    first_implies_second: tuple[ImplicationEvidence, ...]
    second_implies_first: tuple[ImplicationEvidence, ...]

    @property
    def equivalent(self) -> bool:
        return all(e.implied for e in self.first_implies_second) and all(
            e.implied for e in self.second_implies_first
        )


def _implications(
    source: tuple[FunctionalDependency, ...], targets: tuple[FunctionalDependency, ...]
) -> tuple[ImplicationEvidence, ...]:
    result = []
    for fd in targets:
        closure = attribute_closure(fd.lhs, source)
        result.append(ImplicationEvidence(fd, closure, fd.rhs <= closure))
    return tuple(result)


def equivalence_check(
    first: Iterable[FunctionalDependency], second: Iterable[FunctionalDependency]
) -> EquivalenceCheck:
    """Check F ⊨ G and G ⊨ F using attribute closure."""
    f, g = _checked(first), _checked(second)
    return EquivalenceCheck(_implications(f, g), _implications(g, f))


# --- Normalization predicates ----------------------------------------------

@dataclass(frozen=True)
class FDPredicateRow:
    """The 3NF/BCNF predicates evaluated for one non-trivial ``X -> A``."""

    fd: FunctionalDependency
    lhs_closure: frozenset[str]
    lhs_is_superkey: bool
    attribute_is_prime: bool

    @property
    def satisfies_bcnf(self) -> bool:
        return self.lhs_is_superkey

    @property
    def satisfies_3nf(self) -> bool:
        return self.lhs_is_superkey or self.attribute_is_prime


def fd_predicate_rows(
    relation: Iterable[str], fds: Iterable[FunctionalDependency]
) -> tuple[FDPredicateRow, ...]:
    """Evaluate "X is a superkey" / "A is prime" for every non-trivial ``X -> A``.

    Raises:
        RuntimeError: if the failing rows differ from the engine's violations.
    """
    r = resolve_attributes(relation)
    fd_list = _checked(fds)
    keys = find_candidate_keys(r, fd_list)
    prime = frozenset().union(*keys)
    singles = sorted(
        {FunctionalDependency(fd.lhs, frozenset({a})) for fd in fd_list for a in fd.rhs - fd.lhs},
        key=_fd_order,
    )
    rows = []
    for fd in singles:
        closure = attribute_closure(fd.lhs, fd_list)
        (attr,) = fd.rhs
        rows.append(FDPredicateRow(fd, closure, r <= closure, attr in prime))
    analysis = analyze_normalization(r, fd_list)
    if {x.fd for x in rows if not x.satisfies_bcnf} != set(analysis.violations_bcnf) or {
        x.fd for x in rows if not x.satisfies_3nf
    } != set(analysis.violations_3nf):
        raise _mismatch("normalization predicates")
    return tuple(rows)


# --- 3NF synthesis ---------------------------------------------------------

@dataclass(frozen=True)
class SchemaOrigin:
    """A schema built from the cover FDs sharing the determinant ``lhs``."""

    schema: frozenset[str]
    lhs: frozenset[str]
    fds: tuple[FunctionalDependency, ...]


@dataclass(frozen=True)
class PreservationEvidence:
    """The final schema that contains all attributes of a cover FD."""

    fd: FunctionalDependency
    schema: frozenset[str]


@dataclass(frozen=True)
class SynthesisTrace:
    """Real replay of the 3NF synthesis steps."""

    cover: tuple[FunctionalDependency, ...]
    groups: tuple[SchemaOrigin, ...]
    candidate_keys: tuple[frozenset[str], ...]
    key_schema_needed: bool
    key_schema: frozenset[str] | None
    subsumed: tuple[tuple[frozenset[str], frozenset[str]], ...]  # (removed, contained in)
    schemas: tuple[frozenset[str], ...]
    preservation: tuple[PreservationEvidence, ...]
    covers_all_attributes: bool
    keys_contained: tuple[frozenset[str], ...]


def synthesis_trace(
    relation: Iterable[str], fds: Iterable[FunctionalDependency]
) -> SynthesisTrace:
    """Replay :func:`synthesize_3nf`, recording what each step contributed.

    Raises:
        RuntimeError: if the replayed schemas differ from ``synthesize_3nf``.
    """
    r = resolve_attributes(relation)
    fd_list = _checked(fds)
    keys = find_candidate_keys(r, fd_list)
    cover = minimal_cover(fd_list)
    grouped: dict[frozenset[str], list[FunctionalDependency]] = {}
    for fd in cover:
        grouped.setdefault(fd.lhs, []).append(fd)
    groups = tuple(
        SchemaOrigin(lhs | frozenset().union(*(f.rhs for f in members)), lhs, tuple(members))
        for lhs, members in sorted(grouped.items(), key=lambda item: _set_order(item[0]))
    )
    pool = {g.schema for g in groups}
    key_needed = not any(k <= s for s in pool for k in keys)
    key_schema = keys[0] if key_needed else None
    if key_schema is not None:
        pool.add(key_schema)
    subsumed = tuple(
        (s, min((t for t in pool if s < t), key=_set_order))
        for s in sorted(pool, key=_set_order)
        if any(s < t for t in pool)
    )
    final = tuple(sorted((s for s in pool if not any(s < t for t in pool)), key=_set_order))
    if final != synthesize_3nf(r, fd_list):
        raise _mismatch("3NF synthesis")
    preservation = tuple(
        PreservationEvidence(fd, next(s for s in final if (fd.lhs | fd.rhs) <= s))
        for fd in cover
    )
    return SynthesisTrace(
        cover=cover,
        groups=groups,
        candidate_keys=keys,
        key_schema_needed=key_needed,
        key_schema=key_schema,
        subsumed=subsumed,
        schemas=final,
        preservation=preservation,
        covers_all_attributes=frozenset().union(*final) == r,
        keys_contained=tuple(k for k in keys if any(k <= s for s in final)),
    )


# --- Everything the dashboard needs, computed once --------------------------

@dataclass(frozen=True)
class ReportExplanation:
    """All explanation traces for one :class:`AnalysisReport`."""

    relation: frozenset[str]
    fds: tuple[FunctionalDependency, ...]
    key_closures: tuple[ClosureTrace, ...]
    key_evidence: tuple[KeyEvidence, ...]
    search_rows: tuple[SubsetRow, ...]
    cover_trace: CoverTrace
    cover_equivalence: EquivalenceCheck
    predicates: tuple[FDPredicateRow, ...]
    synthesis: SynthesisTrace


def explain_report(report: "AnalysisReport") -> ReportExplanation:
    """Build every trace from a report's relation and analysis FD set.

    The traces are derived from the same inputs the report was built from and
    each verifies itself against the engine (see the individual helpers).
    """
    relation = frozenset(report.attributes)
    fds = report.analysis_fds
    keys = report.normalization.candidate_keys
    trace = minimal_cover_trace(fds)
    if trace.cover != report.minimal_cover:
        raise _mismatch("the report's minimal cover")
    synthesis = synthesis_trace(relation, fds)
    if synthesis.schemas != report.synthesized_schemas:
        raise _mismatch("the report's synthesized schemas")
    return ReportExplanation(
        relation=relation,
        fds=fds,
        key_closures=tuple(closure_trace(k, fds) for k in keys),
        key_evidence=tuple(key_evidence(relation, fds, k) for k in keys),
        search_rows=key_search_table(relation, fds),
        cover_trace=trace,
        cover_equivalence=equivalence_check(fds, trace.cover),
        predicates=fd_predicate_rows(relation, fds),
        synthesis=synthesis,
    )
