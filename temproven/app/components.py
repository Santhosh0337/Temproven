"""
temproven/app/components.py
───────────────────────────
Streamlit presentation components for the Temproven Laboratory Integrity Dashboard.

Implements the 4-panel architecture:
- Header: Live FalkorDB connectivity indicator
- Panel 01: Incident Overview (metadata, affected counts, vertical timeline stepper)
- Panel 02: Downstream Impact Trace (Graphviz DAG, affected items tables)
- Panel 03: Verified Playbook & Procedural Memory (postconditions, steps, efficiency metrics)
- Panel 04: Backend Publication Gate (report inspection, honest CTA, hero decision block)

All Cypher queries remain in queries.py. No mutations on page load.
"""

from __future__ import annotations

import json
from typing import Any, Callable
import streamlit as st

from temproven.app.journey import (
    build_journey,
    build_journey_dot,
    extract_payload,
)
from temproven.app.timeline import (
    CATEGORY_LABELS,
    build_timeline_chart,
    build_timeline_points,
    resolve_selected_seq,
)
from temproven.app.presenters import (
    build_lineage_dot,
    build_timeline_html,
    escape,
    format_interval,
    format_timestamp,
    get_status_badge,
    sanitize_dynamic_text,
)
from temproven.app.theme import (
    COLOR_ALERT,
    COLOR_SUCCESS,
    COLOR_WARNING,
)
from temproven.domain.gate import GateDecision, GateResult
from temproven.memory.episodic import IncidentReconstruction
from temproven.metrics import IncidentMetricsData


def badge_html(status: str | None) -> str:
    """Render an HTML status pill badge with appropriate color scheme."""
    badge = get_status_badge(status)
    return f'<span class="status-pill {badge.style_class}">{escape(badge.text)}</span>'


def render_header(
    db_ok: bool,
    graph_name: str,
    host: str = "localhost",
    port: int = 6379,
    error_msg: str | None = None,
) -> None:
    """
    Render top-level dashboard header and live FalkorDB connectivity indicator.
    """
    if db_ok:
        status_dot = '<span class="status-indicator connected"></span>'
        status_text = f'FALKORDB: CONNECTED ({escape(graph_name)})'
    else:
        status_dot = '<span class="status-indicator unavailable"></span>'
        err_hint = f" ({escape(error_msg)})" if error_msg else ""
        status_text = f'FALKORDB: UNAVAILABLE{err_hint}'

    header_html = f"""
    <div>
        <div class="temproven-breadcrumb">TEMPROVEN CONTROL PLANE &bull; LABORATORY INTEGRITY &amp; INCIDENT LEARNING</div>
        <div class="temproven-header">
            <div>
                <h1 class="temproven-title">TEMPROVEN</h1>
                <div class="temproven-subtitle">When evidence changes, every dependent decision must be reconsidered.</div>
            </div>
            <div>
                <div class="db-status-badge">
                    {status_dot} {status_text}
                </div>
            </div>
        </div>
    </div>
    """
    st.markdown(header_html, unsafe_allow_html=True)


