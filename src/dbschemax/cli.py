"""Command-line interface for DBSchemaX (standard library ``argparse`` only).

Commands: ``analyze`` (manual relation), ``inspect`` (SQLite table) and
``tables`` (list SQLite tables). Reports are produced by the existing reporting
module; this module only parses arguments and prints or saves the result.

Exit codes: 0 success; 1 operational failure (database or file problem);
2 invalid usage or input (bad arguments, FD syntax, attributes).
Unexpected programming errors are not caught.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from dbschemax import __version__
from dbschemax.inputs import parse_attribute_list, parse_fd_items
from dbschemax.reporting import (
    AnalysisReport,
    ReportSaveError,
    build_analysis_report,
    build_table_report,
    render_json_report,
    render_markdown_report,
    save_json_report,
    save_markdown_report,
)
from dbschemax.sqlite_integration import (
    SQLiteInspectionError,
    inspect_sqlite_table,
    list_user_tables,
)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

_EXTENSION_FORMATS = {".md": "markdown", ".markdown": "markdown", ".json": "json"}

_EPILOG = """\
examples:
  dbschemax analyze --attributes A,B,C --fd "A -> B" --fd "B -> C"
  dbschemax analyze --attributes A,B,C --fd "∅ -> A" --format json --output report.json
  dbschemax tables --database school.db
  dbschemax inspect --database school.db --table Students --fd "StudentID -> Name" \\
      --output students.md

FD syntax: "X -> Y" or "X → Y"; attributes separated by commas; "∅ -> A" is an
empty determinant. Use the same syntax for every --fd.
"""


def _add_report_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--fd",
        action="append",
        default=[],
        metavar="FD",
        help='functional dependency such as "A, B -> C" (repeatable)',
    )
    atomicity = parser.add_mutually_exclusive_group()
    atomicity.add_argument(
        "--non-atomic-attribute",
        action="append",
        default=[],
        dest="non_atomic",
        metavar="ATTR",
        help="attribute known to hold non-atomic values (repeatable); "
        "without any atomicity option, 1NF stays unknown",
    )
    atomicity.add_argument(
        "--all-atomic",
        action="store_true",
        help="assert that every attribute holds atomic values",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        help="report format (default: markdown, or inferred from the --output "
        "extension .md/.markdown/.json)",
    )
    parser.add_argument(
        "-o", "--output", type=Path, metavar="PATH",
        help="write the report to PATH instead of printing it",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace --output if it already exists (otherwise it is never overwritten)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser (exposed for tests and documentation)."""
    parser = argparse.ArgumentParser(
        prog="dbschemax",
        description="Relational schema & functional dependency analyzer.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"dbschemax {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    analyze = sub.add_parser(
        "analyze",
        help="analyze a manually defined relation",
        description="Analyze a relation given by its attributes and functional "
        "dependencies: closure-based keys, minimal cover, 2NF-BCNF and 3NF synthesis.",
        epilog='example: dbschemax analyze --attributes A,B,C --fd "A -> B" --fd "B -> C"',
    )
    analyze.add_argument("--attributes", required=True, metavar="A,B,C",
                         help="comma-separated attribute names")
    analyze.add_argument("--name", metavar="NAME", help="relation name shown in the report")
    _add_report_options(analyze)

    inspect = sub.add_parser(
        "inspect",
        help="inspect and analyze a SQLite table (read-only)",
        description="Read a table's schema from an existing SQLite database (opened "
        "read-only) and analyze it. FDs implied by declared keys are derived unless "
        "disabled; any other FD must be supplied with --fd.",
        epilog='example: dbschemax inspect --database school.db --table Students '
        '--fd "StudentID -> Name"',
    )
    inspect.add_argument("--database", required=True, type=Path, metavar="PATH",
                         help="path to an existing SQLite database file")
    inspect.add_argument("--table", required=True, metavar="NAME", help="table to analyze")
    inspect.add_argument(
        "--no-derive-key-fds", action="store_true",
        help="do not derive FDs from declared primary-key/UNIQUE constraints",
    )
    _add_report_options(inspect)

    tables = sub.add_parser(
        "tables",
        help="list user tables of a SQLite database",
        description="List user-defined tables (SQLite internal tables are excluded).",
    )
    tables.add_argument("--database", required=True, type=Path, metavar="PATH",
                        help="path to an existing SQLite database file")
    return parser


def _ensure_utf8_output() -> None:
    """Reports contain ``∅``; make sure the console can print it."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


def _resolve_format(parser: argparse.ArgumentParser, args: argparse.Namespace) -> str:
    if args.format:
        return args.format
    if args.output is None:
        return "markdown"
    inferred = _EXTENSION_FORMATS.get(args.output.suffix.lower())
    if inferred is None:
        parser.error(
            f"cannot infer the format from {args.output.name!r}; "
            "pass --format markdown or --format json"
        )
    return inferred


def _validate_report_options(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> tuple[str, list[str] | None]:
    """Return (format, atomicity information); exit with usage errors."""
    if args.overwrite and args.output is None:
        parser.error("--overwrite requires --output")
    fmt = _resolve_format(parser, args)
    non_atomic: list[str] | None = None
    if args.all_atomic:
        non_atomic = []
    elif args.non_atomic:
        non_atomic = list(args.non_atomic)
    return fmt, non_atomic


def _emit(report: AnalysisReport, fmt: str, output: Path | None, overwrite: bool) -> None:
    if output is None:
        render = render_markdown_report if fmt == "markdown" else render_json_report
        sys.stdout.write(render(report))
        return
    save = save_markdown_report if fmt == "markdown" else save_json_report
    saved = save(report, output, overwrite=overwrite)
    print(f"report written to {saved}", file=sys.stderr)


def _run_analyze(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    fmt, non_atomic = _validate_report_options(parser, args)
    attributes = parse_attribute_list(args.attributes)
    fds = parse_fd_items(args.fd, label="--fd")
    report = build_analysis_report(attributes, fds, args.name, non_atomic_attributes=non_atomic)
    _emit(report, fmt, args.output, args.overwrite)
    return EXIT_OK


def _run_inspect(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    fmt, non_atomic = _validate_report_options(parser, args)
    fds = parse_fd_items(args.fd, label="--fd")
    table = inspect_sqlite_table(args.database, args.table)
    report = build_table_report(
        table,
        fds,
        use_declared_keys=not args.no_derive_key_fds,
        non_atomic_attributes=non_atomic,
    )
    _emit(report, fmt, args.output, args.overwrite)
    return EXIT_OK


def _run_tables(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    names = list_user_tables(args.database)
    for name in names:
        print(name)
    if not names:
        print("no user tables found", file=sys.stderr)
    return EXIT_OK


_COMMANDS = {"analyze": _run_analyze, "inspect": _run_inspect, "tables": _run_tables}


def _exit_code(exc: SystemExit) -> int:
    if exc.code is None:
        return EXIT_OK
    return exc.code if isinstance(exc.code, int) else EXIT_FAILURE


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    _ensure_utf8_output()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return _COMMANDS[args.command](args, parser)
    except SystemExit as exc:  # argparse help, --version and usage errors
        return _exit_code(exc)
    except ReportSaveError as exc:
        _error(str(exc).replace("pass overwrite=True", "use --overwrite to replace it"))
        return EXIT_FAILURE
    except SQLiteInspectionError as exc:
        _error(str(exc))
        return EXIT_FAILURE
    except ValueError as exc:  # InputError, FDParseError, invalid relation/FDs
        _error(str(exc))
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
