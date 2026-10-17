"""
temproven/app/streamlit_app.py
──────────────────────────────
Phase 5: Minimal, Polished Streamlit UI for Temproven.

Editorial, technical laboratory integrity dashboard visualizing:
- 01 / INCIDENT: Incident Overview, metadata, summary counts, vertical timeline stepper
- 02 / IMPACT: Downstream Impact Trace, Graphviz lineage DAG, affected items tables
- 03 / INVESTIGATION: Verified Playbook & Procedural Memory, postconditions, steps, efficiency
- 04 / PUBLICATION: Backend Publication Gate, live dependency check, honest CTA, hero block

FalkorDB is the sole source of truth. Zero writes on page load or rerun.
"""

from __future__ import annotations

import streamlit as st

from temproven import config
from temproven.app.components import (
    render_header,
    render_section_1_overview,
    render_section_2_impact,
    render_section_3_memory,
    render_section_4_gate,
)
from temproven.app.data import (
    check_db_connection,
    execute_gate_action,
    fetch_belief_change,
    fetch_impact_data,
    fetch_incident_detail,
    fetch_incident_metrics,
    fetch_incidents,
    fetch_playbook_data,
    fetch_report_details,
    fetch_reports,
)
from temproven.app.theme import CUSTOM_CSS
from temproven.clock import RealClock
from temproven.graph.gateway import GraphGateway


# Configure page layout and metadata
st.set_page_config(
    page_title="Temproven — Lab Integrity & Incident Learning",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Inject dark theme design tokens & styles
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


@st.cache_resource
def get_gateway(graph_name: str | None = None) -> GraphGateway:
    """Acquire single cached GraphGateway for the session."""
    name = graph_name or config.GRAPH_NAME
    return GraphGateway(graph_name=name)


def main() -> None:
    gw = get_gateway(config.GRAPH_NAME)

    # 1. Connectivity Check
    db_ok, db_err = check_db_connection(gw)
    if not db_ok:
        render_header(
            db_ok=False,
            graph_name=config.GRAPH_NAME,
            host=config.FALKORDB_HOST,
            port=config.FALKORDB_PORT,
            error_msg=db_err,
        )
        st.error(
            f"FalkorDB is currently unreachable at {config.FALKORDB_HOST}:{config.FALKORDB_PORT}.\n\n"
            f"Error: {db_err}\n\n"
            "Please ensure the FalkorDB Docker container is running."
        )
        st.stop()

    render_header(
        db_ok=True,
        graph_name=config.GRAPH_NAME,
        host=config.FALKORDB_HOST,
        port=config.FALKORDB_PORT,
    )

    # 2. Query Available Incidents
    incidents = fetch_incidents(gw)
    if not incidents:
        st.warning(
            f"No incidents found in graph '{config.GRAPH_NAME}'.\n\n"
            "Please seed and run the demo acts first via the CLI:\n\n"
            "```bash\n"
            "python -m temproven.demo --act all\n"
            "```"
        )
        st.stop()

    # 3. Sidebar Navigation & Incident Selection
    st.sidebar.markdown(
        """
        <div style="font-family: monospace; font-size: 0.75rem; letter-spacing: 0.08em; text-transform: uppercase; color: #8A9099; margin-bottom: 0.5rem;">
            Investigation Context
        </div>
        """,
        unsafe_allow_html=True,
    )

    incident_map = {
        inc["id"]: f"{inc['id']} [{inc['status']}] — {inc.get('instrument_id') or 'General'}"
        for inc in incidents
    }

    selected_incident_id = st.sidebar.selectbox(
        "Active Incident",
        options=list(incident_map.keys()),
        format_func=lambda i_id: incident_map[i_id],
        key="selected_incident_id",
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown(
        """
        <div style="font-size: 0.78rem; color: #8A9099; line-height: 1.5;">
            <strong>Temproven</strong> enforces laboratory integrity when evidence changes.
            <br><br>
            <strong>Track 1</strong>: Temporal reasoning changes real application behavior
            (quarantine, gate, tasks).
            <br><br>
            <strong>Track 2</strong>: Episodic, semantic and procedural memory persist in
            FalkorDB with playbook reuse.
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 4. Fetch Structured Data for the Selected Incident
    recon = fetch_incident_detail(gw, selected_incident_id)
    if not recon:
        st.error(f"Incident '{selected_incident_id}' could not be reconstructed from graph nodes.")
        st.stop()

    impact_data = fetch_impact_data(
        gw,
        recon.instrument_id,
        as_of_recorded=recon.opened_at,
    )

    playbook_data = fetch_playbook_data(gw, selected_incident_id)

    cert_id = (
        "CERT-A1" if recon.instrument_id == "INS-A"
        else ("CERT-B2" if recon.instrument_id == "INS-B" else None)
    )
    belief_change = fetch_belief_change(gw, cert_id, as_of_recorded=recon.opened_at) if cert_id else None

    metrics = fetch_incident_metrics(gw, selected_incident_id)
    baseline_metrics = fetch_incident_metrics(gw, "INC-2026-001") if selected_incident_id != "INC-2026-001" else None

    reports = fetch_reports(gw)

    # 5. Render 2x2 Grid Architecture
    # ── Top Row: Panel 01 & Panel 02 ─────────────────────────────────────────
    col_top_left, col_top_right = st.columns(2, gap="large")

    with col_top_left:
        render_section_1_overview(recon)

    with col_top_right:
        render_section_2_impact(recon.instrument_id, impact_data)

    st.markdown(
        "<div style='height: 1px; background-color: #26292D; margin: 1.5rem 0;'></div>",
        unsafe_allow_html=True,
    )

    # ── Bottom Row: Panel 03 & Panel 04 ──────────────────────────────────────
    col_bottom_left, col_bottom_right = st.columns(2, gap="large")

    with col_bottom_left:
        render_section_3_memory(
            selected_incident_id,
            playbook_data,
            belief_change=belief_change,
            metrics=metrics,
            baseline_metrics=baseline_metrics,
        )

    with col_bottom_right:
        def on_publish(report_id: str, actor: str):
            return execute_gate_action(gw, report_id, actor, clock=RealClock())

        def get_report_details(report_id: str):
            return fetch_report_details(gw, report_id)

        render_section_4_gate(
            reports,
            on_publish_fn=on_publish,
            report_details_fn=get_report_details,
        )


if __name__ == "__main__":
    main()
