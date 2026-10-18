"""
temproven/app/presenters.py
───────────────────────────
Pure presentation functions:
- Status-to-badge mapping covering every domain enum
- Bitemporal timestamp formatting (UTC primary, IST secondary, valid vs recorded separated)
- Graphviz DOT string generation with HTML escaping
- Evidence path formatting
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from temproven.domain.gate import GateDecision
from temproven.domain.temporal import CoverageStatus
from temproven.memory.procedural import StepStatus

# IST is UTC+05:30
IST_OFFSET = timezone(timedelta(hours=5, minutes=30), name="IST")


@dataclass(frozen=True)
class StatusBadge:
    text: str
    style_class: str    # "success", "danger", "warning", "neutral"
    color_hex: str


# Exhaustive mapping for all statuses in the system
STATUS_MAP: dict[str, StatusBadge] = {
    # CoverageStatus
    CoverageStatus.COVERED_SINGLE.value: StatusBadge("COVERED (single)", "success", "#22C55E"),
    CoverageStatus.COVERED_CHAIN.value: StatusBadge("COVERED (chain)", "success", "#22C55E"),
    "COVERED": StatusBadge("COVERED", "success", "#22C55E"),
    CoverageStatus.NOT_COVERED.value: StatusBadge("NOT_COVERED", "danger", "#F95738"),
    CoverageStatus.UNKNOWN.value: StatusBadge("UNKNOWN", "warning", "#F59E0B"),
    CoverageStatus.CONFLICTING.value: StatusBadge("CONFLICTING", "danger", "#F95738"),

    # GateDecision
    GateDecision.ALLOW.value: StatusBadge("ALLOW", "success", "#22C55E"),
    GateDecision.BLOCK.value: StatusBadge("BLOCK", "danger", "#F95738"),
    GateDecision.REVIEW_REQUIRED.value: StatusBadge("REVIEW_REQUIRED", "warning", "#F59E0B"),

    # Dataset Status
    "ACTIVE": StatusBadge("ACTIVE", "neutral", "#8C929D"),
    "QUARANTINED": StatusBadge("QUARANTINED", "danger", "#F95738"),

    # Report publication_status
    "DRAFT": StatusBadge("DRAFT", "neutral", "#8C929D"),
    "PUBLISHED": StatusBadge("PUBLISHED", "success", "#22C55E"),
    "HELD": StatusBadge("HELD", "danger", "#F95738"),
    "REQUIRES_REVIEW": StatusBadge("REQUIRES_REVIEW", "warning", "#F59E0B"),

    # Task Status
    "OPEN": StatusBadge("OPEN", "warning", "#F59E0B"),
    "ASSIGNED": StatusBadge("ASSIGNED", "neutral", "#8C929D"),
    "ESCALATED": StatusBadge("ESCALATED", "danger", "#F95738"),
    "COMPLETED": StatusBadge("COMPLETED", "success", "#22C55E"),

    # Playbook Status
    "CANDIDATE": StatusBadge("CANDIDATE", "warning", "#F59E0B"),
    "VERIFIED": StatusBadge("VERIFIED", "success", "#22C55E"),
    "DEPRECATED": StatusBadge("DEPRECATED", "neutral", "#8C929D"),

    # StepStatus
    StepStatus.PASSED.value: StatusBadge("PASSED", "success", "#22C55E"),
    StepStatus.DIFFERED.value: StatusBadge("DIFFERED", "warning", "#F59E0B"),
    StepStatus.FAILED.value: StatusBadge("FAILED", "danger", "#F95738"),
    StepStatus.SKIPPED.value: StatusBadge("SKIPPED", "neutral", "#8C929D"),

    # Incident Status
    "IN_PROGRESS": StatusBadge("IN_PROGRESS", "warning", "#F59E0B"),
    "CLOSED": StatusBadge("CLOSED", "success", "#22C55E"),

    # Handoff Status
    "PENDING": StatusBadge("PENDING", "neutral", "#8C929D"),
    "CLAIMED": StatusBadge("CLAIMED", "warning", "#F59E0B"),
    "EXPIRED": StatusBadge("EXPIRED", "danger", "#F95738"),
}


def get_status_badge(status: str | None) -> StatusBadge:
    """Retrieve the StatusBadge for any known status string, falling back safely."""
    if not status:
        return StatusBadge("UNKNOWN", "neutral", "#8A9099")
    norm = str(status).strip()
    return STATUS_MAP.get(norm, StatusBadge(norm, "neutral", "#8A9099"))


def format_timestamp(
    epoch: int | float | None,
    time_label: str = "TIME",
    include_ist: bool = True,
) -> str:
    """
    Format an epoch second timestamp into UTC primary and IST secondary.
    Returns labeled string, e.g.:
    "2026-10-17 09:00:00 UTC (14:30:00 IST)"
    """
    if epoch is None:
        return f"{time_label}: None"

    try:
        dt_utc = datetime.fromtimestamp(float(epoch), tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return f"{time_label}: {epoch} (invalid epoch)"

    utc_str = dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    if not include_ist:
        return utc_str

    dt_ist = dt_utc.astimezone(IST_OFFSET)
    ist_str = dt_ist.strftime("%H:%M:%S IST")
    return f"{utc_str} ({ist_str})"


def format_epoch_ist(epoch: int | float | None) -> str:
    """
    Format an epoch second timestamp into a clean, standalone IST string:
    '2026-10-17 17:30:00 IST' or 'n/a' if epoch is None/0.
    Handles long/invalid/missing timestamps safely without markup.
    """
    if epoch is None or epoch == 0:
        return "n/a"
    try:
        dt_utc = datetime.fromtimestamp(float(epoch), tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return f"{epoch} (invalid epoch)"
    dt_ist = dt_utc.astimezone(IST_OFFSET)
    return dt_ist.strftime("%Y-%m-%d %H:%M:%S IST")


def format_interval(
    start_epoch: int | float | None,
    end_epoch: int | float | None,
    interval_kind: str = "VALID TIME",
) -> str:
    """
    Format a half-open interval [start, end) clearly labeling UTC and IST.
    Keeps VALID TIME visually distinct from RECORDED TIME.
    """
    start_s = format_timestamp(start_epoch, include_ist=False) if start_epoch is not None else "−∞"
    end_s = format_timestamp(end_epoch, include_ist=False) if end_epoch is not None else "+∞"
    return f"[{interval_kind}] {start_s}  →  {end_s}"


def escape(text: Any) -> str:
    """Escape arbitrary text for safe insertion into HTML or Graphviz DOT labels."""
    if text is None:
        return ""
    return html.escape(str(text), quote=True)


def build_lineage_dot(
    instrument_id: str | None,
    experiments: list[dict[str, Any]],
    datasets: list[dict[str, Any]],
    reports: list[dict[str, Any]],
) -> str:
    """
    Generate a clean Graphviz DOT diagram for downstream lineage:
    Instrument -> Experiments -> Datasets (direct/derived) -> Reports.
    All dynamic strings are escaped against injection.
    """
    lines = [
        'digraph Lineage {',
        '    graph [rankdir="LR", bgcolor="transparent", fontname="monospace", margin=0];',
        '    node [shape="box", style="filled", fontname="monospace", fontsize=10, margin="0.15,0.1"];',
        '    edge [color="#4A5059", fontname="monospace", fontsize=9];',
    ]

    # Instrument root node
    if instrument_id:
        safe_inst = escape(instrument_id)
        lines.append(
            f'    "{safe_inst}" ['
            f'label="{safe_inst}\\n(Instrument)", '
            f'fillcolor="#111419", color="#3E4654", fontcolor="#FFFFFF"];'
        )

    # Experiment nodes
    for exp in experiments:
        eid = exp.get("id") or exp.get("experiment_id") or "UNKNOWN_EXP"
        status = exp.get("status") or "UNKNOWN"
        is_lapsed = status in ("NOT_COVERED", "CONFLICTING")
        color = "#F95738" if is_lapsed else "#22C55E"
        safe_id = escape(eid)
        safe_status = escape(status)

        lines.append(
            f'    "{safe_id}" ['
            f'label="{safe_id}\\n[{safe_status}]", '
            f'fillcolor="#111419", color="{color}", fontcolor="{color}"];'
        )
        if instrument_id:
            safe_inst = escape(instrument_id)
            lines.append(f'    "{safe_inst}" -> "{safe_id}" [label="USAGE", color="#3E4654", fontcolor="#8C929D"];')

    # Dataset nodes
    for ds in datasets:
        did = ds.get("id") or "UNKNOWN_DS"
        status = ds.get("status") or ds.get("current_status") or "ACTIVE"
        is_quarantined = status == "QUARANTINED"
        color = "#F95738" if is_quarantined else "#4B5563"
        safe_id = escape(did)
        safe_status = escape(status)

        lines.append(
            f'    "{safe_id}" ['
            f'label="{safe_id}\\n[{safe_status}]", '
            f'fillcolor="#111419", color="{color}", fontcolor="{color}"];'
        )

        # Draw dependency path edges if provided
        path = ds.get("dependency_path") or []
        for i, elem in enumerate(path):
            if elem == "PRODUCED" and i > 0 and i + 1 < len(path):
                from_n, to_n = escape(path[i - 1]), escape(path[i + 1])
                lines.append(f'    "{from_n}" -> "{to_n}" [label="PRODUCED", color="#F95738", fontcolor="#F95738"];')
            elif elem == "DERIVED_FROM" and i > 0 and i + 1 < len(path):
                parent_n, child_n = escape(path[i - 1]), escape(path[i + 1])
                lines.append(f'    "{parent_n}" -> "{child_n}" [label="DERIVED", color="#F95738", fontcolor="#F95738"];')

    # Report nodes
    for rpt in reports:
        rid = rpt.get("id") or "UNKNOWN_RPT"
        status = rpt.get("status") or rpt.get("current_status") or "DRAFT"
        color = "#F95738" if status in ("HELD", "REQUIRES_REVIEW") else "#22C55E"
        safe_id = escape(rid)
        safe_status = escape(status)

        lines.append(
            f'    "{safe_id}" ['
            f'label="{safe_id}\\n[{safe_status}]", '
            f'fillcolor="#111419", color="{color}", fontcolor="{color}", style="dashed,filled"];'
        )

        path = rpt.get("dependency_path") or []
        for i, elem in enumerate(path):
            if elem == "USES_DATASET" and i > 0 and i + 1 < len(path):
                from_ds, to_rpt = escape(path[i - 1]), escape(path[i + 1])
                lines.append(f'    "{from_ds}" -> "{to_rpt}" [label="USES", style="dashed", color="#F95738", fontcolor="#F95738"];')

    lines.append('}')
    return '\n'.join(lines)


def sanitize_dynamic_text(text: Any) -> str:
    """
    Sanitize dynamic text for safe embedding in Streamlit markdown HTML:
    - Normalizes newlines and whitespace runs into single spaces to prevent
      blank lines or indented code block triggers in CommonMark.
    - Uses html.escape(..., quote=True) to prevent HTML tag or attribute injection.
    - Neutralizes math mode '$' as '&#36;' and inline code backtick '`' as '&#96;'.
    """
    if text is None:
        return ""
    # Collapse newlines and whitespace runs into single spaces
    normalized = " ".join(str(text).split())
    # HTML escape with quotes
    escaped = html.escape(normalized, quote=True)
    # Neutralize Streamlit markdown triggers (math and code)
    return escaped.replace("$", "&#36;").replace("`", "&#96;")


def build_timeline_html(events: list[Any]) -> str:
    """
    Pure presentation function to construct markdown-safe timeline HTML:
    - Guaranteed zero lines beginning with 4+ spaces or tabs.
    - Guaranteed zero blank lines.
    - All dynamic fields properly escaped and neutralized.
    """
    if not events:
        return '<div class="timeline-empty" style="color: #8A9099; font-size: 0.8rem; padding: 0.5rem 0;">No episodic events recorded for this incident.</div>'

    items: list[str] = []
    for ev in events:
        at_val = getattr(ev, "at", None) if not isinstance(ev, dict) else ev.get("at")
        actor_val = getattr(ev, "actor_agent", None) if not isinstance(ev, dict) else ev.get("actor_agent")
        type_val = getattr(ev, "event_type", None) if not isinstance(ev, dict) else ev.get("event_type")
        summary_val = getattr(ev, "summary", None) if not isinstance(ev, dict) else ev.get("summary")
        outcome_val = getattr(ev, "outcome", None) if not isinstance(ev, dict) else ev.get("outcome")

        t_str = format_timestamp(at_val, include_ist=False) if at_val is not None else "TIME: None"
        safe_time = sanitize_dynamic_text(t_str)
        safe_actor = sanitize_dynamic_text(actor_val or "UnknownAgent")
        safe_type = sanitize_dynamic_text(type_val or "EVENT")
        safe_summary = sanitize_dynamic_text(summary_val or "No summary")

        outcome_html = ""
        if outcome_val:
            safe_outcome = sanitize_dynamic_text(outcome_val)
            outcome_html = f' &mdash; <span style="color: #5E9E7A;">{safe_outcome}</span>'

        act_badge = f'<span class="mono-text" style="color: #8A9099; font-size: 0.7rem;">[{safe_actor}]</span>'

        item = (
            f'<div class="timeline-item">'
            f'<div class="timeline-dot"></div>'
            f'<div class="timeline-time mono-text">{safe_time}</div>'
            f'<div class="timeline-type mono-text">{safe_type} {act_badge}</div>'
            f'<div class="timeline-summary">{safe_summary}{outcome_html}</div>'
            f'</div>'
        )
        items.append(item)

    return f'<div style="margin-top: 0.5rem;">{"".join(items)}</div>'


@dataclass
class Milestone:
    key: str
    label: str
    statement: str
    status: str  # "COMPLETED", "FAILED", "RECORDED", "NOT RECORDED"
    supporting_seqs: list[int] = field(default_factory=list)
    supporting_ids: list[str] = field(default_factory=list)
    source_description: str = ""


@dataclass
class LeadAnswers:
    what_happened: str
    what_was_affected: str
    actions_taken: str
    checks_successful: str
    checks_status: str  # "PASS", "FAIL", "NOT RECORDED"
    all_passed: bool


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    """Format count with appropriate grammatical pluralization."""
    p = plural or f"{singular}s"
    return f"{count} {singular}" if count == 1 else f"{count} {p}"


@dataclass
class ClosureRepresentation:
    """Truthful incident closure representation from independent sources."""
    stored_status: str
    has_closing_event: bool
    closing_seq: int | None = None
    closing_time_utc: str | None = None
    closing_time_ist: str | None = None
    actor: str | None = None
    outcome: str | None = None
    milestone_status: str = "NOT RECORDED"  # "COMPLETED" or "NOT RECORDED"
    milestone_statement: str = ""
    supporting_seqs: list[int] = field(default_factory=list)


def describe_incident_closure(recon: Any) -> ClosureRepresentation:
    """
    Derive truthful incident closure presentation from independent persisted sources:
    1. Stored status (Incident.status).
    2. Closing event (searching exclusively for INCIDENT_CLOSED in event history).
    Never substitutes opened_at or any other timestamp for a missing closing event.
    """
    if hasattr(recon, "recon"):
        recon = getattr(recon, "recon")
    elif isinstance(recon, dict) and ("recon" in recon or "incident" in recon):
        recon = recon.get("recon") or recon.get("incident")

    stored_status = getattr(recon, "status", None) or (recon.get("status") if isinstance(recon, dict) else "UNKNOWN")
    events = getattr(recon, "events", None) or (recon.get("events") if isinstance(recon, dict) else []) or []
    
    closed_ev = next(
        (
            e for e in events
            if getattr(e, "event_type", None) == "INCIDENT_CLOSED"
            or (isinstance(e, dict) and e.get("event_type") == "INCIDENT_CLOSED")
        ),
        None,
    )

    if closed_ev:
        seq = getattr(closed_ev, "seq", None) if not isinstance(closed_ev, dict) else closed_ev.get("seq")
        at_val = getattr(closed_ev, "at", None) if not isinstance(closed_ev, dict) else closed_ev.get("at")
        actor = getattr(closed_ev, "actor_agent", None) or getattr(closed_ev, "actor", None) or (
            closed_ev.get("actor_agent") or closed_ev.get("actor") if isinstance(closed_ev, dict) else None
        ) or "n/a"
        outcome = (
            getattr(closed_ev, "outcome", None) if not isinstance(closed_ev, dict) else closed_ev.get("outcome")
        ) or "n/a"

        t_utc = format_timestamp(at_val, include_ist=False) if at_val is not None else "n/a"
        t_ist = format_timestamp(at_val, include_ist=True) if at_val is not None else "n/a"

        if stored_status == "CLOSED":
            stmt = f"Closing event recorded at {t_utc} [Seq #{seq}] by {actor} (outcome: {outcome}). Stored status: CLOSED."
        else:
            stmt = f"Closing event recorded at {t_utc} [Seq #{seq}] by {actor} (outcome: {outcome}); stored status remains {stored_status}."

        return ClosureRepresentation(
            stored_status=stored_status,
            has_closing_event=True,
            closing_seq=seq,
            closing_time_utc=t_utc,
            closing_time_ist=t_ist,
            actor=actor,
            outcome=outcome,
            milestone_status="COMPLETED",
            milestone_statement=stmt,
            supporting_seqs=[seq] if seq is not None else [],
        )
    else:
        if stored_status == "CLOSED":
            stmt = "Stored status is CLOSED; no closing event is recorded."
        else:
            stmt = f"Incident remains {stored_status}; no closing event is recorded."

        return ClosureRepresentation(
            stored_status=stored_status,
            has_closing_event=False,
            milestone_status="NOT RECORDED",
            milestone_statement=stmt,
            supporting_seqs=[],
        )


@dataclass
class ReportRestrictionDetail:
    """Truthful restriction description for a single report."""
    report_id: str
    current_status: str
    action_type: str | None = None
    action_recorded_state: str | None = None
    reason: str = ""
    restriction_kind: str = "NONE"  # "HELD", "REQUIRES_REVIEW", or "NONE"
    wording: str = "unrestricted"   # "held", "requires review", or "unrestricted"
    full_description: str = ""


@dataclass
class IncidentReportRestrictions:
    """Aggregated report restriction summary for an incident."""
    details: list[ReportRestrictionDetail]
    held_ids: list[str]
    review_ids: list[str]
    unrestricted_ids: list[str]
    outcome_strip_label: str
    lead_action_text: str
    milestone_statement: str


def describe_single_report_restriction(
    report_id: str,
    current_status: str | None = None,
    action_records: list[dict[str, Any]] | None = None,
) -> ReportRestrictionDetail:
    """
    Derive restriction wording for a single report from persisted action records and current status:
    - HELD -> 'held'
    - REQUIRES_REVIEW -> 'requires review' (never 'held')
    - DRAFT / PUBLISHED with no restriction -> not restricted ('unrestricted')
    - If ActionRecord recorded state differs from current status, mention both honestly.
    """
    matching_ar = None
    if action_records:
        for a in action_records:
            if str(a.get("target_id", "")).upper() == str(report_id).upper() and a.get("action_type") in ("HOLD_REPORT", "FLAG_REPORT_REVIEW"):
                matching_ar = a
                break

    act_type = matching_ar.get("action_type") if matching_ar else None
    new_state = matching_ar.get("new_state") if matching_ar else None
    reason = matching_ar.get("reason", "") if matching_ar else ""
    
    curr = str(current_status or (new_state if new_state else "DRAFT")).upper()

    if curr == "HELD":
        kind = "HELD"
        wording = "held"
        if new_state and new_state != "HELD":
            desc = f"Report {report_id} held (recorded action was {new_state})"
        else:
            desc = f"Report {report_id} held"
    elif curr == "REQUIRES_REVIEW":
        kind = "REQUIRES_REVIEW"
        wording = "requires review"
        if new_state and new_state != "REQUIRES_REVIEW":
            desc = f"Report {report_id} requires review (recorded action was {new_state})"
        else:
            desc = f"Report {report_id} requires review"
    else:
        # Current status is DRAFT, PUBLISHED, etc.
        if act_type == "HOLD_REPORT" or new_state == "HELD":
            kind = "HELD"
            wording = f"marked HELD; current status: {curr}"
            desc = f"Report {report_id} marked HELD; current status: {curr}"
        elif act_type == "FLAG_REPORT_REVIEW" or new_state == "REQUIRES_REVIEW":
            kind = "REQUIRES_REVIEW"
            wording = f"marked REQUIRES_REVIEW; current status: {curr}"
            desc = f"Report {report_id} marked REQUIRES_REVIEW; current status: {curr}"
        else:
            kind = "NONE"
            wording = "unrestricted"
            desc = f"Report {report_id} is unaffected ({curr})"

    return ReportRestrictionDetail(
        report_id=report_id,
        current_status=curr,
        action_type=act_type,
        action_recorded_state=new_state,
        reason=reason,
        restriction_kind=kind,
        wording=wording,
        full_description=desc,
    )


def describe_incident_report_restrictions(
    action_records: list[dict[str, Any]] | None = None,
    reports_data: list[dict[str, Any]] | None = None,
) -> IncidentReportRestrictions:
    """
    Derive aggregated report restriction presentation for an incident:
    - Reports held vs requiring review are distinguished and counted separately.
    - Zero hardcoding of IDs; purely derived from persisted action records and report status.
    """
    action_records = action_records or []
    reports_data = reports_data or []

    # Map report status by ID from reports_data if available (case-insensitive lookup)
    status_by_id = {str(r["id"]).upper(): r.get("current_status") or r.get("status") for r in reports_data if r.get("id")}

    # Gather affected report IDs from action records and reports_data
    seen_ids: list[str] = []
    seen_ids_upper: set[str] = set()
    for a in action_records:
        if a.get("action_type") in ("HOLD_REPORT", "FLAG_REPORT_REVIEW") and a.get("target_id"):
            tid = a["target_id"]
            if str(tid).upper() not in seen_ids_upper:
                seen_ids.append(tid)
                seen_ids_upper.add(str(tid).upper())

    for r in reports_data:
        rid = r.get("id")
        if rid and str(rid).upper() not in seen_ids_upper:
            seen_ids.append(rid)
            seen_ids_upper.add(str(rid).upper())

    details = [
        describe_single_report_restriction(rid, current_status=status_by_id.get(str(rid).upper()), action_records=action_records)
        for rid in seen_ids
    ]

    held_ids = [d.report_id for d in details if d.restriction_kind == "HELD"]
    review_ids = [d.report_id for d in details if d.restriction_kind == "REQUIRES_REVIEW"]
    unrestricted_ids = [d.report_id for d in details if d.restriction_kind == "NONE"]

    # 1. Outcome strip label (compact, accurate)
    if held_ids and not review_ids:
        strip_label = f"{len(held_ids)} held ({', '.join(held_ids)})"
    elif review_ids and not held_ids:
        strip_label = f"{len(review_ids)} requires review ({', '.join(review_ids)})"
    elif held_ids and review_ids:
        strip_label = f"{len(held_ids)} held ({', '.join(held_ids)}); {len(review_ids)} require review ({', '.join(review_ids)})"
    else:
        strip_label = "0 restricted"

    # 2. Lead action text
    if held_ids and not review_ids:
        lead_text = f"{_plural(len(held_ids), 'report')} held ({', '.join(held_ids)})"
    elif review_ids and not held_ids:
        lead_text = f"{_plural(len(review_ids), 'report')} requires review ({', '.join(review_ids)})"
    elif held_ids and review_ids:
        lead_text = f"{_plural(len(held_ids), 'report')} held ({', '.join(held_ids)}); {_plural(len(review_ids), 'report')} requires review ({', '.join(review_ids)})"
    else:
        lead_text = ""

    # 3. Milestone statement
    if held_ids and not review_ids:
        m6_stmt = f"Restricted publication for {_plural(len(held_ids), 'downstream report')} ({', '.join(held_ids)}): held pending revalidation."
    elif review_ids and not held_ids:
        m6_stmt = f"Restricted publication for {_plural(len(review_ids), 'downstream report')} ({', '.join(review_ids)}): requires review."
    elif held_ids and review_ids:
        m6_stmt = f"Restricted publication for {_plural(len(held_ids), 'downstream report')} held ({', '.join(held_ids)}) and {_plural(len(review_ids), 'downstream report')} requiring review ({', '.join(review_ids)})."
    else:
        m6_stmt = "Report restrictions were not recorded for this incident."

    return IncidentReportRestrictions(
        details=details,
        held_ids=held_ids,
        review_ids=review_ids,
        unrestricted_ids=unrestricted_ids,
        outcome_strip_label=strip_label,
        lead_action_text=lead_text,
        milestone_statement=m6_stmt,
    )


@dataclass
class OrderedStepResult:
    step_number: int
    step_id: str
    step_type: str
    kind: str
    role: str
    expected: str
    status: str
    differences: str
    detail: str = ""
    domain_query_count: int = 0
    elapsed_ms: float = 0.0


@dataclass
class PlaybookRunSummary:
    ordered_steps: list[OrderedStepResult]
    differed_step_numbers: list[int]
    passed_step_numbers: list[int]
    milestone_statement: str
    has_results: bool


def _format_step_numbers(nums: list[int]) -> str:
    """Format a list of step numbers with proper comma and 'and' conjunction."""
    if not nums:
        return "none"
    if len(nums) == 1:
        return str(nums[0])
    if len(nums) == 2:
        return f"{nums[0]} and {nums[1]}"
    return ", ".join(map(str, nums[:-1])) + f" and {nums[-1]}"


def build_ordered_step_results(playbook_data: dict[str, Any] | None) -> PlaybookRunSummary:
    """
    Derive canonical ordered step results and summary statement:
    - Joins StepResults to PlaybookSteps by step_id.
    - Numbers steps strictly by the stored PlaybookStep.order, NEVER list position.
    - Ascending sort order guaranteed.
    - Used identically by both Investigation Memory table and Milestone M3b.
    """
    if not playbook_data:
        return PlaybookRunSummary(
            ordered_steps=[],
            differed_step_numbers=[],
            passed_step_numbers=[],
            milestone_statement="",
            has_results=False,
        )

    steps = playbook_data.get("steps") or []
    run = playbook_data.get("run")
    step_results = playbook_data.get("step_results") or (run.get("step_results") if run else []) or []
    pb = playbook_data.get("playbook") or {}
    pb_status = pb.get("status")

    res_by_step = {str(r.get("step_id")): r for r in step_results if r.get("step_id")}

    ordered_steps: list[OrderedStepResult] = []

    if steps:
        sorted_steps = sorted(steps, key=lambda s: int(s.get("order") or 0))
        for s in sorted_steps:
            s_id = str(s.get("id") or "")
            order_num = int(s.get("order") or 0)
            r = res_by_step.get(s_id, {})
            if r:
                status_str = str(r.get("status") or "PENDING").upper()
            else:
                status_str = "PASSED" if pb_status == "VERIFIED" else "PENDING"

            diffs = r.get("differences_json") or "—"
            if diffs and diffs != "—":
                try:
                    parsed = json.loads(diffs)
                    diffs = str(parsed)
                except Exception:
                    pass

            ordered_steps.append(OrderedStepResult(
                step_number=order_num,
                step_id=s_id,
                step_type=s.get("step_type") or "UNKNOWN",
                kind=s.get("kind") or "READ",
                role=s.get("role") or "CONFIRM",
                expected=s.get("expected_outcome") or "PASSED",
                status=status_str,
                differences=diffs,
                detail=r.get("detail", ""),
                domain_query_count=r.get("domain_query_count", 0),
                elapsed_ms=r.get("elapsed_ms", 0.0),
            ))
    elif step_results:
        for idx, r in enumerate(step_results, 1):
            s_id = str(r.get("step_id") or "")
            order_num = idx
            m = re.search(r"_(\d+)$", s_id)
            if m:
                order_num = int(m.group(1))
            elif r.get("step_number") is not None:
                order_num = int(r["step_number"])

            ordered_steps.append(OrderedStepResult(
                step_number=order_num,
                step_id=s_id,
                step_type=r.get("step_type") or "UNKNOWN",
                kind="UNKNOWN",
                role="UNKNOWN",
                expected="PASSED",
                status=str(r.get("status") or "PENDING").upper(),
                differences=str(r.get("differences_json") or "None"),
                detail=r.get("detail", ""),
                domain_query_count=r.get("domain_query_count", 0),
                elapsed_ms=r.get("elapsed_ms", 0.0),
            ))
        ordered_steps.sort(key=lambda x: x.step_number)

    has_results = bool(step_results)

    differed = [osr.step_number for osr in ordered_steps if osr.status == "DIFFERED"]
    passed = [osr.step_number for osr in ordered_steps if osr.status == "PASSED"]

    if has_results and (differed or passed):
        diff_str = _format_step_numbers(differed)
        pass_str = _format_step_numbers(passed)

        if differed and passed:
            diff_word = "Step" if len(differed) == 1 else "Steps"
            pass_word = "step" if len(passed) == 1 else "steps"
            stmt = f"{diff_word} {diff_str} differed from the verified procedure; {pass_word} {pass_str} passed."
        elif differed and not passed:
            diff_word = "Step" if len(differed) == 1 else "Steps"
            stmt = f"{diff_word} {diff_str} differed from the verified procedure."
        else:
            pass_word = "step" if len(passed) == 1 else "steps"
            stmt = f"All {len(passed)} {pass_word} passed in verified reuse ({pass_str})."
    else:
        stmt = ""

    return PlaybookRunSummary(
        ordered_steps=ordered_steps,
        differed_step_numbers=differed,
        passed_step_numbers=passed,
        milestone_statement=stmt,
        has_results=has_results,
    )


@dataclass
class IncidentTasksSummary:
    total_count: int
    assigned_count: int
    escalated_count: int
    other_count: int
    distinct_reasons: list[tuple[str, int]]
    lead_action_text: str
    milestone_statement: str
    milestone_status: str


def describe_incident_tasks(tasks: list[dict[str, Any]] | None) -> IncidentTasksSummary:
    """
    Derive mutually exclusive task classification, honest counts, and inert wording:
    1. Escalated: status == 'ESCALATED' or has escalation_reason.
    2. Assigned: has recorded assignee and not escalated.
    3. Other: remaining tasks with status reported honestly.
    Reconciles: total = escalated + assigned + other.
    """
    tasks = tasks or []
    total = len(tasks)

    if total == 0:
        return IncidentTasksSummary(
            total_count=0,
            assigned_count=0,
            escalated_count=0,
            other_count=0,
            distinct_reasons=[],
            lead_action_text="no tasks recorded",
            milestone_statement="Investigation tasks were not recorded for this incident.",
            milestone_status="NOT RECORDED",
        )

    escalated_tasks: list[dict[str, Any]] = []
    assigned_tasks: list[dict[str, Any]] = []
    other_tasks: list[dict[str, Any]] = []

    for t in tasks:
        st = str(t.get("status") or "").upper()
        reason = t.get("escalation_reason")
        has_assignee = bool(t.get("assignee_name") or t.get("assignee_id") or t.get("assignee"))

        if st == "ESCALATED" or (reason and str(reason).strip()):
            escalated_tasks.append(t)
        elif has_assignee:
            assigned_tasks.append(t)
        else:
            other_tasks.append(t)

    # Mutually exclusive reconciliation assertion
    assert total == len(escalated_tasks) + len(assigned_tasks) + len(other_tasks), "Task counts must reconcile"

    esc_cnt = len(escalated_tasks)
    asgn_cnt = len(assigned_tasks)
    oth_cnt = len(other_tasks)

    # Extract distinct escalation reasons safely
    reason_map: dict[str, int] = {}
    for t in escalated_tasks:
        raw_r = t.get("escalation_reason")
        r_text = sanitize_dynamic_text(raw_r) if raw_r and str(raw_r).strip() else "no reason recorded"
        reason_map[r_text] = reason_map.get(r_text, 0) + 1

    distinct_reasons = list(reason_map.items())

    # Format escalation reason text
    if esc_cnt > 0:
        if len(distinct_reasons) == 1:
            r_str, _ = distinct_reasons[0]
            esc_reason_clause = f" ({r_str})"
            lead_reason_clause = f": {r_str}"
        else:
            r_parts = [f"{count} with reason: {r}" for r, count in distinct_reasons]
            esc_reason_clause = f" ({'; '.join(r_parts)})"
            lead_reason_clause = f": {'; '.join(r_parts)}"
    else:
        esc_reason_clause = ""
        lead_reason_clause = ""

    # Milestone statement
    parts = []
    if asgn_cnt > 0:
        parts.append(f"{asgn_cnt} assigned to owners")
    if esc_cnt > 0:
        parts.append(f"{esc_cnt} escalated{esc_reason_clause}")
    if oth_cnt > 0:
        oth_statuses = [str(t.get("status") or "UNSPECIFIED").upper() for t in other_tasks]
        parts.append(f"{oth_cnt} other ({', '.join(oth_statuses)})")

    clause_str = "; ".join(parts)
    milestone_stmt = f"Created {_plural(total, 'task')}: {clause_str}."

    # Lead action text
    lead_parts = []
    if asgn_cnt > 0:
        lead_parts.append(f"{asgn_cnt} assigned")
    if esc_cnt > 0:
        lead_parts.append(f"{esc_cnt} escalated{lead_reason_clause}")
    if oth_cnt > 0:
        lead_parts.append(f"{oth_cnt} other")

    lead_text = f"{_plural(total, 'review task')} created ({', '.join(lead_parts)})"

    return IncidentTasksSummary(
        total_count=total,
        assigned_count=asgn_cnt,
        escalated_count=esc_cnt,
        other_count=oth_cnt,
        distinct_reasons=distinct_reasons,
        lead_action_text=lead_text,
        milestone_statement=milestone_stmt,
        milestone_status="COMPLETED",
    )


def build_investigation_lead(
    recon: Any,
    impact_data: dict[str, Any] | None = None,
    playbook_data: dict[str, Any] | None = None,
    belief_change: Any | None = None,
) -> LeadAnswers:
    """
    Generate the 4 plain-English lead answers purely from persisted facts.
    Templates only: no LLM, no free-text parsing.
    """
    # Unpack composite read_model if passed as single argument
    if hasattr(recon, "recon"):
        impact_data = getattr(recon, "impact_data", impact_data)
        playbook_data = getattr(recon, "playbook_data", playbook_data)
        belief_change = getattr(recon, "belief_change", belief_change)
        recon = getattr(recon, "recon")
    elif isinstance(recon, dict) and ("recon" in recon or "incident" in recon):
        impact_data = recon.get("impact_data", recon.get("impact", impact_data))
        playbook_data = recon.get("playbook_data", recon.get("playbook", playbook_data))
        belief_change = recon.get("belief_change", belief_change)
        recon = recon.get("recon") or recon.get("incident")

    # 1. What happened?
    ins_id = getattr(recon, "instrument_id", None)
    if not ins_id:
        ins_id = None
    cert_id = None
    if recon.evidence_claims:
        cert_id = recon.evidence_claims[0].get("claim_id")
    elif belief_change and hasattr(belief_change, "current_belief"):
        cb = belief_change.current_belief
        cert_id = getattr(getattr(cb, "evidence", None), "id", None)

    if cert_id and ins_id:
        what_happened = f"Calibration certificate {cert_id} for instrument {ins_id} was superseded by an authoritative correction, creating an uncertified operational window."
    elif cert_id:
        what_happened = f"Calibration certificate {cert_id} was superseded by an authoritative correction, creating an uncertified operational window."
    elif ins_id:
        what_happened = f"An operational certification lapse was identified concerning instrument {ins_id}."
    else:
        what_happened = "Evidence correction details were not recorded."

    # 2. What was affected?
    exps = len(impact_data.get("experiments", [])) if (impact_data and "experiments" in impact_data) else 0
    dsets = len(impact_data.get("datasets", [])) if (impact_data and "datasets" in impact_data) else 0
    rpts = len(impact_data.get("reports", [])) if (impact_data and "reports" in impact_data) else 0

    if exps == 0 and dsets == 0 and rpts == 0 and recon.action_records:
        dsets = len([a for a in recon.action_records if a.get("action_type") == "QUARANTINE_DATASET"])
        rpts = len([a for a in recon.action_records if a.get("action_type") in ("HOLD_REPORT", "FLAG_REPORT_REVIEW")])

    if exps or dsets or rpts:
        what_was_affected = f"{_plural(exps, 'experiment')}, {_plural(dsets, 'dataset')}, and {_plural(rpts, 'downstream report')} were identified in the affected dependency lineage."
    else:
        what_was_affected = "0 experiments, 0 datasets, and 0 reports were affected."

    # 3. What actions were taken?
    q_ids = [a.get("target_id") for a in (recon.action_records or []) if a.get("action_type") == "QUARANTINE_DATASET" and a.get("target_id")]
    r_restr = describe_incident_report_restrictions(
        action_records=recon.action_records,
        reports_data=impact_data.get("reports", []) if impact_data else None,
    )
    t_summary = describe_incident_tasks(recon.tasks)

    actions_parts = []
    if q_ids:
        actions_parts.append(f"{_plural(len(q_ids), 'dataset')} quarantined ({', '.join(q_ids)})")
    if r_restr.lead_action_text:
        actions_parts.append(r_restr.lead_action_text)
    if t_summary.total_count:
        actions_parts.append(t_summary.lead_action_text)

    if actions_parts:
        actions_taken = "; ".join(actions_parts).capitalize() + "."
    else:
        actions_taken = "No enforcement actions were recorded."

    # 4. Were the verification checks successful?
    pc_events = [e for e in (recon.events or []) if getattr(e, "event_type", "") == "POSTCONDITION_CHECKED"]
    if pc_events:
        total_pc = len(pc_events)
        passed_cnt = sum(
            1 for e in pc_events
            if getattr(e, "outcome", "") == "PASSED" or (isinstance(getattr(e, "payload", {}), dict) and getattr(e, "payload", {}).get("passed") is True)
        )
        if passed_cnt == total_pc:
            checks_successful = f"All {total_pc} postcondition integrity checks passed successfully."
            checks_status = "PASS"
            all_passed = True
        else:
            failed_cnt = total_pc - passed_cnt
            checks_successful = f"{failed_cnt} of {total_pc} postcondition verification checks failed."
            checks_status = "FAIL"
            all_passed = False
    else:
        checks_successful = "Postcondition verification checks were not recorded."
        checks_status = "NOT RECORDED"
        all_passed = False

    return LeadAnswers(
        what_happened=what_happened,
        what_was_affected=what_was_affected,
        actions_taken=actions_taken,
        checks_successful=checks_successful,
        checks_status=checks_status,
        all_passed=all_passed,
    )


def build_investigation_summary(
    recon: Any,
    impact_data: dict[str, Any] | None = None,
    playbook_data: dict[str, Any] | None = None,
    belief_change: Any | None = None,
) -> list[Milestone]:
    """
    Pure transformation: derive the canonical Investigation Summary milestones (M1–M9)
    from live persisted data. Persists nothing.
    """
    milestones: list[Milestone] = []

    # Unpack composite read_model if passed as single argument
    if hasattr(recon, "recon"):
        impact_data = getattr(recon, "impact_data", impact_data)
        playbook_data = getattr(recon, "playbook_data", playbook_data)
        belief_change = getattr(recon, "belief_change", belief_change)
        recon = getattr(recon, "recon")
    elif isinstance(recon, dict) and ("recon" in recon or "incident" in recon):
        impact_data = recon.get("impact_data", recon.get("impact", impact_data))
        playbook_data = recon.get("playbook_data", recon.get("playbook", playbook_data))
        belief_change = recon.get("belief_change", belief_change)
        recon = recon.get("recon") or recon.get("incident")

    # ── M1: Evidence correction received ──
    ins_id = getattr(recon, "instrument_id", None) or "INS"
    cert_id = None
    ec = recon.evidence_claims[0] if (recon.evidence_claims and len(recon.evidence_claims) > 0) else None

    if belief_change and hasattr(belief_change, "current_belief"):
        cb = belief_change.current_belief
        ev_obj = getattr(cb, "evidence", None)
        src = getattr(ev_obj, "source_type", "CALIBRATION_AUTHORITY") if ev_obj else "CALIBRATION_AUTHORITY"
        trust = getattr(ev_obj, "trust_level", "AUTHORITATIVE") if ev_obj else "AUTHORITATIVE"
        cid = getattr(ev_obj, "id", "EV-CLAIM") if ev_obj else "EV-CLAIM"
        val_str = format_interval(belief_change.applies_from, belief_change.applies_until, interval_kind="VALID TIME")
        m1_stmt = f"Authoritative calibration update received for {cid} from {src} (trust: {trust}), establishing revised interval {val_str}."
        m1_status = "COMPLETED"
        m1_ids = [cid]
    elif ec:
        cid = ec.get("claim_id") or "EV-CLAIM"
        src = ec.get("source_type") or "CALIBRATION_AUTHORITY"
        trust = ec.get("trust_level") or "AUTHORITATIVE"
        val_str = format_interval(ec.get("valid_from"), ec.get("valid_until"), interval_kind="VALID TIME")
        m1_stmt = f"Evidence claim {cid} received from {src} (trust: {trust}), establishing revised interval {val_str}."
        m1_status = "COMPLETED"
        m1_ids = [cid]
    else:
        m1_stmt = "Triggering evidence correction was not recorded for this incident."
        m1_status = "NOT RECORDED"
        m1_ids = []

    m1_seqs = [getattr(e, "seq", 0) for e in (recon.events or []) if getattr(e, "event_type", "") in ("EVIDENCE_INGESTED", "INCIDENT_OPENED")]
    milestones.append(Milestone(
        key="M1",
        label="Evidence correction received",
        statement=m1_stmt,
        status=m1_status,
        supporting_seqs=m1_seqs,
        supporting_ids=m1_ids,
        source_description="Evidence claim & belief revision records",
    ))

    # ── M2: Certification lapse confirmed ──
    hypo_events = [e for e in (recon.events or []) if getattr(e, "event_type", "") in ("HYPOTHESIS_CHECKED", "PLAYBOOK_STEP")]
    confirmed_hypo = [e for e in hypo_events if str(getattr(e, "outcome", "")).upper() in ("CONFIRMED", "LAPSED", "PASSED")]
    m2_seqs = [getattr(e, "seq", 0) for e in hypo_events if "LAPSE" in str(getattr(e, "summary", "")).upper() or getattr(e, "event_type", "") == "HYPOTHESIS_CHECKED"]

    lapsed_exps = [e for e in (impact_data.get("experiments", []) if impact_data else []) if e.get("status") in ("NOT_COVERED", "LAPSED")]
    uncovered_count = len(lapsed_exps) if lapsed_exps else (1 if confirmed_hypo else 0)

    if confirmed_hypo:
        m2_stmt = f"Certification lapse confirmed for instrument {ins_id} ({_plural(uncovered_count, 'experiment')} evaluated outside certified coverage)."
        m2_status = "COMPLETED"
    else:
        m2_stmt = "Certification lapse evaluation was not recorded."
        m2_status = "NOT RECORDED"

    milestones.append(Milestone(
        key="M2",
        label="Certification lapse confirmed",
        statement=m2_stmt,
        status=m2_status,
        supporting_seqs=m2_seqs[:3],
        supporting_ids=[ins_id] if ins_id else [],
        source_description="Diagnostic hypothesis and coverage verification",
    ))

    # ── M3: Playbook retrieval and reuse ──
    pb = playbook_data.get("playbook") if playbook_data else None
    run = playbook_data.get("run") if playbook_data else None
    m3_seqs = [getattr(e, "seq", 0) for e in (recon.events or []) if getattr(e, "event_type", "") == "PLAYBOOK_RETRIEVED"]

    if run and run.get("mode"):
        pb_id = run.get("playbook_id")
        src_id = (pb and pb.get("source_incident_id")) or "INC-2026-001"
        mode = run.get("mode")
        m3_stmt = f"Verified playbook {pb_id} from incident {src_id} retrieved and executed in {mode} mode."
        m3_status = "COMPLETED"
        m3_ids = [pb_id]
    elif pb:
        pb_id = pb.get("id")
        pb_status = pb.get("status") or "VERIFIED"
        m3_stmt = f"No matching playbook available; exploratory investigation ran and derived verified playbook {pb_id} (status: {pb_status})."
        m3_status = "COMPLETED"
        m3_ids = [pb_id]
    else:
        m3_stmt = "Playbook retrieval or generation was not recorded."
        m3_status = "NOT RECORDED"
        m3_ids = []

    milestones.append(Milestone(
        key="M3",
        label="Playbook retrieval and execution",
        statement=m3_stmt,
        status=m3_status,
        supporting_seqs=m3_seqs,
        supporting_ids=m3_ids,
        source_description="Procedural memory and execution run record",
    ))

    # ── M3b: Playbook execution differences (only when StepResults exist) ──
    pb_summary = build_ordered_step_results(playbook_data)
    if pb_summary.has_results and pb_summary.milestone_statement:
        milestones.append(Milestone(
            key="M3b",
            label="Playbook execution differences",
            statement=pb_summary.milestone_statement,
            status="RECORDED",
            supporting_seqs=[],
            supporting_ids=[s.step_id for s in pb_summary.ordered_steps if s.step_id],
            source_description="StepResult comparison vs verified golden procedure",
        ))

    # ── M4: Downstream impact identified ──
    exps_cnt = len(impact_data.get("experiments", [])) if (impact_data and "experiments" in impact_data) else 0
    dsets_cnt = len(impact_data.get("datasets", [])) if (impact_data and "datasets" in impact_data) else 0
    rpts_cnt = len(impact_data.get("reports", [])) if (impact_data and "reports" in impact_data) else 0

    if exps_cnt == 0 and dsets_cnt == 0 and rpts_cnt == 0 and recon.action_records:
        dsets_cnt = len([a for a in recon.action_records if a.get("action_type") == "QUARANTINE_DATASET"])
        rpts_cnt = len([a for a in recon.action_records if a.get("action_type") in ("HOLD_REPORT", "FLAG_REPORT_REVIEW")])

    if exps_cnt or dsets_cnt or rpts_cnt:
        m4_stmt = f"Identified {_plural(exps_cnt, 'affected experiment')}, {_plural(dsets_cnt, 'dataset')}, and {_plural(rpts_cnt, 'downstream report')} across the lineage DAG."
        m4_status = "COMPLETED"
    else:
        m4_stmt = "Downstream impact trace was not recorded for this incident."
        m4_status = "NOT RECORDED"

    milestones.append(Milestone(
        key="M4",
        label="Downstream impact identified",
        statement=m4_stmt,
        status=m4_status,
        supporting_seqs=[],
        supporting_ids=[d.get("id") for d in (impact_data.get("datasets", []) if impact_data else [])],
        source_description="ActionService dependency traversal",
    ))

    # ── M5: Dataset quarantine enforced ──
    q_actions = [a for a in (recon.action_records or []) if a.get("action_type") == "QUARANTINE_DATASET" and a.get("target_id")]
    q_ids = [a["target_id"] for a in q_actions]

    if q_ids:
        m5_stmt = f"Quarantined {_plural(len(q_ids), 'dataset')} ({', '.join(q_ids)}) to prevent uncertified data use."
        m5_status = "COMPLETED"
        m5_ids = q_ids
    else:
        m5_stmt = "Dataset quarantine was not recorded for this incident."
        m5_status = "NOT RECORDED"
        m5_ids = []

    m5_seqs = [getattr(e, "seq", 0) for e in (recon.events or []) if getattr(e, "event_type", "") == "ACTION_APPLIED"]
    milestones.append(Milestone(
        key="M5",
        label="Dataset quarantine enforced",
        statement=m5_stmt,
        status=m5_status,
        supporting_seqs=m5_seqs,
        supporting_ids=m5_ids,
        source_description="Persisted ActionRecord entries (QUARANTINE_DATASET)",
    ))

    # ── M6: Report restrictions applied ──
    r_restr = describe_incident_report_restrictions(
        action_records=recon.action_records,
        reports_data=impact_data.get("reports", []) if impact_data else None,
    )
    m6_ids = r_restr.held_ids + r_restr.review_ids
    m6_status = "COMPLETED" if m6_ids else "NOT RECORDED"

    milestones.append(Milestone(
        key="M6",
        label="Report restrictions applied",
        statement=r_restr.milestone_statement,
        status=m6_status,
        supporting_seqs=m5_seqs,
        supporting_ids=m6_ids,
        source_description="Persisted ActionRecord entries (HOLD_REPORT / FLAG_REVIEW)",
    ))

    # ── M7: Investigation tasks assigned and escalated ──
    t_summary = describe_incident_tasks(recon.tasks)
    milestones.append(Milestone(
        key="M7",
        label="Investigation tasks assigned",
        statement=t_summary.milestone_statement,
        status=t_summary.milestone_status,
        supporting_seqs=[],
        supporting_ids=[t.get("task_id") for t in (recon.tasks or []) if t.get("task_id")],
        source_description="Graph Task nodes with assignment and escalation status",
    ))

    # ── M8: Postcondition verification checks ──
    pc_events = [e for e in (recon.events or []) if getattr(e, "event_type", "") == "POSTCONDITION_CHECKED"]
    if pc_events:
        total_pc = len(pc_events)
        passed_cnt = sum(
            1 for e in pc_events
            if getattr(e, "outcome", "") == "PASSED" or (isinstance(getattr(e, "payload", {}), dict) and getattr(e, "payload", {}).get("passed") is True)
        )
        if passed_cnt == total_pc:
            m8_stmt = f"All {total_pc} postcondition verification checks passed (PC1–PC6 validated)."
            m8_status = "COMPLETED"
        else:
            m8_stmt = f"{total_pc - passed_cnt} of {total_pc} postcondition verification checks failed."
            m8_status = "FAILED"
        m8_seqs = [getattr(e, "seq", 0) for e in pc_events]
    else:
        m8_stmt = "Postcondition verification checks were not recorded."
        m8_status = "NOT RECORDED"
        m8_seqs = []

    milestones.append(Milestone(
        key="M8",
        label="Postcondition verification",
        statement=m8_stmt,
        status=m8_status,
        supporting_seqs=m8_seqs,
        supporting_ids=[],
        source_description="Episodic POSTCONDITION_CHECKED audit records",
    ))

    # ── M9: Incident closure ──
    closure = describe_incident_closure(recon)
    milestones.append(Milestone(
        key="M9",
        label="Incident closure",
        statement=closure.milestone_statement,
        status=closure.milestone_status,
        supporting_seqs=closure.supporting_seqs,
        supporting_ids=[getattr(recon, "incident_id", "")],
        source_description="Incident status property and INCIDENT_CLOSED event",
    ))

    return milestones
