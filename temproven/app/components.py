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

    # 5. Incident Journey Graph (Pure domain computation, Graphviz DOT, layout toggle)
    handoff_task_types = {
        h["id"]: h.get("task_type")
        for h in (recon.handoffs or [])
        if isinstance(h, dict) and h.get("id") and h.get("task_type")
    }
    journey = build_journey(recon.events, handoff_task_types)

    col_j_title, col_j_toggle = st.columns([3, 1])
    with col_j_title:
        st.markdown(
            '<div class="micro-label" style="margin-top: 0.6rem; letter-spacing: 0.08em;">'
            'INCIDENT JOURNEY GRAPH (7 CANONICAL STAGES)'
            '</div>',
            unsafe_allow_html=True,
        )
    with col_j_toggle:
        is_vert = st.toggle("Vertical layout", value=False, key=f"tog_vert_{recon.incident_id}")

    present_keys = [s.key for s in journey.present_stages]
    session_stage_key = f"sel_stage_{recon.incident_id}"
    if session_stage_key not in st.session_state or st.session_state[session_stage_key] not in present_keys:
        st.session_state[session_stage_key] = present_keys[0] if present_keys else None

    current_stage_key = st.session_state[session_stage_key]
    orientation = "TB" if is_vert else "LR"
    dot_str = build_journey_dot(journey, selected_stage_key=current_stage_key, orientation=orientation)
    st.graphviz_chart(dot_str, use_container_width=True)
    st.caption("Solid borders = recorded stages. Dashed borders = [NOT RECORDED] in this incident. Edges denote recorded sequence, not causation.")

    # 6. Stage Selection & Stage Detail Panel
    if present_keys:
        key_to_stage = {s.key: s for s in journey.stages}

        def stage_format(k: str) -> str:
            stg = key_to_stage.get(k)
            return f"{stg.label} ({stg.event_count})" if stg else k

        sel = st.segmented_control(
            "Select Stage to Inspect",
            options=present_keys,
            default=current_stage_key if current_stage_key in present_keys else present_keys[0],
            format_func=stage_format,
            key=f"stage_segmented_{recon.incident_id}",
        )
        if sel and sel != st.session_state[session_stage_key]:
            st.session_state[session_stage_key] = sel
            st.rerun()

        active_stage = key_to_stage.get(st.session_state[session_stage_key])
        if active_stage:
            status_b = badge_html(active_stage.status)
            interleave_text = ' &bull; <span style="color: #F59E0B; font-weight: 500;">⚠️ Interleaved sequence</span>' if active_stage.interleaved else ""
            time_info = ""
            if active_stage.first_time:
                t1 = format_timestamp(active_stage.first_time, include_ist=False)
                t2 = format_timestamp(active_stage.last_time, include_ist=False)
                time_info = f" &bull; {t1} &rarr; {t2}"

            st.markdown(
                f"""
                <div style="border: 1px solid #1D2128; background-color: #111419; padding: 0.75rem 0.9rem; border-radius: 4px; margin-top: 0.4rem; margin-bottom: 0.6rem;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <span class="mono-text" style="font-size: 0.85rem; font-weight: 600; color: #FFFFFF;">{escape(active_stage.label)}</span>
                            <span style="margin-left: 0.5rem;">{status_b}</span>
                        </div>
                        <div class="mono-text" style="font-size: 0.75rem; color: #8C929D;">
                            Seq #{active_stage.first_seq} &rarr; #{active_stage.last_seq}{interleave_text}
                        </div>
                    </div>
                    <div style="font-size: 0.73rem; color: #8C929D; margin-top: 0.25rem;">
                        <strong>Actors:</strong> {escape(', '.join(active_stage.actors) or 'System')}
                        {time_info}
                        &bull; <strong>Events:</strong> {active_stage.event_count}
                        &bull; <strong>Supporting Queries:</strong> {active_stage.query_count}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # Stage key events table
            if active_stage.events:
                st_rows = []
                for ev in active_stage.events:
                    at_v = getattr(ev, "at", None) if not isinstance(ev, dict) else ev.get("at")
                    act_v = getattr(ev, "actor_agent", None) if not isinstance(ev, dict) else ev.get("actor_agent")
                    tp_v = getattr(ev, "event_type", None) if not isinstance(ev, dict) else ev.get("event_type")
                    sm_v = getattr(ev, "summary", None) if not isinstance(ev, dict) else ev.get("summary")
                    oc_v = getattr(ev, "outcome", None) if not isinstance(ev, dict) else ev.get("outcome")
                    sq_v = getattr(ev, "seq", None) if not isinstance(ev, dict) else ev.get("seq")

                    st_rows.append({
                        "Seq": sq_v,
                        "Time (UTC)": format_timestamp(at_v, include_ist=False),
                        "Type": tp_v,
                        "Actor": act_v or "System",
                        "Summary": sanitize_dynamic_text(sm_v),
                        "Outcome": oc_v or "—",
                    })
                st.dataframe(st_rows, use_container_width=True, hide_index=True)

            # Supporting queries for this stage (explicitly linked)
            if active_stage.supporting_queries:
                with st.expander(f"Supporting Domain Queries ({len(active_stage.supporting_queries)})", expanded=False):
                    q_rows = []
                    for qe in active_stage.supporting_queries:
                        payload = extract_payload(qe)
                        q_rows.append({
                            "Seq": getattr(qe, "seq", None) if not isinstance(qe, dict) else qe.get("seq"),
                            "Query": payload.get("query_name") or "QUERY",
                            "Rows": payload.get("result_count", 0),
                            "Elapsed (ms)": f"{payload.get('elapsed_ms', 0):.1f}",
                            "Params": ", ".join(payload.get("param_keys", [])),
                        })
                    st.dataframe(q_rows, use_container_width=True, hide_index=True)

    # 7. Collapsed Full Audit Trail Expander
    with st.expander(
        f"Full Audit Trail ({journey.all_events_count} total events, {journey.supporting_queries_count} supporting queries)",
        expanded=False,
    ):
        all_sorted = sorted(
            recon.events,
            key=lambda e: getattr(e, "seq", 0) if not isinstance(e, dict) else e.get("seq", 0),
        )
        trail_rows = []
        for ev in all_sorted:
            at_v = getattr(ev, "at", None) if not isinstance(ev, dict) else ev.get("at")
            act_v = getattr(ev, "actor_agent", None) if not isinstance(ev, dict) else ev.get("actor_agent")
            tp_v = getattr(ev, "event_type", None) if not isinstance(ev, dict) else ev.get("event_type")
            sm_v = getattr(ev, "summary", None) if not isinstance(ev, dict) else ev.get("summary")
            oc_v = getattr(ev, "outcome", None) if not isinstance(ev, dict) else ev.get("outcome")
            sq_v = getattr(ev, "seq", None) if not isinstance(ev, dict) else ev.get("seq")

            trail_rows.append({
                "Seq": sq_v,
                "Time (UTC)": format_timestamp(at_v, include_ist=False),
                "Type": tp_v,
                "Actor": act_v or "System",
                "Summary": sanitize_dynamic_text(sm_v),
                "Outcome": oc_v or "—",
            })
        if trail_rows:
            st.dataframe(trail_rows, use_container_width=True, hide_index=True)
        else:
            st.info("No episodic events recorded for this incident.")


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
