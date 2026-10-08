"""Tests for FD-based normalization analysis."""

import itertools
import random

import pytest

from dbschemax.closure import attribute_closure
from dbschemax.models import FunctionalDependency, Relation
from dbschemax.normalization import (
    NormalizationAnalysis,
    PartialDependency,
    analyze_normalization,
)
from dbschemax.parser import parse_fd


def fds_of(*texts: str) -> list[FunctionalDependency]:
    return [parse_fd(t) for t in texts]


def analyze(attrs: str, *fd_texts: str, **kwargs) -> NormalizationAnalysis:
    return analyze_normalization(list(attrs), fds_of(*fd_texts), **kwargs)


def fd_texts(fds: tuple[FunctionalDependency, ...]) -> list[str]:
    return [str(fd) for fd in fds]


def keys(*groups: str) -> tuple[frozenset[str], ...]:
    return tuple(frozenset(g) for g in groups)


# --- BCNF ------------------------------------------------------------------

def test_relation_in_bcnf() -> None:
    result = analyze("ABC", "A -> B,C")
    assert result.candidate_keys == keys("A")
    assert result.satisfies_2nf and result.satisfies_3nf and result.satisfies_bcnf
    assert result.partial_dependencies == ()
    assert result.violations_3nf == () and result.violations_bcnf == ()
    assert result.highest_fd_normal_form == "BCNF"


def test_no_fds_is_bcnf() -> None:
    result = analyze("ABC")
    assert result.candidate_keys == keys("ABC")
    assert result.satisfies_bcnf
    assert result.prime_attributes == frozenset("ABC")


# --- 3NF but not BCNF ------------------------------------------------------

def test_3nf_but_not_bcnf() -> None:
    result = analyze("ABC", "A,B -> C", "C -> B")
    assert result.candidate_keys == keys("AB", "AC")
    assert result.prime_attributes == frozenset("ABC")
    assert result.satisfies_2nf and result.satisfies_3nf
    assert not result.satisfies_bcnf
    assert fd_texts(result.violations_bcnf) == ["C -> B"]
    assert result.violations_3nf == ()
    assert result.highest_fd_normal_form == "3NF"


# --- Violates 3NF (transitive) ---------------------------------------------

def test_violates_3nf_transitive_dependency() -> None:
    result = analyze("ABC", "A -> B", "B -> C")
    assert result.candidate_keys == keys("A")
    assert result.non_prime_attributes == frozenset("BC")
    assert result.satisfies_2nf
    assert not result.satisfies_3nf and not result.satisfies_bcnf
    assert fd_texts(result.violations_3nf) == ["B -> C"]
    assert fd_texts(result.violations_bcnf) == ["B -> C"]
    assert result.highest_fd_normal_form == "2NF"


# --- Violates 2NF ----------------------------------------------------------

def test_violates_2nf_partial_dependency() -> None:
    result = analyze("ABCD", "A,B -> C", "A -> D")
    assert result.candidate_keys == keys("AB")
    assert result.partial_dependencies == (
        PartialDependency(frozenset("AB"), frozenset("A"), "D"),
    )
    assert not result.satisfies_2nf
    assert not result.satisfies_3nf and not result.satisfies_bcnf
    assert result.highest_fd_normal_form == "below 2NF"


def test_2nf_with_multiple_candidate_keys() -> None:
    # Keys AB and AC; D depends on B (part of AB) and on C (part of AC).
    result = analyze("ABCD", "A,B -> C", "C -> B", "B -> D")
    assert result.candidate_keys == keys("AB", "AC")
    assert result.prime_attributes == frozenset("ABC")
    assert result.partial_dependencies == (
        PartialDependency(frozenset("AB"), frozenset("B"), "D"),
        PartialDependency(frozenset("AC"), frozenset("C"), "D"),
    )
    assert not result.satisfies_2nf


def test_partial_dependency_reports_only_minimal_determinant() -> None:
    # D depends on A alone, so A (not AB-subsets like A,B) is the determinant;
    # key ABC has several proper subsets containing A.
    result = analyze("ABCD", "A,B,C -> D", "A -> D")
    assert result.candidate_keys == keys("ABC")
    assert result.partial_dependencies == (
        PartialDependency(frozenset("ABC"), frozenset("A"), "D"),
    )


