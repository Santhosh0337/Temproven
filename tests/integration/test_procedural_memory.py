"""
tests/integration/test_procedural_memory.py
───────────────────────────────────────────
Integration tests for Procedural Memory against real FalkorDB:
- Deriving CANDIDATE playbook from real Incident 1 events (no entity IDs in playbook)
- Gated promotion under PC1-PC6 (positive test & negative control sabotage)
- Playbook retrieval, execution, and comparison on Incident 2
- DIFFERED step results when structural features differ (chain coverage, escalations)
- Safe fallback when preconditions fail
"""

import json
import uuid
import pytest
import falkordb

from temproven import config
from temproven.clock import FixedClock
from temproven.context import RunContext, MetricsScope
from temproven.domain.actions import ActionService
from temproven.domain.temporal import parse_iso_to_epoch
from temproven.graph import queries
from temproven.graph.gateway import GraphGateway
from temproven.graph.schema import apply_schema
from temproven.memory.episodic import EpisodicRecorder, EventType
from temproven.memory.procedural import (
    DiagnosticLadder,
    derive_playbook,
    promote_playbook,
    retrieve_playbook,
    execute_playbook_step,
    StepStatus,
)
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


def _run_incident_1_flow(gw: GraphGateway, incident_id: str, clock: FixedClock):
    """Helper to run Incident 1 flow through ladder and actions."""
    recorder = EpisodicRecorder()
    metrics = MetricsScope()
    ctx = RunContext(incident_id=incident_id, actor_agent="IntegrityAgent", clock=clock, metrics=metrics, recorder=recorder)

    # Ingest Act 2 correction
    t2 = clock.now_epoch()
    t_v2_end = parse_iso_to_epoch("2026-06-01T00:00:00Z")
    gw.query(
        """
        MATCH (cert:CalibrationCertificate {id: 'CERT-A1'}), (c1:Claim {id: 'CLAIM-A1-v1'})
        CREATE (c2:Claim {
            id: 'CLAIM-A1-v2',
            claim_type: 'CERT_VALIDITY',
            subject_id: 'CERT-A1',
            valid_from: c1.valid_from,
            valid_until: $t_v2_end,
            recorded_at: $t2,
            verified_at: $t2,
            status: 'VERIFIED',
            summary: 'Validity shortened to 2026-06-01'
        }),
        (ev2:Evidence {
            id: 'EV-A1-v2',
            source_type: 'CALIBRATION_AUTHORITY',
            trust_level: 'AUTHORITATIVE',
            observed_at: $t2,
            recorded_at: $t2,
            ref: 'CORR-2026-NMI-099',
            description: 'Sensor drift after 2026-06-01'
        }),
        (c2)-[:SUPPORTED_BY]->(ev2),
        (cert)-[:HAS_VALIDITY]->(c2),
        (c2)-[:SUPERSEDES {recorded_at: $t2, reason: 'Drift detected'}]->(c1)
        SET c1.status = 'SUPERSEDED'
        """,
        {"t2": t2, "t_v2_end": t_v2_end},
    )

    # Ladder check
    ladder_res = DiagnosticLadder.run_ladder(gw, ctx, instrument_id="INS-A")

    # Action execution
    impact = ActionService.trace_impact(gw, ["EXP-A3", "EXP-A4"])
    ActionService.execute_incident_actions(gw, incident_id, impact, t2, actor_agent="ImpactAgent")

    return ctx


def test_derive_playbook_from_incident_1_events(test_gw):
    """
    Derive CANDIDATE playbook from Incident 1 events.
    Assert no specific entity IDs (INS-A, DS-A3, etc.) appear in stored playbook or steps.
    """
    seed_graph(test_gw, clear_existing=False)
    clock = FixedClock(parse_iso_to_epoch("2026-10-17T09:00:00Z"))
    incident_id = "INC-DERIVE-TEST-1"

    _run_incident_1_flow(test_gw, incident_id, clock)

    playbook_id = derive_playbook(test_gw, incident_id)
    assert playbook_id.startswith("pb_")

    # Verify playbook in graph
    pb_row = test_gw.query("MATCH (p:InvestigationPlaybook {id: $id}) RETURN p.status AS status, p.applicability_json AS app, p.exploration_summary_json AS expl", {"id": playbook_id})
    assert pb_row[0]["status"] == "CANDIDATE"

    # Verify steps
    steps = test_gw.query(queries.Q_GET_PLAYBOOK_STEPS, {"playbook_id": playbook_id})
    assert len(steps) == 7

    # Assert no literal instrument/dataset IDs in stored playbook properties
    for s in steps:
        for val in (s["expected_outcome"], s["params_json"]):
            assert "INS-A" not in val
            assert "DS-A" not in val
            assert "INC-DERIVE-TEST-1" not in val


