"""Tests for attribute closure."""

import itertools

import pytest

from dbschemax.closure import attribute_closure
from dbschemax.models import FunctionalDependency
from dbschemax.parser import parse_fd


def fds_of(*texts: str) -> list[FunctionalDependency]:
    return [parse_fd(t) for t in texts]


def test_basic_chain() -> None:
    fds = fds_of("A -> B", "B -> C", "C -> D")
    assert attribute_closure({"A"}, fds) == frozenset("ABCD")


def test_composite_lhs() -> None:
    fds = fds_of("A,B -> C", "C -> D")
    assert attribute_closure({"A", "B"}, fds) == frozenset("ABCD")
    assert attribute_closure({"A"}, fds) == frozenset("A")  # LHS not fully covered


def test_needs_multiple_attributes() -> None:
    fds = fds_of("A -> C", "B -> D", "C,D -> E")
    assert attribute_closure({"A", "B"}, fds) == frozenset("ABCDE")
    assert attribute_closure({"A"}, fds) == frozenset("AC")


def test_no_applicable_fd() -> None:
    assert attribute_closure({"C"}, fds_of("A -> B")) == frozenset("C")


def test_empty_fd_set() -> None:
    assert attribute_closure({"A"}, []) == frozenset("A")


def test_empty_start_and_empty_lhs_fd() -> None:
    fds = fds_of("∅ -> A", "A -> B")
    assert attribute_closure(set(), fds) == frozenset("AB")
    assert attribute_closure(set(), []) == frozenset()
    assert attribute_closure(set(), fds_of("A -> B")) == frozenset()


def test_empty_lhs_fd_applies_to_any_start() -> None:
    assert attribute_closure({"X"}, fds_of("∅ -> A")) == frozenset({"X", "A"})


def test_cyclic_dependencies_terminate() -> None:
    fds = fds_of("A -> B", "B -> C", "C -> A")
    assert attribute_closure({"A"}, fds) == frozenset("ABC")
    assert attribute_closure({"C"}, fds) == frozenset("ABC")


def test_multiple_rhs() -> None:
    assert attribute_closure({"A"}, fds_of("A -> B,C")) == frozenset("ABC")


def test_trivial_and_self_referencing_fd() -> None:
    assert attribute_closure({"A"}, fds_of("A -> A", "A,B -> A")) == frozenset("A")


def test_multi_character_names() -> None:
    fds = fds_of("StudentID -> StudentName", "StudentID, CourseID -> Grade")
    assert attribute_closure({"StudentID", "CourseID"}, fds) == frozenset(
        {"StudentID", "CourseID", "StudentName", "Grade"}
    )


def test_inputs_not_mutated() -> None:
    attrs = {"A", "B"}
    attr_list = ["A", "B"]
    fds = fds_of("A,B -> C", "C -> D")
    fds_before = list(fds)
    lhs_before = [fd.lhs for fd in fds]
    rhs_before = [fd.rhs for fd in fds]

    attribute_closure(attrs, fds)
    attribute_closure(attr_list, fds)

    assert attrs == {"A", "B"}
    assert attr_list == ["A", "B"]
    assert fds == fds_before
    assert [fd.lhs for fd in fds] == lhs_before
    assert [fd.rhs for fd in fds] == rhs_before


def test_returns_frozenset() -> None:
    assert isinstance(attribute_closure({"A"}, []), frozenset)
    assert isinstance(attribute_closure([], []), frozenset)


def test_any_iterable_input() -> None:
    fds = fds_of("A -> B", "B -> C")
    expected = frozenset("ABC")
    assert attribute_closure(["A"], fds) == expected
    assert attribute_closure(("A",), tuple(fds)) == expected
    assert attribute_closure(frozenset("A"), frozenset(fds)) == expected
    assert attribute_closure(iter(["A"]), iter(fds)) == expected  # one-shot iterators
    assert attribute_closure((a for a in "A"), (f for f in fds)) == expected
    assert attribute_closure({"A": 1}, fds) == expected  # dict iterates keys


def test_duplicates_in_input() -> None:
    fds = fds_of("A -> B")
    assert attribute_closure(["A", "A"], fds + fds) == frozenset("AB")


def test_deterministic_and_order_independent() -> None:
    fds = fds_of("A -> B", "B,C -> D", "D -> E", "E -> A,F", "∅ -> C")
    results = {
        attribute_closure({"A"}, list(perm)) for perm in itertools.permutations(fds)
    }
    assert results == {frozenset("ABCDEF")}
    assert attribute_closure({"A"}, fds) == attribute_closure({"A"}, fds)


def test_closure_properties() -> None:
    fds = fds_of("A -> B", "B -> C", "D -> E")
    x = {"A", "D"}
    closure = attribute_closure(x, fds)
    assert x <= closure  # extensive
    assert attribute_closure(closure, fds) == closure  # idempotent
    assert attribute_closure({"A"}, fds) <= attribute_closure({"A", "D"}, fds)  # monotone


def test_whitespace_normalized_like_models() -> None:
    assert attribute_closure([" A "], fds_of("A -> B")) == frozenset("AB")


def test_bare_string_rejected() -> None:
    with pytest.raises(TypeError):
        attribute_closure("AB", [])


def test_non_string_attribute_rejected() -> None:
    with pytest.raises(TypeError):
        attribute_closure([1], [])  # type: ignore[list-item]


def test_empty_attribute_name_rejected() -> None:
    with pytest.raises(ValueError):
        attribute_closure(["A", ""], [])


def test_non_fd_rejected() -> None:
    with pytest.raises(TypeError):
        attribute_closure({"A"}, ["A -> B"])  # type: ignore[list-item]
