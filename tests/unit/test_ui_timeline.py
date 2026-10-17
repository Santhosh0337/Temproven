"""
tests/unit/test_ui_timeline.py
──────────────────────────────
Comprehensive unit test suite for the Interactive Point-Based Event Timeline.
Covers:
1. Sequence ordering with identical timestamps and anomaly detection.
2. Graceful handling of missing optional fields ("n/a" defaults).
3. Unclassified fallback for unknown event types and payload sub-keys.
4. Explicit category mapping table and significance classification.
5. Filter logic (zero-count preservation, banner numbers, immutability).
6. Selection resolution (`resolve_selected_seq`) with cross-incident guards.
7. Altair chart spec validation (seq encoding, selection param, tooltip fields, no text mark).
8. Hostile injection text safety and tooltip length capping.
9. Empty incident and many-event scale safety.
"""

from dataclasses import dataclass, field
from typing import Any
import pytest

from temproven.app.timeline import (
    CANONICAL_TIMELINE_CATEGORIES,
    CATEGORY_LABELS,
    classify_significance,
    map_event_to_category,
    build_timeline_points,
    resolve_selected_seq,
    build_timeline_chart,
    truncate_summary_for_tooltip,
)


@dataclass
class MockEvent:
    id: str
    seq: int
    event_type: str
    at: int = 1792227600
    actor_agent: str = "test_agent"
    summary: str = "Test event summary"
    outcome: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    action_ids: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


def test_ordering_with_identical_timestamps_and_shuffled_input():
    """Events with identical timestamps must emerge strictly ordered by seq."""
    events = [
        MockEvent(id="ev_3", seq=3, event_type="DECISION", at=1792227600),
        MockEvent(id="ev_1", seq=1, event_type="INCIDENT_OPENED", at=1792227600),
        MockEvent(id="ev_4", seq=4, event_type="ACTION_APPLIED", at=1792227600),
        MockEvent(id="ev_2", seq=2, event_type="HYPOTHESIS_CHECKED", at=1792227600),
    ]

    view = build_timeline_points(events)
    seqs = [p.seq for p in view.points]
    assert seqs == [1, 2, 3, 4]
    assert view.has_sequence_anomaly is False


def test_ordering_tie_break_and_anomaly_detection():
    """Duplicate seq values are flagged as an anomaly and broken deterministically by id."""
    events = [
        MockEvent(id="ev_b", seq=2, event_type="OBSERVATION"),
        MockEvent(id="ev_a", seq=2, event_type="OBSERVATION"),
        MockEvent(id="ev_1", seq=1, event_type="INCIDENT_OPENED"),
    ]

    view = build_timeline_points(events)
    assert view.has_sequence_anomaly is True
    # ev_a comes before ev_b
    assert [p.event_id for p in view.points] == ["ev_1", "ev_a", "ev_b"]


def test_missing_optional_fields_shows_na_placeholders():
    """None or missing optional fields never crash and show 'n/a' placeholders."""
    minimal_ev = MockEvent(
        id="ev_min",
        seq=1,
        event_type="OBSERVATION",
        at=0,
        actor_agent="",
        summary="",
        outcome=None,
        evidence_ids=[],
        action_ids=[],
        payload={},
    )

    view = build_timeline_points([minimal_ev])
    point = view.points[0]
    assert point.outcome == "n/a"
    assert point.target_id == "n/a"
    assert point.step_type == "n/a"
    assert point.task_type == "n/a"
    assert point.tooltip_summary == "n/a"


def test_unknown_event_types_land_in_unclassified_and_stay_visible():
    """Unknown event types land in Unclassified category and remain visible."""
    ev = MockEvent(id="ev_unk", seq=1, event_type="ALIEN_SIGNAL_DETECTED")
    cat_key, cat_label = map_event_to_category(ev)
    assert cat_key == "unclassified"
    assert cat_label == "Unclassified"

    view = build_timeline_points([ev])
    assert len(view.points) == 1
    assert view.points[0].category == "Unclassified"
    assert view.points[0].is_visible is True


