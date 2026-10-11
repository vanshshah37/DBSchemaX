"""Tests for read-only SQLite inspection. All databases are temporary files."""

import dataclasses
import sqlite3
from pathlib import Path

import pytest

from dbschemax.candidate_keys import find_candidate_keys
from dbschemax.models import FunctionalDependency, Relation
from dbschemax.normalization import analyze_normalization
from dbschemax.parser import parse_fd
from dbschemax.sqlite_integration import (
    DatabaseInfo,
    SQLiteInspectionError,
    TableInfo,
    derive_key_dependencies,
    inspect_sqlite_database,
    inspect_sqlite_table,
    list_user_tables,
)


def make_db(tmp_path: Path, script: str, name: str = "test.db") -> Path:
    path = tmp_path / name
    connection = sqlite3.connect(path)
    try:
        connection.executescript(script)
        connection.commit()
    finally:
        connection.close()
    return path


def fd(text: str) -> FunctionalDependency:
    return parse_fd(text)


SCHEMA = """
CREATE TABLE no_pk (a INT, b TEXT);
CREATE TABLE one_pk (id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT 'x', score REAL DEFAULT 0);
CREATE TABLE comp (p TEXT, q TEXT, r INT, PRIMARY KEY (q, p));
CREATE TABLE uniq (id INTEGER PRIMARY KEY, email TEXT UNIQUE, code TEXT NOT NULL UNIQUE,
                   a INT NOT NULL, b INT NOT NULL, UNIQUE (a, b));
CREATE UNIQUE INDEX uniq_partial ON uniq(email) WHERE a > 0;
CREATE UNIQUE INDEX uniq_expr ON uniq(lower(code));
CREATE TABLE parent (x INT, y INT, PRIMARY KEY (x, y));
CREATE TABLE child (id INTEGER PRIMARY KEY, px INT, py INT, other INT REFERENCES parent,
                    FOREIGN KEY (px, py) REFERENCES parent (x, y) ON DELETE CASCADE ON UPDATE SET NULL);
CREATE TABLE seq_t (id INTEGER PRIMARY KEY AUTOINCREMENT, v);
CREATE VIEW some_view AS SELECT a FROM no_pk;
"""


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    return make_db(tmp_path, SCHEMA)


# --- Listing ---------------------------------------------------------------

def test_list_user_tables_sorted_and_excludes_internal_and_views(db: Path) -> None:
    tables = list_user_tables(db)
    assert tables == ("child", "comp", "no_pk", "one_pk", "parent", "seq_t", "uniq")
    assert "sqlite_sequence" in {  # internal table really exists
        row[0] for row in sqlite3.connect(db).execute("SELECT name FROM sqlite_master")
    }
    assert not any(name.startswith("sqlite_") for name in tables)
    assert "some_view" not in tables


def test_empty_database_zero_byte_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    path.touch()
    assert list_user_tables(path) == ()
    assert inspect_sqlite_database(path) == DatabaseInfo(path=str(path), tables=())
    with pytest.raises(SQLiteInspectionError, match="not found"):
        inspect_sqlite_table(path, "anything")


def test_database_with_only_dropped_table_is_empty(tmp_path: Path) -> None:
    path = make_db(tmp_path, "CREATE TABLE t (a); DROP TABLE t;")
    assert inspect_sqlite_database(path).tables == ()


def test_inspect_database_multiple_tables(db: Path) -> None:
    info = inspect_sqlite_database(db)
    assert [t.name for t in info.tables] == list(list_user_tables(db))
    assert all(isinstance(t, TableInfo) for t in info.tables)

def test_integer_primary_key_desc_is_not_rowid_alias(tmp_path: Path) -> None:
    path = make_db(
        tmp_path,
        """
        CREATE TABLE regular (
            id INTEGER PRIMARY KEY, payload TEXT
        );
        CREATE TABLE desc_pk (
            id INTEGER PRIMARY KEY DESC, payload TEXT
        );
        INSERT INTO desc_pk VALUES (NULL, 'first'), (NULL, 'second');
        """,
    )

    regular = inspect_sqlite_table(path, "regular")
    assert regular.nullable_primary_key_columns == ()
    assert regular.columns[0].is_rowid_alias

    desc_pk = inspect_sqlite_table(path, "desc_pk")
    assert desc_pk.nullable_primary_key_columns == ("id",)
    assert not desc_pk.columns[0].is_rowid_alias
    assert any(
        "may hold NULL" in note
        for note in derive_key_dependencies(desc_pk).notes
    )


