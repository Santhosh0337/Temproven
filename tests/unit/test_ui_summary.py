"""
tests/unit/test_ui_summary.py
─────────────────────────────
Comprehensive unit test suite for the Investigation Summary & Milestones:
1. Pure milestone derivations (M1 to M9) from structured persisted facts.
2. Grammatical pluralization (1 dataset vs 2 datasets).
3. Postcondition verification honest pass / fail / not recorded logic.
4. Honest NOT RECORDED states for sparse or unclosed incidents.
5. Exploratory vs Playbook-reuse distinction (M3b only present when PlaybookRun exists).
6. Fact-based 4-row plain English lead (never guesses, reports not recorded when missing).
7. Routine, query, and handoff events never become milestones.
8. Two incidents are completely isolated (never cross-contaminate).
9. Pure functions are 100% deterministic.
10. Static guards: zero hardcoded demo IDs, zero HTML tags, zero unsafe_allow_html.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import pytest

from temproven.app.summary import (
    ClosureRepresentation,
    IncidentReportRestrictions,
    IncidentTasksSummary,
    LeadAnswers,
    Milestone,
    OrderedStepResult,
    PlaybookRunSummary,
    ReportRestrictionDetail,
    _plural,
    build_investigation_lead,
    build_investigation_summary,
    build_ordered_step_results,
    describe_incident_closure,
    describe_incident_report_restrictions,
    describe_incident_tasks,
    describe_single_report_restriction,
)


@dataclass
class MockEvidenceClaim:
    claim_id: str = "claim-cert-001"
    evidence_id: str = "ev-cert-001"
    source_type: str = "CALIBRATION_AUTHORITY"
    trust_level: str = "AUTHORITATIVE"
    valid_from: int = 1792224000
    valid_until: int = 1792227600
    recorded_at: int = 1792227650

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


@dataclass
class MockEvent:
    id: str
    seq: int
    event_type: str
    at: int = 1792227600
    actor: str = "test_agent"
    actor_agent: str | None = None
    summary: str = "Test event"
    outcome: str | None = None
    target_id: str = "n/a"
    evidence_ids: list[str] = field(default_factory=list)
    action_ids: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


@dataclass
class MockReconstruction:
    incident_id: str = "test-inc-1"
    incident_type: str = "CERT_LAPSE"
    status: str = "CLOSED"
    opened_at: int = 1792227600
    closed_at: int = 1792231200
    next_seq: int = 10
    instrument_id: str = "inst-x"
    events: list[Any] = field(default_factory=list)
    evidence_claims: list[Any] = field(default_factory=list)
    action_records: list[dict[str, Any]] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    handoffs: list[dict[str, Any]] = field(default_factory=list)


def test_pluralization_helper():
    """Verify _plural handles 1 vs many correctly."""
    assert _plural(1, "dataset") == "1 dataset"
    assert _plural(2, "dataset") == "2 datasets"
    assert _plural(0, "dataset") == "0 datasets"
    assert _plural(1, "downstream report") == "1 downstream report"
    assert _plural(3, "downstream report") == "3 downstream reports"


def test_investigation_lead_answers_all_passed():
    """Lead answers accurately reflect facts and report all passed checks."""
    recon = MockReconstruction(
        instrument_id="inst-alpha",
        evidence_claims=[{"claim_id": "cert-rev-1"}],
        action_records=[
            {"action_type": "QUARANTINE_DATASET", "target_id": "ds-1"},
            {"action_type": "QUARANTINE_DATASET", "target_id": "ds-2"},
            {"action_type": "HOLD_REPORT", "target_id": "rpt-1"},
        ],
        tasks=[{"id": "t-1", "status": "ASSIGNED"}],
        events=[
            MockEvent(id="e1", seq=1, event_type="POSTCONDITION_CHECKED", outcome="PASSED"),
            MockEvent(id="e2", seq=2, event_type="POSTCONDITION_CHECKED", outcome="PASSED"),
        ],
    )
    impact_data = {
        "experiments": [{"id": "exp-1"}],
        "datasets": [{"id": "ds-1"}, {"id": "ds-2"}],
        "reports": [{"id": "rpt-1"}],
    }

    lead = build_investigation_lead(recon, impact_data=impact_data)

    assert "cert-rev-1" in lead.what_happened
    assert "inst-alpha" in lead.what_happened
    assert "1 experiment, 2 datasets, and 1 downstream report" in lead.what_was_affected
    assert "2 datasets quarantined" in lead.actions_taken
    assert "1 report held" in lead.actions_taken
    assert "1 review task created" in lead.actions_taken
    assert lead.checks_status == "PASS"
    assert lead.all_passed is True
    assert "All 2 postcondition integrity checks passed successfully." in lead.checks_successful


def test_investigation_lead_answers_with_failed_checks():
    """Lead answers report failed postconditions in problem status."""
    recon = MockReconstruction(
        events=[
            MockEvent(id="e1", seq=1, event_type="POSTCONDITION_CHECKED", outcome="PASSED"),
            MockEvent(id="e2", seq=2, event_type="POSTCONDITION_CHECKED", outcome="FAILED"),
        ],
    )
    lead = build_investigation_lead(recon)
    assert lead.checks_status == "FAIL"
    assert lead.all_passed is False
    assert "1 of 2 postcondition verification checks failed." in lead.checks_successful


def test_investigation_lead_missing_facts_honest():
    """Missing facts report 'not recorded' instead of hallucinating."""
    recon = MockReconstruction(
        instrument_id="",
        evidence_claims=[],
        action_records=[],
        tasks=[],
        events=[],
    )
    lead = build_investigation_lead(recon)
    assert "not recorded" in lead.what_happened.lower()
    assert "0 experiments, 0 datasets, and 0 reports were affected." == lead.what_was_affected
    assert "No enforcement actions were recorded." == lead.actions_taken
    assert lead.checks_status == "NOT RECORDED"
    assert "not recorded" in lead.checks_successful.lower()


def test_milestones_derivation_exploratory():
    """Exploratory incident generates canonical milestones M1–M9 with M3b omitted."""
    recon = MockReconstruction(
        incident_id="inc-exp",
        instrument_id="inst-9",
        status="CLOSED",
        evidence_claims=[{
            "claim_id": "cert-c1",
            "source_type": "CALIBRATION_AUTHORITY",
            "trust_level": "AUTHORITATIVE",
            "valid_from": 1000,
            "valid_until": 2000,
        }],
        action_records=[
            {"action_type": "QUARANTINE_DATASET", "target_id": "ds-10"},
            {"action_type": "HOLD_REPORT", "target_id": "rpt-20"},
        ],
        tasks=[
            {"id": "t1", "status": "OPEN"},
            {"id": "t2", "status": "ESCALATED", "escalation_reason": "No owner"},
        ],
        events=[
            MockEvent(id="e1", seq=1, event_type="EVIDENCE_INGESTED"),
            MockEvent(id="e2", seq=2, event_type="HYPOTHESIS_CHECKED", outcome="CONFIRMED", summary="LAPSE confirmed"),
            MockEvent(id="e3", seq=3, event_type="POSTCONDITION_CHECKED", outcome="PASSED"),
            MockEvent(id="e4", seq=4, event_type="INCIDENT_CLOSED", at=1792231200),
        ],
    )
    impact_data = {
        "experiments": [{"id": "exp-1", "status": "NOT_COVERED"}],
        "datasets": [{"id": "ds-10", "current_status": "QUARANTINED"}],
        "reports": [{"id": "rpt-20", "current_status": "HELD"}],
    }
    playbook_data = {
        "playbook": {"id": "pb-derived-1", "status": "VERIFIED"},
        "run": None,  # Exploratory mode: no run
    }

    milestones = build_investigation_summary(
        recon,
        impact_data=impact_data,
        playbook_data=playbook_data,
    )

    keys = [m.key for m in milestones]
    assert "M1" in keys
    assert "M2" in keys
    assert "M3" in keys
    assert "M3b" not in keys, "M3b must be omitted for exploratory incident without PlaybookRun"
    assert "M4" in keys
    assert "M5" in keys
    assert "M6" in keys
    assert "M7" in keys
    assert "M8" in keys
    assert "M9" in keys

    m1 = next(m for m in milestones if m.key == "M1")
    assert m1.status == "COMPLETED"
    assert "cert-c1" in m1.statement

    m2 = next(m for m in milestones if m.key == "M2")
    assert m2.status == "COMPLETED"
    assert "1 experiment" in m2.statement

    m3 = next(m for m in milestones if m.key == "M3")
    assert "exploratory" in m3.statement.lower()

    m5 = next(m for m in milestones if m.key == "M5")
    assert m5.status == "COMPLETED"
    assert "1 dataset" in m5.statement

    m7 = next(m for m in milestones if m.key == "M7")
    assert m7.status == "COMPLETED"
    assert "1 escalated" in m7.statement

    m9 = next(m for m in milestones if m.key == "M9")
    assert m9.status == "COMPLETED"
    assert "CLOSED" in m9.statement


def test_milestones_derivation_reuse_with_m3b():
    """Playbook reuse incident includes M3b with exact step numbers and statuses."""
    recon = MockReconstruction(
        incident_id="inc-reuse",
        instrument_id="inst-9",
        status="CLOSED",
        events=[
            MockEvent(id="e1", seq=1, event_type="PLAYBOOK_STEP", outcome="DIFFERED"),
            MockEvent(id="e2", seq=2, event_type="PLAYBOOK_STEP", outcome="PASSED"),
            MockEvent(id="e3", seq=3, event_type="INCIDENT_CLOSED", at=1792231200),
        ],
    )
    playbook_data = {
        "playbook": {"id": "pb-verified", "source_incident_id": "inc-exp"},
        "run": {
            "id": "run-001",
            "mode": "VERIFIED_REUSE",
            "status": "COMPLETED",
            "step_results": [
                {"step_number": 1, "status": "DIFFERED"},
                {"step_number": 2, "status": "PASSED"},
                {"step_number": 3, "status": "PASSED"},
            ],
        },
    }

    milestones = build_investigation_summary(
        recon,
        playbook_data=playbook_data,
    )

    keys = [m.key for m in milestones]
    assert "M3b" in keys, "M3b must be present when PlaybookRun has StepResults"

    m3b = next(m for m in milestones if m.key == "M3b")
    assert m3b.status == "RECORDED"
    assert "step 1 differed" in m3b.statement.lower()
    assert "steps 2 and 3 passed" in m3b.statement.lower()


def test_milestone_m3b_uses_stored_step_order_not_list_position():
    """
    REPRODUCTION TEST FOR OUTCOME A (PLAYBOOK STEP ORDERING BUG):
    When StepResults arrive in arbitrary/reverse order (e.g. 7..1 from Cypher collect):
    1. Steps MUST be mapped to PlaybookStep.order, NEVER enumerate index over list position.
    2. Step 1 (DIFFERED), Step 2 (DIFFERED), Step 3 (DIFFERED), Step 4 (PASSED),
       Step 5 (PASSED), Step 6 (DIFFERED), Step 7 (PASSED).
    3. The statement MUST state: 'Steps 1, 2, 3 and 6 differed from the verified procedure; steps 4, 5 and 7 passed.'
       (Under the bug, it states 'Steps 2, 5, 6 and 7 differed... steps 1, 3 and 4 passed.')
    """
    recon = MockReconstruction(
        incident_id="inc-reuse-7step",
        status="CLOSED",
    )
    # 7 structured PlaybookSteps in order 1..7
    steps = [
        {"id": f"step_{i}", "order": i, "step_type": f"TYPE_{i}"}
        for i in range(1, 8)
    ]
    # StepResults in REVERSE order (7 down to 1), exactly as FalkorDB collect returned
    # Differed: steps 1, 2, 3, 6; Passed: steps 4, 5, 7
    status_map = {1: "DIFFERED", 2: "DIFFERED", 3: "DIFFERED", 4: "PASSED", 5: "PASSED", 6: "DIFFERED", 7: "PASSED"}
    reversed_step_results = [
        {"id": f"sr_{i}", "step_id": f"step_{i}", "status": status_map[i]}
        for i in range(7, 0, -1)
    ]

    playbook_data = {
        "playbook": {"id": "pb-1", "status": "VERIFIED"},
        "steps": steps,
        "run": {
            "id": "run-001",
            "mode": "VERIFIED_REUSE",
            "step_results": reversed_step_results,
        },
        "step_results": reversed_step_results,
    }

    milestones = build_investigation_summary(recon, playbook_data=playbook_data)
    m3b = next(m for m in milestones if m.key == "M3b")

    assert "Steps 1, 2, 3 and 6 differed" in m3b.statement
    assert "steps 4, 5 and 7 passed" in m3b.statement


def test_unclosed_incident_does_not_show_closure():
    """Unclosed incident shows closure as NOT RECORDED honestly."""
    recon = MockReconstruction(status="IN_PROGRESS", events=[])
    milestones = build_investigation_summary(recon)

    m9 = next(m for m in milestones if m.key == "M9")
    assert m9.status == "NOT RECORDED"
    assert "remains IN_PROGRESS" in m9.statement


def test_stored_status_closed_without_closing_event_shows_not_recorded():
    """
    REPRODUCTION TEST FOR CLOSURE BUG:
    When stored status is CLOSED but no INCIDENT_CLOSED event exists (like INC-2026-001):
    1. Milestone M9 status MUST be NOT RECORDED (never COMPLETED).
    2. No closure timestamp may be shown (specifically opened_at MUST NOT be used).
    3. Milestone statement must honestly say: 'Stored status is CLOSED; no closing event is recorded.'
    4. Supporting sequences must be empty (never contain opened_at or unrelated event seqs).
    """
    recon = MockReconstruction(
        incident_id="inc-missing-close",
        status="CLOSED",
        opened_at=1792227600,
        events=[
            MockEvent(id="e1", seq=1, event_type="POSTCONDITION_CHECKED", outcome="PASSED"),
        ],
    )
    milestones = build_investigation_summary(recon)
    m9 = next(m for m in milestones if m.key == "M9")
    assert m9.status == "NOT RECORDED", "A stored status of CLOSED must not turn a missing event into COMPLETED"
    assert "2026-10-17" not in m9.statement, "opened_at must never be substituted for closure time"
    assert "Stored status is CLOSED; no closing event is recorded." in m9.statement
    assert m9.supporting_seqs == []



def test_routine_query_and_handoff_events_never_become_milestones():
    """Routine queries, handoff events, and arbitrary events do not create milestones."""
    events = [
        MockEvent(id="q1", seq=1, event_type="QUERY_EXECUTED"),
        MockEvent(id="h1", seq=2, event_type="HANDOFF_CREATED"),
        MockEvent(id="h2", seq=3, event_type="HANDOFF_COMPLETED"),
    ]
    recon = MockReconstruction(events=events)
    milestones = build_investigation_summary(recon)

    # Standard milestone keys only
    valid_keys = {"M1", "M2", "M3", "M3b", "M4", "M5", "M6", "M7", "M8", "M9"}
    for m in milestones:
        assert m.key in valid_keys
        assert "QUERY_EXECUTED" not in m.label
        assert "HANDOFF" not in m.label


def test_incident_isolation():
    """Two incidents processed in sequence never cross-pollinate data."""
    recon_a = MockReconstruction(
        incident_id="INC-AAA",
        instrument_id="INST-AAA",
        evidence_claims=[{"claim_id": "CERT-AAA"}],
        action_records=[{"action_type": "QUARANTINE_DATASET", "target_id": "DS-AAA"}],
    )
    recon_b = MockReconstruction(
        incident_id="INC-BBB",
        instrument_id="INST-BBB",
        evidence_claims=[{"claim_id": "CERT-BBB"}],
        action_records=[{"action_type": "QUARANTINE_DATASET", "target_id": "DS-BBB"}],
    )

    summary_a = build_investigation_summary(recon_a)
    summary_b = build_investigation_summary(recon_b)

    text_a = " ".join(m.statement for m in summary_a)
    text_b = " ".join(m.statement for m in summary_b)

    assert "AAA" in text_a and "BBB" not in text_a
    assert "BBB" in text_b and "AAA" not in text_b


def test_determinism():
    """Summary generation is strictly deterministic."""
    recon = MockReconstruction(
        incident_id="inc-det",
        instrument_id="inst-det",
        evidence_claims=[{"claim_id": "cert-det"}],
    )
    res1 = build_investigation_summary(recon)
    res2 = build_investigation_summary(recon)

    assert len(res1) == len(res2)
    for m1, m2 in zip(res1, res2):
        assert m1.key == m2.key
        assert m1.label == m2.label
        assert m1.statement == m2.statement
        assert m1.status == m2.status
        assert m1.supporting_seqs == m2.supporting_seqs


def test_static_guards_no_hardcoded_ids_or_html():
    """Verify summary and inspector modules have zero hardcoded demo IDs, HTML tags, or unsafe_allow_html."""
    project_root = Path(__file__).resolve().parents[2]
    summary_file = project_root / "temproven" / "app" / "summary.py"
    inspector_file = project_root / "temproven" / "app" / "inspector.py"

    assert summary_file.exists()
    assert inspector_file.exists()

    summary_code = summary_file.read_text(encoding="utf-8")
    inspector_code = inspector_file.read_text(encoding="utf-8")

    # 1. Zero unsafe_allow_html
    assert "unsafe_allow_html" not in summary_code, "summary.py must have zero calls to unsafe_allow_html"
    assert "unsafe_allow_html" not in inspector_code, "inspector.py must have zero calls to unsafe_allow_html"

    # 2. Zero HTML tag markup strings (<div, <span, <table, <p>, etc.)
    html_tags = re.compile(r"<\s*(div|span|table|tr|td|th|p|b|i|strong|em|h[1-6]|ul|ol|li)\b", re.IGNORECASE)
    assert not html_tags.search(summary_code), "summary.py must have zero HTML tag strings"
    assert not html_tags.search(inspector_code), "inspector.py must have zero HTML tag strings"

    # 3. No hardcoded demo IDs (INC-2026-..., DS-A..., RPT-A..., INS-A..., CERT-A...)
    demo_id_pattern = re.compile(r"\b(INC-2026-\d+|DS-[AB]\d+|RPT-[AB]\d+|EXP-[AB]\d+|INS-[AB]\d+|CERT-[AB]\d+)\b")
    assert not demo_id_pattern.search(summary_code), "summary.py must not contain hardcoded demo IDs"
    assert not demo_id_pattern.search(inspector_code), "inspector.py must not contain hardcoded demo IDs"


def test_investigation_lead_report_requires_review():
    """Lead answers accurately describe REQUIRES_REVIEW reports and do not call them held."""
    recon = MockReconstruction(
        action_records=[
            {"action_type": "FLAG_REPORT_REVIEW", "target_id": "rpt-rev-1"},
        ],
    )
    lead = build_investigation_lead(recon)
    assert "1 report requires review (rpt-rev-1)" in lead.actions_taken
    assert "held" not in lead.actions_taken


def test_investigation_lead_both_held_and_review():
    """Lead answers distinguish held and review reports when both are present."""
    recon = MockReconstruction(
        action_records=[
            {"action_type": "HOLD_REPORT", "target_id": "rpt-h1"},
            {"action_type": "FLAG_REPORT_REVIEW", "target_id": "rpt-r1"},
        ],
    )
    lead = build_investigation_lead(recon)
    assert "1 report held (rpt-h1)" in lead.actions_taken
    assert "1 report requires review (rpt-r1)" in lead.actions_taken


def test_incident_closure_with_present_closing_event():
    """
    Present INCIDENT_CLOSED event:
    1. Shows its own timestamp in UTC.
    2. Shows its own sequence number.
    3. Shows its own actor and outcome.
    4. Milestone status is COMPLETED.
    5. Supporting sequence contains exactly the closing event sequence.
    """
    recon = MockReconstruction(
        incident_id="INC-2026-002",
        status="CLOSED",
        opened_at=1792227600,
        events=[
            MockEvent(
                id="e73",
                seq=73,
                event_type="INCIDENT_CLOSED",
                at=1792238400,  # 2026-10-17 12:00:00 UTC
                actor_agent="IntegrityAgent",
                outcome="CLOSED",
            ),
        ],
    )
    closure = describe_incident_closure(recon)

    assert closure.has_closing_event is True
    assert closure.stored_status == "CLOSED"
    assert closure.closing_seq == 73
    assert closure.closing_time_utc == "2026-10-17 12:00:00 UTC"
    assert closure.actor == "IntegrityAgent"
    assert closure.outcome == "CLOSED"
    assert closure.milestone_status == "COMPLETED"
    assert closure.supporting_seqs == [73]
    assert "Closing event recorded at 2026-10-17 12:00:00 UTC [Seq #73] by IntegrityAgent (outcome: CLOSED). Stored status: CLOSED." == closure.milestone_statement

    # Also verify full summary milestone M9
    milestones = build_investigation_summary(recon)
    m9 = next(m for m in milestones if m.key == "M9")
    assert m9.status == "COMPLETED"
    assert m9.supporting_seqs == [73]
    assert "Seq #73" in m9.statement
    assert "12:00:00" in m9.statement


def test_incident_closure_anomaly_not_closed_with_closing_event():
    """
    Anomaly case: stored status is not CLOSED, but closing event exists.
    Truthful presentation states both facts without masking either.
    """
    recon = MockReconstruction(
        incident_id="inc-anomaly",
        status="IN_PROGRESS",
        events=[
            MockEvent(
                id="e10",
                seq=10,
                event_type="INCIDENT_CLOSED",
                at=1792238400,
                actor_agent="IntegrityAgent",
                outcome="CLOSED",
            ),
        ],
    )
    closure = describe_incident_closure(recon)
    assert closure.has_closing_event is True
    assert closure.stored_status == "IN_PROGRESS"
    assert "stored status remains IN_PROGRESS" in closure.milestone_statement
    assert closure.milestone_status == "COMPLETED"


def test_closure_static_guard_no_opened_at_fallback():
    """
    STATIC GUARD:
    Statically verify that describe_incident_closure in presenters.py NEVER
    references opened_at or attempts fallback substitution.
    """
    project_root = Path(__file__).resolve().parents[2]
    presenters_file = project_root / "temproven" / "app" / "presenters.py"
    code = presenters_file.read_text(encoding="utf-8")

    # Extract describe_incident_closure function body
    match = re.search(r"def describe_incident_closure\(.*?\n(?=def |\Z)", code, re.DOTALL)
    assert match is not None, "describe_incident_closure function must exist in presenters.py"
    fn_body = match.group(0)

    # Filter out docstrings and comments to inspect only executable code
    exec_lines = [
        line for line in fn_body.splitlines()
        if not line.strip().startswith(("#", '"""', "'''", "*")) and "substitutes" not in line
    ]
    exec_code = "\n".join(exec_lines)
    assert "opened_at" not in exec_code, "describe_incident_closure must never reference opened_at in executable code"


