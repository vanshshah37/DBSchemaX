# DBSchemaX

**Relational Schema & Functional Dependency Analyzer** — a CS undergraduate
project showing how **Mathematics for Computer Science (MFCS)** solves a
concrete database-design problem.

**Problem.** Functional dependencies (FDs) and candidate keys are hard to
analyze by hand, and an incorrect or incomplete analysis makes normalization
and decomposition difficult to justify.

**Solution.** DBSchemaX applies set-based reasoning, attribute closure
(a fixed-point computation), exhaustive candidate-key search, FD equivalence,
normalization predicates and 3NF synthesis, and explains every step.

## Status

| Component | Module | Status |
|---|---|---|
| Relation / FD models | `models.py` | Done |
| FD parser (`->`, `→`, `∅`) | `parser.py` | Done |
| Attribute closure | `closure.py` | Done |
| Candidate keys | `candidate_keys.py` | Done |
| Minimal (canonical) cover | `minimal_cover.py` | Done |
| 2NF / 3NF / BCNF analysis (1NF only from caller-supplied atomicity info) | `normalization.py` | Done |
| 3NF synthesis | `synthesis.py` | Done |
| SQLite schema inspection (read-only) | `sqlite_integration.py` | Done |
| Markdown / JSON reports | `reporting.py` | Done |
| Command-line interface | `cli.py`, `__main__.py` | Done |
| Verified step-by-step explanations | `explain.py` | Done |
| Streamlit dashboard | `streamlit_app.py`, `ui_support.py` | Done |
| Final polish / refactoring (Phase 12) | — | **Planned** |

## MFCS concepts demonstrated

| Concept | Where it appears | Dashboard page |
|---|---|---|
| Set theory (union, inclusion, proper subsets) | schemas, determinants, key minimality | Overview |
| FDs and logical implication (`F ⊨ X→Y ⇔ Y ⊆ X⁺`) | `closure.py` | Overview, Dependency Explorer |
| Closure as a fixed-point computation | `closure.py`, `explain.closure_trace` | Dependency Explorer |
| Combinatorial search over `2ⁿ` subsets | `candidate_keys.py` | Candidate Keys |
| Minimality (`K⁺ = R` and no single attribute removable) | `explain.key_evidence` | Candidate Keys |
| Equivalence and minimization (`F ≡ G`) | `minimal_cover.py`, `explain.minimal_cover_trace` | Minimal Cover |
| Logical predicates for 2NF / 3NF / BCNF | `normalization.py` | Normalization |
| 3NF synthesis from a minimal cover | `synthesis.py` | 3NF Synthesis |
| Algorithmic complexity (exponential key search) | — | Overview, Candidate Keys |

The "MFCS Concepts in Action" section of the dashboard shows, for each concept,
its definition, its role in DBSchemaX, and a **real result from the current
analysis**. Nothing is hard-coded: the traces in `explain.py` replay the real
algorithms and then verify their answer against the engine (a disagreement
raises an error instead of displaying a fabricated explanation).

## Installation

Requires Python 3.10+ (developed on 3.12). The core and the CLI use only the
standard library.

```
pip install -e .            # core + CLI, installs the `dbschemax` command
pip install -e ".[ui]"      # adds Streamlit for the dashboard
pip install -e ".[test]"    # pytest
```

Without installing, set `PYTHONPATH=src` (PowerShell: `$env:PYTHONPATH = "src"`).
If `pyproject.toml` is merged into an existing project, keep its
`[project.scripts]`, `[project.optional-dependencies]` and `[tool.pytest.ini_options]`
sections.

## Command-line interface

```
python -m dbschemax --help        # or: dbschemax --help
```

| Command | Purpose |
|---|---|
| `analyze` | Analyze a manually defined relation |
| `inspect` | Inspect a SQLite table (read-only) and analyze it |
| `tables` | List user tables of a SQLite database |

```powershell
# Manual relation: repeat --fd for each dependency
python -m dbschemax analyze --attributes A,B,C --fd "A -> B" --fd "B -> C" --format markdown --output report.md

# Empty determinant, JSON to stdout
python -m dbschemax analyze --attributes A,B,C --fd "∅ -> A" --fd "A -> B" --format json

# SQLite: list tables, then analyze one (extra FDs are optional)
python -m dbschemax tables --database school.db
python -m dbschemax inspect --database school.db --table Students --fd "StudentID -> Name" --format json --output students.json
```

Options common to `analyze` and `inspect`: `--fd` (repeatable), `--format
{markdown,json}`, `--output/-o PATH`, `--overwrite`, `--non-atomic-attribute ATTR`
(repeatable) and `--all-atomic`. `analyze` also takes `--name`; `inspect` also takes
`--no-derive-key-fds`.

* **Output.** Without `--output` the report is printed to stdout. With `--output`
  it is saved; the format is inferred from `.md`/`.markdown`/`.json`, otherwise
  `--format` is required. An existing file is **never** overwritten unless
  `--overwrite` is given.
* **1NF.** Without `--non-atomic-attribute` / `--all-atomic`, 1NF is reported as
  **unknown**: FDs cannot show whether values are atomic.
* **Database safety.** Databases are opened read-only and never modified.
* **Exit codes.** `0` success · `1` operational failure (missing/unreadable database
  or table, file exists, cannot write) · `2` invalid usage or input (bad FD syntax,
  invalid attributes, FD outside the relation). Ordinary errors print a one-line
  `error: ...` message on stderr, not a traceback.

### FD syntax