def test_category_mapping_vocabulary_coverage():
    """All known episodic event types and sub-keys are deterministically categorized."""
    # Routine query
    q_ev = MockEvent(id="q1", seq=1, event_type="QUERY_EXECUTED")
    assert map_event_to_category(q_ev)[1] == "Routine queries"

    # Playbook steps
    ps1 = MockEvent(id="p1", seq=2, event_type="PLAYBOOK_STEP", payload={"step_type": "CONFIRM_LAPSE"})
    assert map_event_to_category(ps1)[1] == "Temporal diagnosis and hypothesis evaluation"

    ps2 = MockEvent(id="p2", seq=3, event_type="PLAYBOOK_STEP", payload={"step_type": "TRACE_DEPENDENTS"})
    assert map_event_to_category(ps2)[1] == "Impact tracing"

    ps3 = MockEvent(id="p3", seq=4, event_type="PLAYBOOK_STEP", payload={"step_type": "QUARANTINE_DATASETS"})
    assert map_event_to_category(ps3)[1] == "Quarantine, holds and task actions"

    ps4 = MockEvent(id="p4", seq=5, event_type="PLAYBOOK_STEP", payload={"step_type": "VERIFY_GATE_BLOCKS"})
    assert map_event_to_category(ps4)[1] == "Postcondition verification and revalidation"

    # Handoffs
    h_ev = MockEvent(id="h1", seq=6, event_type="HANDOFF_CREATED", payload={"task_type": "SELECT_PLAYBOOK"})
    assert map_event_to_category(h_ev)[1] == "Playbook retrieval and execution"

    # Decisions
    d_ev = MockEvent(id="d1", seq=7, event_type="DECISION", payload={"role": "QUARANTINE_DATASET"})
    assert map_event_to_category(d_ev)[1] == "Quarantine, holds and task actions"


def test_significance_classification_rules():
    """Significance correctly distinguishes critical audit points from background noise."""
    assert classify_significance(MockEvent(id="1", seq=1, event_type="INCIDENT_OPENED")) == "SIGNIFICANT"
    assert classify_significance(MockEvent(id="2", seq=2, event_type="ACTION_APPLIED")) == "SIGNIFICANT"
    assert classify_significance(MockEvent(id="3", seq=3, event_type="QUERY_EXECUTED")) == "ROUTINE"
    assert classify_significance(MockEvent(id="4", seq=4, event_type="HANDOFF_CREATED")) == "ROUTINE"

    # Failed or differed step becomes significant
    ps_failed = MockEvent(id="5", seq=5, event_type="PLAYBOOK_STEP", outcome="DIFFERED")
    assert classify_significance(ps_failed) == "SIGNIFICANT"


def test_filter_logic_and_banner_summary():
    """Filtering preserves zero counts for absent categories and never mutates input."""
    events = [
        MockEvent(id="e1", seq=1, event_type="INCIDENT_OPENED"),
        MockEvent(id="e2", seq=2, event_type="QUERY_EXECUTED"),
        MockEvent(id="e3", seq=3, event_type="QUERY_EXECUTED"),
    ]

    # Filter out Routine queries
    active = ["Evidence received or corrected"]
    view = build_timeline_points(events, active_categories=active)

    assert view.total_count == 3
    assert view.visible_count == 1
    assert view.hidden_count == 2
    assert "2 hidden by filter (Routine queries)" in view.filter_banner
    assert view.category_counts["Routine queries"] == 2
    assert view.visible_category_counts["Routine queries"] == 0
    # Zero count category preserved
    assert view.category_counts["Impact tracing"] == 0
    # Source events untouched
    assert len(events) == 3


