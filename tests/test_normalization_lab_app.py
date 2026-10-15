"""Dashboard tests for the Normalization Lab (skipped when Streamlit is missing)."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from dbschemax.streamlit_app import LAB_PAGE, NAV_PAGES, PAGES  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "src" / "dbschemax" / "streamlit_app.py")


def open_lab() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60).run()
    return at.sidebar.radio(key="page").set_value(LAB_PAGE).run()


def test_original_pages_are_unchanged_and_lab_is_added() -> None:
    assert PAGES == (
        "Overview", "Schema Input", "Dependency Explorer", "Candidate Keys",
        "Minimal Cover", "Normalization", "3NF Synthesis", "Reports",
    )
    assert NAV_PAGES == PAGES + (LAB_PAGE,)


def test_lab_waits_for_the_demo_to_be_loaded() -> None:
    at = open_lab()
    assert not at.exception
    assert any(b.label == "Load 250-Record Demo" for b in at.button)
    assert "lab_result" not in at.session_state


def test_loading_the_demo_shows_all_tables() -> None:
    at = open_lab()
    next(b for b in at.button if b.label == "Load 250-Record Demo").click().run()
    assert not at.exception, [e.value for e in at.exception]
    result = at.session_state["lab_result"]
    assert len(result.dataset.rows) == 250 and len(result.relations) == 6
    assert result.reconstruction.ok
    body = "\n".join(m.value for m in at.markdown)
    assert "250 rows" in body
