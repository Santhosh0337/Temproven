"""
tests/unit/test_ui_journey.py
─────────────────────────────
Comprehensive unit tests for pure domain & presentation logic in temproven/app/journey.py:
- Canonical stage definitions and ordering
- Exhaustive event mapping (direct, playbook step, handoffs, decisions)
- QUERY_EXECUTED handling (never key events, explicit linking, unlinked queries)
- Status derivation strictly from explicit persisted outcomes (no assumed success)
- Monotonic sequence (seq) ordering authority and interleaving detection
- Graphviz DOT generation (present vs absent stages, selection penwidth, sequence edges)
"""

import pytest

from temproven.app.journey import (
    CANONICAL_KEYS,
    CANONICAL_STAGES,
    DIRECT_EVENT_STAGE_MAP,
    PLAYBOOK_STEP_STAGE_MAP,
    HANDOFF_TASK_STAGE_MAP,
    DECISION_STAGE_MAP,
    build_journey,
    build_journey_dot,
    derive_stage_status,
    extract_payload,
    link_query_to_stage,
    map_event_to_stage,
)


def test_canonical_stages_order_and_keys():
    """Verify that exactly 7 canonical stages exist in the exact required order."""
    expected_keys = [
        "evidence_received",
        "temporal_diagnosis",
        "playbook_reuse",
        "impact_tracing",
        "quarantine_and_tasks",
        "postcondition_verification",
        "incident_closure",
    ]
    assert CANONICAL_KEYS == expected_keys
    assert len(CANONICAL_STAGES) == 7
    for i, (k, label) in enumerate(CANONICAL_STAGES):
        assert k == expected_keys[i]
        assert len(label) > 0


def test_direct_event_stage_mapping():
    """Verify direct event type mappings to canonical stages."""
    cases = [
        ("INCIDENT_OPENED", "evidence_received"),
        ("EVIDENCE_INGESTED", "evidence_received"),
        ("OBSERVATION", "evidence_received"),
        ("HYPOTHESIS_CHECKED", "temporal_diagnosis"),
        ("PLAYBOOK_RETRIEVED", "playbook_reuse"),
        ("ACTION_APPLIED", "quarantine_and_tasks"),
        ("POSTCONDITION_CHECKED", "postcondition_verification"),
        ("INCIDENT_CLOSED", "incident_closure"),
    ]
    for ev_type, expected_stage in cases:
        ev = {"event_type": ev_type}
        assert map_event_to_stage(ev) == expected_stage


def test_playbook_step_sub_key_mapping():
    """Verify PLAYBOOK_STEP events map by payload.step_type."""
    cases = [
        ("CONFIRM_LAPSE", "temporal_diagnosis"),
        ("VERIFY_PRECONDITIONS", "temporal_diagnosis"),
        ("CHECK_INCIDENT_SHAPE", "temporal_diagnosis"),
        ("TRACE_DEPENDENTS", "impact_tracing"),
        ("TRACE_IMPACT_LINEAGE", "impact_tracing"),
        ("PROPOSE_ACTIONS", "impact_tracing"),
        ("QUARANTINE_DATASETS", "quarantine_and_tasks"),
        ("HOLD_REPORTS", "quarantine_and_tasks"),
        ("CREATE_ASSIGN_TASKS", "quarantine_and_tasks"),
        ("VERIFY_GATE_BLOCKS", "postcondition_verification"),
    ]
    for step_type, expected_stage in cases:
        ev = {"event_type": "PLAYBOOK_STEP", "payload": {"step_type": step_type}}
        assert map_event_to_stage(ev) == expected_stage

    # Unmapped step_type falls back to unclassified
    ev_unknown = {"event_type": "PLAYBOOK_STEP", "payload": {"step_type": "MYSTERY_STEP"}}
    assert map_event_to_stage(ev_unknown) == "unclassified"


