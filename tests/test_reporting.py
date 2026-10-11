"""Tests for report building, Markdown/JSON rendering and saving.

The mathematical engine is used for real (no mocks).
"""

import dataclasses
import itertools
import json
import re
import sqlite3
from pathlib import Path

import pytest

from dbschemax.candidate_keys import find_candidate_keys
from dbschemax.minimal_cover import minimal_cover
from dbschemax.models import FunctionalDependency, Relation
from dbschemax.normalization import analyze_normalization
from dbschemax.parser import parse_fd
from dbschemax.reporting import (
    AnalysisReport,
    ReportSaveError,
    build_analysis_report,
    build_table_report,
    format_set,
    render_json_report,
    render_markdown_report,
    report_to_dict,
    save_json_report,
    save_markdown_report,
)
from dbschemax.sqlite_integration import inspect_sqlite_table
from dbschemax.synthesis import synthesize_3nf


def fds_of(*texts: str) -> list[FunctionalDependency]:
    return [parse_fd(t) for t in texts]


def report_of(attrs: str, *fd_texts: str, **kwargs) -> AnalysisReport:
    return build_analysis_report(list(attrs), fds_of(*fd_texts), **kwargs)


def make_db(tmp_path: Path, script: str) -> Path:
    path = tmp_path / "t.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(script)
        connection.commit()
    finally:
        connection.close()
    return path


def sets(*groups: str) -> tuple[frozenset[str], ...]:
    return tuple(frozenset(g) for g in groups)


# --- Report contents: relations of each kind --------------------------------

def test_simple_relation() -> None:
    report = report_of("ABC", "A -> B", "B -> C", table_name="R")
    assert report.relation_name == "R"
    assert report.attributes == ("A", "B", "C")
    assert report.normalization.candidate_keys == sets("A")
    assert [str(fd) for fd in report.minimal_cover] == ["A -> B", "B -> C"]
    assert report.synthesized_schemas == sets("AB", "BC")
    assert report.candidate_key_represented
    md = render_markdown_report(report)
    assert "# DBSchemaX Analysis Report: `R`" in md
    assert "{B} -> C violates 3NF" in md
    assert "`{A}`" in md

def test_generated_column_metadata_in_markdown_and_json(
    tmp_path: Path,
) -> None:
    path = make_db(
        tmp_path,
        """
        CREATE TABLE totals (
            price REAL,
            quantity INTEGER,
            total REAL GENERATED ALWAYS AS (price * quantity) STORED
        );
        """,
    )

    table = inspect_sqlite_table(path, "totals")
    report = build_table_report(table)

    markdown = render_markdown_report(report)
    payload = json.loads(render_json_report(report))

    total = next(
        column
        for column in payload["table_metadata"]["columns"]
        if column["name"] == "total"
    )

    assert total["hidden"] == 3
    assert total["is_generated"] is True
    assert "generated stored" in markdown

    
def test_composite_candidate_key() -> None:
    report = report_of("ABC", "A,B -> C")
    assert report.normalization.candidate_keys == sets("AB")
    assert report.normalization.satisfies_bcnf
    assert "`{A, B}`" in render_markdown_report(report)


def test_multiple_candidate_keys() -> None:
    report = report_of("ABC", "A -> B", "B -> A", "A -> C")
    assert report.normalization.candidate_keys == sets("A", "B")
    md = render_markdown_report(report)
    assert md.count("**Candidate key:**") == 2
    assert report_to_dict(report)["candidate_keys"]["keys"] == [["A"], ["B"]]


def test_relation_in_bcnf() -> None:
    report = report_of("ABC", "A -> B,C")
    assert report.violation_details == ()
    md = render_markdown_report(report)
    assert "No 2NF, 3NF or BCNF violations were found" in md
    assert "**Highest FD-based normal form (2NF/3NF/BCNF):** BCNF" in md


def test_3nf_but_not_bcnf() -> None:
    report = report_of("ABC", "A,B -> C", "C -> B")
    norm = report.normalization
    assert norm.satisfies_3nf and not norm.satisfies_bcnf
    assert [v.normal_form for v in report.violation_details] == ["BCNF"]
    detail = report.violation_details[0]
    assert detail.determinant == frozenset("C") and detail.attribute == "B"
    assert detail.determinant_closure == frozenset("BC")
    assert "allowed by 3NF because B is a prime attribute" in detail.explanation
    assert "lacks {A}" in detail.explanation