def test_describe_single_report_restriction():
    """Unit tests for describe_single_report_restriction covering all branches."""
    # 1. HELD report
    d1 = describe_single_report_restriction(
        "RPT-3",
        current_status="HELD",
        action_records=[{"action_type": "HOLD_REPORT", "target_id": "RPT-3", "new_state": "HELD"}],
    )
    assert d1.restriction_kind == "HELD"
    assert d1.wording == "held"
    assert d1.full_description == "Report RPT-3 held"

    # 2. REQUIRES_REVIEW report
    d2 = describe_single_report_restriction(
        "RPT-1",
        current_status="REQUIRES_REVIEW",
        action_records=[{"action_type": "FLAG_REPORT_REVIEW", "target_id": "RPT-1", "new_state": "REQUIRES_REVIEW"}],
    )
    assert d2.restriction_kind == "REQUIRES_REVIEW"
    assert d2.wording == "requires review"
    assert d2.full_description == "Report RPT-1 requires review"

    # 3. Differing ActionRecord state vs current status: action was HELD but current status is DRAFT
    d3 = describe_single_report_restriction(
        "RPT-X",
        current_status="DRAFT",
        action_records=[{"action_type": "HOLD_REPORT", "target_id": "RPT-X", "new_state": "HELD"}],
    )
    assert d3.restriction_kind == "HELD"
    assert d3.wording == "marked HELD; current status: DRAFT"
    assert "marked HELD; current status: DRAFT" in d3.full_description

    # 4. Differing ActionRecord state vs current status: action was FLAG_REPORT_REVIEW but status is DRAFT
    d4 = describe_single_report_restriction(
        "RPT-Y",
        current_status="DRAFT",
        action_records=[{"action_type": "FLAG_REPORT_REVIEW", "target_id": "RPT-Y", "new_state": "REQUIRES_REVIEW"}],
    )
    assert d4.restriction_kind == "REQUIRES_REVIEW"
    assert d4.wording == "marked REQUIRES_REVIEW; current status: DRAFT"

    # 5. Control report unaffected (PUBLISHED)
    d5 = describe_single_report_restriction(
        "RPT-2",
        current_status="PUBLISHED",
        action_records=[],
    )
    assert d5.restriction_kind == "NONE"
    assert d5.wording == "unrestricted"
    assert "unaffected (PUBLISHED)" in d5.full_description


