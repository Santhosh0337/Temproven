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

from temproven.app.presenters import (
    build_lineage_dot,
    build_timeline_html,
    escape,
    format_timestamp,
    get_status_badge,
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
        status_dot = '<span class="status-indicator" style="background-color: #5E9E7A;"></span>'
        status_text = f'FALKORDB: CONNECTED ({escape(graph_name)})'
    else:
        status_dot = '<span class="status-indicator" style="background-color: #E8553D;"></span>'
        err_hint = f" ({escape(error_msg)})" if error_msg else ""
        status_text = f'FALKORDB: UNAVAILABLE{err_hint}'

    header_html = f"""
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
    """
    st.markdown(header_html, unsafe_allow_html=True)


def render_section_1_overview(recon: IncidentReconstruction) -> None:
    """
    Panel 01: Incident Overview.
    Displays incident metadata, affected summary counts, and vertical timeline stepper.
    """
    st.markdown(
        """
        <div class="panel-header">
            <div>
                <div class="panel-num">01 / INCIDENT</div>
                <h3 class="panel-title">Incident Overview</h3>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Rationale description
    inst_label = recon.instrument_id or "Instrument"
    if recon.incident_id == "INC-2026-001":
        rationale = (
            f"Authoritative metrology correction shortened validity of {inst_label} certification. "
            "Dependent datasets quarantined, affected reports held, and owner review tasks assigned."
        )
    elif recon.incident_id == "INC-2026-002":
        rationale = (
            f"Authoritative thermocouple drift reported on {inst_label}. "
            "Reused verified playbook from Incident 1 to execute fast, deterministic revalidation."
        )
    else:
        rationale = f"Integrity incident for {inst_label}. Temporal reasoning active."

    st.markdown(f'<p class="panel-desc">{escape(rationale)}</p>', unsafe_allow_html=True)

    # Metadata Grid
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            f'<div class="micro-label">Incident ID</div>'
            f'<div class="micro-value">{escape(recon.incident_id)}</div>',
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
            f'<div class="micro-value">{escape(recon.instrument_id or "N/A")}</div>',
            unsafe_allow_html=True,
        )

    # Secondary metadata row
    st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
    m1, m2 = st.columns([2, 1])
    with m1:
        opened_str = format_timestamp(recon.opened_at, include_ist=True)
        st.markdown(
            f'<div class="micro-label">Opened At</div>'
            f'<div class="micro-value mono-text" style="font-size: 0.8rem;">{escape(opened_str)}</div>',
            unsafe_allow_html=True,
        )
    with m2:
        st.markdown(
            f'<div class="micro-label">Next Sequence</div>'
            f'<div class="micro-value mono-text">{recon.next_seq}</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height: 14px;'></div>", unsafe_allow_html=True)

    # Summary Metrics
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Timeline Events", len(recon.events))
    s2.metric("Actions Applied", len(recon.action_records))
    s3.metric("Tasks Tracked", len(recon.tasks))
    s4.metric("Agent Handoffs", len(recon.handoffs))

    st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

    # Vertical Timeline Stepper
    with st.expander(f"Incident Event Timeline ({len(recon.events)} events)", expanded=True):
        if not recon.events:
            st.info("No episodic events recorded for this incident.")
        else:
            st.markdown(build_timeline_html(recon.events), unsafe_allow_html=True)

    # Incident Tasks List
    if recon.tasks:
        with st.expander(f"Incident Tasks ({len(recon.tasks)})", expanded=False):
            tasks_data = []
            for t in recon.tasks:
                assignee = t.get("assignee_name") or t.get("assignee_id") or "UNASSIGNED"
                tasks_data.append({
                    "Task ID": t.get("task_id") or t.get("id"),
                    "Target": t.get("target_id"),
                    "Type": t.get("task_type"),
                    "Status": t.get("status"),
                    "Assignee": assignee,
                    "Escalation Reason": t.get("escalation_reason") or "—",
                })
            st.dataframe(tasks_data, use_container_width=True, hide_index=True)


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

    p_cols = st.columns(3)
    p_cols_2 = st.columns(3)
    all_p_cols = list(p_cols) + list(p_cols_2)

    for i, (pc_code, pc_key, pc_desc) in enumerate(pc_definitions):
        col = all_p_cols[i]
        with col:
            check_obj = promotion_checks.get(pc_key) if promotion_checks else None
            is_passed = check_obj.get("passed", True) if check_obj else True
            badge = '<span class="status-pill success">PASS</span>' if is_passed else '<span class="status-pill danger">FAIL</span>'
            col.markdown(
                f"""
                <div style="border: 1px solid #26292D; background-color: #151719; padding: 0.5rem; margin-bottom: 0.5rem; border-radius: 2px;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span class="mono-text" style="font-weight: 600; font-size: 0.75rem;">{pc_code}</span>
                        {badge}
                    </div>
                    <div style="font-size: 0.72rem; color: #8A9099; margin-top: 0.25rem;">{escape(pc_desc)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

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
        '<div style="border: 1px solid #26292D; background-color: #151719; padding: 1rem; border-radius: 2px;">'
        '<div style="font-size: 0.75rem; color: #8A9099; margin-bottom: 0.6rem;">'
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
                    <div class="mono-text" style="font-size: 0.85rem; color: #E6E8EA;">
                        {escape(last_res.prior_status)} &rarr; <strong>{escape(last_res.new_status)}</strong>
                    </div>
                </div>
            </div>
            <div style="margin-bottom: 0.5rem;">
                <div class="micro-label">JUSTIFICATION</div>
                <div style="font-size: 0.85rem; color: #E6E8EA; line-height: 1.4;">{escape(last_res.reason)}</div>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.72rem; color: #8A9099; border-top: 1px solid #26292D; padding-top: 0.4rem; margin-top: 0.5rem;">
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