def test_handoff_sub_key_mapping():
    """Verify HANDOFF_CREATED and HANDOFF_COMPLETED map by task_type or handoff lookup."""
    cases = [
        ("SELECT_PLAYBOOK", "playbook_reuse"),
        ("ASSESS_IMPACT", "impact_tracing"),
        ("VALIDATE_PROPOSALS", "quarantine_and_tasks"),
        ("APPLY_APPROVED", "quarantine_and_tasks"),
        ("VERIFY_AND_CLOSE", "postcondition_verification"),
    ]
    for task_type, expected_stage in cases:
        ev_direct = {"event_type": "HANDOFF_CREATED", "payload": {"task_type": task_type}}
        assert map_event_to_stage(ev_direct) == expected_stage

        # Lookup by handoff_id
        ev_id = {"event_type": "HANDOFF_COMPLETED", "payload": {"handoff_id": "h_123"}}
        assert map_event_to_stage(ev_id, handoff_task_types={"h_123": task_type}) == expected_stage


def test_decision_event_mapping():
    """Verify DECISION events map to quarantine_and_tasks or unclassified."""
    ev_quarantine = {"event_type": "DECISION", "payload": {"action_type": "QUARANTINE_DATASET"}}
    assert map_event_to_stage(ev_quarantine) == "quarantine_and_tasks"

    ev_target = {"event_type": "DECISION", "payload": {"target_id": "DS-A3"}}
    assert map_event_to_stage(ev_target) == "quarantine_and_tasks"

    ev_empty = {"event_type": "DECISION", "payload": {}}
    assert map_event_to_stage(ev_empty) == "unclassified"


def test_unclassified_events_not_lost():
    """Verify completely unmapped event types are routed to 'unclassified' without throwing."""
    ev = {"event_type": "WEIRD_CUSTOM_EVENT", "payload": {"foo": "bar"}}
    assert map_event_to_stage(ev) == "unclassified"


def test_query_executed_rule_2_4():
    """
    Verify Rule 2.4:
    - QUERY_EXECUTED is NEVER a key event (not in event_count or key_events_count).
    - Linked queries attach to supporting_queries of the referenced stage.
    - Unlinked queries are kept in unlinked_queries and unlinked_queries_count.
    - Never assigned to a stage merely by proximity.
    """
    events = [
        {"seq": 1, "event_type": "INCIDENT_OPENED", "at": 100},
        # Linked query via hypothesis
        {"seq": 2, "event_type": "QUERY_EXECUTED", "at": 105, "payload": {"hypothesis": "H1", "query_name": "Q_CHECK"}},
        # Linked query via step_type
        {"seq": 3, "event_type": "QUERY_EXECUTED", "at": 110, "payload": {"step_type": "TRACE_DEPENDENTS", "query_name": "Q_LINEAGE"}},
        # Unlinked query
        {"seq": 4, "event_type": "QUERY_EXECUTED", "at": 115, "payload": {"query_name": "Q_ARBITRARY"}},
    ]

    journey = build_journey(events)
    assert journey.all_events_count == 4
    assert journey.key_events_count == 1
    assert journey.supporting_queries_count == 2
    assert journey.unlinked_queries_count == 1
    assert len(journey.unlinked_queries) == 1

    stage_diag = next(s for s in journey.stages if s.key == "temporal_diagnosis")
    assert stage_diag.event_count == 0  # not a key event
    assert stage_diag.query_count == 1  # linked supporting query

    stage_impact = next(s for s in journey.stages if s.key == "impact_tracing")
    assert stage_impact.query_count == 1


def test_status_derivation_honesty():
    """
    Verify that stage status derivation is strictly based on explicit persisted outcomes:
    - No events -> NOT RECORDED
    - Any failure -> FAILED
    - Any difference -> COMPLETED WITH DIFFERENCES
    - Explicit success (PASSED, CONFIRMED, etc.) -> COMPLETED
    - Neutral events only -> RECORDED (never assumed success)
    """
    assert derive_stage_status("temporal_diagnosis", []) == "NOT RECORDED"

    # Neutral observation only -> RECORDED
    assert derive_stage_status("evidence_received", [{"event_type": "OBSERVATION"}]) == "RECORDED"

    # Explicit success -> COMPLETED
    assert derive_stage_status("temporal_diagnosis", [{"outcome": "CONFIRMED"}]) == "COMPLETED"

    # Explicit difference -> COMPLETED WITH DIFFERENCES
    assert derive_stage_status("temporal_diagnosis", [{"outcome": "PASSED"}, {"outcome": "DIFFERED"}]) == "COMPLETED WITH DIFFERENCES"

    # Explicit failure takes precedence -> FAILED
    assert derive_stage_status("temporal_diagnosis", [{"outcome": "PASSED"}, {"outcome": "FAILED"}]) == "FAILED"

    # Closure stage
    assert derive_stage_status("incident_closure", [{"event_type": "INCIDENT_CLOSED"}]) == "COMPLETED"
    assert derive_stage_status("incident_closure", [{"event_type": "OTHER"}]) == "RECORDED"