def test_generated_columns_are_inspected(tmp_path: Path) -> None:
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

    assert table.column_names == ("price", "quantity", "total")
    total = table.columns[2]
    assert total.hidden == 3
    assert total.is_generated
    assert not total.is_hidden
    assert table.to_relation().attributes == frozenset(
        {"price", "quantity", "total"}
    )

    
# --- Columns and keys ------------------------------------------------------

def test_column_metadata(db: Path) -> None:
    table = inspect_sqlite_table(db, "one_pk")
    assert table.column_names == ("id", "name", "score")
    id_col, name_col, score_col = table.columns
    assert (id_col.declared_type, id_col.primary_key_position) == ("TEXT", 1)
    assert id_col.is_primary_key and not name_col.is_primary_key
    assert name_col.not_null and name_col.default_value == "'x'"
    assert not score_col.not_null and score_col.default_value == "0"
    assert id_col.default_value is None


def test_untyped_column_has_empty_declared_type(tmp_path: Path) -> None:
    path = make_db(tmp_path, "CREATE TABLE t (a, b INT);")
    assert inspect_sqlite_table(path, "t").columns[0].declared_type == ""


def test_no_primary_key_is_not_invented(db: Path) -> None:
    table = inspect_sqlite_table(db, "no_pk")
    assert table.primary_key == ()
    assert all(not c.is_primary_key for c in table.columns)


def test_single_column_primary_key(db: Path) -> None:
    assert inspect_sqlite_table(db, "one_pk").primary_key == ("id",)


def test_composite_primary_key_uses_declared_key_order(db: Path) -> None:
    table = inspect_sqlite_table(db, "comp")
    assert table.column_names == ("p", "q", "r")  # column order preserved
    assert table.primary_key == ("q", "p")  # key order is the PRIMARY KEY order
    positions = {c.name: c.primary_key_position for c in table.columns}
    assert positions == {"p": 2, "q": 1, "r": 0}


def test_primary_key_nullability(tmp_path: Path) -> None:
    path = make_db(
        tmp_path,
        """
        CREATE TABLE alias (i INTEGER PRIMARY KEY, j);
        CREATE TABLE text_pk (a TEXT PRIMARY KEY, b);
        CREATE TABLE nn_pk (a TEXT NOT NULL PRIMARY KEY, b);
        CREATE TABLE wr (a TEXT, b TEXT, c, PRIMARY KEY (a, b)) WITHOUT ROWID;
        CREATE TABLE comp_int (a INTEGER, b INTEGER, PRIMARY KEY (a, b));
        """,
    )
    assert inspect_sqlite_table(path, "alias").nullable_primary_key_columns == ()
    assert inspect_sqlite_table(path, "text_pk").nullable_primary_key_columns == ("a",)
    assert inspect_sqlite_table(path, "nn_pk").nullable_primary_key_columns == ()
    assert inspect_sqlite_table(path, "wr").nullable_primary_key_columns == ()
    assert inspect_sqlite_table(path, "comp_int").nullable_primary_key_columns == ("a", "b")


# --- Unique constraints ----------------------------------------------------

def test_unique_constraints(db: Path) -> None:
    table = inspect_sqlite_table(db, "uniq")
    by_cols = {(u.columns, u.index_name): u for u in table.unique_constraints}
    columns = [u.columns for u in table.unique_constraints]
    assert columns == sorted(columns)  # deterministic order
    assert ("a", "b") in columns and ("code",) in columns and ("email",) in columns

    ab = next(u for u in table.unique_constraints if u.columns == ("a", "b"))
    assert ab.origin == "constraint" and not ab.partial
    assert ab.nullable_columns == () and ab.enforces_key

    code = next(u for u in table.unique_constraints if u.columns == ("code",))
    assert code.enforces_key
    assert len(by_cols) == len(table.unique_constraints)


