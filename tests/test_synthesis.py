"""Tests for 3NF synthesis."""

import itertools
import random
from itertools import combinations

import pytest

from dbschemax.closure import attribute_closure
from dbschemax.candidate_keys import find_candidate_keys
from dbschemax.models import FunctionalDependency, Relation
from dbschemax.normalization import analyze_normalization
from dbschemax.parser import parse_fd
from dbschemax.synthesis import synthesize_3nf


def fds_of(*texts: str) -> list[FunctionalDependency]:
    return [parse_fd(t) for t in texts]


def synth(attrs: str, *fd_texts: str) -> tuple[frozenset[str], ...]:
    return synthesize_3nf(list(attrs), fds_of(*fd_texts))


def schemas(*groups: str) -> tuple[frozenset[str], ...]:
    return tuple(frozenset(g) for g in groups)


# --- Mathematical helpers --------------------------------------------------

def project(
    schema: frozenset[str], fds: list[FunctionalDependency]
) -> list[FunctionalDependency]:
    """Projection of F+ onto ``schema``: X -> (X+ ∩ schema) - X for all X ⊆ schema."""
    projected = []
    ordered = sorted(schema)
    for size in range(len(ordered) + 1):
        for combo in combinations(ordered, size):
            lhs = frozenset(combo)
            rhs = (attribute_closure(lhs, fds) & schema) - lhs
            if rhs:
                projected.append(FunctionalDependency(lhs, rhs))
    return projected


def preserves_dependencies(
    result: tuple[frozenset[str], ...], fds: list[FunctionalDependency]
) -> bool:
    """True if the union of the projections implies every FD of F."""
    union = [fd for s in result for fd in project(s, fds)]
    return all(fd.rhs <= attribute_closure(fd.lhs, union) for fd in fds)


def check_synthesis_properties(
    attrs: frozenset[str], fds: list[FunctionalDependency]
) -> tuple[frozenset[str], ...]:
    result = synthesize_3nf(attrs, fds)
    assert frozenset().union(*result) == attrs  # every attribute appears
    keys = find_candidate_keys(attrs, fds)
    assert any(k <= s for s in result for k in keys)  # a schema contains a key
    assert not any(a < b for a in result for b in result)  # no subsumed schemas
    assert len(set(result)) == len(result)
    assert preserves_dependencies(result, fds)  # dependency preservation
    for schema in result:  # each schema is in 3NF wrt projected F+
        analysis = analyze_normalization(schema, project(schema, fds))
        assert analysis.satisfies_3nf, (schema, fds)
    return result


# --- Simple cases ----------------------------------------------------------

def test_simple_chain() -> None:
    result = synth("ABCD", "A -> B", "B -> C", "C -> D")
    assert result == schemas("AB", "BC", "CD")
    check_synthesis_properties(frozenset("ABCD"), fds_of("A -> B", "B -> C", "C -> D"))


def test_composite_determinant() -> None:
    result = synth("ABCD", "A,B -> C", "C -> D")
    assert result == schemas("CD", "ABC")  # sorted by size, then names


def test_multiple_dependencies_same_lhs_combined() -> None:
    assert synth("ABC", "A -> B", "A -> C") == schemas("ABC")
    assert synth("ABC", "A -> B,C") == schemas("ABC")


def test_3nf_not_bcnf_example_stays_together() -> None:
    # AB -> C, C -> B: schema BC is subsumed by ABC.
    result = synth("ABC", "A,B -> C", "C -> B")
    assert result == schemas("ABC")


# --- Redundant / subsumed --------------------------------------------------

def test_redundant_fd_does_not_create_schema() -> None:
    assert synth("ABC", "A -> B", "B -> C", "A -> C") == schemas("AB", "BC")


def test_subsumed_schemas_removed() -> None:
    result = synth("ABC", "A -> B", "B -> A", "A,B -> C")
    assert result == schemas("ABC")
    assert not any(a < b for a in result for b in result)


def test_trivial_and_duplicate_fds_ignored() -> None:
    assert synth("ABC", "A -> A", "A -> B", "A -> B", "B -> C") == schemas("AB", "BC")


# --- Candidate-key step ----------------------------------------------------

def test_key_schema_added_when_no_schema_contains_key() -> None:
    fds = fds_of("A -> B", "C -> D")
    assert find_candidate_keys(list("ABCD"), fds) == schemas("AC")
    assert synthesize_3nf(list("ABCD"), fds) == schemas("AB", "AC", "CD")


def test_key_schema_not_added_when_already_contained() -> None:
    result = synth("ABCD", "A,B -> C", "C -> D")
    assert result == schemas("CD", "ABC")
    assert frozenset("AB") <= frozenset("ABC")  # key AB already inside ABC
    assert frozenset("AB") not in result  # so no separate key schema was added


