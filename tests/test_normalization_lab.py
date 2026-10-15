"""Tests for the synthetic demo dataset and the Normalization Lab pipeline."""

import csv
import io
import zipfile
from dataclasses import replace

import pytest

from dbschemax.demo_data import (
    DEMO_ATTRIBUTES,
    DEMO_CANDIDATE_KEY,
    atomicity_problems,
    build_demo_dataset,
)
from dbschemax.normalization_lab import (
    FDViolationError,
    build_zip,
    check_fds,
    describe_failures,
    filter_rows,
    natural_join,
    project_rows,
    reconstruct,
    run_lab,
    to_csv,
)
from dbschemax.synthesis import synthesize_3nf

EXPECTED_COLUMNS = (
    "StudentID", "StudentName", "StudentEmail", "DeptID", "DeptName", "CourseID",
    "CourseName", "Semester", "InstructorID", "InstructorName", "Grade",
)
EXPECTED_SCHEMAS = {
    "Student": {"StudentID", "StudentName", "StudentEmail", "DeptID"},
    "Department": {"DeptID", "DeptName"},
    "Course": {"CourseID", "CourseName"},
    "Instructor": {"InstructorID", "InstructorName"},
    "CourseOffering": {"CourseID", "Semester", "InstructorID"},
    "Enrollment": {"StudentID", "CourseID", "Semester", "Grade"},
}
EXPECTED_ROWS = {
    "Student": 60, "Department": 5, "Course": 12,
    "Instructor": 8, "CourseOffering": 48, "Enrollment": 250,
}


@pytest.fixture(scope="module")
def dataset():
    return build_demo_dataset()


@pytest.fixture(scope="module")
def result(dataset):
    return run_lab(dataset)


# --- Source data ---------------------------------------------------------------

def test_exactly_250_unique_records_and_expected_columns(dataset) -> None:
    assert len(dataset.rows) == 250
    assert len(set(dataset.rows)) == 250
    assert dataset.attributes == EXPECTED_COLUMNS == DEMO_ATTRIBUTES
    assert all(len(row) == 11 for row in dataset.rows)


def test_entity_counts_and_full_representation(dataset) -> None:
    counts = {name: len(set(dataset.column(name))) for name in
              ("StudentID", "DeptID", "CourseID", "InstructorID", "Semester")}
    assert counts == {"StudentID": 60, "DeptID": 5, "CourseID": 12,
                      "InstructorID": 8, "Semester": 4}


def test_values_are_atomic(dataset) -> None:
    assert atomicity_problems(dataset) == []
    assert dataset.non_atomic == ()


def test_dataset_is_deterministic_and_labelled_synthetic() -> None:
    assert build_demo_dataset() == build_demo_dataset()
    assert "SYNTHETIC" in build_demo_dataset().notice
    assert all(row[2].endswith("@demo.example") for row in build_demo_dataset().rows)


def test_composite_enrollment_key_is_unique(dataset) -> None:
    key = [(r[0], r[5], r[7]) for r in dataset.rows]
    assert len(set(key)) == len(key) == 250


def test_all_declared_fds_hold_on_the_records(dataset) -> None:
    checks = check_fds(dataset.attributes, dataset.rows, dataset.fds)
    assert len(checks) == 6
    assert all(c.holds and c.violating_groups == 0 for c in checks)


# --- Failure handling -----------------------------------------------------------

def test_broken_dependency_is_reported_not_ignored(dataset) -> None:
    rows = list(dataset.rows)
    first = list(rows[0])
    first[1] = "Somebody Else"  # same StudentID, different StudentName
    rows[0] = tuple(first)
    broken = replace(dataset, rows=tuple(rows))
    with pytest.raises(FDViolationError) as info:
        run_lab(broken)
    failed = [c for c in info.value.checks if not c.holds]
    assert any("StudentID" in str(c.fd) for c in failed)
    lines = describe_failures(info.value.checks)
    assert lines and "StudentID = S001" in " ".join(lines)


def test_non_atomic_value_is_rejected(dataset) -> None:
    rows = list(dataset.rows)
    row = list(rows[3])
    row[10] = "A, B"
    rows[3] = tuple(row)
    with pytest.raises(ValueError, match="atomic"):
        run_lab(replace(dataset, rows=tuple(rows)))


def test_wrong_declared_key_is_rejected(dataset) -> None:
    with pytest.raises(ValueError, match="not a candidate key"):
        run_lab(replace(dataset, declared_key=frozenset({"StudentID"})))


# --- Output relations ---------------------------------------------------------------

def test_candidate_key_found_by_existing_engine(result) -> None:
    assert result.report.normalization.candidate_keys == (DEMO_CANDIDATE_KEY,)
    assert result.report.normalization.first_nf is True


