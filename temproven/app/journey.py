"""
temproven/app/journey.py
────────────────────────
Pure domain & presentation service for the Incident Journey Graph.
Converts an incident's linear episodic event history into canonical stages,
enforcing strict honesty rules:
- Canonical stages 1 to 7 (plus Unclassified if non-empty).
- Sequence authority: FOLLOWS sequence order (ev.seq) determines stage chronology.
- Interleaving detection: overlapping sequence windows are explicitly flagged.
- Edges: indicate strictly 'next in recorded sequence', NEVER causation.
- Status derivation: based ONLY on explicit persisted outcomes, never assumed.
- QUERY_EXECUTED: never a key event; linked to stage only via explicit payload references.
- Zero Streamlit or Database imports (pure, unit-testable).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from temproven.app.presenters import escape, format_timestamp


# ── Canonical Stage Definitions ───────────────────────────────────────────────

CANONICAL_STAGES = [
    ("evidence_received", "Evidence received"),
    ("temporal_diagnosis", "Temporal diagnosis"),
    ("playbook_reuse", "Playbook retrieval and reuse"),
    ("impact_tracing", "Impact tracing"),
    ("quarantine_and_tasks", "Quarantine, holds and tasks"),
    ("postcondition_verification", "Postcondition verification"),
    ("incident_closure", "Incident closure"),
]

CANONICAL_KEYS = [k for k, _ in CANONICAL_STAGES]


# ── Stage Mapping Rules Table ─────────────────────────────────────────────────

# Direct mapping by Event.event_type
DIRECT_EVENT_STAGE_MAP: dict[str, str] = {
    "INCIDENT_OPENED": "evidence_received",
    "EVIDENCE_INGESTED": "evidence_received",
    "OBSERVATION": "evidence_received",
    "HYPOTHESIS_CHECKED": "temporal_diagnosis",
    "PLAYBOOK_RETRIEVED": "playbook_reuse",
    "ACTION_APPLIED": "quarantine_and_tasks",
    "POSTCONDITION_CHECKED": "postcondition_verification",
    "INCIDENT_CLOSED": "incident_closure",
}

# Sub-key mappings for PLAYBOOK_STEP by payload.step_type
PLAYBOOK_STEP_STAGE_MAP: dict[str, str] = {
    "CONFIRM_LAPSE": "temporal_diagnosis",
    "VERIFY_PRECONDITIONS": "temporal_diagnosis",
    "CHECK_INCIDENT_SHAPE": "temporal_diagnosis",
    "TRACE_DEPENDENTS": "impact_tracing",
    "TRACE_IMPACT_LINEAGE": "impact_tracing",
    "PROPOSE_ACTIONS": "impact_tracing",
    "QUARANTINE_DATASETS": "quarantine_and_tasks",
    "HOLD_REPORTS": "quarantine_and_tasks",
    "CREATE_ASSIGN_TASKS": "quarantine_and_tasks",
    "VERIFY_GATE_BLOCKS": "postcondition_verification",
}

# Sub-key mappings for HANDOFF_CREATED / HANDOFF_COMPLETED by task_type
HANDOFF_TASK_STAGE_MAP: dict[str, str] = {
    "SELECT_PLAYBOOK": "playbook_reuse",
    "ASSESS_IMPACT": "impact_tracing",
    "VALIDATE_PROPOSALS": "quarantine_and_tasks",
    "APPLY_APPROVED": "quarantine_and_tasks",
    "VERIFY_AND_CLOSE": "postcondition_verification",
}

# Sub-key mappings for DECISION events
DECISION_STAGE_MAP: dict[str, str] = {
    "QUARANTINE_DATASET": "quarantine_and_tasks",
    "HOLD_REPORT": "quarantine_and_tasks",
    "ASSIGN_TASK": "quarantine_and_tasks",
}


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class JourneyStage:
    key: str
    label: str
    status: str  # "COMPLETED", "COMPLETED WITH DIFFERENCES", "FAILED", "RECORDED", "NOT RECORDED"
    event_count: int
    query_count: int
    first_seq: int | None = None
    last_seq: int | None = None
    first_time: int | None = None
    last_time: int | None = None
    actors: list[str] = field(default_factory=list)
    events: list[Any] = field(default_factory=list)
    supporting_queries: list[Any] = field(default_factory=list)
    interleaved: bool = False

    @property
    def is_present(self) -> bool:
        return self.event_count > 0


@dataclass
class Journey:
    stages: list[JourneyStage]
    present_stages: list[JourneyStage]
    absent_stages: list[JourneyStage]
    unclassified_stage: JourneyStage | None
    all_events_count: int
    key_events_count: int
    supporting_queries_count: int
    unlinked_queries_count: int
    unclassified_events_count: int
    edges: list[tuple[str, str]]
    unlinked_queries: list[Any] = field(default_factory=list)


# ── Payload & Event Helpers ──────────────────────────────────────────────────

def extract_payload(event: Any) -> dict[str, Any]:
    """Safely extract payload dictionary from an EventRecord or dict."""
    if hasattr(event, "payload") and isinstance(event.payload, dict):
        return event.payload
    if hasattr(event, "payload_json") and event.payload_json:
        try:
            return json.loads(event.payload_json)
        except Exception:
            return {}
    if isinstance(event, dict):
        p = event.get("payload")
        if isinstance(p, dict):
            return p
        pj = event.get("payload_json")
        if pj:
            try:
                return json.loads(pj)
            except Exception:
                return {}
    return {}


def get_event_field(event: Any, field_name: str, default: Any = None) -> Any:
    """Safely retrieve a field value from EventRecord or dictionary."""
    if hasattr(event, field_name):
        val = getattr(event, field_name)
        return val if val is not None else default
    if isinstance(event, dict):
        val = event.get(field_name)
        return val if val is not None else default
    return default


def map_event_to_stage(event: Any, handoff_task_types: dict[str, str] | None = None) -> str:
    """
    Deterministic mapping from an Event to a canonical stage key.
    Returns the stage key, or 'unclassified' if unmapped.
    Strictly follows the mapping table.
    """
    ev_type = str(get_event_field(event, "event_type", "")).strip()
    payload = extract_payload(event)

    # 1. Direct event_type matches
    if ev_type in DIRECT_EVENT_STAGE_MAP:
        return DIRECT_EVENT_STAGE_MAP[ev_type]

    # 2. PLAYBOOK_STEP by payload.step_type
    if ev_type == "PLAYBOOK_STEP":
        step_type = payload.get("step_type")
        if step_type and step_type in PLAYBOOK_STEP_STAGE_MAP:
            return PLAYBOOK_STEP_STAGE_MAP[step_type]
        return "unclassified"

    # 3. HANDOFF_CREATED / HANDOFF_COMPLETED by task_type
    if ev_type in ("HANDOFF_CREATED", "HANDOFF_COMPLETED"):
        task_type = payload.get("task_type")
        if not task_type and handoff_task_types:
            h_id = payload.get("handoff_id")
            if h_id:
                task_type = handoff_task_types.get(h_id)
        if task_type and task_type in HANDOFF_TASK_STAGE_MAP:
            return HANDOFF_TASK_STAGE_MAP[task_type]
        return "unclassified"

    # 4. DECISION events
    if ev_type == "DECISION":
        action_type = payload.get("action_type") or payload.get("step_type")
        if action_type and action_type in DECISION_STAGE_MAP:
            return DECISION_STAGE_MAP[action_type]
        # In our domain, decision payload often specifies role/target for quarantine/holds
        if payload.get("target_id") or payload.get("role"):
            return "quarantine_and_tasks"
        return "unclassified"

    # 5. OUTCOME events
    if ev_type == "OUTCOME":
        stage_hint = payload.get("stage")
        if stage_hint and stage_hint in CANONICAL_KEYS:
            return stage_hint
        return "unclassified"

    return "unclassified"


def link_query_to_stage(query_event: Any, handoff_task_types: dict[str, str] | None = None) -> str | None:
    """
    Rule 2.4: QUERY_EXECUTED is linked to a stage ONLY when its persisted payload
    explicitly identifies the step, hypothesis, or handoff that issued it.
    Never assign a query by chronological proximity.
    Returns stage key, or None if unlinked.
    """
    payload = extract_payload(query_event)

    # Step link
    step_type = payload.get("step_type")
    if step_type and step_type in PLAYBOOK_STEP_STAGE_MAP:
        return PLAYBOOK_STEP_STAGE_MAP[step_type]

    # Hypothesis link
    if "hypothesis" in payload:
        return "temporal_diagnosis"

    # Handoff link
    task_type = payload.get("task_type")
    if not task_type and handoff_task_types:
        h_id = payload.get("handoff_id")
        if h_id:
            task_type = handoff_task_types.get(h_id)
    if task_type and task_type in HANDOFF_TASK_STAGE_MAP:
        return HANDOFF_TASK_STAGE_MAP[task_type]

    return None


def derive_stage_status(stage_key: str, events: list[Any]) -> str:
    """
    Derive stage status strictly from explicit persisted outcomes.
    NEVER infer success from absence of failure.
    """
    if not events:
        return "NOT RECORDED"

    if stage_key == "incident_closure":
        has_closed = any(get_event_field(e, "event_type") == "INCIDENT_CLOSED" for e in events)
        return "COMPLETED" if has_closed else "RECORDED"

    has_failure = False
    has_differed = False
    has_explicit_success = False

    for e in events:
        outcome = get_event_field(e, "outcome")
        payload = extract_payload(e)
        step_status = payload.get("status")

        # Check outcome string
        if outcome:
            o_upper = str(outcome).strip().upper()
            if o_upper in ("FAILED", "REJECTED", "FAILURE", "ERROR"):
                has_failure = True
            elif o_upper in ("DIFFERED", "SKIPPED"):
                has_differed = True
            elif o_upper in ("PASSED", "CONFIRMED", "SUCCESS", "APPLIED", "APPROVED", "CLOSED", "COMPLETED"):
                has_explicit_success = True

        # Check step status in payload if present
        if step_status:
            s_upper = str(step_status).strip().upper()
            if s_upper in ("FAILED", "ERROR"):
                has_failure = True
            elif s_upper in ("DIFFERED", "SKIPPED"):
                has_differed = True
            elif s_upper in ("PASSED", "VERIFIED"):
                has_explicit_success = True

    if has_failure:
        return "FAILED"
    if has_differed:
        return "COMPLETED WITH DIFFERENCES"
    if has_explicit_success:
        return "COMPLETED"
    return "RECORDED"


# ── Pure Journey Builder ─────────────────────────────────────────────────────

def build_journey(
    events: list[Any],
    handoff_task_types: dict[str, str] | None = None,
) -> Journey:
    """
    Construct the Journey object from an incident's event history.
    - Sorts all events strictly by monotonic sequence (seq).
    - Partitions into key events and supporting queries.
    - Maps key events to canonical stages or unclassified.
    - Computes sequence windows, actors, and interleaving.
    - Derives edges between present stages in recorded succession order.
    """
    # 1. Chronological authority: persisted seq
    sorted_events = sorted(events, key=lambda e: get_event_field(e, "seq", 0))

    # Stage buckets
    stage_key_events: dict[str, list[Any]] = {k: [] for k, _ in CANONICAL_STAGES}
    stage_key_events["unclassified"] = []

    stage_queries: dict[str, list[Any]] = {k: [] for k, _ in CANONICAL_STAGES}
    stage_queries["unclassified"] = []

    unlinked_queries: list[Any] = []

    total_events = len(sorted_events)
    key_events_count = 0
    supporting_queries_count = 0

    # 2. Partition events
    for ev in sorted_events:
        ev_type = str(get_event_field(ev, "event_type", "")).strip()

        if ev_type == "QUERY_EXECUTED":
            # Rule 2.4: Not a key event
            linked_stage = link_query_to_stage(ev, handoff_task_types)
            if linked_stage and linked_stage in stage_queries:
                stage_queries[linked_stage].append(ev)
                supporting_queries_count += 1
            else:
                unlinked_queries.append(ev)
        else:
            # Key event
            dest_stage = map_event_to_stage(ev, handoff_task_types)
            if dest_stage not in stage_key_events:
                dest_stage = "unclassified"
            stage_key_events[dest_stage].append(ev)
            key_events_count += 1

    # 3. Build canonical stages
    stages: list[JourneyStage] = []

    for key, label in CANONICAL_STAGES:
        st_events = stage_key_events[key]
        st_queries = stage_queries[key]
        status = derive_stage_status(key, st_events)

        first_seq = get_event_field(st_events[0], "seq") if st_events else None
        last_seq = get_event_field(st_events[-1], "seq") if st_events else None
        first_time = get_event_field(st_events[0], "at") if st_events else None
        last_time = get_event_field(st_events[-1], "at") if st_events else None

        actors: list[str] = []
        for e in st_events:
            act = get_event_field(e, "actor_agent")
            if act and act not in actors:
                actors.append(act)

        stage_obj = JourneyStage(
            key=key,
            label=label,
            status=status,
            event_count=len(st_events),
            query_count=len(st_queries),
            first_seq=first_seq,
            last_seq=last_seq,
            first_time=first_time,
            last_time=last_time,
            actors=actors,
            events=st_events,
            supporting_queries=st_queries,
            interleaved=False,
        )
        stages.append(stage_obj)

    # 4. Handle Unclassified stage (only present when non-empty)
    unclass_events = stage_key_events["unclassified"]
    unclass_queries = stage_queries["unclassified"]
    unclass_stage: JourneyStage | None = None

    if unclass_events:
        u_first_seq = get_event_field(unclass_events[0], "seq")
        u_last_seq = get_event_field(unclass_events[-1], "seq")
        u_first_time = get_event_field(unclass_events[0], "at")
        u_last_time = get_event_field(unclass_events[-1], "at")
        u_actors = list(dict.fromkeys(
            get_event_field(e, "actor_agent") for e in unclass_events if get_event_field(e, "actor_agent")
        ))
        unclass_stage = JourneyStage(
            key="unclassified",
            label="Unclassified",
            status="RECORDED",
            event_count=len(unclass_events),
            query_count=len(unclass_queries),
            first_seq=u_first_seq,
            last_seq=u_last_seq,
            first_time=u_first_time,
            last_time=u_last_time,
            actors=u_actors,
            events=unclass_events,
            supporting_queries=unclass_queries,
            interleaved=False,
        )
        stages.append(unclass_stage)

    # 5. Detect Interleaving between present stages
    present_stages = [s for s in stages if s.is_present]
    for i, s1 in enumerate(present_stages):
        for j, s2 in enumerate(present_stages):
            if i != j and s1.first_seq is not None and s1.last_seq is not None:
                if s2.first_seq is not None and s2.last_seq is not None:
                    # s2 starts after s1 starts, but before s1 finishes
                    if s1.first_seq < s2.first_seq < s1.last_seq:
                        s1.interleaved = True
                        s2.interleaved = True

    # 6. Build Edges: Connect present stages in order of first_seq
    # Order present stages strictly by first_seq
    chrono_present = sorted(present_stages, key=lambda s: s.first_seq if s.first_seq is not None else 0)
    edges: list[tuple[str, str]] = []
    for k in range(len(chrono_present) - 1):
        edges.append((chrono_present[k].key, chrono_present[k + 1].key))

    absent_stages = [s for s in stages if not s.is_present]

    return Journey(
        stages=stages,
        present_stages=present_stages,
        absent_stages=absent_stages,
        unclassified_stage=unclass_stage,
        all_events_count=total_events,
        key_events_count=key_events_count,
        supporting_queries_count=supporting_queries_count,
        unlinked_queries_count=len(unlinked_queries),
        unclassified_events_count=len(unclass_events),
        edges=edges,
        unlinked_queries=unlinked_queries,
    )


# ── Graphviz DOT Generation ──────────────────────────────────────────────────

def build_journey_dot(
    journey: Journey,
    selected_stage_key: str | None = None,
    orientation: str = "LR",
) -> str:
    """
    Generate Graphviz DOT diagram for the Journey Graph:
    - Charcoal background, monospace typography.
    - Present stages with solid border and status text.
    - Absent stages with dashed border and [NOT RECORDED].
    - Edges only between present stages in recorded sequence order.
    - Highlight for selected stage (heavier border width).
    """
    lines = [
        'digraph IncidentJourney {',
        f'    graph [rankdir="{orientation}", bgcolor="transparent", fontname="monospace", margin=0];',
        '    node [shape="box", style="filled", fontname="monospace", fontsize=9, margin="0.15,0.1"];',
        '    edge [color="#4A5059", fontname="monospace", fontsize=8];',
    ]

    # Map status to colors (Editorial Palette from Reference Images)
    status_colors = {
        "COMPLETED": "#22C55E",
        "COMPLETED WITH DIFFERENCES": "#F59E0B",
        "FAILED": "#F95738",
        "RECORDED": "#8C929D",
        "NOT RECORDED": "#2D323B",
    }

    # Render Nodes
    for stage in journey.stages:
        is_selected = (stage.key == selected_stage_key)
        penwidth = "2.8" if is_selected else "1.0"

        safe_label = escape(stage.label)
        safe_status = escape(stage.status)

        if stage.is_present:
            border_color = "#00D2B4" if is_selected else status_colors.get(stage.status, "#8C929D")
            font_color = "#FFFFFF" if is_selected else "#F3F4F6"
            fill_color = "#111419"
            time_part = f"\\n{format_timestamp(stage.first_time, include_ist=False)}" if stage.first_time else ""
            node_label = f"{safe_label}\\n[{safe_status}]\\nEvents: {stage.event_count}{time_part}"
            style_str = "filled"
        else:
            border_color = "#2D323B"
            font_color = "#6B7280"
            fill_color = "#0B0D10"
            node_label = f"{safe_label}\\n[NOT RECORDED]\\nEvents: 0"
            style_str = "dashed,filled"

        lines.append(
            f'    "{stage.key}" ['
            f'label="{node_label}", '
            f'fillcolor="{fill_color}", '
            f'color="{border_color}", '
            f'fontcolor="{font_color}", '
            f'style="{style_str}", '
            f'penwidth={penwidth}];'
        )

    # Render Edges (Recorded sequence between present stages)
    for src, dst in journey.edges:
        lines.append(f'    "{src}" -> "{dst}" [label="next in sequence", color="#3A414D", fontcolor="#8C929D"];')

    lines.append('}')
    return '\n'.join(lines)
