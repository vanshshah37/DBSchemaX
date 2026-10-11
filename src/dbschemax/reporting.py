"""Analysis report generation (data, Markdown and JSON).

The builder only *gathers* results from the existing engine
(:func:`minimal_cover`, :func:`analyze_normalization`, :func:`synthesize_3nf`,
:func:`attribute_closure`); rendering functions only *format* an
:class:`AnalysisReport`. No algorithm is reimplemented here, and no CLI or UI
code lives here.

Every conclusion in a report is relative to the functional dependencies that
were supplied or derived from declared keys. See the "Limitations" section of
a generated report for what the tool does not know.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from dbschemax.closure import attribute_closure
from dbschemax.minimal_cover import minimal_cover
from dbschemax.models import FunctionalDependency, Relation, _to_attribute_set
from dbschemax.normalization import (
    NormalizationAnalysis,
    _fd_order,
    _set_order,
    analyze_normalization,
    resolve_attributes,
)
from dbschemax.sqlite_integration import (
    DerivedDependencies,
    TableInfo,
    derive_key_dependencies,
)
from dbschemax.synthesis import synthesize_3nf

REPORT_SCHEMA_VERSION = 1

SYNTHESIS_GUARANTEES: tuple[str, ...] = (
    "Every attribute of the relation appears in at least one synthesized schema.",
    "Every FD of the minimal cover lies within a single synthesized schema, so the "
    "decomposition preserves the supplied dependencies (dependency preservation).",
    "Each synthesized schema is in 3NF with respect to the projection of the "
    "supplied FDs.",
    "At least one synthesized schema contains a candidate key of the original "
    "relation (a key schema is added when none does).",
)

SYNTHESIS_CAVEATS: tuple[str, ...] = (
    "These guarantees hold for the supplied FD set only; they say nothing about "
    "dependencies that were not supplied.",
    "Lossless join is NOT verified by DBSchemaX. Textbook theory states that the "
    "standard algorithm including the candidate-key step yields a lossless-join "
    "decomposition, but this report does not check it, and being in 3NF does not by "
    "itself imply lossless join.",
    "3NF synthesis does not guarantee BCNF.",
)


class ReportSaveError(Exception):
    """Raised when a report cannot be written to the requested path."""


# --- Data structures -------------------------------------------------------

@dataclass(frozen=True)
class ViolationDetail:
    """One normal-form violation with a data-backed explanation.

    ``key`` is set only for 2NF violations (the candidate key whose proper
    subset ``determinant`` determines ``attribute``).
    """

    normal_form: str
    determinant: frozenset[str]
    attribute: str
    determinant_closure: frozenset[str]
    key: frozenset[str] | None
    explanation: str


@dataclass(frozen=True)
class AnalysisReport:
    """Everything the report shows. All collections are in deterministic order."""

    relation_name: str | None
    attributes: tuple[str, ...]
    table_info: TableInfo | None
    user_fds: tuple[FunctionalDependency, ...]
    declared_fds: tuple[FunctionalDependency, ...]
    derivation_notes: tuple[str, ...]
    analysis_fds: tuple[FunctionalDependency, ...]
    minimal_cover: tuple[FunctionalDependency, ...]
    normalization: NormalizationAnalysis
    non_atomic_attributes: tuple[str, ...] | None
    violation_details: tuple[ViolationDetail, ...]
    synthesized_schemas: tuple[frozenset[str], ...]
    keys_in_synthesis: tuple[frozenset[str], ...]
    limitations: tuple[str, ...]

    @property
    def candidate_key_represented(self) -> bool:
        """True if some synthesized schema contains a candidate key."""
        return bool(self.keys_in_synthesis)


# --- Formatting helpers ----------------------------------------------------

def format_set(attributes: Iterable[str]) -> str:
    """Render an attribute set as ``{A, B}``, or ``∅`` when empty."""
    names = sorted(attributes)
    return "{" + ", ".join(names) + "}" if names else "∅"


def _sorted_names(attributes: Iterable[str]) -> list[str]:
    return sorted(attributes)


# --- Building --------------------------------------------------------------

def _checked_fds(
    fds: Iterable[FunctionalDependency], what: str
) -> tuple[FunctionalDependency, ...]:
    items = tuple(fds)  # materialize once so one-shot iterators work
    for fd in items:
        if not isinstance(fd, FunctionalDependency):
            raise TypeError(f"{what} must contain FunctionalDependency, got {fd!r}")
    return tuple(sorted(set(items), key=_fd_order))


def _resolve_schema(
    attributes: Relation | Iterable[str] | None,
    table_name: str | None,
    table_info: TableInfo | None,
) -> tuple[frozenset[str], tuple[str, ...], str | None]:
    """Return (attribute set, display order, relation name)."""
    if table_name is not None:
        if not isinstance(table_name, str):
            raise TypeError("table_name must be a string")
        if not table_name.strip():
            raise ValueError("table_name must not be empty")
    if attributes is None:
        if table_info is None:
            raise ValueError("attributes are required unless table_info is provided")
        attribute_set = table_info.to_relation().attributes
    else:
        attribute_set = resolve_attributes(attributes)
        if table_info is not None and attribute_set != frozenset(table_info.column_names):
            raise ValueError("attributes do not match the columns of table_info")
    if table_info is not None and table_name not in (None, table_info.name):
        raise ValueError("table_name does not match table_info.name")
    order = table_info.column_names if table_info else tuple(sorted(attribute_set))
    if table_name is not None:
        name: str | None = table_name
    elif isinstance(attributes, Relation):
        name = attributes.name
    else:
        name = table_info.name if table_info else None
    return attribute_set, order, name


def _explain_fd_violation(
    normal_form: str,
    determinant: frozenset[str],
    attribute: str,
    closure: frozenset[str],
    attributes: frozenset[str],
    prime: frozenset[str],
) -> str:
    missing = format_set(attributes - closure)
    text = (
        f"{format_set(determinant)} -> {attribute} violates {normal_form}: "
        f"{format_set(determinant)} is not a superkey (its closure is "
        f"{format_set(closure)}, which lacks {missing})"
    )
    if normal_form == "3NF":
        return text + f" and {attribute} is not a prime attribute."
    text += "."
    if attribute in prime:
        text += f" It is allowed by 3NF because {attribute} is a prime attribute."
    return text


def _violation_details(
    analysis: NormalizationAnalysis, fds: tuple[FunctionalDependency, ...]
) -> tuple[ViolationDetail, ...]:
    details: list[ViolationDetail] = []
    for partial in analysis.partial_dependencies:
        closure = attribute_closure(partial.determinant, fds)
        details.append(
            ViolationDetail(
                normal_form="2NF",
                determinant=partial.determinant,
                attribute=partial.attribute,
                determinant_closure=closure,
                key=partial.key,
                explanation=(
                    f"{partial.attribute} is a non-prime attribute but depends on "
                    f"{format_set(partial.determinant)}, a proper subset of candidate "
                    f"key {format_set(partial.key)} (closure "
                    f"{format_set(closure)}); 2NF requires non-prime attributes to "
                    "depend on the whole key."
                ),
            )
        )
    for normal_form, violations in (
        ("3NF", analysis.violations_3nf),
        ("BCNF", analysis.violations_bcnf),
    ):
        for fd in violations:
            (attribute,) = fd.rhs
            closure = attribute_closure(fd.lhs, fds)
            details.append(
                ViolationDetail(
                    normal_form=normal_form,
                    determinant=fd.lhs,
                    attribute=attribute,
                    determinant_closure=closure,
                    key=None,
                    explanation=_explain_fd_violation(
                        normal_form,
                        fd.lhs,
                        attribute,
                        closure,
                        analysis.attributes,
                        analysis.prime_attributes,
                    ),
                )
            )
    return tuple(details)


def _limitations(
    table_info: TableInfo | None,
    user_fds: tuple[FunctionalDependency, ...],
    declared_fds: tuple[FunctionalDependency, ...],
    derivation_notes: tuple[str, ...],
    analysis_fds: tuple[FunctionalDependency, ...],
    non_atomic: tuple[str, ...] | None,
) -> tuple[str, ...]:
    lines = [
        "All results are computed only from the functional dependencies listed in "
        "this report. If that set is incomplete, the true candidate keys and "
        "normal-form status of the data may differ.",
        "DBSchemaX does not check FDs against stored data; an FD describes the "
        "intended meaning of the schema and cannot be proven from sample rows.",
        "2NF, 3NF and BCNF results are FD-based and assume attribute values are "
        "atomic. FD information alone cannot establish 1NF.",
    ]
    if non_atomic is None:
        lines.append("1NF status is unknown: no atomicity information was supplied.")
    elif non_atomic:
        lines.append(
            "1NF status comes from user-supplied atomicity information (non-atomic "
            f"attributes: {', '.join(non_atomic)}); it was not derived from FDs."
        )
    else:
        lines.append(
            "1NF status comes from user-supplied atomicity information (the caller "
            "asserted all attributes are atomic); it was not derived from FDs."
        )
    if user_fds:
        lines.append(f"{len(user_fds)} FD(s) were supplied explicitly by the user.")
    if declared_fds:
        lines.append(
            f"{len(declared_fds)} FD(s) were derived from declared SQLite key "
            "constraints (primary key, or non-partial UNIQUE with all columns NOT NULL)."
        )
    if not analysis_fds:
        lines.append(
            "No functional dependencies were supplied, so the only candidate key is "
            "the set of all attributes."
        )
    if table_info is not None:
        lines.append(
            "Schema metadata comes from the SQLite catalog (PRAGMA table_xinfo, "
            "index_list, foreign_key_list). Generated and hidden columns are included "
            "where SQLite exposes them. Foreign keys relate different tables and "
            "are not used as FDs within this relation; no FD is inferred from column "
            "names or row data."
        )
        if not user_fds:
            lines.append(
                "No user-supplied FDs: only dependencies implied by declared keys are "
                "used, so dependencies among non-key attributes (such as partial or "
                "transitive ones) cannot be detected unless supplied. Normal-form "
                "results are therefore likely optimistic."
            )
    lines.extend(derivation_notes)
    return tuple(lines)


def build_analysis_report(
    attributes: Relation | Iterable[str] | None,
    fds: Iterable[FunctionalDependency],
    table_name: str | None = None,
    *,
    non_atomic_attributes: Iterable[str] | None = None,
    table_info: TableInfo | None = None,
    declared_fds: Iterable[FunctionalDependency] = (),
    derivation_notes: Iterable[str] = (),
) -> AnalysisReport:
    """Run the full analysis and collect it into an :class:`AnalysisReport`.

    Args:
        attributes: A Relation or iterable of names; may be None only when
            ``table_info`` is given (its columns are used).
        fds: User-supplied FDs.
        table_name: Optional name; defaults to the Relation's or table's name.
        non_atomic_attributes: Optional atomicity information (see
            :func:`analyze_normalization`); None leaves 1NF unknown.
        table_info: Optional SQLite metadata to attach and display.
        declared_fds: FDs derived from declared constraints (kept separate from
            ``fds`` so the report can state where each FD came from).
        derivation_notes: Notes on skipped constraints, shown as limitations.

    Raises:
        TypeError / ValueError: invalid inputs, an empty relation, an FD that
            mentions attributes outside the relation, or metadata that does not
            match ``attributes``.
    """
    user_fds = _checked_fds(fds, "fds")
    declared = _checked_fds(declared_fds, "declared_fds")
    notes = tuple(derivation_notes)
    if not all(isinstance(note, str) for note in notes):
        raise TypeError("derivation_notes must contain strings")
    attribute_set, ordered, name = _resolve_schema(attributes, table_name, table_info)
    non_atomic = (
        None
        if non_atomic_attributes is None
        else tuple(sorted(_to_attribute_set(non_atomic_attributes, "non_atomic_attributes")))
    )
    analysis_fds = tuple(sorted(set(user_fds) | set(declared), key=_fd_order))

    normalization = analyze_normalization(attribute_set, analysis_fds, non_atomic)
    schemas = synthesize_3nf(attribute_set, analysis_fds)
    return AnalysisReport(
        relation_name=name,
        attributes=ordered,
        table_info=table_info,
        user_fds=user_fds,
        declared_fds=declared,
        derivation_notes=notes,
        analysis_fds=analysis_fds,
        minimal_cover=minimal_cover(analysis_fds),
        normalization=normalization,
        non_atomic_attributes=non_atomic,
        violation_details=_violation_details(normalization, analysis_fds),
        synthesized_schemas=schemas,
        keys_in_synthesis=tuple(
            key
            for key in normalization.candidate_keys
            if any(key <= schema for schema in schemas)
        ),
        limitations=_limitations(
            table_info, user_fds, declared, notes, analysis_fds, non_atomic
        ),
    )


def build_table_report(
    table_info: TableInfo,
    user_fds: Iterable[FunctionalDependency] = (),
    *,
    use_declared_keys: bool = True,
    non_atomic_attributes: Iterable[str] | None = None,
) -> AnalysisReport:
    """Report for an inspected SQLite table.

    FDs come from ``user_fds`` plus, if ``use_declared_keys`` is true, those
    soundly implied by declared keys (:func:`derive_key_dependencies`).
    """
    derived = (
        derive_key_dependencies(table_info)
        if use_declared_keys
        else DerivedDependencies((), ())
    )
    return build_analysis_report(
        None,
        user_fds,
        table_info=table_info,
        declared_fds=derived.dependencies,
        derivation_notes=derived.notes,
        non_atomic_attributes=non_atomic_attributes,
    )


# --- Shared presentation helpers -------------------------------------------

def _first_nf_status(report: AnalysisReport) -> str:
    first_nf = report.normalization.first_nf
    return "unknown" if first_nf is None else ("satisfied" if first_nf else "violated")


def _fd_sources(report: AnalysisReport, fd: FunctionalDependency) -> list[str]:
    sources = []
    if fd in report.user_fds:
        sources.append("user-supplied")
    if fd in report.declared_fds:
        sources.append("declared SQLite key")
    return sources


def _schema_source(report: AnalysisReport) -> str:
    if report.table_info is None:
        return "Attribute list supplied by the caller"
    return "SQLite table metadata (PRAGMA table_xinfo / index_list / foreign_key_list)"


# --- JSON ------------------------------------------------------------------

def _fd_dict(fd: FunctionalDependency) -> dict[str, Any]:
    return {"lhs": _sorted_names(fd.lhs), "rhs": _sorted_names(fd.rhs), "text": str(fd)}


def _table_info_dict(table: TableInfo) -> dict[str, Any]:
    return {
        "name": table.name,
        "columns": [
            {
                "name": c.name,
                "declared_type": c.declared_type,
                "not_null": c.not_null,
                "default_value": c.default_value,
                "primary_key_position": c.primary_key_position,
                "hidden": c.hidden,
                "is_generated": c.is_generated,
                "is_hidden": c.is_hidden,
                "is_rowid_alias": c.is_rowid_alias,
            }
            for c in table.columns
        ],
        "primary_key": list(table.primary_key),
        "nullable_primary_key_columns": list(table.nullable_primary_key_columns),
        "unique_constraints": [
            {
                "columns": list(u.columns),
                "index_name": u.index_name,
                "origin": u.origin,
                "partial": u.partial,
                "nullable_columns": list(u.nullable_columns),
                "enforces_key": u.enforces_key,
            }
            for u in table.unique_constraints
        ],
        "unidentified_unique_indexes": list(table.unidentified_unique_indexes),
        "foreign_keys": [
            {
                "columns": list(f.columns),
                "referenced_table": f.referenced_table,
                "referenced_columns": (
                    None if f.referenced_columns is None else list(f.referenced_columns)
                ),
                "on_update": f.on_update,
                "on_delete": f.on_delete,
            }
            for f in table.foreign_keys
        ],
    }


def report_to_dict(report: AnalysisReport) -> dict[str, Any]:
    """Convert a report to plain JSON-compatible data (lists, dicts, str, bool).

    Sets become sorted lists; collections keep the report's deterministic order.
    """
    norm = report.normalization
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "relation": {
            "name": report.relation_name,
            "attributes": list(report.attributes),
            "schema_source": _schema_source(report),
        },
        "table_metadata": (
            None if report.table_info is None else _table_info_dict(report.table_info)
        ),
        "functional_dependencies": {
            "user_supplied": [_fd_dict(fd) for fd in report.user_fds],
            "derived_from_declared_keys": [_fd_dict(fd) for fd in report.declared_fds],
            "analysis_set": [_fd_dict(fd) for fd in report.analysis_fds],
            "derivation_notes": list(report.derivation_notes),
        },
        "minimal_cover": [_fd_dict(fd) for fd in report.minimal_cover],
        "candidate_keys": {
            "keys": [_sorted_names(k) for k in norm.candidate_keys],
            "prime_attributes": _sorted_names(norm.prime_attributes),
            "non_prime_attributes": _sorted_names(norm.non_prime_attributes),
        },
        "normalization": {
            "first_nf": {
                "status": _first_nf_status(report),
                "assessable_from_fds": False,
                "non_atomic_attributes": (
                    None
                    if report.non_atomic_attributes is None
                    else list(report.non_atomic_attributes)
                ),
            },
            "second_nf": {"satisfied": norm.satisfies_2nf, "basis": "fd-based"},
            "third_nf": {"satisfied": norm.satisfies_3nf, "basis": "fd-based"},
            "bcnf": {"satisfied": norm.satisfies_bcnf, "basis": "fd-based"},
            "highest_fd_normal_form": norm.highest_fd_normal_form,
            "violations": [
                {
                    "normal_form": v.normal_form,
                    "determinant": _sorted_names(v.determinant),
                    "attribute": v.attribute,
                    "determinant_closure": _sorted_names(v.determinant_closure),
                    "key": None if v.key is None else _sorted_names(v.key),
                    "explanation": v.explanation,
                }
                for v in report.violation_details
            ],
        },
        "synthesis": {
            "schemas": [_sorted_names(s) for s in report.synthesized_schemas],
            "candidate_key_represented": report.candidate_key_represented,
            "candidate_keys_contained": [
                _sorted_names(k) for k in report.keys_in_synthesis
            ],
            "guarantees": list(SYNTHESIS_GUARANTEES),
            "caveats": list(SYNTHESIS_CAVEATS),
        },
        "limitations": list(report.limitations),
    }


def render_json_report(report: AnalysisReport, *, indent: int = 2) -> str:
    """Serialize a report to deterministic, valid JSON (sorted keys, UTF-8 text)."""
    return (
        json.dumps(report_to_dict(report), indent=indent, sort_keys=True, ensure_ascii=False)
        + "\n"
    )


# --- Markdown --------------------------------------------------------------

def _code(text: str) -> str:
    return f"`` {text} ``" if "`" in text else f"`{text}`"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _fd_text(fd: FunctionalDependency) -> str:
    return _code(_cell(str(fd)))


def _schema_overview(report: AnalysisReport) -> list[str]:
    table = report.table_info
    name = report.relation_name or "(unnamed)"
    if table is None:
        primary_key = "not available (no database metadata supplied)"
    elif table.primary_key:
        primary_key = _code("(" + ", ".join(table.primary_key) + ")")
    else:
        primary_key = "none declared"
    lines = [
        "## 1. Schema Overview",
        "",
        f"- **Relation:** {_code(name)}",
        f"- **Attributes ({len(report.attributes)}):** "
        + _code("{" + ", ".join(report.attributes) + "}"),
        f"- **Primary key:** {primary_key}",
        f"- **Schema source:** {_schema_source(report)}",
        f"- **FD source:** {len(report.user_fds)} user-supplied; "
        f"{len(report.declared_fds)} derived from declared SQLite key constraints",
    ]
    if table is None:
        return lines + [""]
    lines += ["", "### Columns", ""]
    lines += _table(
        ["#", "Column", "Type", "NOT NULL", "Default", "PK position", "Kind"],
        [
            [
                str(i),
                _code(_cell(c.name)),
                _cell(c.declared_type) or "—",
                "yes" if c.not_null else "no",
                _code(_cell(c.default_value)) if c.default_value is not None else "—",
                str(c.primary_key_position) if c.is_primary_key else "—",
                (
                    "rowid alias" if c.is_rowid_alias
                    else "generated stored" if c.hidden == 3
                    else "generated virtual" if c.hidden == 2
                    else "hidden" if c.hidden == 1
                    else "ordinary"
                ),
            ]
            for i, c in enumerate(table.columns, start=1)
        ],
    )
    lines += ["", "### Unique constraints", ""]
    if table.unique_constraints:
        lines += _table(
            ["Columns", "Origin", "Partial", "Nullable columns", "Enforces key"],
            [
                [
                    _code(_cell(", ".join(u.columns))),
                    u.origin,
                    "yes" if u.partial else "no",
                    _cell(", ".join(u.nullable_columns)) or "—",
                    "yes" if u.enforces_key else "no",
                ]
                for u in table.unique_constraints
            ],
        )
    else:
        lines.append("_None identified._")
    lines += ["", "### Foreign keys", ""]
    if table.foreign_keys:
        lines += _table(
            ["Columns", "References", "ON UPDATE", "ON DELETE"],
            [
                [
                    _code(_cell(", ".join(f.columns))),
                    _code(
                        _cell(
                            f"{f.referenced_table}("
                            + (
                                "primary key"
                                if f.referenced_columns is None
                                else ", ".join(f.referenced_columns)
                            )
                            + ")"
                        )
                    ),
                    f.on_update,
                    f.on_delete,
                ]
                for f in table.foreign_keys
            ],
        )
        lines += [
            "",
            "_Foreign keys are shown for context only; they are not functional "
            "dependencies within this relation._",
        ]
    else:
        lines.append("_None._")
    return lines + [""]


def _fd_section(report: AnalysisReport) -> list[str]:
    lines = ["## 2. Functional Dependencies", "", "### 2.1 Input FDs (analysis set)", ""]
    if report.analysis_fds:
        lines += _table(
            ["#", "Functional dependency", "Source"],
            [
                [str(i), _fd_text(fd), ", ".join(_fd_sources(report, fd))]
                for i, fd in enumerate(report.analysis_fds, start=1)
            ],
        )
    else:
        lines.append("_No functional dependencies were supplied._")
    lines += ["", "### 2.2 Minimal (canonical) cover", ""]
    if report.minimal_cover:
        lines += [f"- {_fd_text(fd)}" for fd in report.minimal_cover]
    else:
        lines.append("_Empty: there are no non-trivial dependencies._")
    lines += [
        "",
        "_A minimal cover is not unique; this is one valid cover, chosen "
        "deterministically._",
        "",
    ]
    return lines


def _keys_section(report: AnalysisReport) -> list[str]:
    norm = report.normalization
    lines = ["## 3. Candidate Keys", ""]
    lines += [f"- **Candidate key:** {_code(_cell(format_set(k)))}" for k in norm.candidate_keys]
    lines += [
        f"- **Prime attributes:** {_code(_cell(format_set(norm.prime_attributes)))}",
        f"- **Non-prime attributes:** {_code(_cell(format_set(norm.non_prime_attributes)))}",
    ]
    if frozenset() in norm.candidate_keys:
        lines.append(
            "- The empty set is a candidate key: every attribute is determined by ∅."
        )
    return lines + [""]


def _normalization_section(report: AnalysisReport) -> list[str]:
    norm = report.normalization
    first = norm.first_nf
    if first is None:
        first_row = [
            "1NF",
            "**Unknown**",
            "Not assessable from FD information; no atomicity information supplied.",
        ]
    elif first:
        first_row = ["1NF", "Satisfied", "As asserted by the caller (all attributes atomic)."]
    else:
        first_row = [
            "1NF",
            "Violated",
            "Caller reported non-atomic attributes: "
            + ", ".join(report.non_atomic_attributes or ()),
        ]

    def status(ok: bool) -> str:
        return "Satisfied" if ok else "**Violated**"

    lines = ["## 4. Normalization Analysis", ""]
    lines += _table(
        ["Normal form", "Status", "Basis"],
        [
            first_row,
            ["2NF", status(norm.satisfies_2nf), "FD-based, for the supplied FDs"],
            ["3NF", status(norm.satisfies_3nf), "FD-based, for the supplied FDs"],
            ["BCNF", status(norm.satisfies_bcnf), "FD-based, for the supplied FDs"],
        ],
    )
    lines += [
        "",
        f"**Highest FD-based normal form (2NF/3NF/BCNF):** {norm.highest_fd_normal_form}. "
        "This says nothing about 1NF.",
        "",
        "### Violations",
        "",
    ]
    if report.violation_details:
        lines += [f"- **{v.normal_form}**: {v.explanation}" for v in report.violation_details]
    else:
        lines.append("No 2NF, 3NF or BCNF violations were found for the supplied FDs.")
    return lines + [""]


def _synthesis_section(report: AnalysisReport) -> list[str]:
    lines = ["## 5. 3NF Synthesis", ""]
    lines += _table(
        ["Schema", "Attributes"],
        [
            [f"R{i}", _code(_cell(format_set(s)))]
            for i, s in enumerate(report.synthesized_schemas, start=1)
        ],
    )
    if report.candidate_key_represented:
        keys = ", ".join(_code(_cell(format_set(k))) for k in report.keys_in_synthesis)
        represented = f"Yes: contained in a synthesized schema ({keys})."
    else:
        represented = "No candidate key of the original relation is contained in a schema."
    lines += ["", f"**Original candidate key represented:** {represented}", ""]
    lines += ["### Guarantees of the standard algorithm", ""]
    lines += [f"- {text}" for text in SYNTHESIS_GUARANTEES]
    lines += ["", "### Assumptions and caveats", ""]
    lines += [f"- {text}" for text in SYNTHESIS_CAVEATS]
    return lines + [""]


def render_markdown_report(report: AnalysisReport) -> str:
    """Render a report as Markdown (headings, tables, set notation ``{A, B}``)."""
    title = report.relation_name or "(unnamed relation)"
    lines = [f"# DBSchemaX Analysis Report: {_code(title)}", ""]
    lines += _schema_overview(report)
    lines += _fd_section(report)
    lines += _keys_section(report)
    lines += _normalization_section(report)
    lines += _synthesis_section(report)
    lines += ["## 6. Limitations and Assumptions", ""]
    lines += [f"- {text}" for text in report.limitations]
    return "\n".join(lines) + "\n"


# --- Saving ----------------------------------------------------------------

def _write_text(path: str | os.PathLike[str], text: str, overwrite: bool) -> Path:
    target = Path(path)
    try:
        with open(target, "w" if overwrite else "x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise ReportSaveError(
            f"refusing to overwrite existing file {str(target)!r}; pass overwrite=True"
        ) from exc
    except OSError as exc:
        raise ReportSaveError(f"cannot write report to {str(target)!r}: {exc}") from exc
    return target


def save_markdown_report(
    report: AnalysisReport, path: str | os.PathLike[str], *, overwrite: bool = False
) -> Path:
    """Write the Markdown report to ``path`` (UTF-8).

    Existing files are never overwritten unless ``overwrite=True``; missing
    parent directories are not created.

    Raises:
        ReportSaveError: file exists, directory missing, or any OS error.
    """
    return _write_text(path, render_markdown_report(report), overwrite)


def save_json_report(
    report: AnalysisReport, path: str | os.PathLike[str], *, overwrite: bool = False
) -> Path:
    """Write the JSON report to ``path`` (UTF-8); same rules as Markdown."""
    return _write_text(path, render_json_report(report), overwrite)