def test_describe_incident_report_restrictions_aggregation():
    """Unit tests for describe_incident_report_restrictions covering combinations and pluralization."""
    # 1. Single held
    r1 = describe_incident_report_restrictions(
        action_records=[{"action_type": "HOLD_REPORT", "target_id": "RPT-3"}],
        reports_data=[{"id": "RPT-3", "current_status": "HELD"}],
    )
    assert r1.outcome_strip_label == "1 held (RPT-3)"
    assert r1.lead_action_text == "1 report held (RPT-3)"
    assert "1 downstream report (RPT-3): held pending revalidation." in r1.milestone_statement
    assert r1.held_ids == ["RPT-3"]
    assert r1.review_ids == []

    # 2. Single requires review
    r2 = describe_incident_report_restrictions(
        action_records=[{"action_type": "FLAG_REPORT_REVIEW", "target_id": "RPT-1"}],
        reports_data=[{"id": "RPT-1", "current_status": "REQUIRES_REVIEW"}],
    )
    assert r2.outcome_strip_label == "1 requires review (RPT-1)"
    assert r2.lead_action_text == "1 report requires review (RPT-1)"
    assert "1 downstream report (RPT-1): requires review." in r2.milestone_statement
    assert r2.held_ids == []
    assert r2.review_ids == ["RPT-1"]

    # 3. Both held and review (never merged under "held")
    r3 = describe_incident_report_restrictions(
        action_records=[
            {"action_type": "HOLD_REPORT", "target_id": "RPT-3"},
            {"action_type": "FLAG_REPORT_REVIEW", "target_id": "RPT-1"},
        ],
        reports_data=[
            {"id": "RPT-3", "current_status": "HELD"},
            {"id": "RPT-1", "current_status": "REQUIRES_REVIEW"},
            {"id": "RPT-2", "current_status": "PUBLISHED"},
        ],
    )
    assert r3.outcome_strip_label == "1 held (RPT-3); 1 require review (RPT-1)"
    assert r3.lead_action_text == "1 report held (RPT-3); 1 report requires review (RPT-1)"
    assert "1 downstream report held (RPT-3) and 1 downstream report requiring review (RPT-1)." in r3.milestone_statement
    assert r3.held_ids == ["RPT-3"]
    assert r3.review_ids == ["RPT-1"]
    assert "RPT-2" in r3.unrestricted_ids

    # 4. Multiple held (pluralization check)
    r4 = describe_incident_report_restrictions(
        action_records=[
            {"action_type": "HOLD_REPORT", "target_id": "RPT-A"},
            {"action_type": "HOLD_REPORT", "target_id": "RPT-B"},
        ],
        reports_data=[
            {"id": "RPT-A", "current_status": "HELD"},
            {"id": "RPT-B", "current_status": "HELD"},
        ],
    )
    assert r4.outcome_strip_label == "2 held (RPT-A, RPT-B)"
    assert r4.lead_action_text == "2 reports held (RPT-A, RPT-B)"
    assert "2 downstream reports (RPT-A, RPT-B): held pending revalidation." in r4.milestone_statement

    # 5. Zero restricted
    r5 = describe_incident_report_restrictions(action_records=[], reports_data=[])
    assert r5.outcome_strip_label == "0 restricted"
    assert r5.lead_action_text == ""
    assert r5.milestone_statement == "Report restrictions were not recorded for this incident."


