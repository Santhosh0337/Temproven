"""
temproven/agents/orchestrator.py
────────────────────────────────
Orchestrator implementation.

Responsibilities:
- Reads PENDING handoffs from FalkorDB in creation order.
- Executes atomic claim (CLAIMED with lease expiry only if status is PENDING).
- Dispatches handoffs to recipient agents (EvidenceAgent, ImpactAgent, IntegrityAgent).
- Records HANDOFF_COMPLETED events.
- Performs durable recovery on expired leases without in-memory state.
- Guards closed incidents from reprocessing.
"""

from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from temproven.agents.evidence import EvidenceAgent
from temproven.agents.impact import ImpactAgent
from temproven.agents.integrity import IntegrityAgent
from temproven.graph import queries
from temproven.memory.episodic import EventType

if TYPE_CHECKING:
    from temproven.clock import Clock
    from temproven.context import RunContext
    from temproven.graph.gateway import GraphGateway


class Orchestrator:
    """Orchestrates multi-agent incident workflows through durable FalkorDB Handoff records."""

    def __init__(
        self,
        gw: GraphGateway,
        clock: Clock,
        evidence_agent: EvidenceAgent | None = None,
        impact_agent: ImpactAgent | None = None,
        integrity_agent: IntegrityAgent | None = None,
        lease_duration_sec: int = 60,
        worker_id: str = "orchestrator-worker-1",
    ) -> None:
        self.gw = gw
        self.clock = clock
        self.lease_duration_sec = lease_duration_sec
        self.worker_id = worker_id
        self.evidence_agent = evidence_agent or EvidenceAgent(gw, clock)
        self.impact_agent = impact_agent or ImpactAgent(gw, clock)
        self.integrity_agent = integrity_agent or IntegrityAgent(gw, clock)

    def run(
        self,
        incident_id: str,
        ctx: RunContext,
        max_steps: int | None = None,
    ) -> dict[str, Any]:
        """
        Execute pending handoffs for incident_id until no pending handoffs remain
        or max_steps limit is reached.
        """
        # Guard: re-processing a CLOSED incident produces no new actions
        inc_rows = self.gw.query(
            queries.Q_GET_INCIDENT_HEADER,
            {"incident_id": incident_id},
            name="CHECK_INCIDENT_HEADER",
            ctx=ctx,
        )
        if inc_rows and inc_rows[0].get("status") == "CLOSED":
            return {
                "status": "ALREADY_PROCESSED",
                "incident_id": incident_id,
                "detail": "Incident is already CLOSED; zero actions taken.",
            }

        steps_executed = 0
        completed_handoffs: list[str] = []

        while True:
            # Check step limit
            if max_steps is not None and steps_executed >= max_steps:
                break

            # Fetch pending handoffs in creation order
            pending = self.gw.query(
                queries.Q_GET_PENDING_HANDOFFS,
                {"incident_id": incident_id},
                name="HANDOFF_GET_PENDING",
                ctx=ctx,
            )
            if not pending:
                break

            # Process next pending handoff
            h = pending[0]
            h_id = h["id"]
            now_epoch = self.clock.now_epoch()
            lease_expires = now_epoch + self.lease_duration_sec

            # 1. Atomic claim
            claim_res = self.gw.query(
                queries.Q_CLAIM_HANDOFF,
                {
                    "id": h_id,
                    "claimed_by": self.worker_id,
                    "claimed_at": now_epoch,
                    "lease_expires_at": lease_expires,
                },
                name="HANDOFF_CLAIM",
                ctx=ctx,
            )
            if not claim_res:
                # Lost claim race, loop again
                continue

            if ctx.metrics:
                ctx.metrics.handoffs_count += 1

            to_agent = h["to_agent"]
            task_type = h["task_type"]
            try:
                payload = json.loads(h.get("payload_json") or "{}")
            except Exception:
                payload = {}

            # 2. Dispatch to recipient agent
            try:
                result_data: dict[str, Any] = {}
                if to_agent == "IntegrityAgent":
                    if task_type == "SELECT_PLAYBOOK":
                        result_data = self.integrity_agent.handle_select_playbook(ctx, payload)
                    elif task_type == "VALIDATE_PROPOSALS":
                        result_data = self.integrity_agent.handle_validate_proposals(ctx, payload)
                    elif task_type == "VERIFY_AND_CLOSE":
                        result_data = self.integrity_agent.handle_verify_and_close(ctx, payload)
                    else:
                        raise ValueError(f"Unknown task_type '{task_type}' for IntegrityAgent")
                elif to_agent == "ImpactAgent":
                    if task_type == "ASSESS_IMPACT":
                        result_data = self.impact_agent.handle_assess_impact(ctx, payload)
                    elif task_type == "APPLY_APPROVED":
                        result_data = self.impact_agent.handle_apply_approved(ctx, payload)
                    else:
                        raise ValueError(f"Unknown task_type '{task_type}' for ImpactAgent")
                else:
                    raise ValueError(f"Unknown recipient agent: {to_agent}")

                # 3. Complete handoff atomically
                completed_at = self.clock.now_epoch()
                self.gw.query(
                    queries.Q_COMPLETE_HANDOFF,
                    {
                        "id": h_id,
                        "completed_at": completed_at,
                        "result_json": json.dumps(result_data),
                    },
                    name="HANDOFF_COMPLETE",
                    ctx=ctx,
                )

                if ctx.recorder:
                    ctx.recorder.record_event(
                        gw=self.gw,
                        ctx=ctx,
                        event_type=EventType.HANDOFF_COMPLETED,
                        summary=f"Completed handoff {task_type} (to: {to_agent})",
                        logical_key=f"complete_{h_id}",
                        outcome="COMPLETED",
                        payload={"handoff_id": h_id, "result": result_data},
                    )

                completed_handoffs.append(h_id)
                steps_executed += 1

            except Exception as exc:
                # Mark handoff FAILED with reason
                self.gw.query(
                    queries.Q_FAIL_HANDOFF,
                    {
                        "id": h_id,
                        "failed_reason": str(exc),
                        "completed_at": self.clock.now_epoch(),
                    },
                    name="HANDOFF_FAIL",
                    ctx=ctx,
                )
                raise

        return {
            "status": "COMPLETED",
            "incident_id": incident_id,
            "steps_executed": steps_executed,
            "completed_handoffs": completed_handoffs,
        }

    def recover(self, max_attempts: int = 3) -> list[dict[str, Any]]:
        """
        Recover expired CLAIMED handoffs whose lease expired.
        Resets to PENDING if attempts < max_attempts, else marks FAILED.
        """
        now_epoch = self.clock.now_epoch()
        return self.gw.query(
            queries.Q_RECOVER_EXPIRED_HANDOFFS,
            {"now": now_epoch, "max_attempts": max_attempts},
            name="HANDOFF_RECOVER",
        )
