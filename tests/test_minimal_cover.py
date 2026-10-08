"""Tests for minimal (canonical) cover."""

import itertools
import random

import pytest

from dbschemax.closure import attribute_closure
from dbschemax.minimal_cover import minimal_cover
from dbschemax.models import FunctionalDependency
from dbschemax.parser import parse_fd


def fds_of(*texts: str) -> list[FunctionalDependency]:
    return [parse_fd(t) for t in texts]


def cover_of(*texts: str) -> tuple[FunctionalDependency, ...]:
    return minimal_cover(fds_of(*texts))


def texts(cover: tuple[FunctionalDependency, ...]) -> list[str]:
    return [str(fd) for fd in cover]


def implies(fds: list[FunctionalDependency], fd: FunctionalDependency) -> bool:
    return fd.rhs <= attribute_closure(fd.lhs, fds)


def equivalent(f: list[FunctionalDependency], g: list[FunctionalDependency]) -> bool:
    return all(implies(f, fd) for fd in g) and all(implies(g, fd) for fd in f)


def is_minimal_cover(cover: list[FunctionalDependency]) -> bool:
    """Single-attribute RHS, non-trivial, no extraneous LHS attr, none redundant."""
    for fd in cover:
        if len(fd.rhs) != 1 or fd.is_trivial():
            return False
        others = [o for o in cover if o != fd]
        if implies(others, fd):
            return False
        (target,) = fd.rhs
        if any(target in attribute_closure(fd.lhs - {a}, cover) for a in fd.lhs):
            return False
    return len(set(cover)) == len(cover)


def random_fds(rng: random.Random, attrs: str = "ABCDE") -> list[FunctionalDependency]:
    result = []
    for _ in range(rng.randint(0, 7)):
        lhs = frozenset(rng.sample(attrs, rng.choice([0, 1, 1, 2, 2, 3])))
        rhs = frozenset(rng.sample(attrs, rng.randint(1, 2)))
        result.append(FunctionalDependency(lhs, rhs))
    return result


# --- Step 1: RHS splitting -------------------------------------------------

def test_rhs_split() -> None:
    assert texts(cover_of("A -> B,C,D")) == ["A -> B", "A -> C", "A -> D"]


def test_empty_input() -> None:
    assert minimal_cover([]) == ()


# --- Trivial and duplicate FDs ---------------------------------------------

def test_trivial_fds_removed() -> None:
    assert cover_of("A -> A") == ()
    assert cover_of("A,B -> A,B") == ()
    assert texts(cover_of("A,B -> A,C")) == ["A, B -> C"]


def test_duplicate_fds_removed() -> None:
    assert texts(cover_of("A -> B", "A -> B", "A -> B")) == ["A -> B"]
    assert texts(cover_of("A -> B,C", "A -> C,B")) == ["A -> B", "A -> C"]


# --- Step 2: extraneous LHS attributes -------------------------------------

def test_extraneous_lhs_attribute_removed() -> None:
    assert texts(cover_of("A,B -> C", "A -> B")) == ["A -> B", "A -> C"]


def test_necessary_lhs_attributes_kept() -> None:
    assert texts(cover_of("A,B -> C")) == ["A, B -> C"]
    assert texts(cover_of("A,B -> C", "C -> D")) == ["A, B -> C", "C -> D"]


def test_extraneous_via_chain() -> None:
    # B+ = {B, C}, so A is extraneous in AB -> C.
    assert texts(cover_of("A,B -> C", "B -> C")) == ["B -> C"]


# --- Step 3: redundant FDs -------------------------------------------------

def test_redundant_transitive_fd_removed() -> None:
    assert texts(cover_of("A -> B", "B -> C", "A -> C")) == ["A -> B", "B -> C"]


def test_textbook_example() -> None:
    cover = cover_of("A -> B,C", "B -> C", "A -> B", "A,B -> C")
    assert texts(cover) == ["A -> B", "B -> C"]