def test_no_fds_gives_whole_relation() -> None:
    assert synth("ABC") == schemas("ABC")


def test_attribute_in_no_fd_lands_in_key_schema() -> None:
    result = synth("ABC", "A -> B")
    assert result == schemas("AB", "AC")
    assert frozenset().union(*result) == frozenset("ABC")


def test_multiple_candidate_keys_one_is_enough() -> None:
    fds = fds_of("A -> B", "B -> A", "A -> C")
    result = synthesize_3nf(list("ABC"), fds)
    assert result == schemas("ABC")
    assert len(find_candidate_keys(list("ABC"), fds)) == 2


# --- Empty LHS -------------------------------------------------------------

def test_empty_lhs_with_extra_attribute() -> None:
    # Cover is ∅ -> A, ∅ -> B (A is extraneous in A -> B): schema AB; key C is added.
    result = synth("ABC", "∅ -> A", "A -> B")
    assert result == schemas("C", "AB")
    check_synthesis_properties(frozenset("ABC"), fds_of("∅ -> A", "A -> B"))


def test_empty_set_determines_everything() -> None:
    assert synth("AB", "∅ -> A", "∅ -> B") == schemas("AB")


# --- Dependency preservation ----------------------------------------------

def test_dependency_preservation_examples() -> None:
    cases = [
        ("ABCD", ["A -> B", "B -> C", "C -> D"]),
        ("ABC", ["A,B -> C", "C -> B"]),
        ("ABCD", ["A -> B", "C -> D"]),
        ("ABCDE", ["A,B -> C", "C -> D,E", "D -> A"]),
    ]
    for attrs, texts in cases:
        check_synthesis_properties(frozenset(attrs), fds_of(*texts))


def test_cyclic_dependencies() -> None:
    fds = fds_of("A -> B", "B -> C", "C -> A")
    result = check_synthesis_properties(frozenset("ABC"), fds)
    assert result == schemas("AB", "AC", "BC")


def test_random_synthesis_properties() -> None:
    rng = random.Random(7)
    attrs = frozenset("ABCDE")
    for _ in range(150):
        fds = []
        for _ in range(rng.randint(0, 6)):
            lhs = frozenset(rng.sample("ABCDE", rng.choice([0, 1, 1, 2, 2, 3])))
            rhs = frozenset(rng.sample("ABCDE", rng.randint(1, 2)))
            fds.append(FunctionalDependency(lhs, rhs))
        check_synthesis_properties(attrs, fds)


# --- Determinism, inputs ---------------------------------------------------

def test_deterministic_regardless_of_order() -> None:
    fds = fds_of("A,B -> C", "C -> D", "D -> A", "B -> E")
    results = {
        synthesize_3nf(list("EDCBA"), list(p)) for p in itertools.permutations(fds)
    }
    assert len(results) == 1


def test_output_sorted_tuple_of_frozensets() -> None:
    result = synth("ABCD", "A -> B", "C -> D")
    assert isinstance(result, tuple)
    assert all(isinstance(s, frozenset) for s in result)
    assert list(result) == sorted(result, key=lambda s: (len(s), tuple(sorted(s))))


def test_inputs_not_mutated() -> None:
    attrs = ["A", "B", "C", "D"]
    fds = fds_of("A -> B", "C -> D")
    before = list(fds)
    synthesize_3nf(attrs, fds)
    assert attrs == ["A", "B", "C", "D"]
    assert fds == before


def test_generator_inputs() -> None:
    fds = fds_of("A -> B", "C -> D")
    expected = synthesize_3nf(list("ABCD"), fds)
    assert synthesize_3nf((a for a in "ABCD"), (f for f in fds)) == expected
    assert synthesize_3nf(iter("ABCD"), iter(fds)) == expected


def test_relation_object_input() -> None:
    relation = Relation("R", {"A", "B", "C"})
    assert synthesize_3nf(relation, fds_of("A -> B", "B -> C")) == schemas("AB", "BC")


# --- Invalid inputs --------------------------------------------------------

def test_bare_string_rejected() -> None:
    with pytest.raises(TypeError):
        synthesize_3nf("ABC", [])


def test_empty_relation_rejected() -> None:
    with pytest.raises(ValueError):
        synthesize_3nf([], [])


def test_fd_outside_relation_rejected() -> None:
    with pytest.raises(ValueError, match="outside the relation"):
        synth("AB", "A -> Z")


def test_non_fd_rejected() -> None:
    with pytest.raises(TypeError):
        synthesize_3nf(["A"], ["A -> A"])  # type: ignore[list-item]
