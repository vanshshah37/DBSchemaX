"""Tests for explanation helpers: traces must match the real engine."""

import itertools
import random

import pytest

import dbschemax.explain as explain
from dbschemax.candidate_keys import find_candidate_keys
from dbschemax.closure import attribute_closure
from dbschemax.explain import (
    closure_trace,
    equivalence_check,
    explain_report,
    fd_predicate_rows,
    key_evidence,
    key_search_table,
    minimal_cover_trace,
    subset_counts,
    superkey_check,
    synthesis_trace,
)
from dbschemax.minimal_cover import minimal_cover
from dbschemax.models import FunctionalDependency
from dbschemax.normalization import analyze_normalization
from dbschemax.parser import parse_fd
from dbschemax.reporting import build_analysis_report
from dbschemax.synthesis import synthesize_3nf
from dbschemax.ui_support import EXAMPLE_CASES, run_analysis, spec_from_example


def fds_of(*texts: str) -> list[FunctionalDependency]:
    return [parse_fd(t) for t in texts]


def fs(text: str) -> frozenset[str]:
    return frozenset(text.split()) if " " in text else frozenset(text)


def random_fds(rng: random.Random, attrs: str = "ABCDE") -> list[FunctionalDependency]:
    result = []
    for _ in range(rng.randint(0, 7)):
        lhs = frozenset(rng.sample(attrs, rng.choice([0, 1, 1, 2, 2, 3])))
        rhs = frozenset(rng.sample(attrs, rng.randint(1, 2)))
        result.append(FunctionalDependency(lhs, rhs))
    return result


# --- closure trace ---------------------------------------------------------

def test_closure_trace_chain() -> None:
    fds = fds_of("A -> B", "B -> C", "C -> D")
    trace = closure_trace(["A"], fds)
    assert trace.start == fs("A") and trace.closure == fs("ABCD")
    assert len(trace.steps) == 1  # FDs are applied in order within one pass
    step = trace.steps[0]
    assert step.iteration == 1 and step.before == fs("A") and step.after == fs("ABCD")
    assert [(str(a.fd), a.added) for a in step.applied] == [
        ("A -> B", fs("B")),
        ("B -> C", fs("C")),
        ("C -> D", fs("D")),
    ]


def test_closure_trace_needs_several_passes_with_unfavourable_order() -> None:
    fds = fds_of("C -> D", "B -> C", "A -> B")  # reverse order: one FD per pass
    trace = closure_trace(["A"], fds)
    assert [s.iteration for s in trace.steps] == [1, 2, 3]
    assert [s.after for s in trace.steps] == [fs("AB"), fs("ABC"), fs("ABCD")]
    assert trace.closure == fs("ABCD")


def test_closure_trace_composite_lhs_and_no_applicable_fd() -> None:
    fds = fds_of("A,B -> C", "C -> D")
    assert closure_trace(["A"], fds).steps == ()
    assert closure_trace(["A"], fds).closure == fs("A")
    assert closure_trace(["A", "B"], fds).closure == fs("ABCD")


def test_closure_trace_empty_lhs_and_empty_start() -> None:
    trace = closure_trace([], fds_of("∅ -> A", "A -> B"))
    assert trace.closure == fs("AB")
    assert trace.steps[0].before == frozenset()
    assert closure_trace([], []).closure == frozenset()


def test_closure_trace_cycle_terminates() -> None:
    assert closure_trace(["A"], fds_of("A -> B", "B -> C", "C -> A")).closure == fs("ABC")


def test_closure_trace_matches_engine_randomly() -> None:
    rng = random.Random(5)
    for _ in range(300):
        fds = random_fds(rng)
        x = rng.sample("ABCDE", rng.randint(0, 3))
        assert closure_trace(x, fds).closure == attribute_closure(x, fds)


def test_closure_trace_input_validation() -> None:
    with pytest.raises(TypeError):
        closure_trace("AB", [])
    with pytest.raises(TypeError):
        closure_trace(["A"], ["A -> B"])  # type: ignore[list-item]


