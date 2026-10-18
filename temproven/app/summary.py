"""
temproven/app/summary.py
────────────────────────
Streamlit presentation services for the Investigation Summary:
- Streamlit-native presentation: clean typography, status pills, no raw HTML for dynamic text.
- Re-exports pure Milestone, LeadAnswers, and build_* from presenters.py.
"""

from __future__ import annotations

import streamlit as st

from temproven.app.presenters import (
    ClosureRepresentation,
    IncidentReportRestrictions,
    LeadAnswers,
    Milestone,
    ReportRestrictionDetail,
    _plural,
    build_investigation_lead,
    build_investigation_summary,
    describe_incident_closure,
    describe_incident_report_restrictions,
    describe_single_report_restriction,
)


def render_investigation_summary(
    lead: LeadAnswers,
    milestones: list[Milestone],
) -> None:
    """
    Render the concise Investigation Summary using 100% native Streamlit components.
    Guaranteed zero HTML generation for dynamic text.
    """
    st.caption("INVESTIGATION SUMMARY (DEFAULT VIEW)")

    # 1. Plain-English Lead Answers (4 rows)
    with st.container(border=True):
        col_q1, col_a1 = st.columns([1.5, 5])
        with col_q1:
            st.caption("WHAT HAPPENED?")
        with col_a1:
            st.text(lead.what_happened)

        col_q2, col_a2 = st.columns([1.5, 5])
        with col_q2:
            st.caption("WHAT WAS AFFECTED?")
        with col_a2:
            st.text(lead.what_was_affected)

        col_q3, col_a3 = st.columns([1.5, 5])
        with col_q3:
            st.caption("WHAT ACTIONS TAKEN?")
        with col_a3:
            st.text(lead.actions_taken)

        col_q4, col_a4, col_b4 = st.columns([1.5, 4.2, 0.8])
        with col_q4:
            st.caption("CHECKS SUCCESSFUL?")
        with col_a4:
            st.text(lead.checks_successful)
        with col_b4:
            if lead.checks_status == "PASS":
                st.badge("PASS", color="green")
            elif lead.checks_status == "FAIL":
                st.badge("FAIL", color="red")
            else:
                st.badge("NOT RECORDED", color="gray")

    # 2. Key Investigation Milestones (M1–M9)
    with st.container(border=True):
        st.caption("KEY INVESTIGATION MILESTONES")
        for m in milestones:
            c_badge, c_body = st.columns([1.1, 5.9])
            with c_badge:
                if m.status == "COMPLETED":
                    st.badge("COMPLETED", color="green")
                elif m.status == "FAILED":
                    st.badge("FAILED", color="red")
                elif m.status == "RECORDED":
                    st.badge("RECORDED", color="blue")
                else:
                    st.badge("NOT RECORDED", color="gray")
            with c_body:
                ref_text = f" [Seq #{', #'.join(map(str, m.supporting_seqs))}]" if m.supporting_seqs else ""
                st.write(f"**{m.label}:** {m.statement}")
                if ref_text:
                    st.caption(f"Traceability: {m.source_description}{ref_text}")
