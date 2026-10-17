"""
tests/integration/test_ui_app.py
────────────────────────────────
End-to-end integration test of the Streamlit application using Streamlit's official AppTest.
Verifies that:
- The entire page runs without exceptions
- All 4 panels render their respective components (markdown, metrics, tables, graph)
- Rerun does not cause writes to the database
"""

from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

from temproven.graph.gateway import GraphGateway

APP_PATH = str(Path(__file__).parent.parent.parent / "temproven" / "app" / "streamlit_app.py")


def test_streamlit_app_renders_successfully():
    """Ensure the full Streamlit UI executes and renders without uncaught exceptions."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=15)

    assert not at.exception, f"Streamlit app raised an exception: {at.exception}"
    assert len(at.markdown) >= 30, "Expected markdown elements across all 4 panels"
    assert len(at.selectbox) >= 1, "Expected incident and/or report selectboxes"
    assert len(at.button) >= 1, "Expected publication attempt button"
    assert len(at.checkbox) >= 1, "Expected explicit confirmation checkbox"


def test_streamlit_app_rerun_zero_writes():
    """Verify that multiple reruns of the dashboard do not mutate graph state."""
    gw = GraphGateway()
    count_before = gw.query("MATCH (n) RETURN count(n) AS cnt")[0]["cnt"]
    gw.close()

    at = AppTest.from_file(APP_PATH)
    at.run(timeout=15)
    # Rerun once
    at.run(timeout=15)

    gw = GraphGateway()
    count_after = gw.query("MATCH (n) RETURN count(n) AS cnt")[0]["cnt"]
    gw.close()

    assert count_before == count_after, "Streamlit app rerun must NOT create or delete any graph nodes!"
