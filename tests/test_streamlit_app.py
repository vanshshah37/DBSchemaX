"""Headless tests of the Streamlit dashboard (Streamlit's AppTest, no browser).

Skipped automatically when Streamlit is not installed, so the core test suite
does not require it.
"""

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

import dbschemax.ui_support as ui_support  # noqa: E402
from dbschemax.streamlit_app import ATOMIC_ALL, ATOMIC_SOME, PAGES  # noqa: E402
from dbschemax.ui_support import DEMO_CASE, EXAMPLE_CASES, run_analysis  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "src" / "dbschemax" / "streamlit_app.py")


def new_app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=60).run()


def go(at: AppTest, page: str) -> AppTest:
    return at.sidebar.radio(key="page").set_value(page).run()


def button(at: AppTest, label: str):
    return next(b for b in at.button if b.label == label)


def assert_clean(at: AppTest) -> None:
    assert not at.exception, [e.value for e in at.exception]


def texts(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [e.value for e in at.error] + [
        s.value for s in at.success] + [i.value for i in at.info] + [c.value for c in at.caption]
    return "\n".join(parts)


# --- Import / configuration --------------------------------------------------

def test_module_imports_without_running_the_ui() -> None:
    import dbschemax.streamlit_app as app

    assert app.PAGES == (
        "Overview", "Schema Input", "Dependency Explorer", "Candidate Keys",
        "Minimal Cover", "Normalization", "3NF Synthesis", "Reports",
    )
    assert callable(app.main)


# --- Default demonstration -----------------------------------------------------

def test_default_state_is_the_labelled_demo() -> None:
    at = new_app()
    assert_clean(at)
    spec = at.session_state["spec"]
    assert spec.source == "demo" and spec.label.startswith("Sample data")
    assert [t.value for t in at.title][:1] == ["Mathematics for Computer Science in database design"]
    assert "MFCS Concepts in Action" in [h.value for h in at.header]
    assert "Sample data" in texts(at)  # visible provenance in sidebar / overview


def test_overview_shows_real_results_for_demo() -> None:
    at = new_app()
    body = texts(at)
    assert "{CourseID, StudentID}" in body  # the composite candidate key (sorted)
    assert "R1(" in body and "R3(" in body  # final schemas from the real synthesis
    assert len(at.expander) == 8  # eight MFCS concepts


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_for_demo(page: str) -> None:
    at = go(new_app(), page)
    assert_clean(at)


@pytest.mark.parametrize("case", EXAMPLE_CASES, ids=lambda c: c.key)
def test_every_page_renders_for_every_example(case) -> None:
    at = go(new_app(), "Schema Input")
    at.selectbox(key="example_choice").set_value(case.title).run()
    button(at, "Load this example").click().run()
    assert_clean(at)
    assert at.session_state["spec"].attributes == case.attributes
    for page in PAGES:
        at = go(at, page)
        assert_clean(at)


def test_demo_numbers_match_the_engine() -> None:
    at = go(new_app(), "Candidate Keys")
    report = run_analysis(at.session_state["spec"])
    assert report.normalization.candidate_keys == (frozenset({"StudentID", "CourseID"}),)
    assert "{CourseID, StudentID}" in texts(at)
    at = go(at, "3NF Synthesis")
    body = texts(at)
    for schema in report.synthesized_schemas:
        assert ", ".join(sorted(schema)) in body


# --- Manual analysis -----------------------------------------------------------------

def submit_manual(at: AppTest, name: str, attributes: str, fds: str) -> AppTest:
    at = go(at, "Schema Input")
    at.text_input(key="manual_name").set_value(name)
    at.text_input(key="manual_attrs").set_value(attributes)
    at.text_area(key="manual_fds").set_value(fds)
    return at.button(key="manual_run").click().run()


def test_manual_analysis_calls_the_real_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []
    real = ui_support.build_analysis_report

    def spy(attributes, fds, *args, **kwargs):
        calls.append((tuple(attributes), tuple(str(f) for f in fds)))
        return real(attributes, fds, *args, **kwargs)  # the real engine still runs

    monkeypatch.setattr(ui_support, "build_analysis_report", spy)
    at = submit_manual(new_app(), "R", "A, B, C", "A -> B\nB -> C")
    assert_clean(at)
    assert calls and calls[-1] == (("A", "B", "C"), ("A -> B", "B -> C"))
    spec = at.session_state["spec"]
    assert spec.source == "manual" and spec.name == "R" and spec.attributes == ("A", "B", "C")
    assert any("Your data" in m.value for m in at.sidebar.markdown)  # sidebar updated at once
    assert any("Analysis updated" in s.value for s in at.success)
    report = run_analysis(spec)
    assert report.normalization.candidate_keys == (frozenset({"A"}),)
    assert not report.normalization.satisfies_3nf


def test_manual_analysis_pages_show_new_results() -> None:
    at = submit_manual(new_app(), "R", "A, B, C", "A -> B\nB -> C")
    at = go(at, "Normalization")
    assert_clean(at)
    assert any("B -> C" in e.value or "{B} -> C" in e.value for e in at.error)
    at = go(at, "3NF Synthesis")
    assert "R1(A, B)" in texts(at) and "R2(B, C)" in texts(at)
    at = go(at, "Minimal Cover")
    assert_clean(at)


@pytest.mark.parametrize(
    "attributes, fds, expected",
    [
        ("A, B, C", "∅ -> A\nA -> B", ("C",)),  # empty LHS
        ("A, B, C", "A -> B\nB -> A\nA -> C", ("A",)),  # multiple candidate keys
        ("A, B, C", "A, B -> C", ("A", "B")),  # composite key
        ("A, B", "", ("A", "B")),  # incomplete / empty FD set
    ],
)
def test_manual_edge_cases_compute_correctly(attributes: str, fds: str, expected: tuple) -> None:
    at = submit_manual(new_app(), "R", attributes, fds)
    assert_clean(at)
    report = run_analysis(at.session_state["spec"])
    assert frozenset(expected) in report.normalization.candidate_keys
    for page in PAGES:
        assert_clean(go(at, page))


def test_invalid_fd_is_reported_without_crashing_or_changing_state() -> None:
    at = submit_manual(new_app(), "R", "A, B", "A B")
    assert_clean(at)
    assert any("Invalid input" in e.value and "line 1" in e.value for e in at.error)
    assert at.session_state["spec"].source == "demo"


def test_fd_outside_relation_and_bad_attributes_are_reported() -> None:
    at = submit_manual(new_app(), "R", "A, B", "A -> Z")
    assert any("Cannot analyze" in e.value and "outside the relation" in e.value for e in at.error)
    at = submit_manual(new_app(), "R", "A,,B", "")
    assert any("empty attribute" in e.value for e in at.error)
    assert at.session_state["spec"].source == "demo"


def test_too_many_attributes_rejected_cleanly() -> None:
    names = ", ".join(f"A{i}" for i in range(20))
    at = submit_manual(new_app(), "R", names, "")
    assert_clean(at)
    assert any("limited" in e.value for e in at.error)
    assert at.session_state["spec"].source == "demo"


def test_1nf_stays_unknown_unless_atomicity_supplied() -> None:
    at = submit_manual(new_app(), "R", "A, B", "A -> B")
    assert run_analysis(at.session_state["spec"]).normalization.first_nf is None
    at = go(at, "Normalization")
    assert any("Unknown" in str(row) for df in at.dataframe for row in df.value.values.tolist())
    at = go(at, "Schema Input")
    at.radio(key="manual_atomic_mode").set_value(ATOMIC_ALL).run()
    at.button(key="manual_run").click().run()
    assert at.session_state["spec"].non_atomic == ()
    at = go(at, "Schema Input")
    at.radio(key="manual_atomic_mode").set_value(ATOMIC_SOME).run()
    at.multiselect(key="manual_non_atomic").set_value(["B"]).run()
    at.button(key="manual_run").click().run()
    assert at.session_state["spec"].non_atomic == ("B",)
    assert_clean(go(at, "Normalization"))


def test_reset_returns_to_the_demo() -> None:
    at = submit_manual(new_app(), "R", "A, B", "A -> B")
    assert at.session_state["spec"].source == "manual"
    button(at, "Reset to Student–Course demo").click().run()
    assert_clean(at)
    spec = at.session_state["spec"]
    assert spec.source == "demo" and spec.attributes == DEMO_CASE.attributes
    assert at.text_input(key="manual_attrs").value == ", ".join(DEMO_CASE.attributes)


# --- Dependency explorer -----------------------------------------------------------------

def test_closure_walkthrough_matches_engine() -> None:
    at = go(new_app(), "Dependency Explorer")
    at.multiselect(key="explorer_x").set_value(["StudentID"]).run()
    assert_clean(at)
    body = texts(at)
    assert "X⁺ = `{StudentID, StudentName}`" in body
    assert "not** a superkey" in body.replace("*", "**", 0) or any("not" in e.value for e in at.error)
    at.multiselect(key="explorer_x").set_value(["StudentID", "CourseID"]).run()
    assert any("superkey" in s.value and "candidate key" in s.value for s in at.success)


# --- SQLite workflow -------------------------------------------------------------------------

def make_db(tmp_path: Path) -> Path:
    path = tmp_path / "shop.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE order_items (order_id INT NOT NULL, product_id INT NOT NULL,
            product_name TEXT, qty INT, PRIMARY KEY (order_id, product_id));
        CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT);
        """
    )
    connection.commit()
    connection.close()
    return path


def test_sqlite_local_path_workflow(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    before = db.read_bytes()
    at = go(new_app(), "Schema Input")
    at.radio(key="sql_kind").set_value("Local path").run()
    at.text_input(key="sql_path").set_value(str(db)).run()
    assert_clean(at)
    assert at.selectbox(key="sql_table").options == ["order_items", "products"]
    at.selectbox(key="sql_table").set_value("order_items").run()
    at.text_area(key="sql_fds").set_value("product_id -> product_name").run()
    at.button(key="sql_run").click().run()
    assert_clean(at)
    spec = at.session_state["spec"]
    assert spec.source == "sqlite" and spec.table_info.name == "order_items"
    report = run_analysis(spec)
    assert not report.normalization.satisfies_2nf  # the user-supplied FD exposes a partial dependency
    assert [str(f) for f in report.user_fds] == ["product_id -> product_name"]
    assert report.declared_fds  # derived from the declared composite primary key
    for page in PAGES:
        assert_clean(go(at, page))
    assert db.read_bytes() == before  # never modified


def test_sqlite_missing_file_and_no_tables(tmp_path: Path) -> None:
    at = go(new_app(), "Schema Input")
    at.radio(key="sql_kind").set_value("Local path").run()
    at.text_input(key="sql_path").set_value(str(tmp_path / "nope.db")).run()
    assert_clean(at)
    assert any("not found" in e.value for e in at.error)
    empty = tmp_path / "empty.db"
    empty.touch()
    at.text_input(key="sql_path").set_value(str(empty)).run()
    assert any("no user tables" in i.value for i in at.info)


def test_sqlite_without_declared_key_fds(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    at = go(new_app(), "Schema Input")
    at.radio(key="sql_kind").set_value("Local path").run()
    at.text_input(key="sql_path").set_value(str(db)).run()
    at.selectbox(key="sql_table").set_value("products").run()
    at.checkbox(key="sql_derive").uncheck().run()
    at.button(key="sql_run").click().run()
    spec = at.session_state["spec"]
    assert spec.use_declared_keys is False
    assert run_analysis(spec).declared_fds == ()


# --- Reports -------------------------------------------------------------------------------------

def test_reports_page_offers_both_downloads() -> None:
    at = go(new_app(), "Reports")
    assert_clean(at)
    downloads = at.get("download_button")
    assert len(downloads) == 2
    assert any("# DBSchemaX Analysis Report" in m.value for m in at.markdown)
    assert any("Limitations" in s.value for s in at.subheader)


# --- Navigation keeps typed input ----------------------------------------------------------------

def test_form_input_survives_page_navigation() -> None:
    at = go(new_app(), "Schema Input")
    at.text_input(key="manual_attrs").set_value("X, Y, Z")
    at.text_area(key="manual_fds").set_value("X -> Y")
    at.run()
    at = go(at, "Overview")
    at = go(at, "Candidate Keys")
    at = go(at, "Schema Input")
    assert_clean(at)
    assert at.text_input(key="manual_attrs").value == "X, Y, Z"
    assert at.text_area(key="manual_fds").value == "X -> Y"
    assert at.session_state["spec"].source == "demo"  # typing alone does not change the analysis


def test_loading_an_example_fills_the_manual_form() -> None:
    at = go(new_app(), "Schema Input")
    case = EXAMPLE_CASES[3]  # empty-LHS example
    at.selectbox(key="example_choice").set_value(case.title).run()
    button(at, "Load this example").click().run()
    assert at.text_input(key="manual_attrs").value == ", ".join(case.attributes)
    assert "∅ -> A" in at.text_area(key="manual_fds").value
