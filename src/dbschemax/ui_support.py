"""Streamlit-independent support code for the dashboard.

Everything here is pure or touches only the filesystem through explicit,
temporary paths, so it can be tested without Streamlit installed.
"""

from __future__ import annotations

import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from dbschemax.explain import MAX_SEARCH_ATTRIBUTES
from dbschemax.inputs import parse_fd_items
from dbschemax.models import FunctionalDependency
from dbschemax.reporting import (
    AnalysisReport,
    build_analysis_report,
    build_table_report,
    render_json_report,
    render_markdown_report,
)
from dbschemax.sqlite_integration import TableInfo

MAX_UI_ATTRIBUTES = MAX_SEARCH_ATTRIBUTES


@dataclass(frozen=True)
class ExampleCase:
    """A labelled sample relation. FDs are plain text parsed by the real parser."""

    key: str
    title: str
    name: str
    attributes: tuple[str, ...]
    fds: tuple[str, ...]
    blurb: str


DEMO_CASE = ExampleCase(
    key="student_course",
    title="Student–Course (demonstration)",
    name="Enrollment",
    attributes=("StudentID", "CourseID", "StudentName", "CourseName", "Grade"),
    fds=(
        "StudentID -> StudentName",
        "CourseID -> CourseName",
        "StudentID, CourseID -> Grade",
    ),
    blurb="Composite key with partial dependencies: the classic 2NF/3NF decomposition.",
)

EXAMPLE_CASES: tuple[ExampleCase, ...] = (
    DEMO_CASE,
    ExampleCase(
        key="three_nf_not_bcnf",
        title="3NF but not BCNF",
        name="Teaching",
        attributes=("Student", "Course", "Instructor"),
        fds=("Student, Course -> Instructor", "Instructor -> Course"),
        blurb="Overlapping candidate keys: Instructor → Course violates BCNF only.",
    ),
    ExampleCase(
        key="multiple_keys",
        title="Multiple candidate keys",
        name="Employee",
        attributes=("EmpID", "Email", "Name"),
        fds=("EmpID -> Email, Name", "Email -> EmpID"),
        blurb="Both EmpID and Email are candidate keys.",
    ),
    ExampleCase(
        key="empty_lhs",
        title="Empty-LHS dependency",
        name="Config",
        attributes=("A", "B", "C"),
        fds=("∅ -> A", "A -> B"),
        blurb="∅ → A means A is constant; the only key must contain C.",
    ),
    ExampleCase(
        key="incomplete",
        title="Incomplete FD set (key FD only)",
        name="OrderLines",
        attributes=("OrderID", "ProductID", "ProductName", "Qty"),
        fds=("OrderID, ProductID -> ProductName, Qty",),
        blurb="Looks BCNF, but the real ProductID → ProductName is missing from F.",
    ),
)


@dataclass(frozen=True)
class AnalysisSpec:
    """What is currently being analyzed (inputs only; results are recomputed).

    ``source`` is ``"demo"``, ``"example"``, ``"manual"`` or ``"sqlite"``;
    ``label`` tells the user where the data came from.
    """

    source: str
    label: str
    name: str | None
    attributes: tuple[str, ...] | None
    user_fds: tuple[FunctionalDependency, ...]
    table_info: TableInfo | None = None
    use_declared_keys: bool = True
    non_atomic: tuple[str, ...] | None = None


def spec_from_example(case: ExampleCase) -> AnalysisSpec:
    """Build a spec from an example, parsing its FDs with the real parser."""
    is_demo = case.key == DEMO_CASE.key
    return AnalysisSpec(
        source="demo" if is_demo else "example",
        label=f"Sample data: {case.title}",
        name=case.name,
        attributes=case.attributes,
        user_fds=parse_fd_items(case.fds),
    )


