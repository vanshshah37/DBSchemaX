"""Tests for the functional dependency parser."""

import pytest

from dbschemax.models import FunctionalDependency
from dbschemax.parser import FDParseError, parse_fd


def fd(lhs: set[str], rhs: set[str]) -> FunctionalDependency:
    return FunctionalDependency(frozenset(lhs), frozenset(rhs))


def test_single_attributes() -> None:
    assert parse_fd("A -> B") == fd({"A"}, {"B"})


def test_multiple_attributes() -> None:
    assert parse_fd("A,B -> C,D") == fd({"A", "B"}, {"C", "D"})


def test_long_names_preserved_exactly() -> None:
    result = parse_fd("StudentID, CourseID -> Grade")
    assert result == fd({"StudentID", "CourseID"}, {"Grade"})
    assert parse_fd("a -> B") == fd({"a"}, {"B"})  # case preserved


def test_whitespace() -> None:
    assert parse_fd("  \tA ,  B\n->  C ,D  ") == fd({"A", "B"}, {"C", "D"})
    assert parse_fd("A->B") == fd({"A"}, {"B"})


def test_unicode_arrow() -> None:
    assert parse_fd("A → B") == fd({"A"}, {"B"})
    assert parse_fd("A,B→C") == fd({"A", "B"}, {"C"})


def test_empty_lhs() -> None:
    result = parse_fd("∅ -> A")
    assert result == fd(set(), {"A"})
    assert result.lhs == frozenset()
    assert parse_fd(" ∅→A ") == fd(set(), {"A"})


def test_duplicate_attributes_removed() -> None:
    assert parse_fd("A, A, B -> C, C") == fd({"A", "B"}, {"C"})


def test_deterministic_regardless_of_order() -> None:
    assert parse_fd("B,A -> D,C") == parse_fd("A,B -> C,D")
    assert hash(parse_fd("B,A -> D,C")) == hash(parse_fd("A,B -> C,D"))


def test_returns_existing_model() -> None:
    assert isinstance(parse_fd("A -> B"), FunctionalDependency)


def test_str_roundtrip() -> None:
    for text in ("A, B -> C", "∅ -> A"):
        assert parse_fd(str(parse_fd(text))) == parse_fd(text)


@pytest.mark.parametrize("text", ["A B", "A, B", "A => B", ""])
def test_missing_arrow(text: str) -> None:
    with pytest.raises(FDParseError, match="missing arrow"):
        parse_fd(text)


@pytest.mark.parametrize("text", ["A -> B -> C", "A → B → C", "A -> B → C", "A ->-> B"])
def test_multiple_arrows(text: str) -> None:
    with pytest.raises(FDParseError, match="multiple arrows"):
        parse_fd(text)


@pytest.mark.parametrize("text", ["A ->", "A ->   ", "A → ", "∅ ->"])
def test_empty_rhs(text: str) -> None:
    with pytest.raises(FDParseError, match="right-hand side"):
        parse_fd(text)


@pytest.mark.parametrize(
    "text", ["A,,B -> C", "A, -> C", ",A -> C", "A -> B,", "A -> ,B", "A -> B,,C"]
)
def test_empty_attribute_name(text: str) -> None:
    with pytest.raises(FDParseError, match="empty attribute"):
        parse_fd(text)


def test_bare_empty_lhs_rejected() -> None:
    with pytest.raises(FDParseError, match="left-hand side is empty"):
        parse_fd("-> A")


@pytest.mark.parametrize("text", ["∅, A -> B", "A -> ∅", "A, ∅ -> B", "∅ -> ∅"])
def test_empty_set_symbol_misuse(text: str) -> None:
    with pytest.raises(FDParseError):
        parse_fd(text)


@pytest.mark.parametrize("text", ["A B -> C", "A -> B C"])
def test_missing_comma_rejected(text: str) -> None:
    with pytest.raises(FDParseError, match="whitespace"):
        parse_fd(text)


def test_malformed_arrow_rejected() -> None:
    with pytest.raises(FDParseError, match="malformed arrow"):
        parse_fd("A --> B")


@pytest.mark.parametrize("value", [None, 123, ["A -> B"], b"A -> B", ("A", "B")])
def test_non_string_input(value: object) -> None:
    with pytest.raises(TypeError):
        parse_fd(value)  # type: ignore[arg-type]


def test_parse_error_is_value_error() -> None:
    assert issubclass(FDParseError, ValueError)
