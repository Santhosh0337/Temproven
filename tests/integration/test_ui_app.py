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


def test_streamlit_timeline_renders_from_graph_data(monkeypatch):
    """
    Verify on a throwaway graph that:
    - The timeline renders data that came from real graph Event nodes
    - The entry count matches persisted Event count
    - An incident with 0 events renders the empty state safely without exception
    """
    import uuid
    import falkordb
    from temproven import config
    from temproven.clock import FixedClock
    from temproven.context import RunContext
    from temproven.graph.schema import apply_schema
    from temproven.memory.episodic import EpisodicRecorder, EventType
    from temproven.seed.seed import seed_graph

    unique_graph = f"temproven_ui_app_test_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("FALKORDB_GRAPH", unique_graph)
    monkeypatch.setattr(config, "GRAPH_NAME", unique_graph)

    gw = GraphGateway(graph_name=unique_graph)
    apply_schema(gw)
    seed_graph(gw)

    # 1. Create Incident 1 with 3 real Event nodes (newer opened_at so it is selected first)
    inc1_id = "INC-APPTEST-1"
    gw.query(
        """
        MATCH (inst:Instrument {id: 'INS-A'})
        CREATE (inc:Incident {
            id: $inc_id,
            incident_type: 'CERT_LAPSE',
            status: 'IN_PROGRESS',
            opened_at: 1792229000,
            next_seq: 1
        })
        CREATE (inc)-[:CONCERNS_INSTRUMENT]->(inst)
        """,
        {"inc_id": inc1_id},
    )

    recorder = EpisodicRecorder()
    clock = FixedClock(1792229000)
    ctx1 = RunContext(incident_id=inc1_id, actor_agent="EvidenceAgent", clock=clock, recorder=recorder)
    recorder.record_event(gw, ctx1, EventType.INCIDENT_OPENED, "Incident opened for INS-A", "open_key")
    recorder.record_event(gw, ctx1, EventType.OBSERVATION, "Sensor drift observed", "obs_key")
    recorder.record_event(gw, ctx1, EventType.ACTION_APPLIED, "Quarantined dataset DS-A3", "action_key")

    # 2. Create Incident 2 with 0 events (older opened_at)
    inc_empty_id = "INC-APPTEST-EMPTY"
    gw.query(
        """
        CREATE (inc:Incident {
            id: $inc_id,
            incident_type: 'CERT_LAPSE',
            status: 'IN_PROGRESS',
            opened_at: 1792227000,
            next_seq: 1
        })
        """,
        {"inc_id": inc_empty_id},
    )

    # Count persisted events in graph for INC-APPTEST-1
    event_count = gw.query(
        "MATCH (inc:Incident {id: $inc_id})-[:HAS_EVENT]->(e:Event) RETURN count(e) AS cnt",
        {"inc_id": inc1_id},
    )[0]["cnt"]
    assert event_count == 3

    gw.close()

    try:
        # Run AppTest
        at = AppTest.from_file(APP_PATH)
        at.run(timeout=15)
        assert not at.exception

        # Timeline for INC-APPTEST-1
        timeline_mds = [m.value for m in at.markdown if "timeline-item" in m.value and "<style>" not in m.value]
        assert len(timeline_mds) >= 1
        timeline_html = timeline_mds[0]

        # Verify entry count equals persisted Event count
        assert timeline_html.count('class="timeline-item"') == 3
        assert timeline_html.count('class="timeline-dot"') == 3
        assert "Incident opened for INS-A" in timeline_html
        assert "Sensor drift observed" in timeline_html
        assert "Quarantined dataset DS-A3" in timeline_html

        # Verify no 4-space indented lines
        for line in timeline_html.splitlines():
            assert not line.startswith("    ")
            assert not line.startswith("\t")

        # Test selecting the empty incident
        select_box = at.sidebar.selectbox[0]
        empty_index = next(
            i for i, opt in enumerate(select_box.options) if inc_empty_id in opt
        )
        select_box.select_index(empty_index)
        at.run(timeout=15)
        assert not at.exception

        # Verify empty state renders safely
        empty_mds = [m.value for m in at.info if "No episodic events recorded" in m.value]
        assert len(empty_mds) >= 1, "Expected empty state message for incident with 0 events"

    finally:
        client = falkordb.FalkorDB(host=config.FALKORDB_HOST, port=config.FALKORDB_PORT)
        try:
            assert unique_graph.startswith("temproven_ui_app_test_")
            client.select_graph(unique_graph).delete()
        except Exception:
            pass
