from __future__ import annotations

import pytest

from dbschemax import FunctionalDependency, Relation


def test_relation_normalizes_name_and_attributes() -> None:
    relation = Relation("  Student  ", {" A ", "B", "A"})

    assert relation.name == "Student"
    assert relation.attributes == frozenset({"A", "B"})
    assert str(relation) == "Student(A, B)"


def test_relation_rejects_empty_name_and_attributes() -> None:
    with pytest.raises(ValueError):
        Relation("   ", {"A"})

    with pytest.raises(ValueError):
        Relation("R", set())


def test_attribute_names_must_be_strings_and_not_blank() -> None:
    with pytest.raises(TypeError):
        Relation("R", "ABC")

    with pytest.raises(TypeError):
        Relation("R", {"A", 1})

    with pytest.raises(ValueError):
        Relation("R", {"A", "   "})


def test_functional_dependency_normalizes_attributes() -> None:
    fd = FunctionalDependency({" A ", "B", "A"}, {" C "})

    assert fd.lhs == frozenset({"A", "B"})
    assert fd.rhs == frozenset({"C"})
    assert fd.attributes == frozenset({"A", "B", "C"})


def test_empty_lhs_is_valid_and_formats_as_empty_set() -> None:
    fd = FunctionalDependency(set(), {"A"})

    assert fd.lhs == frozenset()
    assert not fd.is_trivial()
    assert str(fd) == "∅ -> A"


def test_empty_rhs_is_invalid() -> None:
    with pytest.raises(ValueError):
        FunctionalDependency({"A"}, set())


def test_functional_dependency_triviality() -> None:
    assert FunctionalDependency({"A", "B"}, {"A"}).is_trivial()
    assert not FunctionalDependency({"A"}, {"B"}).is_trivial()


def test_models_are_immutable_and_hashable() -> None:
    relation = Relation("R", {"A", "B"})
    fd = FunctionalDependency({"A"}, {"B"})

    assert hash(relation)
    assert hash(fd)

    with pytest.raises((AttributeError, TypeError)):
        relation.name = "X"  # type: ignore[misc]

    with pytest.raises((AttributeError, TypeError)):
        fd.lhs = frozenset({"B"})  # type: ignore[misc]
