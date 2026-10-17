"""
tests/integration/test_action_enforcement.py
────────────────────────────────────────────
Integration tests for Phase 3 Action Enforcement (S3-S4):
- Impact traversal and dependency paths
- Quarantine actions and report status transitions
- Backend publication gate (live re-evaluation, draft/held/published handling)
- Task creation and assignment fallback (Owner -> Performer -> Custodian -> Escalation)
- ActionRecord audit logging and idempotency
- Preservation of clean data when unverified evidence contradicts
"""

import uuid
import pytest
import falkordb

from temproven import config
from temproven.clock import FixedClock
from temproven.domain.actions import ActionService
from temproven.domain.gate import GateDecision, PublicationGate
from temproven.domain.temporal import parse_iso_to_epoch
from temproven.graph.gateway import GraphGateway
from temproven.graph.schema import apply_schema
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


def test_impact_traversal_reaches_downstream_and_leaves_unrelated_unaffected(test_gw):
    """
    1. A certificate lapse reaches all expected downstream datasets and reports,
       with inspectable dependency paths.
    2. An unrelated dataset is not affected.
    """
    seed_graph(test_gw, clear_existing=False)

    # Lapsed experiments EXP-A3 and EXP-A4
    impact = ActionService.trace_impact(test_gw, ["EXP-A3", "EXP-A4"])

    dataset_ids = {d.id for d in impact.datasets}
    report_ids = {r.id for r in impact.reports}

    # DS-A3 (direct from EXP-A3), DS-A4 (direct from EXP-A4), DS-A5 (derived from DS-A3)
    assert dataset_ids == {"DS-A3", "DS-A4", "DS-A5"}

    # Unrelated datasets DS-A1 and DS-A2 MUST NOT be in impact
    assert "DS-A1" not in dataset_ids
    assert "DS-A2" not in dataset_ids

    # RPT-1 depends on DS-A5, so it must be impacted
    assert "RPT-1" in report_ids

    # RPT-2 uses only DS-A1 and DS-A2, so it MUST NOT be impacted
    assert "RPT-2" not in report_ids

    # Inspectable paths check
    ds_a5_node = next(d for d in impact.datasets if d.id == "DS-A5")
    assert "EXP-A3" in ds_a5_node.dependency_path
    assert "DS-A3" in ds_a5_node.dependency_path
    assert "DS-A5" in ds_a5_node.dependency_path

    rpt_1_node = next(r for r in impact.reports if r.id == "RPT-1")
    assert "USES_DATASET" in rpt_1_node.dependency_path
    assert "RPT-1" in rpt_1_node.dependency_path


def test_invalid_coverage_blocks_draft_report(test_gw):
    """Invalid coverage blocks a draft report from publication."""
    seed_graph(test_gw, clear_existing=False)

    # Ingest authoritative correction ending cert on 2026-06-01
    t2 = parse_iso_to_epoch("2026-10-17T09:00:00Z")
    t_v2_end = parse_iso_to_epoch("2026-06-01T00:00:00Z")

    test_gw.query(
        """
        MATCH (cert:CalibrationCertificate {id: 'CERT-A1'}), (c1:Claim {id: 'CLAIM-A1-v1'})
        CREATE (c2:Claim {
            id: 'CLAIM-A1-v2', claim_type: 'CERT_VALIDITY', subject_id: 'CERT-A1',
            valid_from: c1.valid_from, valid_until: $t_v2_end,
            recorded_at: $t2, verified_at: $t2, status: 'VERIFIED'
        }),
        (cert)-[:HAS_VALIDITY]->(c2),
        (c2)-[:SUPERSEDES {recorded_at: $t2}]->(c1)
        """,
        {"t2": t2, "t_v2_end": t_v2_end},
        name="INGEST_CORRECTION",
    )

    clock = FixedClock(parse_iso_to_epoch("2026-10-17T10:00:00Z"))
    gate = PublicationGate(clock=clock)

    # RPT-1 depends on EXP-A3 (June 15, now NOT_COVERED)
    res = gate.attempt_publish(test_gw, report_id="RPT-1", actor="Tester")
    assert res.decision == GateDecision.BLOCK
    assert res.is_allowed is False
    assert "NOT_COVERED" in res.reason