def test_nullable_unique_does_not_enforce_key(db: Path) -> None:
    table = inspect_sqlite_table(db, "uniq")
    email = [u for u in table.unique_constraints if u.columns == ("email",)]
    plain = next(u for u in email if not u.partial)
    assert plain.nullable_columns == ("email",) and not plain.enforces_key


def test_partial_unique_index_flagged(db: Path) -> None:
    table = inspect_sqlite_table(db, "uniq")
    partial = next(u for u in table.unique_constraints if u.index_name == "uniq_partial")
    assert partial.partial and partial.origin == "index" and not partial.enforces_key


def test_expression_index_listed_but_not_a_column_set(db: Path) -> None:
    table = inspect_sqlite_table(db, "uniq")
    assert table.unidentified_unique_indexes == ("uniq_expr",)
    assert all(u.index_name != "uniq_expr" for u in table.unique_constraints)


def test_primary_key_index_not_reported_as_unique_constraint(db: Path) -> None:
    assert inspect_sqlite_table(db, "comp").unique_constraints == ()
    assert inspect_sqlite_table(db, "one_pk").unique_constraints == ()


# --- Foreign keys ----------------------------------------------------------

def test_foreign_keys(db: Path) -> None:
    table = inspect_sqlite_table(db, "child")
    assert len(table.foreign_keys) == 2
    composite = next(f for f in table.foreign_keys if f.columns == ("px", "py"))
    assert composite.referenced_table == "parent"
    assert composite.referenced_columns == ("x", "y")
    assert composite.on_delete == "CASCADE" and composite.on_update == "SET NULL"
    implicit = next(f for f in table.foreign_keys if f.columns == ("other",))
    assert implicit.referenced_columns is None  # implicit reference to parent's PK
    assert implicit.on_delete == "NO ACTION"


def test_table_without_foreign_keys(db: Path) -> None:
    assert inspect_sqlite_table(db, "no_pk").foreign_keys == ()


# --- Data edge cases and read-only behaviour --------------------------------

def test_empty_and_null_filled_tables(tmp_path: Path) -> None:
    path = make_db(
        tmp_path,
        """
        CREATE TABLE empty_t (a INT, b INT, UNIQUE (a));
        CREATE TABLE nulls_t (a INT, b INT, UNIQUE (a));
        INSERT INTO nulls_t VALUES (NULL, 1), (NULL, 2), (NULL, NULL);
        """,
    )
    empty = inspect_sqlite_table(path, "empty_t")
    nulls = inspect_sqlite_table(path, "nulls_t")
    assert [c.name for c in empty.columns] == [c.name for c in nulls.columns]
    assert nulls.unique_constraints[0].nullable_columns == ("a",)
    assert not nulls.unique_constraints[0].enforces_key
    assert derive_key_dependencies(nulls).dependencies == ()


def test_inspection_never_modifies_the_database(db: Path) -> None:
    before = db.read_bytes()
    entries = sorted(p.name for p in db.parent.iterdir())
    inspect_sqlite_database(db)
    list_user_tables(db)
    inspect_sqlite_table(db, "uniq")
    assert db.read_bytes() == before
    assert sorted(p.name for p in db.parent.iterdir()) == entries  # no journal files


def test_connection_is_read_only(db: Path) -> None:
    from dbschemax.sqlite_integration import _open_database

    with _open_database(db) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE should_fail (x)")
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO no_pk VALUES (1, 'x')")


