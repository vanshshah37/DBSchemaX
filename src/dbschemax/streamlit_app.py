"""DBSchemaX dashboard: Mathematics for Computer Science applied to schema design.

Launch (from the project root)::

    pip install -e ".[ui]"          # or: pip install streamlit
    streamlit run src/dbschemax/streamlit_app.py

This module is presentation only. Every number, set and step shown is produced
by the existing analysis engine (``reporting``) or by the verified trace helpers
in ``explain``; nothing is hard-coded as a result.
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:  # `streamlit run` from a source checkout without installing the package
    import dbschemax  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from dbschemax import reporting as reporting_module
from dbschemax.explain import (
    ClosureTrace,
    ReportExplanation,
    closure_trace,
    explain_report,
    key_evidence,
    subset_counts,
    superkey_check,
)
from dbschemax.inputs import InputError, parse_attribute_list, parse_fd_lines
from dbschemax import normalization_lab as lab
from dbschemax.demo_data import build_demo_dataset
from dbschemax.reporting import AnalysisReport, format_set
from dbschemax.sqlite_integration import (
    SQLiteInspectionError,
    TableInfo,
    inspect_sqlite_table,
    list_user_tables,
)
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

PAGES = (
    "Overview",
    "Schema Input",
    "Dependency Explorer",
    "Candidate Keys",
    "Minimal Cover",
    "Normalization",
    "3NF Synthesis",
    "Reports",
)
LAB_PAGE = "Normalization Lab"
NAV_PAGES = PAGES + (LAB_PAGE,)  # PAGES keeps the original eight analysis pages
ATOMIC_UNKNOWN = "Unknown (do not assess 1NF)"
ATOMIC_ALL = "All attributes are atomic"
ATOMIC_SOME = "Some attributes are non-atomic"
ATOMIC_MODES = (ATOMIC_UNKNOWN, ATOMIC_ALL, ATOMIC_SOME)
# Streamlit discards a widget's state when its page is not rendered. Re-assigning each
# key to itself at the start of every run turns the live widget value into ordinary
# session state, so typed input survives navigating between pages.
PERSISTED_KEYS = (
    "example_choice", "manual_name", "manual_attrs", "manual_fds", "manual_atomic_mode",
    "manual_non_atomic", "lab_filter", "lab_table", "sql_kind", "sql_path", "sql_table", "sql_fds", "sql_derive",
    "sql_atomic_mode", "sql_non_atomic", "explorer_x", "keys_view",
)
LEGEND = ":blue[Input] · :orange[Intermediate computation] · :red[Violation] · :green[Final result]"


# --- Small formatting helpers ----------------------------------------------

def fmt(attributes) -> str:
    """``{A, B}`` or ``∅``."""
    return format_set(attributes)


def code(text: str) -> str:
    return f"`{text}`"


def table(rows: list[dict[str, str]]) -> None:
    if rows:
        st.dataframe(rows, hide_index=True)


def yes_no(value: bool) -> str:
    return "✓ yes" if value else "✗ no"


def schema_text(index: int, attributes) -> str:
    return f"R{index}({', '.join(sorted(attributes))})"


# --- Session state -----------------------------------------------------------

def _persist_fields() -> None:
    ss = st.session_state
    for key in PERSISTED_KEYS:
        if key in ss:
            ss[key] = ss[key]


def _set_spec(spec: AnalysisSpec) -> None:
    st.session_state["spec"] = spec
    st.session_state.pop("explorer_x", None)


def _fill_manual_fields(
    name: str, attributes: tuple[str, ...], fds: tuple[str, ...]
) -> None:
    ss = st.session_state
    ss["manual_name"] = name
    ss["manual_attrs"] = ", ".join(attributes)
    ss["manual_fds"] = "\n".join(fds)
    ss["manual_atomic_mode"] = ATOMIC_UNKNOWN
    ss["manual_non_atomic"] = []


def _reset_demo() -> None:
    _set_spec(spec_from_example(DEMO_CASE))
    _fill_manual_fields(DEMO_CASE.name, DEMO_CASE.attributes, DEMO_CASE.fds)
    st.session_state["example_choice"] = DEMO_CASE.title


def _load_example() -> None:
    title = st.session_state.get("example_choice", DEMO_CASE.title)
    case = next(c for c in EXAMPLE_CASES if c.title == title)
    _set_spec(spec_from_example(case))
    _fill_manual_fields(case.name, case.attributes, case.fds)


def _ensure_state() -> None:
    ss = st.session_state
    _persist_fields()
    if "spec" not in ss:
        _reset_demo()
    ss.setdefault("page", PAGES[0])
    ss.setdefault("sql_derive", True)


def _current() -> tuple[AnalysisSpec, AnalysisReport | None, ReportExplanation | None, str | None]:
    spec = st.session_state["spec"]
    try:
        report = run_analysis(spec)
        return spec, report, explain_report(report), None
    except (ValueError, TypeError) as exc:  # invalid input: shown, never a crash
        return spec, None, None, str(exc)


# --- Sidebar -------------------------------------------------------------------

def _sidebar(spec: AnalysisSpec, report: AnalysisReport | None) -> None:
    with st.sidebar:
        st.title("DBSchemaX")
        st.caption("Mathematics for Computer Science applied to relational schema design")
        st.radio("Navigate", NAV_PAGES, key="page")
        st.divider()
        st.markdown("**Currently analyzing**")
        tag = ":orange[Sample data]" if spec.source in ("demo", "example") else ":blue[Your data]"
        st.markdown(f"{tag} — {spec.label}")
        if report is not None:
            st.markdown(
                f"Relation {code(report.relation_name or '(unnamed)')} · "
                f"{len(report.attributes)} attributes · {len(report.analysis_fds)} FDs"
            )
        st.button("Reset to Student–Course demo", on_click=_reset_demo)
        st.divider()
        st.caption(LEGEND)


# --- Reusable renderers ----------------------------------------------------------

def render_closure_trace(trace: ClosureTrace, report: AnalysisReport, fds) -> None:
    """Show the real fixed-point iteration for X and whether X is a superkey."""
    relation = frozenset(report.attributes)
    st.markdown(f":blue[**Start:**] X = {code(fmt(trace.start))}")
    if not trace.steps:
        st.info("No FD is applicable (no FD has its left side inside X), so X⁺ = X.")
    for step in trace.steps:
        with st.container(border=True):
            st.markdown(f":orange[**Pass {step.iteration}**] — current set {code(fmt(step.before))}")
            table(
                [
                    {
                        "FD applied": str(a.fd),
                        "Left side ⊆ current set": fmt(a.fd.lhs),
                        "New attributes": fmt(a.added),
                    }
                    for a in step.applied
                ]
            )
            st.markdown(f"Updated set: {code(fmt(step.after))}")
    st.markdown(
        f"Pass {len(trace.steps) + 1}: no FD adds a new attribute, so the **fixed point** is reached."
    )
    st.success(f"X⁺ = {code(fmt(trace.closure))}")
    check = superkey_check(trace.start, relation, fds)
    if check.is_superkey:
        evidence = key_evidence(relation, fds, trace.start)
        verdict = (
            "and no single attribute can be removed, so X is a **candidate key**."
            if evidence.is_minimal
            else "but X is not minimal, so it is a superkey that is not a candidate key."
        )
        st.success(f"X is a **superkey**: X⁺ = R. {verdict}")
    else:
        st.error(
            f"X is **not** a superkey: X⁺ ≠ R; the attributes {code(fmt(check.missing))} "
            "are not determined by X."
        )


def render_removal_table(evidence) -> None:
    table(
        [
            {
                "Attribute removed": item.removed,
                "Remaining set": fmt(item.remaining),
                "Closure of remaining set": fmt(item.closure),
                "Still a superkey?": yes_no(item.still_superkey),
            }
            for item in evidence.removals
        ]
    )


def render_predicate_table(exp: ReportExplanation) -> None:
    table(
        [
            {
                "Non-trivial FD X → A": str(row.fd),
                "X⁺": fmt(row.lhs_closure),
                "X superkey?": yes_no(row.lhs_is_superkey),
                "A prime?": yes_no(row.attribute_is_prime),
                "3NF: X superkey ∨ A prime": "✓ holds" if row.satisfies_3nf else "✗ violated",
                "BCNF: X superkey": "✓ holds" if row.satisfies_bcnf else "✗ violated",
            }
            for row in exp.predicates
        ]
    )


# --- Overview ---------------------------------------------------------------------

def _arrow() -> None:
    st.markdown("&emsp;&emsp;&emsp;**↓**")


def _stage(title: str, given: str, result: str, tone: str = "orange") -> None:
    with st.container(border=True):
        left, middle, right = st.columns([2, 4, 4])
        left.markdown(f":{tone}[**{title}**]")
        middle.markdown(f"**Input** — {given}")
        right.markdown(f"**Output** — {result}")


def _pipeline(report: AnalysisReport, exp: ReportExplanation) -> None:
    norm = report.normalization
    keys = norm.candidate_keys
    first_key = exp.key_closures[0]
    fds_text = "; ".join(str(fd) for fd in report.analysis_fds) or "no FDs"
    _stage(
        "1 · Relation + FDs",
        f"R = {code(fmt(report.attributes))}",
        f"F = {code(fds_text)}",
        "blue",
    )
    _arrow()
    _stage(
        "2 · Attribute closure",
        f"X = {code(fmt(first_key.start))}, F",
        f"X⁺ = {code(fmt(first_key.closure))}"
        + (" = R" if first_key.closure == exp.relation else ""),
    )
    _arrow()
    _stage(
        "3 · Candidate keys",
        f"closures of the {2 ** len(report.attributes)} subsets of R",
        ", ".join(code(fmt(k)) for k in keys),
    )
    _arrow()
    _stage(
        "4 · Minimal cover",
        f"{len(report.analysis_fds)} FDs",
        f"{len(report.minimal_cover)} FDs: "
        + (code("; ".join(str(fd) for fd in report.minimal_cover)) if report.minimal_cover else "∅"),
    )
    _arrow()
    _stage(
        "5 · Normalization",
        "keys, prime attributes, FDs",
        f"highest FD-based form: **{norm.highest_fd_normal_form}** · 1NF: "
        + ("unknown" if norm.first_nf is None else str(norm.first_nf)),
        "green" if norm.satisfies_3nf else "red",
    )
    _arrow()
    _stage(
        "6 · 3NF synthesis",
        "minimal cover + a candidate key",
        f"{len(report.synthesized_schemas)} schemas",
        "green",
    )
    _arrow()
    with st.container(border=True):
        st.markdown(":green[**7 · Final relation schemas**]")
        for index, schema in enumerate(report.synthesized_schemas, start=1):
            st.markdown(f"- {code(schema_text(index, schema))}")


def _concept(title: str, definition, role: str, result, expanded: bool = False) -> None:
    with st.expander(title, expanded=expanded):
        st.markdown("**Definition**")
        definition()
        st.markdown("**Role in DBSchemaX**")
        st.write(role)
        st.markdown(":green[**Result from the current analysis**]")
        result()


def _concepts(report: AnalysisReport, exp: ReportExplanation) -> None:
    relation = exp.relation
    n = len(relation)
    keys = report.normalization.candidate_keys
    key = keys[0]
    syn = exp.synthesis

    def set_result() -> None:
        st.markdown(f"R = {code(fmt(report.attributes))}  (a set of {n} attributes)")
        table(
            [
                {
                    "Determinant X": fmt(fd.lhs),
                    "Dependent Y": fmt(fd.rhs),
                    "X ∪ Y": fmt(fd.attributes),
                    "X ∪ Y ⊆ R": yes_no(fd.attributes <= relation),
                }
                for fd in report.analysis_fds
            ]
        )
        if syn.groups:
            g = syn.groups[0]
            rhs = frozenset().union(*(f.rhs for f in g.fds))
            st.markdown(f"Union in action: {code(fmt(g.lhs))} ∪ {code(fmt(rhs))} = {code(fmt(g.schema))}")
        container = next((s for s in syn.schemas if key <= s), None)
        if container is not None:
            st.markdown(f"Inclusion: key {code(fmt(key))} ⊆ schema {code(fmt(container))}")
        proper = [row for row in exp.search_rows if row.subset < key]
        st.markdown(f"Proper subsets of the key {code(fmt(key))} (⊊), with their closures:")
        table(
            [
                {"Proper subset S ⊊ K": fmt(r.subset), "S⁺": fmt(r.closure),
                 "S⁺ = R?": yes_no(r.is_superkey)}
                for r in proper
            ]
        )

    def implication_result() -> None:
        st.markdown(
            f"Because {code(fmt(key))}⁺ = {code(fmt(exp.key_closures[0].closure))} = R, "
            f"the FD {code(f'{fmt(key)} → {fmt(relation)}')} is **implied** by F "
            "even if it was never listed."
        )
        given = {fd for fd in report.analysis_fds}
        derived_new = [
            e for e in exp.cover_equivalence.first_implies_second
            if e.fd not in given
        ]
        st.markdown(
            f"Every FD of the minimal cover is implied by F: "
            f"{yes_no(all(e.implied for e in exp.cover_equivalence.first_implies_second))}"
            + (f" ({len(derived_new)} cover FD(s) were not in the input verbatim)." if derived_new else ".")
        )

    def closure_result() -> None:
        st.markdown(f"Closure of the first candidate key X = {code(fmt(key))}:")
        render_closure_trace(exp.key_closures[0], report, exp.fds)

    def search_result() -> None:
        st.markdown(f"|R| = {n} ⇒ {code(str(2 ** n))} subsets; superkeys and candidate keys found by size:")
        table(
            [
                {"Subset size": str(s), "Subsets": str(t), "Superkeys": str(sk), "Candidate keys": str(ck)}
                for s, t, sk, ck in subset_counts(exp.search_rows)
            ]
        )

    def minimality_result() -> None:
        for evidence in exp.key_evidence:
            st.markdown(
                f"Key {code(fmt(evidence.key))}: K⁺ = R ✓ — minimal: {yes_no(evidence.is_minimal)}"
            )
            render_removal_table(evidence)

    def cover_result() -> None:
        trace = exp.cover_trace
        st.markdown(
            f"{len(trace.original)} input FD(s) → {len(trace.after_split)} after splitting RHS → "
            f"{len(trace.after_lhs_reduction)} after removing extraneous LHS attributes → "
            f"{len(trace.cover)} in the minimal cover."
        )
        st.markdown(
            f"Extraneous attributes removed: {len(trace.extraneous_steps)} · "
            f"redundant FDs removed: {len(trace.redundant_steps)} · "
            f"F ≡ cover (mutual implication verified): {yes_no(exp.cover_equivalence.equivalent)}"
        )

    def predicate_result() -> None:
        st.markdown(f"Prime attributes: {code(fmt(report.normalization.prime_attributes))}")
        if exp.predicates:
            render_predicate_table(exp)
        else:
            st.info("There are no non-trivial FDs, so both predicates hold vacuously.")

    def complexity_result() -> None:
        st.markdown(
            f"Here n = |R| = {n}, so the search space has 2^{n} = {code(str(2 ** n))} subsets. "
            f"The dashboard limits exhaustive search to n ≤ {MAX_UI_ATTRIBUTES}."
        )
        sizes = sorted({n, 5, 10, 20, 30, 40})
        table([{"n = |R|": str(k), "Subsets 2ⁿ": f"{2 ** k:,}"} for k in sizes])

    _concept(
        "1 · Set theory",
        lambda: st.latex(r"R=\{A_1,\dots,A_n\},\quad X\cup Y,\quad X\subseteq Y,\quad X\subsetneq Y"),
        "Attributes, determinants and dependents are sets; unions build schemas, inclusion tests "
        "decide applicability of an FD, and proper subsets define minimality.",
        set_result,
        expanded=True,
    )
    _concept(
        "2 · Functional dependencies and logical implication",
        lambda: (
            st.latex(r"X\rightarrow Y\iff\forall t_1,t_2:\ t_1[X]=t_2[X]\Rightarrow t_1[Y]=t_2[Y]"),
            st.latex(r"F\models X\rightarrow Y\iff Y\subseteq X^{+}_F"),
        ),
        "An FD constrains which tuples may coexist. Implication lets DBSchemaX decide whether an FD "
        "follows from others using closure (Armstrong's axioms: reflexivity, augmentation, transitivity).",
        implication_result,
    )
    _concept(
        "3 · Closure and fixed-point computation",
        lambda: (
            st.latex(r"X^{(0)}=X,\qquad X^{(i+1)}=X^{(i)}\cup\bigcup\{Y:(V\rightarrow Y)\in F,\ V\subseteq X^{(i)}\}"),
            st.latex(r"X^{+}=X^{(k)}\ \text{ where }\ X^{(k+1)}=X^{(k)}"),
        ),
        "X⁺ is the smallest set containing X that is closed under F. It answers 'what does X determine?' "
        "and is the building block for keys, covers and normal-form tests.",
        closure_result,
    )
    _concept(
        "4 · Combinatorial search",
        lambda: st.latex(r"|\mathcal{P}(R)|=2^{n}\quad\text{subsets of an } n\text{-attribute relation}"),
        "Candidate keys are found by examining subsets of R by increasing size, computing each closure, "
        "and skipping supersets of keys already found.",
        search_result,
    )
    _concept(
        "5 · Minimality",
        lambda: st.latex(r"K\ \text{is a candidate key}\iff K^{+}=R\ \wedge\ \forall A\in K:\ (K\setminus\{A\})^{+}\neq R"),
        "A superkey may contain redundant attributes. Candidate keys are exactly the minimal superkeys; "
        "checking single-attribute removals suffices because superkeys are upward closed.",
        minimality_result,
    )
    _concept(
        "6 · Equivalence and minimization",
        lambda: st.latex(r"F\equiv G\iff F\models G\ \wedge\ G\models F"),
        "A minimal cover is a smaller, equivalent FD set: split right sides, drop extraneous left-side "
        "attributes, drop redundant FDs. It is the input of 3NF synthesis. (It is not unique.)",
        cover_result,
    )
    _concept(
        "7 · Logical predicates for normalization",
        lambda: (
            st.latex(r"\text{2NF: }\nexists\,S\subsetneq K\ (K\text{ a candidate key}),\ A\text{ non-prime}:\ A\in S^{+}"),
            st.latex(r"\text{3NF: }\forall\,X\rightarrow A\text{ non-trivial}:\ X\text{ is a superkey}\ \vee\ A\text{ is prime}"),
            st.latex(r"\text{BCNF: }\forall\,X\rightarrow A\text{ non-trivial}:\ X\text{ is a superkey}"),
        ),
        "Normal forms are universally quantified predicates over the FDs. Prime attributes (members of "
        "some candidate key) are what separates 3NF from BCNF.",
        predicate_result,
    )
    _concept(
        "8 · Algorithmic complexity",
        lambda: st.latex(r"\text{subsets examined in the worst case: }2^{n}"),
        "Exhaustive key search is exponential in the number of attributes (and a relation can even have "
        "exponentially many keys), which is acceptable for classroom-sized schemas but not for very wide tables. "
        "Each closure is cheap: at most n+1 passes over F.",
        complexity_result,
    )


def page_overview(spec, report, exp) -> None:
    st.title("Mathematics for Computer Science in database design")
    st.caption(LEGEND)
    problem, solution = st.columns(2)
    with problem:
        with st.container(border=True):
            st.markdown("### The problem")
            st.write(
                "Functional dependencies and candidate keys are hard to analyze by hand. Incorrect or "
                "incomplete analysis makes normalization and schema decomposition difficult to justify."
            )
    with solution:
        with st.container(border=True):
            st.markdown("### The solution")
            st.write(
                "DBSchemaX applies set-based reasoning, closure computation, exhaustive candidate-key "
                "search, FD equivalence, normalization rules and 3NF synthesis to analyze a relational schema."
            )
    if spec.source in ("demo", "example"):
        st.info(
            f"**Sample data — {spec.label.split(': ', 1)[-1]}.** Every value below is computed live "
            "by the real analysis functions. Use **Schema Input** to analyze your own relation or SQLite table."
        )
    else:
        st.info(f"Showing results for your data: {spec.label}.")
    st.header("The workflow, with this analysis' real results")
    _pipeline(report, exp)
    st.header("MFCS Concepts in Action")
    st.write("Each concept shows its definition, its role in DBSchemaX, and a result from the current analysis.")
    _concepts(report, exp)


# --- Schema input ------------------------------------------------------------------------

def _safe_attributes(text: str) -> list[str]:
    try:
        return list(parse_attribute_list(text))
    except (InputError, TypeError):
        return []


def _atomicity_widgets(prefix: str, options: list[str]) -> tuple[str, ...] | None:
    mode_key, list_key = f"{prefix}_atomic_mode", f"{prefix}_non_atomic"
    st.session_state.setdefault(mode_key, ATOMIC_UNKNOWN)
    mode = st.radio(
        "Atomicity information (for 1NF)",
        ATOMIC_MODES,
        key=mode_key,
        help="Functional dependencies cannot show whether values are atomic. "
        "Leave 'Unknown' unless you know; 1NF is then reported as not assessable.",
    )
    if mode == ATOMIC_ALL:
        return ()
    if mode == ATOMIC_SOME:
        st.session_state[list_key] = [a for a in st.session_state.get(list_key, []) if a in options]
        return tuple(st.multiselect("Non-atomic attributes", options, key=list_key))
    return None


def _submit(spec: AnalysisSpec) -> None:
    try:
        run_analysis(spec)
    except (ValueError, TypeError) as exc:
        st.error(f"Cannot analyze this input: {exc}")
        return
    _set_spec(spec)
    st.session_state["flash"] = "Analysis updated. Open any page in the sidebar to see the results."
    st.rerun()  # redraw the sidebar with the new relation


def _manual_tab() -> None:
    ss = st.session_state
    st.text_input("Relation name", key="manual_name")
    st.text_input("Attributes (comma-separated)", key="manual_attrs",
                  help="e.g. StudentID, CourseID, Grade")
    st.text_area(
        "Functional dependencies (one per line)",
        key="manual_fds",
        height=150,
        help='Syntax: "X -> Y" or "X → Y", attributes separated by commas, "∅ -> A" for an empty '
        "determinant. Lines starting with # are ignored.",
    )
    non_atomic = _atomicity_widgets("manual", _safe_attributes(ss.get("manual_attrs", "")))
    if st.button("Analyze", type="primary", key="manual_run"):
        try:
            attributes = parse_attribute_list(ss["manual_attrs"])
            fds = parse_fd_lines(ss["manual_fds"])
        except InputError as exc:
            st.error(f"Invalid input: {exc}")
            return
        name = ss["manual_name"].strip() or None
        _submit(AnalysisSpec("manual", "User-entered relation", name, attributes, fds, non_atomic=non_atomic))


def _database_source():
    """Return (bytes | Path | None) for the chosen database source."""
    kind = st.radio("Database source", ("Upload a file", "Local path"), horizontal=True, key="sql_kind")
    if kind == "Upload a file":
        uploaded = st.file_uploader(
            "SQLite database file", type=["db", "sqlite", "sqlite3", "db3"], key="sql_upload",
            help="The file is copied to a temporary location, opened read-only, and deleted afterwards.",
        )
        return uploaded.getvalue() if uploaded is not None else None
    text = st.text_input("Path to an existing SQLite file", key="sql_path")
    return Path(text.strip()) if text.strip() else None


@contextmanager
def _open_source(source) -> Iterator[Path]:
    if isinstance(source, (bytes, bytearray)):
        with temporary_database(bytes(source)) as path:
            yield path
    else:
        yield source


def _show_table_metadata(table_info: TableInfo) -> None:
    st.markdown(
        f"**Primary key:** {code('(' + ', '.join(table_info.primary_key) + ')') if table_info.primary_key else 'none declared'}"
    )
    extras = [name for name in ("is_generated", "is_hidden", "is_rowid_alias")
              if table_info.columns and hasattr(table_info.columns[0], name)]
    rows = []
    for column in table_info.columns:
        row = {
            "Column": column.name,
            "Type": column.declared_type or "—",
            "NOT NULL": yes_no(column.not_null),
            "Default": "—" if column.default_value is None else str(column.default_value),
            "PK position": str(column.primary_key_position) if column.is_primary_key else "—",
        }
        for name in extras:
            row[name.replace("_", " ")] = yes_no(bool(getattr(column, name)))
        rows.append(row)
    table(rows)
    if table_info.unique_constraints:
        st.markdown("**Unique constraints**")
        table([
            {"Columns": ", ".join(u.columns), "Origin": u.origin, "Partial": yes_no(u.partial),
             "Nullable columns": ", ".join(u.nullable_columns) or "—",
             "Enforces a key": yes_no(u.enforces_key)}
            for u in table_info.unique_constraints
        ])
    if table_info.foreign_keys:
        st.markdown("**Foreign keys** (context only; not used as FDs)")
        table([
            {"Columns": ", ".join(f.columns),
             "References": f"{f.referenced_table}({'primary key' if f.referenced_columns is None else ', '.join(f.referenced_columns)})",
             "ON UPDATE": f.on_update, "ON DELETE": f.on_delete}
            for f in table_info.foreign_keys
        ])


def _sqlite_tab() -> None:
    st.warning(
        "A table's schema does not reveal its functional dependencies. Only declared keys imply FDs "
        "(and only when sound). Add the FDs you know to the box below; results are only as complete "
        "as the FD set."
    )
    source = _database_source()
    if source is None:
        st.info("Choose a database to list its tables.")
        return
    try:
        with _open_source(source) as path:
            tables = list_user_tables(path)
    except SQLiteInspectionError as exc:
        st.error(str(exc))
        return
    if not tables:
        st.info("This database has no user tables.")
        return
    name = st.selectbox("Table", tables, key="sql_table")
    try:
        with _open_source(source) as path:
            table_info = inspect_sqlite_table(path, name)
    except SQLiteInspectionError as exc:
        st.error(str(exc))
        return
    _show_table_metadata(table_info)
    st.text_area(
        "Additional functional dependencies you know (one per line)",
        key="sql_fds",
        height=120,
        help='e.g. "ProductID -> ProductName". Attributes must be columns of this table.',
    )
    derive = st.checkbox("Derive FDs from declared key constraints", key="sql_derive")
    non_atomic = _atomicity_widgets("sql", list(table_info.column_names))
    if st.button("Analyze this table", type="primary", key="sql_run"):
        try:
            fds = parse_fd_lines(st.session_state.get("sql_fds", ""))
        except InputError as exc:
            st.error(f"Invalid input: {exc}")
            return
        origin = "uploaded file" if isinstance(source, (bytes, bytearray)) else "local file"
        _submit(AnalysisSpec(
            "sqlite", f"SQLite table '{table_info.name}' ({origin})", table_info.name, None, fds,
            table_info=table_info, use_declared_keys=derive, non_atomic=non_atomic,
        ))


def page_schema_input(spec, report, error) -> None:
    st.header("Schema Input")
    st.caption("Choose what to analyze. The sidebar always shows the relation currently analyzed.")
    if error:
        st.error(f"The current input cannot be analyzed: {error}")
    with st.container(border=True):
        st.markdown("**Examples** (labelled sample data)")
        st.selectbox("Example relation", [c.title for c in EXAMPLE_CASES], key="example_choice")
        chosen = next(c for c in EXAMPLE_CASES if c.title == st.session_state["example_choice"])
        st.caption(chosen.blurb)
        st.button("Load this example", on_click=_load_example)
    manual, sqlite_tab = st.tabs(["Manual relation", "SQLite table"])
    with manual:
        _manual_tab()
    with sqlite_tab:
        _sqlite_tab()


# --- Dependency explorer -------------------------------------------------------------------

def page_explorer(spec, report, exp) -> None:
    st.header("Dependency Explorer")
    st.write("F as a graph, and a step-by-step closure walkthrough for any attribute set X.")
    attrs = list(report.attributes)
    if "explorer_x" not in st.session_state:
        st.session_state["explorer_x"] = sorted(report.normalization.candidate_keys[0])
    st.session_state["explorer_x"] = [a for a in st.session_state["explorer_x"] if a in attrs]
    chosen = st.multiselect("Attribute set X (empty = ∅)", attrs, key="explorer_x")

    trace = closure_trace(chosen, exp.fds)
    left, right = st.columns([3, 2])
    with left:
        st.markdown("**FD graph** (:blue[blue] = X, :green[green] = derived by closure)")
        if exp.fds:
            st.graphviz_chart(fd_graph_dot(attrs, exp.fds, start=trace.start, closure=trace.closure))
        else:
            st.info("No functional dependencies to draw.")
        with st.expander("All FDs"):
            for fd in exp.fds:
                st.markdown(f"- {code(str(fd))}")
    with right:
        st.markdown("**Closure walkthrough**")
        render_closure_trace(trace, report, exp.fds)


# --- Candidate keys -----------------------------------------------------------------------------

def page_keys(spec, report, exp) -> None:
    norm = report.normalization
    st.header("Candidate Keys")
    st.latex(r"\text{superkey: }K^{+}=R\qquad\text{candidate key: a superkey with no proper subset a superkey}")
    for evidence in exp.key_evidence:
        with st.container(border=True):
            st.markdown(f":green[**Candidate key**] {code(fmt(evidence.key))}")
            st.markdown(f"Closure: K⁺ = {code(fmt(evidence.closure))} — equals R: {yes_no(evidence.closure == exp.relation)}")
            if evidence.removals:
                st.markdown("Minimality evidence — remove each attribute and recompute:")
                render_removal_table(evidence)
            else:
                st.markdown("K = ∅ has no attributes to remove: it is minimal.")
    left, right = st.columns(2)
    left.markdown(f"**Prime attributes** (in some candidate key): {code(fmt(norm.prime_attributes))}")
    right.markdown(f"**Non-prime attributes:** {code(fmt(norm.non_prime_attributes))}")
    st.info(
        "Why this matters: 3NF forgives a dependency whose right side is **prime**; 2NF only concerns "
        "**non-prime** attributes that depend on part of a key."
    )
    st.subheader("Exhaustive search: superkeys vs. candidate keys")
    table([
        {"Subset size": str(s), "Subsets": str(t), "Superkeys": str(sk), "Candidate keys": str(ck)}
        for s, t, sk, ck in subset_counts(exp.search_rows)
    ])
    view = st.radio("Show", ("Candidate keys", "All superkeys", "Every subset"), horizontal=True, key="keys_view")
    rows = exp.search_rows
    if view == "Candidate keys":
        rows = tuple(r for r in rows if r.is_candidate_key)
    elif view == "All superkeys":
        rows = tuple(r for r in rows if r.is_superkey)
    table([
        {
            "Subset": fmt(r.subset),
            "Closure": fmt(r.closure),
            "Superkey (closure = R)": yes_no(r.is_superkey),
            "Candidate key": yes_no(r.is_candidate_key),
            "Why not a candidate key": (
                f"contains candidate key {fmt(r.contains_key)}" if r.contains_key is not None
                else ("closure ≠ R" if not r.is_superkey else "")
            ),
        }
        for r in rows
    ])
    st.caption(
        f"Complexity: n = {len(report.attributes)} attributes ⇒ 2^n = {2 ** len(report.attributes)} subsets "
        "in the worst case; the number of subsets grows exponentially."
    )


# --- Minimal cover ---------------------------------------------------------------------------------

def page_cover(spec, report, exp) -> None:
    trace = exp.cover_trace
    st.header("Minimal Cover")
    original, final = st.columns(2)
    with original:
        st.markdown(":blue[**Original F**]")
        for fd in report.analysis_fds:
            st.markdown(f"- {code(str(fd))}")
        if not report.analysis_fds:
            st.write("(no FDs)")
    with final:
        st.markdown(":green[**Minimal cover**]")
        for fd in trace.cover:
            st.markdown(f"- {code(str(fd))}")
        if not trace.cover:
            st.write("(empty: no non-trivial dependencies)")
    st.caption("A minimal cover is not unique; this is one valid cover, chosen deterministically.")

    st.subheader("Step 1 — split right-hand sides")
    st.latex(r"X\rightarrow AB\ \Longrightarrow\ X\rightarrow A,\ X\rightarrow B")
    split_rows = [
        {"FD": str(s.original), "Singleton parts": "; ".join(str(p) for p in s.parts) or "(none)",
         "Trivial part dropped": fmt(s.dropped_trivial) if s.dropped_trivial else "—"}
        for s in trace.split_steps
    ]
    table(split_rows)
    if trace.duplicates_merged:
        st.caption(f"{trace.duplicates_merged} duplicate FD(s) were merged.")

    st.subheader("Step 2 — remove extraneous left-side attributes")
    st.latex(r"B\ \text{is extraneous in}\ X\rightarrow A\iff A\in(X\setminus\{B\})^{+}_F")
    if trace.extraneous_steps:
        for step in trace.extraneous_steps:
            st.markdown(
                f":orange[**Removed {step.attribute}**] from {code(str(step.before))} → {code(str(step.after))}: "
                f"closure of {code(fmt(step.after.lhs))} under the current FDs is {code(fmt(step.evidence_closure))}, "
                f"which contains {code(next(iter(step.before.rhs)))}."
            )
    else:
        st.info("No extraneous attribute was found: every left-side attribute is needed.")

    st.subheader("Step 3 — remove redundant FDs")
    st.latex(r"X\rightarrow A\ \text{is redundant}\iff A\in X^{+}_{F\setminus\{X\rightarrow A\}}")
    if trace.redundant_steps:
        for step in trace.redundant_steps:
            st.markdown(
                f":orange[**Removed {code(str(step.removed))}**]: closure of {code(fmt(step.removed.lhs))} under the "
                f"remaining FDs is {code(fmt(step.closure_without))}, which already contains it."
            )
    else:
        st.info("No redundant FD was found.")

    st.subheader("Verification: F and the cover imply each other")
    eq = exp.cover_equivalence
    if eq.equivalent:
        st.success("F ≡ cover (mutual implication holds).")
    else:
        st.error("The sets are not equivalent.")
    a, b = st.columns(2)
    with a:
        st.markdown("Cover ⊨ each original FD")
        table([{"FD": str(e.fd), "LHS⁺ under cover": fmt(e.lhs_closure), "Implied": yes_no(e.implied)}
               for e in eq.second_implies_first])
    with b:
        st.markdown("Original F ⊨ each cover FD")
        table([{"FD": str(e.fd), "LHS⁺ under F": fmt(e.lhs_closure), "Implied": yes_no(e.implied)}
               for e in eq.first_implies_second])


# --- Normalization -----------------------------------------------------------------------------------

def page_normalization(spec, report, exp) -> None:
    norm = report.normalization
    st.header("Normalization: rules as logical predicates")
    st.warning(
        "Results are relative to the supplied FDs. If that set is incomplete, true violations can be missed."
    )
    c1, c2 = st.columns(2)
    with c1:
        with st.container(border=True):
            st.markdown("**3NF** — for every non-trivial FD X → A:")
            st.latex(r"X\ \text{is a superkey}\ \ \vee\ \ A\ \text{is prime}")
    with c2:
        with st.container(border=True):
            st.markdown("**BCNF** — for every non-trivial FD X → A:")
            st.latex(r"X\ \text{is a superkey}")
    if norm.first_nf is None:
        first = ("Not enough information", "No: FDs do not describe whether values are atomic.",
                 "Unknown", "No atomicity information was supplied.")
    else:
        first = ("Caller-supplied information", "Only the atomicity information you entered (not derived from FDs).",
                 "Satisfied" if norm.first_nf else "Violated",
                 "Non-atomic: " + (", ".join(report.non_atomic_attributes or ()) or "none"))
    table([
        {"Normal form": "1NF", "Rule checked": "every attribute value is atomic",
         "Enough information?": first[1], "Result": first[2], "Evidence": first[3]},
        {"Normal form": "2NF", "Rule checked": "no non-prime attribute depends on a proper subset of a candidate key",
         "Enough information?": "Yes, for the supplied FDs (assuming atomic values)",
         "Result": "Satisfied" if norm.satisfies_2nf else "Violated",
         "Evidence": f"{len(norm.partial_dependencies)} partial dependency(ies)"},
        {"Normal form": "3NF", "Rule checked": "every non-trivial X → A: X superkey or A prime",
         "Enough information?": "Yes, for the supplied FDs (assuming atomic values)",
         "Result": "Satisfied" if norm.satisfies_3nf else "Violated",
         "Evidence": f"{len(norm.violations_3nf)} violating FD(s)"},
        {"Normal form": "BCNF", "Rule checked": "every non-trivial X → A: X superkey",
         "Enough information?": "Yes, for the supplied FDs (assuming atomic values)",
         "Result": "Satisfied" if norm.satisfies_bcnf else "Violated",
         "Evidence": f"{len(norm.violations_bcnf)} violating FD(s)"},
    ])
    st.markdown(
        f"Candidate keys: {', '.join(code(fmt(k)) for k in norm.candidate_keys)} · "
        f"prime: {code(fmt(norm.prime_attributes))} · non-prime: {code(fmt(norm.non_prime_attributes))}"
    )
    st.subheader("Predicates evaluated for each non-trivial FD")
    if exp.predicates:
        render_predicate_table(exp)
    else:
        st.info("No non-trivial FDs: nothing can violate 3NF or BCNF.")
    only_bcnf = [r for r in exp.predicates if r.satisfies_3nf and not r.satisfies_bcnf]
    if only_bcnf:
        st.info(
            "**3NF ≠ BCNF here:** "
            + "; ".join(f"{code(str(r.fd))} is allowed by 3NF because {next(iter(r.fd.rhs))} is prime, but violates BCNF "
                        f"because {code(fmt(r.fd.lhs))} is not a superkey" for r in only_bcnf)
        )
    st.subheader("Violations and why they occur")
    if not report.violation_details:
        st.success("No 2NF, 3NF or BCNF violations for the supplied FDs.")
    for detail in report.violation_details:
        label = f"{detail.normal_form}: {fmt(detail.determinant)} → {detail.attribute}"
        with st.expander(label, expanded=False):
            st.error(detail.explanation)
            st.markdown(f"Closure: {code(fmt(detail.determinant))}⁺ = {code(fmt(detail.determinant_closure))}")
            if detail.key is not None:
                st.markdown(f"Candidate key involved: {code(fmt(detail.key))}")


# --- 3NF synthesis -------------------------------------------------------------------------------------

def page_synthesis(spec, report, exp) -> None:
    syn = exp.synthesis
    norm = report.normalization
    st.header("3NF Synthesis: before and after")
    if spec.source == "demo":
        st.info("Demonstration relation: Student–Course. Everything below is computed live by the real functions.")
    before, after = st.columns(2)
    with before:
        st.markdown(":red[**Before**]" if not norm.satisfies_3nf else ":blue[**Before**]")
        with st.container(border=True):
            st.markdown(f"**{report.relation_name or 'R'}**({', '.join(report.attributes)})")
            st.markdown("Candidate keys: " + ", ".join(code(fmt(k)) for k in norm.candidate_keys))
            st.markdown("FDs:")
            for fd in report.analysis_fds:
                st.markdown(f"- {code(str(fd))}")
            if norm.partial_dependencies:
                st.markdown("Partial dependencies (2NF violations):")
                for p in norm.partial_dependencies:
                    st.markdown(f"- {code(fmt(p.determinant))} → {p.attribute} (part of key {code(fmt(p.key))})")
            st.markdown(f"Highest FD-based normal form: **{norm.highest_fd_normal_form}**")
    with after:
        st.markdown(":green[**After**]")
        for index, schema in enumerate(syn.schemas, start=1):
            with st.container(border=True):
                st.markdown(f"**{schema_text(index, schema)}**")
                origin = [g for g in syn.groups if g.schema == schema]
                if origin:
                    st.caption("from cover FD(s): " + "; ".join(str(f) for g in origin for f in g.fds))
                elif syn.key_schema == schema:
                    st.caption("candidate-key schema (added so that a key of the original relation is kept)")
                else:
                    st.caption("subsuming schema")
    st.subheader("How the schemas were produced")
    st.markdown(f"**1. Minimal cover:** " + ("; ".join(code(str(f)) for f in syn.cover) or "(empty)"))
    st.markdown("**2. One schema per determinant** (X ∪ all attributes it determines in the cover):")
    table([{"Determinant X": fmt(g.lhs), "FDs": "; ".join(str(f) for f in g.fds), "Schema": fmt(g.schema)}
           for g in syn.groups])
    if syn.subsumed:
        st.markdown("**3. Subsumed schemas removed:** " + "; ".join(
            f"{code(fmt(s))} ⊂ {code(fmt(t))}" for s, t in syn.subsumed))
    else:
        st.markdown("**3. Subsumed schemas removed:** none.")
    if syn.key_schema_needed:
        st.markdown(f"**4. Key step:** no schema contained a candidate key, so {code(fmt(syn.key_schema))} was added.")
    else:
        st.markdown(
            "**4. Key step:** a candidate key is already contained in a schema ("
            + ", ".join(code(fmt(k)) for k in syn.keys_contained) + "), so no key schema was needed."
        )
    st.subheader("Dependency preservation evidence")
    index_of = {s: i for i, s in enumerate(syn.schemas, start=1)}
    table([{"Cover FD": str(p.fd), "Contained in": schema_text(index_of[p.schema], p.schema)}
           for p in syn.preservation])
    st.markdown(f"All original attributes appear in some schema: {yes_no(syn.covers_all_attributes)}")
    st.subheader("Guarantees and caveats")
    st.markdown("**Guarantees of the standard algorithm (for the supplied FDs):**")
    for text in getattr(reporting_module, "SYNTHESIS_GUARANTEES", ()):
        st.markdown(f"- {text}")
    st.markdown("**Caveats:**")
    for text in getattr(reporting_module, "SYNTHESIS_CAVEATS", ()):
        st.markdown(f"- {text}")


# --- Reports ----------------------------------------------------------------------------------------

def page_reports(spec, report, exp) -> None:
    st.header("Reports")
    st.write("Exports are generated by the same report functions the command-line tool uses.")
    downloads = build_downloads(report)
    left, right = st.columns(2)
    for column, key, label in ((left, "markdown", "Download Markdown report"),
                               (right, "json", "Download JSON report")):
        name, data, mime = downloads[key]
        column.download_button(label, data=data, file_name=name, mime=mime, key=f"dl_{key}")
    st.subheader("Limitations and assumptions")
    for line in report.limitations:
        st.markdown(f"- {line}")
    with st.expander("Preview: Markdown"):
        st.markdown(downloads["markdown"][1].decode("utf-8"))
    with st.expander("Preview: JSON"):
        st.json(json.loads(downloads["json"][1].decode("utf-8")))


# --- Normalization Lab ------------------------------------------------------------------------------

def _load_lab_demo() -> None:
    """Build the synthetic dataset and run the real pipeline; keep result or error."""
    ss = st.session_state
    ss.pop("lab_result", None)
    ss.pop("lab_error", None)
    try:
        ss["lab_result"] = lab.run_lab(build_demo_dataset())
    except lab.FDViolationError as exc:
        ss["lab_error"] = (str(exc), lab.describe_failures(exc.checks))
    except (ValueError, TypeError) as exc:
        ss["lab_error"] = (str(exc), [])
    ss["lab_filter"] = ""


def _analyze_demo_elsewhere() -> None:
    result = st.session_state["lab_result"]
    ds = result.dataset
    _set_spec(AnalysisSpec(
        "example", "Sample data: 250-record synthetic enrollment demo", ds.relation_name,
        ds.attributes, ds.fds, non_atomic=ds.non_atomic,
    ))
    st.session_state["flash"] = "The 11-column demo relation is now the one analyzed on the other pages."


def _lab_original(result) -> None:
    ds, report = result.dataset, result.report
    norm = report.normalization
    st.warning(ds.notice)
    st.markdown(f"**{ds.title}** — **{len(ds.rows)} rows** × {len(ds.attributes)} columns")
    st.text_input("Filter rows (matches any column)", key="lab_filter")
    shown = lab.filter_rows(ds.rows, st.session_state.get("lab_filter", ""))
    st.caption(f"Showing {len(shown)} of {len(ds.rows)} rows.")
    st.dataframe(lab.rows_as_dicts(ds.attributes, shown), hide_index=True, height=300)
    st.download_button("Download original data (CSV)", data=lab.to_csv(ds.attributes, ds.rows),
                       file_name="Enrollment_Raw.csv", mime="text/csv", key="dl_lab_original")
    left, right = st.columns(2)
    with left:
        st.markdown("**Functional dependencies** (declared)")
        for fd in report.analysis_fds:
            st.markdown(f"- {code(str(fd))}")
    with right:
        st.markdown("**Candidate key** (found by the existing key search)")
        for key in norm.candidate_keys:
            st.markdown(f"- {code(fmt(key))}")
        st.markdown(f"**Atomicity:** all {len(ds.attributes)} columns hold single values "
                    f"(1NF: {yes_no(bool(norm.first_nf))}).")
    st.subheader("Do the declared dependencies hold on these rows?")
    table([
        {"FD": str(c.fd), "Holds on the data": yes_no(c.holds),
         "Evidence": lab.repetition_summary(c)}
        for c in result.fd_checks
    ])
    st.success(f"All {len(result.fd_checks)} declared FDs hold on all {len(ds.rows)} records, "
               "so the schema is consistent with the data and normalization can proceed.")


def _lab_why(result) -> None:
    report, exp = result.report, result.explanation
    norm = report.normalization
    st.markdown("**Step 1 — 1NF.** " + (
        "Every value is atomic (one value per cell), so the table is in 1NF."
        if norm.first_nf else "Some values are not atomic."))
    st.markdown(f"**Step 2 — 2NF.** Key = {code(fmt(norm.candidate_keys[0]))}. A non-key fact must "
                "depend on the *whole* key. These depend on only part of it:")
    table([
        {"Depends on only…": fmt(p.determinant), "…but is stored as": p.attribute,
         "Part of key": fmt(p.key)}
        for p in norm.partial_dependencies
    ])
    st.markdown("**Step 3 — 3NF.** A non-key fact must not depend on another non-key fact "
                "(otherwise it is a chain of dependencies). Violating dependencies:")
    table([{"Dependency": str(fd), "Why it violates 3NF":
            f"{fmt(fd.lhs)} is not a key and {next(iter(fd.rhs))} is not part of a key"}
           for fd in norm.violations_3nf])
    st.markdown(f"Highest normal form of the original table: **{norm.highest_fd_normal_form}**. "
                "Engine explanations:")
    for detail in report.violation_details:
        with st.expander(f"{detail.normal_form}: {fmt(detail.determinant)} → {detail.attribute}"):
            st.error(detail.explanation)
    st.markdown("**Step 4 — the fix (existing 3NF synthesis).** One table per determinant:")
    table([{"Determinant": fmt(g.lhs), "Cover FDs": "; ".join(str(f) for f in g.fds),
            "Schema": fmt(g.schema)} for g in exp.synthesis.groups])
    st.caption("Candidate key already inside a schema: " + ", ".join(
        code(fmt(k)) for k in exp.synthesis.keys_contained) if exp.synthesis.keys_contained
        else "A key schema was added.")
    st.subheader("Why each table exists")
    for rel in result.relations:
        with st.container(border=True):
            st.markdown(f"**{rel.name}** — {rel.description}")
            st.markdown(f"Justified by: {code(rel.dependency_text)}")
            st.write(rel.reason)


def _lab_tables(result) -> None:
    st.info("Table names are friendly labels. The columns of each table come from the 3NF "
            "synthesis; the rows are the original records projected onto those columns with "
            "duplicate tuples removed.")
    names = [r.name for r in result.relations]
    st.session_state.setdefault("lab_table", names[0])
    if st.session_state["lab_table"] not in names:
        st.session_state["lab_table"] = names[0]
    table([
        {"Table": r.name, "Rows": str(r.row_count), "Columns": ", ".join(r.attributes),
         "Primary key": ", ".join(r.primary_key),
         "Foreign keys": "; ".join(f"({', '.join(f.columns)}) → {f.references}"
                                   + ("" if f.verified else " ✗") for f in r.foreign_keys) or "—",
         "Duplicates removed": str(r.duplicates_removed)}
        for r in result.relations
    ])
    st.radio("Show table", names, key="lab_table", horizontal=True)
    rel = result.relation(st.session_state["lab_table"])
    st.markdown(f"**{rel.name}({', '.join(rel.attributes)})** — {rel.row_count} rows; "
                f"primary key {code(', '.join(rel.primary_key))}")
    st.dataframe(lab.rows_as_dicts(rel.attributes, rel.rows), hide_index=True, height=300)


def _lab_verify(result) -> None:
    rec, m = result.reconstruction, result.metrics
    st.subheader("Lossless check: rebuild the original from the tables")
    steps = rec.starts_with + "".join(f" ⋈ {s.relation}" for s in rec.steps)
    st.markdown(f"Natural join order: {code(steps)}")
    if rec.ok:
        st.success(f"Reconstruction succeeded: {rec.reconstructed_count} tuples rebuilt, identical to "
                   f"the {rec.original_count} original tuples (0 missing, 0 spurious).")
    else:
        st.error(f"Reconstruction FAILED: {rec.missing} original tuple(s) missing, "
                 f"{rec.extra} spurious tuple(s).")
    st.caption(lab.RECONSTRUCTION_NOTE)
    st.subheader("Before vs after")
    table([
        {"Measure": "Relations", "Before": str(m.relations_before), "After": str(m.relations_after),
         "Change": f"{m.relations_after - m.relations_before:+d}"},
        {"Measure": "Rows", "Before": str(m.rows_before), "After": str(m.rows_after),
         "Change": f"{m.rows_delta:+d}"},
        {"Measure": "Attribute-value cells", "Before": f"{m.cells_before:,}",
         "After": f"{m.cells_after:,}",
         "Change": f"{m.cells_delta:+,} ({m.cells_change_percent:+.1f}%)"},
    ])
    st.caption(lab.CELL_COUNT_NOTE)
    st.caption(lab.ROW_COUNT_NOTE)
    st.subheader("Per table")
    table([
        {"Table": r.name, "Rows": str(r.row_count), "Columns": str(len(r.attributes)),
         "Cells": f"{r.cell_count:,}"} for r in result.relations
    ])


def _lab_downloads(result) -> None:
    st.write("Each normalized table as CSV, or all of them in one ZIP.")
    st.download_button("Download all normalized tables (ZIP)", data=lab.build_zip(result.relations),
                       file_name="normalized_tables.zip", mime="application/zip", key="dl_lab_zip")
    columns = st.columns(3)
    for i, rel in enumerate(result.relations):
        columns[i % 3].download_button(
            f"{rel.name}.csv ({rel.row_count} rows)", data=lab.to_csv(rel.attributes, rel.rows),
            file_name=lab.relation_csv_name(rel), mime="text/csv", key=f"dl_lab_{rel.name}")


def page_lab(spec, report, exp) -> None:
    ss = st.session_state
    st.header("Normalization Lab")
    st.write("See normalization happen on real records: a 250-row table is split into 3NF tables "
             "using the project's own analysis and synthesis code, then rebuilt and checked.")
    st.button("Load 250-Record Demo", type="primary", key="lab_load", on_click=_load_lab_demo)
    if "lab_error" in ss:
        message, lines = ss["lab_error"]
        st.error(f"The demo cannot be normalized: {message}")
        for line in lines:
            st.markdown(f"- {line}")
        return
    result = ss.get("lab_result")
    if result is None:
        st.info("Click **Load 250-Record Demo** to preload the synthetic dataset, its 11 columns, "
                "functional dependencies, candidate key and atomicity information.")
        return
    st.button("Analyze this relation on the other pages", key="lab_elsewhere",
              on_click=_analyze_demo_elsewhere)
    tabs = st.tabs(["1 · Original data", "2 · Why split?", "3 · Normalized tables",
                    "4 · Verify & compare", "5 · Download"])
    for tab, render in zip(tabs, (_lab_original, _lab_why, _lab_tables, _lab_verify, _lab_downloads)):
        with tab:
            render(result)


PAGE_FUNCTIONS = {
    "Overview": page_overview,
    "Dependency Explorer": page_explorer,
    "Candidate Keys": page_keys,
    "Minimal Cover": page_cover,
    "Normalization": page_normalization,
    "3NF Synthesis": page_synthesis,
    "Reports": page_reports,
    LAB_PAGE: page_lab,
}


def main() -> None:
    st.set_page_config(page_title="DBSchemaX — MFCS in Database Design", page_icon="🧮", layout="wide")
    _ensure_state()
    spec, report, exp, error = _current()
    _sidebar(spec, report)
    flash = st.session_state.pop("flash", None)
    if flash:
        st.success(flash)
    page = st.session_state["page"]
    if page == "Schema Input":
        page_schema_input(spec, report, error)
    elif page == LAB_PAGE:  # self-contained: independent of the analyzed relation
        page_lab(spec, report, exp)
    elif error:
        st.error(f"The current input cannot be analyzed: {error}")
        st.info("Open **Schema Input** in the sidebar to fix it or load an example.")
    else:
        PAGE_FUNCTIONS[page](spec, report, exp)


if __name__ == "__main__":
    main()
