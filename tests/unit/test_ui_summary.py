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
    LeadAnswers,
    Milestone,
    _plural,
    build_investigation_lead,
    build_investigation_summary,
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
    assert "1 report restricted from publication" in lead.actions_taken
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


def test_unclosed_incident_does_not_show_closure():
    """Unclosed incident shows closure as NOT RECORDED honestly."""
    recon = MockReconstruction(status="IN_PROGRESS", events=[])
    milestones = build_investigation_summary(recon)

    m9 = next(m for m in milestones if m.key == "M9")
    assert m9.status == "NOT RECORDED"
    assert "remains IN_PROGRESS" in m9.statement


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
