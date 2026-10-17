"""
tests/unit/test_ui_presenters.py
────────────────────────────────
Unit tests for pure UI presentation logic in temproven/app/presenters.py:
- Exhaustive coverage of domain enums in STATUS_MAP
- Proper bitemporal timestamp formatting (UTC primary, IST secondary, valid vs recorded separated)
- Graphviz DOT diagram generation with HTML/character escaping
"""

import pytest

from temproven.app.presenters import (
    STATUS_MAP,
    StatusBadge,
    build_lineage_dot,
    escape,
    format_interval,
    format_timestamp,
    get_status_badge,
)
from temproven.domain.gate import GateDecision
from temproven.domain.temporal import CoverageStatus
from temproven.memory.episodic import EventType
from temproven.memory.procedural import StepStatus


def test_status_map_covers_all_domain_enums():
    """Verify that every core domain enum has an entry in STATUS_MAP."""
    # CoverageStatus
    for cs in CoverageStatus:
        badge = get_status_badge(cs.value)
        assert isinstance(badge, StatusBadge)
        assert badge.text == cs.value
        assert badge.style_class in ("success", "danger", "warning", "neutral")

    # GateDecision
    for gd in GateDecision:
        badge = get_status_badge(gd.value)
        assert isinstance(badge, StatusBadge)
        assert badge.text == gd.value

    # StepStatus
    for ss in StepStatus:
        badge = get_status_badge(ss.value)
        assert isinstance(badge, StatusBadge)
        assert badge.text == ss.value

    # Common entity statuses
    common_statuses = [
        "ACTIVE", "QUARANTINED",
        "DRAFT", "PUBLISHED", "HELD", "REQUIRES_REVIEW",
        "OPEN", "ASSIGNED", "ESCALATED", "COMPLETED",
        "CANDIDATE", "VERIFIED", "DEPRECATED",
        "IN_PROGRESS", "CLOSED",
        "PENDING", "CLAIMED", "EXPIRED",
    ]
    for st in common_statuses:
        badge = get_status_badge(st)
        assert badge.text == st
        assert badge.style_class in ("success", "danger", "warning", "neutral")


def test_status_badge_fallback_on_unknown():
    """Unknown or None status returns safe fallback badge without throwing."""
    b_none = get_status_badge(None)
    assert b_none.text == "UNKNOWN"
    assert b_none.style_class == "neutral"

    b_custom = get_status_badge("SOME_WEIRD_STATE")
    assert b_custom.text == "SOME_WEIRD_STATE"
    assert b_custom.style_class == "neutral"


def test_format_timestamp_utc_and_ist():
    """Timestamp formatting produces UTC primary and IST secondary with exact offset."""
    # 2026-10-17 09:00:00 UTC = 1792227600
    # IST = UTC + 05:30 -> 14:30:00 IST
    epoch = 1792227600
    res = format_timestamp(epoch, include_ist=True)
    assert "2026-10-17 09:00:00 UTC" in res
    assert "14:30:00 IST" in res

    # Without IST
    res_no_ist = format_timestamp(epoch, include_ist=False)
    assert res_no_ist == "2026-10-17 09:00:00 UTC"
    assert "IST" not in res_no_ist


def test_format_timestamp_edge_cases():
    """Handles None and out-of-range epoch values safely."""
    assert format_timestamp(None) == "TIME: None"
    assert format_timestamp(None, time_label="RECORDED") == "RECORDED: None"
    assert "invalid epoch" in format_timestamp(10**16)


def test_format_interval_distinguishes_valid_time():
    """Interval formatting labels VALID TIME clearly and handles infinite bounds."""
    t1 = 1792227600  # 2026-10-17 09:00:00 UTC
    t2 = 1792238400  # 2026-10-17 12:00:00 UTC

    interval_str = format_interval(t1, t2, interval_kind="VALID TIME")
    assert "[VALID TIME]" in interval_str
    assert "2026-10-17 09:00:00 UTC" in interval_str
    assert "2026-10-17 12:00:00 UTC" in interval_str

    inf_interval = format_interval(None, t2)
    assert "−∞" in inf_interval
    assert "+∞" not in inf_interval

    inf_interval_end = format_interval(t1, None)
    assert "+∞" in inf_interval_end


def test_escape_against_injection():
    """Ensure HTML and special characters are safely escaped."""
    raw = '<script>alert("xss")</script> & "quotes"'
    escaped = escape(raw)
    assert "<script>" not in escaped
    assert "&lt;script&gt;" in escaped
    assert "&quot;" in escaped
    assert escape(None) == ""


def test_build_lineage_dot_empty_and_populated():
    """Test Graphviz DOT generation with empty and populated inputs."""
    # Empty inputs
    dot_empty = build_lineage_dot(None, [], [], [])
    assert "digraph Lineage {" in dot_empty
    assert dot_empty.strip().endswith("}")

    # Populated inputs with potential dangerous characters
    experiments = [
        {"id": "EXP-1", "status": "COVERED"},
        {"id": "EXP-2<bad>", "status": "NOT_COVERED"},
    ]
    datasets = [
        {
            "id": "DS-1",
            "current_status": "QUARANTINED",
            "dependency_path": ["EXP-2<bad>", "PRODUCED", "DS-1"],
        }
    ]
    reports = [
        {
            "id": "RPT-1",
            "current_status": "HELD",
            "dependency_path": ["DS-1", "USES_DATASET", "RPT-1"],
        }
    ]

    dot = build_lineage_dot("INS-ALPHA", experiments, datasets, reports)
    assert "INS-ALPHA" in dot
    assert "EXP-1" in dot
    assert "&lt;bad&gt;" in dot  # Escaped
    assert "PRODUCED" in dot
    assert "QUARANTINED" in dot
    assert "HELD" in dot
    assert "USES_DATASET" not in dot  # Replaced with label="USES"
    assert "USES" in dot
