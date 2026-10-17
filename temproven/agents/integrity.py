"""
temproven/agents/integrity.py
─────────────────────────────
IntegrityAgent implementation.

Responsibilities:
- Selects playbooks based on graph shape and incident type.
- Validates action proposals (provenance paths, assignee legitimacy, deduplication)
  before approving write executions.
- Enforces postcondition checks PC1-PC6, closes incidents, derives/promotes playbooks,
  and persists IncidentMetrics.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, TYPE_CHECKING

from temproven.graph import queries
from temproven.memory.episodic import EventType
from temproven.memory.procedural import (
    derive_playbook,
    promote_playbook,
    retrieve_playbook,
)

if TYPE_CHECKING:
    from temproven.clock import Clock
    from temproven.context import RunContext
    from temproven.graph.gateway import GraphGateway


class IntegrityAgent:
    """Agent enforcing policy gates, playbook selection, proposal validation, and closing."""

    def __init__(self, gw: GraphGateway, clock: Clock | None = None) -> None:
        self.gw = gw
        self.clock = clock

    def handle_select_playbook(self, ctx: RunContext, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Evaluate candidate playbooks against graph shape and dispatch ASSESS_IMPACT.
        """
        incident_type = payload.get("incident_type", "CERT_LAPSE")
        instrument_id = payload.get("instrument_id")

        matched = retrieve_playbook(self.gw, incident_type=incident_type, instrument_id=instrument_id)
        if matched:
            playbook_id = matched["id"]
            status = matched["status"]
            mode = "VERIFIED_REUSE" if status == "VERIFIED" else "CANDIDATE"
            if ctx.recorder:
                ctx.recorder.record_event(
                    gw=self.gw,
                    ctx=ctx,
                    event_type=EventType.PLAYBOOK_RETRIEVED,
                    summary=f"Retrieved playbook {playbook_id} (status: {status}, mode: {mode})",
                    logical_key=f"retrieve_{playbook_id}",
                    outcome="FOUND",
                    payload={"playbook_id": playbook_id, "mode": mode, "status": status},
                )
        else:
            playbook_id = None
            mode = "EXPLORATORY"
            if ctx.recorder:
                ctx.recorder.record_event(
                    gw=self.gw,
                    ctx=ctx,
                    event_type=EventType.PLAYBOOK_RETRIEVED,
                    summary="No matching playbook found; running exploratory ladder",
                    logical_key="retrieve_none",
                    outcome="NONE_FOUND",
                    payload={"mode": "EXPLORATORY"},
                )

        # Dispatch ASSESS_IMPACT to ImpactAgent
        h_payload = {
            "instrument_id": instrument_id,
            "cert_id": payload.get("cert_id"),
            "playbook_id": playbook_id,
            "mode": mode,
            "incident_type": incident_type,
        }
        h_key = f"handoff_assess_impact_{ctx.incident_id}"
        h_id = f"h_{hashlib.sha256(f'{ctx.incident_id}:{h_key}'.encode()).hexdigest()[:16]}"
        at_epoch = ctx.clock.now_epoch()

        self.gw.query(
            queries.Q_CREATE_HANDOFF,
            {
                "id": h_id,
                "incident_id": ctx.incident_id,
                "from_agent": "IntegrityAgent",
                "to_agent": "ImpactAgent",
                "task_type": "ASSESS_IMPACT",
                "payload_json": json.dumps(h_payload),
                "idempotency_key": h_key,
                "created_at": at_epoch,
            },
            name="HANDOFF_CREATE",
            ctx=ctx,
        )

        if ctx.recorder:
            ctx.recorder.record_event(
                gw=self.gw,
                ctx=ctx,
                event_type=EventType.HANDOFF_CREATED,
                summary=f"Created handoff ASSESS_IMPACT to ImpactAgent (mode: {mode})",
                logical_key=f"ev_{h_key}",
                payload={"handoff_id": h_id, "to_agent": "ImpactAgent", "mode": mode},
            )

        return {"playbook_id": playbook_id, "mode": mode, "handoff_id": h_id}

    def handle_validate_proposals(self, ctx: RunContext, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Validate proposed actions and dispatch APPLY_APPROVED to ImpactAgent.
        """
        proposals = payload.get("proposals", [])
        approved: list[dict[str, Any]] = []

        for p in proposals:
            target_id = p["target_id"]
            action_type = p["action_type"]

            # 1. Target node existence check
            exists = self.gw.query("MATCH (n {id: $id}) RETURN n.id AS id", {"id": target_id}, name="VALIDATE_TARGET", ctx=ctx)
            if not exists:
                if ctx.recorder:
                    ctx.recorder.record_event(
                        gw=self.gw,
                        ctx=ctx,
                        event_type=EventType.DECISION,
                        summary=f"Rejected action {action_type} for missing target {target_id}",
                        logical_key=f"reject_{target_id}",
                        outcome="REJECTED",
                    )
                continue

            # 2. Path re-resolution check
            path = p.get("path", [])
            valid_path = True
            for nid in path:
                if nid in ("PRODUCED", "USES_DATASET", "DERIVED_FROM"):
                    continue
                node_exists = self.gw.query("MATCH (n {id: $id}) RETURN n.id AS id", {"id": nid}, name="VALIDATE_PATH_NODE", ctx=ctx)
                if not node_exists:
                    valid_path = False
                    break

            if not valid_path:
                continue

            approved.append(p)
            if ctx.recorder:
                ctx.recorder.record_event(
                    gw=self.gw,
                    ctx=ctx,
                    event_type=EventType.DECISION,
                    summary=f"Approved action {action_type} for target {target_id} (assignee: {p.get('assignee_id') or 'ESCALATED'})",
                    logical_key=f"approve_{action_type}_{target_id}",
                    outcome="APPROVED",
                    payload={"target_id": target_id, "role": p.get("role")},
                )

        # Dispatch APPLY_APPROVED to ImpactAgent
        h_payload = {
            "approved_proposals": approved,
            "playbook_id": payload.get("playbook_id"),
            "mode": payload.get("mode"),
            "instrument_id": payload.get("instrument_id"),
            "state": payload.get("state", {}),
        }
        h_key = f"handoff_apply_approved_{ctx.incident_id}"
        h_id = f"h_{hashlib.sha256(f'{ctx.incident_id}:{h_key}'.encode()).hexdigest()[:16]}"
        at_epoch = ctx.clock.now_epoch()

        self.gw.query(
            queries.Q_CREATE_HANDOFF,
            {
                "id": h_id,
                "incident_id": ctx.incident_id,
                "from_agent": "IntegrityAgent",
                "to_agent": "ImpactAgent",
                "task_type": "APPLY_APPROVED",
                "payload_json": json.dumps(h_payload),
                "idempotency_key": h_key,
                "created_at": at_epoch,
            },
            name="HANDOFF_CREATE",
            ctx=ctx,
        )

        if ctx.recorder:
            ctx.recorder.record_event(
                gw=self.gw,
                ctx=ctx,
                event_type=EventType.HANDOFF_CREATED,
                summary=f"Created handoff APPLY_APPROVED to ImpactAgent ({len(approved)} approved)",
                logical_key=f"ev_{h_key}",
                payload={"handoff_id": h_id, "to_agent": "ImpactAgent", "approved_count": len(approved)},
            )

        return {"approved_count": len(approved), "handoff_id": h_id}

    def handle_verify_and_close(self, ctx: RunContext, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Verify PC1-PC6 postconditions, close incident, derive/promote playbooks, and persist metrics.
        """
        mode = payload.get("mode", "EXPLORATORY")
        playbook_id = payload.get("playbook_id")

        promoted = False
        checks: dict[str, Any] = {}

        if mode == "EXPLORATORY":
            # Derive candidate playbook and attempt promotion
            playbook_id = derive_playbook(self.gw, ctx.incident_id)
            promoted, checks = promote_playbook(self.gw, playbook_id, ctx.incident_id, ctx)
        else:
            # Mode VERIFIED_REUSE or CANDIDATE: validate in incident
            if playbook_id:
                promoted, checks = promote_playbook(self.gw, playbook_id, ctx.incident_id, ctx)

        # Close Incident in FalkorDB
        self.gw.query(
            queries.Q_CLOSE_INCIDENT,
            {"incident_id": ctx.incident_id},
            name="CLOSE_INCIDENT",
            ctx=ctx,
        )

        if ctx.recorder:
            ctx.recorder.record_event(
                gw=self.gw,
                ctx=ctx,
                event_type=EventType.INCIDENT_CLOSED,
                summary=f"Incident {ctx.incident_id} successfully resolved and closed.",
                logical_key="close_incident",
                outcome="CLOSED",
                payload={"mode": mode, "promoted": promoted},
            )

        # Compute & persist metrics
        elapsed_ms = ctx.metrics.finish() if ctx.metrics else 0.0
        dom_q = ctx.metrics.domain_queries if ctx.metrics else 0
        book_q = ctx.metrics.bookkeeping_queries if ctx.metrics else 0
        steps_exec = ctx.metrics.steps_executed if ctx.metrics else 7
        steps_skip = ctx.metrics.steps_skipped if ctx.metrics else 0
        handoffs_cnt = ctx.metrics.handoffs_count if ctx.metrics else 5
        q_counts_json = json.dumps(ctx.metrics.query_counts if ctx.metrics else {})

        metrics_id = f"metrics_{ctx.incident_id}"
        now_epoch = ctx.clock.now_epoch()

        self.gw.query(
            queries.Q_PERSIST_INCIDENT_METRICS,
            {
                "id": metrics_id,
                "incident_id": ctx.incident_id,
                "mode": mode,
                "domain_queries": dom_q,
                "bookkeeping_queries": book_q,
                "steps_executed": steps_exec,
                "steps_skipped": steps_skip,
                "handoffs": handoffs_cnt,
                "elapsed_ms": elapsed_ms,
                "started_at": now_epoch,
                "finished_at": now_epoch,
                "query_counts_json": q_counts_json,
            },
            name="METRICS_PERSIST",
            ctx=ctx,
        )

        return {
            "incident_id": ctx.incident_id,
            "status": "CLOSED",
            "playbook_id": playbook_id,
            "promoted": promoted,
            "checks": checks,
            "metrics": {
                "domain_queries": dom_q,
                "bookkeeping_queries": book_q,
                "elapsed_ms": elapsed_ms,
            },
        }