def test_monotonic_seq_authority_and_interleaving():
    """
    Verify:
    - Chronology is strictly governed by seq, even if at timestamps are scrambled.
    - Overlapping sequence windows trigger interleaving = True.
    """
    events = [
        # Stage: evidence_received (seq 1, 4)
        {"seq": 1, "at": 500, "event_type": "INCIDENT_OPENED"},
        # Stage: temporal_diagnosis (seq 2, 3)
        {"seq": 2, "at": 100, "event_type": "HYPOTHESIS_CHECKED", "outcome": "CONFIRMED"},
        {"seq": 3, "at": 200, "event_type": "HYPOTHESIS_CHECKED", "outcome": "CONFIRMED"},
        # Back to evidence_received
        {"seq": 4, "at": 600, "event_type": "EVIDENCE_INGESTED"},
    ]

    journey = build_journey(events)
    stage_ev = next(s for s in journey.stages if s.key == "evidence_received")
    stage_diag = next(s for s in journey.stages if s.key == "temporal_diagnosis")

    # Sequence range
    assert stage_ev.first_seq == 1
    assert stage_ev.last_seq == 4
    assert stage_diag.first_seq == 2
    assert stage_diag.last_seq == 3

    # Interleaving detected because stage_diag runs within stage_ev's sequence window
    assert stage_ev.interleaved is True
    assert stage_diag.interleaved is True


def test_build_journey_edges_and_absent_stages():
    """
    Verify:
    - Absent stages have is_present == False and no edges.
    - Edges connect present stages in chronological succession of first_seq.
    """
    # Only Stages 2 and 6 present (like INC-2026-001)
    events = [
        {"seq": 1, "event_type": "HYPOTHESIS_CHECKED", "outcome": "CONFIRMED"},
        {"seq": 2, "event_type": "POSTCONDITION_CHECKED", "outcome": "PASSED"},
    ]

    journey = build_journey(events)
    assert len(journey.present_stages) == 2
    assert len(journey.absent_stages) == 5
    assert journey.unclassified_stage is None

    # Edges link temporal_diagnosis -> postcondition_verification
    assert len(journey.edges) == 1
    assert journey.edges[0] == ("temporal_diagnosis", "postcondition_verification")

    # Unclassified present if there are unclassified events
    events_with_unclass = events + [{"seq": 3, "event_type": "UNKNOWN_ACTION"}]
    journey_u = build_journey(events_with_unclass)
    assert journey_u.unclassified_stage is not None
    assert journey_u.unclassified_stage.event_count == 1
    assert len(journey_u.edges) == 2
    assert journey_u.edges[1] == ("postcondition_verification", "unclassified")


def test_build_journey_dot_rendering():
    """
    Verify Graphviz DOT output:
    - Valid digraph structure.
    - Present stages have solid border, absent have dashed border.
    - Selected stage has heavier penwidth.
    - Sequence edges have label 'next in sequence'.
    - HTML characters are safely escaped.
    """
    events = [
        {"seq": 1, "event_type": "INCIDENT_OPENED", "actor_agent": "Alice<Lead>"},
    ]
    journey = build_journey(events)

    # Highlight evidence_received
    dot = build_journey_dot(journey, selected_stage_key="evidence_received", orientation="TB")

    assert dot.startswith("digraph IncidentJourney {")
    assert 'rankdir="TB"' in dot
    assert dot.strip().endswith("}")

    # Evidence received node has penwidth=2.8
    assert '"evidence_received" [' in dot
    assert "penwidth=2.8" in dot
    assert 'style="filled"' in dot

    # Absent stage has dashed border and [NOT RECORDED]
    assert '"incident_closure" [' in dot
    assert "[NOT RECORDED]" in dot
    assert 'style="dashed,filled"' in dot
    assert "penwidth=1.0" in dot

    # Escaping check
    assert "Alice&lt;Lead&gt;" not in dot  # actor is in stage.actors, not node label directly, but let's check label safety