def test_build_ordered_step_results_comprehensive():
    """
    Comprehensive tests for build_ordered_step_results:
    1. Handles arbitrary/shuffled/reversed StepResults input correctly sorted by stored order.
    2. Handles missing steps, None, or empty input gracefully without errors.
    3. Milestone statement grammar:
       - Single differed, multiple passed.
       - Multiple differed, single passed.
       - All passed.
       - All differed.
    4. Two distinct playbooks / runs remain isolated without state leak.
    """
    # 1. Shuffled step results sorted strictly by PlaybookStep.order
    steps = [
        {"id": "step_c", "order": 3, "step_type": "CHECK_LINEAGE", "kind": "READ", "role": "CONFIRM"},
        {"id": "step_a", "order": 1, "step_type": "CONFIRM_LAPSE", "kind": "READ", "role": "CONFIRM"},
        {"id": "step_d", "order": 4, "step_type": "QUARANTINE_DATA", "kind": "WRITE", "role": "REMEDIATE"},
        {"id": "step_b", "order": 2, "step_type": "TRACE_DEPENDENTS", "kind": "READ", "role": "CONFIRM"},
    ]
    # In randomized order
    shuffled_results = [
        {"id": "res_d", "step_id": "step_d", "status": "PASSED"},
        {"id": "res_a", "step_id": "step_a", "status": "DIFFERED"},
        {"id": "res_c", "step_id": "step_c", "status": "PASSED"},
        {"id": "res_b", "step_id": "step_b", "status": "DIFFERED"},
    ]
    pb_data = {
        "steps": steps,
        "step_results": shuffled_results,
    }
    summary = build_ordered_step_results(pb_data)
    assert summary.has_results is True
    assert [s.step_number for s in summary.ordered_steps] == [1, 2, 3, 4]
    assert [s.step_id for s in summary.ordered_steps] == ["step_a", "step_b", "step_c", "step_d"]
    assert summary.differed_step_numbers == [1, 2]
    assert summary.passed_step_numbers == [3, 4]
    assert summary.milestone_statement == "Steps 1 and 2 differed from the verified procedure; steps 3 and 4 passed."

    # 2. Single differed, multiple passed
    res_single_diff = [
        {"step_id": "step_a", "status": "PASSED"},
        {"step_id": "step_b", "status": "DIFFERED"},
        {"step_id": "step_c", "status": "PASSED"},
        {"step_id": "step_d", "status": "PASSED"},
    ]
    summary_sd = build_ordered_step_results({"steps": steps, "step_results": res_single_diff})
    assert summary_sd.milestone_statement == "Step 2 differed from the verified procedure; steps 1, 3 and 4 passed."

    # 3. Multiple differed, single passed
    res_single_pass = [
        {"step_id": "step_a", "status": "DIFFERED"},
        {"step_id": "step_b", "status": "DIFFERED"},
        {"step_id": "step_c", "status": "PASSED"},
        {"step_id": "step_d", "status": "DIFFERED"},
    ]
    summary_sp = build_ordered_step_results({"steps": steps, "step_results": res_single_pass})
    assert summary_sp.milestone_statement == "Steps 1, 2 and 4 differed from the verified procedure; step 3 passed."

    # 4. All passed
    res_all_pass = [
        {"step_id": "step_a", "status": "PASSED"},
        {"step_id": "step_b", "status": "PASSED"},
        {"step_id": "step_c", "status": "PASSED"},
        {"step_id": "step_d", "status": "PASSED"},
    ]
    summary_ap = build_ordered_step_results({"steps": steps, "step_results": res_all_pass})
    assert summary_ap.milestone_statement == "All 4 steps passed in verified reuse (1, 2, 3 and 4)."

    # 5. All differed
    res_all_diff = [
        {"step_id": "step_a", "status": "DIFFERED"},
        {"step_id": "step_b", "status": "DIFFERED"},
        {"step_id": "step_c", "status": "DIFFERED"},
        {"step_id": "step_d", "status": "DIFFERED"},
    ]
    summary_ad = build_ordered_step_results({"steps": steps, "step_results": res_all_diff})
    assert summary_ad.milestone_statement == "Steps 1, 2, 3 and 4 differed from the verified procedure."

    # 6. Empty / None input
    summary_empty = build_ordered_step_results(None)
    assert summary_empty.has_results is False
    assert summary_empty.ordered_steps == []
    assert summary_empty.milestone_statement == ""

    summary_no_results = build_ordered_step_results({"steps": steps, "step_results": []})
    assert summary_no_results.has_results is False
    assert len(summary_no_results.ordered_steps) == 4
    assert summary_no_results.ordered_steps[0].status == "PENDING"
    assert summary_no_results.milestone_statement == ""