def test_cyclic_dependencies() -> None:
    cover = cover_of("A -> B", "B -> C", "C -> A", "A -> C")
    assert texts(cover) == ["A -> B", "B -> C", "C -> A"]
    assert is_minimal_cover(list(cover))


# --- Empty LHS -------------------------------------------------------------

def test_empty_lhs_kept() -> None:
    assert texts(cover_of("∅ -> A")) == ["∅ -> A"]


def test_empty_lhs_makes_other_fd_redundant() -> None:
    assert texts(cover_of("∅ -> A", "B -> A")) == ["∅ -> A"]


def test_empty_lhs_makes_attribute_extraneous() -> None:
    # A is determined by ∅, so A is extraneous in AB -> C.
    assert texts(cover_of("∅ -> A", "A,B -> C")) == ["∅ -> A", "B -> C"]


def test_lhs_reduced_to_empty_set() -> None:
    # A is determined by ∅, so A is extraneous in A -> B: it reduces to ∅ -> B.
    assert texts(cover_of("∅ -> A", "A -> B")) == ["∅ -> A", "∅ -> B"]
    # C -> B is then implied by ∅ -> B and is redundant.
    assert texts(cover_of("A -> B", "∅ -> A", "C -> B")) == ["∅ -> A", "∅ -> B"]


# --- Non-uniqueness --------------------------------------------------------

def test_minimal_cover_is_not_unique() -> None:
    original = fds_of("A -> B", "B -> A", "A -> C", "B -> C")
    ours = list(minimal_cover(original))
    alt1 = fds_of("A -> B", "B -> A", "A -> C")
    alt2 = fds_of("A -> B", "B -> A", "B -> C")
    for cover in (ours, alt1, alt2):
        assert is_minimal_cover(cover)
        assert equivalent(cover, original)
    assert set(alt1) != set(alt2)  # two different valid minimal covers


# --- Properties ------------------------------------------------------------

def test_equivalence_and_minimality_random() -> None:
    rng = random.Random(1234)
    for _ in range(300):
        original = random_fds(rng)
        cover = list(minimal_cover(original))
        assert is_minimal_cover(cover), (original, cover)
        assert equivalent(cover, original), (original, cover)


def test_idempotent() -> None:
    rng = random.Random(99)
    for _ in range(100):
        cover = minimal_cover(random_fds(rng))
        assert minimal_cover(cover) == cover


def test_deterministic_regardless_of_order() -> None:
    fds = fds_of("A,B -> C", "A -> B", "B -> A", "C -> D,E", "∅ -> F")
    results = {minimal_cover(list(p)) for p in itertools.permutations(fds)}
    assert len(results) == 1
    assert minimal_cover(fds) == minimal_cover(fds)


def test_output_is_sorted_tuple_of_fds() -> None:
    result = cover_of("C -> D", "A -> B")
    assert isinstance(result, tuple)
    assert all(isinstance(fd, FunctionalDependency) for fd in result)
    assert texts(result) == ["A -> B", "C -> D"]


# --- Inputs ----------------------------------------------------------------

def test_inputs_not_mutated() -> None:
    fds = fds_of("A -> B,C", "A,B -> C", "B -> C")
    before = list(fds)
    minimal_cover(fds)
    assert fds == before


def test_generator_and_iterator_inputs() -> None:
    fds = fds_of("A -> B", "B -> C", "A -> C")
    expected = minimal_cover(fds)
    assert minimal_cover(f for f in fds) == expected
    assert minimal_cover(iter(fds)) == expected
    assert minimal_cover(frozenset(fds)) == expected


def test_non_fd_rejected() -> None:
    with pytest.raises(TypeError):
        minimal_cover(["A -> B"])  # type: ignore[list-item]
    with pytest.raises(TypeError):
        minimal_cover("A -> B")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        minimal_cover([None])  # type: ignore[list-item]