def test_trace_disagreement_is_reported_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(explain, "attribute_closure", lambda *a, **k: frozenset("XYZ"))
    with pytest.raises(RuntimeError, match="disagrees"):
        closure_trace(["A"], fds_of("A -> B"))


# --- superkey / key evidence ----------------------------------------------

def test_superkey_check() -> None:
    fds = fds_of("A -> B", "B -> C")
    yes = superkey_check(["A"], list("ABC"), fds)
    assert yes.is_superkey and yes.missing == frozenset() and yes.closure == fs("ABC")
    no = superkey_check(["B"], list("ABC"), fds)
    assert not no.is_superkey and no.missing == fs("A") and no.closure == fs("BC")


def test_key_evidence_composite_key() -> None:
    fds = fds_of("A,B -> C")
    evidence = key_evidence(list("ABC"), fds, ["A", "B"])
    assert evidence.is_candidate_key and evidence.closure == fs("ABC")
    assert [(r.removed, r.closure, r.still_superkey) for r in evidence.removals] == [
        ("A", fs("B"), False),
        ("B", fs("A"), False),
    ]


def test_key_evidence_detects_non_minimal_superkey() -> None:
    fds = fds_of("A -> B,C")
    evidence = key_evidence(list("ABC"), fds, ["A", "B"])
    assert evidence.is_superkey and not evidence.is_minimal and not evidence.is_candidate_key
    assert [r.removed for r in evidence.removals if r.still_superkey] == ["B"]


def test_key_evidence_non_superkey_and_empty_key() -> None:
    assert not key_evidence(list("ABC"), fds_of("A -> B"), ["A"]).is_superkey
    empty = key_evidence(list("AB"), fds_of("∅ -> A", "∅ -> B"), [])
    assert empty.is_candidate_key and empty.removals == ()


def test_key_evidence_rejects_attribute_outside_relation() -> None:
    with pytest.raises(ValueError):
        key_evidence(list("AB"), [], ["Z"])


def test_key_evidence_agrees_with_engine() -> None:
    rng = random.Random(8)
    attrs = frozenset("ABCDE")
    for _ in range(100):
        fds = random_fds(rng)
        for key in find_candidate_keys(attrs, fds):
            assert key_evidence(attrs, fds, key).is_candidate_key


# --- exhaustive search table ----------------------------------------------

def test_key_search_table_rows_and_flags() -> None:
    fds = fds_of("A -> B", "B -> A", "A,B -> C")
    rows = key_search_table(list("ABC"), fds)
    assert len(rows) == 2 ** 3
    assert [len(r.subset) for r in rows] == sorted(len(r.subset) for r in rows)
    by_subset = {r.subset: r for r in rows}
    assert by_subset[fs("A")].is_candidate_key and by_subset[fs("B")].is_candidate_key
    ab = by_subset[fs("AB")]
    assert ab.is_superkey and not ab.is_candidate_key and ab.contains_key in (fs("A"), fs("B"))
    assert not by_subset[fs("C")].is_superkey and by_subset[fs("C")].contains_key is None
    assert by_subset[frozenset()].closure == frozenset()


def test_subset_counts() -> None:
    rows = key_search_table(list("ABC"), fds_of("A -> B", "B -> A", "A,B -> C"))
    assert subset_counts(rows) == ((0, 1, 0, 0), (1, 3, 2, 2), (2, 3, 3, 0), (3, 1, 1, 0))
    for size, total, supers, keys in subset_counts(rows):
        assert keys <= supers <= total


def test_key_search_table_limit() -> None:
    with pytest.raises(ValueError, match="limited"):
        key_search_table(list("ABCDEFG"), [], max_attributes=6)


def test_key_search_table_matches_engine_randomly() -> None:
    rng = random.Random(3)
    attrs = frozenset("ABCDE")
    for _ in range(100):
        fds = random_fds(rng)
        rows = key_search_table(attrs, fds)
        assert {r.subset for r in rows if r.is_candidate_key} == set(find_candidate_keys(attrs, fds))


# --- minimal cover trace ---------------------------------------------------