def test_previously_published_report_marked_requires_review(test_gw):
    """
    A previously published report is marked REQUIRES_REVIEW;
    do not claim it was unpublished or recalled.
    """
    seed_graph(test_gw, clear_existing=False)

    # First publish RPT-1 before the lapse
    test_gw.query(
        "MATCH (r:Report {id: 'RPT-1'}) SET r.publication_status = 'PUBLISHED'",
        {},
        name="SET_PUBLISHED",
    )

    # Run impact and actions
    impact = ActionService.trace_impact(test_gw, ["EXP-A3"])
    now = parse_iso_to_epoch("2026-10-17T09:00:00Z")
    summary = ActionService.execute_incident_actions(
        test_gw,
        incident_id="INC-PUB-TEST",
        impact=impact,
        recorded_at=now,
    )

    assert "RPT-1" in summary["reports_flagged_review"]

    # Verify report in graph is REQUIRES_REVIEW (not DRAFT, not DELETED)
    rpt_row = test_gw.query("MATCH (r:Report {id: 'RPT-1'}) RETURN r.publication_status AS status")[0]
    assert rpt_row["status"] == "REQUIRES_REVIEW"


def test_unknown_coverage_blocks_publication_conservatively(test_gw):
    """Unknown coverage (e.g. missing timestamps) blocks publication conservatively as REVIEW_REQUIRED."""
    seed_graph(test_gw, clear_existing=False)

    # Corrupt USG-A1 to have null used_until
    test_gw.query("MATCH (u:InstrumentUsage {id: 'USG-A1'}) SET u.used_until = null", {})

    clock = FixedClock(parse_iso_to_epoch("2026-07-01T00:00:00Z"))
    gate = PublicationGate(clock=clock)

    res = gate.attempt_publish(test_gw, report_id="RPT-2", actor="Tester")
    assert res.decision == GateDecision.REVIEW_REQUIRED
    assert res.is_allowed is False
    assert "UNKNOWN" in res.reason


def test_unverified_contradictory_evidence_does_not_quarantine(test_gw):
    """
    UNVERIFIED evidence that contradicts an effective verified claim:
    - Affected assessments become CONFLICTING
    - Does NOT quarantine datasets
    """
    seed_graph(test_gw, clear_existing=False)

    t_now = parse_iso_to_epoch("2026-07-01T00:00:00Z")

    # Add unverified contradicting claim
    test_gw.query(
        """
        MATCH (c1:Claim {id: 'CLAIM-A1-v1'})
        CREATE (c_unv:Claim {
            id: 'CLAIM-A1-UNVERIFIED',
            claim_type: 'CERT_VALIDITY',
            subject_id: 'CERT-A1',
            valid_from: 1767225600,
            valid_until: 1770000000,
            recorded_at: $t,
            verified_at: null,
            status: 'UNVERIFIED'
        }),
        (c1)-[:CONTRADICTS {detected_at: $t, reason: 'Unverified tip'}]->(c_unv)
        """,
        {"t": t_now},
    )

    # Evaluate gate for RPT-2
    clock = FixedClock(t_now)
    gate = PublicationGate(clock=clock)
    res = gate.attempt_publish(test_gw, report_id="RPT-2", actor="Tester")
    assert res.decision == GateDecision.REVIEW_REQUIRED
    assert "CONFLICTING" in res.reason

    # Verify datasets DS-A1 and DS-A2 are STILL ACTIVE, NOT QUARANTINED!
    ds_rows = test_gw.query("MATCH (d:Dataset) WHERE d.id IN ['DS-A1', 'DS-A2'] RETURN d.id AS id, d.status AS status")
    for r in ds_rows:
        assert r["status"] == "ACTIVE"