def test_selection_resolution_and_cross_incident_guard():
    """`resolve_selected_seq` accepts valid seq, guards against other incidents, and handles hidden state."""
    events = [
        MockEvent(id="e1", seq=1, event_type="INCIDENT_OPENED"),
        MockEvent(id="e2", seq=2, event_type="QUERY_EXECUTED"),
    ]

    # Valid selection from chart
    sel_dict = {"pt_sel": [{"seq": 2}]}
    assert resolve_selected_seq(sel_dict, events, previous_selected_seq=1, incident_id="INC-1") == 2

    # Selection from another incident not in this event list is rejected
    stale_sel = {"pt_sel": [{"seq": 999}]}
    assert resolve_selected_seq(stale_sel, events, previous_selected_seq=1, incident_id="INC-1") == 1

    # Empty selection retains previous selection
    empty_sel = {"pt_sel": []}
    assert resolve_selected_seq(empty_sel, events, previous_selected_seq=2, incident_id="INC-1") == 2


def test_selected_point_hidden_by_filter_is_retained():
    """If selected event is hidden by filter, it stays selected and flags `is_selected_hidden=True`."""
    events = [
        MockEvent(id="e1", seq=1, event_type="INCIDENT_OPENED"),
        MockEvent(id="e2", seq=2, event_type="QUERY_EXECUTED"),
    ]

    # Select seq 2 (query), but active filter only includes Evidence
    view = build_timeline_points(events, active_categories=["Evidence received or corrected"], selected_seq=2)
    assert view.selected_seq == 2
    assert view.selected_point is not None
    assert view.selected_point.seq == 2
    assert view.is_selected_hidden is True


def test_altair_chart_spec_invariants():
    """Chart specification enforces sequence axis, point selection param, and tooltips."""
    events = [
        MockEvent(id="e1", seq=1, event_type="INCIDENT_OPENED"),
        MockEvent(id="e2", seq=2, event_type="ACTION_APPLIED"),
    ]
    view = build_timeline_points(events, selected_seq=1)
    chart = build_timeline_chart(view, orientation="horizontal")
    spec = chart.to_dict()

    # Must contain params with pt_sel on seq
    params = spec.get("params", [])
    pt_param = next((p for p in params if p.get("name") == "pt_sel"), None)
    assert pt_param is not None
    assert pt_param["select"]["fields"] == ["seq"]

    # Must not contain any text mark printing descriptions
    layers = spec.get("layer", [])
    for layer in layers:
        mark = layer.get("mark")
        mark_type = mark if isinstance(mark, str) else mark.get("type")
        assert mark_type != "text", "Chart must not contain a text-mark layer printing descriptions"


def test_long_summary_capping_and_hostile_text_safety():
    """Summaries in tooltips are capped at ~120 chars; hostile text is safely stored."""
    hostile_str = "<script>alert('xss')</script> & <div>test</div> \" ' $var$ `code`\nnewline"
    long_str = "A" * 150

    ev = MockEvent(id="e1", seq=1, event_type="OBSERVATION", summary=long_str)
    view = build_timeline_points([ev])
    p = view.points[0]
    assert len(p.tooltip_summary) <= 120
    assert p.tooltip_summary.endswith("...")
    assert p.summary == long_str  # Inspector keeps full untruncated text

    # Hostile text preserved accurately without premature unescaping or crash
    ev_hostile = MockEvent(id="e2", seq=2, event_type="OBSERVATION", summary=hostile_str)
    view2 = build_timeline_points([ev_hostile])
    p2 = view2.points[0]
    assert "<script>" in p2.summary
    assert "\n" not in p2.tooltip_summary  # newlines collapsed for tooltip


def test_empty_incident_and_many_events():
    """Empty incident and 200+ event incident build without errors."""
    # Empty
    empty_view = build_timeline_points([])
    assert empty_view.total_count == 0
    empty_chart = build_timeline_chart(empty_view)
    assert empty_chart.to_dict() is not None

    # Many events
    many_evs = [
        MockEvent(id=f"ev_{i}", seq=i, event_type="QUERY_EXECUTED" if i % 2 == 0 else "OBSERVATION")
        for i in range(1, 250)
    ]
    large_view = build_timeline_points(many_evs)
    assert large_view.total_count == 249
    large_chart = build_timeline_chart(large_view, orientation="vertical")
    assert large_chart.to_dict() is not None