def test_relation_violating_3nf() -> None:
    report = report_of("ABC", "A -> B", "B -> C")
    forms = [v.normal_form for v in report.violation_details]
    assert forms == ["3NF", "BCNF"]
    third = report.violation_details[0]
    assert "not a superkey" in third.explanation
    assert "C is not a prime attribute" in third.explanation
    md = render_markdown_report(report)
    assert "| 3NF | **Violated** |" in md and "| 2NF | Satisfied |" in md


def test_2nf_violation_explained() -> None:
    report = report_of("ABCD", "A,B -> C", "A -> D")
    two = [v for v in report.violation_details if v.normal_form == "2NF"]
    assert len(two) == 1
    assert two[0].key == frozenset("AB") and two[0].determinant == frozenset("A")
    assert "proper subset of candidate key {A, B}" in two[0].explanation


def test_empty_lhs_notation() -> None:
    report = report_of("AB", "∅ -> A")
    md = render_markdown_report(report)
    assert "`∅ -> A`" in md
    assert format_set(frozenset()) == "∅"
    assert report_to_dict(report)["functional_dependencies"]["analysis_set"][0]["lhs"] == []


# --- 1NF handling ----------------------------------------------------------

def test_1nf_unknown_from_fds_alone() -> None:
    report = report_of("ABC", "A -> B,C")
    md = render_markdown_report(report)
    assert report.normalization.first_nf is None
    assert "| 1NF | **Unknown** | Not assessable from FD information" in md
    assert "| 1NF | Satisfied" not in md
    first = report_to_dict(report)["normalization"]["first_nf"]
    assert first == {
        "status": "unknown",
        "assessable_from_fds": False,
        "non_atomic_attributes": None,
    }
    assert any("1NF status is unknown" in line for line in report.limitations)


def test_1nf_from_caller_information() -> None:
    ok = report_of("AB", "A -> B", non_atomic_attributes=[])
    bad = report_of("AB", "A -> B", non_atomic_attributes=["B"])
    assert report_to_dict(ok)["normalization"]["first_nf"]["status"] == "satisfied"
    assert report_to_dict(bad)["normalization"]["first_nf"]["status"] == "violated"
    assert report_to_dict(bad)["normalization"]["first_nf"]["non_atomic_attributes"] == ["B"]
    assert "asserted by the caller" in render_markdown_report(ok)
    assert "non-atomic attributes: B" in " ".join(bad.limitations)
    # atomicity information never alters the FD-based results
    assert ok.normalization.satisfies_bcnf == bad.normalization.satisfies_bcnf


# --- Synthesis -------------------------------------------------------------

def test_synthesized_schemas_and_key_step() -> None:
    report = report_of("ABCD", "A -> B", "C -> D")
    assert report.synthesized_schemas == sets("AB", "AC", "CD")
    assert report.keys_in_synthesis == sets("AC")
    md = render_markdown_report(report)
    assert "| R1 | `{A, B}` |" in md and "| R2 | `{A, C}` |" in md
    assert "**Original candidate key represented:** Yes" in md
    synth = report_to_dict(report)["synthesis"]
    assert synth["schemas"] == [["A", "B"], ["A", "C"], ["C", "D"]]
    assert synth["candidate_key_represented"] is True


def test_synthesis_text_makes_no_unsupported_lossless_claim() -> None:
    md = render_markdown_report(report_of("ABC", "A -> B"))
    assert "Lossless join is NOT verified by DBSchemaX" in md
    assert "dependency preservation" in md
    assert "does not guarantee BCNF" in md
    assert "always lossless" not in md.lower()


# --- Agreement with the engine (no mocks) -----------------------------------

def test_report_matches_engine_results() -> None:
    fds = fds_of("A,B -> C", "C -> B", "B -> D", "∅ -> E")
    attrs = frozenset("ABCDE")
    report = build_analysis_report(attrs, fds)
    assert report.minimal_cover == minimal_cover(fds)
    assert report.normalization == analyze_normalization(attrs, fds)
    assert report.normalization.candidate_keys == find_candidate_keys(attrs, fds)
    assert report.synthesized_schemas == synthesize_3nf(attrs, fds)


def test_relation_object_input_and_name() -> None:
    report = build_analysis_report(Relation("Orders", {"A", "B"}), fds_of("A -> B"))
    assert report.relation_name == "Orders"
    assert build_analysis_report(Relation("Orders", {"A", "B"}), [], "Other").relation_name == "Other"


# --- Minimal / invalid inputs ----------------------------------------------

