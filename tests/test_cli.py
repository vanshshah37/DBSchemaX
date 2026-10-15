"""Tests for the command-line interface (in-process and as real subprocesses)."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import dbschemax.cli as cli
from dbschemax.cli import main

SRC = str(Path(__file__).resolve().parents[1] / "src")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=SRC, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "dbschemax", *args],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=60,
    )


def invoke(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def make_db(tmp_path: Path, script: str, name: str = "school.db") -> Path:
    path = tmp_path / name
    connection = sqlite3.connect(path)
    try:
        connection.executescript(script)
        connection.commit()
    finally:
        connection.close()
    return path


SCHOOL = """
CREATE TABLE Students (StudentID INTEGER PRIMARY KEY, Name TEXT NOT NULL, Email TEXT UNIQUE);
CREATE TABLE Enrollments (StudentID INT NOT NULL, CourseID INT NOT NULL, Grade TEXT,
                          PRIMARY KEY (StudentID, CourseID));
INSERT INTO Students VALUES (1, 'Ann', NULL), (2, 'Bob', NULL);
"""


@pytest.fixture()
def school(tmp_path: Path) -> Path:
    return make_db(tmp_path, SCHOOL)


# --- help / version --------------------------------------------------------

def test_top_level_help(capsys) -> None:
    code, out, _ = invoke(capsys, "--help")
    assert code == 0
    for word in ("analyze", "inspect", "tables", "FD syntax"):
        assert word in out


@pytest.mark.parametrize(
    "command, options",
    [
        ("analyze", ["--attributes", "--fd", "--name", "--non-atomic-attribute",
                     "--all-atomic", "--format", "--output", "--overwrite"]),
        ("inspect", ["--database", "--table", "--fd", "--no-derive-key-fds",
                     "--non-atomic-attribute", "--format", "--output", "--overwrite"]),
        ("tables", ["--database"]),
    ],
)
def test_subcommand_help(capsys, command: str, options: list[str]) -> None:
    code, out, _ = invoke(capsys, command, "--help")
    assert code == 0
    for option in options:
        assert option in out


def test_version(capsys) -> None:
    code, out, _ = invoke(capsys, "--version")
    assert code == 0 and out.startswith("dbschemax ")


def test_no_command_is_usage_error(capsys) -> None:
    code, _, err = invoke(capsys)
    assert code == 2 and "usage:" in err


# --- analyze ---------------------------------------------------------------

def test_analyze_markdown_to_stdout(capsys) -> None:
    code, out, err = invoke(
        capsys, "analyze", "--attributes", "A,B,C", "--fd", "A -> B", "--fd", "B -> C",
        "--name", "R",
    )
    assert code == 0 and err == ""
    assert out.startswith("# DBSchemaX Analysis Report: `R`")
    assert "{B} -> C violates 3NF" in out and "**Unknown**" in out


def test_analyze_json_to_stdout(capsys) -> None:
    code, out, _ = invoke(
        capsys, "analyze", "--attributes", "A, B, C", "--fd", "A -> B,C", "--format", "json",
    )
    data = json.loads(out)
    assert code == 0
    assert data["candidate_keys"]["keys"] == [["A"]]
    assert data["normalization"]["bcnf"]["satisfied"] is True
    assert data["relation"]["name"] is None


def test_analyze_multiple_fds_and_empty_lhs(capsys) -> None:
    code, out, _ = invoke(
        capsys, "analyze", "--attributes", "A,B,C", "--fd", "∅ -> A", "--fd", "A -> B",
        "--format", "json",
    )
    data = json.loads(out)
    assert code == 0
    assert data["candidate_keys"]["keys"] == [["C"]]
    assert len(data["functional_dependencies"]["user_supplied"]) == 2


def test_analyze_without_fds(capsys) -> None:
    code, out, _ = invoke(capsys, "analyze", "--attributes", "A,B", "--format", "json")
    assert code == 0 and json.loads(out)["candidate_keys"]["keys"] == [["A", "B"]]


def test_atomicity_options(capsys) -> None:
    base = ["analyze", "--attributes", "A,B", "--fd", "A -> B", "--format", "json"]
    status = lambda extra: json.loads(invoke(capsys, *base, *extra)[1])["normalization"][  # noqa: E731
        "first_nf"]["status"]
    assert status([]) == "unknown"  # never assumed from FDs
    assert status(["--all-atomic"]) == "satisfied"
    assert status(["--non-atomic-attribute", "B"]) == "violated"
    assert status(["--non-atomic-attribute", "A", "--non-atomic-attribute", "B"]) == "violated"


def test_conflicting_atomicity_options(capsys) -> None:
    code, _, err = invoke(
        capsys, "analyze", "--attributes", "A,B", "--all-atomic", "--non-atomic-attribute", "A",
    )
    assert code == 2 and "not allowed with" in err


def test_non_atomic_attribute_outside_relation(capsys) -> None:
    code, out, err = invoke(
        capsys, "analyze", "--attributes", "A,B", "--non-atomic-attribute", "Z",
    )
    assert code == 2 and out == "" and "outside the relation" in err


# --- output and saving -----------------------------------------------------

def test_output_file_matches_stdout(capsys, tmp_path: Path) -> None:
    args = ["analyze", "--attributes", "A,B,C", "--fd", "∅ -> A", "--fd", "A -> B", "--name", "R"]
    _, stdout_text, _ = invoke(capsys, *args)
    target = tmp_path / "report.md"
    code, out, err = invoke(capsys, *args, "--output", str(target))
    assert code == 0 and out == "" and "report written" in err
    assert target.read_text(encoding="utf-8") == stdout_text
    assert "∅" in target.read_text(encoding="utf-8")


def test_format_inferred_from_extension(capsys, tmp_path: Path) -> None:
    for name, check in (("a.json", json.loads), ("b.md", lambda t: t.startswith("# DBSchemaX")),
                        ("c.MARKDOWN", lambda t: t.startswith("# DBSchemaX"))):
        code, _, _ = invoke(capsys, "analyze", "--attributes", "A", "-o", str(tmp_path / name))
        assert code == 0
        assert check((tmp_path / name).read_text(encoding="utf-8"))


def test_explicit_format_overrides_extension(capsys, tmp_path: Path) -> None:
    target = tmp_path / "data.txt"
    code, _, _ = invoke(capsys, "analyze", "--attributes", "A", "--format", "json",
                        "--output", str(target))
    assert code == 0 and json.loads(target.read_text(encoding="utf-8"))["schema_version"] == 1


def test_unknown_extension_without_format_is_rejected(capsys, tmp_path: Path) -> None:
    code, _, err = invoke(capsys, "analyze", "--attributes", "A", "--output", str(tmp_path / "r.txt"))
    assert code == 2 and "cannot infer the format" in err
    assert not (tmp_path / "r.txt").exists()


def test_existing_output_is_never_overwritten(capsys, tmp_path: Path) -> None:
    target = tmp_path / "keep.md"
    target.write_text("precious", encoding="utf-8")
    code, out, err = invoke(capsys, "analyze", "--attributes", "A", "-o", str(target))
    assert code == 1 and out == "" and "--overwrite" in err and "overwrite=True" not in err
    assert target.read_text(encoding="utf-8") == "precious"
    code, _, _ = invoke(capsys, "analyze", "--attributes", "A", "-o", str(target), "--overwrite")
    assert code == 0 and target.read_text(encoding="utf-8").startswith("# DBSchemaX")


def test_overwrite_requires_output(capsys) -> None:
    code, _, err = invoke(capsys, "analyze", "--attributes", "A", "--overwrite")
    assert code == 2 and "--overwrite requires --output" in err


def test_output_in_missing_directory(capsys, tmp_path: Path) -> None:
    code, _, err = invoke(capsys, "analyze", "--attributes", "A", "-o", str(tmp_path / "no" / "r.md"))
    assert code == 1 and "cannot write" in err and "Traceback" not in err


# --- invalid inputs --------------------------------------------------------

@pytest.mark.parametrize(
    "extra, fragment",
    [
        (["--fd", "A B"], "invalid --fd 'A B'"),
        (["--fd", "A -> B -> C"], "multiple arrows"),
        (["--fd", "A ->"], "right-hand side"),
        (["--fd", "A -> Z"], "outside the relation"),
    ],
)
def test_invalid_fds(capsys, extra: list[str], fragment: str) -> None:
    code, out, err = invoke(capsys, "analyze", "--attributes", "A,B", *extra)
    assert code == 2 and out == ""
    assert fragment in err and err.startswith("error:") and "Traceback" not in err


@pytest.mark.parametrize(
    "attributes, fragment",
    [("A,,B", "empty attribute"), ("A B", "whitespace"), ("A,B,A", "duplicate"),
     ("", "no attributes"), ("∅,A", "not a valid")],
)
def test_invalid_attributes(capsys, attributes: str, fragment: str) -> None:
    code, _, err = invoke(capsys, "analyze", "--attributes", attributes)
    assert code == 2 and fragment in err and "Traceback" not in err


def test_missing_required_arguments(capsys) -> None:
    for argv in (["analyze"], ["inspect", "--database", "x.db"], ["inspect", "--table", "t"],
                 ["tables"]):
        code, out, err = invoke(capsys, *argv)
        assert code == 2 and out == "" and "required" in err


def test_unknown_option_and_bad_format(capsys) -> None:
    assert invoke(capsys, "analyze", "--attributes", "A", "--bogus")[0] == 2
    assert invoke(capsys, "analyze", "--attributes", "A", "--format", "pdf")[0] == 2


def test_unexpected_errors_are_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("programming error")

    monkeypatch.setattr(cli, "build_analysis_report", boom)
    with pytest.raises(RuntimeError, match="programming error"):
        main(["analyze", "--attributes", "A"])


# --- inspect ---------------------------------------------------------------

def test_inspect_markdown(capsys, school: Path) -> None:
    code, out, _ = invoke(capsys, "inspect", "--database", str(school), "--table", "Students",
                          "--fd", "StudentID -> Name")
    assert code == 0
    assert "# DBSchemaX Analysis Report: `Students`" in out
    assert "### Columns" in out and "declared SQLite key" in out and "user-supplied" in out


def test_inspect_json_composite_key(capsys, school: Path) -> None:
    code, out, _ = invoke(capsys, "inspect", "--database", str(school), "--table", "Enrollments",
                          "--format", "json")
    data = json.loads(out)
    assert code == 0
    assert data["table_metadata"]["primary_key"] == ["StudentID", "CourseID"]
    assert data["candidate_keys"]["keys"] == [["CourseID", "StudentID"]]
    assert data["functional_dependencies"]["derived_from_declared_keys"]


def test_inspect_can_disable_declared_key_fds(capsys, school: Path) -> None:
    args = ["inspect", "--database", str(school), "--table", "Students", "--format", "json"]
    with_keys = json.loads(invoke(capsys, *args)[1])
    without = json.loads(invoke(capsys, *args, "--no-derive-key-fds")[1])
    assert with_keys["functional_dependencies"]["derived_from_declared_keys"]
    assert without["functional_dependencies"]["derived_from_declared_keys"] == []
    assert without["candidate_keys"]["keys"] == [["Email", "Name", "StudentID"]]


def test_inspect_saves_report_and_never_modifies_database(capsys, school: Path, tmp_path: Path) -> None:
    before = school.read_bytes()
    entries = sorted(p.name for p in tmp_path.iterdir())
    target = tmp_path / "students.json"
    code, _, _ = invoke(capsys, "inspect", "--database", str(school), "--table", "students",
                        "--output", str(target))
    assert code == 0 and json.loads(target.read_text(encoding="utf-8"))["relation"]["name"] == "Students"
    assert school.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([*entries, "students.json"])


def test_inspect_errors(capsys, school: Path, tmp_path: Path) -> None:
    missing = tmp_path / "nope.db"
    code, out, err = invoke(capsys, "inspect", "--database", str(missing), "--table", "t")
    assert code == 1 and out == "" and "not found" in err and not missing.exists()
    code, _, err = invoke(capsys, "inspect", "--database", str(school), "--table", "Ghost")
    assert code == 1 and "'Ghost'" in err and "Students" in err and "Traceback" not in err
    code, _, err = invoke(capsys, "inspect", "--database", str(school), "--table", "sqlite_master")
    assert code == 1 and "internal" in err
    fake = tmp_path / "fake.db"
    fake.write_bytes(b"this is not a database at all " * 10)
    code, _, err = invoke(capsys, "inspect", "--database", str(fake), "--table", "t")
    assert code == 1 and "not a database" in err
    code, _, err = invoke(capsys, "inspect", "--database", str(tmp_path), "--table", "t")
    assert code == 1 and "not a file" in err


def test_inspect_invalid_fd_and_fd_outside_table(capsys, school: Path) -> None:
    base = ["inspect", "--database", str(school), "--table", "Students"]
    assert invoke(capsys, *base, "--fd", "bad")[0] == 2
    code, _, err = invoke(capsys, *base, "--fd", "Nope -> Name")
    assert code == 2 and "outside the relation" in err


# --- tables ----------------------------------------------------------------

def test_tables_listing(capsys, school: Path) -> None:
    code, out, err = invoke(capsys, "tables", "--database", str(school))
    assert code == 0 and out.splitlines() == ["Enrollments", "Students"] and err == ""


def test_tables_empty_database(capsys, tmp_path: Path) -> None:
    empty = tmp_path / "empty.db"
    empty.touch()
    code, out, err = invoke(capsys, "tables", "--database", str(empty))
    assert code == 0 and out == "" and "no user tables" in err


def test_tables_errors(capsys, tmp_path: Path) -> None:
    code, _, err = invoke(capsys, "tables", "--database", str(tmp_path / "missing.db"))
    assert code == 1 and "not found" in err
    fake = tmp_path / "x.db"
    fake.write_text("hello world " * 30)
    assert invoke(capsys, "tables", "--database", str(fake))[0] == 1


# --- real subprocesses (python -m dbschemax) -----------------------------------

def test_module_help_subprocess() -> None:
    result = run_cli("--help")
    assert result.returncode == 0 and "analyze" in result.stdout and "inspect" in result.stdout


def test_module_analyze_subprocess() -> None:
    result = run_cli("analyze", "--attributes", "A,B,C", "--fd", "A -> B", "--fd", "B -> C",
                     "--format", "json")
    assert result.returncode == 0 and result.stderr == ""
    assert json.loads(result.stdout)["candidate_keys"]["keys"] == [["A"]]


def test_module_unicode_output_subprocess() -> None:
    result = run_cli("analyze", "--attributes", "A,B", "--fd", "∅ -> A")
    assert result.returncode == 0 and "∅ -> A" in result.stdout


def test_module_exit_codes_subprocess(tmp_path: Path) -> None:
    bad_fd = run_cli("analyze", "--attributes", "A", "--fd", "oops")
    assert bad_fd.returncode == 2 and "error:" in bad_fd.stderr and "Traceback" not in bad_fd.stderr
    no_db = run_cli("tables", "--database", str(tmp_path / "missing.db"))
    assert no_db.returncode == 1 and "not found" in no_db.stderr
    usage = run_cli("analyze")
    assert usage.returncode == 2 and "required" in usage.stderr


def test_module_inspect_and_tables_subprocess(school: Path, tmp_path: Path) -> None:
    before = school.read_bytes()
    listing = run_cli("tables", "--database", str(school))
    assert listing.returncode == 0 and listing.stdout.split() == ["Enrollments", "Students"]
    target = tmp_path / "out.md"
    result = run_cli("inspect", "--database", str(school), "--table", "Students",
                     "--fd", "StudentID -> Name", "-o", str(target))
    assert result.returncode == 0 and target.read_text(encoding="utf-8").startswith("# DBSchemaX")
    again = run_cli("inspect", "--database", str(school), "--table", "Students", "-o", str(target))
    assert again.returncode == 1 and "--overwrite" in again.stderr
    assert school.read_bytes() == before


def test_cli_does_not_import_streamlit() -> None:
    code = "import sys, dbschemax.cli, dbschemax.__main__; assert 'streamlit' not in sys.modules"
    env = dict(os.environ, PYTHONPATH=SRC)
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
