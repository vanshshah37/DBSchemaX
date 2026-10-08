"""Tests for candidate key detection."""

import itertools

import pytest

from dbschemax.candidate_keys import find_candidate_keys
from dbschemax.closure import attribute_closure
from dbschemax.models import FunctionalDependency
from dbschemax.parser import parse_fd


def fds_of(*texts: str) -> list[FunctionalDependency]:
    return [parse_fd(t) for t in texts]


def keys(*groups: str) -> tuple[frozenset[str], ...]:
    return tuple(frozenset(g) for g in groups)


def test_single_key() -> None:
    assert find_candidate_keys(["A", "B", "C"], fds_of("A -> B", "B -> C")) == keys("A")


def test_composite_key() -> None:
    assert find_candidate_keys(["A", "B", "C"], fds_of("A,B -> C")) == keys("AB")


def test_multiple_candidate_keys() -> None:
    # A -> B puts {A,B} in the closure of A, so AB -> C fires: A+ = ABC. Same for B.
    fds = fds_of("A -> B", "B -> A", "A,B -> C")
    assert find_candidate_keys(["A", "B", "C"], fds) == keys("A", "B")
    assert attribute_closure({"A"}, fds) == frozenset("ABC")


def test_multiple_single_attribute_keys() -> None:
    fds = fds_of("A -> B", "B -> A", "A -> C")
    assert find_candidate_keys(["A", "B", "C"], fds) == keys("A", "B")


def test_no_dependencies() -> None:
    assert find_candidate_keys(["A", "B", "C"], []) == keys("ABC")


def test_trivial_dependencies_unaffected() -> None:
    assert find_candidate_keys(["A", "B", "C"], fds_of("A -> A")) == keys("ABC")
    assert find_candidate_keys(["A", "B", "C"], fds_of("A,B -> A")) == keys("ABC")


def test_empty_lhs_dependency() -> None:
    fds = fds_of("∅ -> A", "A -> B")
    assert find_candidate_keys(["A", "B", "C"], fds) == keys("C")


def test_empty_set_is_the_unique_key() -> None:
    result = find_candidate_keys(["A", "B"], fds_of("∅ -> A", "∅ -> B"))
    assert result == (frozenset(),)


def test_empty_relation_has_empty_key() -> None:
    assert find_candidate_keys([], []) == (frozenset(),)


def test_no_single_attribute_determines_relation() -> None:
    fds = fds_of("A,B -> C", "C -> D")
    result = find_candidate_keys(["A", "B", "C", "D"], fds)
    assert result == keys("AB")
    assert all(attribute_closure({a}, fds) != frozenset("ABCD") for a in "ABCD")


def test_minimality_supersets_not_returned() -> None:
    fds = fds_of("A -> B,C")
    result = find_candidate_keys(["A", "B", "C"], fds)
    assert result == keys("A")  # AB, AC, ABC are superkeys but not minimal


def test_larger_key_not_containing_smaller_key_is_found() -> None:
    # {A} and {B,C} are both keys; {B,C} contains no smaller key.
    fds = fds_of("A -> B,C", "B,C -> A")
    assert find_candidate_keys(["A", "B", "C"], fds) == keys("A", "BC")


def test_several_candidate_keys_cycle() -> None:
    fds = fds_of("A -> B", "B -> C", "C -> D", "D -> A")
    assert find_candidate_keys(list("ABCD"), fds) == keys("A", "B", "C", "D")


def test_classic_overlapping_keys() -> None:
    # R(A,B,C), AB -> C, C -> A: keys AB and BC
    fds = fds_of("A,B -> C", "C -> A")
    assert find_candidate_keys(["A", "B", "C"], fds) == keys("AB", "BC")


def test_deterministic_ordering() -> None:
    fds = fds_of("A -> B", "B -> A", "A,B -> C")
    result = find_candidate_keys(["C", "B", "A"], fds)
    assert result == find_candidate_keys(["A", "B", "C"], list(reversed(fds)))
    assert [sorted(k) for k in result] == [["A"], ["B"]]
    # by size first, then lexicographic
    mixed = find_candidate_keys(list("ABC"), fds_of("A -> B,C", "B,C -> A"))
    assert [sorted(k) for k in mixed] == [["A"], ["B", "C"]]


