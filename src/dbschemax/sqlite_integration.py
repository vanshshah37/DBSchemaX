"""Read-only SQLite schema inspection.

Reads table metadata (columns, primary key, unique constraints, foreign keys)
from an existing SQLite database and converts it to the project's
:class:`Relation`. Nothing here writes to the database: connections are opened
with ``mode=ro`` and all identifiers are passed as bound parameters to the
``pragma_*`` table-valued functions, never interpolated into SQL.

What SQLite metadata can and cannot tell you
--------------------------------------------
Schema metadata is NOT a functional-dependency set. Only enforced key
constraints soundly imply FDs, and :func:`derive_key_dependencies` derives just
those:

* a declared PRIMARY KEY determines every other column;
* a non-partial UNIQUE constraint whose columns are all NOT NULL does too.

A UNIQUE constraint with nullable columns is NOT used, because SQLite treats
NULLs as distinct inside UNIQUE constraints (several rows may share a NULL).
Foreign keys relate different tables and are never turned into FDs. No FD is
inferred from column names or sample rows; any other FD must be supplied by the
caller (see :func:`dbschemax.parser.parse_fd`). If the table has no primary
key, none is invented.

Known limits: unique indexes on expressions cannot be represented as plain
attribute sets and are listed by name only. Column metadata is obtained using
PRAGMA table_xinfo, which includes generated and hidden columns.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from dbschemax.models import FunctionalDependency, Relation
from dbschemax.normalization import _fd_order


class SQLiteInspectionError(Exception):
    """Raised when a database or table cannot be inspected."""



@dataclass(frozen=True)
class ColumnInfo:
    """One column as reported by PRAGMA table_xinfo.

    hidden: 0 = ordinary, 1 = hidden virtual-table column,
    2 = generated VIRTUAL, 3 = generated STORED.
    """

    name: str
    declared_type: str
    not_null: bool
    default_value: str | None
    primary_key_position: int
    hidden: int = 0
    is_rowid_alias: bool = False

    @property
    def is_primary_key(self) -> bool:
        return self.primary_key_position > 0

    @property
    def is_generated(self) -> bool:
        return self.hidden in (2, 3)

    @property
    def is_hidden(self) -> bool:
        return self.hidden == 1



@dataclass(frozen=True)
class UniqueConstraintInfo:
    """A UNIQUE constraint or unique index over plain columns.

    ``origin`` is ``"constraint"`` (declared UNIQUE) or ``"index"`` (CREATE
    UNIQUE INDEX). ``nullable_columns`` lists columns that may hold NULL.
    """

    columns: tuple[str, ...]
    index_name: str
    origin: str
    partial: bool
    nullable_columns: tuple[str, ...]

    @property
    def enforces_key(self) -> bool:
        """True only if it constrains every row and no column can be NULL."""
        return not self.partial and not self.nullable_columns


@dataclass(frozen=True)
class ForeignKeyInfo:
    """A foreign key. ``referenced_columns`` is None when the reference is
    implicit (it targets the parent table's primary key)."""

    columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str, ...] | None
    on_update: str
    on_delete: str


@dataclass(frozen=True)
class TableInfo:
    """Metadata of one table. Column and primary-key order are SQLite's.

    ``unique_constraints`` are sorted by columns then index name (SQLite lists
    indexes in reverse creation order, which carries no meaning).
    ``unidentified_unique_indexes`` names unique indexes that are not plain
    column sets (e.g. expression indexes).
    """

    name: str
    columns: tuple[ColumnInfo, ...]
    primary_key: tuple[str, ...]
    unique_constraints: tuple[UniqueConstraintInfo, ...]
    foreign_keys: tuple[ForeignKeyInfo, ...]
    unidentified_unique_indexes: tuple[str, ...] = ()

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    @property
    def nullable_primary_key_columns(self) -> tuple[str, ...]:
        """Primary-key columns that may hold NULL (see module docstring)."""
        by_name = {column.name: column for column in self.columns}
        return tuple(
            name
            for name in self.primary_key
            if _may_hold_null(by_name[name], self.primary_key)
        )

    def to_relation(self) -> Relation:
        """Convert to the core :class:`Relation`.

        Raises:
            ValueError: if a column name is empty or has surrounding
                whitespace (the core model strips names, which could merge
                distinct columns).
        """
        for name in self.column_names:
            if not name.strip() or name != name.strip():
                raise ValueError(
                    f"column name {name!r} is empty or has surrounding whitespace "
                    "and cannot be represented exactly as a Relation attribute"
                )
        return Relation(self.name, set(self.column_names))


@dataclass(frozen=True)
class DatabaseInfo:
    """All user tables of a database, sorted by table name."""

    path: str
    tables: tuple[TableInfo, ...]


@dataclass(frozen=True)
class DerivedDependencies:
    """FDs soundly implied by declared keys, plus notes on what was skipped."""

    dependencies: tuple[FunctionalDependency, ...]
    notes: tuple[str, ...]


# --- Connection handling ---------------------------------------------------