def test_promote_playbook_positive_and_negative_control(test_gw):
    """
    Test that promotion succeeds when PC1-PC6 pass, and fails when a condition is sabotaged.
    """
    seed_graph(test_gw, clear_existing=False)
    clock = FixedClock(parse_iso_to_epoch("2026-10-17T09:00:00Z"))
    incident_id = "INC-PROMO-1"

    ctx = _run_incident_1_flow(test_gw, incident_id, clock)
    playbook_id = derive_playbook(test_gw, incident_id)

    # 1. Positive case: all PC1-PC6 pass -> promotes to VERIFIED
    promoted, checks = promote_playbook(test_gw, playbook_id, incident_id, ctx)
    assert promoted is True
    assert all(c["passed"] for c in checks.values())

    # Verify status in graph
    stat_row = test_gw.query("MATCH (p:InvestigationPlaybook {id: $id}) RETURN p.status AS status", {"id": playbook_id})
    assert stat_row[0]["status"] == "VERIFIED"

    # Verify VALIDATED_IN edge
    val_row = test_gw.query("MATCH (p:InvestigationPlaybook {id: $id})-[:VALIDATED_IN]->(i:Incident) RETURN i.id AS inc_id", {"id": playbook_id})
    assert len(val_row) == 1 and val_row[0]["inc_id"] == incident_id

    # 2. Negative control sabotage: sabotage PC1 by resetting a dataset back to ACTIVE
    incident_sabotage = "INC-SABOTAGE-1"
    ctx_sab = _run_incident_1_flow(test_gw, incident_sabotage, clock)
    pb_sab = derive_playbook(test_gw, incident_sabotage, version="1.0.1")

    # Sabotage: reset a quarantined dataset to ACTIVE
    test_gw.query("MATCH (ds:Dataset {id: 'DS-A3'}) SET ds.status = 'ACTIVE'")

    promoted_sab, checks_sab = promote_playbook(test_gw, pb_sab, incident_sabotage, ctx_sab)
    assert promoted_sab is False
    assert checks_sab["PC1_datasets_quarantined"]["passed"] is False

    # Playbook remains CANDIDATE
    stat_sab = test_gw.query("MATCH (p:InvestigationPlaybook {id: $id}) RETURN p.status AS status", {"id": pb_sab})
    assert stat_sab[0]["status"] == "CANDIDATE"


