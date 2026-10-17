"""
tests/integration/test_episodic_memory.py
─────────────────────────────────────────
Integration tests for Episodic Memory against real FalkorDB:
- Event persistence with required fields and relationships
- Monotonic gap-free sequence and linear [:FOLLOWS] chain
- Replay idempotency on logical_key
- Fresh-session reconstruction proof
- Unknown incident returns None (no fabrication)
"""

import uuid
import pytest
import falkordb

from temproven import config
from temproven.clock import FixedClock
from temproven.context import RunContext, MetricsScope
from temproven.graph.gateway import GraphGateway
from temproven.graph.schema import apply_schema
from temproven.memory.episodic import (
    EpisodicRecorder,
    EventType,
    get_timeline,
    reconstruct_incident,
)


@pytest.fixture
def test_gw():
    """Create a GraphGateway connected to a fresh throwaway graph, deleted upon teardown."""
    unique_graph = f"temproven_test_{uuid.uuid4().hex[:8]}"
    gw = GraphGateway(graph_name=unique_graph)
    apply_schema(gw)
    yield gw
    gw.close()
    client = falkordb.FalkorDB(host=config.FALKORDB_HOST, port=config.FALKORDB_PORT)
    try:
        client.select_graph(unique_graph).delete()
    except Exception:
        pass


def test_event_persistence_and_references(test_gw):
    """Test that events store all required fields and link evidence/action relationships."""
    incident_id = "INC-TEST-EPISODIC-1"
    clock = FixedClock(1782864000)
    recorder = EpisodicRecorder()
    metrics = MetricsScope()
    ctx = RunContext(
        incident_id=incident_id,
        actor_agent="EvidenceAgent",
        clock=clock,
        metrics=metrics,
        recorder=recorder,
    )

    # Setup incident, evidence, action record nodes
    test_gw.query(
        "CREATE (i:Incident {id: $inc_id, incident_type: 'CERT_LAPSE', status: 'IN_PROGRESS', opened_at: 1782864000, next_seq: 1})",
        {"inc_id": incident_id},
    )
    test_gw.query(
        "CREATE (e:Evidence {id: 'EV-101', source_type: 'LAB_AUDIT', trust_level: 'AUTHORITATIVE', observed_at: 1782864000, recorded_at: 1782864000})",
    )
    test_gw.query(
        "CREATE (ar:ActionRecord {id: 'AR-201', action_type: 'QUARANTINE_DATASET', target_id: 'DS-1', prior_state: 'ACTIVE', new_state: 'QUARANTINED', reason: 'Lapsed', evidence_path_json: '[]', recorded_at: 1782864000, actor_agent: 'ImpactAgent', rule_version: '1.0.0'})",
    )

    # 1. Record event with evidence and action reference
    ev1 = recorder.record_event(
        gw=test_gw,
        ctx=ctx,
        event_type=EventType.EVIDENCE_INGESTED,
        summary="Ingested audit evidence EV-101",
        logical_key="ingest_audit",
        evidence_ids=["EV-101"],
        action_ids=["AR-201"],
        outcome="CONFIRMED",
        payload={"audit_ref": "AUD-99"},
    )

    assert ev1.id.startswith("ev_")
    assert ev1.seq == 1
    assert ev1.event_type == "EVIDENCE_INGESTED"
    assert ev1.outcome == "CONFIRMED"
    assert ev1.evidence_ids == ["EV-101"]
    assert ev1.action_ids == ["AR-201"]

    # Verify relationships in graph directly
    ev_rel = test_gw.query(
        "MATCH (ev:Event {id: $id})-[:REFERENCES_EVIDENCE]->(e:Evidence) RETURN e.id AS eid",
        {"id": ev1.id},
    )
    assert len(ev_rel) == 1 and ev_rel[0]["eid"] == "EV-101"

    ar_rel = test_gw.query(
        "MATCH (ev:Event {id: $id})-[:REFERENCES_ACTION]->(ar:ActionRecord) RETURN ar.id AS arid",
        {"id": ev1.id},
    )
    assert len(ar_rel) == 1 and ar_rel[0]["arid"] == "AR-201"