def test_repeating_same_incident_is_idempotent(test_gw):
    """Repeating the same incident does not duplicate tasks or ActionRecords."""
    seed_graph(test_gw, clear_existing=False)

    impact = ActionService.trace_impact(test_gw, ["EXP-A3"])
    now = parse_iso_to_epoch("2026-10-17T09:00:00Z")

    # Run 1
    summary1 = ActionService.execute_incident_actions(
        test_gw,
        incident_id="INC-IDEMP-001",
        impact=impact,
        recorded_at=now,
    )
    assert len(summary1["quarantined_datasets"]) == 2  # DS-A3, DS-A5

    # Count tasks and action records after Run 1
    t_count1 = test_gw.query("MATCH (t:Task) RETURN count(t) AS c")[0]["c"]
    ar_count1 = test_gw.query("MATCH (ar:ActionRecord) RETURN count(ar) AS c")[0]["c"]

    # Run 2 (re-processing same incident)
    summary2 = ActionService.execute_incident_actions(
        test_gw,
        incident_id="INC-IDEMP-001",
        impact=impact,
        recorded_at=now + 10,
    )
    assert len(summary2["quarantined_datasets"]) == 0
    assert len(summary2["actions_skipped"]) >= 2

    # Counts must be identical
    t_count2 = test_gw.query("MATCH (t:Task) RETURN count(t) AS c")[0]["c"]
    ar_count2 = test_gw.query("MATCH (ar:ActionRecord) RETURN count(ar) AS c")[0]["c"]
    assert t_count1 == t_count2
    assert ar_count1 == ar_count2


def test_missing_owner_performer_custodian_results_in_explicit_escalation(test_gw):
    """
    When no owner, performer, or custodian exists for an affected dataset,
    the task status must be ESCALATED with an explicit escalation_reason.
    """
    seed_graph(test_gw, clear_existing=False)

    # In our seed data, DS-B3 is produced by EXP-B3 (no performer), on INS-B (no custodian),
    # and has no owner!
    assign_res = ActionService.resolve_assignment(test_gw, "DS-B3")
    assert assign_res.is_escalated is True
    assert assign_res.assignee_id is None
    assert "No owner, performer, or custodian" in assign_res.escalation_reason

    # Execute incident actions for EXP-B3
    impact_b = ActionService.trace_impact(test_gw, ["EXP-B3"])
    now = parse_iso_to_epoch("2026-10-17T09:00:00Z")
    summary = ActionService.execute_incident_actions(
        test_gw,
        incident_id="INC-ESCALATION-001",
        impact=impact_b,
        recorded_at=now,
    )

    # Find the task for DS-B3
    task_row = test_gw.query(
        "MATCH (t:Task {target_id: 'DS-B3'}) RETURN t.status AS status, t.escalation_reason AS reason, t.assignee_id AS assignee"
    )[0]
    assert task_row["status"] == "ESCALATED"
    assert task_row["assignee"] is None
    assert "No owner, performer, or custodian" in task_row["reason"]


def test_publication_gate_rechecks_live_evidence_after_lapse(test_gw):
    """
    A publication attempt rechecks current graph evidence,
    including a lapse that occurred after an earlier check.
    """
    seed_graph(test_gw, clear_existing=False)

    clock = FixedClock(parse_iso_to_epoch("2026-07-01T00:00:00Z"))
    gate = PublicationGate(clock=clock)

    # Attempt 1: at July 1 -> ALLOW
    res1 = gate.attempt_publish(test_gw, report_id="RPT-1", actor="Tester")
    assert res1.decision == GateDecision.ALLOW

    # Reset report to DRAFT for second attempt
    test_gw.query("MATCH (r:Report {id: 'RPT-1'}) SET r.publication_status = 'DRAFT'", {})

    # Lapse occurs in background (new superseding claim recorded at Oct 17)
    t_lapse = parse_iso_to_epoch("2026-10-17T09:00:00Z")
    test_gw.query(
        """
        MATCH (cert:CalibrationCertificate {id: 'CERT-A1'}), (c1:Claim {id: 'CLAIM-A1-v1'})
        CREATE (c2:Claim {
            id: 'CLAIM-A1-v2', claim_type: 'CERT_VALIDITY', subject_id: 'CERT-A1',
            valid_from: c1.valid_from, valid_until: 1780272000,
            recorded_at: $t, verified_at: $t, status: 'VERIFIED'
        }),
        (cert)-[:HAS_VALIDITY]->(c2),
        (c2)-[:SUPERSEDES {recorded_at: $t}]->(c1)
        """,
        {"t": t_lapse},
    )

    # Advance clock to Oct 17
    clock.advance_to(parse_iso_to_epoch("2026-10-17T12:00:00Z"))

    # Attempt 2: at Oct 17 -> Even though dataset wasn't quarantined yet,
    # the gate re-evaluates upstream usages and catches the lapse!
    res2 = gate.attempt_publish(test_gw, report_id="RPT-1", actor="Tester")
    assert res2.decision == GateDecision.BLOCK
    assert "NOT_COVERED" in res2.reason