def test_connection_closed_even_on_error(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def spy(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", spy)
    with pytest.raises(SQLiteInspectionError):
        inspect_sqlite_table(db, "missing_table")
    inspect_sqlite_table(db, "no_pk")
    assert len(opened) == 2
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


# --- Names, identifiers and errors -------------------------------------------

def test_table_name_lookup_is_case_insensitive_and_canonical(db: Path) -> None:
    assert inspect_sqlite_table(db, "ONE_PK").name == "one_pk"


def test_internal_table_rejected(db: Path) -> None:
    with pytest.raises(SQLiteInspectionError, match="internal"):
        inspect_sqlite_table(db, "sqlite_sequence")
    with pytest.raises(SQLiteInspectionError, match="internal"):
        inspect_sqlite_table(db, "SQLITE_master")


def test_view_is_not_a_table(db: Path) -> None:
    with pytest.raises(SQLiteInspectionError, match="not found"):
        inspect_sqlite_table(db, "some_view")


def test_missing_table_error_lists_available_tables(db: Path) -> None:
    with pytest.raises(SQLiteInspectionError) as info:
        inspect_sqlite_table(db, "nope")
    message = str(info.value)
    assert "'nope'" in message and "one_pk" in message and "comp" in message


@pytest.mark.parametrize("bad", [None, 5, b"t", ["t"]])
def test_non_string_table_name_rejected(db: Path, bad: object) -> None:
    with pytest.raises(TypeError):
        inspect_sqlite_table(db, bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_table_name_rejected(db: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        inspect_sqlite_table(db, bad)


def test_missing_database_path_not_created(tmp_path: Path) -> None:
    path = tmp_path / "does_not_exist.db"
    with pytest.raises(SQLiteInspectionError, match="not found"):
        list_user_tables(path)
    with pytest.raises(SQLiteInspectionError, match="not found"):
        inspect_sqlite_table(path, "t")
    with pytest.raises(SQLiteInspectionError, match="not found"):
        inspect_sqlite_database(str(path))
    assert not path.exists()  # inspecting must never create a database


def test_directory_path_rejected(tmp_path: Path) -> None:
    with pytest.raises(SQLiteInspectionError, match="not a file"):
        list_user_tables(tmp_path)


def test_non_sqlite_file_rejected(tmp_path: Path) -> None:
    path = tmp_path / "fake.db"
    path.write_bytes(b"this is definitely not a sqlite database " * 20)
    before = path.read_bytes()
    with pytest.raises(SQLiteInspectionError, match="not a database"):
        list_user_tables(path)
    with pytest.raises(SQLiteInspectionError):
        inspect_sqlite_table(path, "t")
    assert path.read_bytes() == before


def test_invalid_path_type_rejected() -> None:
    with pytest.raises(TypeError):
        list_user_tables(123)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        list_user_tables(None)  # type: ignore[arg-type]


def test_str_and_path_inputs_equivalent(db: Path) -> None:
    assert inspect_sqlite_table(str(db), "comp") == inspect_sqlite_table(db, "comp")


def test_hostile_table_name_is_treated_as_data(tmp_path: Path) -> None:
    hostile = 'x"; DROP TABLE victim; --'
    escaped = hostile.replace('"', '""')
    path = make_db(
        tmp_path, f'CREATE TABLE victim (a); CREATE TABLE "{escaped}" (c INT PRIMARY KEY);'
    )
    assert inspect_sqlite_table(path, hostile).column_names == ("c",)
    with pytest.raises(SQLiteInspectionError):
        inspect_sqlite_table(path, "victim'; DROP TABLE victim; --")
    assert "victim" in list_user_tables(path)


def test_unusual_column_names(tmp_path: Path) -> None:
    path = make_db(tmp_path, 'CREATE TABLE "weird tbl" ("col one" INT, "col|two" INT);')
    table = inspect_sqlite_table(path, "weird tbl")
    assert table.column_names == ("col one", "col|two")
    assert table.to_relation().attributes == frozenset({"col one", "col|two"})


# --- Relation conversion and FD derivation -----------------------------------

def test_to_relation(db: Path) -> None:
    relation = inspect_sqlite_table(db, "comp").to_relation()
    assert relation == Relation("comp", {"p", "q", "r"})


def test_to_relation_rejects_names_the_core_would_alter(tmp_path: Path) -> None:
    path = make_db(tmp_path, 'CREATE TABLE t (" a" INT, b INT);')
    with pytest.raises(ValueError, match="whitespace"):
        inspect_sqlite_table(path, "t").to_relation()


def test_derive_from_single_primary_key(db: Path) -> None:
    derived = derive_key_dependencies(inspect_sqlite_table(db, "one_pk"))
    assert derived.dependencies == (fd("id -> name, score"),)
    assert any("may hold NULL" in note for note in derived.notes)  # TEXT PRIMARY KEY


def test_derive_from_composite_primary_key(db: Path) -> None:
    derived = derive_key_dependencies(inspect_sqlite_table(db, "comp"))
    assert derived.dependencies == (fd("p, q -> r"),)


def test_derive_no_primary_key_invents_nothing(db: Path) -> None:
    derived = derive_key_dependencies(inspect_sqlite_table(db, "no_pk"))
    assert derived.dependencies == ()
    assert any("No primary key" in note and "none was invented" in note for note in derived.notes)


def test_derive_when_primary_key_covers_all_columns(db: Path) -> None:
    derived = derive_key_dependencies(inspect_sqlite_table(db, "parent"))
    assert derived.dependencies == ()
    assert any("covers every column" in note for note in derived.notes)


def test_derive_uses_only_sound_unique_constraints(db: Path) -> None:
    derived = derive_key_dependencies(inspect_sqlite_table(db, "uniq"))
    texts = {str(dep) for dep in derived.dependencies}
    # PK id; NOT NULL UNIQUE code; NOT NULL UNIQUE (a, b) are sound keys.
    assert "id -> a, b, code, email" in texts
    assert "code -> a, b, email, id" in texts
    assert "a, b -> code, email, id" in texts
    assert not any(t.startswith("email ->") for t in texts)  # nullable, partial
    notes = " ".join(derived.notes)
    assert "may hold NULL" in notes and "partial index" in notes
    assert "uniq_expr" in notes and "expression" in notes


def test_derive_ignores_foreign_keys(db: Path) -> None:
    derived = derive_key_dependencies(inspect_sqlite_table(db, "child"))
    assert derived.dependencies == (fd("id -> other, px, py"),)


def test_derive_deduplicates_unique_equal_to_primary_key(tmp_path: Path) -> None:
    path = make_db(tmp_path, "CREATE TABLE t (a INT NOT NULL PRIMARY KEY, b INT, UNIQUE (a));")
    derived = derive_key_dependencies(inspect_sqlite_table(path, "t"))
    assert derived.dependencies == (fd("a -> b"),)


def test_derive_does_not_use_data_or_names(tmp_path: Path) -> None:
    path = make_db(
        tmp_path,
        """
        CREATE TABLE emp (emp_id INT, emp_name TEXT, dept_id INT, dept_name TEXT);
        INSERT INTO emp VALUES (1, 'a', 10, 'x'), (2, 'b', 10, 'x');
        """,
    )
    derived = derive_key_dependencies(inspect_sqlite_table(path, "emp"))
    assert derived.dependencies == ()  # dept_id -> dept_name holds in the rows, not declared


# --- Integration with the mathematical core ----------------------------------

def test_inspected_table_feeds_the_analysis_engine(tmp_path: Path) -> None:
    path = make_db(
        tmp_path,
        """
        CREATE TABLE order_items (
            order_id INT NOT NULL, product_id INT NOT NULL, product_name TEXT, qty INT,
            PRIMARY KEY (order_id, product_id));
        """,
    )
    table = inspect_sqlite_table(path, "order_items")
    derived = derive_key_dependencies(table)
    only_declared = analyze_normalization(table.to_relation(), derived.dependencies)
    assert only_declared.candidate_keys == (frozenset({"order_id", "product_id"}),)
    assert only_declared.satisfies_bcnf  # optimistic: no other FD is known

    extra = [fd("product_id -> product_name")]  # user-supplied knowledge
    fuller = analyze_normalization(table.to_relation(), [*derived.dependencies, *extra])
    assert not fuller.satisfies_2nf
    assert find_candidate_keys(table.to_relation().attributes, [*derived.dependencies, *extra]) == (
        frozenset({"order_id", "product_id"}),
    )


# --- Immutability ----------------------------------------------------------

def test_results_are_immutable(db: Path) -> None:
    table = inspect_sqlite_table(db, "comp")
    with pytest.raises(dataclasses.FrozenInstanceError):
        table.name = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        table.columns[0].name = "z"  # type: ignore[misc]
    assert isinstance(table.columns, tuple) and isinstance(table.primary_key, tuple)


def test_path_argument_not_mutated(db: Path) -> None:
    given = Path(str(db))
    inspect_sqlite_table(given, "comp")
    assert given == db
