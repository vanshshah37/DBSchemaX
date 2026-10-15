"""Normalization Lab: turn a relation *instance* into real normalized tables.

The lab does not contain its own normalization algorithm. It

1. checks that every declared FD actually holds on the supplied rows,
2. runs the project's existing analysis (:func:`build_analysis_report`,
   :func:`explain_report`) which uses :func:`find_candidate_keys`,
   :func:`minimal_cover` and :func:`synthesize_3nf`,
3. projects the original rows onto each synthesized schema and removes
   duplicate tuples,
4. derives primary keys (existing candidate-key search on the projected FDs)
   and foreign keys (a relation that carries another relation's primary key),
5. rebuilds the original rows by natural join and compares the two sets, and
6. counts relations, rows and attribute-value cells before and after.

Pure standard library; no Streamlit, database or file-system access.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from typing import Iterable, Sequence

from dbschemax.candidate_keys import find_candidate_keys
from dbschemax.closure import attribute_closure
from dbschemax.demo_data import DemoDataset, atomicity_problems
from dbschemax.explain import ReportExplanation, explain_report
from dbschemax.models import FunctionalDependency
from dbschemax.reporting import AnalysisReport, build_analysis_report, format_set

Row = tuple[str, ...]

MAX_EXAMPLES = 3

CELL_COUNT_NOTE = (
    "An attribute-value cell is one value in one row (rows × columns). The count is a simple "
    "indicator of how much information is stored redundantly, not a measurement of disk space: "
    "real storage also depends on data types, indexes, keys and compression."
)
ROW_COUNT_NOTE = (
    "Normalization does not necessarily reduce the total number of rows: splitting one table "
    "into several usually adds rows overall (each new table has its own rows). What shrinks is "
    "the repetition of facts inside those rows."
)
RECONSTRUCTION_NOTE = (
    "This is a check on these specific rows, not a proof for all possible data. (For 3NF "
    "synthesis that includes a candidate-key schema, lossless join is guaranteed by theory; "
    "this check confirms it on the actual records.)"
)


# --- Errors -------------------------------------------------------------------------

class FDViolationError(ValueError):
    """Raised when a declared FD is false on the supplied rows."""

    def __init__(self, checks: Sequence["FDCheck"]) -> None:
        self.checks = tuple(checks)
        failed = [c for c in self.checks if not c.holds]
        super().__init__(
            f"{len(failed)} declared functional dependency(ies) do not hold on the data: "
            + "; ".join(str(c.fd) for c in failed)
        )


# --- Step 1: do the declared FDs hold on the rows? ----------------------------------

@dataclass(frozen=True)
class ViolationExample:
    """One determinant value that maps to more than one dependent value."""

    determinant: tuple[tuple[str, str], ...]  # (attribute, value) pairs
    conflicting: tuple[tuple[tuple[str, str], ...], ...]  # distinct dependent values


@dataclass(frozen=True)
class FDCheck:
    """Result of testing one FD against the rows.

    ``groups`` is the number of distinct determinant values; with ``rows`` rows
    this means each fact is stored ``rows - groups`` extra times in the original
    table when the FD holds.
    """

    fd: FunctionalDependency
    holds: bool
    rows: int
    groups: int
    violating_groups: int
    examples: tuple[ViolationExample, ...]


def _ordered(attributes: Sequence[str], subset: Iterable[str]) -> tuple[str, ...]:
    wanted = frozenset(subset)
    return tuple(a for a in attributes if a in wanted)


def check_fd(
    attributes: Sequence[str], rows: Iterable[Row], fd: FunctionalDependency
) -> FDCheck:
    """Test ``fd`` on ``rows``: equal determinant values must give equal dependents."""
    attributes = tuple(attributes)
    index = {name: i for i, name in enumerate(attributes)}
    missing = fd.attributes - set(index)
    if missing:
        raise ValueError(f"FD {fd} mentions unknown attributes: {', '.join(sorted(missing))}")
    lhs = _ordered(attributes, fd.lhs)
    rhs = _ordered(attributes, fd.rhs)
    seen: dict[Row, dict[Row, None]] = {}
    total = 0
    for row in rows:
        total += 1
        key = tuple(row[index[a]] for a in lhs)
        value = tuple(row[index[a]] for a in rhs)
        seen.setdefault(key, {})[value] = None
    bad = [(key, tuple(values)) for key, values in seen.items() if len(values) > 1]
    examples = tuple(
        ViolationExample(
            tuple(zip(lhs, key)),
            tuple(tuple(zip(rhs, value)) for value in values),
        )
        for key, values in bad[:MAX_EXAMPLES]
    )
    return FDCheck(fd, not bad, total, len(seen), len(bad), examples)


def check_fds(
    attributes: Sequence[str], rows: Iterable[Row], fds: Iterable[FunctionalDependency]
) -> tuple[FDCheck, ...]:
    rows = tuple(rows)
    return tuple(check_fd(attributes, rows, fd) for fd in fds)


def describe_failures(checks: Iterable[FDCheck]) -> list[str]:
    """Plain-language lines explaining each failed FD with concrete conflicting values."""
    lines = []
    for check in checks:
        if check.holds:
            continue
        lines.append(
            f"{check.fd} is false: {check.violating_groups} determinant value(s) map to more "
            "than one dependent value."
        )
        for example in check.examples:
            left = ", ".join(f"{a} = {v}" for a, v in example.determinant) or "(empty determinant)"
            right = " vs ".join(
                "(" + ", ".join(f"{a} = {v}" for a, v in option) + ")"
                for option in example.conflicting
            )
            lines.append(f"  e.g. {left} appears with {right}")
    return lines


# --- Step 2/3: projection, keys, foreign keys ------------------------------------------

def project_rows(
    attributes: Sequence[str], rows: Iterable[Row], schema: Iterable[str]
) -> tuple[Row, ...]:
    """Project rows onto ``schema`` (columns in original order) and drop duplicate tuples."""
    attributes = tuple(attributes)
    index = {name: i for i, name in enumerate(attributes)}
    columns = _ordered(attributes, schema)
    unique: dict[Row, None] = {}
    for row in rows:
        unique[tuple(row[index[a]] for a in columns)] = None
    return tuple(unique)


def _projected_candidate_keys(
    schema: frozenset[str], fds: Sequence[FunctionalDependency]
) -> tuple[frozenset[str], ...]:
    """Candidate keys of ``schema`` under F+, using the existing closure and key search.

    The FDs of F+ that live inside ``schema`` are obtained from closures of every
    subset of the schema; the project's key search is then run on them.
    """
    ordered = sorted(schema)
    projected = []
    for size in range(1, len(ordered) + 1):
        for combo in combinations(ordered, size):
            lhs = frozenset(combo)
            rhs = (attribute_closure(lhs, fds) & schema) - lhs
            if rhs:
                projected.append(FunctionalDependency(lhs, rhs))
    return find_candidate_keys(schema, projected)


@dataclass(frozen=True)
class ForeignKey:
    """``columns`` of the owning relation reference the primary key ``references``.

    The referenced columns carry the same names as ``columns``. ``verified`` is
    True when every value combination in the owning relation's rows appears in
    the referenced relation's rows.
    """

    columns: tuple[str, ...]
    references: str
    verified: bool


@dataclass(frozen=True)
class OutputRelation:
    """One normalized table with its real, deduplicated rows."""

    name: str
    description: str
    attributes: tuple[str, ...]
    candidate_keys: tuple[frozenset[str], ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKey, ...]
    determinant: frozenset[str] | None
    determined: frozenset[str]
    reason: str
    source_rows: int
    rows: tuple[Row, ...]

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def duplicates_removed(self) -> int:
        return self.source_rows - len(self.rows)

    @property
    def cell_count(self) -> int:
        return len(self.rows) * len(self.attributes)

    @property
    def dependency_text(self) -> str:
        if self.determinant is None:
            return "(candidate-key relation added by the synthesis)"
        return f"{', '.join(sorted(self.determinant))} → {', '.join(sorted(self.determined))}"


def _unique_name(base: str, taken: set[str]) -> str:
    name, number = base, 2
    while name in taken:
        name = f"{base}_{number}"
        number += 1
    taken.add(name)
    return name


def _generic_name(primary_key: Iterable[str]) -> str:
    parts = [a[:-2] if len(a) > 2 and a.endswith("ID") else a for a in sorted(primary_key)]
    return "_".join(parts) or "Relation"


def _describe_set(attributes: Iterable[str]) -> str:
    names = sorted(attributes)
    return names[0] if len(names) == 1 else "the combination (" + ", ".join(names) + ")"


def _reason(
    relation_name: str,
    determinant: frozenset[str] | None,
    determined: frozenset[str],
    holds_key: bool,
    source_rows: int,
    row_count: int,
) -> str:
    if determinant is None:
        return (
            f"{relation_name} keeps the original key together so the other tables can be "
            "joined back into the full records."
        )
    who = _describe_set(determinant)
    what = ", ".join(sorted(determined))
    if holds_key:
        return (
            f"{who} identify one record, and {what} describe that record as a whole, so they "
            f"need every part of the key. This table holds {row_count} such records."
        )
    return (
        f"Each value of {who} fixes {what}. In the original table these facts were copied onto "
        f"every one of the {source_rows} rows; here they are stored once per {who} "
        f"({row_count} rows)."
    )


def _derive_foreign_keys(
    attrs: dict[str, frozenset[str]],
    primary: dict[str, frozenset[str]],
    projected: dict[str, tuple[Row, ...]],
    columns: dict[str, tuple[str, ...]],
) -> dict[str, tuple[ForeignKey, ...]]:
    """Key-to-key references between the output relations.

    S references T on K when K is T's primary key, K is a part of S's columns, and K is
    not also S's own primary key (equal keys would make the direction ambiguous, so no
    foreign key is claimed). A reference is dropped as redundant when S already reaches T
    through another relation U that S references and that carries K. Each remaining
    reference is verified against the rows.
    """
    names = list(attrs)
    edges = {
        (s, primary[t], t)
        for s in names
        for t in names
        if s != t
        and primary[t] <= attrs[s]
        and primary[t] != primary[s]
        and primary[t] != attrs[s]
    }
    kept = {
        (s, key, t)
        for (s, key, t) in edges
        if not any(
            s2 == s and u != t and key <= attrs[u] and (u, key, t) in edges
            for (s2, _key2, u) in edges
        )
    }
    result: dict[str, list[ForeignKey]] = {name: [] for name in names}
    for s, key, t in sorted(kept, key=lambda e: (e[0], sorted(e[1]), e[2])):
        key_columns = _ordered(columns[s], key)
        s_index = [columns[s].index(a) for a in key_columns]
        t_index = [columns[t].index(a) for a in key_columns]
        parent_values = {tuple(row[i] for i in t_index) for row in projected[t]}
        verified = all(tuple(row[i] for i in s_index) in parent_values for row in projected[s])
        result[s].append(ForeignKey(key_columns, t, verified))
    return {name: tuple(fks) for name, fks in result.items()}


def _order_parents_first(
    names: list[str], foreign_keys: dict[str, tuple[ForeignKey, ...]]
) -> list[str]:
    """Referenced relations before the relations that reference them (stable)."""
    remaining = list(names)
    ordered: list[str] = []
    while remaining:
        ready = [
            n for n in remaining
            if all(fk.references in ordered for fk in foreign_keys[n])
        ]
        pick = ready[0] if ready else remaining[0]  # a cycle cannot occur; stay safe
        ordered.append(pick)
        remaining.remove(pick)
    return ordered


def build_output_relations(
    dataset: DemoDataset, report: AnalysisReport, explanation: ReportExplanation
) -> tuple[OutputRelation, ...]:
    """Project the dataset onto the synthesized schemas of ``report``."""
    attributes = dataset.attributes
    fds = report.analysis_fds
    schemas = report.synthesized_schemas
    groups = explanation.synthesis.groups
    key_schemas = {s for s in schemas for k in report.normalization.candidate_keys if k <= s}

    taken: set[str] = set()
    staged: list[dict] = []
    for schema in schemas:
        keys = _projected_candidate_keys(schema, fds)
        primary_key = keys[0] if keys else schema
        label = dataset.labels.get(frozenset(primary_key))
        name = _unique_name(label[0] if label else _generic_name(primary_key), taken)
        origin = next((g for g in groups if g.schema == schema), None)
        determinant = origin.lhs if origin is not None else None
        determined = (
            frozenset().union(*(f.rhs for f in origin.fds)) if origin is not None else frozenset()
        )
        rows = project_rows(attributes, dataset.rows, schema)
        columns = _ordered(attributes, schema)
        order_cols = [columns.index(a) for a in _ordered(columns, primary_key)]
        rows = tuple(sorted(rows, key=lambda r: ([r[i] for i in order_cols], r)))
        staged.append(
            {
                "name": name,
                "schema": schema,
                "columns": columns,
                "keys": keys,
                "primary_key": _ordered(columns, primary_key),
                "label": label,
                "determinant": determinant,
                "determined": determined,
                "rows": rows,
                "holds_key": schema in key_schemas,
            }
        )

    foreign_keys = _derive_foreign_keys(
        {s["name"]: s["schema"] for s in staged},
        {s["name"]: frozenset(s["primary_key"]) for s in staged},
        {s["name"]: s["rows"] for s in staged},
        {s["name"]: s["columns"] for s in staged},
    )
    by_name = {s["name"]: s for s in staged}
    relations = []
    for name in _order_parents_first([s["name"] for s in staged], foreign_keys):
        s = by_name[name]
        relations.append(
            OutputRelation(
                name=name,
                description=s["label"][1] if s["label"] else "",
                attributes=s["columns"],
                candidate_keys=s["keys"],
                primary_key=s["primary_key"],
                foreign_keys=foreign_keys[name],
                determinant=s["determinant"],
                determined=s["determined"],
                reason=_reason(
                    name, s["determinant"], s["determined"], s["holds_key"],
                    len(dataset.rows), len(s["rows"]),
                ),
                source_rows=len(dataset.rows),
                rows=s["rows"],
            )
        )
    return tuple(relations)


# --- Step 4: reconstruct the original by natural join -----------------------------------

def natural_join(
    left_columns: Sequence[str],
    left_rows: Iterable[Row],
    right_columns: Sequence[str],
    right_rows: Iterable[Row],
) -> tuple[tuple[str, ...], tuple[Row, ...]]:
    """Natural join (cross product when no column is shared)."""
    left_columns, right_columns = tuple(left_columns), tuple(right_columns)
    shared = [c for c in right_columns if c in left_columns]
    extra = [c for c in right_columns if c not in shared]
    left_at = [left_columns.index(c) for c in shared]
    right_at = [right_columns.index(c) for c in shared]
    extra_at = [right_columns.index(c) for c in extra]
    buckets: dict[Row, list[Row]] = {}
    for row in right_rows:
        buckets.setdefault(tuple(row[i] for i in right_at), []).append(row)
    joined: list[Row] = []
    for row in left_rows:
        for match in buckets.get(tuple(row[i] for i in left_at), ()):
            joined.append(row + tuple(match[i] for i in extra_at))
    return left_columns + tuple(extra), tuple(joined)


@dataclass(frozen=True)
class JoinStep:
    relation: str
    on: tuple[str, ...]
    rows_after: int


@dataclass(frozen=True)
class ReconstructionCheck:
    """Comparison of the original tuple set with the join of the output tables."""

    ok: bool
    original_count: int
    reconstructed_count: int
    missing: int  # in the original but not reconstructed
    extra: int  # reconstructed but not in the original (spurious tuples)
    steps: tuple[JoinStep, ...]
    starts_with: str


def reconstruct(
    attributes: Sequence[str],
    original_rows: Iterable[Row],
    relations: Sequence[OutputRelation],
    prefer: Iterable[str] = (),
) -> ReconstructionCheck:
    """Join ``relations`` back together and compare with ``original_rows`` as sets.

    The join starts from the first relation whose name is in ``prefer`` (the
    relation that holds a candidate key) and then repeatedly joins the relation
    sharing the most columns with the result so far.
    """
    attributes = tuple(attributes)
    original = set(original_rows)
    preferred = set(prefer)
    pending = list(relations)
    if not pending:
        return ReconstructionCheck(False, len(original), 0, len(original), 0, (), "")
    first = next((r for r in pending if r.name in preferred), max(pending, key=lambda r: r.row_count))
    pending.remove(first)
    columns, rows = first.attributes, first.rows
    steps: list[JoinStep] = []
    while pending:
        nxt = max(pending, key=lambda r: len(set(r.attributes) & set(columns)))
        pending.remove(nxt)
        on = tuple(c for c in nxt.attributes if c in columns)
        columns, rows = natural_join(columns, rows, nxt.attributes, nxt.rows)
        steps.append(JoinStep(nxt.name, on, len(rows)))
    if set(columns) != set(attributes):
        return ReconstructionCheck(False, len(original), len(set(rows)), len(original), 0,
                                   tuple(steps), first.name)
    index = [columns.index(a) for a in attributes]
    rebuilt = {tuple(row[i] for i in index) for row in rows}
    missing, extra = len(original - rebuilt), len(rebuilt - original)
    return ReconstructionCheck(
        ok=not missing and not extra,
        original_count=len(original),
        reconstructed_count=len(rebuilt),
        missing=missing,
        extra=extra,
        steps=tuple(steps),
        starts_with=first.name,
    )


# --- Step 5: before / after counts --------------------------------------------------------

@dataclass(frozen=True)
class Metrics:
    relations_before: int
    rows_before: int
    cells_before: int
    relations_after: int
    rows_after: int
    cells_after: int

    @property
    def rows_delta(self) -> int:
        return self.rows_after - self.rows_before

    @property
    def cells_delta(self) -> int:
        return self.cells_after - self.cells_before

    @property
    def cells_change_percent(self) -> float:
        return 100.0 * self.cells_delta / self.cells_before if self.cells_before else 0.0


def compute_metrics(
    attributes: Sequence[str], rows: Sequence[Row], relations: Sequence[OutputRelation]
) -> Metrics:
    return Metrics(
        relations_before=1,
        rows_before=len(rows),
        cells_before=len(rows) * len(attributes),
        relations_after=len(relations),
        rows_after=sum(r.row_count for r in relations),
        cells_after=sum(r.cell_count for r in relations),
    )


# --- The whole pipeline ----------------------------------------------------------------------

@dataclass(frozen=True)
class LabResult:
    dataset: DemoDataset
    fd_checks: tuple[FDCheck, ...]
    report: AnalysisReport
    explanation: ReportExplanation
    relations: tuple[OutputRelation, ...]
    reconstruction: ReconstructionCheck
    metrics: Metrics

    def relation(self, name: str) -> OutputRelation:
        return next(r for r in self.relations if r.name == name)


def validate_dataset(dataset: DemoDataset) -> tuple[FDCheck, ...]:
    """Check shape, atomicity, row distinctness and the declared FDs.

    Returns the FD checks (all passing).

    Raises:
        ValueError: ragged or duplicate rows, or values that are not atomic.
        FDViolationError: a declared FD is false on the rows (nothing is
            normalized in that case).
    """
    if len(set(dataset.attributes)) != len(dataset.attributes):
        raise ValueError("duplicate column names")
    if len(set(dataset.rows)) != len(dataset.rows):
        raise ValueError("the rows are not distinct; a relation is a set of tuples")
    problems = atomicity_problems(dataset)
    if problems:
        raise ValueError(
            "values are not atomic (1NF): " + "; ".join(problems[:MAX_EXAMPLES])
            + (f" … and {len(problems) - MAX_EXAMPLES} more" if len(problems) > MAX_EXAMPLES else "")
        )
    checks = check_fds(dataset.attributes, dataset.rows, dataset.fds)
    if not all(c.holds for c in checks):
        raise FDViolationError(checks)
    return checks


def run_lab(dataset: DemoDataset) -> LabResult:
    """Validate ``dataset``, run the existing analysis, normalize and verify.

    Raises:
        FDViolationError: a declared FD does not hold on the data.
        ValueError: invalid data, or a declared key that is not a candidate key.
    """
    checks = validate_dataset(dataset)
    report = build_analysis_report(
        dataset.attributes,
        dataset.fds,
        dataset.relation_name,
        non_atomic_attributes=dataset.non_atomic,
    )
    if dataset.declared_key not in report.normalization.candidate_keys:
        raise ValueError(
            f"the declared key {format_set(dataset.declared_key)} is not a candidate key "
            f"under the declared FDs (engine found: "
            f"{', '.join(format_set(k) for k in report.normalization.candidate_keys)})"
        )
    explanation = explain_report(report)
    relations = build_output_relations(dataset, report, explanation)
    holding = {
        r.name
        for r in relations
        if any(k <= frozenset(r.attributes) for k in report.normalization.candidate_keys)
    }
    return LabResult(
        dataset=dataset,
        fd_checks=checks,
        report=report,
        explanation=explanation,
        relations=relations,
        reconstruction=reconstruct(dataset.attributes, dataset.rows, relations, prefer=holding),
        metrics=compute_metrics(dataset.attributes, dataset.rows, relations),
    )


# --- Filtering and export ----------------------------------------------------------------------

def filter_rows(rows: Iterable[Row], text: str) -> tuple[Row, ...]:
    """Rows with a cell containing ``text`` (case-insensitive); all rows if blank."""
    needle = text.strip().casefold()
    if not needle:
        return tuple(rows)
    return tuple(row for row in rows if any(needle in cell.casefold() for cell in row))


def rows_as_dicts(attributes: Sequence[str], rows: Iterable[Row]) -> list[dict[str, str]]:
    return [dict(zip(attributes, row)) for row in rows]


def to_csv(attributes: Sequence[str], rows: Iterable[Row]) -> bytes:
    """UTF-8 CSV with a header row and ``\\n`` line endings."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(attributes)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def relation_csv_name(relation: OutputRelation) -> str:
    return f"{relation.name}.csv"


def build_zip(relations: Iterable[OutputRelation]) -> bytes:
    """ZIP with one ``<name>.csv`` per output relation (fixed timestamps: reproducible)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for relation in relations:
            info = zipfile.ZipInfo(relation_csv_name(relation), date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, to_csv(relation.attributes, relation.rows))
    return buffer.getvalue()


def repetition_summary(check: FDCheck) -> str:
    """How often the FD's facts are repeated in the original table (from the data)."""
    repeated = check.rows - check.groups
    return (
        f"{check.rows} rows but only {check.groups} distinct "
        f"{'value' if check.groups == 1 else 'values'} of the determinant → "
        f"{repeated} repeated {'copy' if repeated == 1 else 'copies'} of these facts"
    )


def determinant_counts(rows: Iterable[Row], attributes: Sequence[str], names: Iterable[str]) -> Counter:
    """Occurrences of each value combination of ``names`` (helper for explanations)."""
    columns = _ordered(attributes, names)
    index = [tuple(attributes).index(a) for a in columns]
    return Counter(tuple(row[i] for i in index) for row in rows)
