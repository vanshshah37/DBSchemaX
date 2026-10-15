"""Tests for shared CLI/UI input parsing."""

import pytest

from dbschemax.inputs import InputError, parse_attribute_list, parse_fd_items, parse_fd_lines
from dbschemax.parser import parse_fd


def test_attribute_list_valid() -> None:
    assert parse_attribute_list("A,B,C") == ("A", "B", "C")
    assert parse_attribute_list("  StudentID ,  CourseID\t") == ("StudentID", "CourseID")
    assert parse_attribute_list("A") == ("A",)
    assert parse_attribute_list("b, A") == ("b", "A")  # order and case preserved


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("", "no attributes"),
        ("   ", "no attributes"),
        ("A,,B", "empty attribute"),
        ("A,B,", "empty attribute"),
        (",A", "empty attribute"),
        ("A B", "whitespace"),
        ("A,B,A", "duplicate"),
        ("∅", "not a valid"),
        ("A,a>b", "not a valid"),
        ("A,→", "not a valid"),
    ],
)
def test_attribute_list_invalid(text: str, fragment: str) -> None:
    with pytest.raises(InputError, match=fragment):
        parse_attribute_list(text)


def test_attribute_list_case_sensitive_not_duplicate() -> None:
    assert parse_attribute_list("a,A") == ("a", "A")


@pytest.mark.parametrize("value", [None, 5, ["A"], b"A"])
def test_attribute_list_type_error(value: object) -> None:
    with pytest.raises(TypeError):
        parse_attribute_list(value)  # type: ignore[arg-type]


def test_input_error_is_value_error() -> None:
    assert issubclass(InputError, ValueError)


def test_fd_lines_parse_with_comments_and_blanks() -> None:
    text = "# keys\nA -> B\n\n  B,C → D  \n∅ -> E\n"
    assert parse_fd_lines(text) == (parse_fd("A -> B"), parse_fd("B,C -> D"), parse_fd("∅ -> E"))
    assert parse_fd_lines("") == ()
    assert parse_fd_lines("\n  \n# only a comment") == ()


def test_fd_lines_report_line_number() -> None:
    with pytest.raises(InputError, match="line 3"):
        parse_fd_lines("A -> B\n\nA B -> C\n")
    with pytest.raises(InputError, match="line 1.*missing arrow"):
        parse_fd_lines("A, B")
    with pytest.raises(InputError, match="line 2.*multiple arrows"):
        parse_fd_lines("A -> B\nA -> B -> C")


def test_fd_lines_type_error() -> None:
    with pytest.raises(TypeError):
        parse_fd_lines(None)  # type: ignore[arg-type]


def test_fd_items() -> None:
    assert parse_fd_items(["A -> B", "B -> C"]) == (parse_fd("A -> B"), parse_fd("B -> C"))
    assert parse_fd_items(iter(["A -> B"])) == (parse_fd("A -> B"),)
    with pytest.raises(InputError, match=r"invalid --fd 'A B'"):
        parse_fd_items(["A -> B", "A B"], label="--fd")
