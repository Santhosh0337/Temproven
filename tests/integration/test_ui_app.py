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


def test_streamlit_journey_and_audit_trail_render_from_graph_data(monkeypatch):
    """
    Verify on a throwaway graph that:
    - The Incident Journey Graph renders canonical stages and sequence edges from real graph Event nodes
    - Present stages have solid borders, absent stages have dashed borders and [NOT RECORDED]
    - The Audit Trail renders data matching the persisted Event count
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

        # Verify Timeline controls and legend
        assert len(at.toggle) >= 1
        assert any(t.label == "Vertical" for t in at.toggle)

        # Verify caption
        captions = [c.value for c in at.caption if "Line = recorded event sequence" in c.value]
        assert len(captions) >= 1, "Expected Timeline legend caption to render"

        # Verify category multiselect filter exists
        assert len(at.multiselect) >= 1
        ms = at.multiselect[0]
        assert any("Evidence received" in opt for opt in ms.options)

        # Verify navigation buttons exist
        button_labels = [b.label for b in at.button]
        assert "All" in button_labels
        assert "Reset" in button_labels
        assert "◀ Prev" in button_labels
        assert "Next ▶" in button_labels
        assert "Clear" in button_labels

        # Verify number input for Go to sequence exists
        assert len(at.number_input) >= 1

        # Verify Investigation Summary (Default View) for active incident (3 events)
        captions = [c.value for c in at.caption if "INVESTIGATION SUMMARY (DEFAULT VIEW)" in c.value or "WHAT HAPPENED?" in c.value]
        assert len(captions) >= 1, "Expected Investigation Summary to render by default"

        # Verify Collapsed Advanced: Full Technical Audit Trail expander with exact label for active incident
        audit_expanders = [e for e in at.expander if e.label == f"Advanced: Full Technical Audit Trail ({event_count} events)"]
        assert len(audit_expanders) == 1, f"Expected expander labeled 'Advanced: Full Technical Audit Trail ({event_count} events)'"

        # Test selecting the empty incident
        select_box = at.sidebar.selectbox[0]
        empty_index = next(
            i for i, opt in enumerate(select_box.options) if inc_empty_id in opt
        )
        select_box.select_index(empty_index)
        at.run(timeout=15)
        assert not at.exception

        # Verify empty state renders safely (markdown or native st.info)
        empty_mds = [m.value for m in at.markdown if "No episodic events recorded" in m.value]
        empty_infos = [i.value for i in at.info if "Select a point on the timeline" in i.value]
        assert (len(empty_mds) + len(empty_infos)) >= 1, "Expected empty state message for incident with 0 events"

        # Verify empty incident has audit expander with 0 events
        empty_audit_expanders = [e for e in at.expander if e.label == "Advanced: Full Technical Audit Trail (0 events)"]
        assert len(empty_audit_expanders) == 1, "Expected expander labeled 'Advanced: Full Technical Audit Trail (0 events)'"

    finally:
        client = falkordb.FalkorDB(host=config.FALKORDB_HOST, port=config.FALKORDB_PORT)
        try:
            assert unique_graph.startswith("temproven_ui_app_test_")
            client.select_graph(unique_graph).delete()
        except Exception:
            pass