def test_schemas_come_from_the_existing_synthesis(result, dataset) -> None:
    expected = synthesize_3nf(dataset.attributes, dataset.fds)
    assert {frozenset(r.attributes) for r in result.relations} == set(expected)
    assert {r.name: set(r.attributes) for r in result.relations} == EXPECTED_SCHEMAS


def test_projected_row_counts(result) -> None:
    assert {r.name: r.row_count for r in result.relations} == EXPECTED_ROWS


def test_primary_and_foreign_keys(result) -> None:
    pk = {r.name: set(r.primary_key) for r in result.relations}
    assert pk == {
        "Student": {"StudentID"}, "Department": {"DeptID"}, "Course": {"CourseID"},
        "Instructor": {"InstructorID"}, "CourseOffering": {"CourseID", "Semester"},
        "Enrollment": {"StudentID", "CourseID", "Semester"},
    }
    fk = {r.name: {(fk.columns, fk.references) for fk in r.foreign_keys} for r in result.relations}
    assert fk["Enrollment"] == {(("StudentID",), "Student"), (("CourseID", "Semester"), "CourseOffering")}
    assert fk["Student"] == {(("DeptID",), "Department")}
    assert fk["CourseOffering"] == {(("CourseID",), "Course"), (("InstructorID",), "Instructor")}
    assert fk["Department"] == fk["Course"] == fk["Instructor"] == set()
    assert all(f.verified for r in result.relations for f in r.foreign_keys)


def test_every_table_row_is_unique_and_keys_are_unique(result) -> None:
    for relation in result.relations:
        assert len(set(relation.rows)) == relation.row_count
        index = [relation.attributes.index(a) for a in relation.primary_key]
        keys = [tuple(row[i] for i in index) for row in relation.rows]
        assert len(set(keys)) == len(keys)


def test_projection_removes_duplicate_tuples() -> None:
    attrs = ("A", "B", "C")
    rows = [("1", "x", "p"), ("1", "x", "q"), ("2", "y", "p")]
    assert project_rows(attrs, rows, {"A", "B"}) == (("1", "x"), ("2", "y"))
    assert project_rows(attrs, rows, {"C"}) == (("p",), ("q",))


def test_duplicates_removed_counts(result) -> None:
    assert result.relation("Student").duplicates_removed == 190
    assert result.relation("Department").duplicates_removed == 245
    assert result.relation("Enrollment").duplicates_removed == 0


# --- Reconstruction ---------------------------------------------------------------------

def test_original_tuples_are_reconstructed(result) -> None:
    rec = result.reconstruction
    assert rec.ok and rec.missing == 0 and rec.extra == 0
    assert rec.original_count == rec.reconstructed_count == 250


def test_reconstruction_detects_a_lossy_decomposition(result, dataset) -> None:
    damaged = tuple(
        replace(r, rows=r.rows[1:]) if r.name == "Student" else r for r in result.relations
    )
    check = reconstruct(dataset.attributes, dataset.rows, damaged, prefer={"Enrollment"})
    assert not check.ok and check.missing > 0


def test_natural_join_basics() -> None:
    cols, rows = natural_join(("A", "B"), [("1", "x"), ("2", "y")], ("B", "C"), [("x", "p")])
    assert cols == ("A", "B", "C") and rows == (("1", "x", "p"),)


# --- Counts, filter, export ----------------------------------------------------------------

def test_before_after_metrics(result) -> None:
    m = result.metrics
    assert (m.relations_before, m.rows_before, m.cells_before) == (1, 250, 2750)
    assert m.relations_after == 6
    assert m.rows_after == sum(EXPECTED_ROWS.values()) == 383
    assert m.cells_after == sum(r.row_count * len(r.attributes) for r in result.relations)
    assert m.rows_delta > 0  # total rows go up; only repetition inside rows shrinks
    assert m.cells_after < m.cells_before


def test_filter_rows(dataset) -> None:
    assert len(filter_rows(dataset.rows, "")) == 250
    hits = filter_rows(dataset.rows, "s001")
    assert hits and all(any("s001" in c.lower() for c in row) for row in hits)
    assert filter_rows(dataset.rows, "no such text") == ()


def test_csv_and_zip_export(result) -> None:
    student = result.relation("Student")
    parsed = list(csv.reader(io.StringIO(to_csv(student.attributes, student.rows).decode("utf-8"))))
    assert tuple(parsed[0]) == student.attributes and len(parsed) == 61
    with zipfile.ZipFile(io.BytesIO(build_zip(result.relations))) as archive:
        assert sorted(archive.namelist()) == sorted(f"{n}.csv" for n in EXPECTED_ROWS)
        enrollment = archive.read("Enrollment.csv").decode("utf-8").splitlines()
        assert len(enrollment) == 251