def test_composite_key_without_partial_dependency_is_2nf() -> None:
    result = analyze("ABC", "A,B -> C")
    assert result.candidate_keys == keys("AB")
    assert result.satisfies_2nf and result.satisfies_bcnf


def test_non_prime_determined_by_empty_set_violates_2nf_for_composite_key() -> None:
    result = analyze("ABC", "A,B -> C", "∅ -> C")
    assert result.candidate_keys == keys("AB")
    assert result.partial_dependencies == (
        PartialDependency(frozenset("AB"), frozenset(), "C"),
    )


# --- Multiple keys / all prime ---------------------------------------------

def test_multiple_candidate_keys_cycle_all_prime() -> None:
    result = analyze("ABC", "A -> B", "B -> C", "C -> A")
    assert result.candidate_keys == keys("A", "B", "C")
    assert result.prime_attributes == frozenset("ABC")
    assert result.non_prime_attributes == frozenset()
    assert result.satisfies_bcnf


def test_all_prime_attributes_always_3nf() -> None:
    result = analyze("ABC", "A,B -> C", "C -> A", "C -> B")
    assert result.prime_attributes == result.attributes
    assert result.satisfies_3nf


def test_prime_attributes_union_of_all_keys() -> None:
    result = analyze("ABCD", "A -> B", "B -> A", "A,C -> D")
    assert result.candidate_keys == keys("AC", "BC")
    assert result.prime_attributes == frozenset("ABC")


# --- 1NF handling ----------------------------------------------------------

def test_1nf_not_assessable_from_fds() -> None:
    result = analyze("ABC", "A -> B,C")
    assert result.first_nf is None


def test_1nf_from_caller_information() -> None:
    assert analyze("ABC", "A -> B", non_atomic_attributes=[]).first_nf is True
    assert analyze("ABC", "A -> B", non_atomic_attributes=["B"]).first_nf is False
    assert analyze("ABC", "A -> B", non_atomic_attributes=iter(["B"])).first_nf is False


def test_atomicity_info_does_not_change_fd_results() -> None:
    plain = analyze("ABC", "A -> B", "B -> C")
    flagged = analyze("ABC", "A -> B", "B -> C", non_atomic_attributes=["C"])
    assert flagged.first_nf is False and plain.first_nf is None
    assert flagged.violations_3nf == plain.violations_3nf
    assert flagged.partial_dependencies == plain.partial_dependencies
    assert flagged.candidate_keys == plain.candidate_keys


def test_non_atomic_attribute_outside_relation_rejected() -> None:
    with pytest.raises(ValueError, match="outside the relation"):
        analyze("ABC", "A -> B", non_atomic_attributes=["Z"])


# --- Empty LHS -------------------------------------------------------------

def test_empty_lhs_not_superkey_violates_bcnf_and_3nf() -> None:
    result = analyze("AB", "∅ -> A")
    assert result.candidate_keys == keys("B")
    assert fd_texts(result.violations_bcnf) == ["∅ -> A"]
    assert fd_texts(result.violations_3nf) == ["∅ -> A"]
    assert result.satisfies_2nf


def test_empty_set_as_superkey_gives_bcnf() -> None:
    result = analyze("AB", "∅ -> A", "∅ -> B")
    assert result.candidate_keys == (frozenset(),)
    assert result.prime_attributes == frozenset()
    assert result.satisfies_bcnf


# --- Trivial / duplicate FDs and multi-attribute RHS -----------------------

def test_trivial_fds_ignored() -> None:
    result = analyze("ABC", "A -> A", "A,B -> A", "A,B,C -> B")
    assert result.violations_3nf == () and result.violations_bcnf == ()
    assert result.satisfies_bcnf


def test_duplicate_fds_do_not_duplicate_violations() -> None:
    result = analyze("ABC", "A -> B", "B -> C", "B -> C", "B -> C")
    assert fd_texts(result.violations_bcnf) == ["B -> C"]


def test_multi_attribute_rhs_split_in_violations() -> None:
    result = analyze("ABCD", "A -> B", "B -> C,D")
    assert fd_texts(result.violations_bcnf) == ["B -> C", "B -> D"]


def test_composite_determinant_violation() -> None:
    result = analyze("ABCD", "A -> B", "B,C -> D")
    assert result.candidate_keys == keys("AC")
    assert fd_texts(result.violations_bcnf) == ["A -> B", "B, C -> D"]


