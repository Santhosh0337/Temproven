"""
temproven/memory/episodic.py
────────────────────────────
Episodic Memory implementation for Temproven.

Maintains durable, ordered, gap-free audit trails of incident history:
- Single source of truth for Event persistence via EpisodicRecorder.
- Monotonic sequence numbering taken from Incident.next_seq.
- Linear [:FOLLOWS] chains linking newer events to older events.
- Deterministic event IDs: hash(incident_id, logical_key).
- Full incident reconstruction for fresh sessions without in-memory state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

from temproven.graph import queries

if TYPE_CHECKING:
    from temproven.context import RunContext
    from temproven.graph.gateway import GraphGateway


class EventType(str, Enum):
    """Allowed event types for the episodic incident timeline."""
    INCIDENT_OPENED = "INCIDENT_OPENED"
    EVIDENCE_INGESTED = "EVIDENCE_INGESTED"
    OBSERVATION = "OBSERVATION"
    QUERY_EXECUTED = "QUERY_EXECUTED"
    HYPOTHESIS_CHECKED = "HYPOTHESIS_CHECKED"
    DECISION = "DECISION"
    ACTION_APPLIED = "ACTION_APPLIED"
    HANDOFF_CREATED = "HANDOFF_CREATED"
    HANDOFF_COMPLETED = "HANDOFF_COMPLETED"
    PLAYBOOK_RETRIEVED = "PLAYBOOK_RETRIEVED"
    PLAYBOOK_STEP = "PLAYBOOK_STEP"
    POSTCONDITION_CHECKED = "POSTCONDITION_CHECKED"
    OUTCOME = "OUTCOME"
    INCIDENT_CLOSED = "INCIDENT_CLOSED"


@dataclass
class EventRecord:
    """Represents a single episodic event in the incident timeline."""
    id: str
    incident_id: str
    seq: int
    event_type: str
    at: int
    actor_agent: str
    summary: str
    outcome: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    action_ids: list[str] = field(default_factory=list)
    logical_key: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class IncidentReconstruction:
    """Complete structured reconstruction of an incident purely from graph state."""
    incident_id: str
    incident_type: str
    status: str
    opened_at: int
    next_seq: int
    events: list[EventRecord]
    timeline: list[EventRecord]
    action_records: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    handoffs: list[dict[str, Any]]
    playbook_runs: list[dict[str, Any]]
    evidence_claims: list[dict[str, Any]]
    metrics: dict[str, Any] | None = None
    instrument_id: str | None = None


def make_event_id(incident_id: str, logical_key: str) -> str:
    """Generate a deterministic event ID from incident ID and logical key."""
    h = hashlib.sha256(f"{incident_id}:{logical_key}".encode("utf-8")).hexdigest()[:16]
    return f"ev_{h}"


class EpisodicRecorder:
    """
    Sole writer of Event nodes to FalkorDB.
    Enforces deterministic deduplication, gap-free sequence ordering,
    and linear [:FOLLOWS] relationships.
    """

    def record_event(
        self,
        gw: GraphGateway,
        ctx: RunContext,
        event_type: EventType | str,
        summary: str,
        logical_key: str,
        actor_agent: str | None = None,
        outcome: str | None = None,
        evidence_ids: list[str] | None = None,
        action_ids: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> EventRecord:
        """
        Idempotently record an event into the incident timeline.
        Replaying the same logical_key returns the existing event without
        incrementing next_seq or duplicating graph elements.
        """
        # Validate event type
        if isinstance(event_type, str):
            try:
                ev_type_enum = EventType(event_type)
            except ValueError:
                raise ValueError(f"Invalid EventType: '{event_type}'. Must be one of {[e.value for e in EventType]}")
        else:
            ev_type_enum = event_type

        event_id = make_event_id(ctx.incident_id, logical_key)

        # 1. Check if event already exists (deduplication)
        existing = gw.query(
            queries.Q_CHECK_EVENT_EXISTS,
            {"event_id": event_id},
            name="EVENT_CHECK_EXISTS",
        )
        if existing:
            # Event already recorded, retrieve from timeline or construct record
            timeline = get_timeline(gw, ctx.incident_id)
            for ev in timeline:
                if ev.id == event_id:
                    return ev
            # Fallback if not found in timeline for some reason
            return EventRecord(
                id=event_id,
                incident_id=ctx.incident_id,
                seq=existing[0].get("seq", 0),
                event_type=ev_type_enum.value,
                at=ctx.clock.now_epoch(),
                actor_agent=actor_agent or ctx.actor_agent,
                summary=summary,
                outcome=outcome,
                evidence_ids=evidence_ids or [],
                action_ids=action_ids or [],
                logical_key=logical_key,
                payload=payload or {},
            )

        # 2. Append new event atomically
        actor = actor_agent or ctx.actor_agent
        at_epoch = ctx.clock.now_epoch()
        payload_data = payload or {}
        payload_json = json.dumps(payload_data)

        params = {
            "event_id": event_id,
            "incident_id": ctx.incident_id,
            "event_type": ev_type_enum.value,
            "at": at_epoch,
            "actor_agent": actor,
            "summary": summary,
            "outcome": outcome,
            "logical_key": logical_key,
            "payload_json": payload_json,
        }

        created = gw.query(queries.Q_APPEND_EVENT, params, name="EVENT_APPEND")
        seq = created[0]["seq"] if created else 1

        # 3. Link evidence references
        if evidence_ids:
            for ev_id in evidence_ids:
                gw.query(
                    queries.Q_LINK_EVENT_EVIDENCE,
                    {"event_id": event_id, "evidence_id": ev_id},
                    name="EVENT_LINK_EVIDENCE",
                )

        # 4. Link action references
        if action_ids:
            for ar_id in action_ids:
                gw.query(
                    queries.Q_LINK_EVENT_ACTION,
                    {"event_id": event_id, "action_id": ar_id},
                    name="EVENT_LINK_ACTION",
                )

        return EventRecord(
            id=event_id,
            incident_id=ctx.incident_id,
            seq=seq,
            event_type=ev_type_enum.value,
            at=at_epoch,
            actor_agent=actor,
            summary=summary,
            outcome=outcome,
            evidence_ids=evidence_ids or [],
            action_ids=action_ids or [],
            logical_key=logical_key,
            payload=payload_data,
        )

    def record_query_executed(
        self,
        gw: GraphGateway,
        ctx: RunContext,
        query_name: str,
        param_keys: list[str],
        result_count: int,
        elapsed_ms: float,
    ) -> EventRecord | None:
        """
        Record a QUERY_EXECUTED event for an executed DOMAIN query.
        Uses an ordinal logical key to ensure each query is uniquely recorded.
        """
        domain_ordinal = ctx.metrics.domain_queries if ctx.metrics else 0
        logical_key = f"query_{query_name}_{domain_ordinal}"
        summary = f"Executed domain query {query_name} ({result_count} rows, {elapsed_ms}ms)"
        payload = {
            "query_name": query_name,
            "param_keys": param_keys,
            "result_count": result_count,
            "elapsed_ms": elapsed_ms,
        }

        return self.record_event(
            gw=gw,
            ctx=ctx,
            event_type=EventType.QUERY_EXECUTED,
            summary=summary,
            logical_key=logical_key,
            payload=payload,
        )


def get_timeline(gw: GraphGateway, incident_id: str) -> list[EventRecord]:
    """Retrieve full chronological timeline of events for an incident."""
    rows = gw.query(queries.Q_GET_TIMELINE, {"incident_id": incident_id}, name="GET_TIMELINE")
    records: list[EventRecord] = []
    for r in rows:
        payload_raw = r.get("payload_json") or "{}"
        try:
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        except Exception:
            payload = {}

        evidence_ids = [e for e in (r.get("evidence_ids") or []) if e is not None]
        action_ids = [a for a in (r.get("action_ids") or []) if a is not None]

        records.append(EventRecord(
            id=r["id"],
            incident_id=incident_id,
            seq=r["seq"],
            event_type=r["event_type"],
            at=r["at"],
            actor_agent=r["actor_agent"],
            summary=r["summary"],
            outcome=r.get("outcome"),
            evidence_ids=evidence_ids,
            action_ids=action_ids,
            logical_key=r.get("logical_key") or "",
            payload=payload,
        ))
    return records


def reconstruct_incident(gw: GraphGateway, incident_id: str) -> IncidentReconstruction | None:
    """
    Reconstruct an incident's complete state and history purely from graph nodes.
    Returns None if the incident is not found.
    """
    header_rows = gw.query(
        queries.Q_GET_INCIDENT_HEADER,
        {"incident_id": incident_id},
        name="RECONSTRUCT_HEADER",
    )
    if not header_rows:
        return None

    header = header_rows[0]
    timeline = get_timeline(gw, incident_id)
    actions = gw.query(
        queries.Q_GET_INCIDENT_ACTION_RECORDS,
        {"incident_id": incident_id},
        name="RECONSTRUCT_ACTIONS",
    )
    tasks = gw.query(
        queries.Q_GET_INCIDENT_TASKS,
        {"incident_id": incident_id},
        name="RECONSTRUCT_TASKS",
    )
    handoffs = gw.query(
        queries.Q_GET_ALL_INCIDENT_HANDOFFS,
        {"incident_id": incident_id},
        name="RECONSTRUCT_HANDOFFS",
    )
    playbook_runs = gw.query(
        queries.Q_GET_PLAYBOOK_RUNS,
        {"incident_id": incident_id},
        name="RECONSTRUCT_PLAYBOOK_RUNS",
    )
    evidence_claims = gw.query(
        queries.Q_RECONSTRUCT_EVIDENCE_CLAIMS,
        {"incident_id": incident_id},
        name="RECONSTRUCT_EVIDENCE_CLAIMS",
    )
    metrics_rows = gw.query(
        queries.Q_GET_INCIDENT_METRICS,
        {"incident_id": incident_id},
        name="RECONSTRUCT_METRICS",
    )
    metrics = metrics_rows[0] if metrics_rows else None

    return IncidentReconstruction(
        incident_id=header["id"],
        incident_type=header["incident_type"],
        status=header["status"],
        opened_at=header["opened_at"],
        next_seq=header.get("next_seq", 1),
        events=timeline,
        timeline=timeline,
        action_records=actions,
        tasks=tasks,
        handoffs=handoffs,
        playbook_runs=playbook_runs,
        evidence_claims=evidence_claims,
        metrics=metrics,
        instrument_id=header.get("instrument_id"),
    )
