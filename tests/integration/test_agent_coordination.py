"""
tests/integration/test_agent_coordination.py
────────────────────────────────────────────
Integration tests for Agent Coordination against real FalkorDB:
- Atomic claiming of handoff nodes (concurrency guard)
- Handoff creation idempotency
- Durable pipeline execution and recovery across orchestrator instances
- Lease expiration recovery and attempt cap failure
- Closed incident re-processing protection
"""

import json
import uuid
import pytest
import falkordb

from temproven import config
from temproven.agents.evidence import EvidenceAgent
from temproven.agents.impact import ImpactAgent
from temproven.agents.integrity import IntegrityAgent
from temproven.agents.orchestrator import Orchestrator
from temproven.clock import FixedClock
from temproven.context import RunContext, MetricsScope
from temproven.domain.temporal import parse_iso_to_epoch
from temproven.graph import queries
from temproven.graph.gateway import GraphGateway
from temproven.graph.schema import apply_schema
from temproven.memory.episodic import EpisodicRecorder, reconstruct_incident
from temproven.seed.seed import seed_graph


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


def test_atomic_claim_and_idempotency(test_gw):
    """
    Test that claiming a handoff is atomic (second attempt returns no row),
    and creating a handoff with the same idempotency_key creates no duplicate.
    """
    incident_id = "INC-ATOMIC-1"
    test_gw.query("CREATE (i:Incident {id: $id, incident_type: 'CERT_LAPSE', status: 'IN_PROGRESS', opened_at: 1000, next_seq: 1})", {"id": incident_id})

    # 1. Create handoff
    h_id = "h_atomic_test_01"
    key = "idem_key_01"
    test_gw.query(
        queries.Q_CREATE_HANDOFF,
        {
            "id": h_id,
            "incident_id": incident_id,
            "from_agent": "EvidenceAgent",
            "to_agent": "IntegrityAgent",
            "task_type": "SELECT_PLAYBOOK",
            "payload_json": "{}",
            "idempotency_key": key,
            "created_at": 1000,
        },
    )

    # Re-create with same ID and key -> MERGE ensures no duplicate
    test_gw.query(
        queries.Q_CREATE_HANDOFF,
        {
            "id": h_id,
            "incident_id": incident_id,
            "from_agent": "EvidenceAgent",
            "to_agent": "IntegrityAgent",
            "task_type": "SELECT_PLAYBOOK",
            "payload_json": "{}",
            "idempotency_key": key,
            "created_at": 1000,
        },
    )
    cnt = test_gw.query("MATCH (h:Handoff {id: $id}) RETURN count(h) AS cnt", {"id": h_id})
    assert cnt[0]["cnt"] == 1

    # 2. Worker 1 claims handoff
    res1 = test_gw.query(
        queries.Q_CLAIM_HANDOFF,
        {"id": h_id, "claimed_by": "worker-1", "claimed_at": 1005, "lease_expires_at": 1065},
    )
    assert len(res1) == 1
    assert res1[0]["status"] == "CLAIMED"

    # Worker 2 attempts to claim the same handoff -> returns no row
    res2 = test_gw.query(
        queries.Q_CLAIM_HANDOFF,
        {"id": h_id, "claimed_by": "worker-2", "claimed_at": 1006, "lease_expires_at": 1066},
    )
    assert len(res2) == 0


def test_lease_expiration_and_recovery(test_gw):
    """
    Test that a handoff claimed whose lease expires is recovered back to PENDING,
    and when attempt_count exceeds limit, is marked FAILED.
    """
    incident_id = "INC-LEASE-1"
    test_gw.query("CREATE (i:Incident {id: $id, incident_type: 'CERT_LAPSE', status: 'IN_PROGRESS', opened_at: 1000, next_seq: 1})", {"id": incident_id})

    h_id = "h_lease_test_01"
    test_gw.query(
        queries.Q_CREATE_HANDOFF,
        {
            "id": h_id,
            "incident_id": incident_id,
            "from_agent": "EvidenceAgent",
            "to_agent": "IntegrityAgent",
            "task_type": "SELECT_PLAYBOOK",
            "payload_json": "{}",
            "idempotency_key": "k1",
            "created_at": 1000,
        },
    )

    # Claim at t=1000 with lease expiring at t=1050
    test_gw.query(
        queries.Q_CLAIM_HANDOFF,
        {"id": h_id, "claimed_by": "worker-1", "claimed_at": 1000, "lease_expires_at": 1050},
    )

    clock = FixedClock(1060)  # Clock is past lease_expires_at
    orch = Orchestrator(test_gw, clock)

    # Recovery pass: attempt_count was 1 -> resets to PENDING
    rec_rows = orch.recover(max_attempts=3)
    assert len(rec_rows) == 1
    assert rec_rows[0]["status"] == "PENDING"

    # Re-claim 2 more times to exceed limit
    test_gw.query(queries.Q_CLAIM_HANDOFF, {"id": h_id, "claimed_by": "w1", "claimed_at": 1060, "lease_expires_at": 1070})
    clock.advance_to(1080)
    orch.recover(max_attempts=3)
    test_gw.query(queries.Q_CLAIM_HANDOFF, {"id": h_id, "claimed_by": "w1", "claimed_at": 1080, "lease_expires_at": 1090})
    clock.advance_to(1100)

    # Now attempt_count = 3 >= max_attempts -> marks FAILED
    rec_fail = orch.recover(max_attempts=3)
    assert len(rec_fail) == 1
    assert rec_fail[0]["status"] == "FAILED"