def test_incident_2_playbook_reuse_and_differed_step(test_gw):
    """
    Test that Incident 2 retrieves the VERIFIED playbook and executes steps.
    Because Incident 2 features chained certificate coverage and an unowned dataset,
    the comparison detects structural differences and records DIFFERED.
    """
    seed_graph(test_gw, clear_existing=False)
    clock = FixedClock(parse_iso_to_epoch("2026-10-17T09:00:00Z"))

    # Setup and promote playbook from Incident 1
    inc1 = "INC-SRC-1"
    ctx1 = _run_incident_1_flow(test_gw, inc1, clock)
    pb_id = derive_playbook(test_gw, inc1)
    promote_playbook(test_gw, pb_id, inc1, ctx1)

    # Advance clock for Incident 2
    t_inc2 = parse_iso_to_epoch("2026-10-17T12:00:00Z")
    clock.advance_to(t_inc2)

    # Ingest Incident 2 authoritative drift for CERT-B2 (shortened validity)
    t_b2_short = parse_iso_to_epoch("2026-05-01T00:00:00Z")
    test_gw.query(
        """
        MATCH (cert:CalibrationCertificate {id: 'CERT-B2'}), (c1:Claim {id: 'CLAIM-B2-v1'})
        CREATE (c2:Claim {
            id: 'CLAIM-B2-v2',
            claim_type: 'CERT_VALIDITY',
            subject_id: 'CERT-B2',
            valid_from: c1.valid_from,
            valid_until: $t_b2_short,
            recorded_at: $t2,
            verified_at: $t2,
            status: 'VERIFIED',
            summary: 'Thermocouple drift shortened CERT-B2 to 2026-05-01'
        }),
        (ev2:Evidence {
            id: 'EV-B2-v2',
            source_type: 'CALIBRATION_AUTHORITY',
            trust_level: 'AUTHORITATIVE',
            observed_at: $t2,
            recorded_at: $t2,
            ref: 'CORR-2026-NMI-142',
            description: 'Thermocouple drift beyond +/-0.5C tolerance'
        }),
        (c2)-[:SUPPORTED_BY]->(ev2),
        (cert)-[:HAS_VALIDITY]->(c2),
        (c2)-[:SUPERSEDES {recorded_at: $t2, reason: 'Drift'}]->(c1)
        SET c1.status = 'SUPERSEDED'
        """,
        {"t2": t_inc2, "t_b2_short": t_b2_short},
    )

    # Retrieve playbook for Incident 2
    matched_pb = retrieve_playbook(test_gw, "CERT_LAPSE", instrument_id="INS-B")
    assert matched_pb is not None
    assert matched_pb["status"] == "VERIFIED"

    # Execute PlaybookRun on Incident 2
    inc2_id = "INC-2026-002"
    recorder2 = EpisodicRecorder()
    metrics2 = MetricsScope()
    ctx2 = RunContext(incident_id=inc2_id, actor_agent="ImpactAgent", clock=clock, metrics=metrics2, recorder=recorder2)

    test_gw.query(
        "CREATE (i:Incident {id: $id, incident_type: 'CERT_LAPSE', status: 'IN_PROGRESS', opened_at: $at, next_seq: 1})",
        {"id": inc2_id, "at": t_inc2},
    )

    run_id = f"run_{inc2_id}_1"
    test_gw.query(
        queries.Q_RECORD_PLAYBOOK_RUN,
        {"id": run_id, "incident_id": inc2_id, "playbook_id": matched_pb["id"], "mode": "VERIFIED_REUSE", "started_at": t_inc2, "finished_at": None},
    )

    steps = test_gw.query(queries.Q_GET_PLAYBOOK_STEPS, {"playbook_id": matched_pb["id"]})
    state: dict = {"params": {"instrument_id": "INS-B"}}

    executed_results = []
    for s in steps:
        res = execute_playbook_step(test_gw, ctx2, run_id, s, state)
        executed_results.append(res)

    # Step 1 (CONFIRM_LAPSE) on INS-B uses chain coverage for EXP-B2 -> DIFFERED from expected ["single"]
    step1_res = executed_results[0]
    assert step1_res["status"] == StepStatus.DIFFERED.value
    assert any("coverage_modes" in d for d in step1_res["differences"])

    # Step 3 (PROPOSE_ACTIONS) on INS-B has unowned DS-B4 -> has_escalations=True -> DIFFERED from expected False
    step3_res = executed_results[2]
    assert step3_res["status"] == StepStatus.DIFFERED.value
    assert any("has_escalations" in d for d in step3_res["differences"])


def test_applicability_negative_and_precondition_failure(test_gw):
    """
    1. Unmatched incident type returns None.
    2. Missing prerequisites trigger a clean FAILED step result without crashing.
    """
    seed_graph(test_gw, clear_existing=False)

    # 1. Unmatched type
    res = retrieve_playbook(test_gw, "SECURITY_BREACH", instrument_id="INS-A")
    assert res is None

    # 2. Precondition failure (empty state where TRACE_DEPENDENTS needs lapsed_exp_ids)
    clock = FixedClock(1782864000)
    recorder = EpisodicRecorder()
    ctx = RunContext("INC-FAIL-TEST", "ImpactAgent", clock=clock, recorder=recorder)
    test_gw.query("CREATE (i:Incident {id: 'INC-FAIL-TEST', incident_type: 'CERT_LAPSE', status: 'IN_PROGRESS', opened_at: 1782864000, next_seq: 1})")
    test_gw.query(
        queries.Q_RECORD_PLAYBOOK_RUN,
        {"id": "run_fail_1", "incident_id": "INC-FAIL-TEST", "playbook_id": "dummy_pb", "mode": "VERIFIED_REUSE", "started_at": 1782864000, "finished_at": None},
    )

    empty_state = {}
    step_info = {
        "id": "step_trace",
        "step_type": "TRACE_DEPENDENTS",
        "order": 2,
        "expected_outcome": "{}",
    }
    fail_res = execute_playbook_step(test_gw, ctx, "run_fail_1", step_info, empty_state)
    assert fail_res["status"] == StepStatus.FAILED.value
    assert "Missing lapsed experiment IDs" in fail_res["detail"]