def test_describe_incident_tasks_comprehensive():
    """
    Comprehensive tests for describe_incident_tasks:
    1. Count reconciliation: total == escalated + assigned + other.
    2. Zero tasks returns honest 'not recorded' state.
    3. All assigned tasks lists distinct assignees.
    4. All escalated tasks safely escapes reasons and preserves counts.
    5. Mixed tasks reconciles mutually exclusive categories.
    6. Hostile injection in escalation reason is rendered inert without markup.
    """
    # 1. Zero tasks
    t0 = describe_incident_tasks([])
    assert t0.total_count == 0
    assert t0.assigned_count == 0
    assert t0.escalated_count == 0
    assert t0.other_count == 0
    assert t0.milestone_status == "NOT RECORDED"
    assert t0.milestone_statement == "Investigation tasks were not recorded for this incident."
    assert t0.lead_action_text == "no tasks recorded"

    # None input
    t_none = describe_incident_tasks(None)
    assert t_none.total_count == 0
    assert t_none.milestone_status == "NOT RECORDED"

    # 2. All assigned tasks (like INC-2026-001 scenario without escalation)
    tasks_assigned = [
        {"task_id": "t1", "status": "ASSIGNED", "assignee_name": "Dr. Alice Chen"},
        {"task_id": "t2", "status": "ASSIGNED", "assignee_name": "Dr. Bob Martinez"},
        {"task_id": "t3", "status": "IN_PROGRESS", "assignee_id": "usr-003"},
    ]
    t_asgn = describe_incident_tasks(tasks_assigned)
    assert t_asgn.total_count == 3
    assert t_asgn.assigned_count == 3
    assert t_asgn.escalated_count == 0
    assert t_asgn.other_count == 0
    assert t_asgn.milestone_status == "COMPLETED"
    assert t_asgn.total_count == t_asgn.assigned_count + t_asgn.escalated_count + t_asgn.other_count
    assert "Created 3 tasks: 3 assigned to owners." in t_asgn.milestone_statement
    assert "3 review tasks created (3 assigned)" in t_asgn.lead_action_text

    # 3. All escalated tasks (like INC-2026-002: 2 escalated with single distinct reason)
    tasks_escalated = [
        {"task_id": "t1", "status": "ESCALATED", "escalation_reason": "No owner found in graph"},
        {"task_id": "t2", "status": "ESCALATED", "escalation_reason": "No owner found in graph"},
    ]
    t_esc = describe_incident_tasks(tasks_escalated)
    assert t_esc.total_count == 2
    assert t_esc.assigned_count == 0
    assert t_esc.escalated_count == 2
    assert t_esc.other_count == 0
    assert t_esc.total_count == t_esc.assigned_count + t_esc.escalated_count + t_esc.other_count
    assert t_esc.distinct_reasons == [("No owner found in graph", 2)]
    assert "Created 2 tasks: 2 escalated (No owner found in graph)." in t_esc.milestone_statement
    assert "2 review tasks created (2 escalated: No owner found in graph)" in t_esc.lead_action_text

    # 4. Mixed tasks: 1 escalated, 2 assigned, 1 unassigned/pending
    tasks_mixed = [
        {"task_id": "t1", "status": "ASSIGNED", "assignee_name": "Dr. Alice Chen"},
        {"task_id": "t2", "status": "ASSIGNED", "assignee_name": "Dr. Bob Martinez"},
        {"task_id": "t3", "status": "ESCALATED", "escalation_reason": "Senior review required"},
        {"task_id": "t4", "status": "PENDING"},
    ]
    t_mix = describe_incident_tasks(tasks_mixed)
    assert t_mix.total_count == 4
    assert t_mix.assigned_count == 2
    assert t_mix.escalated_count == 1
    assert t_mix.other_count == 1
    assert t_mix.total_count == t_mix.assigned_count + t_mix.escalated_count + t_mix.other_count
    assert "Created 4 tasks: 2 assigned to owners; 1 escalated (Senior review required); 1 other (PENDING)." in t_mix.milestone_statement
    assert "4 review tasks created (2 assigned, 1 escalated: Senior review required, 1 other)" in t_mix.lead_action_text

    # 5. Hostile injection in escalation reason
    hostile_reason = "<script>alert('xss')</script> & <div style='color:red'>fail</div>"
    tasks_hostile = [
        {"task_id": "th1", "status": "ESCALATED", "escalation_reason": hostile_reason},
    ]
    t_hostile = describe_incident_tasks(tasks_hostile)
    assert t_hostile.total_count == 1
    assert t_hostile.escalated_count == 1
    # Check that angle brackets are escaped into &lt; / &gt; or inert strings
    assert "<script>" not in t_hostile.milestone_statement
    assert "&lt;script&gt;" in t_hostile.milestone_statement
    assert "<script>" not in t_hostile.lead_action_text
    assert "&lt;script&gt;" in t_hostile.lead_action_text