def test_chain_integrity_and_linear_follows(test_gw):
    """Test that events form a strict linear [:FOLLOWS] chain with gap-free seq numbers."""
    incident_id = "INC-TEST-CHAIN"
    clock = FixedClock(1782864000)
    recorder = EpisodicRecorder()
    ctx = RunContext(
        incident_id=incident_id,
        actor_agent="EvidenceAgent",
        clock=clock,
        recorder=recorder,
    )

    test_gw.query(
        "CREATE (i:Incident {id: $inc_id, incident_type: 'CERT_LAPSE', status: 'IN_PROGRESS', opened_at: 1782864000, next_seq: 1})",
        {"inc_id": incident_id},
    )

    # Record 4 sequential events
    events = []
    for i in range(1, 5):
        clock.advance(10)
        ev = recorder.record_event(
            gw=test_gw,
            ctx=ctx,
            event_type=EventType.OBSERVATION,
            summary=f"Observation {i}",
            logical_key=f"obs_step_{i}",
        )
        events.append(ev)

    # Check seq numbers are strictly 1, 2, 3, 4
    assert [e.seq for e in events] == [1, 2, 3, 4]

    # Verify FOLLOWS edges
    timeline = get_timeline(test_gw, incident_id)
    assert len(timeline) == 4
    for idx, e in enumerate(timeline):
        assert e.seq == idx + 1

    # Verify linear graph chain (e4 -> e3 -> e2 -> e1)
    chain = test_gw.query("""
        MATCH path = (e4:Event {seq: 4})-[:FOLLOWS*]->(e1:Event {seq: 1})
        RETURN length(path) AS len
    """)
    assert len(chain) == 1
    assert chain[0]["len"] == 3


def test_replay_idempotency_does_not_advance_seq(test_gw):
    """Test that re-recording with the same logical_key does not create duplicates or advance seq."""
    incident_id = "INC-TEST-IDEM"
    clock = FixedClock(1782864000)
    recorder = EpisodicRecorder()
    ctx = RunContext(
        incident_id=incident_id,
        actor_agent="EvidenceAgent",
        clock=clock,
        recorder=recorder,
    )

    test_gw.query(
        "CREATE (i:Incident {id: $inc_id, incident_type: 'CERT_LAPSE', status: 'IN_PROGRESS', opened_at: 1782864000, next_seq: 1})",
        {"inc_id": incident_id},
    )

    # Record first event
    ev1 = recorder.record_event(
        gw=test_gw,
        ctx=ctx,
        event_type=EventType.INCIDENT_OPENED,
        summary="Incident opened",
        logical_key="open_incident",
    )
    assert ev1.seq == 1

    # Re-record with identical logical key
    ev1_again = recorder.record_event(
        gw=test_gw,
        ctx=ctx,
        event_type=EventType.INCIDENT_OPENED,
        summary="Incident opened (duplicate call)",
        logical_key="open_incident",
    )
    assert ev1_again.id == ev1.id
    assert ev1_again.seq == 1

    # Now record second event
    ev2 = recorder.record_event(
        gw=test_gw,
        ctx=ctx,
        event_type=EventType.OBSERVATION,
        summary="Next observation",
        logical_key="obs_next",
    )
    assert ev2.seq == 2

    # Check total event nodes in graph
    count_row = test_gw.query("MATCH (e:Event {incident_id: $inc_id}) RETURN count(e) AS cnt", {"inc_id": incident_id})
    assert count_row[0]["cnt"] == 2


def test_fresh_session_reconstruction_proof(test_gw):
    """
    Fresh-session proof:
    Run an incident with session A objects, then reconstruct using completely
    NEW gateway and recorder objects with no shared memory.
    """
    graph_name = test_gw.graph_name
    incident_id = "INC-FRESH-PROOF"
    clock = FixedClock(1782864000)

    # Session A
    recorder_a = EpisodicRecorder()
    ctx_a = RunContext(
        incident_id=incident_id,
        actor_agent="EvidenceAgent",
        clock=clock,
        recorder=recorder_a,
    )

    test_gw.query(
        "CREATE (i:Incident {id: $inc_id, incident_type: 'CERT_LAPSE', status: 'IN_PROGRESS', opened_at: 1782864000, next_seq: 1})",
        {"inc_id": incident_id},
    )
    ev_a1 = recorder_a.record_event(
        test_gw, ctx_a, EventType.INCIDENT_OPENED, "Opened", "key_open"
    )
    ev_a2 = recorder_a.record_event(
        test_gw, ctx_a, EventType.OBSERVATION, "Observed lapse", "key_obs"
    )

    # Session B: Brand new gateway, new connection, no shared objects
    gw_b = GraphGateway(graph_name=graph_name)
    recon = reconstruct_incident(gw_b, incident_id)
    gw_b.close()

    assert recon is not None
    assert recon.incident_id == incident_id
    assert recon.status == "IN_PROGRESS"
    assert len(recon.events) == 2
    assert recon.events[0].id == ev_a1.id
    assert recon.events[0].seq == 1
    assert recon.events[1].id == ev_a2.id
    assert recon.events[1].seq == 2


def test_reconstruct_unknown_incident_returns_none(test_gw):
    """Reconstructing a non-existent incident returns None without fabrication."""
    recon = reconstruct_incident(test_gw, "NON-EXISTENT-INCIDENT")
    assert recon is None
