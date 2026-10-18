"""
temproven/app/timeline.py
─────────────────────────
Pure domain & presentation data model and chart builder for the
Interactive Point-Based Event Timeline.

Key Invariants:
- FalkorDB is the sole source of truth.
- Sequence authority: events are sorted strictly by `seq` ONLY.
- Timestamps never affect order or chart x/y positioning.
- Category mapping extends the canonical journey mapping as a single source of truth.
- Tooltips, inspector, and chart encodings are strictly typed and injection-safe.
- Zero Streamlit or Database imports in pure transformation functions.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import altair as alt
import pandas as pd

from temproven.app.journey import (
    DIRECT_EVENT_STAGE_MAP,
    PLAYBOOK_STEP_STAGE_MAP,
    HANDOFF_TASK_STAGE_MAP,
    DECISION_STAGE_MAP,
)
from temproven.app.presenters import escape, format_timestamp, format_epoch_ist


# ── Canonical Display Categories ──────────────────────────────────────────────

CANONICAL_TIMELINE_CATEGORIES: list[tuple[str, str, str]] = [
    ("evidence_received", "Evidence received or corrected", "#00D2B4"),  # Cyan
    ("temporal_diagnosis", "Temporal diagnosis and hypothesis evaluation", "#F59E0B"),  # Amber
    ("playbook_reuse", "Playbook retrieval and execution", "#818CF8"),  # Indigo
    ("impact_tracing", "Impact tracing", "#38BDF8"),  # Sky
    ("quarantine_and_tasks", "Quarantine, holds and task actions", "#F95738"),  # Coral
    ("postcondition_verification", "Postcondition verification and revalidation", "#22C55E"),  # Emerald
    ("incident_closure", "Incident closure", "#A78BFA"),  # Purple
    ("routine_queries", "Routine queries", "#6B7280"),  # Slate grey
    ("unclassified", "Unclassified", "#9CA3AF"),  # Muted grey
]

CATEGORY_LABELS: list[str] = [label for _, label, _ in CANONICAL_TIMELINE_CATEGORIES]
CATEGORY_KEY_TO_LABEL: dict[str, str] = {k: label for k, label, _ in CANONICAL_TIMELINE_CATEGORIES}
CATEGORY_LABEL_TO_COLOR: dict[str, str] = {label: color for _, label, color in CANONICAL_TIMELINE_CATEGORIES}

# Stage key to display category label
STAGE_KEY_TO_CATEGORY_LABEL: dict[str, str] = {
    "evidence_received": "Evidence received or corrected",
    "temporal_diagnosis": "Temporal diagnosis and hypothesis evaluation",
    "playbook_reuse": "Playbook retrieval and execution",
    "impact_tracing": "Impact tracing",
    "quarantine_and_tasks": "Quarantine, holds and task actions",
    "postcondition_verification": "Postcondition verification and revalidation",
    "incident_closure": "Incident closure",
}


# ── Significance Classification ──────────────────────────────────────────────

SIGNIFICANT_EVENT_TYPES = {
    "INCIDENT_OPENED",
    "EVIDENCE_INGESTED",
    "PLAYBOOK_RETRIEVED",
    "ACTION_APPLIED",
    "POSTCONDITION_CHECKED",
    "DECISION",
    "HYPOTHESIS_CHECKED",
    "INCIDENT_CLOSED",
}


def classify_significance(ev: Any) -> str:
    """
    Deterministically classify an event as SIGNIFICANT or ROUTINE.
    - SIGNIFICANT: opens/closes, evidence ingested, actions applied, postcondition checks,
      decisions, hypothesis checks, and any playbook step with detected differences/failures.
    - ROUTINE: QUERY_EXECUTED, handoff creation/completion, routine observations.
    """
    ev_type = getattr(ev, "event_type", "") or ""
    outcome = (getattr(ev, "outcome", None) or "").upper()
    payload = getattr(ev, "payload", {}) or {}

    # Explicit failure or difference flags make an event significant
    if outcome in ("FAILED", "LAPSED", "DIFFERED", "MISMATCH"):
        return "SIGNIFICANT"
    if payload.get("differences"):
        return "SIGNIFICANT"
    if payload.get("passed") is False:
        return "SIGNIFICANT"

    # Routine types
    if ev_type == "QUERY_EXECUTED":
        return "ROUTINE"
    if ev_type in ("HANDOFF_CREATED", "HANDOFF_COMPLETED", "OBSERVATION"):
        return "ROUTINE"

    if ev_type in SIGNIFICANT_EVENT_TYPES:
        return "SIGNIFICANT"

    # Playbook step without differences is routine execution
    if ev_type == "PLAYBOOK_STEP":
        return "ROUTINE"

    return "ROUTINE"


# ── Category Classification ──────────────────────────────────────────────────

def map_event_to_category(ev: Any, handoff_task_types: dict[str, str] | None = None) -> tuple[str, str]:
    """
    Map an event to (category_key, category_label).
    Extends the canonical journey mapping with explicit handling for QUERY_EXECUTED and Unclassified.
    """
    ev_type = getattr(ev, "event_type", "") or ""
    payload = getattr(ev, "payload", {}) or {}

    # 1. Routine queries
    if ev_type == "QUERY_EXECUTED":
        return "routine_queries", "Routine queries"

    # 2. Playbook step sub-key mapping
    if ev_type == "PLAYBOOK_STEP":
        step_type = payload.get("step_type") or ""
        stg_key = PLAYBOOK_STEP_STAGE_MAP.get(step_type)
        if stg_key and stg_key in STAGE_KEY_TO_CATEGORY_LABEL:
            return stg_key, STAGE_KEY_TO_CATEGORY_LABEL[stg_key]

    # 3. Handoff sub-key mapping
    if ev_type in ("HANDOFF_CREATED", "HANDOFF_COMPLETED"):
        h_id = payload.get("handoff_id") or ""
        task_type = (handoff_task_types or {}).get(h_id) or payload.get("task_type") or ""
        stg_key = HANDOFF_TASK_STAGE_MAP.get(task_type)
        if stg_key and stg_key in STAGE_KEY_TO_CATEGORY_LABEL:
            return stg_key, STAGE_KEY_TO_CATEGORY_LABEL[stg_key]

    # 4. Decision sub-key mapping
    if ev_type == "DECISION":
        role = payload.get("role") or ""
        stg_key = DECISION_STAGE_MAP.get(role)
        if stg_key and stg_key in STAGE_KEY_TO_CATEGORY_LABEL:
            return stg_key, STAGE_KEY_TO_CATEGORY_LABEL[stg_key]

    # 5. Direct event type mapping
    stg_key = DIRECT_EVENT_STAGE_MAP.get(ev_type)
    if stg_key and stg_key in STAGE_KEY_TO_CATEGORY_LABEL:
        return stg_key, STAGE_KEY_TO_CATEGORY_LABEL[stg_key]

    # 6. Fallback: Unclassified
    return "unclassified", "Unclassified"


# ── Timeline Data Classes ─────────────────────────────────────────────────────

@dataclass
class TimelinePoint:
    seq: int
    event_id: str
    event_type: str
    category_key: str
    category: str
    significance: str  # "SIGNIFICANT" or "ROUTINE"
    actor: str
    timestamp_epoch: int
    timestamp_utc: str
    timestamp_ist: str
    summary: str
    tooltip_summary: str
    outcome: str
    evidence_ids: list[str] = field(default_factory=list)
    action_ids: list[str] = field(default_factory=list)
    target_id: str = "n/a"
    step_type: str = "n/a"
    task_type: str = "n/a"
    payload: dict[str, Any] = field(default_factory=dict)
    is_selected: bool = False
    is_visible: bool = True
    shape: str = "circle"  # "diamond" for significant, "circle" for routine
    color: str = "#9CA3AF"


@dataclass
class TimelineView:
    points: list[TimelinePoint]
    visible_points: list[TimelinePoint]
    all_categories: list[str]
    active_categories: list[str]
    category_counts: dict[str, int]
    visible_category_counts: dict[str, int]
    total_count: int
    visible_count: int
    hidden_count: int
    hidden_categories: list[str]
    filter_banner: str
    selected_point: TimelinePoint | None = None
    selected_seq: int | None = None
    is_selected_hidden: bool = False
    has_sequence_anomaly: bool = False


# ── Pure Data Transformation Functions ────────────────────────────────────────

def format_epoch_utc(epoch_sec: int | None) -> str:
    """Format epoch seconds to readable UTC string."""
    if epoch_sec is None or epoch_sec == 0:
        return "n/a"
    try:
        dt = datetime.fromtimestamp(epoch_sec, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return "n/a"



def truncate_summary_for_tooltip(summary: str, max_chars: int = 120) -> str:
    """Safely cap summary at max_chars with an ellipsis for tooltips."""
    if not summary:
        return "n/a"
    cleaned = " ".join(summary.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars - 3].rstrip() + "..."


def build_timeline_points(
    events: list[Any],
    active_categories: list[str] | None = None,
    selected_seq: int | None = None,
    handoff_task_types: dict[str, str] | None = None,
) -> TimelineView:
    """
    Pure transformation: convert raw Event records into TimelineView.
    - Sorts by `seq` ONLY (with tie-break by id and anomaly flagging).
    - Categorizes using the unified mapping table.
    - Classifies significance (SIGNIFICANT vs ROUTINE).
    - Applies category filters without mutating source events.
    - Builds safe, graceful tooltip strings.
    """
    if not events:
        return TimelineView(
            points=[],
            visible_points=[],
            all_categories=CATEGORY_LABELS,
            active_categories=active_categories or CATEGORY_LABELS,
            category_counts={cat: 0 for cat in CATEGORY_LABELS},
            visible_category_counts={cat: 0 for cat in CATEGORY_LABELS},
            total_count=0,
            visible_count=0,
            hidden_count=0,
            hidden_categories=[],
            filter_banner="",
            selected_point=None,
            selected_seq=None,
            is_selected_hidden=False,
            has_sequence_anomaly=False,
        )

    # 1. Deterministic sort by seq ONLY, breaking ties by event id
    has_anomaly = False
    seen_seqs: set[int] = set()

    def sort_key(ev: Any) -> tuple[int, str]:
        s = getattr(ev, "seq", 0) or 0
        i = getattr(ev, "id", "") or ""
        return (s, str(i))

    sorted_events = sorted(events, key=sort_key)

    # Detect duplicates or non-monotonic gaps
    for ev in sorted_events:
        s = getattr(ev, "seq", 0) or 0
        if s in seen_seqs:
            has_anomaly = True
        seen_seqs.add(s)

    # 2. Build TimelinePoint for each event
    points: list[TimelinePoint] = []
    category_counts = {cat: 0 for cat in CATEGORY_LABELS}

    for ev in sorted_events:
        seq_num = getattr(ev, "seq", 0) or 0
        ev_id = getattr(ev, "id", "") or "n/a"
        ev_type = getattr(ev, "event_type", "") or "UNKNOWN"
        raw_at = getattr(ev, "at", None)
        actor = getattr(ev, "actor_agent", None) or getattr(ev, "actor", None) or "system"
        summary = getattr(ev, "summary", "") or ""
        raw_outcome = getattr(ev, "outcome", None)
        outcome_str = raw_outcome if raw_outcome is not None and str(raw_outcome).strip() else "n/a"
        ev_ids = [str(x) for x in (getattr(ev, "evidence_ids", []) or []) if x is not None]
        ar_ids = [str(x) for x in (getattr(ev, "action_ids", []) or []) if x is not None]
        payload = getattr(ev, "payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}

        # Whitelisted payload extraction
        target_id = payload.get("target_id") or payload.get("instrument_id") or payload.get("claim_id") or "n/a"
        step_type = payload.get("step_type") or "n/a"
        task_type = payload.get("task_type") or (handoff_task_types or {}).get(payload.get("handoff_id", ""), "n/a")

        cat_key, cat_label = map_event_to_category(ev, handoff_task_types)
        significance = classify_significance(ev)
        shape = "diamond" if significance == "SIGNIFICANT" else "circle"
        color = CATEGORY_LABEL_TO_COLOR.get(cat_label, "#9CA3AF")

        category_counts[cat_label] = category_counts.get(cat_label, 0) + 1

        point = TimelinePoint(
            seq=seq_num,
            event_id=ev_id,
            event_type=ev_type,
            category_key=cat_key,
            category=cat_label,
            significance=significance,
            actor=str(actor),
            timestamp_epoch=raw_at or 0,
            timestamp_utc=format_epoch_utc(raw_at),
            timestamp_ist=format_epoch_ist(raw_at),
            summary=summary,
            tooltip_summary=truncate_summary_for_tooltip(summary),
            outcome=str(outcome_str),
            evidence_ids=ev_ids,
            action_ids=ar_ids,
            target_id=str(target_id),
            step_type=str(step_type),
            task_type=str(task_type),
            payload=payload,
            is_selected=(selected_seq is not None and seq_num == selected_seq),
            is_visible=True,
            shape=shape,
            color=color,
        )
        points.append(point)

    # 3. Apply active category filters
    eff_active = active_categories if active_categories is not None else CATEGORY_LABELS
    active_set = set(eff_active)

    visible_points: list[TimelinePoint] = []
    visible_counts = {cat: 0 for cat in CATEGORY_LABELS}

    for p in points:
        p.is_visible = (p.category in active_set)
        if p.is_visible:
            visible_points.append(p)
            visible_counts[p.category] = visible_counts.get(p.category, 0) + 1

    total_cnt = len(points)
    vis_cnt = len(visible_points)
    hidden_cnt = total_cnt - vis_cnt

    hidden_cats = [c for c in CATEGORY_LABELS if c not in active_set and category_counts.get(c, 0) > 0]
    hidden_details = f" ({', '.join(hidden_cats)})" if hidden_cats else ""

    if hidden_cnt > 0:
        filter_banner = f"Showing {vis_cnt} of {total_cnt} events; {hidden_cnt} hidden by filter{hidden_details}. Gaps on axis indicate filtered events."
    else:
        filter_banner = f"Showing all {total_cnt} recorded events across {len(active_set)} categories."

    # 4. Resolve selected point and whether selected event is hidden
    sel_point = next((p for p in points if p.seq == selected_seq), None)
    is_sel_hidden = bool(sel_point and not sel_point.is_visible)

    return TimelineView(
        points=points,
        visible_points=visible_points,
        all_categories=CATEGORY_LABELS,
        active_categories=eff_active,
        category_counts=category_counts,
        visible_category_counts=visible_counts,
        total_count=total_cnt,
        visible_count=vis_cnt,
        hidden_count=hidden_cnt,
        hidden_categories=hidden_cats,
        filter_banner=filter_banner,
        selected_point=sel_point,
        selected_seq=selected_seq,
        is_selected_hidden=is_sel_hidden,
        has_sequence_anomaly=has_anomaly,
    )


def resolve_selected_seq(
    chart_selection_event: Any,
    events: list[Any],
    previous_selected_seq: int | None,
    incident_id: str,
) -> int | None:
    """
    Deterministically resolve the selected event sequence number:
    - Extracts `seq` from VegaLiteState selection parameter `pt_sel`.
    - Accepts seq ONLY if it exists in THIS incident's events (guards against stale cross-incident state).
    - If chart selection is empty, retains previous_selected_seq.
    - Retains selection even if currently hidden by filter (caller flags note in inspector).
    """
    valid_seqs = {getattr(e, "seq", 0) for e in events if getattr(e, "seq", None) is not None}
    if not valid_seqs:
        return None

    # Inspect chart selection event dictionary / attribute
    raw_pt = None
    if chart_selection_event is not None:
        sel_obj = getattr(chart_selection_event, "selection", None)
        if isinstance(sel_obj, dict) or hasattr(sel_obj, "get"):
            raw_pt = sel_obj.get("pt_sel")
        elif isinstance(chart_selection_event, dict):
            sel_dict = chart_selection_event.get("selection", {})
            if isinstance(sel_dict, dict) and "pt_sel" in sel_dict:
                raw_pt = sel_dict.get("pt_sel")
            else:
                raw_pt = chart_selection_event.get("pt_sel")

    extracted_seq: int | None = None
    if raw_pt:
        # Altair/Vega-Lite emits either [{'seq': 12}] or {'seq': [12]}
        if isinstance(raw_pt, list) and len(raw_pt) > 0:
            first_item = raw_pt[0]
            if isinstance(first_item, dict):
                extracted_seq = first_item.get("seq")
            elif isinstance(first_item, (int, float)):
                extracted_seq = int(first_item)
        elif isinstance(raw_pt, dict):
            val = raw_pt.get("seq")
            if isinstance(val, list) and len(val) > 0:
                extracted_seq = int(val[0])
            elif isinstance(val, (int, float)):
                extracted_seq = int(val)

    if extracted_seq is not None and extracted_seq in valid_seqs:
        return extracted_seq

    # Fallback to previous selection if valid for this incident
    if previous_selected_seq is not None and previous_selected_seq in valid_seqs:
        return previous_selected_seq

    # Default to first available seq
    sorted_seqs = sorted(list(valid_seqs))
    return sorted_seqs[0] if sorted_seqs else None


# ── Chart Builder ─────────────────────────────────────────────────────────────

def build_timeline_chart(
    view: TimelineView,
    orientation: str = "horizontal",
    chart_key: str = "timeline_chart",
) -> alt.Chart:
    """
    Construct a Vega-Altair chart specification for the event timeline:
    - X-axis strictly encodes `seq:Q` (tickMinStep=1, format='d').
    - Y-axis encodes `category:N` with canonical domain order.
    - Connects visible events with a thin sequence line.
    - Distinct marks: diamond for SIGNIFICANT, circle for ROUTINE.
    - Outer highlight ring for selected point based on `is_selected`.
    - Fully populated tooltips for hover inspection.
    - Altair point selection parameter `pt_sel` on field `seq`.
    """
    # Create DataFrame from visible points
    records = []
    for p in view.visible_points:
        records.append({
            "seq": p.seq,
            "category": p.category,
            "significance": p.significance,
            "event_type": p.event_type,
            "actor": p.actor,
            "timestamp_utc": p.timestamp_utc,
            "outcome": p.outcome,
            "target_id": p.target_id,
            "tooltip_summary": p.tooltip_summary,
            "shape": p.shape,
            "color": p.color,
            "is_selected": p.is_selected,
        })

    if not records:
        # Empty placeholder DataFrame to render axis cleanly
        df = pd.DataFrame(columns=[
            "seq", "category", "significance", "event_type", "actor",
            "timestamp_utc", "outcome", "target_id", "tooltip_summary",
            "shape", "color", "is_selected"
        ])
    else:
        df = pd.DataFrame(records)

    # Base chart
    base = alt.Chart(df)

    # Selection parameter for click-to-select
    point_sel = alt.selection_point(name="pt_sel", fields=["seq"], empty=False)

    # Canonical domain categories
    cat_domain = CATEGORY_LABELS
    cat_colors = [CATEGORY_LABEL_TO_COLOR[c] for c in cat_domain]

    is_vert = (orientation.lower() == "vertical")

    if not is_vert:
        # Horizontal layout: x = seq, y = category
        x_enc = alt.X(
            "seq:Q",
            axis=alt.Axis(
                tickMinStep=1,
                format="d",
                title="Event Sequence (seq)",
                titleColor="#9CA3AF",
                labelColor="#9CA3AF",
                gridColor="#1D2128",
                domainColor="#1D2128",
            )
        )
        y_enc = alt.Y(
            "category:N",
            scale=alt.Scale(domain=cat_domain),
            axis=alt.Axis(
                title=None,
                labelColor="#D1D5DB",
                gridColor="#181B22",
                domainColor="#1D2128",
                labelPadding=8,
            )
        )
    else:
        # Vertical layout: x = category, y = seq (running top to bottom)
        x_enc = alt.X(
            "category:N",
            scale=alt.Scale(domain=cat_domain),
            axis=alt.Axis(
                title=None,
                labelColor="#D1D5DB",
                gridColor="#181B22",
                domainColor="#1D2128",
                labelAngle=-45,
            )
        )
        y_enc = alt.Y(
            "seq:Q",
            sort="ascending",
            axis=alt.Axis(
                tickMinStep=1,
                format="d",
                title="Event Sequence (seq)",
                titleColor="#9CA3AF",
                labelColor="#9CA3AF",
                gridColor="#1D2128",
                domainColor="#1D2128",
            )
        )

    # Tooltips
    tooltips = [
        alt.Tooltip("seq:Q", title="Sequence"),
        alt.Tooltip("category:N", title="Category"),
        alt.Tooltip("event_type:N", title="Type"),
        alt.Tooltip("significance:N", title="Significance"),
        alt.Tooltip("tooltip_summary:N", title="Summary"),
        alt.Tooltip("actor:N", title="Actor"),
        alt.Tooltip("timestamp_utc:N", title="Recorded Time (UTC)"),
        alt.Tooltip("outcome:N", title="Outcome"),
        alt.Tooltip("target_id:N", title="Target"),
    ]

    # Layer 1: Sequence line connecting points in seq order
    line_layer = base.mark_line(
        color="#374151",
        strokeWidth=1.2,
        strokeDash=[3, 3],
        opacity=0.85
    ).encode(
        x=x_enc,
        y=y_enc,
        order=alt.Order("seq:Q")
    )

    # Layer 2: Routine Points (circles)
    routine_df = base.transform_filter(alt.datum.significance == "ROUTINE")
    routine_points = routine_df.mark_circle(
        size=75,
        opacity=0.9,
    ).encode(
        x=x_enc,
        y=y_enc,
        color=alt.Color("category:N", scale=alt.Scale(domain=cat_domain, range=cat_colors), legend=None),
        tooltip=tooltips,
    )

    # Layer 3: Significant Points (diamonds)
    sig_df = base.transform_filter(alt.datum.significance == "SIGNIFICANT")
    sig_points = sig_df.mark_point(
        shape="diamond",
        size=170,
        filled=True,
        opacity=1.0,
    ).encode(
        x=x_enc,
        y=y_enc,
        color=alt.Color("category:N", scale=alt.Scale(domain=cat_domain, range=cat_colors), legend=None),
        tooltip=tooltips,
    )

    # Layer 4: Selected Point Highlight Ring (cyan ring around selected seq)
    selected_df = base.transform_filter(alt.datum.is_selected == True)
    highlight_ring = selected_df.mark_point(
        shape="circle",
        size=360,
        stroke="#00D2B4",
        strokeWidth=2.6,
        filled=False,
        opacity=1.0,
    ).encode(
        x=x_enc,
        y=y_enc,
        tooltip=tooltips,
    )

    # Calculate height based on orientation and lane count
    chart_height = 360 if not is_vert else max(520, len(view.visible_points) * 14)

    chart = alt.layer(
        line_layer,
        routine_points,
        sig_points,
        highlight_ring,
    ).add_params(
        point_sel
    ).properties(
        height=chart_height,
        background="#0E1116",
    ).configure_view(
        stroke="#1D2128",
        fill="#0E1116",
    )

    return chart