def test_durable_orchestration_step_limit_and_continuation(test_gw):
    """
    Run pipeline with step_limit=1 (leaves PENDING handoff).
    A NEW orchestrator instance picks it up and finishes it.
    Reconstructing from graph shows complete closed incident with all actions and tasks.
    """
    seed_graph(test_gw, clear_existing=False)

    clock = FixedClock(parse_iso_to_epoch("2026-10-17T09:00:00Z"))
    incident_id = "INC-ORCH-DURABLE"
    recorder = EpisodicRecorder()
    metrics = MetricsScope()
    ctx = RunContext(incident_id=incident_id, actor_agent="EvidenceAgent", clock=clock, metrics=metrics, recorder=recorder)

    # Evidence agent ingests and creates initial handoff
    evidence_agent = EvidenceAgent(test_gw, clock)
    t_v2_end = parse_iso_to_epoch("2026-06-01T00:00:00Z")
    evidence_agent.ingest_and_open_incident(
        incident_id=incident_id,
        instrument_id="INS-A",
        cert_id="CERT-A1",
        old_claim_id="CLAIM-A1-v1",
        new_claim_id="CLAIM-A1-v2",
        new_valid_until=t_v2_end,
        evidence_id="EV-A1-v2",
        evidence_ref="CORR-2026-NMI-099",
        description="Sensor drift detected",
        trust_level="AUTHORITATIVE",
        ctx=ctx,
    )

    # Orchestrator A runs only 1 step
    orch_a = Orchestrator(test_gw, clock, worker_id="worker-A")
    res_a = orch_a.run(incident_id, ctx, max_steps=1)
    assert res_a["steps_executed"] == 1

    # Check there is a pending handoff in graph
    pending_a = test_gw.query(queries.Q_GET_PENDING_HANDOFFS, {"incident_id": incident_id})
    assert len(pending_a) > 0

    # Orchestrator B on NEW instance continues the workflow to completion
    orch_b = Orchestrator(test_gw, clock, worker_id="worker-B")
    res_b = orch_b.run(incident_id, ctx)
    assert res_b["status"] == "COMPLETED"

    # Reconstruct incident from graph
    recon = reconstruct_incident(test_gw, incident_id)
    assert recon is not None
    assert recon.status == "CLOSED"
    assert len(recon.action_records) > 0
    assert len(recon.tasks) > 0


def test_closed_incident_reprocessing_guard(test_gw):
    """
    Calling run() on an already CLOSED incident does not execute new steps
    or duplicate actions. Returns ALREADY_PROCESSED.
    """
    seed_graph(test_gw, clear_existing=False)
    clock = FixedClock(parse_iso_to_epoch("2026-10-17T09:00:00Z"))
    incident_id = "INC-CLOSED-GUARD"
    recorder = EpisodicRecorder()
    metrics = MetricsScope()
    ctx = RunContext(incident_id=incident_id, actor_agent="EvidenceAgent", clock=clock, metrics=metrics, recorder=recorder)

    evidence_agent = EvidenceAgent(test_gw, clock)
    t_v2_end = parse_iso_to_epoch("2026-06-01T00:00:00Z")
    evidence_agent.ingest_and_open_incident(
        incident_id=incident_id,
        instrument_id="INS-A",
        cert_id="CERT-A1",
        old_claim_id="CLAIM-A1-v1",
        new_claim_id="CLAIM-A1-v2",
        new_valid_until=t_v2_end,
        evidence_id="EV-A1-v2",
        evidence_ref="CORR-2026-NMI-099",
        description="Sensor drift detected",
        trust_level="AUTHORITATIVE",
        ctx=ctx,
    )

    orch = Orchestrator(test_gw, clock)
    res = orch.run(incident_id, ctx)
    assert res["status"] == "COMPLETED"

    # Count nodes before re-run
    events_before = test_gw.query("MATCH (e:Event {incident_id: $id}) RETURN count(e) AS cnt", {"id": incident_id})[0]["cnt"]
    actions_before = test_gw.query("MATCH (ar:ActionRecord)-[:PART_OF]->(i:Incident {id: $id}) RETURN count(ar) AS cnt", {"id": incident_id})[0]["cnt"]

    # Re-run on closed incident
    re_res = orch.run(incident_id, ctx)
    assert re_res["status"] == "ALREADY_PROCESSED"

    # Count nodes after re-run
    events_after = test_gw.query("MATCH (e:Event {incident_id: $id}) RETURN count(e) AS cnt", {"id": incident_id})[0]["cnt"]
    actions_after = test_gw.query("MATCH (ar:ActionRecord)-[:PART_OF]->(i:Incident {id: $id}) RETURN count(ar) AS cnt", {"id": incident_id})[0]["cnt"]

    assert events_before == events_after
    assert actions_before == actions_after