def test_cover_trace_textbook_example() -> None:
    trace = minimal_cover_trace(fds_of("A -> B,C", "B -> C", "A -> B", "A,B -> C"))
    assert [str(f) for f in trace.cover] == ["A -> B", "B -> C"]
    assert [str(f) for f in trace.after_split] == ["A -> B", "A -> C", "A, B -> C", "B -> C"]
    assert trace.duplicates_merged == 1  # the second "A -> B" part
    ext = trace.extraneous_steps
    assert len(ext) == 1 and ext[0].attribute == "A"
    assert str(ext[0].before) == "A, B -> C" and str(ext[0].after) == "B -> C"
    assert ext[0].evidence_closure == fs("BC")
    assert [str(f) for f in trace.after_lhs_reduction] == ["A -> B", "A -> C", "B -> C"]
    assert [(str(r.removed), r.closure_without) for r in trace.redundant_steps] == [
        ("A -> C", fs("ABC"))
    ]


def test_cover_trace_split_and_trivial() -> None:
    trace = minimal_cover_trace(fds_of("A,B -> A,C"))
    (step,) = trace.split_steps
    assert [str(p) for p in step.parts] == ["A, B -> C"]
    assert step.dropped_trivial == fs("A")
    assert minimal_cover_trace([]).cover == ()


def test_cover_trace_empty_lhs() -> None:
    trace = minimal_cover_trace(fds_of("∅ -> A", "A,B -> C"))
    assert [str(s.attribute) for s in trace.extraneous_steps] == ["A"]
    assert [str(f) for f in trace.cover] == ["∅ -> A", "B -> C"]


def test_cover_trace_matches_engine_randomly() -> None:
    rng = random.Random(11)
    for _ in range(300):
        fds = random_fds(rng)
        trace = minimal_cover_trace(fds)  # raises RuntimeError on any disagreement
        assert trace.cover == minimal_cover(fds)
        removed = {s.removed for s in trace.redundant_steps}
        assert removed.isdisjoint(trace.cover)
        assert set(trace.after_lhs_reduction) == set(trace.cover) | removed


def test_cover_trace_every_recorded_step_is_valid() -> None:
    rng = random.Random(12)
    for _ in range(100):
        trace = minimal_cover_trace(random_fds(rng))
        for step in trace.extraneous_steps:
            (target,) = step.before.rhs
            assert target in step.evidence_closure
        for step in trace.redundant_steps:
            assert step.removed.rhs <= step.closure_without


# --- equivalence ------------------------------------------------------------

def test_equivalence_of_cover() -> None:
    original = fds_of("A -> B,C", "B -> C", "A -> B", "A,B -> C")
    check = equivalence_check(original, minimal_cover(original))
    assert check.equivalent
    assert all(e.implied and e.fd.rhs <= e.lhs_closure for e in check.first_implies_second)


def test_equivalence_detects_difference() -> None:
    check = equivalence_check(fds_of("A -> B"), fds_of("A -> B", "B -> C"))
    assert not check.equivalent
    assert [e.implied for e in check.first_implies_second] == [True, False]
    assert all(e.implied for e in check.second_implies_first)


def test_cover_always_equivalent_randomly() -> None:
    rng = random.Random(21)
    for _ in range(200):
        fds = random_fds(rng)
        assert equivalence_check(fds, minimal_cover(fds)).equivalent


# --- normalization predicates ----------------------------------------------

def test_predicate_rows_3nf_not_bcnf() -> None:
    rows = fd_predicate_rows(list("ABC"), fds_of("A,B -> C", "C -> B"))
    by_text = {str(r.fd): r for r in rows}
    assert by_text["A, B -> C"].satisfies_bcnf
    c_to_b = by_text["C -> B"]
    assert not c_to_b.lhs_is_superkey and c_to_b.attribute_is_prime
    assert c_to_b.satisfies_3nf and not c_to_b.satisfies_bcnf