def _resolve_path(database_path: str | os.PathLike[str]) -> Path:
    if not isinstance(database_path, (str, os.PathLike)):
        raise TypeError(
            f"database_path must be a str or path-like, got {type(database_path).__name__}"
        )
    path = Path(database_path)
    if not path.exists():
        raise SQLiteInspectionError(f"database file not found: {str(path)!r}")
    if not path.is_file():
        raise SQLiteInspectionError(f"database path is not a file: {str(path)!r}")
    return path


@contextmanager
def _open_database(
    database_path: str | os.PathLike[str],
) -> Iterator[sqlite3.Connection]:
    """Open an existing database read-only; always close; wrap SQLite errors."""
    path = _resolve_path(database_path)
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise SQLiteInspectionError(f"cannot open {str(path)!r}: {exc}") from exc
    try:
        yield connection
    except sqlite3.Error as exc:
        raise SQLiteInspectionError(
            f"cannot read SQLite database {str(path)!r}: {exc}"
        ) from exc
    finally:
        connection.close()


# --- Catalog queries -------------------------------------------------------

def _list_tables(connection: sqlite3.Connection) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name"
    ).fetchall()
    return tuple(row[0] for row in rows)


def _canonical_table_name(connection: sqlite3.Connection, table_name: str) -> str:
    """Resolve ``table_name`` (case-insensitive) to the stored name."""
    if not isinstance(table_name, str):
        raise TypeError(f"table_name must be a string, got {type(table_name).__name__}")
    if not table_name.strip():
        raise ValueError("table_name must not be empty")
    if table_name.lower().startswith("sqlite_"):
        raise SQLiteInspectionError(
            f"{table_name!r} is an SQLite internal table, not a user table"
        )
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ? COLLATE NOCASE",
        (table_name,),
    ).fetchone()
    if row is None:
        available = ", ".join(_list_tables(connection)) or "(none)"
        raise SQLiteInspectionError(
            f"table {table_name!r} not found; available tables: {available}"
        )
    return row[0]



def _load_columns(connection: sqlite3.Connection, table: str) -> tuple[ColumnInfo, ...]:
    rows = connection.execute(
        'SELECT name, type, "notnull", dflt_value, pk, hidden '
        "FROM pragma_table_xinfo(?) ORDER BY cid",
        (table,),
    ).fetchall()

    # A genuine INTEGER PRIMARY KEY rowid alias has no separate primary-key
    # index. INTEGER PRIMARY KEY DESC is an SQLite exception and has an index.
    primary_rows = [row for row in rows if row[4] > 0]
    index_origins = connection.execute(
        "SELECT origin FROM pragma_index_list(?)",
        (table,),
    ).fetchall()
    has_primary_key_index = any(
        origin == "pk" for (origin,) in index_origins
    )

    rowid_alias_name = None
    if (
        len(primary_rows) == 1
        and (primary_rows[0][1] or "").upper() == "INTEGER"
        and not has_primary_key_index
    ):
        rowid_alias_name = primary_rows[0][0]

    return tuple(
        ColumnInfo(
            name=name,
            declared_type=declared_type or "",
            not_null=bool(not_null),
            default_value=default,
            primary_key_position=pk,
            hidden=hidden,
            is_rowid_alias=(name == rowid_alias_name),
        )
        for name, declared_type, not_null, default, pk, hidden in rows
    )


def _may_hold_null(column: ColumnInfo, primary_key: tuple[str, ...]) -> bool:
    """Return whether this column may contain NULL under SQLite's schema."""
    if column.not_null:
        return False
    return not (
        column.is_rowid_alias and primary_key == (column.name,)
    )



def _load_unique_constraints(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[ColumnInfo, ...],
    primary_key: tuple[str, ...],
) -> tuple[tuple[UniqueConstraintInfo, ...], tuple[str, ...]]:
    by_name = {column.name: column for column in columns}
    constraints: list[UniqueConstraintInfo] = []
    unidentified: list[str] = []
    index_rows = connection.execute(
        'SELECT name, origin, partial FROM pragma_index_list(?) WHERE "unique" = 1',
        (table,),
    ).fetchall()
    for index_name, origin, partial in index_rows:
        if origin == "pk":  # the primary key's own index
            continue
        key_rows = connection.execute(
            "SELECT cid, name FROM pragma_index_xinfo(?) WHERE key = 1 ORDER BY seqno",
            (index_name,),
        ).fetchall()
        if any(cid < 0 for cid, _ in key_rows):  # expression index
            unidentified.append(index_name)
            continue
        names = tuple(name for _, name in key_rows)
        constraints.append(
            UniqueConstraintInfo(
                columns=names,
                index_name=index_name,
                origin="constraint" if origin == "u" else "index",
                partial=bool(partial),
                nullable_columns=tuple(
                    n for n in names if _may_hold_null(by_name[n], primary_key)
                ),
            )
        )
    constraints.sort(key=lambda c: (c.columns, c.index_name))
    return tuple(constraints), tuple(sorted(unidentified))


