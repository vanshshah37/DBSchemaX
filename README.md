# DBSchemaX

**Relational Schema & Functional Dependency Analyzer** — a Python project applying discrete mathematics and database theory to relational schema design. DBSchemaX analyses functional dependencies, computes keys and covers, checks normal forms, synthesizes a 3NF decomposition, and provides a command-line interface and Streamlit dashboard.

## What DBSchemaX does

Database schemas can store the same facts repeatedly, which can lead to update, insertion, and deletion anomalies. DBSchemaX helps analyse a relation's structure and functional dependencies, identify relevant keys and normalization issues, and generate a proposed normalized schema.

The project combines a reusable mathematical core with CLI, SQLite integration, reports, and a Streamlit user interface.

## Features

| Feature | Description |
|---|---|
| Relation and FD models | Represents relation schemas and functional dependencies using attribute sets. |
| FD parser | Parses functional-dependency input, including multi-attribute sides and an empty left-hand side. |
| Attribute closure | Computes the attributes implied by a given attribute set and FD set. |
| Candidate keys | Finds minimal superkeys using closure and subset search. |
| Minimal cover | Simplifies a set of functional dependencies while preserving its logical implications. |
| Normal-form analysis | Analyses 1NF when atomicity information is supplied, and checks 2NF, 3NF, and BCNF using the schema and FDs. |
| 3NF synthesis | Produces a set of relation schemas from a minimal cover using the project's synthesis algorithm. |
| SQLite integration | Reads and analyses supported SQLite schema metadata. |
| Reports | Produces analysis reports, including Markdown and JSON output. |
| CLI | Runs supported analyses from the command line. |
| Streamlit dashboard | Provides pages for overview, schema input, dependency exploration, candidate keys, minimal cover, normalization, 3NF synthesis, reports, and the Normalization Lab. |
| Normalization Lab | Demonstrates the transformation of a deterministic, synthetic 250-record enrollment dataset into normalized tables. |

## Normalization Lab: a before-and-after demonstration

The Normalization Lab is designed to show a concrete result instead of only displaying labels such as 2NF or 3NF.

- Loads **250 synthetic enrollment records** with 11 columns.
- Provides the demo schema, functional dependencies, and atomicity information for guided analysis.
- Uses the existing DBSchemaX analysis and 3NF synthesis implementation.
- Projects and deduplicates the source records into the synthesized relations.
- Displays the resulting tables and provides CSV downloads, plus a ZIP export where supported by the UI.
- Checks the example's declared functional dependencies and verifies whether the original tuples can be reconstructed from the decomposed tables.
- Compares the original and normalized table counts, rows, and attribute-value-cell counts.

The example has six declared dependencies and produces six normalized relations. In the included demo, the expected comparison is 250 original rows and 2,750 attribute-value cells versus 383 total rows across the normalized tables and 1,434 attribute-value cells. The cell count is a simple indicator of repeated values, not a measurement of disk space. Normalization does **not** necessarily reduce the total row count.

The data is synthetic, generated for a reproducible demonstration; it is not taken from a real university dataset. A dependency that holds in a sample is verified for that sample only—it does not establish that the same rule must hold for all future real-world data.

## Mathematical concepts used

- **Relation schema** `R(A1, ..., An)`: the set of attributes that define a relation.
- **Functional dependency (FD)** `X -> Y`: a rule stating that an attribute set `X` determines an attribute set `Y`. The mathematical model permits an empty left-hand side.
- **Attribute closure** `X+`: all attributes logically implied by `X` under a set of FDs. If `X+` contains every attribute of `R`, then `X` is a superkey.
- **Candidate key**: a minimal superkey; removing any attribute means the remaining set no longer determines all of `R`.
- **Minimal cover**: an equivalent FD set simplified using standard transformations, such as removing extraneous attributes and redundant dependencies.
- **1NF**: concerns atomic values. DBSchemaX needs atomicity information when it cannot infer whether an attribute stores an indivisible value or a list/repeating group.
- **2NF**: rules out partial dependencies of non-prime attributes on a proper subset of a candidate key.
- **3NF**: uses a formal FD-based condition to avoid certain problematic dependencies on non-superkeys.
- **BCNF**: requires every determinant in a non-trivial FD to be a superkey.
- **3NF synthesis**: constructs smaller relation schemas from a minimal cover; the generated decomposition is intended to preserve dependencies and be lossless under the algorithm's conditions.

The tool performs analysis from the schema and functional dependencies supplied to it. It cannot reliably infer every semantic dependency from a small set of sample rows alone.

## Requirements

- Python 3.10 or newer
- Git (optional, for version control)
- Streamlit for the graphical dashboard
- pytest for running the test suite

The mathematical core and CLI use the Python standard library. Streamlit and pytest are optional dependencies.

## Setup on Windows PowerShell

From the project root, create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install the project in editable mode with the dashboard and test dependencies:

```powershell
python -m pip install -e ".[ui,test]"
```

If PowerShell blocks activation, allow script execution for the current terminal session only, then activate the environment:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

## Run the project

### Command-line interface

Show the available commands and options:

```powershell
python -m dbschemax --help
```

After the editable installation, the installed entry point is also available:

```powershell
dbschemax --help
```

### Streamlit dashboard

From the project root:

```powershell
python -m streamlit run src/dbschemax/streamlit_app.py
```

Open the local URL printed by Streamlit in your browser. To demonstrate the dataset transformation, open **Normalization Lab** and load the 250-record demo.

### Tests

Run the full test suite from the project root:

```powershell
python -m pytest
```

For more verbose test output:

```powershell
python -m pytest -v
```

## Project structure

```text
DBSchemaX/
├── pyproject.toml
├── README.md
├── src/
│   └── dbschemax/
│       ├── __init__.py
│       ├── __main__.py
│       ├── models.py
│       ├── parser.py
│       ├── closure.py
│       ├── candidate_keys.py
│       ├── minimal_cover.py
│       ├── normalization.py
│       ├── synthesis.py
│       ├── sqlite_integration.py
│       ├── reporting.py
│       ├── cli.py
│       ├── explain.py
│       ├── inputs.py
│       ├── ui_support.py
│       ├── demo_data.py
│       ├── normalization_lab.py
│       └── streamlit_app.py
└── tests/
    ├── test_parser.py
    ├── test_cli.py
    ├── test_explain.py
    ├── test_inputs.py
    ├── test_normalization_lab.py
    ├── test_normalization_lab_app.py
    └── ...
```

The tests directory may contain additional test modules. The core functional-dependency and normalization logic is separated from the CLI and UI code.

## Current API example

```python
from dbschemax import Relation, FunctionalDependency

relation = Relation("R", {"A", "B", "C"})
fd = FunctionalDependency({"A"}, {"B"})
```

The model types are immutable and hashable, and store their attributes as `frozenset[str]`.

## Known limitations and interpretation

- The analysis depends on the supplied relation schema and functional dependencies being appropriate for the real domain.
- Normalization rules cannot always be determined from sample values alone; atomicity information may be needed for 1NF.
- Candidate-key discovery uses subset search, which can become expensive as the number of attributes increases.
- The Normalization Lab's 250 records are generated demonstration data and should not be presented as a real institutional dataset.
- Fewer attribute-value cells in the demo are not the same thing as a measured reduction in database file size.

## Development principle

Keep the core mathematical logic independent of the database and UI layers. The CLI, SQLite integration, reporting, and Streamlit dashboard should reuse the same core analysis rather than implement separate normalization rules.
