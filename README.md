# DBSchemaX

**Relational Schema & Functional Dependency Analyzer** — a CS undergraduate
project (MFCS) that applies discrete mathematics (set theory and logic) to
relational database design.

## Status

Phase 1 (foundation) only. Everything marked **Planned** is not implemented yet.

| Feature | Status |
|---|---|
| Relation and Functional Dependency data models | Done |
| Attribute closure | Planned |
| Candidate key discovery | Planned |
| Minimal (canonical) cover | Planned |
| Normal form checks (2NF, 3NF, BCNF) | Planned |
| Decomposition | Planned |
| SQLite persistence | Planned |
| Streamlit UI | Planned (final phase) |

## MFCS concepts

- **Relation schema** `R(A1, ..., An)`: a named finite set of attributes.
- **Functional dependency (FD)** `X -> Y`: `X` and `Y` are attribute sets; an
  empty left side is valid in the mathematical model.
- **Trivial FD**: `X -> Y` where `Y` is a subset of `X`.
- **Attribute closure** `X+` (planned): all attributes determined by `X`
  under a set of FDs.
- **Candidate key** (planned): a minimal attribute set whose closure is all of `R`.
- **Minimal cover** (planned): an equivalent FD set with single-attribute
  right sides, no redundant FDs, and no extraneous left-side attributes.
- **Normal forms** (planned): 2NF, 3NF, BCNF.
- **Decomposition** (planned): splitting `R` into smaller schemas.

## Planned architecture

```text
src/dbschemax/
    models.py      # Relation, FunctionalDependency (implemented)
    ...            # pure-logic modules for planned algorithms
    ...            # SQLite storage layer (planned)
    ...            # Streamlit app (planned, final phase)
tests/             # pytest suite
examples/          # sample schemas and FD sets
```

Design rule: the core mathematical logic stays independent of the database
and UI code.

## Setup

Requires Python 3.10+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest
```

For local package development, the project uses a standard `src/` layout and
can be installed with:

```powershell
pip install -e .
```

## Current API

```python
from dbschemax import Relation, FunctionalDependency

r = Relation("R", {"A", "B", "C"})
fd = FunctionalDependency({"A"}, {"B"})
```

Both types are immutable, hashable, and store attributes as `frozenset[str]`.