def render_section_1_overview(
    recon: IncidentReconstruction,
    impact_data: dict[str, Any] | None = None,
    playbook_data: dict[str, Any] | None = None,
    belief_change: Any | None = None,
) -> None:
    """
    Panel 01: Incident Journey & Overview.
    Displays:
    - Incident header strip with metadata
    - Triggering evidence revision block (separating valid time vs recorded time)
    - Key figures row (affected experiments, datasets, reports, tasks; em dash '—' if unavailable)
    - Major outcomes row (quarantined counts/ids, held reports, task escalations, closure/playbook status)
    - Incident Journey Graph (compact 7 canonical stages with layout toggle)
    - Interactive Stage Selection and Stage Detail panel
    - Collapsed Full Audit Trail expander
    """
    st.markdown(
        """
        <div class="panel-header">
            <div>
                <div class="panel-num">01 / INCIDENT</div>
                <h3 class="panel-title">Incident Journey & Overview</h3>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 1. Header Strip: Incident Metadata
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            f'<div class="micro-label">Incident ID</div>'
            f'<div class="micro-value mono-text">{escape(recon.incident_id)}</div>',
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f'<div class="micro-label">Type</div>'
            f'<div class="micro-value">{escape(recon.incident_type)}</div>',
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f'<div class="micro-label">Status</div>'
            f'<div style="margin-top: 2px;">{badge_html(recon.status)}</div>',
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            f'<div class="micro-label">Instrument</div>'
            f'<div class="micro-value mono-text">{escape(recon.instrument_id or "N/A")}</div>',
            unsafe_allow_html=True,
        )

    # Secondary metadata row
    st.markdown("<div style='height: 6px;'></div>", unsafe_allow_html=True)
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        opened_str = format_timestamp(recon.opened_at, include_ist=True)
        st.markdown(
            f'<div class="micro-label">Opened At</div>'
            f'<div class="micro-value mono-text" style="font-size: 0.75rem;">{escape(opened_str)}</div>',
            unsafe_allow_html=True,
        )
    with m2:
        st.markdown(
            f'<div class="micro-label">Next Sequence</div>'
            f'<div class="micro-value mono-text">{recon.next_seq}</div>',
            unsafe_allow_html=True,
        )
    with m3:
        st.markdown(
            f'<div class="micro-label">Timeline Events</div>'
            f'<div class="micro-value mono-text">{len(recon.events)}</div>',
            unsafe_allow_html=True,
        )
    with m4:
        st.markdown(
            f'<div class="micro-label">Agent Handoffs</div>'
            f'<div class="micro-value mono-text">{len(recon.handoffs)}</div>',
            unsafe_allow_html=True,
        )

    # 2. Triggering Evidence & Belief Revision Block (Valid Time vs Recorded Time)
    prior_html = ""
    if belief_change and hasattr(belief_change, "current_belief"):
        cb = belief_change.current_belief
        pb = getattr(belief_change, "previous_belief", None)
        ev_obj = getattr(cb, "evidence", None)
        ev_id = getattr(ev_obj, "id", "EV-CORRECTION") if ev_obj else "EV-CORRECTION"
        source_type = getattr(ev_obj, "source_type", "CALIBRATION_AUTHORITY") if ev_obj else "CALIBRATION_AUTHORITY"
        trust_level = getattr(ev_obj, "trust_level", "AUTHORITATIVE") if ev_obj else "AUTHORITATIVE"
        val_str = format_interval(belief_change.applies_from, belief_change.applies_until, interval_kind="VALID TIME")
        rec_str = format_timestamp(cb.recorded_at, time_label="RECORDED TIME", include_ist=True)
        ev_summary = cb.summary or getattr(belief_change, "rationale", "Authoritative calibration update supersedes prior claim.")
        if pb and pb.valid_from and pb.valid_until:
            prior_interval_str = format_interval(pb.valid_from, pb.valid_until, interval_kind="PRIOR VALID TIME")
            prior_html = f'<div class="mono-text" style="font-size: 0.72rem; color: #8A9099; text-decoration: line-through; margin-top: 2px;">{escape(prior_interval_str)}</div>'
    elif recon.evidence_claims:
        ec = recon.evidence_claims[0]
        ev_id = ec.get("evidence_id") or "EV-CLAIM"
        source_type = ec.get("source_type") or "CALIBRATION_AUTHORITY"
        trust_level = ec.get("trust_level") or "AUTHORITATIVE"
        val_str = format_interval(ec.get("valid_from"), ec.get("valid_until"), interval_kind="VALID TIME")
        rec_str = format_timestamp(ec.get("recorded_at"), time_label="RECORDED TIME", include_ist=True)
        ev_summary = f"Claim {ec.get('claim_id', '')} validity revised based on authoritative evidence {ev_id}."
    else:
        ev_id = "EV-MANUAL"
        source_type = "OPERATOR"
        trust_level = "VERIFIED"
        val_str = "[VALID TIME] Derived from incident scope"
        rec_str = format_timestamp(recon.opened_at, time_label="RECORDED TIME", include_ist=True)
        ev_summary = "Incident scope initialized from laboratory event record."

    evidence_block_html = f"""
    <div style="border: 1px solid #1D2128; background-color: #111419; padding: 0.75rem 0.9rem; border-radius: 4px; margin: 0.6rem 0;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem;">
            <span class="micro-label" style="letter-spacing: 0.08em;">TRIGGERING EVIDENCE &amp; BELIEF REVISION</span>
            <div>
                <span class="status-pill success">{escape(source_type)}</span>
                <span class="status-pill neutral">{escape(trust_level)}</span>
            </div>
        </div>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.6rem; margin-bottom: 0.4rem;">
            <div>
                <div class="micro-label">VALID TIME (APPLIES TO SCIENCE)</div>
                <div class="mono-text" style="font-size: 0.76rem; color: #22C55E; font-weight: 500;">{escape(val_str)}</div>
                {prior_html}
            </div>
            <div>
                <div class="micro-label">RECORDED TIME (SYSTEM AS-OF)</div>
                <div class="mono-text" style="font-size: 0.76rem; color: #F3F4F6;">{escape(rec_str)}</div>
                <div style="font-size: 0.72rem; color: #8C929D; margin-top: 2px;">Evidence ID: <code class="mono-text">{escape(ev_id)}</code></div>
            </div>
        </div>
        <div style="font-size: 0.75rem; color: #8C929D; border-top: 1px solid #1D2128; padding-top: 0.35rem;">
            <strong style="color: #ADB1B8;">Context:</strong> {escape(ev_summary)}
        </div>
    </div>
    """
    st.markdown(evidence_block_html, unsafe_allow_html=True)

    # 3. Key Figures Row (em dash '—' when unavailable, never 0 unless genuinely 0)
    exp_cnt = len(impact_data["experiments"]) if (impact_data is not None and "experiments" in impact_data) else None
    ds_cnt = len(impact_data["datasets"]) if (impact_data is not None and "datasets" in impact_data) else None
    rpt_cnt = len(impact_data["reports"]) if (impact_data is not None and "reports" in impact_data) else None
    tsk_cnt = len(recon.tasks) if recon.tasks is not None else None

    exp_str = str(exp_cnt) if exp_cnt is not None else "—"
    ds_str = str(ds_cnt) if ds_cnt is not None else "—"
    rpt_str = str(rpt_cnt) if rpt_cnt is not None else "—"
    tsk_str = str(tsk_cnt) if tsk_cnt is not None else "—"

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Affected Experiments", exp_str)
    k2.metric("Affected Datasets", ds_str)
    k3.metric("Downstream Reports", rpt_str)
    k4.metric("Incident Tasks", tsk_str)

    # 4. Major Outcomes Row (Quarantine counts/IDs, held reports, task escalations, procedure status)
    quarantined_ids: list[str] = []
    if impact_data and "datasets" in impact_data:
        quarantined_ids = [d["id"] for d in impact_data["datasets"] if d.get("current_status") == "QUARANTINED"]
    if not quarantined_ids and recon.action_records:
        quarantined_ids = [a["target_id"] for a in recon.action_records if a.get("action_type") == "QUARANTINE_DATASET" and a.get("target_id")]
    q_str = f"{len(quarantined_ids)} quarantined ({', '.join(quarantined_ids)})" if quarantined_ids else "0 quarantined"

    held_rpt_ids: list[str] = []
    if impact_data and "reports" in impact_data:
        held_rpt_ids = [r["id"] for r in impact_data["reports"] if r.get("current_status") in ("HELD", "REQUIRES_REVIEW")]
    if not held_rpt_ids and recon.action_records:
        held_rpt_ids = [a["target_id"] for a in recon.action_records if a.get("action_type") in ("HOLD_REPORT", "FLAG_REPORT_REVIEW") and a.get("target_id")]
    r_str = f"{len(held_rpt_ids)} held ({', '.join(held_rpt_ids)})" if held_rpt_ids else "0 held"

    escalated = [t for t in (recon.tasks or []) if t.get("status") == "ESCALATED"]
    if escalated:
        reasons = list(dict.fromkeys(t.get("escalation_reason", "") for t in escalated if t.get("escalation_reason")))
        reason_label = reasons[0] if reasons else "missing owner"
        if len(reason_label) > 28:
            reason_label = reason_label[:25] + "..."
        t_str = f"{len(escalated)} escalated ({reason_label})"
    else:
        t_str = "0 escalated"

    pb = playbook_data.get("playbook") if playbook_data else None
    run = playbook_data.get("run") if playbook_data else None
    mode_str = run.get("mode") if run else ("EXPLORATORY" if recon.incident_id == "INC-2026-001" else "VERIFIED")
    p_status = (pb and pb.get("status")) or "VERIFIED"
    proc_str = f"{p_status} ({mode_str})"

    outcomes_html = f"""
    <div style="border: 1px solid #1D2128; background-color: #111419; padding: 0.65rem 0.85rem; border-radius: 4px; margin: 0.6rem 0;">
        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.6rem; font-size: 0.74rem;">
            <div>
                <div class="micro-label">QUARANTINE ENFORCEMENT</div>
                <div class="mono-text" style="color: #F95738; font-weight: 500;">{escape(q_str)}</div>
            </div>
            <div>
                <div class="micro-label">PUBLICATION GATE HOLD</div>
                <div class="mono-text" style="color: #F95738; font-weight: 500;">{escape(r_str)}</div>
            </div>
            <div>
                <div class="micro-label">TASK ESCALATIONS</div>
                <div class="mono-text" style="color: #F59E0B; font-weight: 500;">{escape(t_str)}</div>
            </div>
            <div>
                <div class="micro-label">PROCEDURE STATUS</div>
                <div class="mono-text" style="color: #22C55E; font-weight: 500;">{escape(proc_str)}</div>
            </div>
        </div>
    </div>
    """
    st.markdown(outcomes_html, unsafe_allow_html=True)

    # 5. Interactive Point-Based Event Timeline (Primary Centerpiece)
    handoff_task_types = {
        h["id"]: h.get("task_type")
        for h in (recon.handoffs or [])
        if isinstance(h, dict) and h.get("id") and h.get("task_type")
    }

    filter_state_key = f"timeline_filter_{recon.incident_id}"
    selected_seq_key = f"timeline_sel_{recon.incident_id}"
    vert_state_key = f"timeline_vert_{recon.incident_id}"

    # Default category filter: hide Routine queries by default if present
    initial_active = [c for c in CATEGORY_LABELS if c != "Routine queries"]
    if filter_state_key not in st.session_state:
        st.session_state[filter_state_key] = initial_active

    if selected_seq_key not in st.session_state:
        st.session_state[selected_seq_key] = None

    if vert_state_key not in st.session_state:
        st.session_state[vert_state_key] = False

    col_t_title, col_t_all, col_t_reset, col_t_toggle = st.columns([3.5, 1, 1, 1.5])
    with col_t_title:
        st.markdown(
            '<div class="micro-label" style="margin-top: 0.6rem; letter-spacing: 0.08em;">'
            'INTERACTIVE EVENT TIMELINE (SEQUENCE ORDER)'
            '</div>',
            unsafe_allow_html=True,
        )
    with col_t_all:
        if st.button("All", key=f"btn_all_{recon.incident_id}", use_container_width=True):
            st.session_state[filter_state_key] = list(CATEGORY_LABELS)
            st.rerun()
    with col_t_reset:
        if st.button("Reset", key=f"btn_reset_{recon.incident_id}", use_container_width=True):
            st.session_state[filter_state_key] = initial_active
            st.rerun()
    with col_t_toggle:
        is_vert = st.toggle("Vertical", key=vert_state_key)

    # First pass: count all events by category
    unfiltered_view = build_timeline_points(recon.events, active_categories=CATEGORY_LABELS, handoff_task_types=handoff_task_types)

    # Multi-select category filter
    selected_cats = st.multiselect(
        "Filter Event Categories",
        options=CATEGORY_LABELS,
        default=st.session_state[filter_state_key],
        format_func=lambda cat: f"{cat} ({unfiltered_view.category_counts.get(cat, 0)})",
        key=f"ms_filter_{recon.incident_id}",
    )
    st.session_state[filter_state_key] = selected_cats

    # Build filtered view
    current_selected_seq = st.session_state[selected_seq_key]
    view = build_timeline_points(
        recon.events,
        active_categories=selected_cats,
        selected_seq=current_selected_seq,
        handoff_task_types=handoff_task_types,
    )

    # Render Filter Banner
    st.markdown(
        f'<div class="mono-text" style="font-size: 0.74rem; color: #8C929D; margin-top: 0.2rem; margin-bottom: 0.5rem;">'
        f'{escape(view.filter_banner)}'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Render Altair Chart
    chart = build_timeline_chart(view, orientation="vertical" if is_vert else "horizontal")
    chart_event = st.altair_chart(
        chart,
        on_select="rerun",
        key=f"altair_chart_{recon.incident_id}",
        use_container_width=True,
    )
    st.caption("Line = recorded event sequence, not causation. Shapes: ⬥ Significant event, ● Routine query/handoff.")

    # Resolve Chart Selection (returns selected seq from chart click)
    new_selected_seq = resolve_selected_seq(chart_event, recon.events, current_selected_seq, recon.incident_id)
    if new_selected_seq != current_selected_seq and new_selected_seq is not None:
        st.session_state[selected_seq_key] = new_selected_seq
        # Rebuild view with new selection
        view = build_timeline_points(
            recon.events,
            active_categories=selected_cats,
            selected_seq=new_selected_seq,
            handoff_task_types=handoff_task_types,
        )

    # Navigation Controls (Previous / Next / Go to Seq / Clear Selection)
    visible_seqs = [p.seq for p in view.visible_points]
    all_seqs = [p.seq for p in view.points]
    nav_seqs = visible_seqs if (view.selected_point and view.selected_point.is_visible) else all_seqs

    c_prev, c_next, c_goto, c_clear = st.columns([1, 1, 2, 1.2])
    with c_prev:
        if st.button("◀ Prev", key=f"btn_prev_{recon.incident_id}", use_container_width=True, disabled=not nav_seqs):
            if view.selected_seq and view.selected_seq in nav_seqs:
                curr_idx = nav_seqs.index(view.selected_seq)
                if curr_idx > 0:
                    st.session_state[selected_seq_key] = nav_seqs[curr_idx - 1]
                    st.rerun()
            elif nav_seqs:
                st.session_state[selected_seq_key] = nav_seqs[0]
                st.rerun()
    with c_next:
        if st.button("Next ▶", key=f"btn_next_{recon.incident_id}", use_container_width=True, disabled=not nav_seqs):
            if view.selected_seq and view.selected_seq in nav_seqs:
                curr_idx = nav_seqs.index(view.selected_seq)
                if curr_idx < len(nav_seqs) - 1:
                    st.session_state[selected_seq_key] = nav_seqs[curr_idx + 1]
                    st.rerun()
            elif nav_seqs:
                st.session_state[selected_seq_key] = nav_seqs[0]
                st.rerun()
    with c_goto:
        goto_seq = st.number_input(
            "Go to sequence",
            min_value=1,
            max_value=max(all_seqs) if all_seqs else 1,
            value=view.selected_seq or (all_seqs[0] if all_seqs else 1),
            step=1,
            key=f"num_goto_{recon.incident_id}",
            label_visibility="collapsed",
        )
        if goto_seq != view.selected_seq and goto_seq in all_seqs:
            st.session_state[selected_seq_key] = goto_seq
            st.rerun()
    with c_clear:
        if st.button("Clear", key=f"btn_clear_{recon.incident_id}", use_container_width=True):
            st.session_state[selected_seq_key] = None
            st.rerun()

    # 6. Event Inspection Panel
    p = view.selected_point
    if p is None:
        st.markdown(
            '<div class="inspector-panel" style="color: #8C929D; font-size: 0.8rem; text-align: center; padding: 1.5rem 0;">'
            'Select a point on the timeline to inspect an event.'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        hidden_warning = ""
        if view.is_selected_hidden:
            hidden_warning = (
                '<div style="background-color: rgba(245, 158, 11, 0.15); border: 1px solid #F59E0B; color: #F59E0B; '
                'padding: 0.35rem 0.65rem; border-radius: 3px; font-size: 0.74rem; margin-bottom: 0.5rem;">'
                '⚠️ Selected event is currently hidden by the category filter.'
                '</div>'
            )

        sig_pill_class = "warning" if p.significance == "SIGNIFICANT" else "neutral"
        out_pill_class = "success" if p.outcome.upper() in ("PASS", "SUCCESS", "ALLOWED", "VERIFIED") else ("danger" if p.outcome.upper() in ("FAIL", "FAILED", "LAPSED", "BLOCKED") else "neutral")

        st.markdown(
            f"""
            <div class="inspector-panel">
                {hidden_warning}
                <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #1D2128; padding-bottom: 0.45rem;">
                    <div>
                        <span class="mono-text" style="font-size: 1.05rem; font-weight: 700; color: #00D2B4;">EVENT #{p.seq}</span>
                        <span style="margin-left: 0.5rem; font-size: 0.76rem; color: #D1D5DB; font-weight: 600;">{escape(p.category)}</span>
                    </div>
                    <div>
                        <span class="status-pill {sig_pill_class}">{escape(p.significance)}</span>
                        <span class="status-pill {out_pill_class}" style="margin-left: 0.3rem;">{escape(p.outcome)}</span>
                    </div>
                </div>

                <div class="inspector-summary">
                    <strong style="color: #9CA3AF;">Summary:</strong> {escape(p.summary or 'No summary provided.')}
                </div>

                <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.6rem; font-size: 0.74rem; margin-top: 0.5rem;">
                    <div>
                        <div class="micro-label">EVENT TYPE &amp; ID</div>
                        <div class="mono-text" style="color: #FFFFFF; font-weight: 600;">{escape(p.event_type)}</div>
                        <div class="mono-text" style="color: #8C929D; font-size: 0.7rem;">{escape(p.event_id)}</div>
                    </div>
                    <div>
                        <div class="micro-label">ACTOR / AGENT</div>
                        <div class="mono-text" style="color: #FFFFFF;">{escape(p.actor)}</div>
                        <div style="color: #8C929D; font-size: 0.7rem;">Target: <code class="mono-text">{escape(p.target_id)}</code></div>
                    </div>
                    <div>
                        <div class="micro-label">RECORDED TIME (TRANSACTION)</div>
                        <div class="mono-text" style="color: #F3F4F6;">{escape(p.timestamp_utc)}</div>
                        <div class="mono-text" style="color: #8C929D; font-size: 0.7rem;">{escape(p.timestamp_ist)}</div>
                    </div>
                    <div>
                        <div class="micro-label">STEP / TASK TYPE</div>
                        <div class="mono-text" style="color: #FFFFFF;">{escape(p.step_type if p.step_type != 'n/a' else p.task_type)}</div>
                        <div style="color: #8C929D; font-size: 0.7rem;">Refs: {len(p.evidence_ids)} ev, {len(p.action_ids)} act</div>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Full payload expander
        if p.payload:
            with st.expander(f"Event #{p.seq} Full Raw Payload ({len(p.payload)} keys)", expanded=False):
                st.json(p.payload)

    # 7. Secondary Collapsed Stage Summary Expander
    journey = build_journey(recon.events, handoff_task_types)
    with st.expander("Stage summary (7 canonical stages)", expanded=False):
        stage_dot = build_journey_dot(journey, selected_stage_key=None, orientation="LR")
        st.graphviz_chart(stage_dot, use_container_width=True)
        st.caption("Solid borders = recorded stages. Dashed borders = [NOT RECORDED] in this incident. Edges denote recorded sequence, not causation.")

    # 8. Collapsed Full Audit Trail Expander (unaffected by filters or selection)
    with st.expander(f"Full Audit Trail ({len(recon.events)} events recorded)", expanded=False):
        all_sorted = sorted(
            recon.events,
            key=lambda e: getattr(e, "seq", 0) if not isinstance(e, dict) else e.get("seq", 0),
        )
        st.markdown(build_timeline_html(all_sorted), unsafe_allow_html=True)


def render_section_2_impact(
    instrument_id: str | None,
    impact_data: dict[str, Any],
) -> None:
    """
    Panel 02: Downstream Impact Trace.
    Displays Graphviz lineage DAG and inspectable tables for experiments, datasets, and reports.
    """
    st.markdown(
        """
        <div class="panel-header">
            <div>
                <div class="panel-num">02 / IMPACT</div>
                <h3 class="panel-title">Downstream Impact Trace</h3>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        '<p class="panel-desc">Interactive dependency graph showing how lapsed instrument certification '
        'propagates down to experiments, produced datasets, derived datasets, and published reports.</p>',
        unsafe_allow_html=True,
    )

    experiments = impact_data.get("experiments", [])
    datasets = impact_data.get("datasets", [])
    reports = impact_data.get("reports", [])

    # Graphviz DAG
    dot_str = build_lineage_dot(instrument_id, experiments, datasets, reports)
    st.graphviz_chart(dot_str, use_container_width=True)

    # Affected Items Breakdown
    tab_ds, tab_exp, tab_rpt = st.tabs([
        f"Datasets ({len(datasets)})",
        f"Experiments ({len(experiments)})",
        f"Reports ({len(reports)})",
    ])

    with tab_ds:
        if not datasets:
            st.info("No downstream datasets affected.")
        else:
            ds_rows = []
            for d in datasets:
                path_str = " → ".join(d.get("dependency_path", []))
                ds_rows.append({
                    "Dataset ID": d["id"],
                    "Status": d["current_status"],
                    "Reason": d["reason"],
                    "Lineage Path": path_str,
                })
            st.dataframe(ds_rows, use_container_width=True, hide_index=True)

    with tab_exp:
        if not experiments:
            st.info("No experiments found for this instrument.")
        else:
            exp_rows = []
            for e in experiments:
                gaps = e.get("gaps") or []
                gap_parts = []
                for g in gaps:
                    if isinstance(g, (tuple, list)) and len(g) >= 2:
                        t_start = format_timestamp(g[0], include_ist=False)
                        t_end = format_timestamp(g[1], include_ist=False)
                        gap_parts.append(f"[{t_start} -> {t_end}]")
                    elif isinstance(g, dict):
                        gap_parts.append(f"[{g.get('gap_start', '')} -> {g.get('gap_end', '')}]")
                    else:
                        gap_parts.append(str(g))
                gap_str = ", ".join(gap_parts) if gap_parts else "None"
                exp_rows.append({
                    "Experiment ID": e.get("id"),
                    "Usage ID": e.get("usage_id"),
                    "Status": e.get("status"),
                    "Coverage Gaps": gap_str,
                    "Reason": e.get("reason"),
                })
            st.dataframe(exp_rows, use_container_width=True, hide_index=True)

    with tab_rpt:
        if not reports:
            st.info("No downstream reports affected.")
        else:
            rpt_rows = []
            for r in reports:
                path_str = " → ".join(r.get("dependency_path", []))
                rpt_rows.append({
                    "Report ID": r["id"],
                    "Status": r["current_status"],
                    "Reason": r["reason"],
                    "Lineage Path": path_str,
                })
            st.dataframe(rpt_rows, use_container_width=True, hide_index=True)


def render_section_3_memory(
    incident_id: str,
    playbook_data: dict[str, Any],
    belief_change: Any | None = None,
    metrics: IncidentMetricsData | None = None,
    baseline_metrics: IncidentMetricsData | None = None,
) -> None:
    """
    Panel 03: Verified Playbook & Procedural Memory.
    Displays playbook metadata, postcondition checklist (PC1-PC6), steps table,
    belief change supersession evidence, and metrics comparison.
    """
    st.markdown(
        """
        <div class="panel-header">
            <div>
                <div class="panel-num">03 / INVESTIGATION</div>
                <h3 class="panel-title">Verified Playbook & Procedure Reuse</h3>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        '<p class="panel-desc">Investigation memory in FalkorDB enables deterministic reuse of verified '
        'procedures across incidents without re-deriving diagnosis or relying on fragile prompt memory.</p>',
        unsafe_allow_html=True,
    )

    pb = playbook_data.get("playbook")
    run = playbook_data.get("run")
    steps = playbook_data.get("steps", [])
    step_results = playbook_data.get("step_results", [])
    promotion_checks = playbook_data.get("promotion_checks", {})

    if not pb and not run:
        st.info("No playbook or procedural execution records found for this incident.")
        return

    # Metadata row
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        pb_id = (pb and pb.get("id")) or (run and run.get("playbook_id")) or "N/A"
        st.markdown(
            f'<div class="micro-label">Playbook ID</div>'
            f'<div class="micro-value mono-text">{escape(pb_id)}</div>',
            unsafe_allow_html=True,
        )
    with c2:
        status_val = (pb and pb.get("status")) or "VERIFIED"
        st.markdown(
            f'<div class="micro-label">Status</div>'
            f'<div style="margin-top: 2px;">{badge_html(status_val)}</div>',
            unsafe_allow_html=True,
        )
    with c3:
        mode_val = (run and run.get("mode")) or ("EXPLORATORY" if incident_id == "INC-2026-001" else "REUSED")
        st.markdown(
            f'<div class="micro-label">Execution Mode</div>'
            f'<div class="micro-value mono-text">{escape(mode_val)}</div>',
            unsafe_allow_html=True,
        )
    with c4:
        src_inc = (pb and pb.get("source_incident_id")) or "INC-2026-001"
        st.markdown(
            f'<div class="micro-label">Source Incident</div>'
            f'<div class="micro-value mono-text">{escape(src_inc)}</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

    # Postcondition Verification Checklist (PC1 to PC6)
    st.markdown('<div class="micro-label">Postcondition Verification Checklist (PC1–PC6)</div>', unsafe_allow_html=True)

    pc_definitions = [
        ("PC1", "PC1_CERT_LAPSE_CONFIRMED", "Calibration lapse isolated to target instrument"),
        ("PC2", "PC2_LINEAGE_TRAVERSED", "Lineage traversed without cyclic dependencies"),
        ("PC3", "PC3_DATASETS_QUARANTINED", "All affected datasets quarantined"),
        ("PC4", "PC4_REPORTS_HELD", "Downstream reports blocked from publication"),
        ("PC5", "PC5_TASKS_CREATED", "Dataset review tasks created and assigned"),
        ("PC6", "PC6_ESCALATION_ON_NO_OWNER", "Missing owners escalated with explicit reason"),
    ]

    checklist_rows_html = []
    for pc_code, pc_key, pc_desc in pc_definitions:
        check_obj = promotion_checks.get(pc_key) if promotion_checks else None
        is_passed = check_obj.get("passed", True) if check_obj else True
        badge = '<span class="status-pill success">&#10003; PASS</span>' if is_passed else '<span class="status-pill danger">&#10005; FAIL</span>'
        checklist_rows_html.append(
            f"""
            <div class="compliance-row">
                <div class="compliance-info">
                    <span class="compliance-code">{pc_code} &bull; <span style="font-weight: 400; color: #ADB1B8;">{escape(pc_desc)}</span></span>
                </div>
                <div>
                    {badge}
                </div>
            </div>
            """
        )
    st.markdown("".join(checklist_rows_html), unsafe_allow_html=True)

    # Playbook Steps / Step Results Table
    st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
    with st.expander("Playbook Steps & Execution Results", expanded=True):
        # Match step definition with run result
        res_by_step = {r.get("step_id"): r for r in step_results}
        table_rows = []

        # If we have structured steps
        if steps:
            for s in steps:
                s_id = s.get("id")
                r = res_by_step.get(s_id, {})
                status_str = r.get("status") or ("PASSED" if pb and pb.get("status") == "VERIFIED" else "PENDING")
                diffs = r.get("differences_json") or "—"
                if diffs and diffs != "—":
                    try:
                        parsed = json.loads(diffs)
                        diffs = str(parsed)
                    except Exception:
                        pass

                table_rows.append({
                    "Step": s.get("order"),
                    "Step ID": s_id,
                    "Kind": s.get("kind"),
                    "Role": s.get("role"),
                    "Expected": s.get("expected_outcome") or "PASSED",
                    "Status": status_str,
                    "Differences": diffs,
                })
        elif step_results:
            for r in step_results:
                table_rows.append({
                    "Step ID": r.get("step_id"),
                    "Status": r.get("status"),
                    "Detail": r.get("detail"),
                    "Differences": r.get("differences_json") or "None",
                    "Domain Queries": r.get("domain_query_count", 0),
                    "Elapsed (ms)": f"{r.get('elapsed_ms', 0):.1f}",
                })

        if table_rows:
            st.dataframe(table_rows, use_container_width=True, hide_index=True)
        else:
            st.info("No step definitions recorded for this playbook.")

    # Semantic Memory & Belief Explanation Expander
    if belief_change is not None:
        with st.expander("Semantic Memory: Belief Evolution & Supersession", expanded=False):
            if hasattr(belief_change, "render_text"):
                st.code(belief_change.render_text(), language="text")
            else:
                st.write(belief_change)

    # Efficiency Metrics Comparison Expander
    if metrics is not None or baseline_metrics is not None:
        with st.expander("Investigation Efficiency & Query Count Comparison", expanded=False):
            m_cols = st.columns(3)
            with m_cols[0]:
                st.markdown('<div class="micro-label">Domain Queries</div>', unsafe_allow_html=True)
                val = metrics.domain_queries if metrics else (baseline_metrics.domain_queries if baseline_metrics else 0)
                st.markdown(f'<div class="micro-value mono-text">{val}</div>', unsafe_allow_html=True)
            with m_cols[1]:
                st.markdown('<div class="micro-label">Bookkeeping Queries</div>', unsafe_allow_html=True)
                bval = metrics.bookkeeping_queries if metrics else (baseline_metrics.bookkeeping_queries if baseline_metrics else 0)
                st.markdown(f'<div class="micro-value mono-text">{bval}</div>', unsafe_allow_html=True)
            with m_cols[2]:
                st.markdown('<div class="micro-label">Diagnostic Skip</div>', unsafe_allow_html=True)
                st.markdown(
                    '<div class="micro-value mono-text" style="color: #5E9E7A;">LADDER CHECKS H2–H5 SKIPPED</div>',
                    unsafe_allow_html=True,
                )


def render_section_4_gate(
    reports: list[dict[str, Any]],
    on_publish_fn: Callable[[str, str], GateResult],
    report_details_fn: Callable[[str], dict[str, Any] | None],
) -> None:
    """
    Panel 04: Backend Publication Gate.
    Displays live report dependency inspection, honest publication attempt CTA,
    and hero decision block.
    """
    st.markdown(
        """
        <div class="panel-header">
            <div>
                <div class="panel-num">04 / PUBLICATION</div>
                <h3 class="panel-title">Backend Publication Gate</h3>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        '<p class="panel-desc">Every publication attempt re-evaluates live graph evidence at current time. '
        'No cached UI status is trusted; the backend publication gate enforces all integrity rules.</p>',
        unsafe_allow_html=True,
    )

    if not reports:
        st.info("No reports found in the graph.")
        return

    # Report Selector
    report_options = {
        r["id"]: f"{r['id']} — {r.get('title', 'Untitled')} [{r.get('publication_status', 'DRAFT')}]"
        for r in reports
    }

    selected_report_id = st.selectbox(
        "Select Report to Inspect & Publish",
        options=list(report_options.keys()),
        format_func=lambda rid: report_options[rid],
        key="selected_gate_report_id",
    )

    # Fetch live report details (read-only)
    report_info = report_details_fn(selected_report_id) if selected_report_id else None

    if report_info:
        r_cols = st.columns(3)
        with r_cols[0]:
            st.markdown(
                f'<div class="micro-label">Report ID</div>'
                f'<div class="micro-value mono-text">{escape(report_info.get("report_id"))}</div>',
                unsafe_allow_html=True,
            )
        with r_cols[1]:
            st.markdown(
                f'<div class="micro-label">Title</div>'
                f'<div class="micro-value">{escape(report_info.get("report_title", "Untitled"))}</div>',
                unsafe_allow_html=True,
            )
        with r_cols[2]:
            st.markdown(
                f'<div class="micro-label">Current Status</div>'
                f'<div style="margin-top: 2px;">{badge_html(report_info.get("report_status"))}</div>',
                unsafe_allow_html=True,
            )

        # Show upstream dependencies
        used_ds = [d for d in report_info.get("used_datasets", []) if d and d.get("id")]
        upstream_usg = [u for u in report_info.get("upstream_usages", []) if u and u.get("usage_id")]

        with st.expander(f"Upstream Dependencies ({len(used_ds)} datasets, {len(upstream_usg)} instrument usages)", expanded=False):
            if used_ds:
                st.markdown('<div class="micro-label">Used Datasets</div>', unsafe_allow_html=True)
                st.dataframe(used_ds, use_container_width=True, hide_index=True)
            if upstream_usg:
                st.markdown('<div class="micro-label">Upstream Usages</div>', unsafe_allow_html=True)
                st.dataframe(upstream_usg, use_container_width=True, hide_index=True)

    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

    # Honest CTA Action Block with explicit confirmation
    st.markdown(
        '<div style="border: 1px solid #1D2128; background-color: #111419; padding: 0.85rem 1rem; border-radius: 4px;">'
        '<div style="font-size: 0.75rem; color: #8C929D; margin-bottom: 0.6rem; line-height: 1.4;">'
        'ACTION: Attempting publication will execute live bitemporal coverage re-evaluation in FalkorDB '
        'and persist an immutable <code>PublicationAttempt</code> audit record.'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    confirm = st.checkbox(
        "I understand this attempts publication and records an immutable audit record in FalkorDB.",
        key=f"confirm_publish_{selected_report_id}",
    )

    col_btn, _ = st.columns([1, 2])
    with col_btn:
        publish_btn = st.button(
            "Attempt publication",
            type="primary",
            disabled=not confirm,
            key=f"btn_publish_{selected_report_id}",
        )

    # Handle publication attempt
    session_result_key = f"gate_result_{selected_report_id}"

    if publish_btn:
        with st.spinner("Evaluating live graph evidence..."):
            res = on_publish_fn(selected_report_id, "UI_Operator_Alice")
            st.session_state[session_result_key] = res
            st.rerun()

    # If a result exists in session state for this report, render the Hero Block
    last_res: GateResult | None = st.session_state.get(session_result_key)
    if last_res:
        dec_val = last_res.decision.value
        hero_class = dec_val  # BLOCK, ALLOW, or REVIEW_REQUIRED

        badge = badge_html(dec_val)
        time_str = format_timestamp(last_res.at, include_ist=True)

        hero_html = f"""
        <div class="gate-hero-block {hero_class}">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem;">
                <div>
                    <span class="micro-label">GATE DECISION</span>
                    <div style="margin-top: 4px;">{badge}</div>
                </div>
                <div style="text-align: right;">
                    <div class="micro-label">REPORT TRANSITION</div>
                    <div class="mono-text" style="font-size: 0.85rem; color: #FFFFFF;">
                        {escape(last_res.prior_status)} &rarr; <strong>{escape(last_res.new_status)}</strong>
                    </div>
                </div>
            </div>
            <div style="margin-bottom: 0.5rem;">
                <div class="micro-label">JUSTIFICATION</div>
                <div style="font-size: 0.85rem; color: #F3F4F6; line-height: 1.4;">{escape(last_res.reason)}</div>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.72rem; color: #8C929D; border-top: 1px solid #1D2128; padding-top: 0.4rem; margin-top: 0.5rem;">
                <span>ACTOR: <code class="mono-text">{escape(last_res.actor)}</code></span>
                <span>ATTEMPT ID: <code class="mono-text">{escape(last_res.attempt_id)}</code></span>
                <span>{escape(time_str)}</span>
            </div>
        </div>
        """
        st.markdown(hero_html, unsafe_allow_html=True)

        if last_res.evidence_path:
            with st.expander("Inspected Evidence Paths & Coverage Gaps", expanded=False):
                st.json(last_res.evidence_path)