def test_predicate_rows_3nf_violation() -> None:
    (a, b) = fd_predicate_rows(list("ABC"), fds_of("A -> B", "B -> C"))
    assert a.satisfies_bcnf and not b.satisfies_3nf and not b.satisfies_bcnf


def test_predicate_rows_ignore_trivial_and_match_engine_randomly() -> None:
    assert fd_predicate_rows(list("AB"), fds_of("A -> A")) == ()
    rng = random.Random(31)
    attrs = frozenset("ABCDE")
    for _ in range(200):
        fds = random_fds(rng)
        rows = fd_predicate_rows(attrs, fds)  # raises on disagreement with the engine
        analysis = analyze_normalization(attrs, fds)
        assert (not any(not r.satisfies_bcnf for r in rows)) == analysis.satisfies_bcnf


# --- synthesis trace -------------------------------------------------------

def test_synthesis_trace_student_course() -> None:
    fds = fds_of("StudentID -> StudentName", "CourseID -> CourseName", "StudentID, CourseID -> Grade")
    attrs = ["StudentID", "CourseID", "StudentName", "CourseName", "Grade"]
    trace = synthesis_trace(attrs, fds)
    assert trace.schemas == synthesize_3nf(attrs, fds)
    assert not trace.key_schema_needed and trace.key_schema is None
    assert len(trace.groups) == 3 and trace.subsumed == ()
    assert trace.covers_all_attributes
    assert trace.keys_contained == (frozenset({"StudentID", "CourseID"}),)
    for item in trace.preservation:
        assert item.fd.lhs | item.fd.rhs <= item.schema


def test_synthesis_trace_key_schema_step() -> None:
    trace = synthesis_trace(list("ABCD"), fds_of("A -> B", "C -> D"))
    assert trace.key_schema_needed and trace.key_schema == fs("AC")
    assert fs("AC") in trace.schemas


def test_synthesis_trace_subsumption() -> None:
    trace = synthesis_trace(list("ABC"), fds_of("A,B -> C", "C -> B"))
    assert trace.subsumed == ((fs("BC"), fs("ABC")),)
    assert trace.schemas == (fs("ABC"),)


def test_synthesis_trace_empty_lhs() -> None:
    trace = synthesis_trace(list("ABC"), fds_of("∅ -> A", "A -> B"))
    assert trace.key_schema == fs("C") and trace.covers_all_attributes


def test_synthesis_trace_matches_engine_randomly() -> None:
    rng = random.Random(41)
    attrs = frozenset("ABCDE")
    for _ in range(200):
        fds = random_fds(rng)
        trace = synthesis_trace(attrs, fds)
        assert trace.covers_all_attributes and trace.keys_contained
        assert len(trace.preservation) == len(trace.cover)


# --- explain_report --------------------------------------------------------

@pytest.mark.parametrize("case", EXAMPLE_CASES, ids=lambda c: c.key)
def test_explain_report_for_every_example(case) -> None:
    report = run_analysis(spec_from_example(case))
    result = explain_report(report)
    assert result.relation == frozenset(report.attributes)
    assert result.cover_trace.cover == report.minimal_cover
    assert result.synthesis.schemas == report.synthesized_schemas
    assert result.cover_equivalence.equivalent
    assert [e.key for e in result.key_evidence] == list(report.normalization.candidate_keys)
    assert all(e.is_candidate_key for e in result.key_evidence)
    assert all(t.closure == result.relation for t in result.key_closures)
    assert len(result.search_rows) == 2 ** len(report.attributes)


def test_explain_report_incomplete_fd_set_and_no_fds() -> None:
    result = explain_report(build_analysis_report(list("AB"), []))
    assert result.cover_trace.cover == () and result.synthesis.schemas == (fs("AB"),)
    assert [e.key for e in result.key_evidence] == [fs("AB")]


def test_explain_report_permutation_independent() -> None:
    fds = fds_of("A,B -> C", "C -> B", "∅ -> D")
    outputs = {
        explain_report(build_analysis_report(list("DCBA"), list(p))).synthesis.schemas
        for p in itertools.permutations(fds)
    }
    assert len(outputs) == 1
