"""
temproven/app/inspector.py
──────────────────────────
Native Streamlit presentation component for the Event Inspection Panel.

Invariants:
- 100% Streamlit-native elements (columns, subheader, badge, caption, text, code, json, expander).
- ZERO HTML tag strings (no div, span, table, style, or class markup).
- ZERO calls to raw HTML parameters.
- Dynamic graph-derived strings are rendered with elements that do not interpret markup
  (st.text, st.code(language=None), st.json).
- Fixed enum-to-color mapping for status badges; never dynamic styling from data.
"""

from __future__ import annotations

import json
from typing import Any
import streamlit as st


def render_event_inspector(
    point: Any | None,
    is_selected_hidden: bool = False,
) -> None:
    """
    Render a structured, Streamlit-native inspection panel for a selected event.
    Guaranteed zero HTML generation and zero markup interpretation of untrusted text.
    """
    if point is None:
        st.info("Select a point on the timeline to inspect an event.")
        return

    # Banner note if selected event is hidden by active category filters
    if is_selected_hidden:
        st.warning("Selected event is currently hidden by the category filter above.")

    # 1. Header with sequence, category, and status badges
    seq_num = getattr(point, "seq", 0) or 0
    category = getattr(point, "category", "Unclassified") or "Unclassified"
    significance = getattr(point, "significance", "ROUTINE") or "ROUTINE"
    raw_outcome = getattr(point, "outcome", "n/a") or "n/a"

    col_title, col_sig, col_out = st.columns([4, 1.2, 1.2])
    with col_title:
        st.subheader(f"EVENT #{seq_num}: {category}")
    with col_sig:
        sig_color = "orange" if significance == "SIGNIFICANT" else "gray"
        st.badge(significance, color=sig_color)
    with col_out:
        out_upper = str(raw_outcome).upper()
        if out_upper in ("PASS", "SUCCESS", "ALLOWED", "VERIFIED", "COMPLETED"):
            out_color = "green"
        elif out_upper in ("FAIL", "FAILED", "LAPSED", "BLOCKED"):
            out_color = "red"
        elif out_upper in ("DIFFERED", "MISMATCH"):
            out_color = "orange"
        else:
            out_color = "gray"
        st.badge(str(raw_outcome), color=out_color)

    # 2. Full untruncated summary rendered in plain text (wrapping, no markup)
    st.caption("EVENT SUMMARY")
    summary_text = getattr(point, "summary", "") or "No summary provided."
    st.text(summary_text)

    # 3. Labeled metadata grid (4 columns, wider column 3 for timestamp)
    c1, c2, c3, c4 = st.columns([1, 1, 1.25, 1])
    with c1:
        st.caption("EVENT TYPE & ID")
        ev_type = getattr(point, "event_type", "n/a") or "n/a"
        ev_id = getattr(point, "event_id", "n/a") or "n/a"
        st.code(f"{ev_type}\n{ev_id}", language=None)
    with c2:
        st.caption("ACTOR / AGENT")
        actor = getattr(point, "actor", "system") or "system"
        target_id = getattr(point, "target_id", "n/a") or "n/a"
        target_str = f"Target: {target_id}" if target_id != "n/a" else "Target: n/a"
        st.code(f"{actor}\n{target_str}", language=None)
    with c3:
        st.caption("RECORDED TIME (TRANSACTION)")
        t_utc = getattr(point, "timestamp_utc", "n/a") or "n/a"
        t_ist = getattr(point, "timestamp_ist", "n/a") or "n/a"
        st.code(f"UTC: {t_utc}\nIST: {t_ist}", language=None)
    with c4:
        st.caption("STEP / TASK TYPE & REFS")
        step_type = getattr(point, "step_type", "n/a") or "n/a"
        task_type = getattr(point, "task_type", "n/a") or "n/a"
        stg_sub = step_type if step_type != "n/a" else task_type
        ev_ids = getattr(point, "evidence_ids", []) or []
        ar_ids = getattr(point, "action_ids", []) or []
        refs_str = f"Refs: {len(ev_ids)} ev, {len(ar_ids)} act"
        st.code(f"{stg_sub}\n{refs_str}", language=None)

    # 4. Explicit reference lists when present
    if ev_ids or ar_ids:
        r1, r2 = st.columns(2)
        if ev_ids:
            with r1:
                st.caption("EVIDENCE REFERENCES")
                st.code(", ".join(ev_ids), language=None)
        if ar_ids:
            with r2:
                st.caption("ACTION REFERENCES")
                st.code(", ".join(ar_ids), language=None)

    # 5. Collapsed raw JSON payload
    payload = getattr(point, "payload", {})
    if payload:
        with st.expander(f"Raw payload (JSON) — {len(payload)} fields", expanded=False):
            if isinstance(payload, dict):
                st.json(payload)
            elif isinstance(payload, str):
                try:
                    parsed = json.loads(payload)
                    st.json(parsed)
                except Exception:
                    st.code(payload, language=None)
            else:
                st.code(str(payload), language=None)