def _load_foreign_keys(
    connection: sqlite3.Connection, table: str
) -> tuple[ForeignKeyInfo, ...]:
    rows = connection.execute(
        'SELECT id, "table", "from", "to", on_update, on_delete '
        "FROM pragma_foreign_key_list(?) ORDER BY id, seq",
        (table,),
    ).fetchall()
    grouped: dict[int, list[tuple]] = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(row)
    result = []
    for _, group in sorted(grouped.items()):
        targets = tuple(row[3] for row in group)
        result.append(
            ForeignKeyInfo(
                columns=tuple(row[2] for row in group),
                referenced_table=group[0][1],
                referenced_columns=None if all(t is None for t in targets) else targets,
                on_update=group[0][4],
                on_delete=group[0][5],
            )
        )
    return tuple(result)


def _load_table(connection: sqlite3.Connection, table: str) -> TableInfo:
    columns = _load_columns(connection, table)
    primary_key = tuple(
        column.name
        for column in sorted(
            (c for c in columns if c.is_primary_key),
            key=lambda c: c.primary_key_position,
        )
    )
    unique, unidentified = _load_unique_constraints(
        connection, table, columns, primary_key
    )
    return TableInfo(
        name=table,
        columns=columns,
        primary_key=primary_key,
        unique_constraints=unique,
        foreign_keys=_load_foreign_keys(connection, table),
        unidentified_unique_indexes=unidentified,
    )


# --- Public API ------------------------------------------------------------

def list_user_tables(database_path: str | os.PathLike[str]) -> tuple[str, ...]:
    """Return user table names (sorted), excluding SQLite internal tables.

    Raises:
        TypeError: ``database_path`` is not a str or path-like.
        SQLiteInspectionError: missing path, not a file, or not a database.
    """
    with _open_database(database_path) as connection:
        return _list_tables(connection)


def inspect_sqlite_table(
    database_path: str | os.PathLike[str], table_name: str
) -> TableInfo:
    """Inspect one table (name matched case-insensitively, as SQLite does).

    Raises:
        TypeError / ValueError: invalid ``table_name``.
        SQLiteInspectionError: unreadable database, internal table name, or
            unknown table.
    """
    with _open_database(database_path) as connection:
        return _load_table(connection, _canonical_table_name(connection, table_name))


def inspect_sqlite_database(database_path: str | os.PathLike[str]) -> DatabaseInfo:
    """Inspect every user table. An empty database gives ``tables == ()``."""
    with _open_database(database_path) as connection:
        tables = tuple(_load_table(connection, t) for t in _list_tables(connection))
    return DatabaseInfo(path=str(database_path), tables=tables)


def _format_columns(columns: tuple[str, ...]) -> str:
    return ", ".join(columns)


def derive_key_dependencies(table: TableInfo) -> DerivedDependencies:
    """Derive the FDs soundly implied by the table's declared keys.

    * PRIMARY KEY ``K`` gives ``K -> (all other columns)``. If a key column may
      hold NULL (SQLite allows this for non-INTEGER primary keys of ordinary
      tables unless NOT NULL is declared), the FD is still derived but a note
      says it assumes non-NULL key values.
    * A UNIQUE constraint/index gives the same kind of FD only if it is not
      partial and none of its columns can be NULL; otherwise it is skipped
      with a note.
    * No primary key: nothing is invented.

    Foreign keys, column names and row data are never used.
    """
    attributes = frozenset(table.column_names)
    dependencies: set[FunctionalDependency] = set()
    notes: list[str] = []

    if not table.primary_key:
        notes.append("No primary key is declared; none was invented.")
    else:
        rest = attributes - frozenset(table.primary_key)
        if rest:
            dependencies.add(FunctionalDependency(frozenset(table.primary_key), rest))
        else:
            notes.append(
                "The primary key covers every column, so it implies no non-trivial "
                "dependency."
            )
        nullable = table.nullable_primary_key_columns
        if nullable:
            notes.append(
                f"Primary key ({_format_columns(table.primary_key)}) has column(s) "
                f"that may hold NULL ({_format_columns(nullable)}); SQLite does not "
                "enforce NOT NULL on such keys, so the derived FD assumes non-NULL "
                "key values."
            )

    for unique in table.unique_constraints:
        label = f"UNIQUE ({_format_columns(unique.columns)}) [{unique.index_name}]"
        if unique.partial:
            notes.append(
                f"{label} was not used: it is a partial index, so it does not "
                "constrain every row."
            )
        elif unique.nullable_columns:
            notes.append(
                f"{label} was not used: column(s) "
                f"{_format_columns(unique.nullable_columns)} may hold NULL, and SQLite "
                "treats NULLs as distinct in UNIQUE constraints, so it does not "
                "behave as a key."
            )
        else:
            rest = attributes - frozenset(unique.columns)
            if rest:
                dependencies.add(FunctionalDependency(frozenset(unique.columns), rest))

    for name in table.unidentified_unique_indexes:
        notes.append(
            f"Unique index [{name}] is not a plain column set (e.g. an expression "
            "index) and was not used."
        )
    return DerivedDependencies(
        dependencies=tuple(sorted(dependencies, key=_fd_order)),
        notes=tuple(notes),
    )