def run_analysis(spec: AnalysisSpec) -> AnalysisReport:
    """Run the real analysis engine for ``spec``.

    Raises:
        ValueError: invalid inputs, or more than ``MAX_UI_ATTRIBUTES`` attributes
            (exhaustive key search is exponential in the attribute count).
    """
    if spec.table_info is not None:
        count = len(spec.table_info.columns)
    else:
        count = len(spec.attributes or ())
    if count > MAX_UI_ATTRIBUTES:
        raise ValueError(
            f"{count} attributes means {2 ** count} subsets to search; the dashboard "
            f"is limited to {MAX_UI_ATTRIBUTES} attributes"
        )
    if spec.table_info is not None:
        return build_table_report(
            spec.table_info,
            spec.user_fds,
            use_declared_keys=spec.use_declared_keys,
            non_atomic_attributes=spec.non_atomic,
        )
    return build_analysis_report(
        spec.attributes,
        spec.user_fds,
        spec.name,
        non_atomic_attributes=spec.non_atomic,
    )


def _safe_filename(name: str | None) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or "").strip("._")
    return cleaned or "relation"


def build_downloads(report: AnalysisReport) -> dict[str, tuple[str, bytes, str]]:
    """Return ``{"markdown"|"json": (file name, UTF-8 bytes, MIME type)}``.

    Content comes straight from the existing report renderers.
    """
    stem = f"{_safe_filename(report.relation_name)}_report"
    return {
        "markdown": (
            f"{stem}.md",
            render_markdown_report(report).encode("utf-8"),
            "text/markdown",
        ),
        "json": (
            f"{stem}.json",
            render_json_report(report).encode("utf-8"),
            "application/json",
        ),
    }


@contextmanager
def temporary_database(data: bytes) -> Iterator[Path]:
    """Write uploaded bytes to a private temporary file; delete it afterwards.

    The inspector opens the copy read-only, so the user's original file is
    never touched, and the copy is removed even if inspection fails.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("database data must be bytes")
    with tempfile.TemporaryDirectory(prefix="dbschemax_") as folder:
        path = Path(folder) / "uploaded.sqlite"
        path.write_bytes(bytes(data))
        yield path


def _quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def fd_graph_dot(
    attributes: Iterable[str],
    fds: Iterable[FunctionalDependency],
    *,
    start: Iterable[str] = (),
    closure: Iterable[str] = (),
) -> str:
    """Graphviz DOT source for an FD diagram.

    Attributes are boxes. ``X -> Y`` draws edges from each attribute of X to a
    small junction node and from the junction to each attribute of Y (so
    composite determinants stay readable); an empty LHS draws from ``∅``.
    ``start`` attributes are shaded blue and attributes in ``closure`` but not
    in ``start`` green.
    """
    start_set = frozenset(start)
    derived = frozenset(closure) - start_set
    lines = [
        "digraph FDs {",
        "  rankdir=LR;",
        '  node [shape=box, style="rounded,filled", fillcolor="white", fontname="Helvetica"];',
        '  edge [fontname="Helvetica"];',
    ]
    for attr in sorted(set(attributes)):
        fill = "lightblue" if attr in start_set else "palegreen" if attr in derived else "white"
        lines.append(f"  {_quote(attr)} [fillcolor={fill}];")
    uses_empty = False
    for index, fd in enumerate(sorted(fds, key=lambda f: (sorted(f.lhs), sorted(f.rhs)))):
        junction = f'"fd{index}"'
        label = _quote(str(fd))
        if len(fd.lhs) == 1 and len(fd.rhs) == 1:
            (left,), (right,) = tuple(fd.lhs), tuple(fd.rhs)
            lines.append(f"  {_quote(left)} -> {_quote(right)};")
            continue
        lines.append(f"  {junction} [shape=point, width=0.08, label=\"\", tooltip={label}];")
        if fd.lhs:
            for left in sorted(fd.lhs):
                lines.append(f"  {_quote(left)} -> {junction} [arrowhead=none];")
        else:
            uses_empty = True
            lines.append(f'  "∅" -> {junction} [arrowhead=none];')
        for right in sorted(fd.rhs):
            lines.append(f"  {junction} -> {_quote(right)};")
    if uses_empty:
        lines.append('  "∅" [shape=circle, style=dashed, fillcolor=white];')
    lines.append("}")
    return "\n".join(lines)
