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
from dataclasses import dataclass
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
    CoverageStatus.COVERED_SINGLE.value: StatusBadge("COVERED (single)", "success", "#5E9E7A"),
    CoverageStatus.COVERED_CHAIN.value: StatusBadge("COVERED (chain)", "success", "#5E9E7A"),
    "COVERED": StatusBadge("COVERED", "success", "#5E9E7A"),
    CoverageStatus.NOT_COVERED.value: StatusBadge("NOT_COVERED", "danger", "#E8553D"),
    CoverageStatus.UNKNOWN.value: StatusBadge("UNKNOWN", "warning", "#E09F3E"),
    CoverageStatus.CONFLICTING.value: StatusBadge("CONFLICTING", "danger", "#E8553D"),

    # GateDecision
    GateDecision.ALLOW.value: StatusBadge("ALLOW", "success", "#5E9E7A"),
    GateDecision.BLOCK.value: StatusBadge("BLOCK", "danger", "#E8553D"),
    GateDecision.REVIEW_REQUIRED.value: StatusBadge("REVIEW_REQUIRED", "warning", "#E09F3E"),

    # Dataset Status
    "ACTIVE": StatusBadge("ACTIVE", "neutral", "#8A9099"),
    "QUARANTINED": StatusBadge("QUARANTINED", "danger", "#E8553D"),

    # Report publication_status
    "DRAFT": StatusBadge("DRAFT", "neutral", "#8A9099"),
    "PUBLISHED": StatusBadge("PUBLISHED", "success", "#5E9E7A"),
    "HELD": StatusBadge("HELD", "danger", "#E8553D"),
    "REQUIRES_REVIEW": StatusBadge("REQUIRES_REVIEW", "warning", "#E09F3E"),

    # Task Status
    "OPEN": StatusBadge("OPEN", "warning", "#E09F3E"),
    "ASSIGNED": StatusBadge("ASSIGNED", "neutral", "#8A9099"),
    "ESCALATED": StatusBadge("ESCALATED", "danger", "#E8553D"),
    "COMPLETED": StatusBadge("COMPLETED", "success", "#5E9E7A"),

    # Playbook Status
    "CANDIDATE": StatusBadge("CANDIDATE", "warning", "#E09F3E"),
    "VERIFIED": StatusBadge("VERIFIED", "success", "#5E9E7A"),
    "DEPRECATED": StatusBadge("DEPRECATED", "neutral", "#8A9099"),

    # StepStatus
    StepStatus.PASSED.value: StatusBadge("PASSED", "success", "#5E9E7A"),
    StepStatus.DIFFERED.value: StatusBadge("DIFFERED", "warning", "#E09F3E"),
    StepStatus.FAILED.value: StatusBadge("FAILED", "danger", "#E8553D"),
    StepStatus.SKIPPED.value: StatusBadge("SKIPPED", "neutral", "#8A9099"),

    # Incident Status
    "IN_PROGRESS": StatusBadge("IN_PROGRESS", "warning", "#E09F3E"),
    "CLOSED": StatusBadge("CLOSED", "success", "#5E9E7A"),

    # Handoff Status
    "PENDING": StatusBadge("PENDING", "neutral", "#8A9099"),
    "CLAIMED": StatusBadge("CLAIMED", "warning", "#E09F3E"),
    "EXPIRED": StatusBadge("EXPIRED", "danger", "#E8553D"),
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
            f'fillcolor="#151719", color="#4A5059", fontcolor="#E6E8EA"];'
        )

    # Experiment nodes
    for exp in experiments:
        eid = exp.get("id") or exp.get("experiment_id") or "UNKNOWN_EXP"
        status = exp.get("status") or "UNKNOWN"
        is_lapsed = status in ("NOT_COVERED", "CONFLICTING")
        color = "#E8553D" if is_lapsed else "#5E9E7A"
        safe_id = escape(eid)
        safe_status = escape(status)

        lines.append(
            f'    "{safe_id}" ['
            f'label="{safe_id}\\n[{safe_status}]", '
            f'fillcolor="#151719", color="{color}", fontcolor="{color}"];'
        )
        if instrument_id:
            safe_inst = escape(instrument_id)
            lines.append(f'    "{safe_inst}" -> "{safe_id}" [label="USAGE"];')

    # Dataset nodes
    for ds in datasets:
        did = ds.get("id") or "UNKNOWN_DS"
        status = ds.get("status") or ds.get("current_status") or "ACTIVE"
        is_quarantined = status == "QUARANTINED"
        color = "#E8553D" if is_quarantined else "#8A9099"
        safe_id = escape(did)
        safe_status = escape(status)

        lines.append(
            f'    "{safe_id}" ['
            f'label="{safe_id}\\n[{safe_status}]", '
            f'fillcolor="#151719", color="{color}", fontcolor="{color}"];'
        )

        # Draw dependency path edges if provided
        path = ds.get("dependency_path") or []
        for i, elem in enumerate(path):
            if elem == "PRODUCED" and i > 0 and i + 1 < len(path):
                from_n, to_n = escape(path[i - 1]), escape(path[i + 1])
                lines.append(f'    "{from_n}" -> "{to_n}" [label="PRODUCED", color="#E8553D"];')
            elif elem == "DERIVED_FROM" and i > 0 and i + 1 < len(path):
                # In graph, derived points from child to parent, so visually parent -> child
                parent_n, child_n = escape(path[i - 1]), escape(path[i + 1])
                lines.append(f'    "{parent_n}" -> "{child_n}" [label="DERIVED", color="#E8553D"];')

    # Report nodes
    for rpt in reports:
        rid = rpt.get("id") or "UNKNOWN_RPT"
        status = rpt.get("status") or rpt.get("current_status") or "DRAFT"
        color = "#E8553D" if status in ("HELD", "REQUIRES_REVIEW") else "#5E9E7A"
        safe_id = escape(rid)
        safe_status = escape(status)

        lines.append(
            f'    "{safe_id}" ['
            f'label="{safe_id}\\n[{safe_status}]", '
            f'fillcolor="#151719", color="{color}", fontcolor="{color}", style="dashed,filled"];'
        )

        path = rpt.get("dependency_path") or []
        for i, elem in enumerate(path):
            if elem == "USES_DATASET" and i > 0 and i + 1 < len(path):
                from_ds, to_rpt = escape(path[i - 1]), escape(path[i + 1])
                lines.append(f'    "{from_ds}" -> "{to_rpt}" [label="USES", style="dashed", color="#E8553D"];')

    lines.append('}')
    return '\n'.join(lines)
