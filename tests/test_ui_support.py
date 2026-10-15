"""Tests for Streamlit-independent dashboard support code."""

import dataclasses
import json
import sqlite3
from pathlib import Path

import pytest

from dbschemax.parser import parse_fd
from dbschemax.reporting import render_json_report, render_markdown_report
from dbschemax.sqlite_integration import inspect_sqlite_table, list_user_tables
from dbschemax.ui_support import (
    DEMO_CASE,
    EXAMPLE_CASES,
    MAX_UI_ATTRIBUTES,
    AnalysisSpec,
    build_downloads,
    fd_graph_dot,
    run_analysis,
    spec_from_example,
    temporary_database,
)


def test_demo_is_computed_by_the_real_engine() -> None:
    report = run_analysis(spec_from_example(DEMO_CASE))
    norm = report.normalization
    assert norm.candidate_keys == (frozenset({"StudentID", "CourseID"}),)
    assert norm.prime_attributes == frozenset({"StudentID", "CourseID"})
    assert not norm.satisfies_2nf and not norm.satisfies_3nf and not norm.satisfies_bcnf
    assert {(p.determinant, p.attribute) for p in norm.partial_dependencies} == {
        (frozenset({"StudentID"}), "StudentName"),
        (frozenset({"CourseID"}), "CourseName"),
    }
    assert set(report.synthesized_schemas) == {
        frozenset({"StudentID", "StudentName"}),
        frozenset({"CourseID", "CourseName"}),
        frozenset({"StudentID", "CourseID", "Grade"}),
    }
    assert report.candidate_key_represented
    assert norm.first_nf is None  # never assumed from FDs


def test_example_cases_all_analyze() -> None:
    keys = {c.key for c in EXAMPLE_CASES}
    assert len(keys) == len(EXAMPLE_CASES) and DEMO_CASE.key in keys
    for case in EXAMPLE_CASES:
        spec = spec_from_example(case)
        assert spec.label.startswith("Sample data")
        report = run_analysis(spec)
        assert report.relation_name == case.name
    assert spec_from_example(DEMO_CASE).source == "demo"
    assert spec_from_example(EXAMPLE_CASES[1]).source == "example"


def test_example_semantics() -> None:
    reports = {c.key: run_analysis(spec_from_example(c)) for c in EXAMPLE_CASES}
    assert reports["three_nf_not_bcnf"].normalization.highest_fd_normal_form == "3NF"
    assert len(reports["multiple_keys"].normalization.candidate_keys) == 2
    empty = reports["empty_lhs"].normalization
    assert empty.candidate_keys == (frozenset({"C"}),)
    assert reports["incomplete"].normalization.satisfies_bcnf  # optimistic: FD missing


def test_run_analysis_manual_spec_and_non_atomic() -> None:
    spec = AnalysisSpec(
        source="manual", label="User-entered", name="R", attributes=("A", "B"),
        user_fds=(parse_fd("A -> B"),), non_atomic=("B",),
    )
    report = run_analysis(spec)
    assert report.normalization.first_nf is False
    assert report.relation_name == "R"


def test_run_analysis_attribute_limit() -> None:
    attrs = tuple(f"A{i}" for i in range(MAX_UI_ATTRIBUTES + 1))
    spec = AnalysisSpec("manual", "x", "R", attrs, ())
    with pytest.raises(ValueError, match="limited"):
        run_analysis(spec)
    ok = AnalysisSpec("manual", "x", "R", attrs[:MAX_UI_ATTRIBUTES], ())
    assert run_analysis(ok).normalization.candidate_keys == (frozenset(attrs[:MAX_UI_ATTRIBUTES]),)


def test_run_analysis_invalid_fd_outside_relation() -> None:
    spec = AnalysisSpec("manual", "x", "R", ("A",), (parse_fd("A -> Z"),))
    with pytest.raises(ValueError, match="outside the relation"):
        run_analysis(spec)


def test_spec_is_immutable() -> None:
    spec = spec_from_example(DEMO_CASE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.name = "x"  # type: ignore[misc]


def test_downloads_come_from_the_real_renderers() -> None:
    report = run_analysis(spec_from_example(EXAMPLE_CASES[3]))  # contains ∅
    downloads = build_downloads(report)
    name, data, mime = downloads["markdown"]
    assert name == "Config_report.md" and mime == "text/markdown"
    assert data == render_markdown_report(report).encode("utf-8")
    assert "∅" in data.decode("utf-8")
    name, data, mime = downloads["json"]
    assert name == "Config_report.json" and mime == "application/json"
    assert data.decode("utf-8") == render_json_report(report)
    assert json.loads(data)["relation"]["name"] == "Config"


def test_download_filename_is_sanitized() -> None:
    spec = AnalysisSpec("manual", "x", "my table/../x?", ("A",), ())
    safe = build_downloads(run_analysis(spec))["json"][0]
    assert "/" not in safe and "?" not in safe and safe.endswith("_report.json")
    unnamed = AnalysisSpec("manual", "x", None, ("A",), ())
    assert build_downloads(run_analysis(unnamed))["markdown"][0] == "relation_report.md"


def test_temporary_database_roundtrip_and_cleanup(tmp_path: Path) -> None:
    original = tmp_path / "orig.db"
    connection = sqlite3.connect(original)
    connection.executescript("CREATE TABLE t (id INT PRIMARY KEY, v TEXT);")
    connection.commit()
    connection.close()
    before = original.read_bytes()
    with temporary_database(original.read_bytes()) as copy:
        assert copy != original and copy.exists()
        assert list_user_tables(copy) == ("t",)
        assert inspect_sqlite_table(copy, "t").primary_key == ("id",)
        kept = copy
    assert not kept.exists() and not kept.parent.exists()  # deleted afterwards
    assert original.read_bytes() == before


def test_temporary_database_cleans_up_on_error() -> None:
    seen: list[Path] = []
    with pytest.raises(RuntimeError):
        with temporary_database(b"not a database") as copy:
            seen.append(copy)
            raise RuntimeError("boom")
    assert not seen[0].exists()


def test_temporary_database_requires_bytes() -> None:
    with pytest.raises(TypeError):
        with temporary_database("text"):  # type: ignore[arg-type]
            pass


def test_graph_dot_simple_composite_and_empty_lhs() -> None:
    dot = fd_graph_dot(["A", "B", "C"], [parse_fd("A -> B"), parse_fd("A,B -> C"), parse_fd("∅ -> A")])
    assert dot.startswith("digraph FDs {") and dot.rstrip().endswith("}")
    assert '"A" -> "B";' in dot  # simple FD: direct edge
    assert '"A" -> "fd' in dot and '"B" -> "fd' in dot  # composite LHS via junction
    assert '"∅"' in dot


def test_graph_dot_highlights_and_escapes() -> None:
    dot = fd_graph_dot(['we"ird', "B"], [parse_fd("B -> B2")], start=["B"], closure=["B", "B2"])
    assert '"we\\"ird"' in dot
    assert '"B" [fillcolor=lightblue];' in dot
    assert "fillcolor=white" in dot


def test_graph_dot_deterministic() -> None:
    fds = [parse_fd("A,B -> C"), parse_fd("C -> A")]
    assert fd_graph_dot("ABC", fds) == fd_graph_dot("CBA", list(reversed(fds)))