def test_prime_rhs_excused_in_3nf_only() -> None:
    result = analyze("ABCD", "A,B -> C,D", "D -> A")
    assert result.candidate_keys == keys("AB", "BD")
    assert fd_texts(result.violations_bcnf) == ["D -> A"]
    assert result.violations_3nf == ()  # A is prime


# --- Mathematical oracle ---------------------------------------------------

def random_fds(rng: random.Random, attrs: str) -> list[FunctionalDependency]:
    result = []
    for _ in range(rng.randint(0, 6)):
        lhs = frozenset(rng.sample(attrs, rng.choice([0, 1, 1, 2, 2, 3])))
        rhs = frozenset(rng.sample(attrs, rng.randint(1, 2)))
        result.append(FunctionalDependency(lhs, rhs))
    return result


def oracle(attrs: frozenset[str], fds: list[FunctionalDependency]):
    """Brute force over all non-trivial FDs X -> A in F+ (X any subset)."""
    ordered = sorted(attrs)
    subsets = [
        frozenset(c) for n in range(len(ordered) + 1)
        for c in itertools.combinations(ordered, n)
    ]
    closures = {s: attribute_closure(s, fds) for s in subsets}
    superkeys = [s for s in subsets if closures[s] == attrs]
    keys_ = [s for s in superkeys if not any(o < s for o in superkeys)]
    prime = frozenset().union(*keys_)
    bcnf = all(closures[x] == attrs for x in subsets if closures[x] - x)
    third = all(
        closures[x] == attrs or (closures[x] - x) <= prime for x in subsets
    )
    second = not any(
        (closures[s] - s) - prime
        for key in keys_ if len(key) > 1
        for s in subsets if s < key
    )
    return bcnf, third, second, keys_, prime


def test_matches_brute_force_oracle_and_hierarchy() -> None:
    rng = random.Random(2024)
    attrs = frozenset("ABCDE")
    for _ in range(300):
        fds = random_fds(rng, "ABCDE")
        result = analyze_normalization(attrs, fds)
        bcnf, third, second, keys_, prime = oracle(attrs, fds)
        assert result.satisfies_bcnf == bcnf, fds
        assert result.satisfies_3nf == third, fds
        assert result.satisfies_2nf == second, fds
        assert set(result.candidate_keys) == set(keys_)
        assert result.prime_attributes == prime
        if result.satisfies_bcnf:
            assert result.satisfies_3nf
        if result.satisfies_3nf:
            assert result.satisfies_2nf


# --- Inputs and determinism ------------------------------------------------

def test_relation_object_input() -> None:
    relation = Relation("R", {"A", "B", "C"})
    result = analyze_normalization(relation, fds_of("A -> B", "B -> C"))
    assert result.attributes == frozenset("ABC")
    assert result.candidate_keys == keys("A")


def test_generator_inputs() -> None:
    fds = fds_of("A -> B", "B -> C")
    expected = analyze_normalization(list("ABC"), fds)
    assert analyze_normalization((a for a in "ABC"), (f for f in fds)) == expected
    assert analyze_normalization(iter("ABC"), iter(fds)) == expected


def test_deterministic_regardless_of_order() -> None:
    fds = fds_of("A,B -> C", "C -> B", "B -> D", "A -> E")
    results = {
        analyze_normalization(list("EDCBA"), list(p))
        for p in itertools.permutations(fds)
    }
    assert len(results) == 1


def test_inputs_not_mutated() -> None:
    attrs = ["A", "B", "C"]
    fds = fds_of("A -> B", "B -> C")
    before = list(fds)
    analyze_normalization(attrs, fds, non_atomic_attributes=["C"])
    assert attrs == ["A", "B", "C"]
    assert fds == before


def test_result_is_immutable() -> None:
    result = analyze("ABC", "A -> B")
    with pytest.raises(Exception):
        result.first_nf = True  # type: ignore[misc]


# --- Invalid inputs --------------------------------------------------------

def test_bare_string_rejected() -> None:
    with pytest.raises(TypeError):
        analyze_normalization("ABC", [])


def test_empty_relation_rejected() -> None:
    with pytest.raises(ValueError):
        analyze_normalization([], [])


def test_fd_outside_relation_rejected() -> None:
    with pytest.raises(ValueError, match="outside the relation"):
        analyze("AB", "A -> Z")


def test_non_fd_rejected() -> None:
    with pytest.raises(TypeError):
        analyze_normalization(["A"], ["A -> A"])  # type: ignore[list-item]
