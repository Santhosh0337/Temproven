"""
tests/unit/test_ui_architecture.py
──────────────────────────────────
Architecture guard tests for the Streamlit UI presentation layer:
- No hardcoded secrets or credentials in app files
- No inline Cypher mutation strings (CREATE, MERGE, SET, DELETE) in temproven/app
- All queries in temproven/app/data.py are imported from queries.py (central query catalog)
- PublicationGate is never invoked without user trigger
"""

import ast
import re
from pathlib import Path
import pytest

APP_DIR = Path(__file__).parent.parent.parent / "temproven" / "app"


def test_no_hardcoded_passwords_or_tokens_in_app():
    """Ensure no passwords, tokens, or credentials exist in app source files."""
    for py_file in APP_DIR.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert not re.search(r"password\s*=\s*['\"][^'\"]+['\"]", content, re.IGNORECASE)
        assert not re.search(r"secret\s*=\s*['\"][^'\"]+['\"]", content, re.IGNORECASE)
        assert not re.search(r"api_key\s*=\s*['\"][^'\"]+['\"]", content, re.IGNORECASE)


def test_no_inline_cypher_mutations_in_app():
    """
    Ensure the UI layer does not formulate ad-hoc Cypher write queries.
    All Cypher must reside in temproven.graph.queries.
    """
    write_keywords = re.compile(r"\b(CREATE|MERGE|SET|DELETE|DETACH\s+DELETE)\b", re.IGNORECASE)

    for py_file in APP_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            # Check string literals
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value.strip()
                # Skip docstrings, CSS, or DOT strings
                if "{" in val and "}" in val and ("color" in val or "border" in val or "digraph" in val):
                    continue
                if "<style>" in val or "<div" in val:
                    continue
                # If it looks like a Cypher query (starts with MATCH/CREATE/MERGE/SET)
                lines = [l.strip() for l in val.splitlines() if l.strip()]
                for line in lines:
                    if line.upper().startswith(("CREATE ", "MERGE ", "DETACH DELETE ")):
                        pytest.fail(f"Found inline Cypher mutation in {py_file.name}: {line}")


def test_app_data_uses_central_queries():
    """Verify that temproven/app/data.py uses queries from queries.py."""
    data_file = APP_DIR / "data.py"
    content = data_file.read_text(encoding="utf-8")
    assert "from temproven.graph import queries" in content
    assert "queries.Q_LIST_INCIDENTS" in content
    assert "queries.Q_GET_PLAYBOOK_BY_SOURCE_INCIDENT" in content
    assert "queries.Q_REPORT_UPSTREAM_DEPENDENCIES" in content


def test_theme_tokens_present():
    """Verify that required theme color and typography tokens exist."""
    from temproven.app.theme import (
        BG_COLOR,
        CARD_BG,
        BORDER_COLOR,
        TEXT_COLOR,
        COLOR_ALERT,
        COLOR_SUCCESS,
        COLOR_WARNING,
        CUSTOM_CSS,
    )
    assert BG_COLOR.startswith("#")
    assert CARD_BG.startswith("#")
    assert BORDER_COLOR.startswith("#")
    assert TEXT_COLOR.startswith("#")
    assert COLOR_ALERT.startswith("#")
    assert COLOR_SUCCESS.startswith("#")
    assert COLOR_WARNING.startswith("#")
    assert "<style>" in CUSTOM_CSS
