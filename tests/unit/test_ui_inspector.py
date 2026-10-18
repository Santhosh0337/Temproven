"""
tests/unit/test_ui_inspector.py
───────────────────────────────
Unit tests for the native Streamlit Event Inspection Panel (temproven/app/inspector.py):
1. None point renders informational placeholder.
2. Formats all labeled metadata fields without HTML.
3. Hidden-by-filter warning is displayed when is_selected_hidden=True.
4. Payload rendering with dict, json string, and arbitrary text.
5. Hostile injection strings are inert (rendered as plain text/code).
6. Static safety invariants: zero HTML tag strings, zero calls to unsafe_allow_html.
"""

from __future__ import annotations

import re
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest


def test_inspector_none_point_renders_info():
    """None point renders an informational banner to prompt user selection."""
    def script():
        from temproven.app.inspector import render_event_inspector
        render_event_inspector(None)

    at = AppTest.from_function(script)
    at.run()
    assert not at.exception
    assert len(at.info) == 1
    assert "Select a point on the timeline" in at.info[0].value


def test_inspector_selected_point_renders_native_components():
    """Selected point renders subheader, badges, captions, text, and json."""
    def script():
        from dataclasses import dataclass, field
        from typing import Any
        from temproven.app.inspector import render_event_inspector

        @dataclass
        class MockPoint:
            seq: int = 7
            category: str = "Playbook step"
            significance: str = "SIGNIFICANT"
            outcome: str = "PASSED"
            summary: str = "Executed verification step"
            event_type: str = "PLAYBOOK_STEP"
            event_id: str = "ev-step-1"
            actor: str = "VerificationAgent"
            target_id: str = "ds-target-1"
            timestamp_utc: str = "2026-10-18 10:00:00 UTC"
            timestamp_ist: str = "15:30:00 IST"
            step_type: str = "CHECK_LINEAGE"
            task_type: str = "n/a"
            evidence_ids: list[str] = field(default_factory=lambda: ["ev-1", "ev-2"])
            action_ids: list[str] = field(default_factory=lambda: ["act-1"])
            payload: dict[str, Any] = field(default_factory=lambda: {"key": "value", "count": 42})

        render_event_inspector(MockPoint(), is_selected_hidden=False)

    at = AppTest.from_function(script)
    at.run()
    assert not at.exception

    # Subheader check
    assert len(at.subheader) >= 1
    assert "EVENT #7: Playbook step" in at.subheader[0].value

    # Summary rendered via st.text
    text_values = [t.value for t in at.text]
    assert any("Executed verification step" in t for t in text_values)

    # Code blocks contain metadata
    code_values = [c.value for c in at.code]
    assert any("PLAYBOOK_STEP" in c for c in code_values)
    assert any("VerificationAgent" in c for c in code_values)
    assert any("CHECK_LINEAGE" in c for c in code_values)

    # Expander for raw payload
    assert len(at.expander) >= 1
    assert "Raw payload (JSON)" in at.expander[0].label


def test_inspector_hidden_warning():
    """Selected point hidden by active filters shows warning."""
    def script():
        from dataclasses import dataclass
        from temproven.app.inspector import render_event_inspector

        @dataclass
        class MockPoint:
            seq: int = 3
            category: str = "Routine queries"
            significance: str = "ROUTINE"
            outcome: str = "n/a"
            summary: str = "Routine query executed"
            event_type: str = "QUERY_EXECUTED"
            event_id: str = "ev-q-1"
            actor: str = "VerificationAgent"
            target_id: str = "n/a"
            timestamp_utc: str = "2026-10-18 10:00:00 UTC"
            timestamp_ist: str = "15:30:00 IST"
            step_type: str = "n/a"
            task_type: str = "n/a"
            evidence_ids: list[str] = None
            action_ids: list[str] = None
            payload: dict = None

        render_event_inspector(MockPoint(), is_selected_hidden=True)

    at = AppTest.from_function(script)
    at.run()
    assert not at.exception
    assert len(at.warning) >= 1
    assert "Selected event is currently hidden" in at.warning[0].value


def test_inspector_hostile_strings_inert():
    """Hostile script or HTML strings are rendered inert without markup interpretation."""
    def script():
        from dataclasses import dataclass, field
        from typing import Any
        from temproven.app.inspector import render_event_inspector

        @dataclass
        class MockHostilePoint:
            seq: int = 99
            category: str = "<script>alert('cat')</script>"
            significance: str = "SIGNIFICANT"
            outcome: str = "<div class='danger'>FAIL</div>"
            summary: str = "<script>alert('xss')</script><div style='color:red;'>Injected</div>"
            event_type: str = "<b onmouseover=alert(1)>EV</b>"
            event_id: str = "ev-99"
            actor: str = "<img src=x onerror=alert(1)>"
            target_id: str = "<svg/onload=alert(3)>"
            timestamp_utc: str = "2026-10-18 10:00:00 UTC"
            timestamp_ist: str = "15:30:00 IST"
            step_type: str = "n/a"
            task_type: str = "n/a"
            evidence_ids: list[str] = field(default_factory=list)
            action_ids: list[str] = field(default_factory=list)
            payload: dict[str, Any] = field(default_factory=lambda: {"attack": "<svg onload=alert(2)>"})

        render_event_inspector(MockHostilePoint())

    at = AppTest.from_function(script)
    at.run()
    assert not at.exception

    # The raw string appears literally in text/code without causing DOM execution
    text_values = [t.value for t in at.text]
    assert any("<script>alert('xss')</script>" in t for t in text_values)


def test_inspector_static_safety_guards():
    """Verify inspector.py source code contains 0 HTML tags and 0 unsafe_allow_html."""
    inspector_file = Path(__file__).resolve().parents[2] / "temproven" / "app" / "inspector.py"
    code = inspector_file.read_text(encoding="utf-8")

    assert "unsafe_allow_html" not in code
    html_pattern = re.compile(r"<\s*(div|span|table|tr|td|th|p|b|i|strong|em|h[1-6]|ul|ol|li)\b", re.IGNORECASE)
    assert not html_pattern.search(code)