def test_minimal_valid_input() -> None:
    report = report_of("A")
    assert report.normalization.candidate_keys == sets("A")
    assert report.synthesized_schemas == sets("A")
    assert report.minimal_cover == ()
    md = render_markdown_report(report)
    assert "_No functional dependencies were supplied._" in md
    assert "only candidate key is the set of all attributes" in md
    json.loads(render_json_report(report))


def test_empty_candidate_key_noted() -> None:
    md = render_markdown_report(report_of("AB", "∅ -> A", "∅ -> B"))
    assert "The empty set is a candidate key" in md


def test_invalid_inputs_rejected() -> None:
    with pytest.raises(ValueError):
        build_analysis_report([], [])  # empty relation
    with pytest.raises(TypeError):
        build_analysis_report("ABC", [])  # bare string
    with pytest.raises(TypeError):
        build_analysis_report(["A"], ["A -> A"])  # type: ignore[list-item]
    with pytest.raises(TypeError):
        build_analysis_report(["A"], [], declared_fds=["A -> A"])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="outside the relation"):
        report_of("AB", "A -> Z")
    with pytest.raises(ValueError, match="attributes are required"):
        build_analysis_report(None, [])
    with pytest.raises(TypeError):
        build_analysis_report(["A"], [], non_atomic_attributes="A")
    with pytest.raises(ValueError, match="outside the relation"):
        report_of("AB", "A -> B", non_atomic_attributes=["Z"])
    with pytest.raises(TypeError):
        build_analysis_report(["A"], [], table_name=5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        build_analysis_report(["A"], [], table_name="  ")
    with pytest.raises(TypeError):
        build_analysis_report(["A"], [], derivation_notes=[1])  # type: ignore[list-item]


# --- Determinism and immutability ------------------------------------------

def test_deterministic_output() -> None:
    fds = fds_of("A,B -> C", "C -> B", "B -> D", "A -> E")
    outputs = {
        (
            render_markdown_report(build_analysis_report(list("EDCBA"), list(p), "R")),
            render_json_report(build_analysis_report(list("EDCBA"), list(p), "R")),
        )
        for p in itertools.permutations(fds)
    }
    assert len(outputs) == 1
    again = build_analysis_report(list("ABCDE"), fds, "R")
    assert render_json_report(again) == render_json_report(again)


def test_duplicate_fds_collapsed() -> None:
    report = report_of("AB", "A -> B", "A -> B")
    assert len(report.user_fds) == 1


def test_inputs_not_mutated_and_report_frozen() -> None:
    attrs = ["A", "B", "C"]
    fds = fds_of("A -> B", "B -> C")
    before = list(fds)
    atomic = ["C"]
    report = build_analysis_report(attrs, fds, non_atomic_attributes=atomic)
    render_markdown_report(report)
    render_json_report(report)
    assert attrs == ["A", "B", "C"] and fds == before and atomic == ["C"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.relation_name = "x"  # type: ignore[misc]


def test_generator_inputs() -> None:
    fds = fds_of("A -> B")
    report = build_analysis_report((a for a in "AB"), (f for f in fds))
    assert report.normalization.candidate_keys == sets("A")


# --- JSON ------------------------------------------------------------------

def test_json_is_valid_and_complete() -> None:
    report = report_of("ABCD", "A,B -> C", "C -> B", "∅ -> D", table_name="R")
    text = render_json_report(report)
    data = json.loads(text)
    assert data["schema_version"] == 1
    assert data["relation"] == {
        "name": "R",
        "attributes": ["A", "B", "C", "D"],
        "schema_source": "Attribute list supplied by the caller",
    }
    assert data["table_metadata"] is None
    assert set(data) == {
        "schema_version", "relation", "table_metadata", "functional_dependencies",
        "minimal_cover", "candidate_keys", "normalization", "synthesis", "limitations",
    }
    assert data["minimal_cover"][0] == {"lhs": [], "rhs": ["D"], "text": "∅ -> D"}
    assert data["candidate_keys"]["keys"] == [["A", "B"], ["A", "C"]]
    assert data["candidate_keys"]["prime_attributes"] == ["A", "B", "C"]
    assert data["candidate_keys"]["non_prime_attributes"] == ["D"]
    norm = data["normalization"]
    assert norm["bcnf"]["satisfied"] is False and norm["highest_fd_normal_form"]
    assert text.endswith("\n") and "∅" in text  # UTF-8, not \u2205 escapes


def test_json_deterministic_ordering_and_roundtrip() -> None:
    report = report_of("ABC", "A -> B", "B -> C")
    text = render_json_report(report)

    def check_sorted(pairs):
        keys = [k for k, _ in pairs]
        assert keys == sorted(keys)
        return dict(pairs)

    json.loads(text, object_pairs_hook=check_sorted)  # all object keys sorted
    data = json.loads(text)
    assert json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n" == text
    assert text == render_json_report(report)


def test_json_has_only_plain_types() -> None:
    def walk(value):
        if isinstance(value, dict):
            assert all(isinstance(k, str) for k in value)
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        else:
            assert value is None or isinstance(value, (str, bool, int))

    walk(report_to_dict(report_of("ABC", "A -> B", "∅ -> C")))


def test_json_indent_option() -> None:
    report = report_of("AB", "A -> B")
    compact = render_json_report(report, indent=0)
    assert json.loads(compact) == json.loads(render_json_report(report))


# --- Markdown formatting -----------------------------------------------------

def test_markdown_structure() -> None:
    md = render_markdown_report(report_of("ABC", "A -> B", "B -> C", table_name="R"))
    headings = [line for line in md.splitlines() if line.startswith("## ")]
    assert headings == [
        "## 1. Schema Overview",
        "## 2. Functional Dependencies",
        "## 3. Candidate Keys",
        "## 4. Normalization Analysis",
        "## 5. 3NF Synthesis",
        "## 6. Limitations and Assumptions",
    ]
    assert md.startswith("# DBSchemaX Analysis Report: `R`\n")
    assert md.endswith("\n") and not md.endswith("\n\n")
    assert "### 2.2 Minimal (canonical) cover" in md
    assert "| Normal form | Status | Basis |" in md


def test_markdown_tables_are_well_formed() -> None:
    table = _db_report_with_weird_columns()
    md = render_markdown_report(table)
    block: list[str] = []
    for line in md.splitlines() + [""]:
        if line.startswith("|"):
            block.append(line)
            continue
        if block:
            counts = {len(re.findall(r"(?<!\\)\|", row)) for row in block}
            assert len(counts) == 1, block
            assert re.fullmatch(r"\|( --- \|)+", block[1]), block[1]
            block = []


def _db_report_with_weird_columns() -> AnalysisReport:
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    path = make_db(tmp, 'CREATE TABLE "my tbl" ("a|b" INT PRIMARY KEY, "c d" TEXT DEFAULT \'x|y\');')
    return build_table_report(inspect_sqlite_table(path, "my tbl"))


def test_markdown_escapes_pipes_in_cells() -> None:
    md = render_markdown_report(_db_report_with_weird_columns())
    assert "`a\\|b`" in md and "`'x\\|y'`" in md


# --- SQLite metadata attached ----------------------------------------------

ORDERS = """
CREATE TABLE order_items (
    order_id INT NOT NULL, product_id INT NOT NULL, product_name TEXT, qty INT,
    email TEXT UNIQUE,
    PRIMARY KEY (order_id, product_id)
);
CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT);
"""


def test_report_with_sqlite_metadata(tmp_path: Path) -> None:
    path = make_db(tmp_path, ORDERS)
    table = inspect_sqlite_table(path, "order_items")
    report = build_table_report(table)
    assert report.relation_name == "order_items"
    assert report.attributes == table.column_names  # SQLite column order
    assert report.table_info == table
    assert [str(fd) for fd in report.declared_fds] == [
        "order_id, product_id -> email, product_name, qty"
    ]
    assert report.user_fds == ()
    md = render_markdown_report(report)
    for expected in (
        "### Columns", "### Unique constraints", "### Foreign keys",
        "**Primary key:** `(order_id, product_id)`",
        "SQLite table metadata", "declared SQLite key",
        "0 user-supplied; 1 derived from declared SQLite key constraints",
    ):
        assert expected in md
    data = json.loads(render_json_report(report))
    meta = data["table_metadata"]
    assert meta["primary_key"] == ["order_id", "product_id"]
    assert [c["name"] for c in meta["columns"]] == list(table.column_names)
    assert meta["unique_constraints"][0]["enforces_key"] is False
    assert data["functional_dependencies"]["user_supplied"] == []


def test_user_fds_combine_with_declared_keys(tmp_path: Path) -> None:
    path = make_db(tmp_path, ORDERS)
    table = inspect_sqlite_table(path, "order_items")
    report = build_table_report(table, fds_of("product_id -> product_name"))
    assert not report.normalization.satisfies_2nf
    md = render_markdown_report(report)
    assert "| user-supplied |" in md and "declared SQLite key" in md
    two = [v for v in report.violation_details if v.normal_form == "2NF"]
    assert two and two[0].attribute == "product_name"


def test_declared_only_report_warns_results_are_optimistic(tmp_path: Path) -> None:
    table = inspect_sqlite_table(make_db(tmp_path, ORDERS), "order_items")
    report = build_table_report(table)
    text = " ".join(report.limitations)
    assert "likely optimistic" in text
    assert "no FD is inferred from column names or row data" in text
    assert "Foreign keys relate different tables" in text
    # the nullable UNIQUE column was skipped, and the report says why
    assert "may hold NULL" in text and "UNIQUE (email)" in text


def test_user_fds_remove_optimism_warning(tmp_path: Path) -> None:
    table = inspect_sqlite_table(make_db(tmp_path, ORDERS), "order_items")
    report = build_table_report(table, fds_of("product_id -> product_name"))
    assert "likely optimistic" not in " ".join(report.limitations)


def test_table_without_primary_key(tmp_path: Path) -> None:
    table = inspect_sqlite_table(make_db(tmp_path, "CREATE TABLE t (a INT, b INT);"), "t")
    report = build_table_report(table)
    assert report.normalization.candidate_keys == sets("ab")
    assert "**Primary key:** none declared" in render_markdown_report(report)
    assert "none was invented" in " ".join(report.limitations)


def test_use_declared_keys_false(tmp_path: Path) -> None:
    table = inspect_sqlite_table(make_db(tmp_path, ORDERS), "products")
    report = build_table_report(table, use_declared_keys=False)
    assert report.declared_fds == () and report.normalization.candidate_keys == (frozenset({"id", "name"}),)


def test_without_sqlite_metadata_has_no_table_sections() -> None:
    md = render_markdown_report(report_of("AB", "A -> B"))
    assert "### Columns" not in md and "### Foreign keys" not in md
    assert "not available (no database metadata supplied)" in md
    assert "Foreign keys relate" not in " ".join(report_of("AB", "A -> B").limitations)


def test_table_info_consistency_checks(tmp_path: Path) -> None:
    table = inspect_sqlite_table(make_db(tmp_path, ORDERS), "products")
    with pytest.raises(ValueError, match="do not match"):
        build_analysis_report(["id", "other"], [], table_info=table)
    with pytest.raises(ValueError, match="does not match table_info"):
        build_analysis_report(None, [], table_name="other", table_info=table)
    ok = build_analysis_report(table.to_relation(), [], table_info=table)
    assert ok.relation_name == "products"


def test_end_to_end_database_to_reports(tmp_path: Path) -> None:
    path = make_db(tmp_path, ORDERS)
    table = inspect_sqlite_table(path, "order_items")
    report = build_table_report(table, [parse_fd("product_id -> product_name")])
    out_md = save_markdown_report(report, tmp_path / "r.md")
    out_json = save_json_report(report, tmp_path / "r.json")
    assert "## 4. Normalization Analysis" in out_md.read_text(encoding="utf-8")
    assert json.loads(out_json.read_text(encoding="utf-8"))["relation"]["name"] == "order_items"


# --- Saving ----------------------------------------------------------------

def test_save_writes_exact_rendered_content(tmp_path: Path) -> None:
    report = report_of("AB", "∅ -> A", "A -> B", table_name="R")
    md_path = save_markdown_report(report, tmp_path / "report.md")
    json_path = save_json_report(report, str(tmp_path / "report.json"))
    assert md_path.read_text(encoding="utf-8") == render_markdown_report(report)
    assert json_path.read_text(encoding="utf-8") == render_json_report(report)
    assert isinstance(md_path, Path)


def test_save_never_silently_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "keep.md"
    target.write_text("precious", encoding="utf-8")
    report = report_of("AB", "A -> B")
    with pytest.raises(ReportSaveError, match="overwrite"):
        save_markdown_report(report, target)
    with pytest.raises(ReportSaveError, match="overwrite"):
        save_json_report(report, target)
    assert target.read_text(encoding="utf-8") == "precious"
    save_markdown_report(report, target, overwrite=True)
    assert target.read_text(encoding="utf-8").startswith("# DBSchemaX")


def test_save_errors_are_reported(tmp_path: Path) -> None:
    report = report_of("AB", "A -> B")
    with pytest.raises(ReportSaveError, match="cannot write"):
        save_markdown_report(report, tmp_path / "no_such_dir" / "r.md")
    with pytest.raises(ReportSaveError, match="cannot write"):
        save_json_report(report, tmp_path, overwrite=True)  # a directory
    assert not (tmp_path / "no_such_dir").exists()  # parents are not created


def test_save_rejects_invalid_path_type(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        save_markdown_report(report_of("A"), 123)  # type: ignore[arg-type]