```
A -> B                      single attribute
A,B -> C,D                  composite sides, comma-separated
StudentID, CourseID -> Grade   whitespace is ignored
A → B                       Unicode arrow is accepted
∅ -> A                      empty determinant
```

Names are case-sensitive and cannot contain spaces. Duplicate attributes in a
side are merged; a missing arrow, several arrows, an empty right side or an
empty attribute is rejected.

## Streamlit dashboard

```
pip install -e ".[ui]"
streamlit run src/dbschemax/streamlit_app.py
```

(Run from the project root; the app adds `src/` to the path if the package is not
installed.) The app opens on a **labelled Student–Course demonstration**; the
sidebar always shows which relation is being analyzed and whether it is sample or
your own data, and **Reset to Student–Course demo** returns to the example at any time.

| Page | Contents |
|---|---|
| Overview | Problem, solution, the 7-stage pipeline with this analysis' real results, the eight MFCS concepts |
| Schema Input | Example gallery; **manual relation** form; **SQLite table** workflow (upload or local path) |
| Dependency Explorer | FD graph and a pass-by-pass closure walkthrough for any attribute set X |
| Candidate Keys | Keys with minimality evidence, prime/non-prime attributes, exhaustive search table |
| Minimal Cover | Original vs. cover, the three real transformations, mutual-implication proof |
| Normalization | 1NF–BCNF rules, per-FD predicate table, violations with explanations |
| 3NF Synthesis | Before/after schemas, how each schema arose, dependency-preservation evidence, guarantees and caveats |
| Reports | Markdown and JSON downloads (same functions as the CLI) |
| Normalization Lab | One-click 250-record synthetic demo: original rows, why each split happens, the real 3NF tables with data, lossless-join check, before/after counts, CSV/ZIP downloads |

**Manual workflow.** Enter a name, comma-separated attributes and one FD per line,
optionally atomicity information, then *Analyze*. Invalid input produces a message,
never a crash, and the previous analysis stays active.

**SQLite workflow.** Upload a `.db` file (it is copied to a temporary file, opened
read-only and deleted afterwards) or give a local path; pick a table; review its
columns, primary key, unique constraints and foreign keys; add the FDs you know;
choose whether to derive FDs from declared keys; *Analyze this table*. No
user-supplied SQL is ever executed.

**Normalization Lab.** Open *Normalization Lab* in the sidebar and click **Load 250-Record
Demo**. The synthetic dataset (`demo_data.py`; invented values, deterministic) is checked
against its declared FDs, analyzed by the existing engine, and projected onto the schemas
returned by `synthesize_3nf` (duplicates removed). If a declared FD fails on the data, an
error is shown and nothing is normalized. The original is then rebuilt by natural join and
compared as a set of tuples (an instance check, not a proof). Cell counts are a simple
redundancy indicator, not disk usage, and total rows normally *increase*.

The dashboard limits exhaustive search to 12 attributes (`2¹² = 4096` subsets).

## Worked example

Student–Course (this exact output is produced by `python -m dbschemax analyze`):

```
R(StudentID, CourseID, StudentName, CourseName, Grade)
StudentID -> StudentName     CourseID -> CourseName     StudentID, CourseID -> Grade
```

* Candidate key: `{CourseID, StudentID}` (its closure is all of R; removing either
  attribute leaves a closure that is not R).
* 2NF violated: `StudentName` depends on `{StudentID}` and `CourseName` on
  `{CourseID}`, proper subsets of the key. 3NF and BCNF are violated as well.
* 1NF: **unknown** (not derivable from FDs).
* 3NF synthesis: `R1(CourseID, CourseName)`, `R2(StudentID, StudentName)`,
  `R3(CourseID, Grade, StudentID)`. `R3` contains the candidate key, so no extra
  key schema is needed, and every cover FD lies inside one schema (dependency
  preservation).

## Architecture

```
src/dbschemax/
    models.py  parser.py  closure.py  candidate_keys.py  minimal_cover.py
    normalization.py  synthesis.py            # pure mathematical core
    sqlite_integration.py                     # read-only schema inspection
    reporting.py                              # Markdown / JSON reports
    inputs.py  explain.py  ui_support.py      # shared parsing, verified traces, UI support (no Streamlit)
    cli.py  __main__.py                       # command line (no Streamlit)
    streamlit_app.py                          # dashboard (only module importing Streamlit)
tests/  examples/
```

The mathematical core never imports SQLite, reporting, CLI or Streamlit code.

## Tests

```powershell
$env:PYTHONPATH = "src"      # not needed after `pip install -e .`
python -m pytest
```

The core suite runs without Streamlit; `tests/test_streamlit_app.py` uses
Streamlit's headless `AppTest` and is skipped automatically when Streamlit is not
installed. Last verified run: 428 passed with Streamlit installed; 393 passed and
1 skipped without it.

## Limitations

* All conclusions are relative to the FDs supplied or derived from declared keys.
  A table's schema does not reveal its real-world dependencies, and the tool never
  infers FDs from column names or row data. Incomplete FD sets can give optimistic
  results (see the "Incomplete FD set" example in the dashboard).
* 2NF/3NF/BCNF results assume atomic values; 1NF is only reported from
  caller-supplied information.
* Candidate-key search is exhaustive (exponential in the number of attributes).
* 3NF synthesis is dependency-preserving and yields 3NF schemas; **lossless join
  is not verified by this tool** and BCNF is not guaranteed.
* A minimal cover is not unique; the tool returns one deterministic cover.
* No screenshots are included in this repository.