def test_order_independent_over_all_permutations() -> None:
    fds = fds_of("A,B -> C", "C -> A", "C -> D", "D -> E")
    results = {
        find_candidate_keys(list("EDCBA"), list(p))
        for p in itertools.permutations(fds)
    }
    assert len(results) == 1


def test_no_duplicate_keys() -> None:
    fds = fds_of("A -> B", "B -> A")
    result = find_candidate_keys(["A", "B", "A", "B"], fds + fds)
    assert len(result) == len(set(result))


def test_inputs_not_mutated() -> None:
    attrs = ["A", "B", "C"]
    attr_set = {"A", "B", "C"}
    fds = fds_of("A -> B", "B -> C")
    fds_before = list(fds)
    find_candidate_keys(attrs, fds)
    find_candidate_keys(attr_set, fds)
    assert attrs == ["A", "B", "C"]
    assert attr_set == {"A", "B", "C"}
    assert fds == fds_before


def test_generator_and_iterator_inputs() -> None:
    fds = fds_of("A -> B", "B -> C")
    expected = keys("A")
    assert find_candidate_keys((a for a in "ABC"), (f for f in fds)) == expected
    assert find_candidate_keys(iter(["A", "B", "C"]), iter(fds)) == expected
    assert find_candidate_keys(frozenset("ABC"), frozenset(fds)) == expected


def test_returns_tuple_of_frozensets() -> None:
    result = find_candidate_keys(["A"], [])
    assert isinstance(result, tuple)
    assert all(isinstance(k, frozenset) for k in result)


def _brute_force_keys(
    relation: frozenset[str], fds: list[FunctionalDependency]
) -> set[frozenset[str]]:
    """Independent oracle: all subsets, filter superkeys, then keep minimal ones."""
    subsets = [
        frozenset(c)
        for n in range(len(relation) + 1)
        for c in itertools.combinations(sorted(relation), n)
    ]
    superkeys = [s for s in subsets if attribute_closure(s, fds) == relation]
    return {s for s in superkeys if not any(o < s for o in superkeys)}


@pytest.mark.parametrize(
    "fd_texts",
    [
        [],
        ["A -> B"],
        ["A -> B", "B -> C", "C -> D", "D -> E"],
        ["A,B -> C", "C -> A", "D -> E", "E -> D"],
        ["∅ -> A", "B,C -> D", "D -> B"],
        ["A,B -> C,D,E", "E -> A", "C,D -> B"],
        ["A -> A", "B -> B,C", "C,D -> E", "E -> C"],
    ],
)
def test_matches_brute_force_oracle(fd_texts: list[str]) -> None:
    relation = frozenset("ABCDE")
    fds = fds_of(*fd_texts)
    result = find_candidate_keys(relation, fds)
    assert set(result) == _brute_force_keys(relation, fds)
    assert len(result) == len(set(result))


def test_every_key_is_superkey_and_minimal() -> None:
    relation = frozenset("ABCDE")
    fds = fds_of("A,B -> C", "C -> D", "D -> A", "E -> E")
    for key in find_candidate_keys(relation, fds):
        assert attribute_closure(key, fds) == relation
        for attr in key:
            assert attribute_closure(key - {attr}, fds) != relation


def test_fd_outside_relation_rejected() -> None:
    with pytest.raises(ValueError, match="outside the relation"):
        find_candidate_keys(["A", "B"], fds_of("A -> Z"))
    with pytest.raises(ValueError, match="outside the relation"):
        find_candidate_keys(["A", "B"], fds_of("Z -> A"))


def test_bare_string_rejected() -> None:
    with pytest.raises(TypeError):
        find_candidate_keys("ABC", [])


def test_non_fd_rejected() -> None:
    with pytest.raises(TypeError):
        find_candidate_keys(["A"], ["A -> A"])  # type: ignore[list-item]


def test_non_string_and_empty_names_rejected() -> None:
    with pytest.raises(TypeError):
        find_candidate_keys([1], [])  # type: ignore[list-item]
    with pytest.raises(ValueError):
        find_candidate_keys(["A", " "], [])
